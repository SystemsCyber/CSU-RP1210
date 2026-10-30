"""Regenerate tests/j1939db_vectors.json from the local licensed J1939 databases.

    python tests/build_vectors.py [--metric J1939db.licensed.json] [--us J1939db.us.licensed.json]

The licensed databases are used only to *encode* realistic operating points and
to *decode* captured frames. The output holds payloads and expected results,
never scaling, offsets, bit positions, ranges or Digital Annex text, so it can
be committed. Limits (enforced by tests/test_no_licensed_content.py):

* only the widely published parameters listed in PUBLIC_SPNS (at most 40);
* at most 3 vectors per SPN;
* names are our own wording, not Digital Annex labels.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import j1939db_tools as tools  # noqa: E402

MAX_VECTORS_PER_SPN = 3
TOLERANCE = 0.0001

# Widely published J1939 parameters (engine, vehicle, fuel, electrical, time, VIN):
# (PGN, SPN, our name, highway-cruise value in metric units or text)
PUBLIC_SPNS = [
    (61444, 190, "Engine speed", 1450.0),
    (61444, 513, "Actual engine torque (percent)", 55.0),
    (61444, 512, "Driver demand torque (percent)", 58.0),
    (61443, 91, "Accelerator pedal position", 38.4),
    (61443, 92, "Engine load at current speed", 62.0),
    (65262, 110, "Engine coolant temperature", 88.0),
    (65262, 174, "Fuel temperature", 45.0),
    (65262, 175, "Engine oil temperature", 101.0),
    (65263, 100, "Engine oil pressure", 344.0),
    (65265, 84, "Wheel-based vehicle speed", 104.5),
    (65265, 70, "Parking brake switch (state)", 0),
    (65265, 597, "Brake switch (state)", 0),
    (65266, 183, "Fuel rate", 32.45),
    (65266, 184, "Instantaneous fuel economy", 3.21),
    (65270, 102, "Boost pressure (intake manifold 1)", 172.0),
    (65270, 105, "Intake manifold 1 temperature", 47.0),
    (65271, 167, "Charging system voltage", 14.05),
    (65271, 168, "Battery voltage", 13.85),
    (65253, 247, "Engine total hours", 12345.65),
    (65248, 245, "Total vehicle distance", 812345.125),
    (65276, 96, "Fuel level 1", 63.2),
    (65269, 108, "Barometric pressure", 99.5),
    (65269, 171, "Ambient air temperature", 18.5),
    (61445, 523, "Transmission current gear", 10),
    (61445, 524, "Transmission selected gear", 10),
    (65254, 959, "Time of day: seconds", 45),
    (65254, 960, "Time of day: minutes", 23),
    (65254, 961, "Time of day: hours", 14),
    (65254, 963, "Date: month", 10),
    (65254, 962, "Date: day", 4),
    (65254, 964, "Date: year", 2026),
    (65260, 237, "Vehicle identification number", "1XKYDP9X0LJ123456"),
]

# Synthetic proprietary-range vectors (match tests/synthetic_da.py).
SYNTHETIC = [
    ("Synthetic shaft speed", 65280, 520192, "401F0182006400FF", 1000.0, 1000.0),
    ("Synthetic fluid temperature", 65280, 520193, "401F3C82006400FF", 20.0, 68.0),
    ("Synthetic fluid pressure", 65280, 520194, "401F3C64006400FF", 400.0, 58.0150951),
    ("Synthetic road speed", 65280, 520195, "401F3C64006400FF", 100.0, 62.13711922),
    ("Synthetic distance", 65281, 520197, "00350C00C8000000", 100000.0, 62137.11922),
    ("Synthetic fluid volume", 65281, 520198, "00350C00C8000000", 100.0, 26.41720524),
    ("Synthetic acceleration (unit with a digit)", 65283, 520200, "0001FFFFFFFFFFFF", 13.1, 42.97900262),
]

CAPTURE = os.path.join(HERE, "fixtures", "mcx30_keyon_excerpt.log")
CAPTURE_SOURCE = "captured: 2012 MasterCraft X30, key on (tests/fixtures/mcx30_keyon_excerpt.log)"


def encode_payload(db, pgn, values):
    """Build a realistic payload: listed SPNs set, everything else 0xFF (not available)."""
    pgn_entry = db["J1939PGNdb"][str(pgn)]
    length = tools._num(pgn_entry.get("PGNLength"))
    data = bytearray([0xFF] * (int(length) if length and length <= 64 else 8))
    for spn, value in values.items():
        entry = db["J1939SPNdb"][str(spn)]
        if isinstance(value, str):
            return bytearray(value.encode("ascii") + b"*")
        start = tools.spn_start_in_pgn(db, str(pgn), pgn_entry["SPNs"].index(spn))
        nbits = entry["SPNLength"]
        res = tools._num(entry.get("Resolution")) or 1.0
        raw = int(round((value - (tools._num(entry.get("Offset")) or 0.0)) / res))
        word = int.from_bytes(bytes(data), "little")
        mask = ((1 << nbits) - 1) << start
        word = (word & ~mask) | ((raw << start) & mask)
        data = bytearray(word.to_bytes(len(data), "little"))
    return data


def decoded_expectations(metric, us, pgn, spn, data):
    m, u = tools.decode_spn(metric, pgn, spn, data), tools.decode_spn(us, pgn, spn, data)
    if m is None or u is None:
        return None
    if m["text"] is not None:
        return {"expected_text": m["text"]}
    if m["status"] != "Valid":
        return None
    return {"expected": {"metric": round(m["value"], 9), "us": round(u["value"], 9)}, "tolerance": TOLERANCE}


def captured_frames():
    frames = []
    for line in open(CAPTURE):
        parts = line.split()
        if line.startswith("#") or len(parts) < 3 or "#" not in parts[2]:
            continue
        cid, data = parts[2].split("#")
        cid = int(cid, 16)
        pf, ps = (cid >> 16) & 0xFF, (cid >> 8) & 0xFF
        frames.append((((cid >> 8) & 0x3FF00) | (ps if pf >= 240 else 0), bytes.fromhex(data)))
    return frames


def build(metric, us):
    vectors, per_spn = [], {}

    def add(vector):
        spn = vector["spn"]
        if spn < 516096 and per_spn.get(spn, 0) >= MAX_VECTORS_PER_SPN:
            return
        per_spn[spn] = per_spn.get(spn, 0) + 1
        vectors.append(vector)

    for name, pgn, spn, data, m, u in SYNTHETIC:
        add({"name": name, "pgn": pgn, "spn": spn, "data": data, "expected": {"metric": m, "us": u},
             "tolerance": 0.001, "source": "synthetic (tests/synthetic_da.py)"})

    names = {spn: name for _, spn, name, _ in PUBLIC_SPNS}
    by_pgn = {}
    for pgn, spn, _, value in PUBLIC_SPNS:
        by_pgn.setdefault(pgn, {})[spn] = value
    for pgn, values in by_pgn.items():
        if str(pgn) not in metric["J1939PGNdb"]:
            continue
        data = encode_payload(metric, pgn, values)
        for spn in values:
            exp = decoded_expectations(metric, us, pgn, spn, data)
            if exp:
                add(dict({"name": f"{names[spn]} (highway cruise)", "pgn": pgn, "spn": spn,
                          "data": bytes(data).hex().upper()}, **exp, source="constructed: highway cruise operating point"))

    for pgn, data in captured_frames():
        for p, spn, _, _ in PUBLIC_SPNS:
            if p != pgn:
                continue
            exp = decoded_expectations(metric, us, pgn, spn, data)
            if exp:
                add(dict({"name": f"{names[spn]} (key on, engine off)", "pgn": pgn, "spn": spn,
                          "data": data.hex().upper()}, **exp, source=CAPTURE_SOURCE))

    # J1939-71 indicator ranges: all ones = not available; 0xFE in the top byte = error.
    add({"name": "Engine speed not available", "pgn": 61444, "spn": 190, "data": "FFFFFFFFFFFFFFFF",
         "expected_status": "NotAvailable", "source": "J1939-71 not-available range"})
    add({"name": "Engine coolant temperature error indicator", "pgn": 65262, "spn": 110, "data": "FEFFFFFFFFFFFFFF",
         "expected_status": "Error", "source": "J1939-71 error indicator range"})
    add({"name": "Wheel-based vehicle speed not available", "pgn": 65265, "spn": 84, "data": "FFFFFFFFFFFFFFFF",
         "expected_status": "NotAvailable", "source": "J1939-71 not-available range"})
    return vectors


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--metric", default=os.path.join(ROOT, tools.OUTPUT_NAMES[tools.METRIC]))
    ap.add_argument("--us", default=os.path.join(ROOT, tools.OUTPUT_NAMES[tools.US]))
    ap.add_argument("--out", default=tools.VECTORS_FILE)
    args = ap.parse_args(argv)
    vectors = build(tools.load(args.metric), tools.load(args.us))
    tools.save_vectors(vectors, args.out)
    print(f"Wrote {len(vectors)} vectors to {args.out}")


if __name__ == "__main__":
    main()
