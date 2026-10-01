"""Offline tests for scripts/generate_airport_list.py (no network)."""
import csv
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from scraper import ScraperOrchestrator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "airport_list"
FULL_USA_PAGE = Path(
    "/private/tmp/claude-503/-Users-it-macmini-1-tahir-Documents/"
    "01e4f88b-2d4f-4c20-86a6-68704b4d9839/scratchpad/pages/country_USA.html"
)

_spec = importlib.util.spec_from_file_location(
    "generate_airport_list", PROJECT_ROOT / "scripts" / "generate_airport_list.py"
)
gal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gal)


def _country(href, name):
    return {"name": name, "href": href, "url": "https://acukwik.com" + href}


@pytest.fixture
def offline_dir(tmp_path):
    directory = tmp_path / "pages"
    shutil.copytree(FIXTURES, directory)
    return directory


def test_power_search_country_links_keep_encoded_region_urls():
    links = gal.parse_country_links((FIXTURES / "power_search.html").read_bytes())
    assert len(links) == 249
    by_name = {link["name"]: link for link in links}
    assert by_name["USA"]["url"] == "https://acukwik.com/Airports/USA"
    assert by_name["SPAIN/CANARY ISLANDS"]["url"] == "https://acukwik.com/Airports/SPAIN%2fCANARY-ISLANDS"
    assert by_name["BONAIRE, ST EUSTATIUS & SABA"]["href"] == "/Airports/BONAIRE%2c-ST-EUSTATIUS-%7e-SABA"
    assert gal.country_slug(by_name["SPAIN/CANARY ISLANDS"]["href"]) == "SPAIN_CANARY_ISLANDS"


def test_country_list_page_rows_and_results_found():
    rows, stats = gal.parse_country_page(
        (FIXTURES / "country_USA.html").read_bytes(),
        "https://acukwik.com/Airports/USA",
        _country("/Airports/USA", "USA"),
    )
    assert stats["kind"] == "list"
    assert stats["status"] == "ok"
    assert stats["results_found"] == len(rows) == 25
    assert rows[0] == {
        "ICAO": "",
        "Airport Name": "Abbeville",
        "City": "Abbeville",
        "State": "SC",
        "Country": "USA",
        "Airport Link": "https://acukwik.com/Airport-Info/SC81",
        "Source ID": "SC81",
        "Country Page": "https://acukwik.com/Airports/USA",
    }
    assert rows[1]["ICAO"] == "KIYA"
    assert rows[1]["State"] == "LA"  # trailing &nbsp; stripped
    assert all(r["Airport Link"].startswith("https://acukwik.com/Airport-Info/") for r in rows)


def test_single_airport_country_redirect_gives_one_row():
    # Saved page has no response URL; the redirect is detected from the form action.
    rows, stats = gal.parse_country_page(
        (FIXTURES / "country_MALTA.html").read_bytes(),
        "https://acukwik.com/Airports/MALTA",
        _country("/Airports/MALTA", "MALTA"),
    )
    assert stats["kind"] == "redirect"
    assert stats["status"] == "ok"
    assert stats["final_url"] == "https://acukwik.com/Airport-Info/LMML"
    # The airport page's restriction rows (div.result.clearfix) must not become airports.
    assert rows == [{
        "ICAO": "LMML",
        "Airport Name": "Malta International (Luqa Intl)",
        "City": "Valletta",
        "State": "",
        "Country": "MALTA",
        "Airport Link": "https://acukwik.com/Airport-Info/LMML",
        "Source ID": "LMML",
        "Country Page": "https://acukwik.com/Airports/MALTA",
    }]


def test_count_mismatch_saves_debug_html(offline_dir, tmp_path):
    usa = offline_dir / "country_USA.html"
    usa.write_text(usa.read_text(encoding="utf-8").replace("25 results found", "26 results found"), encoding="utf-8")
    debug_dir = tmp_path / "debug"
    result = gal.generate(offline_dir=offline_dir, debug_dir=debug_dir, only=["USA", "MALTA"])
    usa_stats = next(c for c in result["countries"] if c["country"] == "USA")
    assert usa_stats["status"] == "count_mismatch"
    assert Path(usa_stats["debug_html"]).exists()
    checks = gal.run_checks(result)
    assert not checks["passed"]
    assert not checks["raw_rows_equal_results_found_plus_redirects"]


def test_too_few_country_links_aborts(offline_dir):
    with pytest.raises(gal.BlockedError):
        gal.generate(offline_dir=offline_dir, min_countries=250)


@pytest.mark.parametrize("status, url, body, history", [
    (403, "https://acukwik.com/Airports/USA", "<html></html>", []),
    (200, "https://acukwik.com/Airports/USA", "<title>Just a moment...</title>", []),
    (200, "https://acukwik.com/Airports/USA", "<script>window._cf_chl_opt={};cf-chl</script>", []),
    (200, "https://acukwik.com/Login?returnurl=%2fAirports%2fUSA", "<html></html>", []),
    (200, "https://acukwik.com/Airports/USA", "<html></html>", ["/Login?returnurl=x"]),
])
def test_blocked_responses_stop_the_run(status, url, body, history):
    with pytest.raises(gal.BlockedError):
        gal.check_blocked(status, url, body, history)


def test_normal_pages_are_not_flagged_as_blocked():
    for name in ("power_search.html", "country_USA.html", "country_MALTA.html"):
        gal.check_blocked(200, "https://acukwik.com/Airports/X", (FIXTURES / name).read_text(encoding="utf-8"))


def test_fetcher_requests_encoded_urls_exactly_and_uses_cookies(tmp_path, monkeypatch):
    cookies = tmp_path / "cookies.json"
    cookies.write_text(json.dumps([{"name": "a", "value": "b", "domain": ".acukwik.com", "path": "/"}]))
    fetcher = gal.Fetcher(cookies, "UA-TEST", delay=0)
    sent = []

    class _Response:
        status_code = 200
        history = []
        content = b"<html></html>"
        text = "<html></html>"

        def __init__(self, url):
            self.url = url

        def raise_for_status(self):
            return None

    def fake_send(prepared, **kwargs):
        sent.append(prepared)
        return _Response(prepared.url)

    monkeypatch.setattr(fetcher.session, "send", fake_send)
    url = "https://acukwik.com/Airports/BONAIRE%2c-ST-EUSTATIUS-%7e-SABA"
    fetcher.get(url)
    assert sent[0].url == url
    assert sent[0].headers["User-Agent"] == "UA-TEST"
    assert "a=b" in sent[0].headers.get("Cookie", "")


def test_main_offline_writes_new_csv_and_diff_report(offline_dir, tmp_path):
    old_csv = tmp_path / "country_links.csv"
    old_csv.write_text(
        "ICAO,Airport Name,City,State,Country,Airport Link\n"
        "KIYA,Old Name,Abbeville,LA,USA,https://acukwik.com/Airport-Info/KIYA\n"
        ",Gone Field,Nowhere,TX,USA,https://acukwik.com/Airport-Info/GONE\n"
        ",,,,,https://acukwik.com/Airport-Info/LMML\n"
        ",,,,Hawaii,https://acukwik.com/Airports/USA%2fHAWAII\n",
        encoding="utf-8",
    )
    output = tmp_path / "out" / "country_links_test.csv"
    exit_code = gal.main([
        "--offline-dir", str(offline_dir),
        "--output", str(output),
        "--old-csv", str(old_csv),
        "--only", "USA", "MALTA",
    ])
    assert exit_code == 0
    assert old_csv.read_text(encoding="utf-8").startswith("ICAO,Airport Name")  # old file untouched

    with open(output, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert reader.fieldnames == gal.FIELDNAMES
    assert len(rows) == 26
    assert len({r["Source ID"] for r in rows}) == 26

    report = json.loads(output.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["checks"]["passed"]
    assert report["checks"]["usa_rows"] == 25
    diff = report["diff"]
    assert {r["Source ID"] for r in diff["removed"]} == {"GONE"}
    assert "SC81" in {r["Source ID"] for r in diff["added"]}
    assert diff["renamed"] == [{"Source ID": "KIYA", "old": "Old Name", "new": "Abbeville Chris Crusta Memorial"}]
    assert diff["old_invalid_rows"][0]["Airport Link"].endswith("USA%2fHAWAII")
    lmml = next(c for c in diff["changed"] if c["Source ID"] == "LMML")
    assert lmml["fields"]["ICAO"] == {"old": "", "new": "LMML"}
    assert diff["country_column_counts"]["USA"] == {"old": 2, "new": 25}


def test_main_refuses_to_overwrite_old_csv(offline_dir, tmp_path):
    old_csv = tmp_path / "country_links.csv"
    old_csv.write_text("ICAO,Airport Name,City,State,Country,Airport Link\n", encoding="utf-8")
    assert gal.main(["--offline-dir", str(offline_dir), "--output", str(old_csv), "--old-csv", str(old_csv)]) == 1


def test_generated_csv_loads_in_scraper(offline_dir, tmp_path):
    output = tmp_path / "airports.csv"
    gal.main(["--offline-dir", str(offline_dir), "--output", str(output), "--old-csv", "", "--only", "USA", "MALTA"])
    orchestrator = object.__new__(ScraperOrchestrator)
    orchestrator.config = {"input": {"csv_paths": [str(output)]}}
    orchestrator.limit = None
    records = orchestrator.load_input_data()
    assert len(records) == 26
    assert records[0]["Airport Link"] == "https://acukwik.com/Airport-Info/SC81"
    assert records[0]["ICAO"] == ""
    assert next(r for r in records if r["Source ID"] == "LMML")["ICAO"] == "LMML"


@pytest.mark.skipif(not FULL_USA_PAGE.exists(), reason="full saved USA country page not available")
def test_full_usa_page_gives_18572_rows():
    rows, stats = gal.parse_country_page(
        FULL_USA_PAGE.read_bytes(), "https://acukwik.com/Airports/USA", _country("/Airports/USA", "USA")
    )
    assert stats["status"] == "ok"
    assert stats["results_found"] == len(rows) == 18572
    assert len({r["Source ID"] for r in rows}) == 18572
