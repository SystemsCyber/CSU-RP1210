"""Guards against committing SAE J1939 Digital Annex content.

Runs on every test run (no licensed files needed). It inspects the git index,
so it also catches files that are staged but not yet committed.
"""

import json
import os
import shutil
import subprocess

import pytest

import j1939db_tools as tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROPRIETARY_SPN_MIN = 516096        # manufacturer-proprietary SPN range
MAX_PUBLIC_SPNS = 40
MAX_VECTORS_PER_SPN = 3
ALLOWED_VECTOR_KEYS = {"name", "pgn", "spn", "data", "expected", "expected_status", "expected_text",
                       "tolerance", "source"}


def tracked_files():
    if shutil.which("git") is None:
        pytest.skip("git not available")
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [f for f in out.decode("utf-8").split("\0") if f]


def test_no_workbooks_or_licensed_databases_tracked():
    bad = [f for f in tracked_files()
           if f.lower().endswith((".xls", ".xlsx", ".xlsm"))
           or os.path.basename(f).upper().startswith("J1939DA")
           or "licensed" in os.path.basename(f).lower() and f.lower().endswith(".json")]
    bad = [f for f in bad if not f.startswith("tests/test_")]
    assert not bad, f"licensed files are tracked or staged: {bad}"


def test_tracked_databases_are_skeletons():
    for f in tracked_files():
        if not f.lower().endswith(".json"):
            continue
        path = os.path.join(ROOT, f)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and "J1939SPNdb" in data:
            assert data.get("_meta", {}).get("skeleton") is True, f"{f} is not marked as a skeleton"
            assert len(data["J1939SPNdb"]) <= 20, f"{f} holds {len(data['J1939SPNdb'])} SPN definitions"


def test_vectors_stay_within_licensing_limits():
    vectors = tools.load_vectors()
    per_spn = {}
    for v in vectors:
        extra = set(v) - ALLOWED_VECTOR_KEYS
        assert not extra, f"vector {v.get('name')!r} carries non-vector fields {extra}"
        assert len(v["name"]) <= 80
        if v["spn"] < PROPRIETARY_SPN_MIN:
            per_spn[v["spn"]] = per_spn.get(v["spn"], 0) + 1
    assert len(per_spn) <= MAX_PUBLIC_SPNS, f"{len(per_spn)} public SPNs (limit {MAX_PUBLIC_SPNS})"
    over = {s: n for s, n in per_spn.items() if n > MAX_VECTORS_PER_SPN}
    assert not over, f"too many vectors per SPN: {over}"


# ---- SAE J1587 / J1708 (same rules) ----------------------------------------

ALLOWED_J1587_VECTOR_KEYS = {"name", "mid", "pid", "data", "expected", "expected_text", "tolerance", "source"}


def test_no_sae_documents_or_exports_tracked():
    bad = [f for f in tracked_files()
           if f.lower().endswith((".pdf", ".licensed.dbc"))
           or os.path.basename(f).upper().startswith(("J1587", "J1708")) and f.lower().endswith(".pdf")]
    assert not bad, f"SAE documents or licensed exports are tracked or staged: {bad}"


def test_tracked_j1587_databases_are_skeletons():
    for f in tracked_files():
        if not f.lower().endswith(".json") or not os.path.exists(os.path.join(ROOT, f)):
            continue
        try:
            with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
                data = json.load(fh)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and {"PID", "SID", "FMI"} <= set(data):
            assert data.get("_meta", {}).get("skeleton") is True, f"{f} is not marked as a skeleton"
            assert not data["PID"] and not data["FMI"] and len(data["MID"]) == 0, f"{f} holds J1587 definitions"


def test_j1587_vectors_stay_within_licensing_limits():
    import j1587db_tools
    per_pid = {}
    for v in j1587db_tools.load_vectors():
        extra = set(v) - ALLOWED_J1587_VECTOR_KEYS
        assert not extra, f"vector {v.get('name')!r} carries non-vector fields {extra}"
        assert len(v["name"]) <= 80
        per_pid[v["pid"]] = per_pid.get(v["pid"], 0) + 1
    assert len(per_pid) <= MAX_PUBLIC_SPNS, f"{len(per_pid)} PIDs (limit {MAX_PUBLIC_SPNS})"
    over = {p: n for p, n in per_pid.items() if n > MAX_VECTORS_PER_SPN}
    assert not over, f"too many vectors per PID: {over}"
