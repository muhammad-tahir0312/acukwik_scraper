"""Airport-Info, Clearance-Overview and Nearby parsing on saved live pages.

Fixtures in tests/fixtures are saved acukwik.com pages with the account name
and IDs replaced; no test touches the network.
"""
from pathlib import Path

import pytest

from html_driver import HtmlDriver
from parsers import AirportPageParser, ClearanceParser, NearbyParser

FIXTURES = Path(__file__).parent / "fixtures"


def _airport(icao: str) -> dict:
    url = f"https://acukwik.com/Airport-Info/{icao}"
    driver = HtmlDriver.from_file(FIXTURES / f"org_listings_{icao}.html", current_url=url)
    return AirportPageParser(driver).parse(url)[0]


def _clearance(html: str, icao: str = "EGLL") -> dict:
    url = f"https://acukwik.com/Clearance-Overview/{icao}"
    return ClearanceParser(HtmlDriver(html, url)).parse(url, icao, load_page=False)


def _nearby(fixture: str, icao: str) -> dict:
    url = f"https://acukwik.com/Nearby/{icao}"
    driver = HtmlDriver.from_file(FIXTURES / fixture, current_url=url)
    return NearbyParser(driver).parse(url, icao, load_page=False)


@pytest.fixture(scope="module")
def egll():
    return _airport("EGLL")


@pytest.fixture(scope="module")
def kteb():
    return _airport("KTEB")


# --- Airport Restrictions and Information -------------------------------------

def _contact(airport: dict, section: str) -> dict:
    return next(row for row in airport["data"]["airport_contacts_raw"] if row["section"] == section)


def test_restrictions_keep_email_and_website_next_to_phone(egll):
    row = _contact(egll, "SCR LEVEL 3 - COORDINATED ARPT")
    assert row["phone"] == "+44 20 8564 0613"
    assert row["fax"] == "+44 20 8564 0691"
    assert row["email"] == "lonacxh@acl-uk.org"
    assert row["emails"] == ["lonacxh@acl-uk.org"]
    assert row["websites"] == ["http://www.acl-uk.org"]
    assert row["contact_numbers"] == [
        {"label": "Phone", "value": "+44 20 8564 0613"},
        {"label": "Fax", "value": "+44 20 8564 0691"},
    ]
    assert _contact(egll, "ATC (NATS)")["website"] == "http://www.nats.co.uk"
    assert _contact(egll, "CUSTOMS")["website"] == "http://www.hmrc.gov.uk"
    rows = egll["data"]["airport_contacts_raw"]
    assert not any(row.get("email_website_raw") == "Phone" for row in rows)
    assert len(rows) == 12


def test_restrictions_kteb_customs_email(kteb):
    row = _contact(kteb, "CUSTOMS LRA PPR - MIN 2 HRS PN")
    assert row["email"] == "KTEB_GAP@cbp.dhs.gov"
    assert row["website"] == "http://www.cbp.gov"
    assert row["phones"] == ["+1 201 288 8799"]
    assert row["faxes"] == ["+1 201 288 4699"]
    assert _contact(kteb, "ASOS")["frequency"] == "132.025"


def test_restriction_row_with_several_phones_and_emails():
    html = """<html><body><div class="mb44px">
      <div class="clearboth h1">Airport Restrictions and Information</div>
      <div class="results-content"><div class='clearfix result'>
        <div class="w31p fl bold">OPS</div><div class="w17p fl p3px">121.5</div>
        <div class="w30p fl p3px">
          <div class="clearfix"><div class="fl w30p">Phone</div><div class="fl w70p">+1 1</div></div>
          <div class="clearfix"><div class="fl w30p">Phone</div><div class="fl w70p">+1 2</div></div>
          <div class="clearfix"><div class="fl w30p">Toll Free Phone</div><div class="fl w70p">+1 800</div></div>
        </div>
        <div class="w22p fl p3px">
          <div><a href="mailto:a@x.test">a@x.test</a></div><div><a href="mailto:b@x.test">b@x.test</a></div>
          <div><a href="/ops">ops page</a></div>
        </div>
      </div></div></div></body></html>"""
    url = "https://acukwik.com/Airport-Info/KAAA"
    row = AirportPageParser(HtmlDriver(html, url))._extract_restrictions_table([], [], [])[0]
    assert row["phones"] == ["+1 1", "+1 2"]
    assert row["phone"] == "+1 1"
    assert {"label": "Toll Free Phone", "value": "+1 800"} in row["contact_numbers"]
    assert row["emails"] == ["a@x.test", "b@x.test"]
    assert row["websites"] == ["https://acukwik.com/ops"]


# --- Customs panel, diagrams, field mappings ----------------------------------

def test_customs_info_panel(kteb, egll):
    customs = kteb["data"]["customs_info"]
    assert customs["phone"] == "+1 201 288 8799"
    assert customs["fax"] == "+1 201 288 4699"
    assert customs["remarks"].startswith("0700-2400L 7 DAYS Must secure permission to land")
    assert "Customs Information" not in customs["remarks"]
    assert egll["data"]["customs_info"]["phone"] == "+44 20 8750 1515"
    assert "remarks" not in egll["data"]["customs_info"]


def test_diagram_urls_are_absolute(kteb, egll):
    assert kteb["data"]["runway_diagram_url"] == "https://acukwik.com/extimages/Listing-Images/KTEB.jpg?s=8DF126312227FB3"
    assert kteb["data"]["faa_diagram_url"] == "https://acukwik.com/extimages/Listing-Images/00890AD.jpg?s=8DEF49E0D663000"
    assert kteb["data"]["faa_diagram_img_src"] == "https://acukwik.com/runwaydiagram.ashx?Procedure_ID=12853"
    assert egll["data"]["runway_diagram_url"].startswith("https://acukwik.com/extimages/Listing-Images/EGLL.jpg")
    assert "faa_diagram_url" not in egll["data"]


def test_open_24h_keeps_raw_value_and_unicom_is_mapped(egll):
    assert egll["data"]["open_24h"] is True  # existing boolean is unchanged
    assert egll["data"]["open_24h_raw"] == "Restricted"
    assert egll["data"]["unicom_frequency"] == "124.475"


# --- Clearance ----------------------------------------------------------------

def test_clearance_contacts_from_saved_page():
    clearance = _clearance((FIXTURES / "clearance_EGLL.html").read_text(encoding="utf-8"))
    data = clearance["data"]
    assert clearance["scrape_status"] == "SUCCESS"
    corporate, commercial = data["clearance_contacts"]
    assert corporate["section"] == "Corporate/Private Flights"
    assert corporate["agency"] == "Civil Aviation Authority, Foreign Registered Aircraft Permits Department"
    assert corporate["phone"] == "+44 33 0138 3484"
    assert corporate["email"] == "foreigncarrierpermits@caa.co.uk"
    assert corporate["website"] == "www.caa.co.uk/"
    assert corporate["raw_value"].startswith("Corporate/Private Flights: Civil Aviation Authority")
    assert commercial["phones"] == ["+44 20 7453 6436"]
    assert data["wgs84"].startswith("All coordinates published by the UK authorities")


def test_clearance_contacts_keep_every_value():
    value = (
        "Corporate/Private Flights: Qatar Civil Aviation Authority, Tel +974 4455 7333, 7247, "
        "e-mail info@caa.gov.qa, Web www.caa.gov.qa, SITA DOHXYYF, AFS/AFTN OTBDYAYX. "
        "Commercial Flights: Agency-Name Dept, Tel +7 495 645 8555 (ext. 5911, 5920), "
        "e-mail a@b.test, Web www.mwti.gov.ws/divisions/civil-aviation/, AFS/AFTN NGTAYAYX & copy NGTTYTYX."
    )
    corporate, commercial = ClearanceParser._parse_clearance_contacts(value)
    assert corporate["phones"] == ["+974 4455 7333", "7247"]
    assert corporate["phone"] == "+974 4455 7333"
    assert corporate["sita_addresses"] == ["DOHXYYF"]
    assert corporate["afs_aftn"] == "OTBDYAYX"
    assert commercial["agency"] == "Agency-Name Dept"
    assert commercial["phones"] == ["+7 495 645 8555 (ext. 5911, 5920)"]
    assert commercial["website"] == "www.mwti.gov.ws/divisions/civil-aviation/"
    assert commercial["afs_aftn_addresses"] == ["NGTAYAYX", "NGTTYTYX"]


def test_clearance_heading_fallback_does_not_overwrite_table_values():
    html = """<html><body>
      <h2>Visakhapatnam, AP INDIA</h2><div>Not the visa text</div>
      <div class="clearanceOverviewContent"><table>
        <tr><td class="grayBg">Visa</td><td>Required for all crew.</td></tr>
      </table></div>
      <div><strong>Comments</strong><div>Outside the clearance content</div></div>
    </body></html>"""
    data = _clearance(html, "VOVZ")["data"]
    assert data["visa"] == "Required for all crew."
    assert "comments" not in data


def test_clearance_heading_fallback_inside_content():
    html = """<html><body><div class="clearanceOverviewContent">
      <div><strong>WGS-84</strong><div>Implemented.</div></div>
    </div></body></html>"""
    assert _clearance(html)["data"]["wgs84"] == "Implemented."


# --- Nearby -------------------------------------------------------------------

def test_nearby_markers_cover_every_page_of_egll():
    first = _nearby("nearby_EGLL_p1.html", "EGLL")
    data = first["data"]
    markers = data["map_markers"]
    assert len(markers) == 80
    assert [m["source_airport_id"] for m in markers if m["is_origin"]] == ["EGLL"]
    assert data["expected_count"] == 79

    rows = list(data["nearby_airports"])
    assert len(rows) == 25 and all("latitude" in row for row in rows)
    for page in (2, 3, 4):
        page_entity = _nearby(f"nearby_EGLL_p{page}.html", "EGLL")
        assert "map_markers" not in page_entity["data"]  # the map is on page 1 only
        rows.extend(page_entity["data"]["nearby_airports"])
    assert len(rows) == 79
    assert NearbyParser.apply_map_markers(rows, markers) == 79

    northolt = next(row for row in rows if row.get("icao") == "EGWU")
    assert northolt["latitude"] == 51.553333
    assert northolt["longitude"] == -0.418333
    assert northolt["iata"] == "NHT"
    assert northolt["runway_length_ft"] == 5525
    assert northolt["website"] == "http://www.londonvipairport.com"
    assert northolt["hours_24"] == "No"
    assert northolt["marker_category"] == "orange"
    assert northolt["url"] == "https://acukwik.com/Airport-Info/EGWU"
    assert northolt["links"][0]["url"] == "https://acukwik.com/Airport-Info/EGWU"


def test_nearby_ltba():
    entity = _nearby("nearby_LTBA.html", "LTBA")
    data = entity["data"]
    assert entity["scrape_status"] == "SUCCESS"
    assert data["expected_count"] == 9
    assert len(data["nearby_airports"]) == 9
    new_istanbul = next(row for row in data["nearby_airports"] if row.get("icao") == "LTFM")
    assert new_istanbul["name"] == "Istanbul (New)"
    assert new_istanbul["iata"] == "IST"
    assert new_istanbul["latitude"] == 41.275


def test_nearby_name_keeps_hyphens_and_empty_list_is_success():
    html = """<html><body><div class="results-content">
      <div class="result clearfix">
        <div class="col2 w45p fl p10px"><a href="/Airport-Info/LESU">LESU - Andorra-La Seu d'Urgell</a></div>
        <div class="col3 w15p fl p10px">4134 x 98 ft</div>
        <div class="col3 w15p fl p10px">Civil</div>
        <div class="col4 w25p fl p10px">La Seu d'Urgell</div>
      </div></div></body></html>"""
    url = "https://acukwik.com/Nearby/LEXX"
    entity = NearbyParser(HtmlDriver(html, url)).parse(url, "LEXX", load_page=False)
    row = entity["data"]["nearby_airports"][0]
    assert row["name"] == "Andorra-La Seu d'Urgell"
    assert row["url"] == "https://acukwik.com/Airport-Info/LESU"

    empty = "<html><body><div class='results-content'></div></body></html>"
    entity = NearbyParser(HtmlDriver(empty, url)).parse(url, "LEXX", load_page=False)
    assert entity["scrape_status"] == "SUCCESS"
    assert entity["data"]["nearby_airports"] == []
