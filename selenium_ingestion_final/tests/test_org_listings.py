"""Organization listings on saved live Airport-Info pages (EGLL, KTEB).

Fixtures are real pages with the logged-in account name/ID replaced by
TEST USER / 0 and the ASP.NET view state blanked.
"""

from collections import Counter
from pathlib import Path

import pytest

from html_driver import HtmlDriver
from parsers import AirportPageParser, block_text
from roles import normalize_section_title

FIXTURES = Path(__file__).parent / "fixtures"

# Distinct non-empty data-anchor-id values per panel (hotels: distinct names).
EXPECTED_COUNTS = {
    "EGLL": {
        "FBOs": 1, "Handlers": 1, "Fuel Only": 8, "Caterers": 7, "Limo": 2,
        "Maintenance": 6, "Protection": 1, "Detailers": 4, "Hotels": 23, "Car Rental": 7,
    },
    "KTEB": {
        "FBOs": 5, "Flight Support Organizations": 3, "Caterers": 5, "Charter": 9,
        "Limo": 9, "Maintenance": 1, "Hotels": 9, "Car Rental": 4,
    },
}


def _parse(icao):
    url = f"https://acukwik.com/Airport-Info/{icao}"
    driver = HtmlDriver.from_file(FIXTURES / f"org_listings_{icao}.html", current_url=url)
    records = AirportPageParser(driver).parse(url)
    return records[0], records[1:]


@pytest.fixture(scope="module", params=["EGLL", "KTEB"])
def parsed(request):
    airport, orgs = _parse(request.param)
    return request.param, airport, orgs


def _by_name(orgs, name):
    matches = [org["data"] for org in orgs if org["data"]["name"] == name]
    assert len(matches) == 1, f"{name}: {len(matches)} listings"
    return matches[0]


def _contacts(data, contact_type):
    return [c["value"] for c in data.get("contacts", []) if c["type"] == contact_type]


def test_per_panel_counts_match_source_anchors(parsed):
    icao, airport, orgs = parsed
    counts = Counter(org["data"]["source_section"] for org in orgs)
    assert dict(counts) == EXPECTED_COUNTS[icao]

    panel_counts = airport["data"]["organization_panel_counts"]
    for section, expected in EXPECTED_COUNTS[icao].items():
        assert panel_counts[section] == {"expected": expected, "parsed": expected}
    assert not [e for e in airport["errors"] if e.get("field") == "organization_panel_counts"]


def test_no_listing_carries_a_source_profile_as_website(parsed):
    _, _, orgs = parsed
    for org in orgs:
        for website in _contacts(org["data"], "website"):
            assert "acukwik.com" not in website, (org["data"]["name"], website)
            assert "/Basic-Info/" not in website


def test_fbos_fuel_info_panel_does_not_duplicate_fbos(parsed):
    _, _, orgs = parsed
    assert normalize_section_title("FBOs Fuel Info") == "FBOs"
    assert not [org for org in orgs if org["data"]["roles"] == ["OTHER"]]
    keys = [(org["data"]["source_listing_key"], org["data"]["roles"][0]) for org in orgs]
    assert len(keys) == len(set(keys))


def test_raw_text_is_block_aware_and_script_free(parsed):
    _, _, orgs = parsed
    for org in orgs:
        raw_text = org["data"]["raw_text"]
        assert "googletag" not in raw_text
        assert "SITA" not in raw_text or "\nSITA\n" in raw_text
        for field in org["data"]["raw_fields"]:
            assert field["value"].strip(" :").lower() != field["label"].lower()
            assert "googletag" not in field["value"]


def test_two_column_rows_keep_each_listing_separate():
    _, orgs = _parse("KTEB")
    first = _by_name(orgs, "121 INFLIGHT CATERING")
    second = _by_name(orgs, "EXECUTIVE SUITE CATERING SERVICE")

    assert _contacts(first, "phone") == ["+1 914 669 8199"]
    assert _contacts(first, "toll_free") == ["+1 877 463 5121"]
    assert _contacts(first, "fax") == []
    assert _contacts(first, "website") == ["http://www.121vipinflight.com"]
    assert first["source_profile_url"].endswith("/Basic-Info/KTEB/121-INFLIGHT-CATERING")

    assert _contacts(second, "phone") == ["+1 973 227 4119"]
    assert _contacts(second, "fax") == ["+1 973 575 1602"]
    assert _contacts(second, "website") == ["http://www.executive-catering.com"]
    assert second["source_profile_url"].endswith("/Basic-Info/KTEB/EXECUTIVE-SUITE-CATERING-SERVICE")
    assert "121" not in second["raw_text"]


def test_empty_filler_column_is_not_a_listing():
    _, orgs = _parse("EGLL")
    protection = [org["data"] for org in orgs if org["data"]["source_section"] == "Protection"]
    assert [data["name"] for data in protection] == ["CHECKPORT UK"]


def test_brand_and_sita_labels_are_captured():
    _, egll = _parse("EGLL")
    assert _by_name(egll, "AIR BP VIA AFS")["brand"] == ["AIRBP"]
    assert _by_name(egll, "SIGNATURE AVIATION")["brand"] == ["INDEPENDENT"]
    assert _by_name(egll, "MENZIES AVIATION")["sita_code"] == "LHROOXH"
    fuel_only = [org["data"] for org in egll if org["data"]["source_section"] == "Fuel Only"]
    # Every Fuel Only listing with a "Brand" label (all but MENZIES FUELLING).
    assert sum(1 for data in fuel_only if data.get("brand")) == 7
    assert "brand" not in _by_name(egll, "MENZIES FUELLING")

    _, kteb = _parse("KTEB")
    mixjet = _by_name(kteb, "MIXJET FLIGHT SUPPORT")
    assert mixjet["sita_code"] == "DXBMX7X"
    assert "website" not in mixjet["attributes"]
    assert _contacts(mixjet, "website") == ["http://www.mixjet.aero"]
    assert _by_name(kteb, "FASTWAY AVIATION GROUP (FAG)")["sita_code"] == "LAXFAXH"
    assert _by_name(kteb, "JET AVIATION")["brand"] == ["PHILLIPS 66"]


def test_fuel_price_table_is_structured():
    _, orgs = _parse("KTEB")
    jet = _by_name(orgs, "JET AVIATION")
    assert jet["fuel_prices"] == [
        {"fuel": "JET A", "service": "Full", "price": "$10.38", "usd_price": "$10.38"},
        {"fuel": "SAF", "service": "Full", "price": "$12.22", "usd_price": "$12.22"},
    ]
    assert jet["fuel_price_unit"] == "Price/Gallon"
    assert jet["fuel_price_message"] == "Last Updated: 28 Sep 2026"
    assert jet["fuel_prices_last_updated"] == "28 Sep 2026"
    labels = {field["label"] for field in jet["raw_fields"]}
    assert not labels & {"Self", "Full", "JET A", "SAF"}

    stale = _by_name(orgs, "SIGNATURE AVIATION SOUTH")
    assert stale["fuel_price_message"] == "Prices Not Updated Last 90 DAYS"
    assert "fuel_prices" not in stale


def test_toll_free_phone_label_and_hidden_ids():
    _, orgs = _parse("KTEB")
    jet = _by_name(orgs, "JET AVIATION")
    assert _contacts(jet, "toll_free") == ["+1 800 538 0832"]
    assert jet["source_identifiers"]["hfAcukwik_ID"] == ["56150"]
    assert jet["source_identifiers"]["hfGROUND_HANDLER_ID"] == ["20094"]
    assert jet["source_identifiers"]["hfHaveADV"] == ["False"]
    assert jet["address"]["full"] == "112 Charles Lindbergh Dr, Teterboro, NJ 07608"

    limo = _by_name(orgs, "DC LIVERY WORLDWIDE CHAUFFEURED TRANSPORTATION")
    assert limo["source_identifiers"]["hfSupplier_ID"] == ["18436"]

    _, egll = _parse("EGLL")
    avis = _by_name(egll, "AVIS")
    assert avis["source_identifiers"]["hfSupplier_ID"] == ["1567"]


def test_listing_keys_ignore_hidden_ids():
    """Adding hidden IDs to source_identifiers must not change listing keys."""
    _, orgs = _parse("EGLL")
    avis = _by_name(orgs, "AVIS")
    assert avis["source_listing_key"] == "8f5832539e512b4186077d06"


def test_other_copy_of_known_listing_is_dropped_and_same_name_hotels_kept():
    html = """<html><body>
      <div class="bluePanel"><div class="bluePanelTitle">FBOs</div>
        <div class="fbo"><div class="vendor"><a data-anchor-id="ACME"></a>
          <div class="vendorName"><a href="/Basic-Info/TEST/ACME"><strong class="fs18px">ACME FBO</strong></a></div>
        </div></div></div>
      <div class="bluePanel"><div class="bluePanelTitle">Odd New Panel</div>
        <div class="vendor"><a data-anchor-id="ACME"></a>
          <div class="vendorName"><a href="/Basic-Info/TEST/ACME"><strong class="fs18px">ACME FBO</strong></a></div>
        </div></div>
      <div id="dnn_ctr422_VDC_ctl00_pnlHotels" class="bluePanel Hotels">
        <div class="bluePanelRow"><div class="fs18px bold">Same Inn</div>
          <div class="w25p"><div class="bold">Address</div><div>1 North Rd</div></div></div>
        <div class="bluePanelRow"><div class="fs18px bold">Same Inn</div>
          <div class="w25p"><div class="bold">Address</div><div>9 South Rd</div></div></div>
      </div>
    </body></html>"""
    url = "https://acukwik.com/Airport-Info/TEST"
    orgs = AirportPageParser(HtmlDriver(html, url))._parse_organization_entities(url, "TEST")
    fbos = [org["data"] for org in orgs if org["data"]["name"] == "ACME FBO"]
    assert len(fbos) == 1 and fbos[0]["roles"] == ["FBO"]
    assert "Odd New Panel" in fbos[0]["source_sections"]
    hotels = [org for org in orgs if org["data"]["name"] == "Same Inn"]
    assert len(hotels) == 2
    assert len({org["external_id"] for org in hotels}) == 2


def test_block_text_splits_blocks_and_drops_scripts():
    driver = HtmlDriver(
        "<html><body><div id='x'><div><strong>SITA</strong></div><div>ABCDEFG</div>"
        "<script>googletag.cmd.push(1)</script><span>a</span> <span>b</span><br>c</div></body></html>"
    )
    assert block_text(driver.find_element("id", "x")) == "SITA\nABCDEFG\na b\nc"
