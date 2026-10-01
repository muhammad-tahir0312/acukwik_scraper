#!/usr/bin/env python3
"""
Regenerate the AC-U-KWIK airport input list (replacement for country_links.csv).

Steps:
1. GET /Power-Search-Airports and collect the ``div.byCountry ul li a`` links.
2. GET every country page (``%2f`` region URLs are requested exactly as written,
   redirects are followed):
   - a redirect to ``/Airport-Info/{ID}`` (single-airport country) gives one row
     built from the airport page header;
   - otherwise every ``div.result.clearfix`` row with a
     ``div.col2 a[href*='/Airport-Info/']`` link gives one row, and the row count
     is checked against the page's "N results found" number.
3. Rows are de-duplicated on Source ID and written to a NEW CSV
   (default ``country_links_<YYYYMMDD>.csv``). A JSON report next to it holds the
   per-country counts, the validation checks and a diff against the old
   ``country_links.csv`` keyed on Source ID (added / removed / renamed / changed).

The scraper's CSV loader only needs ``Airport Link`` (and optionally ``ICAO``);
the extra columns are ignored by it.

Usage:
    .venv/bin/python scripts/generate_airport_list.py
    .venv/bin/python scripts/generate_airport_list.py --output /tmp/airports.csv
    .venv/bin/python scripts/generate_airport_list.py --offline-dir saved_pages/

Offline mode reads ``power_search.html`` and ``country_<SLUG>.html`` files
(SLUG = the part after ``/Airports/``, URL-decoded, non-alphanumerics -> ``_``,
upper case, e.g. ``country_USA.html``, ``country_SPAIN_CANARY_ISLANDS.html``).
Countries without a saved file are reported as ``missing_offline``.

Exit codes: 0 = written and all checks passed, 2 = written but checks failed,
1 = aborted (blocked / logged out / too few countries).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urljoin

import lxml.html

logger = logging.getLogger("generate_airport_list")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://acukwik.com"
POWER_SEARCH_PATH = "/Power-Search-Airports"
DEFAULT_COOKIES = PROJECT_ROOT / "cookies.json"
# Compare against the newest existing list (country_links_YYYYMMDD.csv sorts by date).
_EXISTING_LISTS = sorted(PROJECT_ROOT.glob("country_links*.csv"))
DEFAULT_OLD_CSV = _EXISTING_LISTS[-1] if _EXISTING_LISTS else PROJECT_ROOT / "country_links.csv"
# Must match the browser that produced cookies.json (Cloudflare ties clearance to it).
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)
MIN_COUNTRIES = 240

FIELDNAMES = [
    "ICAO",
    "Airport Name",
    "City",
    "State",
    "Country",
    "Airport Link",
    "Source ID",
    "Country Page",
]

# Region pages that previously produced no rows at all; they must have rows now.
EXPECTED_NONEMPTY = [
    "SPAIN/CANARY ISLANDS",
    "PORTUGAL/AZORES",
    "PORTUGAL/MADEIRA",
    "CHILE/EASTER ISLAND",
]

AIRPORT_ID_RE = re.compile(r"/Airport-Info/([^/?#]+)", re.IGNORECASE)
RESULTS_FOUND_RE = re.compile(r"([\d,]+)\s+results?\s+found", re.IGNORECASE)
HEADER_CODE_RE = re.compile(r"\b(ICAO|IATA|FAA ID)\s*-\s*([A-Z0-9]+)", re.IGNORECASE)


class BlockedError(RuntimeError):
    """Raised when the site blocks us or the session is logged out."""


def _cls(name: str) -> str:
    """XPath predicate matching one CSS class token."""
    return f"contains(concat(' ', normalize-space(@class), ' '), ' {name} ')"


def _text(element: Any) -> str:
    """Whitespace-normalized text content (non-breaking spaces removed)."""
    if element is None:
        return ""
    return " ".join(element.text_content().replace("\xa0", " ").split())


def source_id_from_url(url: str) -> str:
    match = AIRPORT_ID_RE.search(url or "")
    return unquote(match.group(1)) if match else ""


def country_slug(href: str) -> str:
    """Offline file slug for a country link (``/Airports/SPAIN%2fCANARY-ISLANDS`` -> ``SPAIN_CANARY_ISLANDS``)."""
    tail = unquote(href.split("/Airports/", 1)[-1])
    return re.sub(r"[^A-Za-z0-9]+", "_", tail).strip("_").upper()


# --------------------------------------------------------------------------
# Parsing (pure functions, used both online and offline)
# --------------------------------------------------------------------------

def parse_country_links(html: str | bytes, base_url: str = BASE_URL) -> List[Dict[str, str]]:
    """Return ``[{name, href, url}]`` for the Power Search "by country" list."""
    doc = lxml.html.fromstring(html)
    links = []
    seen = set()
    for anchor in doc.xpath(f"//div[{_cls('byCountry')}]//ul/li/a[@href]"):
        href = anchor.get("href").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        links.append({"name": _text(anchor), "href": href, "url": urljoin(base_url, href)})
    return links


def parse_results_found(doc: Any) -> Optional[int]:
    nodes = doc.xpath(f"//div[{_cls('resultsFound')}]")
    text = _text(nodes[0]) if nodes else ""
    match = RESULTS_FOUND_RE.search(text)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def parse_country_rows(doc: Any, country_page: str, base_url: str = BASE_URL) -> List[Dict[str, str]]:
    """Parse the airport rows of a country results page."""
    rows = []
    for result in doc.xpath(f"//div[{_cls('result')} and {_cls('clearfix')}]"):
        anchors = result.xpath(f"./div[{_cls('col2')}]//a[contains(@href, '/Airport-Info/')]")
        if not anchors:
            continue
        href = anchors[0].get("href").strip()
        col1 = result.xpath(f"./div[{_cls('col1')}]")
        col3 = result.xpath(f"./div[{_cls('col3')}]")
        col4 = result.xpath(f"./div[{_cls('col4')}]")
        rows.append({
            "ICAO": _text(col1[0]) if col1 else "",
            "Airport Name": _text(anchors[0]),
            "City": _text(col3[0]) if col3 else "",
            "State": _text(col3[1]) if len(col3) > 1 else "",
            "Country": _text(col4[0]) if col4 else "",
            "Airport Link": urljoin(base_url, href),
            "Source ID": source_id_from_url(href),
            "Country Page": country_page,
        })
    return rows


def airport_page_url(doc: Any, base_url: str = BASE_URL) -> str:
    """Airport URL of a saved page (form action), or '' when it is not an airport page."""
    for action in doc.xpath("//form/@action"):
        if "/Airport-Info/" in action:
            return urljoin(base_url, action.strip())
    return ""


def parse_airport_page_row(
    doc: Any,
    final_url: str,
    country_name: str,
    country_page: str,
    base_url: str = BASE_URL,
) -> Dict[str, str]:
    """Best-effort CSV row from an /Airport-Info/ page (single-airport countries)."""
    source_id = source_id_from_url(final_url)

    codes: Dict[str, str] = {}
    for h3 in doc.xpath("//h3"):
        for label, value in HEADER_CODE_RE.findall(_text(h3)):
            codes.setdefault(label.upper(), value.upper())
        if codes:
            break

    # Name: "<ID> - <Name>" in the h1, falling back to "<ID>/<Name> General Airport Information".
    name = ""
    h1 = doc.xpath("//h1")
    if h1:
        h1_text = _text(h1[0])
        if " - " in h1_text:
            name = h1_text.split(" - ", 1)[1].strip()
    if not name:
        title = _text(doc.find(".//title")) if doc.find(".//title") is not None else ""
        title = re.sub(r"\s*General Airport Information\s*$", "", title, flags=re.IGNORECASE)
        name = title.split("/", 1)[1].strip() if "/" in title else title

    # Location: "Located in <City>[, <State>], <COUNTRY>".
    city = state = country = ""
    for h2 in doc.xpath("//h2"):
        h2_text = _text(h2)
        if h2_text.lower().startswith("located in"):
            parts = [p.strip() for p in h2_text[len("located in"):].split(",")]
            parts = [p for p in parts if p]
            if parts:
                city = parts[0]
            if len(parts) >= 2:
                country = parts[-1]
            if len(parts) >= 3:
                state = parts[1]
            break

    return {
        "ICAO": codes.get("ICAO", ""),
        "Airport Name": name,
        "City": city,
        "State": state,
        "Country": country or country_name,
        "Airport Link": urljoin(base_url, f"/Airport-Info/{source_id}") if source_id else final_url,
        "Source ID": source_id,
        "Country Page": country_page,
    }


def country_search_form(power_search_html: str | bytes, country_name: str) -> Optional[Dict[str, str]]:
    """Form fields that search Power Search by country (heliports box unticked).

    Some country/region links (e.g. SPAIN/CANARY ISLANDS, GUINEA-BISSAU) open a
    page that searches for a mangled name and lists nothing; the search form's
    country dropdown still returns their airports.
    """
    doc = lxml.html.fromstring(power_search_html)
    select = doc.xpath('//select[contains(@name, "ddlCountry")]')
    if not select:
        return None
    value = None
    for option in select[0].xpath(".//option"):
        if option.text_content().strip().upper() == country_name.strip().upper():
            value = option.get("value")
            break
    if not value:
        return None
    form = {
        field.get("name"): field.get("value") or ""
        for field in doc.xpath('//form[@id="Form"]//input[@name]')
        if (field.get("type") or "text").lower() in ("hidden", "text")
    }
    form[select[0].get("name")] = value
    button = doc.xpath('//a[contains(@href, "lbtnSearch") and not(contains(@href, "BasicSearch"))]')
    match = re.search(r"__doPostBack\('([^']+)'", button[0].get("href")) if button else None
    if not match:
        return None
    form["__EVENTTARGET"] = match.group(1)
    form["__EVENTARGUMENT"] = ""
    return form


def parse_country_page(
    html: str | bytes,
    final_url: str,
    country: Dict[str, str],
    base_url: str = BASE_URL,
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """Parse one country response. Returns (rows, per-country stats)."""
    doc = lxml.html.fromstring(html)
    if "/Airport-Info/" not in (final_url or ""):
        # Offline pages carry no response URL: detect the redirect from the form action.
        final_url = airport_page_url(doc, base_url) or final_url

    stats: Dict[str, Any] = {
        "country": country["name"],
        "url": country["url"],
        "final_url": final_url,
    }
    if "/Airport-Info/" in (final_url or ""):
        row = parse_airport_page_row(doc, final_url, country["name"], country["url"], base_url)
        stats.update({"kind": "redirect", "results_found": None, "rows": 1})
        stats["status"] = "ok" if row["Source ID"] else "no_source_id"
        return [row], stats

    rows = parse_country_rows(doc, country["url"], base_url)
    results_found = parse_results_found(doc)
    stats.update({"kind": "list", "results_found": results_found, "rows": len(rows)})
    if not rows:
        stats["status"] = "zero_rows"
    elif results_found is None:
        stats["status"] = "no_results_found_count"
    elif results_found != len(rows):
        stats["status"] = "count_mismatch"
    else:
        stats["status"] = "ok"
    return rows, stats


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def check_blocked(status_code: int, final_url: str, body: str, history_urls: List[str] = ()) -> None:
    """Raise BlockedError for Cloudflare challenges, 403s and login redirects."""
    if status_code == 403:
        raise BlockedError(f"HTTP 403 for {final_url}")
    head = body[:20000]
    if "Just a moment" in head or "cf-chl" in body:
        raise BlockedError(f"Cloudflare challenge page for {final_url}")
    for url in list(history_urls) + [final_url]:
        if re.search(r"/Login\b", url or "", re.IGNORECASE):
            raise BlockedError(f"Redirected to login ({url}); refresh cookies.json")


class Fetcher:
    """Throttled requests session using the saved AC-U-KWIK cookies."""

    def __init__(self, cookies_file: Path, user_agent: str, delay: float = 1.0, retries: int = 3):
        import requests

        self._requests = requests
        self.delay = delay
        self.retries = retries
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._load_cookies(cookies_file)

    def _load_cookies(self, cookies_file: Path) -> None:
        if not cookies_file.exists():
            raise BlockedError(f"Cookies file not found: {cookies_file}")
        cookies = json.loads(cookies_file.read_text(encoding="utf-8"))
        if not isinstance(cookies, list):
            raise BlockedError(f"Cookies file must be a JSON array: {cookies_file}")
        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue
            self.session.cookies.set(
                name,
                value,
                domain=cookie.get("domain") or "acukwik.com",
                path=cookie.get("path") or "/",
            )
        logger.info("Loaded %d cookies from %s", len(cookies), cookies_file)

    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get(self, url: str) -> Tuple[bytes, str]:
        """GET ``url`` exactly as written (no re-quoting); return (body, final_url)."""
        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                prepared = self.session.prepare_request(self._requests.Request("GET", url))
                prepared.url = url  # keep %2f / %7e exactly as the site wrote them
                response = self.session.send(prepared, allow_redirects=True, timeout=(15, 90))
            except self._requests.RequestException as e:
                last_error = e
                logger.warning("GET %s failed (attempt %d/%d): %s", url, attempt, self.retries, e)
                time.sleep(2 * attempt)
                continue

            body_text = response.text
            check_blocked(
                response.status_code,
                response.url,
                body_text,
                [r.headers.get("Location", "") for r in response.history] + [r.url for r in response.history],
            )
            if response.status_code >= 500 or response.status_code == 429:
                last_error = RuntimeError(f"HTTP {response.status_code} for {url}")
                logger.warning("%s (attempt %d/%d)", last_error, attempt, self.retries)
                time.sleep(5 * attempt)
                continue
            response.raise_for_status()
            return response.content, response.url
        raise RuntimeError(f"Giving up on {url}: {last_error}")

    def post(self, url: str, data: Dict[str, str]) -> Tuple[bytes, str]:
        """Submit a form (as the browser's Search button does); return (body, final_url)."""
        self._throttle()
        response = self.session.post(
            url, data=data, allow_redirects=True, timeout=(15, 90),
            headers={"Referer": url, "Origin": BASE_URL},
        )
        check_blocked(response.status_code, response.url, response.text,
                      [r.headers.get("Location", "") for r in response.history])
        response.raise_for_status()
        return response.content, response.url


class OfflineFetcher:
    """Serves saved pages from a directory instead of the network."""

    def __init__(self, directory: Path):
        self.directory = directory

    def power_search(self) -> Tuple[bytes, str]:
        path = self.directory / "power_search.html"
        if not path.exists():
            raise FileNotFoundError(f"Offline power search page not found: {path}")
        return path.read_bytes(), urljoin(BASE_URL, POWER_SEARCH_PATH)

    def country(self, country: Dict[str, str]) -> Optional[Tuple[bytes, str]]:
        path = self.directory / f"country_{country_slug(country['href'])}.html"
        if not path.exists():
            return None
        body = path.read_bytes()
        check_blocked(200, country["url"], body.decode("utf-8", errors="replace"))
        return body, country["url"]


# --------------------------------------------------------------------------
# Diff / report
# --------------------------------------------------------------------------

def load_old_rows(path: Path) -> List[Dict[str, str]]:
    if not path or not path.exists():
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def diff_against_old(new_rows: List[Dict[str, str]], old_rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """Compare old and new lists keyed on Source ID (old IDs come from the Airport Link)."""
    old_by_id: Dict[str, Dict[str, str]] = OrderedDict()
    old_invalid = []
    for row in old_rows:
        source_id = row.get("Source ID") or source_id_from_url(row.get("Airport Link", ""))
        if not source_id:
            old_invalid.append(row)
            continue
        old_by_id.setdefault(source_id, row)
    new_by_id = OrderedDict((row["Source ID"], row) for row in new_rows)

    def brief(row: Dict[str, str]) -> Dict[str, str]:
        return {k: (row.get(k) or "").strip() for k in ("ICAO", "Airport Name", "City", "State", "Country", "Airport Link")}

    added = [dict(brief(new_by_id[i]), **{"Source ID": i}) for i in new_by_id if i not in old_by_id]
    removed = [dict(brief(old_by_id[i]), **{"Source ID": i}) for i in old_by_id if i not in new_by_id]
    renamed = []
    changed = []
    for source_id, new in new_by_id.items():
        old = old_by_id.get(source_id)
        if old is None:
            continue
        old_b, new_b = brief(old), brief(new)
        # A blank old name (e.g. old redirect rows) is a fill-in, not a rename.
        if old_b["Airport Name"] and old_b["Airport Name"] != new_b["Airport Name"]:
            renamed.append({"Source ID": source_id, "old": old_b["Airport Name"], "new": new_b["Airport Name"]})
        compare = ("ICAO", "City", "State", "Country", "Airport Link")
        if not old_b["Airport Name"]:
            compare = ("Airport Name",) + compare
        fields = {
            k: {"old": old_b[k], "new": new_b[k]}
            for k in compare
            if old_b[k] != new_b[k]
        }
        if fields:
            changed.append({"Source ID": source_id, "fields": fields})

    old_countries = Counter((row.get("Country") or "").strip() for row in old_rows)
    new_countries = Counter((row.get("Country") or "").strip() for row in new_rows)
    country_counts = {
        country: {"old": old_countries.get(country, 0), "new": new_countries.get(country, 0)}
        for country in sorted(set(old_countries) | set(new_countries))
    }
    return {
        "summary": {
            "old_rows": len(old_rows),
            "old_unique_source_ids": len(old_by_id),
            "old_invalid_rows": len(old_invalid),
            "new_rows": len(new_rows),
            "added": len(added),
            "removed": len(removed),
            "renamed": len(renamed),
            "changed_other_fields": len(changed),
        },
        "added": added,
        "removed": removed,
        "renamed": renamed,
        "changed": changed,
        "old_invalid_rows": old_invalid,
        "country_column_counts": country_counts,
    }


# --------------------------------------------------------------------------
# Main flow
# --------------------------------------------------------------------------

def generate(
    *,
    fetcher: Any = None,
    offline_dir: Optional[Path] = None,
    debug_dir: Optional[Path] = None,
    min_countries: int = MIN_COUNTRIES,
    only: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Collect all rows. Returns {rows, countries, duplicates, raw_row_count}."""
    offline = OfflineFetcher(offline_dir) if offline_dir else None

    if offline:
        body, _ = offline.power_search()
    else:
        body, _ = fetcher.get(urljoin(BASE_URL, POWER_SEARCH_PATH))
    power_search_body = body
    countries = parse_country_links(body)
    logger.info("Found %d country links", len(countries))
    if len(countries) < min_countries:
        raise BlockedError(
            f"Only {len(countries)} country links on Power Search (expected >= {min_countries}); "
            "the page layout changed or the session is not logged in"
        )
    if only:
        wanted = {o.upper() for o in only}
        countries = [c for c in countries if c["name"].upper() in wanted or country_slug(c["href"]) in wanted]

    all_rows: List[Dict[str, str]] = []
    country_stats: List[Dict[str, Any]] = []
    for index, country in enumerate(countries, 1):
        if offline:
            fetched = offline.country(country)
            if fetched is None:
                country_stats.append({
                    "country": country["name"], "url": country["url"], "status": "missing_offline",
                    "kind": None, "results_found": None, "rows": 0,
                })
                continue
            body, final_url = fetched
        else:
            body, final_url = fetcher.get(country["url"])

        rows, stats = parse_country_page(body, final_url, country)
        if stats["status"] == "zero_rows" and not offline:
            form = country_search_form(power_search_body, country["name"])
            if form:
                body, final_url = fetcher.post(urljoin(BASE_URL, POWER_SEARCH_PATH), form)
                rows, stats = parse_country_page(body, final_url, country)
                stats["kind"] = f"search_form_{stats['kind']}"
                logger.info("%s: link page was empty; search form returned %d rows", country["name"], len(rows))
        if stats["status"] != "ok" and debug_dir:
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_file = debug_dir / f"country_{country_slug(country['href'])}.html"
            debug_file.write_bytes(body)
            stats["debug_html"] = str(debug_file)
        if stats["status"] != "ok":
            logger.warning(
                "%s: %s (rows=%s, results_found=%s)",
                country["name"], stats["status"], stats["rows"], stats["results_found"],
            )
        else:
            logger.info("[%d/%d] %s: %d rows (%s)", index, len(countries), country["name"], len(rows), stats["kind"])
        all_rows.extend(rows)
        country_stats.append(stats)

    unique: "OrderedDict[str, Dict[str, str]]" = OrderedDict()
    duplicates = []
    for row in all_rows:
        key = row["Source ID"] or row["Airport Link"]
        if key in unique:
            duplicates.append({"Source ID": key, "Country Page": row["Country Page"],
                               "kept_from": unique[key]["Country Page"]})
            continue
        unique[key] = row

    return {
        "rows": list(unique.values()),
        "raw_row_count": len(all_rows),
        "countries": country_stats,
        "duplicates": duplicates,
        "country_link_count": len(countries),
    }


def run_checks(result: Dict[str, Any]) -> Dict[str, Any]:
    fetched = [c for c in result["countries"] if c["status"] != "missing_offline"]
    expected_raw = sum(c["results_found"] or 0 for c in fetched if (c["kind"] or "").endswith("list")) + sum(
        1 for c in fetched if (c["kind"] or "").endswith("redirect")
    )
    bad_links = [
        r["Source ID"] for r in result["rows"]
        if not r["Airport Link"] or "/Airports/" in r["Airport Link"] or "/Airport-Info/" not in r["Airport Link"]
    ]
    by_name = {c["country"]: c for c in fetched}
    expected_nonempty = {
        name: (by_name[name]["rows"] if name in by_name else None) for name in EXPECTED_NONEMPTY
    }
    usa = by_name.get("USA")
    checks = {
        "raw_rows_equal_results_found_plus_redirects": result["raw_row_count"] == expected_raw,
        "expected_raw_rows": expected_raw,
        "raw_rows": result["raw_row_count"],
        "non_ok_countries": [
            {k: c.get(k) for k in ("country", "status", "rows", "results_found", "debug_html")}
            for c in fetched if c["status"] != "ok"
        ],
        "missing_offline_countries": [c["country"] for c in result["countries"] if c["status"] == "missing_offline"],
        "bad_links": bad_links,
        "expected_nonempty_regions": expected_nonempty,
        "usa_rows": usa["rows"] if usa else None,
        "usa_results_found": usa["results_found"] if usa else None,
    }
    checks["passed"] = bool(
        checks["raw_rows_equal_results_found_plus_redirects"]
        and not checks["non_ok_countries"]
        and not bad_links
        and all(v for v in expected_nonempty.values() if v is not None)
        and (usa is None or usa["rows"] == usa["results_found"])
    )
    return checks


def write_csv(rows: List[Dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})
    tmp.replace(path)


def main(argv: Optional[List[str]] = None) -> int:
    today = datetime.now().strftime("%Y%m%d")
    parser = argparse.ArgumentParser(description="Regenerate the AC-U-KWIK airport list CSV.")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / f"country_links_{today}.csv",
                        help="Output CSV (default: country_links_<YYYYMMDD>.csv in the project root)")
    parser.add_argument("--report", type=Path, default=None,
                        help="Diff/validation report JSON (default: <output>.report.json)")
    parser.add_argument("--old-csv", default=str(DEFAULT_OLD_CSV),
                        help="Previous list to diff against (default: country_links.csv; '' to skip the diff)")
    parser.add_argument("--debug-dir", type=Path, default=None,
                        help="Where to save HTML of 0-row / mismatched countries (default: <output stem>_debug/)")
    parser.add_argument("--cookies", type=Path, default=DEFAULT_COOKIES)
    parser.add_argument("--user-agent", default=None,
                        help="User-Agent header (default: env USER_AGENT or the Chrome 154 UA used for cookies.json)")
    parser.add_argument("--delay", type=float, default=1.0, help="Minimum seconds between requests")
    parser.add_argument("--offline-dir", type=Path, default=None,
                        help="Parse saved pages (power_search.html, country_<SLUG>.html) instead of fetching")
    parser.add_argument("--min-countries", type=int, default=MIN_COUNTRIES)
    parser.add_argument("--only", nargs="*", default=None,
                        help="Restrict to these country names or slugs (testing)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    output = args.output.resolve()
    old_csv = Path(args.old_csv).resolve() if args.old_csv else None
    if old_csv and output == old_csv:
        logger.error("Refusing to overwrite the old list %s; choose another --output", old_csv)
        return 1
    report_path = args.report or output.with_suffix(".report.json")
    debug_dir = args.debug_dir or output.parent / f"{output.stem}_debug"

    try:
        fetcher = None
        if not args.offline_dir:
            user_agent = args.user_agent or os.getenv("USER_AGENT") or DEFAULT_USER_AGENT
            fetcher = Fetcher(args.cookies, user_agent, delay=args.delay)
        result = generate(
            fetcher=fetcher,
            offline_dir=args.offline_dir,
            debug_dir=debug_dir,
            min_countries=args.min_countries,
            only=args.only,
        )
    except BlockedError as e:
        logger.error("ABORTED: %s", e)
        return 1

    checks = run_checks(result)
    write_csv(result["rows"], output)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output": str(output),
        "offline_dir": str(args.offline_dir) if args.offline_dir else None,
        "country_link_count": result["country_link_count"],
        "unique_rows": len(result["rows"]),
        "checks": checks,
        "duplicates": result["duplicates"],
        "countries": result["countries"],
        "diff": diff_against_old(result["rows"], load_old_rows(old_csv)) if old_csv else None,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Wrote %d rows to %s", len(result["rows"]), output)
    logger.info("Report: %s", report_path)
    if report["diff"]:
        logger.info("Diff vs %s: %s", old_csv, report["diff"]["summary"])
    if not checks["passed"]:
        logger.warning("Validation checks FAILED; see 'checks' in %s", report_path)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
