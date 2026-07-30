# -*- coding: utf-8 -*-
"""Tier-2 feature tests: composition keys, materials dossiers, faceted
search, paper queue, releases."""
import json
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="sd3_test_")
os.environ.setdefault("SD3_DB", os.path.join(_TMP, "test.db"))
os.environ.setdefault("SD3_VOCAB_EXT", os.path.join(_TMP, "ext.json"))

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient          # noqa: E402
import starrydata3.app as sd3app                   # noqa: E402
from starrydata3 import composition                # noqa: E402
from starrydata3.app import (APIKey, Paper, Property, SCHEMA_PATH, Session,  # noqa: E402
                             app, engine, hash_key, init_db, new_api_key,
                             select)
from tests.test_tier1 import _te_record            # noqa: E402


def _make_key(scope="write"):
    init_db()
    raw = new_api_key()
    with Session(engine) as s:
        s.add(APIKey(name=f"t2-{scope}", key_hash=hash_key(raw), scope=scope))
        s.commit()
    return raw


@pytest.fixture(scope="module")
def client():
    from starrydata3.manage import property_vocab_from_kmds
    from starrydata3.units import _clean
    init_db()
    with Session(engine) as s:
        if not s.exec(select(Property)).first():
            for n, c, u in property_vocab_from_kmds(SCHEMA_PATH):
                cu = _clean(u)
                cu = "" if cu.lower() in ("none", "-") else cu
                s.add(Property(name=n, category=c, default_unit=cu))
            s.commit()
    return TestClient(app)


@pytest.fixture(scope="module")
def wkey():
    return _make_key("write")


# ---------------------------------------------------------------------------
# composition parsing
# ---------------------------------------------------------------------------

def test_parse_formula():
    assert composition.parse_formula("Bi2Te3") == {"Bi": 2.0, "Te": 3.0}
    assert composition.parse_formula("(Bi0.5Sb0.5)2Te3") == \
        {"Bi": 1.0, "Sb": 1.0, "Te": 3.0}
    c = composition.parse_formula("Ta0.84Ti0.16FeSb")
    assert c and c["Ta"] == pytest.approx(0.84) and c["Fe"] == 1.0
    assert composition.parse_formula("Process (A)") is None
    assert composition.parse_formula("LCE") is None
    assert composition.parse_formula("In") is None      # word, not indium


def test_comp_key_normalizes_and_aggregates():
    k1, e1 = composition.comp_key("Bi2Te3")
    k2, e2 = composition.comp_key("Bi4Te6")             # same phase, doubled
    assert k1 == k2 and e1 == e2 == "Bi Te"
    k3, _ = composition.comp_key("Fe3Al2Si3 (τ1 phase)")   # fragment rescue
    assert k3 == "Al0.25Fe0.375Si0.375"
    assert composition.comp_key("n-type sample, process (A)") == ("", "")


# ---------------------------------------------------------------------------
# materials dossier + faceted search
# ---------------------------------------------------------------------------

def test_materials_and_facet(client, wkey):
    rec = _te_record("10.9999/t2.bite", "Bi2Te3", {
        ("Seebeck coefficient", "mu V K^-1"): [[t, 200.0] for t in range(300, 700, 25)],
        ("ZT", "-"): [[t, 4e-3 * t] for t in range(300, 700, 25)],
    })
    r = client.post("/api/v1/records", json=rec, headers={"X-API-Key": wkey})
    assert r.status_code == 200

    mats = client.get("/ui/materials").json()
    keys = {m["comp_key"] for m in mats["materials"]}
    assert "Bi0.4Te0.6" in keys
    page = client.get("/material/Bi0.4Te0.6")
    assert page.status_code == 200 and "Bi0.4Te0.6" in page.text

    # ZT >= 1 somewhere in 500-700 K, containing Te
    hits = client.get("/ui/facet", params={
        "prop": "ZT", "vmin": 1, "xmin": 500, "xmax": 700,
        "element": "Te"}).json()
    assert hits["count"] == 1
    assert hits["hits"][0]["comp_key"] == "Bi0.4Te0.6"
    assert hits["hits"][0]["best_value"] == pytest.approx(4e-3 * 675)
    # element NOT present -> no hits
    none = client.get("/ui/facet", params={
        "prop": "ZT", "vmin": 1, "element": "Pb"}).json()
    assert none["count"] == 0


# ---------------------------------------------------------------------------
# paper queue
# ---------------------------------------------------------------------------

def test_queue_lifecycle(client, wkey, monkeypatch):
    monkeypatch.setattr(sd3app, "_crossref_meta", lambda doi: {
        "title": f"Title for {doi}", "journal": "J. Test", "year": 2026,
        "authors": "A. Tester"})
    r = client.post("/api/v1/queue",
                    json={"dois": ["10.9999/t2.new", "10.9999/t2.bite"],
                          "project": "TE"},
                    headers={"X-API-Key": wkey})
    assert r.status_code == 200 and len(r.json()["added"]) == 2

    q = client.get("/ui/queue").json()
    by_doi = {p["doi"]: p for p in q["papers"]}
    assert by_doi["10.9999/t2.new"]["status"] == "queued"
    assert by_doi["10.9999/t2.new"]["title"] == "Title for 10.9999/t2.new"
    assert by_doi["10.9999/t2.bite"]["status"] == "digitized"   # record exists
    assert by_doi["10.9999/t2.bite"]["sid"]

    # ingesting the queued paper flips it to digitized
    rec = _te_record("10.9999/t2.new", "PbTe", {
        ("Seebeck coefficient", "mu V K^-1"): [[t, 150.0] for t in range(300, 500, 25)]})
    client.post("/api/v1/records", json=rec, headers={"X-API-Key": wkey})
    with Session(engine) as s:
        p = s.exec(select(Paper).where(Paper.doi == "10.9999/t2.new")).first()
    assert p.status == "digitized"

    # re-queue is idempotent
    r2 = client.post("/api/v1/queue", json={"dois": ["10.9999/t2.new"]},
                     headers={"X-API-Key": wkey})
    assert r2.json()["skipped_existing"] == ["10.9999/t2.new"]
    assert client.get("/queue").status_code == 200


def test_queue_requires_write_key(client):
    assert client.post("/api/v1/queue", json={"dois": ["10.1/x"]}
                       ).status_code in (401, 403)


# ---------------------------------------------------------------------------
# release bundle
# ---------------------------------------------------------------------------

def test_release_bundle(tmp_path):
    import hashlib
    import shutil
    from starrydata3.manage import cmd_release

    class A:
        tag = "pytest"
    cmd_release(A)
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "releases", "starrydata3-pytest")
    try:
        with open(os.path.join(root, "manifest.json")) as f:
            man = json.load(f)
        assert man["records"] >= 1 and man["curves"] >= 1
        for name, digest in man["sha256"].items():
            with open(os.path.join(root, name), "rb") as f:
                assert hashlib.sha256(f.read()).hexdigest() == digest
        with open(os.path.join(root, "curves.csv")) as f:
            header = f.readline().strip().split(",")
        assert "comp_key" in header and "x" in header and "y" in header
    finally:
        shutil.rmtree(root, ignore_errors=True)
