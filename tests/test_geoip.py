"""GeoIP service + telemetry ingestion of coarse geo (country + region)."""


def test_geoip_graceful_without_db():
    from app.services import geoip
    geoip.reset_cache()
    assert geoip.lookup(None) is None
    # default path doesn't exist in the test env -> None, no crash, geo disabled
    assert geoip.lookup("8.8.8.8") is None


def test_geoip_parses_record_drops_city(monkeypatch):
    from app.services import geoip

    class _FakeReader:
        def get(self, ip):
            return {
                "country": {"iso_code": "US"},
                "subdivisions": [{"iso_code": "CA", "names": {"en": "California"}}],
                "city": {"names": {"en": "San Francisco"}},  # must be dropped
            }
    monkeypatch.setattr(geoip, "_get_reader", lambda family: _FakeReader())
    assert geoip.lookup("1.2.3.4") == {"country": "US", "region": "CA"}
    geoip.reset_cache()


def test_geoip_routes_ipv6_to_v6_reader(monkeypatch):
    from app.services import geoip

    seen = []

    class _R:
        def get(self, ip):
            return {"country": {"iso_code": "DE"}}
    monkeypatch.setattr(geoip, "_get_reader", lambda family: (seen.append(family) or _R()))
    geoip.lookup("2001:db8::1")
    geoip.lookup("8.8.8.8")
    assert seen == ["v6", "v4"]
    geoip.reset_cache()


def test_geoip_parses_sapics_flat_schema(monkeypatch):
    from app.services import geoip

    class _FakeReader:
        def get(self, ip):
            # sapics/ip-location-db dbip-city flat record (what we actually ship)
            return {
                "country_code": "US",
                "state1": "California",
                "state2": "",
                "city": "Mountain View",  # must be dropped
                "latitude": 37.42,
                "longitude": -122.08,
            }
    monkeypatch.setattr(geoip, "_get_reader", lambda family: _FakeReader())
    assert geoip.lookup("8.8.8.8") == {"country": "US", "region": "California"}
    geoip.reset_cache()


def test_geoip_empty_record_is_none(monkeypatch):
    from app.services import geoip

    class _R:
        def get(self, ip):
            return {}
    monkeypatch.setattr(geoip, "_get_reader", lambda family: _R())
    assert geoip.lookup("1.2.3.4") is None
    geoip.reset_cache()


def test_ping_stores_geo(client, tmp_db_path, monkeypatch):
    import uuid, sqlite3
    dev = str(uuid.uuid4())
    monkeypatch.setattr("app.services.geoip.lookup", lambda ip: {"country": "US", "region": "CA"})
    r = client.post("/v1/events/ping", json={"event_type": "app_start", "device_id": dev})
    assert r.status_code == 204, r.text
    con = sqlite3.connect(tmp_db_path)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT country, region FROM telemetry_events WHERE device_id=?", (dev,)).fetchone()
    assert row["country"] == "US" and row["region"] == "CA"
