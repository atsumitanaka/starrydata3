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


POINTS_RECORD = {
    "metadata": {"publication": {
        "DOI": "10.9/pts.1", "title": "Point values",
        "samples": [{"sample local id": "s-001", "name": "A + B",
                     "components": [{"name": "A"}, {"name": "B"}]}],
        "tables": [{"table local id": "t-001", "table name": "Table 1",
                    "headers": ["samples", "T_g (°C)", "E' at 30 °C (MPa)",
                                "υ_e (mol m⁻³)"],
                    "rows": [["A + B", 199.0, 1977.1, 3758.2]],
                    "samples": ["s-001"]}],
    }},
    "materials": [{"id": "material_01", "name": "A", "property": {
        "thermophysical property": {"thermal conductivity": {
            "value": 0.38,
            "measurement": [{"analysis environment": {"temperature": 25}}]}}}}],
}


def test_point_values_indexed(client, wkey, rkey):
    r = client.post("/api/v1/records", json=POINTS_RECORD, headers={"X-API-Key": wkey})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["points_indexed"] == 4
    sid = body["sid"]

    d = client.get(f"/ui/record_points/{sid}").json()
    by_prop = {g["prop"]: g for g in d["groups"]}
    # material property tree, canonical term + implied canonical unit
    tc = by_prop["thermal conductivity"]
    assert tc["kmds"] and tc["unit"] == "W m^-1 K^-1"
    assert tc["entries"][0] == {"entity": "A", "value": 0.38, "env_temperature": 25.0}
    # table columns: T_g synonym + °C→K, E' DMA notation → storage modulus
    tg = by_prop["glass transition temperature"]
    assert tg["kmds"] and tg["unit"] == "K"
    assert abs(tg["entries"][0]["value"] - (199.0 + 273.15)) < 1e-9
    sm = by_prop["storage modulus"]
    assert sm["kmds"] and sm["unit"] == "MPa"          # vocab has no canonical unit
    assert sm["entries"][0]["env_temperature"] == 30.0  # parsed from "at 30 °C"
    # unknown property stays raw and is flagged, never silently mapped
    assert by_prop["υ_e"]["kmds"] is False
    assert "υ_e" in body["non_kmds_properties"]


def test_point_values_in_explorer(client, wkey):
    # measurement-temperature values appear as marker series on the pair plot
    pairs = client.get("/ui/pairs").json()["pairs"]
    assert any(p["prop_x"] == "temperature" and p["prop_y"] == "thermal conductivity"
               and p["points"] for p in pairs)
    g = client.get("/ui/graph", params={"prop_x": "temperature",
                                        "prop_y": "thermal conductivity"}).json()
    pts = [c for c in g["curves"] if c.get("type") == "points"]
    assert pts and pts[0]["points"][0] == [25 + 273.15, 0.38]   # °C → K on the axis


def test_csv_exports(client, wkey):
    full = client.get("/ui/export.csv")
    assert full.status_code == 200
    assert "thermal conductivity" in full.text and "curve" in full.text
    one = client.get("/ui/graph.csv", params={"prop_x": "temperature", "prop_y": "ZT"})
    assert one.status_code == 200 and "ZT" in one.text


def test_json_full_export(client, wkey):
    r = client.get("/ui/export.json")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    dump = r.json()
    assert dump["count"] == len(dump["records"]) > 0
    rec = next(x for x in dump["records"] if x["doi"] == "10.1234/test.001")
    assert rec["record"]["metadata"]["publication"]["DOI"] == "10.1234/test.001"


def test_assemble_record_and_review_commit(client, wkey):
    """The digitization review flow: a session's approved figures become a
    stored KMDS record with legend labels as samples."""
    from starrydata3.app import _REVIEWS
    import tempfile
    _REVIEWS["testrid"] = {
        "doi": "10.9/review.1", "created": "now", "dir": tempfile.mkdtemp(),
        "figures": [
            {"index": 0, "caption": "Fig 1", "calibrated": True, "error": "",
             "x_name": "Temperature (K)", "y_name": "ZT",
             "x_log": False, "y_log": False, "width": 100, "height": 80,
             "image_path": "/nonexistent.png",
             "series": [
                 {"label": "Line 1", "points_px": [[1, 2]],
                  "points_data": [[300, 0.1], [400, 0.4], [500, 0.9]]},
                 {"label": "junk", "points_px": [[3, 4]],
                  "points_data": [[1, 1], [2, 2], [3, 3]]}]},
            {"index": 1, "caption": "Fig 2 (bad)", "calibrated": False,
             "error": "no calibratable axes detected", "x_name": "", "y_name": "",
             "x_log": False, "y_log": False, "width": 10, "height": 10,
             "image_path": "/nonexistent.png", "series": []},
        ]}
    # the session is readable by the verification UI
    d = client.get("/api/v1/review/testrid").json()
    assert len(d["figures"]) == 2 and d["figures"][1]["calibrated"] is False
    # commit: keep figure 0, rename its first series, drop the junk one
    r = client.post("/api/v1/review/testrid/commit", headers={"X-API-Key": wkey},
                    json={"doi": "10.9/review.1", "title": "Reviewed paper",
                          "figures": [
                              {"index": 0, "include": True,
                               "x_name": "Temperature", "x_unit": "K",
                               "y_name": "ZT", "y_unit": "-",
                               "series": [{"label": "SnSe poly", "include": True},
                                          {"label": "junk", "include": False}]},
                              {"index": 1, "include": False}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["curves_indexed"] == 1 and body["kmds_curves"] == 1
    assert "testrid" not in _REVIEWS                 # session consumed
    g = client.get("/ui/graph", params={"prop_x": "temperature", "prop_y": "ZT"}).json()
    assert any(c["sample"] == "SnSe poly" for c in g["curves"])
    # the stored record carries the reviewed sample + real axis units
    rec = client.get(f"/api/v1/records/{body['sid']}", headers={"X-API-Key": wkey}).json()
    pub = rec["metadata"]["publication"]
    assert pub["title"] == "Reviewed paper"
    assert [s["name"] for s in pub["samples"]] == ["SnSe poly"]
    assert pub["figures"][0]["graphs"][0]["axes"][0]["unit"] == "K"


def test_caption_symbol_mapping(client, wkey):
    """Header symbols are resolved through the table's own caption definitions:
    'Young's modulus (E) and Poisson's ratio (γ)' makes columns E/γ KMDS."""
    rec = {"metadata": {"publication": {
        "DOI": "10.9/sym.1", "title": "Symbols",
        "samples": [{"sample local id": "s-001", "name": "n-FAST",
                     "components": [{"name": "FeAlSi"}]}],
        "tables": [{"table local id": "t-001", "table name": "Table 1",
                    "caption summary": "Young's modulus (E) and Poisson's ratio (γ) "
                                       "at room temperature.",
                    "headers": ["specimen", "E (GPa)", "γ"],
                    "rows": [["n-FAST", 285.0, 0.220]],
                    "samples": ["s-001"]}],
    }}, "materials": [{"id": "material_01", "name": "FeAlSi"}]}
    r = client.post("/api/v1/records", json=rec, headers={"X-API-Key": wkey})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kmds_points"] == 2 and body["points_indexed"] == 2
    d = client.get(f"/ui/record_points/{body['sid']}").json()
    by_prop = {g["prop"]: g for g in d["groups"]}
    assert by_prop["Young's modulus"]["kmds"] and by_prop["Young's modulus"]["unit"] == "GPa"
    assert by_prop["Poisson's ratio"]["kmds"]


def test_digitized_curves_merge_into_existing_record(client, wkey):
    """Digitizing a PDF whose DOI is already stored must enrich the record
    (append figures/samples), never clobber its metadata."""
    from starrydata3.app import _REVIEWS
    import tempfile
    _REVIEWS["mergerid"] = {
        "doi": "10.9/pts.1", "created": "now", "dir": tempfile.mkdtemp(),
        "figures": [
            {"index": 0, "caption": "Fig 9", "calibrated": True, "error": "",
             "x_name": "Temperature (K)", "y_name": "thermal conductivity",
             "x_log": False, "y_log": False, "width": 100, "height": 80,
             "image_path": "/nonexistent.png",
             "series": [{"label": "A + B", "points_px": [[1, 2]],
                         "points_data": [[300, 0.3], [350, 0.5], [400, 0.8]]}]}]}
    r = client.post("/api/v1/review/mergerid/commit", headers={"X-API-Key": wkey},
                    json={"doi": "10.9/pts.1",
                          "figures": [{"index": 0, "include": True,
                                       "x_name": "Temperature", "x_unit": "K",
                                       "y_name": "thermal conductivity",
                                       "y_unit": "W m^-1 K^-1",
                                       "series": [{"label": "A + B", "include": True}]}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["curves_indexed"] == 1
    rec = client.get(f"/api/v1/records/{body['sid']}", headers={"X-API-Key": wkey}).json()
    pub = rec["metadata"]["publication"]
    assert pub["title"] == "Point values"                 # metadata survived
    assert pub["tables"] and len(pub["samples"]) == 2     # original + digitized sample
    ids = [s["sample local id"] for s in pub["samples"]]
    assert len(set(ids)) == 2                             # collision remapped
    assert pub["figures"][-1]["graphs"][0]["samples"] == [ids[-1]]
    # and the point values survived re-indexing of the merged record
    d = client.get(f"/ui/record_points/{body['sid']}").json()
    assert any(g["prop"] == "glass transition temperature" for g in d["groups"])


def test_stub_upload_merges_instead_of_clobbering(client, wkey):
    """The desktop app's no-metadata digitization stub must ENRICH the stored
    record (figures appended, curves donor-matched to real graphs), never
    replace it — the data-loss mode that hit the FeAlSi record."""
    rich = {"metadata": {"publication": {
        "DOI": "10.9/stub.1", "title": "Rich record",
        "samples": [{"sample local id": "s-001", "name": "M1",
                     "components": [{"name": "M1"}]}],
        "figures": [{"figure local id": "f-001", "graphs": [{
            "graph local id": "g-001",
            "axes": [{"axis": "x", "quantity": {"term": "temperature"}, "unit": "K",
                      "reference ticks": [300, 400, 500]},
                     {"axis": "y", "quantity": {"term": "ZT"}, "unit": "-",
                      "reference ticks": [0, 1]}],
            "samples": ["s-001"]}]}],
    }}, "materials": [{"id": "material_01", "name": "M1"}]}
    r = client.post("/api/v1/records", json=rich, headers={"X-API-Key": wkey})
    assert r.status_code == 200
    sid = r.json()["sid"]

    stub = {"metadata": {"publication": {"DOI": "10.9/stub.1", "figures": [{
        "figure local id": "Figure 1", "figure name": "Figure 1",
        "graphs": [{"graph local id": "Figure 1",
                    "axes": [{"axis": "x", "quantity": {"term": "X"}, "unit": ""},
                             {"axis": "y", "quantity": {"term": "Y"}, "unit": ""}],
                    "digitization_data": {"series": [
                        {"label": "Line 1",
                         "points_data": [[320, 0.2], [400, 0.5], [480, 0.9]]}]}}]}]}}}
    r = client.post("/api/v1/records", json=stub, headers={"X-API-Key": wkey})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sid"] == sid
    # merged, not clobbered: metadata survives, and the placeholder curve was
    # donor-matched onto the rich record's real graph
    rec = client.get(f"/api/v1/records/{sid}", headers={"X-API-Key": wkey}).json()
    assert rec["metadata"]["publication"]["title"] == "Rich record"
    assert body["curves_indexed"] == 1 and body["kmds_curves"] == 1
    g = client.get("/ui/graph", params={"prop_x": "temperature", "prop_y": "ZT"}).json()
    assert any(c["sid"] == sid and c["sample"] == "M1" for c in g["curves"])


def test_record_page_and_kmds_viewer(client, wkey):
    sids = {r["doi"]: r["sid"] for r in
            client.get("/api/v1/records", headers={"X-API-Key": wkey}).json()["records"]}
    sid = sids["10.9/pts.1"]
    page = client.get(f"/view/{sid}").text
    assert "Measured properties" in page and "glass transition temperature" in page
    assert "Table 1" in page                       # KMDS table rendered
    viewer = client.get(f"/kmds/{sid}").text
    assert "validateAndRender" in viewer and f"/ui/record/{sid}" in viewer
