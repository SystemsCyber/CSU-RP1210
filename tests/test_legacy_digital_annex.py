"""Older Digital Annex layouts (e.g. the 2012 'SPN & PGN' sheet) are recognized by content and converted."""

import pytest

import j1939db_tools as tools
import synthetic_da


@pytest.fixture(scope="module")
def legacy(tmp_path_factory):
    pytest.importorskip("pretty_j1939")
    folder = tmp_path_factory.mktemp("legacy")
    workbook = synthetic_da.build_legacy(str(folder / "cs1939_synthetic.xlsx"))
    messages = []
    outputs = tools.generate([workbook], str(folder), log=messages.append)
    return workbook, {s: tools.load(p) for s, p in outputs.items()}, messages


def test_spn_sheet_is_found_by_its_columns(legacy):
    workbook = legacy[0]
    sheet, keys = tools.find_spn_sheet(workbook)
    assert sheet == "SPN & PGN" and "POS" in keys and "NAME" in keys


def test_old_layout_converts_with_quirks_fixed(legacy):
    _, dbs, messages = legacy
    spns = dbs[tools.METRIC]["J1939SPNdb"]
    assert (spns["520192"]["Resolution"], spns["520192"]["StartBit"], spns["520192"]["SPNLength"]) == (0.125, 0, 16)
    assert (spns["520193"]["Resolution"], spns["520193"]["Offset"], spns["520193"]["Units"]) == (1, -40, "deg C")
    assert spns["520197"]["Units"] == "km"                       # taken from "0.125 km/bit", not the "m" column
    assert spns["520201"]["Resolution"] == pytest.approx(1e-7)   # "10^-7 deg/bit", not 10 XOR -7
    assert spns["520202"]["Resolution"] == 0                     # "Request Dependent": raw value
    assert dbs[tools.METRIC]["J1939SATabledb"]["249"] == "Example Service Tool"
    assert any("older Digital Annex layout (sheet 'SPN & PGN')" in m for m in messages)
    assert any("power-of-ten resolution 1 rows" in m for m in messages)
    us = dbs[tools.US]["J1939SPNdb"]
    assert (us["520193"]["Units"], us["520197"]["Units"]) == ("deg F", "miles")


def test_decoding_with_the_converted_database(legacy):
    db = legacy[1][tools.METRIC]
    data = bytes.fromhex("401F3C00E8030000")
    assert tools.decode_spn(db, 65280, 520192, data)["value"] == 1000
    assert tools.decode_spn(db, 65280, 520193, data)["value"] == 20
    assert tools.decode_spn(db, 65280, 520197, data)["value"] == 125


def test_current_layout_is_not_converted(synthetic_workbook, tmp_path):
    assert tools.normalize_digital_annex(synthetic_workbook, str(tmp_path)) == synthetic_workbook


def test_unrecognized_workbook_names_its_sheets(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.title = "Notes"
    wb.active.append(["Column A", "Column B"])
    path = str(tmp_path / "other.xlsx")
    wb.save(path)
    with pytest.raises(ValueError, match="no sheet with PGN and SPN columns.*'Notes': COLUMN_A, COLUMN_B"):
        tools.normalize_digital_annex(path, str(tmp_path))
