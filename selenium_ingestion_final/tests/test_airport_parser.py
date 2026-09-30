from html_driver import HtmlDriver
from parsers import AirportPageParser, ClearanceParser, NearbyParser
from roles import SECTION_ROLES
from validators import validate_record


def test_hotels_without_source_ids_get_distinct_listing_keys():
    html = '''<html><body><div id="dnn_ctr422_VDC_ctl00_pnlHotels" class="Hotels">
      <div class="bluePanelRow"><div class="fs18px bold">First Hotel</div></div>
      <div class="bluePanelRow"><div class="fs18px bold">Second Hotel</div></div>
    </div></body></html>'''
    parser = AirportPageParser(HtmlDriver(html))
    rows = parser._extract_vendors_from_section('Hotels', 'HOTEL', 'https://acukwik.com/Airport-Info/TEST', 'TEST')
    assert len(rows) == 2
    assert len({row['data']['source_listing_key'] for row in rows}) == 2
    assert len({row['external_id'] for row in rows}) == 2


def _vendor(name: str, source_id: str) -> str:
    return f"""
    <div class="vendor" id="listing-{source_id}">
      <div class="vendorName"><a data-anchor-id="{source_id}" href="/Basic-Info/{source_id}"><strong>{name}</strong></a></div>
      <div class="clearfix"><div class="fl w35p bold">Phone</div><div class="fl w65p">+1 555 0100</div></div>
      <div class="clearfix"><div class="fl w35p bold">Website</div><div class="fl w65p"><a href="https://example.test/{source_id}">Website</a></div></div>
      <div class="clearfix"><div class="fl w35p bold">Membership</div><div class="fl w65p">Platinum Partner</div></div>
      <img src="/logos/{source_id}.png" alt="{name} logo">
    </div>
    """


def _airport_html() -> str:
    panels = []
    for index, section in enumerate(SECTION_ROLES):
        if section == "FBOs":
            panels.append(f'<div class="fbo">{_vendor("Shared Aviation", "shared")}</div>')
        else:
            source_id = "shared" if section == "Handlers" else f"listing-{index}"
            panels.append(
                f'<div class="bluePanel"><div class="bluePanelTitle">{section}</div>'
                f'{_vendor(section + " Company", source_id)}</div>'
            )
    return f"""
    <html><body>
      <h1>KAAA - Test International</h1>
      <h2>Located in Test City, TESTLAND</h2>
      <h3>ICAO - KAAA, IATA - TST, FAA - KAAA</h3>
      <div class="clearboth p3xp bold">Airport Type</div><div class="clearboth p3px">Civil</div>
      <div class="clearboth table"><div class="fl bold">Experimental Field</div><div class="fl">Future value</div></div>
      {''.join(panels)}
    </body></html>
    """


def test_all_service_sections_and_unknown_fields_are_preserved():
    parser = AirportPageParser(HtmlDriver(_airport_html(), "https://acukwik.com/Airport-Info/KAAA"))
    records = parser.parse("https://acukwik.com/Airport-Info/KAAA")

    airport = records[0]
    organizations = records[1:]
    assert airport["data"]["icao"] == "KAAA"
    assert {item["label"] for item in airport["data"]["airport_fields_raw"]} >= {
        "Airport Type", "Experimental Field"
    }
    assert len(organizations) == len(SECTION_ROLES)
    assert {record["data"]["roles"][0] for record in organizations} == set(SECTION_ROLES.values())

    sample = next(record for record in organizations if record["data"]["roles"] == ["FBO"])
    assert sample["data"]["attributes"]["membership"] == "Platinum Partner"
    assert sample["data"]["source_profile_url"].endswith("/Basic-Info/shared")
    assert sample["data"]["media"][0]["url"].endswith("/logos/shared.png")
    assert validate_record(sample) == []


def test_same_source_listing_can_have_different_roles_at_one_airport():
    parser = AirportPageParser(HtmlDriver(_airport_html(), "https://acukwik.com/Airport-Info/KAAA"))
    organizations = parser.parse("https://acukwik.com/Airport-Info/KAAA")[1:]
    fbo = next(record for record in organizations if record["data"]["roles"] == ["FBO"])
    handler = next(record for record in organizations if record["data"]["roles"] == ["HANDLER"])

    assert fbo["data"]["source_listing_key"] == handler["data"]["source_listing_key"]
    assert fbo["external_id"] != handler["external_id"]
    assert fbo["data"]["associated_airports"] == ["KAAA"]
    assert handler["data"]["associated_airports"] == ["KAAA"]


def test_airport_without_standard_code_uses_acukwik_source_id():
    source = """
    <html><body>
      <h1>Wanlawayn (Baledogle) AB</h1>
      <h2>Located in Wanlaweyn, SOMALIA</h2>
    </body></html>
    """
    url = "https://acukwik.com/Airport-Info/ACKTNON"
    airport = AirportPageParser(HtmlDriver(source, url)).parse(url)[0]

    assert airport["data"]["source_airport_id"] == "ACKTNON"
    assert airport["external_id"] == "acukwik_source_ACKTNON"
    assert validate_record(airport) == []


def test_clearance_and_nearby_keep_unmapped_source_data(monkeypatch):
    monkeypatch.setattr("parsers.time.sleep", lambda *_args: None)
    clearance_html = """<html><body>
    <div class="clearanceOverviewContent"><table>
      <tr><td>Country</td><td>TESTLAND</td></tr>
      <tr><td>New Clearance Field</td><td><a href="/rules">Future rule</a></td></tr>
    </table></div></body></html>
    """
    clearance = ClearanceParser(
        HtmlDriver(clearance_html, "https://acukwik.com/Clearance-Overview/KAAA")
    ).parse("https://acukwik.com/Clearance-Overview/KAAA", "KAAA", load_page=False)
    assert {field["label"] for field in clearance["data"]["raw_fields"]} == {
        "Country", "New Clearance Field"
    }
    assert clearance["data"]["links"][0]["url"].endswith("/rules")

    nearby_html = """<html><body>
    <table><tr><th>Airport</th><th>Rwy</th><th>Type</th><th>City</th><th>Future</th></tr>
      <tr><td><a href="/Airport-Info/KBBB">KBBB</a></td><td>5000 x 100</td><td>Civil</td><td>Test</td><td>Extra value</td></tr>
      <tr><td><a href="/Airport-Info/ACKTNON">No-code Field</a></td><td>3000 x 80</td><td>Military</td><td>Test</td><td>Other value</td></tr>
    </table></body></html>
    """
    nearby = NearbyParser(
        HtmlDriver(nearby_html, "https://acukwik.com/Nearby/KAAA")
    ).parse("https://acukwik.com/Nearby/KAAA", "KAAA", load_page=False)
    row = nearby["data"]["nearby_airports"][0]
    assert row["raw_cells"][-1] == "Extra value"
    no_code_row = nearby["data"]["nearby_airports"][1]
    assert no_code_row["source_airport_id"] == "ACKTNON"
    assert no_code_row["raw_cells"][-1] == "Other value"
    assert nearby["external_id"] == "acukwik_nearby_KAAA"
