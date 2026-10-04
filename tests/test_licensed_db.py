"""Checks your own licensed J1939 databases (any Digital Annex release).

Skipped unless the licensed files are present locally. Point CSU_J1939DB at a
file, or place J1939db.licensed.json / J1939db.us.licensed.json (and, for the
SLOT and leak checks, the J1939DA*.xlsx workbook) in the repository root.
"""

import glob
import json
import os
import shutil
import subprocess

import pytest

import j1939db_tools as tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANDIDATES = [os.environ.get("CSU_J1939DB")] + [os.path.join(ROOT, n) for n in tools.OUTPUT_NAMES.values()]
FOUND = sorted({p for p in CANDIDATES if p and os.path.exists(p)})
METRIC_PATH = os.path.join(ROOT, tools.OUTPUT_NAMES[tools.METRIC])
US_PATH = os.path.join(ROOT, tools.OUTPUT_NAMES[tools.US])
BOTH = os.path.exists(METRIC_PATH) and os.path.exists(US_PATH)
DA = sorted(glob.glob(os.path.join(ROOT, "J1939DA*.xls*")))

needs_both = pytest.mark.skipif(not BOTH, reason="metric and US licensed databases not both present")
needs_da = pytest.mark.skipif(not (BOTH and DA), reason="licensed databases and Digital Annex workbook not present")


@pytest.fixture(scope="module")
def table():
    return tools.UnitTable()


@pytest.fixture(scope="module")
def metric():
    return tools.load(METRIC_PATH)


@pytest.fixture(scope="module")
def us():
    return tools.load(US_PATH)


@pytest.mark.skipif(not FOUND, reason="no licensed J1939 database present")
@pytest.mark.parametrize("path", FOUND, ids=os.path.basename)
def test_licensed_database(path, table):
    db = tools.load(path)
    failures = [c for c in tools.validate(db, table) if c.level == tools.FAIL]
    assert not failures, failures
    results = tools.run_vectors(db, tools.load_vectors(), table)
    assert not [r for r in results if r[1] == "FAIL"], [(v["name"], m) for v, s, m in results if s == "FAIL"]
    assert sum(r[1] == "PASS" for r in results) >= 40, "realistic vectors should apply to a full database"


@needs_both
def test_licensed_unit_systems_agree(metric, us, table):
    conversion = [c for c in tools.compare(us, metric, table) if c.name == "Unit conversion"]
    assert conversion and conversion[0].level == tools.PASS, conversion


@needs_da
def test_slot_crosscheck(metric, us, table):
    checks = tools.slot_crosscheck(DA[-1:], metric, us, table)
    named = {c.name: c for c in checks}
    assert not [c for c in checks if c.level == tools.FAIL], [c for c in checks if c.level == tools.FAIL]
    assert named["Metric scaling vs SLOT"].level == tools.PASS
    assert named["US scaling vs SLOT conversion"].level == tools.PASS


@needs_da
def test_no_digital_annex_text_in_tracked_files():
    """No SP/PG description or long label from the Digital Annex appears in any tracked file."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    corpus = []
    for f in files.decode("utf-8").split("\0"):
        path = os.path.join(ROOT, f)
        if f and os.path.isfile(path) and os.path.getsize(path) < 5_000_000:
            with open(path, "rb") as fh:
                corpus.append(fh.read().decode("utf-8", "ignore").lower())
    corpus = "\n".join(corpus)
    phrases = set()
    for row in tools._table(DA[-1], "SPs & PGs", "SLOT_IDENTIFIER"):
        for key in ("SP_DESCRIPTION", "PG_DESCRIPTION"):
            text = " ".join(str(row.get(key) or "").split())
            if len(text) >= 60:
                phrases.add(text[:60].lower())
    assert phrases, "no descriptions read from the Digital Annex"
    leaked = [p for p in phrases if p in corpus]
    assert not leaked, f"{len(leaked)} Digital Annex phrases found in tracked files, e.g. {leaked[:3]}"


def _csu_binary():
    for build in ("debug", "release"):
        exe = os.path.join(ROOT, "target", build, "csu.exe" if os.name == "nt" else "csu")
        if os.path.exists(exe):
            return exe
    return None


@needs_both
@pytest.mark.parametrize("system", [tools.METRIC, tools.US])
def test_rust_core_passes_vectors(system, tmp_path):
    """The Rust decoder reproduces every realistic vector with the licensed database."""
    exe = _csu_binary()
    if exe is None:
        pytest.skip("build the Rust core first (cargo build)")
    vectors = [v for v in tools.load_vectors() if v["spn"] < 516096]
    log = tmp_path / "vectors.log"
    lines = []
    for i, v in enumerate(vectors):
        pgn = v["pgn"]
        pf = (pgn >> 8) & 0xFF
        can_id = (6 << 26) | (pgn << 8 if pf >= 240 else (pgn & 0x3FF00) << 8 | (0xFF << 8))
        sep = "#" if len(v["data"]) <= 16 else "##0"
        lines.append(f"({i}.0) can0 {can_id:08X}{sep}{v['data']}")
    log.write_text("\n".join(lines) + "\n")
    path = METRIC_PATH if system == tools.METRIC else US_PATH
    out = subprocess.run([exe, "--db", path, "decode", str(log)], capture_output=True, text=True, check=True).stdout
    messages = [json.loads(line) for line in out.splitlines() if '"pgn"' in line]
    assert len(messages) == len(vectors)
    for v, msg in zip(vectors, messages):
        spn = next(s for s in msg["spns"] if s["spn"] == v["spn"])
        if "expected_status" in v:
            assert spn["status"] == v["expected_status"], (v["name"], spn)
        elif "expected_text" in v:
            assert spn["text"] == v["expected_text"], (v["name"], spn)
        else:
            assert spn["value"] == pytest.approx(v["expected"][system], abs=v["tolerance"]), (v["name"], system, spn)
