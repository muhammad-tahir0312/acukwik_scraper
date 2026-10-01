"""HTTP-mode scraper tests: pagination, retries, user agent and Basic-Info profiles.

Every request goes to a fake session that serves saved (anonymised) live pages
from tests/fixtures, so no test touches the network.
"""
from pathlib import Path
import shutil

import pytest
import requests

import scraper
from basic_info import is_basic_info_url, parse_basic_info
from html_driver import HtmlDriver
from parsers import NearbyParser
from progress import ProgressTracker
from scraper import DEFAULT_USER_AGENT, IncompleteScrapeError, ScraperOrchestrator

FIXTURES = Path(__file__).parent / "fixtures"
NEARBY_URL = "https://acukwik.com/Nearby/EGLL"
AUTHENTICATED_PAGE = '<html><body><li id="dnn_MyAccountLink1_MyAccount">TEST USER</li>{}</body></html>'


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class _Response:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class _PagerSession:
    """Answers nearby postbacks by the viewstate of the page that was posted."""

    def __init__(self, pages=None, fail_on=()):
        self.pages = pages or {
            "VIEWSTATE-nearby_EGLL_p1": "nearby_EGLL_p2.html",
            "VIEWSTATE-nearby_EGLL_p2": "nearby_EGLL_p3.html",
            "VIEWSTATE-nearby_EGLL_p3": "nearby_EGLL_p4.html",
        }
        self.fail_on = set(fail_on)
        self.posts = []
        self.gets = []

    def post(self, url, data=None, **kwargs):
        self.posts.append((url, data, kwargs))
        viewstate = data["__VIEWSTATE"]
        if viewstate in self.fail_on:
            self.fail_on.discard(viewstate)
            raise requests.ConnectionError("connection reset")
        return _Response(_fixture(self.pages[viewstate]))

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        raise AssertionError(f"unexpected GET {url}")


def _orchestrator(tmp_path, **scraping):
    orchestrator = object.__new__(ScraperOrchestrator)
    orchestrator.config = {
        "source": {"name": "acukwik"},
        "scraping": {
            "fetch_mode": "http",
            "max_retries": 2,
            "retry_backoff": 0,
            "scrape_clearance": False,
            "scrape_nearby": False,
            "scrape_basic_info": False,
            **scraping,
        },
        "selenium": {"user_agent": None},
        "authentication": {"base_url": "https://acukwik.com"},
    }
    orchestrator.source = "acukwik"
    orchestrator.html_cache_dir = tmp_path / "cache"
    orchestrator.html_cache_dir.mkdir()
    orchestrator.keep_html_cache = False
    orchestrator.stats = {"processed": 0, "success": 0, "failed": 0, "skipped": 0}
    return orchestrator


def _nearby_first_page(tmp_path):
    first_page = tmp_path / "nearby_p1.html"
    shutil.copy(FIXTURES / "nearby_EGLL_p1.html", first_page)
    entity = NearbyParser(HtmlDriver.from_file(first_page, current_url=NEARBY_URL)).parse(
        NEARBY_URL, "EGLL", load_page=False
    )
    return first_page, entity


# --- postback form -----------------------------------------------------------

def test_postback_form_submits_selects_and_checked_inputs_like_a_browser():
    html = """<html><body><form id="Form">
      <input type="hidden" name="__VIEWSTATE" value="VS">
      <input type="text" name="search" value="">
      <input type="checkbox" name="unchecked">
      <input type="checkbox" name="checked" checked="checked">
      <input type="radio" name="choice" value="b" checked>
      <input type="submit" name="go" value="Go">
      <select name="first"><option value="0">Any</option><option value="1">One</option></select>
      <select name="chosen"><option value="0">Any</option><option value="5000" selected>5000 ft</option></select>
      <textarea name="note">hello</textarea>
      <a id="x_rPagingBasic_ctl05_lbtnNext" href="javascript:__doPostBack('x$lbtnNext','')">Next</a>
    </form></body></html>"""
    form = ScraperOrchestrator._next_page_postback(html)
    assert form["__EVENTTARGET"] == "x$lbtnNext"
    assert form["__EVENTARGUMENT"] == ""
    assert form["__VIEWSTATE"] == "VS"
    assert form["first"] == "0"
    assert form["chosen"] == "5000"
    assert form["checked"] == "on"
    assert form["choice"] == "b"
    assert form["note"] == "hello"
    assert "unchecked" not in form
    assert "go" not in form


def test_saved_nearby_page_postback_includes_runway_filter_and_viewstate():
    form = ScraperOrchestrator._next_page_postback(_fixture("nearby_EGLL_p1.html"))
    assert form["__EVENTTARGET"] == "dnn$ctr577$VDC$ctl00$rPagingBasic$ctl05$lbtnNext"
    assert form["dnn$ctr577$VDC$ctl00$ddlLength"] == "0"
    assert form["dnn$ctr577$VDC$ctl00$hfPageIndex"] == "1"
    assert form["__VIEWSTATE"] == "VIEWSTATE-nearby_EGLL_p1"
    assert "dnn$ctr577$VDC$ctl00$ckbHeliports" not in form
    # The last page's Next link is disabled.
    assert ScraperOrchestrator._next_page_postback(_fixture("nearby_EGLL_p4.html")) is None


def test_page_index_is_read_from_saved_pages():
    for number in range(1, 5):
        assert ScraperOrchestrator._page_index(_fixture(f"nearby_EGLL_p{number}.html")) == str(number)
    assert ScraperOrchestrator._page_index("<html></html>") is None


# --- HTTP pagination ---------------------------------------------------------

def test_http_pagination_collects_every_nearby_row(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    first_page, entity = _nearby_first_page(tmp_path)
    entity["data"]["expected_count"] = 79
    session = _PagerSession()
    cached = []
    orchestrator._add_nearby_pages(entity, NEARBY_URL, first_page, "EGLL", "acukwik_EGLL", session, {}, cached)

    assert entity["scrape_status"] == "SUCCESS"
    assert entity["data"]["pages_fetched"] == 4
    assert len(entity["data"]["nearby_airports"]) == 79
    assert len(cached) == 3
    assert [data["dnn$ctr577$VDC$ctl00$hfPageIndex"] for _, data, _ in session.posts] == ["1", "2", "3"]
    assert all(kwargs["headers"]["User-Agent"] == DEFAULT_USER_AGENT for _, _, kwargs in session.posts)


def test_http_pagination_applies_page_one_map_markers_to_every_row(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    first_page, entity = _nearby_first_page(tmp_path)
    assert entity["data"]["expected_count"] == 79
    orchestrator._add_nearby_pages(
        entity, NEARBY_URL, first_page, "EGLL", "acukwik_EGLL", _PagerSession(), {}, []
    )

    rows = entity["data"]["nearby_airports"]
    assert len(rows) == 79
    assert all(isinstance(row.get("latitude"), float) for row in rows)
    assert all(isinstance(row.get("longitude"), float) for row in rows)
    later_rows = {row.get("icao") or row.get("source_airport_id"): row for row in rows[25:]}
    assert {"EGDD", "EGTC", "EGTH", "EGTO", "EGTK"} <= set(later_rows)
    assert later_rows["EGTK"].get("marker_category")


def test_postback_returning_the_same_page_fails_instead_of_looping(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    first_page, entity = _nearby_first_page(tmp_path)
    session = _PagerSession(pages={"VIEWSTATE-nearby_EGLL_p1": "nearby_EGLL_p1.html"})
    with pytest.raises(RuntimeError, match="page index '1'"):
        orchestrator._add_nearby_pages(entity, NEARBY_URL, first_page, "EGLL", "acukwik_EGLL", session, {}, [])
    assert len(session.posts) == 1
    assert entity["scrape_status"] == "PARTIAL"
    assert entity["data"]["pages_fetched"] is None
    assert entity["errors"][-1]["field"] == "nearby_pagination"
    # The rows already parsed are kept.
    assert len(entity["data"]["nearby_airports"]) == 25


@pytest.mark.parametrize("body, message", [
    ('<html><title>Just a moment...</title></html>', "Cloudflare"),
    ('<html><body><div class="reg-form loginForm">Log in to AC-U-KWIK</div></body></html>', "Authentication failure"),
    ('<html><body><li id="dnn_MyAccountLink1_MyAccount"></li>'
     '<input type="hidden" name="x$hfPageIndex" value="2" /></body></html>', "no result rows"),
])
def test_invalid_postback_responses_are_rejected(tmp_path, monkeypatch, body, message):
    orchestrator = _orchestrator(tmp_path)
    monkeypatch.setattr(orchestrator, "_verify_authentication", _strict_auth_check)
    first_page, entity = _nearby_first_page(tmp_path)

    class Session(_PagerSession):
        def post(self, url, data=None, **kwargs):
            self.posts.append((url, data, kwargs))
            return _Response(body)

    with pytest.raises(Exception, match=message):
        orchestrator._add_nearby_pages(entity, NEARBY_URL, first_page, "EGLL", "acukwik_EGLL", Session(), {}, [])
    assert entity["scrape_status"] == "PARTIAL"


def _strict_auth_check(driver, url, external_id):
    """The orchestrator's login-page check, without writing diagnostics to disk."""
    source = driver.page_source
    if 'id="dnn_MyAccountLink1_MyAccount"' in source:
        return
    if "loginForm" in source:
        raise Exception("Authentication failure detected - cookies may be invalid")


def test_real_authentication_check_rejects_login_postback(tmp_path, monkeypatch):
    # Diagnostics are written next to the module; keep them in tmp_path.
    monkeypatch.setattr(scraper, "__file__", str(tmp_path / "scraper.py"))
    orchestrator = _orchestrator(tmp_path)
    login = '<html><body><div class="reg-form loginForm">Log in to AC-U-KWIK</div></body></html>'
    with pytest.raises(Exception, match="Authentication failure"):
        orchestrator._validate_paginated_page(login, 2, "nearby", NEARBY_URL)
    assert list((tmp_path / "errors").glob("auth_failure_nearby_p2_*.html"))


def test_nearby_row_count_must_match_expected_count(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    first_page, entity = _nearby_first_page(tmp_path)
    entity["data"]["expected_count"] = 80
    with pytest.raises(RuntimeError, match="does not match expected 80"):
        orchestrator._add_nearby_pages(
            entity, NEARBY_URL, first_page, "EGLL", "acukwik_EGLL", _PagerSession(), {}, []
        )
    assert entity["scrape_status"] == "PARTIAL"
    assert entity["errors"][-1]["field"] == "nearby_count"
    assert len(entity["data"]["nearby_airports"]) == 79


def test_browser_pagination_waits_for_the_pager_index(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper.time, "sleep", lambda _seconds: None)
    orchestrator = _orchestrator(tmp_path)
    first_page = tmp_path / "nearby_p1.html"
    shutil.copy(FIXTURES / "nearby_EGLL_p1.html", first_page)
    # The last saved page (Next disabled), served as page 2 so the run ends there.
    page_two = _fixture("nearby_EGLL_p4.html").replace(
        'id="dnn_ctr577_VDC_ctl00_hfPageIndex" value="4"', 'id="dnn_ctr577_VDC_ctl00_hfPageIndex" value="2"'
    )
    # A changed but not-yet-updated page (still index 1) must not be accepted.
    in_progress = _fixture("nearby_EGLL_p1.html").replace("</body>", "<div>loading</div></body>")

    class Driver:
        def __init__(self):
            self.sources = []

        def execute_script(self, script, target):
            assert target.endswith("lbtnNext")
            if not self.sources:
                self.sources = [in_progress, in_progress, page_two]

        @property
        def page_source(self):
            return self.sources.pop(0) if len(self.sources) > 1 else self.sources[0]

    driver = Driver()
    holder = {"transport": "browser", "driver": driver}
    pages = orchestrator._fetch_paginated_pages(NEARBY_URL, first_page, "acukwik_EGLL", "nearby", None, holder)
    assert len(pages) == 1
    assert ScraperOrchestrator._page_index(pages[0].read_text(encoding="utf-8")) == "2"


# --- user agent --------------------------------------------------------------

def test_null_user_agent_uses_env_or_full_chrome_user_agent(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    monkeypatch.delenv("USER_AGENT", raising=False)
    assert orchestrator._build_requests_session().headers["User-Agent"] == DEFAULT_USER_AGENT
    assert "Chrome/154" in DEFAULT_USER_AGENT
    monkeypatch.setenv("USER_AGENT", "Mozilla/5.0 Env")
    assert orchestrator._build_requests_session().headers["User-Agent"] == "Mozilla/5.0 Env"
    orchestrator.config["selenium"]["user_agent"] = "Mozilla/5.0 Configured"
    assert orchestrator._user_agent() == "Mozilla/5.0 Configured"


def test_page_fetch_sends_a_real_user_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("USER_AGENT", raising=False)
    orchestrator = _orchestrator(tmp_path)

    class Session:
        def get(self, url, **kwargs):
            self.headers = kwargs["headers"]
            return _Response("<html>ok</html>")

    session = Session()
    path = orchestrator._fetch_html_to_cache_with_session("https://acukwik.com/Airport-Info/EGLL", "acukwik_EGLL", "airport", session)
    assert path.read_text() == "<html>ok</html>"
    assert session.headers["User-Agent"] == DEFAULT_USER_AGENT


def test_default_config_fetches_over_http(monkeypatch):
    from config_loader import load_config
    monkeypatch.delenv("FETCH_MODE", raising=False)
    config = load_config(str(Path(scraper.__file__).with_name("config.yaml")))
    assert config["scraping"]["fetch_mode"] == "http"
    assert config["scraping"]["scrape_basic_info"] is True
    monkeypatch.setenv("FETCH_MODE", "selenium")
    assert load_config(str(Path(scraper.__file__).with_name("config.yaml")))["scraping"]["fetch_mode"] == "selenium"


# --- Basic-Info profiles -----------------------------------------------------

def test_parse_basic_info_egll():
    profile = parse_basic_info(_fixture("basic_info_EGLL.html"), "https://acukwik.com/Basic-Info/EGLL/SIGNATURE-AVIATION")
    assert profile["name"] == "SIGNATURE AVIATION"
    assert profile["vendor_id"] == "5338"
    assert profile["source_identifiers"] == {"hfVendor_ID": "5338"}
    assert profile["airport_codes"] == {"icao": "EGLL", "iata": "LHR"}
    assert profile["hours"] == "0600-2230L"
    assert profile["phone_after_hours"] == "+44 33 0027 1274"
    assert profile["fuel_brand"] == "INDEPENDENT"
    assert profile["fuel_types"] == ["Into-Plane Fuelling", "Jet A-1"]
    assert profile["website"] == "http://www.signatureaviation.com/locations/LHR"
    assert len(profile["fuel_cards_accepted"]) == 11
    assert profile["fuel_cards_accepted"][0] == "Ascend (Air Routing)"
    assert "Visa" in profile["fuel_cards_accepted"]
    assert profile["email_buttons"][0]["data-id"] == "5338"
    labels = [field["label"] for field in profile["raw_fields"]]
    assert labels == [
        "Address", "Phone", "Tel After Hours", "Email", "Website", "Frequency",
        "Hours", "Fuel brand", "Fuel types", "Brand",
    ]
    assert profile["raw_fields"][-1]["section"] == "Fuel"
    # Review form, logged-in user, ads and scripts are not profile content.
    assert "TEST USER" not in profile["raw_text"]
    assert "googletag" not in profile["raw_text"]
    assert "Hours\n0600-2230L" in profile["raw_text"]
    assert {"text": "All Airport Data for EGLL", "url": "https://acukwik.com/Airport-Info/EGLL"} in profile["links"]


def test_parse_basic_info_kteb():
    profile = parse_basic_info(_fixture("basic_info_KTEB.html"), "https://acukwik.com/Basic-Info/KTEB/ATLANTIC-AVIATION")
    assert profile["name"] == "ATLANTIC AVIATION"
    assert profile["vendor_id"] == "21109"
    assert profile["airport_codes"] == {"icao": "KTEB", "iata": "TEB", "faa": "TEB"}
    assert profile["hours"] == "H24"
    assert profile["fax"] == "+1 201 288 7503"
    assert profile["fuel_types"] == ["Avgas 100LL", "Jet A"]
    assert profile["fuel_cards_accepted"] == [
        "Multi Service", "UVair", "World Fuel Services", "American Express",
        "AvCard", "Discover", "MasterCard",
    ]
    assert [fbo["name"] for fbo in profile["additional_fbos"]] == [
        "JET AVIATION", "SIGNATURE AVIATION EAST", "SIGNATURE AVIATION SOUTH", "SIGNATURE AVIATION WEST",
    ]
    assert profile["additional_fbos"][0]["ground_handler_id"] == "20094"
    assert profile["additional_fbos"][0]["details"] == "+1 201 462 4000 / 131.425 / PHILLIPS 66"


def test_non_profile_page_has_no_name():
    assert parse_basic_info("<html><body><div class='loginForm'></div></body></html>")["name"] is None


def test_basic_info_url_detection():
    assert is_basic_info_url("https://acukwik.com/Basic-Info/KTEB/ATLANTIC-AVIATION")
    assert is_basic_info_url("/Basic-Info/KTEB/ATLANTIC-AVIATION")
    assert not is_basic_info_url("https://example.com/Basic-Info/KTEB/X")
    assert not is_basic_info_url("https://acukwik.com/Airport-Info/KTEB")


def _organization(name, profile_url=None, links=(), contacts=()):
    return {
        "entity_type": "organization",
        "external_id": f"acukwik_org_{name}",
        "scrape_status": "SUCCESS",
        "data": {
            "name": name,
            "source_profile_url": profile_url,
            "links": list(links),
            "contacts": list(contacts),
            "phone": "listing phone",
        },
        "observed_fields": ["contacts", "name"],
        "errors": [],
    }


class _ProfileSession:
    def __init__(self, fail=False):
        self.gets = []
        self.fail = fail

    def get(self, url, **kwargs):
        self.gets.append(url)
        if self.fail:
            raise requests.ConnectionError("timed out")
        return _Response(_fixture("basic_info_KTEB.html"))


def test_profiles_are_fetched_once_per_url_and_attached(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    url = "https://acukwik.com/Basic-Info/KTEB/ATLANTIC-AVIATION"
    fbo = _organization("ATLANTIC AVIATION", profile_url=url)
    fuel = _organization("ATLANTIC AVIATION", links=[{"text": "ATLANTIC AVIATION", "url": url}])
    other = _organization("NO PROFILE", contacts=[{"type": "website", "value": "https://example.com"}])
    airport = {"entity_type": "airport", "data": {"source_profile_url": url}}
    session = _ProfileSession()
    cached = []

    failures = orchestrator._add_basic_info_profiles([airport, fbo, fuel, other], "acukwik_KTEB", session, {}, cached)

    assert failures == []
    assert session.gets == [url]
    assert len(cached) == 1
    for entity in (fbo, fuel):
        profile = entity["data"]["profile"]
        assert profile["vendor_id"] == "21109"
        assert profile["hours"] == "H24"
        assert profile["matches_listing"] is True
        assert entity["data"]["phone"] == "listing phone"  # listing fields untouched
        assert "profile" in entity["observed_fields"]
    assert fbo["data"]["profile"] is not fuel["data"]["profile"]
    assert "profile" not in other["data"]
    assert "profile" not in airport["data"]


def test_profile_failure_is_recorded_and_raised(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    fbo = _organization("ATLANTIC AVIATION", profile_url="https://acukwik.com/Basic-Info/KTEB/ATLANTIC-AVIATION")
    with pytest.raises(RuntimeError, match="timed out"):
        orchestrator._add_basic_info_profiles([fbo], "acukwik_KTEB", _ProfileSession(fail=True), {}, [])
    assert fbo["errors"][0]["field"] == "profile"

    fbo["errors"].clear()
    failures = orchestrator._add_basic_info_profiles(
        [fbo], "acukwik_KTEB", _ProfileSession(fail=True), {}, [], stop_on_error=False
    )
    assert len(failures) == 1
    assert fbo["errors"][0]["error"] == "timed out"
    assert "profile" not in fbo["data"]


# --- retries and resume ------------------------------------------------------

class _Writer:
    def __init__(self):
        self.success = []
        self.failures = []

    def write_success(self, entity):
        self.success.append(entity)

    def write_failure(self, external_id, url, error, entity_type="unknown"):
        self.failures.append((external_id, error))


class _AirportParser:
    def __init__(self, driver):
        self.driver = driver

    def parse(self, url):
        return [
            {
                "entity_type": "airport",
                "external_id": "acukwik_airport_EGLL",
                "url": url,
                "scrape_status": "SUCCESS",
                "data": {"name": "LONDON HEATHROW", "icao": "EGLL", "source_airport_id": "EGLL"},
                "errors": [],
            },
            _organization("ATLANTIC AVIATION", profile_url="https://acukwik.com/Basic-Info/KTEB/ATLANTIC-AVIATION"),
        ]


def _run_airport(tmp_path, monkeypatch, fetch, session=None, **scraping):
    monkeypatch.setattr(scraper.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(scraper, "AirportPageParser", _AirportParser)
    orchestrator = _orchestrator(tmp_path, **scraping)
    orchestrator.output_writer = _Writer()
    orchestrator.progress_tracker = ProgressTracker(str(tmp_path / "progress.json"))
    orchestrator._build_requests_session = lambda: session or _PagerSession()
    calls = []

    def fake_fetch(url, external_id, page_label, session, holder):
        calls.append(page_label)
        content = fetch(url, page_label, calls.count(page_label))
        path = orchestrator.html_cache_dir / f"{page_label}_{len(calls)}.html"
        path.write_text(content, encoding="utf-8")
        return path

    orchestrator._fetch_page_to_cache = fake_fetch
    record = {"ICAO": "EGLL", "Airport Link": "https://acukwik.com/Airport-Info/EGLL"}
    result = orchestrator.scrape_record(record)
    external_id = orchestrator._generate_external_id(record["Airport Link"], "airport")
    return orchestrator, result, external_id, calls


def test_clearance_failure_retries_then_is_not_marked_completed(tmp_path, monkeypatch):
    def fetch(url, label, count):
        if label == "clearance":
            raise requests.ConnectionError("clearance down")
        return AUTHENTICATED_PAGE.format("")

    orchestrator, result, external_id, calls = _run_airport(
        tmp_path, monkeypatch, fetch, scrape_clearance=True
    )
    assert result is None
    assert calls == ["airport", "clearance", "airport", "clearance"]
    tracker = orchestrator.progress_tracker
    assert not tracker.is_completed(external_id)
    assert tracker.failed_ids[external_id] == 1
    assert tracker.should_process(external_id, 2)  # a resumed run retries it
    # What was parsed on the final attempt is still written.
    writer = orchestrator.output_writer
    assert [entity["entity_type"] for entity in writer.success] == ["airport", "organization"]
    assert writer.success[0]["errors"][0]["field"] == "clearance"
    assert "clearance down" in writer.failures[0][1]
    assert orchestrator.stats["failed"] == 1


def test_transient_nearby_page_failure_is_retried_to_completion(tmp_path, monkeypatch):
    session = _PagerSession(fail_on={"VIEWSTATE-nearby_EGLL_p2"})

    def fetch(url, label, count):
        if label == "nearby":
            return _fixture("nearby_EGLL_p1.html")
        return AUTHENTICATED_PAGE.format("")

    orchestrator, result, external_id, calls = _run_airport(
        tmp_path, monkeypatch, fetch, session=session, scrape_nearby=True
    )
    assert result is not None
    assert calls == ["airport", "nearby", "airport", "nearby"]
    assert orchestrator.progress_tracker.is_completed(external_id)
    nearby = [entity for entity in orchestrator.output_writer.success if entity["entity_type"] == "nearby_airports"]
    assert len(nearby) == 1
    assert len(nearby[0]["data"]["nearby_airports"]) == 79
    assert nearby[0]["data"]["pages_fetched"] == 4
    assert orchestrator.output_writer.failures == []


def test_profile_failure_on_every_attempt_leaves_airport_retryable(tmp_path, monkeypatch):
    def fetch(url, label, count):
        if label.startswith("profile"):
            raise requests.ConnectionError("profile down")
        return AUTHENTICATED_PAGE.format("")

    orchestrator, result, external_id, calls = _run_airport(
        tmp_path, monkeypatch, fetch, scrape_basic_info=True
    )
    assert calls == ["airport", "profile1", "airport", "profile1"]
    assert not orchestrator.progress_tracker.is_completed(external_id)
    organization = orchestrator.output_writer.success[1]
    assert organization["errors"][0]["field"] == "profile"
    assert "profile" not in organization["data"]


def test_profile_is_attached_during_a_full_scrape(tmp_path, monkeypatch):
    def fetch(url, label, count):
        if label.startswith("profile"):
            return _fixture("basic_info_KTEB.html")
        return AUTHENTICATED_PAGE.format("")

    orchestrator, result, external_id, calls = _run_airport(
        tmp_path, monkeypatch, fetch, scrape_basic_info=True
    )
    assert calls == ["airport", "profile1"]
    assert orchestrator.progress_tracker.is_completed(external_id)
    assert orchestrator.output_writer.success[1]["data"]["profile"]["fuel_types"] == ["Avgas 100LL", "Jet A"]


def test_incomplete_scrape_error_is_a_runtime_error():
    assert issubclass(IncompleteScrapeError, RuntimeError)
