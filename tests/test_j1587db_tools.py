"""J1587 database tools on synthetic document text (made-up names in the layout of the SAE
document; no SAE content), plus the decoder and the dialog."""

import os

import pytest

import j1587db_tools as tools
from j1939db_tools import UnitTable

# Lines as extract_lines() returns them (layout mode: columns separated by 2+ spaces).
SYNTHETIC = """\
TABLE 1 - MESSAGE ID ASSIGNMENT LIST ....................................... 6
TABLE 1 - MESSAGE ID ASSIGNMENT LIST
MID #      Basic Heavy Duty       Mass Transit Specific      Marine Specific
0-127      Defined elsewhere      Defined elsewhere          Defined elsewhere
128        Example Engine         Example Engine             Example Engine
157 (Reclaimed)    Example Brake Unit     -
           Example Brake Unit
176        Example Gearbox /      Example Gearbox /          -
           Hybrid Unit            Hybrid Unit
3.3 Parameter Identification Assignments
TABLE 2 - PARAMETER IDENTIFICATION ASSIGNMENT LIST
PID   Parameter
20    Example Pressure
21(1)(2)  Reserved - To be assigned
140   Example Temperature
200   Example Distance
300 (44)  Example Percent
3.4 Parameter Data Types
TABLE 6 - FAILURE MODE IDENTIFIERS (FMI)
0   Example failure zero
    (that is, an example)
1   Example failure one
3.10 Procedure
TABLE 7 - SUBSYSTEM IDENTIFICATION (SID) ASSIGNMENT LIST
151   Example System Code #1
Common SIDs
203   Example Door Switch
Example Engine SIDs (MID = 128, 175)
1     Example Injector #1
2-3   Example Injector Pair
281 (25)   Example Valve
4. NOTES
APPENDIX A - PARAMETER DEFINITIONS ........................................ 48
APPENDIX A - PARAMETER DEFINITIONS
A.20 EXAMPLE PRESSURE
Pressure of an example fluid.
Parameter Data Length:   1 Character
Data Type:   Unsigned Short Integer
Bit Resolution:   0.689 kPa (0.1 lbf/in2)
Maximum Range:   0.0 to 175.8 kPa (0.0 to 25.5 lbf/in2)
Transmission Update Period:   1.0 s
Message Priority:   4
Format:
PID   Data
20   a
a- Example pressure
A.140 EXAMPLE TEMPERATURE
Temperature of an example fluid.
Parameter Data Length:   2 Characters
Data Type:   Signed Integer
Bit Resolution:   0.25 °F
Maximum Range:   -8192.00 to +8191.75 °F
Transmission Update Period:   1.0 s
Message Priority:   5
Format:
PID   Data
140   a a
A.200 EXAMPLE DISTANCE
Distance travelled.
Parameter Data Length:   4 Characters
Data Type:   Unsigned Long Integer
Bit Resolution:   0.161 km (0.1 mi)
Maximum Range:   0.0 to 691207984.6 km (0.0 to 429496729.5 mi)
Transmission Update Period:   10.0 s
Message Priority:   7
Format:
PID   Data
200   n a a a a
A.300 EXAMPLE PERCENT
Parameter Data Length:   1 Character
Data Type:   Unsigned Short Integer
Bit Resolution:   0.5%
Maximum Range:   0.0 to 127.5%
Message Priority:   6
APPENDIX B - TRANSPORT PROTOCOL
""".splitlines()


@pytest.fixture(scope="module")
def dbs():
    return tools.build(SYNTHETIC, UnitTable())[0]


def test_tables(dbs):
    db = dbs[tools.METRIC]
    assert db["MID"] == {"128": "Example Engine", "157": "Example Brake Unit", "176": "Example Gearbox / Hybrid Unit"}
    assert db["PIDNames"]["21"] == "Reserved - To be assigned" and db["PIDNames"]["300"] == "Example Percent"
    assert db["FMI"] == {"0": "Example failure zero (that is, an example)", "1": "Example failure one"}
    sids = db["SID"]
    assert sids["-1"] == {"151": "Example System Code #1", "203": "Example Door Switch"}
    assert sids["128"]["2"] == sids["128"]["3"] == "Example Injector Pair" and sids["175"]["281"] == "Example Valve"
    assert sids["128"]["203"] == "Example Door Switch"          # common SIDs copied into each MID


def test_exact_us_value_and_both_unit_systems(dbs):
    m, u = dbs[tools.METRIC]["PID"]["20"], dbs[tools.US]["PID"]["20"]
    assert (u["BitResolution"], u["Unit"], u["Maximum"]) == (0.1, "psi", 25.5)     # exact US value, not 0.689 kPa
    assert m["Unit"] == "kPa" and m["BitResolution"] == pytest.approx(0.1 / 0.1450377377)
    assert (m["DataLength"], m["DataType"], m["Priority"], m["DataForm"]) == (1, "Unsigned Short Integer", 4, "a")


def test_fahrenheit_needs_an_offset_in_metric(dbs):
    m, u = dbs[tools.METRIC]["PID"]["140"], dbs[tools.US]["PID"]["140"]
    assert (u["BitResolution"], u["Offset"], u["Unit"]) == (0.25, 0, "deg F")
    assert m["Unit"] == "deg C" and m["BitResolution"] == pytest.approx(0.25 / 1.8)
    assert m["Offset"] == pytest.approx(-32 / 1.8)
    data = (212 * 4).to_bytes(2, "little", signed=True)          # 212 deg F
    assert tools.decode_pid(dbs[tools.US], 140, data)["value"] == 212
    assert tools.decode_pid(dbs[tools.METRIC], 140, data)["value"] == pytest.approx(100)
    assert tools.decode_pid(dbs[tools.US], 140, (-40 * 4).to_bytes(2, "little", signed=True))["value"] == -40
    # Display: the US value has the exact decimals; the converted metric one shows one digit below a step.
    assert tools.format_value(tools.decode_pid(dbs[tools.US], 140, data)) == "212.00"
    assert tools.format_value(tools.decode_pid(dbs[tools.METRIC], 140, data)) == "100.00"


def test_count_byte_and_page_two(dbs):
    data = bytes([4]) + (1000).to_bytes(4, "little")              # n a a a a
    assert tools.decode_pid(dbs[tools.US], 200, data)["value"] == 100
    assert tools.decode_pid(dbs[tools.METRIC], 200, data)["value"] == pytest.approx(160.9344)
    assert tools.decode_pid(dbs[tools.US], 300, b"\xc8")["value"] == 100
    assert tools.format_value(tools.decode_pid(dbs[tools.US], 300, b"\xc8")) == "100.0"


def test_split_pids():
    # MID 128: PID 84 (1 byte), PID 190 (2 bytes), PID 194 (count + 3), page 2 PID 300 (255 escape, 1 byte)
    msg = bytes([128, 84, 3, 190, 0x6F, 0x09, 194, 3, 0x9B, 0xB0, 1, 255, 44, 0xC8])
    mid, pairs = tools.split_pids(msg)
    assert mid == 128
    assert pairs == [(84, b"\x03"), (190, b"\x6f\x09"), (194, b"\x03\x9b\xb0\x01"), (300, b"\xc8")]
    assert tools.split_pids(bytes([128, 190, 1]))[1] == []        # truncated


def test_number_and_quantity_parsing():
    assert tools.parse_number("1/512") == pytest.approx(1 / 512)
    assert tools.parse_number("16.428 x 10-6") == pytest.approx(16.428e-6)
    assert tools.parse_quantity("4.14 kPa/bit (0.6 psi/bit)") == [(4.14, "kPa"), (0.6, "psi")]
    assert tools.parse_range("-8192.00 to +8191.75 °F") == [(-8192.0, 8191.75, "°F")]


def test_stamp_lines_are_removed():
    assert tools.FURNITURE.match("Licensed to Someone")
    assert tools.STAMP.sub("", "Fax:  724-776-0790 Author:Name-SID:1-GUID:2-1.2.3.4").strip() == "Fax:  724-776-0790"
    assert tools.STAMP.sub("", "C.2 GUIDELINES") == "C.2 GUIDELINES"


def test_vectors_use_the_j1939_schema(dbs):
    vector = {"name": "x", "mid": 128, "pid": 140, "data": "5003", "expected": {"metric": 100, "us": 212}, "tolerance": 1e-6}
    assert tools.run_vector(dbs[tools.US], vector, tools.US)[0]
    assert tools.run_vector(dbs[tools.METRIC], vector, tools.METRIC)[0]
    assert not tools.run_vector(dbs[tools.US], dict(vector, data="5103"), tools.US)[0]


def test_skeleton_validates():
    skeleton = tools.load(os.path.join(tools.MODULE_DIR, tools.SKELETON_NAME))
    assert skeleton["_meta"]["skeleton"] is True and not skeleton["PID"]
    assert not [c for c in tools.validate(skeleton) if c.level == tools.FAIL]


def test_dialog_validates_a_database(tmp_path, dbs):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    import json
    from J1587DatabaseSelect import J1587DatabaseDialog
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "J1587db.synthetic.json"
    path.write_text(json.dumps(dict(dbs[tools.US], _meta={"units": "us"})))
    dialog = J1587DatabaseDialog(storage_dir=str(tmp_path))
    dialog.db_combo.setEditText(str(path))
    dialog.run_validation()
    results = {dialog.results.topLevelItem(i).text(1): dialog.results.topLevelItem(i).text(0)
               for i in range(dialog.results.topLevelItemCount())}
    assert results["data length vs PID range"] == tools.PASS and results["test vectors"] == tools.FAIL
    dialog.close()
