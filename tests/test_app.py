# -*- coding: utf-8 -*-
"""End-to-end tests for Starrydata3 against a throwaway DB."""
import json
import os
import tempfile

import pytest

# isolate a temp DB before importing the app
_TMP = tempfile.mkdtemp(prefix="sd3_test_")
os.environ["SD3_DB"] = os.path.join(_TMP, "test.db")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient          # noqa: E402
from starrydata3.app import (APIKey, Session, engine, hash_key, init_db,  # noqa: E402
                             new_api_key, app, select)


def _make_key(scope="write"):
    init_db()
    raw = new_api_key()
    with Session(engine) as s:
        s.add(APIKey(name=f"test-{scope}", key_hash=hash_key(raw), scope=scope))
        s.commit()
    return raw


def _seed_vocab():
    """Seed the KMDS property vocabulary (canonical names + units) so property
    normalization and unit conversion are exercised."""
    from starrydata3.app import Property, SCHEMA_PATH
    from starrydata3.manage import property_vocab_from_kmds
    from starrydata3.units import _clean
    init_db()
    with Session(engine) as s:
        if s.exec(select(Property)).first():
            return
        for n, c, u in property_vocab_from_kmds(SCHEMA_PATH):
            cu = _clean(u)
            cu = "" if cu.lower() in ("none", "-") else cu
            s.add(Property(name=n, category=c, default_unit=cu))
        s.commit()


@pytest.fixture(scope="module")
def client():
    _seed_vocab()
    return TestClient(app)


@pytest.fixture(scope="module")
def wkey():
    return _make_key("write")


@pytest.fixture(scope="module")
def rkey():
    return _make_key("read")


SAMPLE_RECORD = {
    "metadata": {"publication": {
        "DOI": "10.1234/test.001", "title": "Test paper",
        "samples": [{"sample local id": "s-001", "name": "TaFeSb",
                     "components": [{"name": "TaFeSb"}]}],
        "figures": [{"figure local id": "f-001", "graphs": [{
            "graph local id": "g-001",
            "axes": [{"axis": "x", "quantity": {"term": "Temperature"}, "unit": "K"},
                     {"axis": "y", "quantity": {"term": "ZT"}, "unit": "-"}],
            "samples": ["s-001"],
            "digitization_data": {"series": [
                {"label": "TaFeSb", "points_data": [[300, 0.2], [400, 0.6], [500, 1.1]]}]},
        }]}],
    }}, "materials": [{"id": "material_01", "name": "TaFeSb"}],
}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["service"] == "starrydata3"


def test_write_requires_key(client):
    r = client.post("/api/v1/records", json=SAMPLE_RECORD)
    assert r.status_code == 401


def test_read_key_cannot_write(client, rkey):
    r = client.post("/api/v1/records", json=SAMPLE_RECORD, headers={"X-API-Key": rkey})
    assert r.status_code == 403


def test_submit_and_query(client, wkey, rkey):
    r = client.post("/api/v1/records", json=SAMPLE_RECORD, headers={"X-API-Key": wkey})
    assert r.status_code == 200, r.text
    body = r.json()
    sid = body["sid"]
    assert body["ok"] and body["curves_indexed"] == 1 and body["doi"] == "10.1234/test.001"

    # fetch full KMDS record back
    r = client.get(f"/api/v1/records/{sid}", headers={"X-API-Key": rkey})
    assert r.status_code == 200
    assert r.json()["metadata"]["publication"]["DOI"] == "10.1234/test.001"

    # property-pair query returns the curve with real points (props are
    # normalized to the canonical KMDS vocabulary term, e.g. Temperature->temperature)
    r = client.get("/api/v1/graphs", params={"prop_x": "temperature", "prop_y": "ZT"},
                   headers={"X-API-Key": rkey})
    assert r.status_code == 200
    g = r.json()
    assert g["count"] == 1
    assert g["curves"][0]["composition"] == "TaFeSb"
    assert g["curves"][0]["points"][0] == [300, 0.2]
    assert body["kmds_curves"] == 1 and body["non_kmds_properties"] == []


def test_upsert_by_doi(client, wkey):
    # submitting the same DOI twice updates, does not duplicate
    client.post("/api/v1/records", json=SAMPLE_RECORD, headers={"X-API-Key": wkey})
    r = client.get("/api/v1/records", headers={"X-API-Key": wkey})
    dois = [rec["doi"] for rec in r.json()["records"]]
    assert dois.count("10.1234/test.001") == 1


def test_unit_normalization(client, wkey, rkey):
    """A Seebeck curve in V/K is converted to the canonical μV/K (×1e6)."""
    rec = {"metadata": {"publication": {
        "DOI": "10.9/see.1", "title": "See",
        "samples": [{"sample local id": "s-001", "name": "X", "components": [{"name": "X"}]}],
        "figures": [{"figure local id": "f-001", "graphs": [{
            "graph local id": "g-001",
            "axes": [{"axis": "x", "quantity": {"term": "temperature"}, "unit": "K"},
                     {"axis": "y", "quantity": {"term": "Seebeck coefficient"}, "unit": "V K^-1"}],
            "samples": ["s-001"],
            "digitization_data": {"series": [
                {"label": "X", "points_data": [[300, 0.0002], [400, 0.00025]]}]}}]}],
    }}, "materials": [{"id": "material_01", "name": "X"}]}
    r = client.post("/api/v1/records", json=rec, headers={"X-API-Key": wkey})
    assert r.status_code == 200, r.text
    assert r.json()["unit_normalized_curves"] == 1
    r = client.get("/api/v1/graphs",
                   params={"prop_x": "temperature", "prop_y": "Seebeck coefficient"},
                   headers={"X-API-Key": rkey})
    c = r.json()["curves"][0]
    assert abs(c["points"][0][1] - 200.0) < 1e-6      # 0.0002 V/K -> 200 μV/K
    assert "V K^-1" in c["unit_y"]


def test_validate_endpoint(client, rkey):
    bad = {"metadata": {"publication": {"DOI": "10.1/x"}}, "materials": "not-an-array"}
    r = client.post("/api/v1/validate", json=bad, headers={"X-API-Key": rkey})
    assert r.status_code == 200
    assert r.json()["valid"] is False
