"""Checks of the local, git-ignored licensed J1587 databases (skipped when absent).

Create them with Tools > J1587 Database or: python j1587db_tools.py generate J1587.pdf J1708.pdf
"""

import bisect
import json
import os

import pytest

import j1587db_tools as tools
import j1939db_tools
import vehicle_spy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "neovi_ddec6_excerpt.csv")
PATHS = {s: os.path.join(ROOT, tools.OUTPUT_NAMES[s]) for s in (tools.METRIC, tools.US)}
J1939 = {s: os.path.join(ROOT, j1939db_tools.OUTPUT_NAMES[s]) for s in (tools.METRIC, tools.US)}

pytestmark = pytest.mark.skipif(not all(os.path.exists(p) for p in PATHS.values()),
                                reason="licensed J1587 databases not generated")


@pytest.fixture(scope="module")
def dbs():
    return {s: tools.load(p) for s, p in PATHS.items()}


@pytest.mark.parametrize("system", [tools.METRIC, tools.US])
def test_vectors_and_validation(dbs, system):
    db = dbs[system]
    assert db["_meta"]["units"] == system and not db["_meta"]["skeleton"]
    assert not [c for c in tools.validate(db) if c.level == tools.FAIL]
    failed = [(v["name"], m) for v, ok, m in tools.run_vectors(db, tools.load_vectors()) if not ok]
    assert not failed


def test_tables_are_complete(dbs):
    db = dbs[tools.METRIC]
    assert len(db["PID"]) >= 500 and len(db["FMI"]) == 16 and len(db["MID"]) >= 128
    assert set(db["SID"]) >= {"-1", "128", "130", "136"}


def test_no_license_stamp_in_databases():
    for path in PATHS.values():
        text = open(path, encoding="utf-8").read()
        for marker in ("Licensed to", "Downloaded", "GUID:", "SAE Digital Library"):
            assert marker not in text, f"{marker!r} found in {path}"


# The DDEC6 broadcasts these on both networks: J1587 PID (MID 128) and J1939 (PGN, SPN) from SA 0.
SAME_QUANTITY = {110: (65262, 110), 175: (65262, 175), 168: (65271, 168), 94: (65263, 94), 108: (65269, 108),
                 105: (65270, 105), 173: (65270, 173), 190: (61444, 190)}


@pytest.mark.skipif(not all(os.path.exists(p) for p in J1939.values()), reason="licensed J1939 databases not generated")
@pytest.mark.parametrize("system", [tools.METRIC, tools.US])
def test_j1587_agrees_with_j1939(dbs, system):
    """Independent check of the PDF parsing: the same engine values arrive on J1708 and J1939."""
    db9 = j1939db_tools.load(J1939[system])
    j1939, j1587 = {}, {}
    reassembler = vehicle_spy.J1939Reassembler()
    for rec in vehicle_spy.read(FIXTURE):
        if isinstance(rec, vehicle_spy.CANRecord):
            for _, pgn, sa, _, data in reassembler.feed(rec):
                if sa == 0:
                    j1939.setdefault(pgn, []).append((rec.time, data))
        elif rec.mid == 128:
            for pid, data in tools.split_pids(rec.message)[1]:
                j1587.setdefault(pid, []).append((rec.time, data))
    checked = 0
    for pid, (pgn, spn) in SAME_QUANTITY.items():
        if pid not in j1587 or pgn not in j1939:
            continue
        times = [t for t, _ in j1939[pgn]]
        for t, data in j1587[pid]:
            ours = tools.decode_pid(dbs[system], pid, data)
            nearest = j1939[pgn][min(bisect.bisect_left(times, t), len(times) - 1)][1]
            theirs = j1939db_tools.decode_spn(db9, pgn, spn, nearest)
            assert theirs is not None, f"SPN {spn} is not in PGN {pgn}"
            assert ours["units"] == theirs["units"], (pid, ours["units"], theirs["units"])
            # Both are quantized: allow one step of each resolution (engine speed moves at idle).
            step = abs(dbs[system]["PID"][str(pid)]["BitResolution"]) + abs(db9["J1939SPNdb"][str(spn)]["Resolution"])
            allowed = step + (15 if pid == 190 else 0)
            assert abs(ours["value"] - theirs["value"]) <= allowed + 1e-9, (pid, ours["value"], theirs["value"])
            checked += 1
    assert checked >= 20
