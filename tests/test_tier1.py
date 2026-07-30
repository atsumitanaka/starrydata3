# -*- coding: utf-8 -*-
"""Tier-1 feature tests: consistency checks, corpus bands, provenance."""
import base64
import json
import os
import tempfile

import pytest

# isolate a temp DB before importing the app (no-op if test_app imported first)
_TMP = tempfile.mkdtemp(prefix="sd3_test_")
os.environ.setdefault("SD3_DB", os.path.join(_TMP, "test.db"))
os.environ.setdefault("SD3_VOCAB_EXT", os.path.join(_TMP, "ext.json"))

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient          # noqa: E402
from starrydata3 import checks                     # noqa: E402
from starrydata3.app import (APIKey, Property, SCHEMA_PATH, Session,  # noqa: E402
                             app, engine, hash_key, init_db, new_api_key,
                             select)


def _make_key(scope="write"):
    init_db()
    raw = new_api_key()
    with Session(engine) as s:
        s.add(APIKey(name=f"t1-{scope}", key_hash=hash_key(raw), scope=scope))
        s.commit()
    return raw


def _seed_vocab():
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


def _te_record(doi, sample, curves):
    """A KMDS record with one thermoelectric sample and the given
    {(prop_term, unit): points} temperature curves."""
    figures = []
    for i, ((term, unit), pts) in enumerate(curves.items(), start=1):
        figures.append({"figure local id": f"f-{i:03d}", "graphs": [{
            "graph local id": f"g-{i:03d}",
            "axes": [{"axis": "x", "quantity": {"term": "Temperature"},
                      "unit": "K"},
                     {"axis": "y", "quantity": {"term": term}, "unit": unit}],
            "samples": ["s-001"],
            "digitization_data": {"series": [
                {"label": sample, "points_data": pts}]},
        }]})
    return {"metadata": {"publication": {
        "DOI": doi, "title": f"TE test {doi}",
        "samples": [{"sample local id": "s-001", "name": sample,
                     "components": [{"name": sample}]}],
        "figures": figures,
    }}, "materials": [{"id": "material_01", "name": sample}]}


# S = 200 μV/K, σ = 1e5 S/m, κ = 1 W/mK  (all flat in T)
#   -> PF = S²σ = (2e-4)²·1e5 = 4e-3 W/mK²;  ZT(T) = 4e-3·T
_T = list(range(300, 800, 25))
_S_PTS = [[t, 200.0] for t in _T]                      # mu V K^-1
_SIGMA_PTS = [[t, 1000.0] for t in _T]                 # Ω^-1 cm^-1 = 1e5 S/m
_KAPPA_PTS = [[t, 1.0] for t in _T]                    # W m^-1 K^-1
_PF_PTS = [[t, 4e-3] for t in _T]                      # W m^-1 K^-2
_ZT_PTS = [[t, 4e-3 * t] for t in _T]                  # -


# ---------------------------------------------------------------------------
# unit parsing
# ---------------------------------------------------------------------------

def test_si_parse_handles_real_units():
    assert checks.si_parse("Ω^-1 cm^-1") == (pytest.approx(100.0), {"S": 1, "m": -1})
    f, d = checks.si_parse("mu V K^-1")
    assert f == pytest.approx(1e-6) and d == {"V": 1, "K": -1}
    f, d = checks.si_parse("µ W m^-1 K^-2")
    assert f == pytest.approx(1e-6) and d == {"W": 1, "m": -1, "K": -2}
    f, d = checks.si_parse("10^4 S m^-1")
    assert f == pytest.approx(1e4) and d == {"S": 1, "m": -1}
    assert checks.si_parse("-") == (1.0, {})            # dimensionless
    assert checks.si_parse("bananas") is None


def test_si_parse_ohm_conductivity_equals_siemens():
    assert checks.si_factor("Ω^-1 cm^-1", {"S": 1, "m": -1}) == pytest.approx(100.0)
    assert checks.si_factor("S cm^-1", {"S": 1, "m": -1}) == pytest.approx(100.0)
    assert checks.si_factor("W m^-1 K^-1", {"S": 1, "m": -1}) is None


# ---------------------------------------------------------------------------
# consistency checks
# ---------------------------------------------------------------------------

def test_consistent_record_passes(client, wkey):
    rec = _te_record("10.9999/t1.ok", "GoodSample", {
        ("Seebeck coefficient", "mu V K^-1"): _S_PTS,
        ("electrical conductivity", "Ω^-1 cm^-1"): _SIGMA_PTS,
        ("thermal conductivity", "W m^-1 K^-1"): _KAPPA_PTS,
        ("power factor", "W m^-1 K^-2"): _PF_PTS,
        ("ZT", "-"): _ZT_PTS,
    })
    r = client.post("/api/v1/records", json=rec, headers={"X-API-Key": wkey})
    assert r.status_code == 200
    flags = r.json()["consistency_flags"]
    by_check = {f["check"]: f for f in flags}
    assert by_check["power factor = S²σ"]["status"] == "ok"
    assert by_check["ZT = S²σT/κ"]["status"] == "ok"


def test_exponent_error_fails_with_unit_hint(client, wkey):
    bad_pf = [[t, 4.0] for t in _T]                    # ×1000 too large
    rec = _te_record("10.9999/t1.exp", "ExpSample", {
        ("Seebeck coefficient", "mu V K^-1"): _S_PTS,
        ("electrical conductivity", "Ω^-1 cm^-1"): _SIGMA_PTS,
        ("power factor", "W m^-1 K^-2"): bad_pf,
    })
    r = client.post("/api/v1/records", json=rec, headers={"X-API-Key": wkey})
    flags = r.json()["consistency_flags"]
    pf = next(f for f in flags if f["check"] == "power factor = S²σ")
    assert pf["status"] == "fail"
    assert "×1000 unit/exponent error" in pf["detail"]


def test_calibration_drift_detected(client, wkey):
    # ratio drifts 1 -> ~150 across the T range (mis-calibrated axis)
    drift_pf = [[t, 4e-3 * 10 ** ((t - 300) / 230)] for t in _T]
    rec = _te_record("10.9999/t1.drift", "DriftSample", {
        ("Seebeck coefficient", "mu V K^-1"): _S_PTS,
        ("electrical conductivity", "Ω^-1 cm^-1"): _SIGMA_PTS,
        ("power factor", "W m^-1 K^-2"): drift_pf,
    })
    r = client.post("/api/v1/records", json=rec, headers={"X-API-Key": wkey})
    pf = next(f for f in r.json()["consistency_flags"]
              if f["check"] == "power factor = S²σ")
    assert pf["status"] == "fail"
    assert "axis calibration" in pf["detail"]


def test_flags_served_and_rendered(client, wkey):
    with Session(engine) as s:
        from starrydata3.app import Record
        rec = s.exec(select(Record)
                     .where(Record.doi == "10.9999/t1.exp")).first()
    r = client.get(f"/ui/record_points/{rec.sid}")
    assert any(f["status"] == "fail" for f in r.json()["consistency"])
    page = client.get(f"/view/{rec.sid}").text
    assert "Consistency checks" in page and "unit/exponent" in page


def test_sigma_from_resistivity_fallback():
    curves = [
        {"sample_name": "x", "prop_x": "temperature", "prop_y": "Seebeck coefficient",
         "unit_x": "K", "unit_y": "mu V K^-1", "points": _S_PTS},
        {"sample_name": "x", "prop_x": "temperature", "prop_y": "resistivity",
         "unit_x": "K", "unit_y": "Ohm m", "points": [[t, 1e-5] for t in _T]},
        {"sample_name": "x", "prop_x": "temperature", "prop_y": "power factor",
         "unit_x": "K", "unit_y": "W m^-1 K^-2", "points": _PF_PTS},
    ]
    flags = checks.check_curves(curves)
    assert flags and flags[0]["status"] == "ok"


# ---------------------------------------------------------------------------
# corpus percentile band
# ---------------------------------------------------------------------------

def test_band_needs_two_other_records(client, wkey):
    r = client.get("/ui/band", params={
        "prop_x": "temperature", "prop_y": "thermal conductivity",
        "unit_x": "K", "unit_y": "W m^-1 K^-1"})
    assert r.json()["n_records"] == 0     # only one record has κ curves


def test_band_percentiles(client, wkey):
    # three records now hold Seebeck curves at 200 μV/K; add two more papers
    # at 100 and 300 so the band brackets them
    for doi, val in (("10.9999/t1.b1", 100.0), ("10.9999/t1.b2", 300.0)):
        rec = _te_record(doi, f"Band{val:.0f}", {
            ("Seebeck coefficient", "mu V K^-1"): [[t, val] for t in _T]})
        assert client.post("/api/v1/records", json=rec,
                           headers={"X-API-Key": wkey}).status_code == 200
    r = client.get("/ui/band", params={
        "prop_x": "temperature", "prop_y": "Seebeck coefficient",
        "unit_x": "K", "unit_y": "mu V K^-1"})
    b = r.json()
    assert b["n_records"] >= 3 and len(b["x"]) >= 2
    assert all(p10 <= p50 <= p90 for p10, p50, p90
               in zip(b["p10"], b["p50"], b["p90"]))
    assert 100 <= b["p50"][0] <= 300      # median inside the data spread
    # exclusion drops one record from the corpus
    with Session(engine) as s:
        from starrydata3.app import Record
        sid = s.exec(select(Record)
                     .where(Record.doi == "10.9999/t1.b1")).first().sid
    r2 = client.get("/ui/band", params={
        "prop_x": "temperature", "prop_y": "Seebeck coefficient",
        "unit_x": "K", "unit_y": "mu V K^-1", "exclude_sid": sid})
    assert r2.json()["n_records"] == b["n_records"] - 1
    # the record page renders the band note for a property with a corpus
    page = client.get(f"/view/{sid}").text
    assert "corpus p10" in page


# ---------------------------------------------------------------------------
# provenance capsule
# ---------------------------------------------------------------------------

_PNG_1PX = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "h6FO1AAAAABJRU5ErkJggg==")).decode()


def test_provenance_roundtrip(client, wkey):
    body = {"doi": "10.9999/t1.ok", "figures": [{
        "label": "Fig 2a", "page": 3, "png_b64": _PNG_1PX,
        "calibration": {"x1_val": 300, "x2_val": 800, "xIsLogScale": False,
                        "y1_val": 0, "y2_val": 250, "yIsLogScale": False},
        "extractor": {"app": "AutoLineDigitizer vtest", "line_model": "general_v2"},
        "curator": "tester"}]}
    r = client.post("/api/v1/provenance", json=body, headers={"X-API-Key": wkey})
    assert r.status_code == 200 and r.json()["figures_stored"] == 1
    sid = r.json()["sid"]

    lst = client.get(f"/ui/provenance/{sid}").json()["entries"]
    assert len(lst) == 1 and lst[0]["label"] == "Fig 2a"
    assert lst[0]["calibration"]["x2_val"] == 800 and lst[0]["has_image"]

    img = client.get(f"/ui/provenance/{sid}/{lst[0]['id']}.png")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"

    page = client.get(f"/view/{sid}").text
    assert "Digitization provenance" in page and "Fig 2a" in page

    # same label re-upload replaces, not duplicates
    r2 = client.post("/api/v1/provenance", json=body, headers={"X-API-Key": wkey})
    assert r2.status_code == 200
    assert len(client.get(f"/ui/provenance/{sid}").json()["entries"]) == 1


def test_provenance_requires_key_and_record(client, wkey):
    assert client.post("/api/v1/provenance",
                       json={"doi": "10.9999/t1.ok", "figures": []}
                       ).status_code in (401, 403)
    r = client.post("/api/v1/provenance",
                    json={"doi": "10.9999/no.such", "figures": []},
                    headers={"X-API-Key": wkey})
    assert r.status_code == 404
