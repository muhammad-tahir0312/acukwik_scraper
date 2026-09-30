from scraper import ScraperOrchestrator


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
