"""DBC export from the J1939 databases built from the synthetic Digital Annex.

The DBC is parsed back with a small reader and every exported signal is decoded
with its DBC scaling and compared with j1939db_tools.decode_spn.
"""

import re

import pytest

import j1939_dbc
import j1939db_tools as tools

PAYLOADS = {65280: bytes.fromhex("401F3C82006400FF"), 65281: bytes.fromhex("10000000E8030000"),
            65283: bytes.fromhex("7D00FFFFFFFFFFFF")}


def parse_dbc(text):
    messages, values = {}, {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^BO_ (\d+) (\w+): (\d+) ", line)
        if m:
            current = messages.setdefault(int(m.group(1)), {"name": m.group(2), "dlc": int(m.group(3)), "signals": {}})
            continue
        m = re.match(r'^ SG_ (\w+) : (\d+)\|(\d+)@1\+ \(([^,]+),([^)]+)\) \[([^|]+)\|([^\]]+)\] "([^"]*)"', line)
        if m and current is not None:
            current["signals"][m.group(1)] = dict(start=int(m.group(2)), length=int(m.group(3)), scale=float(m.group(4)),
                                                 offset=float(m.group(5)), min=float(m.group(6)), max=float(m.group(7)),
                                                 unit=m.group(8))
            continue
        m = re.match(r'^BA_ "SPN" SG_ (\d+) (\w+) (\d+);', line)
        if m:
            messages[int(m.group(1))]["signals"][m.group(2)]["spn"] = int(m.group(3))
        m = re.match(r"^VAL_ (\d+) (\w+) (.*) ;$", line)
        if m:
            values[(int(m.group(1)), m.group(2))] = dict((int(k), v) for k, v in re.findall(r'(\d+) "([^"]*)"', m.group(3)))
    return messages, values


@pytest.mark.parametrize("system", [tools.METRIC, tools.US])
def test_dbc_signals_decode_like_the_database(generated, system):
    db = tools.load(generated[system])
    text, stats = j1939_dbc.build(db)
    messages, values = parse_dbc(text)
    assert stats["messages"] == len(messages) == 3                  # 65282 holds only an ASCII field
    for frame_id, msg in messages.items():
        assert frame_id & 0x80000000 and frame_id & 0xFF == 0xFE   # extended, SA 254
        pgn = (frame_id >> 8) & 0x3FFFF
        data = PAYLOADS[pgn]
        for name, sig in msg["signals"].items():
            raw = tools.extract_bits(data, sig["start"], sig["length"])
            expected = tools.decode_spn(db, pgn, sig["spn"], data)
            assert raw * sig["scale"] + sig["offset"] == pytest.approx(expected["value"], abs=1e-9), (pgn, name)
            assert sig["unit"] == ("" if expected["units"] in ("bit", "") else expected["units"])


def test_priority_names_and_value_tables(generated):
    db = tools.load(generated[tools.US])
    messages, values = parse_dbc(j1939_dbc.build(db)[0])
    by_name = {m["name"]: (fid, m) for fid, m in messages.items()}
    fid, expb1 = by_name["EXPB1"]
    assert fid == 0x80000000 | 3 << 26 | 65280 << 8 | 0xFE           # Default Priority 3 from the Digital Annex
    assert by_name["EXPB2"][0] >> 26 & 7 == 6
    temp = expb1["signals"]["ExampleFluidTemperature"]
    assert (temp["scale"], temp["offset"], temp["unit"]) == (1.8, -40, "deg F")
    switch = values[(fid, "ExampleSwitch")]
    assert switch == {0: "off", 1: "on", 2: "error", 3: "not available"}   # as pretty_j1939 stores them


def test_skipped_fields_are_listed(generated):
    db = tools.load(generated[tools.METRIC])
    text, stats = j1939_dbc.build(db)
    assert stats["skipped_spns"] == 1 and "SPN 520199" not in text.split("CM_")[0]
    full, _ = j1939_dbc.build(db, pgns={65283})
    assert [l for l in full.splitlines() if l.startswith("BO_ ")] == ["BO_ 2566849534 EXPB4: 8 Vector__XXX"]


def test_identifier_and_numbers():
    assert j1939_dbc.identifier("Engine Speed") == "EngineSpeed"
    assert j1939_dbc.identifier("2nd gear ratio") == "_2ndGearRatio"
    assert len(j1939_dbc.identifier("x" * 80)) == 32
    assert j1939_dbc.number(0.125) == "0.125" and j1939_dbc.number(40) == "40"
    assert j1939_dbc.cycle_time_ms("Every 5 s") == 5000 and j1939_dbc.cycle_time_ms("On request") is None
