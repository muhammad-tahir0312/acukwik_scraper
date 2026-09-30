from scraper import ScraperOrchestrator
import json
from pathlib import Path
import pytest


def test_local_credentials_load_without_overriding_environment(tmp_path, monkeypatch):
    from config_loader import load_config
    config_path = tmp_path / "config.yaml"
    config_path.write_text(Path(__file__).parents[1].joinpath("config.yaml").read_text())
    (tmp_path / ".env").write_text("AUTH_EMAIL=local@example.test\nAUTH_PASSWORD='literal${VALUE}'\n")
    monkeypatch.setenv("AUTH_EMAIL", "environment@example.test")
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    config = load_config(str(config_path))
    assert config["authentication"]["email"] == "environment@example.test"
    assert config["authentication"]["password"] == "literal${VALUE}"


def test_selenium_mode_does_not_send_http_request(tmp_path):
    class Driver:
        title = "Airport information"
        page_source = "<h1>Test airport</h1>"

        def get(self, url):
            self.url = url

    driver = Driver()
    orchestrator = object.__new__(ScraperOrchestrator)
    orchestrator.config = {"scraping": {"fetch_mode": "selenium"}}
    orchestrator.html_cache_dir = tmp_path
    orchestrator._fetch_html_to_cache_with_session = lambda *args: pytest.fail("HTTP used in Selenium mode")
    path = orchestrator._fetch_page_to_cache(
        "https://acukwik.com/Airport-Info/TEST", "TEST", "airport", object(), {"driver": driver}
    )
    assert path.read_text() == driver.page_source
    assert driver.url.endswith("/TEST")


def test_input_limit_bounds_the_run(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("ICAO,Airport Link\nONE,https://example.test/ONE\nTWO,https://example.test/TWO\n")
    orchestrator = object.__new__(ScraperOrchestrator)
    orchestrator.config = {"input": {"csv_paths": [str(source)]}}
    orchestrator.limit = 1
    assert [row["ICAO"] for row in orchestrator.load_input_data()] == ["ONE"]


def test_full_source_id_is_retained_for_airports_without_icao():
    orchestrator = object.__new__(ScraperOrchestrator)
    assert orchestrator._extract_airport_id_from_url("https://acukwik.com/Airport-Info/ACKTNON") == "ACKTNON"
    assert orchestrator._extract_airport_id_from_url("https://acukwik.com/Airport-Info/HCMB?x=1") == "HCMB"


class _Button:
    def __init__(self, class_name, data_id, service_type_id=None):
        self.values = {
            "class": class_name,
            "data-id": data_id,
            "data-service": service_type_id,
        }

    def get_attribute(self, name):
        return self.values.get(name)


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"emailHtml": '<a href="mailto:test@example.test">Email</a>'}


class _Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


def test_email_resolver_routes_all_button_types():
    orchestrator = object.__new__(ScraperOrchestrator)
    orchestrator.config = {"authentication": {"base_url": "https://acukwik.com"}, "selenium": {}}
    session = _Session()
    resolver = orchestrator._build_email_resolver(session)

    assert resolver(_Button("aEmail", "HCMB")) == "test@example.test"
    assert session.calls[-1][0].endswith("/GetARPTEmail")
    assert session.calls[-1][1]["params"] == {"ICAO": "HCMB"}

    assert resolver(_Button("ghEmail", "32482", "10")) == "test@example.test"
    assert session.calls[-1][0].endswith("/GetGHEmail")
    assert session.calls[-1][1]["params"] == {
        "GROUND_HANDLER_ID": "32482",
        "Service_Type_ID": "10",
    }

    assert resolver(_Button("sEmail", "99", "7")) == "test@example.test"
    assert session.calls[-1][0].endswith("/GetSupplierEmail")
    assert session.calls[-1][1]["params"] == {
        "SUPPLIER_ID": "99",
        "Service_Type_ID": "7",
    }


def test_email_resolver_reuses_selenium_session():
    class Browser:
        def execute_async_script(self, script, url, params):
            assert url.endswith("/GetARPTEmail")
            assert params == {"ICAO": "TEST"}
            return {"status": 200, "body": json.dumps({"emailHtml": '<a href="mailto:test@example.test">Email</a>'})}

    orchestrator = object.__new__(ScraperOrchestrator)
    orchestrator.config = {"authentication": {"base_url": "https://acukwik.com"}}
    assert orchestrator._build_email_resolver(None, Browser())(_Button("aEmail", "TEST")) == "test@example.test"


def test_failed_authentication_is_not_a_successful_run(monkeypatch):
    import scraper
    orchestrator = object.__new__(ScraperOrchestrator)
    orchestrator.config = {}
    monkeypatch.setattr(scraper, "auto_login_if_needed", lambda _: False)
    with pytest.raises(RuntimeError, match="Failed to obtain valid cookies"):
        orchestrator.run()


def test_headless_driver_uses_complete_installed_browser_user_agent(monkeypatch):
    import browser
    captured = {}

    class Driver:
        def execute_script(self, script):
            return "Mozilla/5.0 TestPlatform HeadlessChrome/154.0.0.0 Safari/537.36"

        def execute_cdp_cmd(self, command, values):
            captured[command] = values

        def set_page_load_timeout(self, seconds):
            pass

        def set_script_timeout(self, seconds):
            pass

        def implicitly_wait(self, seconds):
            pass

    def launch(options):
        captured["arguments"] = options.arguments
        return Driver()

    monkeypatch.setattr(browser.webdriver, "Chrome", launch)
    browser.create_driver({"selenium": {"headless": True, "user_agent": None}})
    assert "--headless=new" in captured["arguments"]
    assert captured["Network.setUserAgentOverride"]["userAgent"] == (
        "Mozilla/5.0 TestPlatform Chrome/154.0.0.0 Safari/537.36"
    )
