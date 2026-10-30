"""
Build tests/j1587db_vectors.json and the neoVI fixture excerpt from a Vehicle Spy
recording of a running DDEC6 engine (J1708/J1587 and J1939), using the local,
git-ignored licensed J1587 databases.

    python tests/build_j1587_vectors.py "D:/.../HathawayTerminalTest0 ... .csv"

LICENSING: like tests/j1939db_vectors.json, the vectors hold recorded payloads and
expected values only (no resolutions, offsets, ranges or SAE text), use our own
names, cover at most 40 widely published PIDs with at most 3 vectors each.
tests/test_no_licensed_content.py enforces this.
"""

import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import j1587db_tools as tools  # noqa: E402
import vehicle_spy  # noqa: E402

EXCERPT = os.path.join(HERE, "fixtures", "neovi_ddec6_excerpt.csv")
EXCERPT_SECONDS = 2.0

# PID -> our label. Widely published parameters only.
LABELS = {
    68: "Torque limit factor", 84: "Road speed", 86: "Cruise set speed", 91: "Accelerator pedal",
    92: "Engine load", 93: "Output torque", 94: "Fuel delivery pressure", 100: "Oil pressure",
    102: "Boost pressure", 105: "Intake manifold temperature", 106: "Air inlet pressure",
    108: "Barometric pressure", 110: "Coolant temperature", 111: "Coolant level", 122: "Retarder percent",
    168: "Battery voltage", 171: "Ambient air temperature", 173: "Exhaust gas temperature",
    174: "Fuel temperature", 175: "Oil temperature", 183: "Fuel rate", 184: "Instantaneous fuel economy",
    185: "Average fuel economy", 187: "PTO set speed", 190: "Engine speed", 245: "Total vehicle distance",
    247: "Total engine hours", 439: "Extended boost pressure",
}


def write_excerpt(log, out, seconds=EXCERPT_SECONDS):
    """Copy the header block and the first `seconds` of messages (notes cleared)."""
    with open(log, newline="", encoding="latin-1") as f:
        rows = list(csv.reader(f))
    start = next(i for i, r in enumerate(rows) if r and r[0] == "1")
    t0 = float(rows[start][1])
    header = [["Notes", "Excerpt for CSU-RP1210 tests"] if r and r[0] == "Notes" else r for r in rows[:start]]
    body = [r for r in rows[start:] if r and float(r[1]) - t0 <= seconds]
    with open(out, "w", newline="", encoding="latin-1") as f:
        csv.writer(f, lineterminator="\n").writerows(header + body)
    return len(body)


def main(log):
    dbs = {s: tools.load(os.path.join(ROOT, tools.OUTPUT_NAMES[s])) for s in (tools.METRIC, tools.US)}
    samples = {}
    for rec in vehicle_spy.read(log):
        if isinstance(rec, vehicle_spy.J1708Record) and rec.checksum_ok:
            mid, pairs = tools.split_pids(rec.message)
            for pid, data in pairs:
                if pid in LABELS:
                    samples.setdefault((mid, pid), []).append(data)
    vectors = []
    for (mid, pid), payloads in sorted(samples.items()):
        if mid != 128:          # the engine; other modules repeat battery voltage
            continue
        distinct = list(dict.fromkeys(payloads))
        decoded = [(p, tools.decode_pid(dbs[tools.METRIC], pid, p)) for p in distinct]
        decoded = [(p, d) for p, d in decoded if d and d["value"] is not None]
        if not decoded:
            continue
        picks = {}
        for kind, (payload, _) in (("first", decoded[0]), ("highest", max(decoded, key=lambda x: x[1]["value"])),
                                   ("last", decoded[-1])):
            picks.setdefault(payload, kind)
        for payload, kind in picks.items():
            expected = {s: tools.decode_pid(dbs[s], pid, payload)["value"] for s in dbs}
            vectors.append({"name": f"DDEC6 {LABELS[pid].lower()} ({kind})", "mid": mid, "pid": pid,
                            "data": payload.hex().upper(), "expected": expected, "tolerance": 1e-6,
                            "source": "neoVI recording of a DDEC6 truck, 2013-07-01"})
    doc = ["Decode test vectors for J1587 databases. Each vector decodes the data bytes of one PID (for PIDs",
           "192-253 including the byte count) and checks 'expected' (value per unit system, absolute 'tolerance').",
           "LICENSING: vectors must not reproduce SAE J1587. They hold recorded payloads and expected results only",
           "(no resolutions, offsets, ranges or SAE text), cover at most 40 widely published PIDs with at most 3",
           "vectors each, and use our own names. tests/test_no_licensed_content.py enforces this.",
           "Regenerate with: python tests/build_j1587_vectors.py LOG.csv (requires the local licensed databases)."]
    tools.save_vectors(vectors, tools.VECTORS_FILE, doc)
    print(f"Wrote {len(vectors)} vectors to {tools.VECTORS_FILE}")
    print(f"Wrote {write_excerpt(log, EXCERPT)} messages to {EXCERPT}")


if __name__ == "__main__":
    main(sys.argv[1])
