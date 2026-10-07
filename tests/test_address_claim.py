"""J1939-81 NAME decoding, industry-group source address names, and address claims in the J1939 tab."""

import os
import struct
from collections import defaultdict

import pytest

import j1939_name

DB = {
    "J1939SATabledb": {"0": "Engine #1", "38": "Virtual Terminal (in cab)", "128": "Reserved (dynamic)", "200": "Example IG1 Bridge"},
    "J1939SATabledbByIG": {"1": {"128": "Reserved (dynamic)", "200": "Example IG1 Bridge"},
                           "2": {"200": "Example IG2 Server"}},
    "J1939IndustryGroupdb": {"0": "Global", "1": "On-Highway Equipment", "2": "Agricultural and Forestry Equipment"},
    "J1939VehicleSystemdb": {"1_0": "Non-specific System", "2_1": "Example Tractor"},
    "J1939Functiondb": {"0": "Engine", "3": "Transmission", "1_0_130": "Example IG1 Function", "0_0_129": "Example Global Tool",
                        "2_1_130": "Example Implement Function"},
    "J1939Manufacturerdb": {"683": "Example Manufacturer Inc."},
    "J1939PGNdb": {}, "J1939SPNdb": {}, "J1939BitDecodings": {}, "J1939FMITabledb": {},
}


def name_bytes(identity=0x12345, mfr=683, ecu=2, fi=0, function=0, vs=0, vsi=1, ig=1, aac=True):
    raw = (identity | mfr << 21 | ecu << 32 | fi << 35 | function << 40 | vs << 49 | vsi << 56 | ig << 60
           | int(aac) << 63)
    return raw.to_bytes(8, "little")


def test_decode_matches_the_rust_core_layout():
    n = j1939_name.decode(name_bytes(identity=0x12345, mfr=0x2AB, ecu=2, fi=3, function=0x81, vs=0x10, vsi=1, ig=1))
    assert (n["identity_number"], n["manufacturer_code"], n["ecu_instance"], n["function_instance"]) == (0x12345, 0x2AB, 2, 3)
    assert (n["function"], n["vehicle_system"], n["vehicle_system_instance"], n["industry_group"]) == (0x81, 0x10, 1, 1)
    assert n["arbitrary_address_capable"] and j1939_name.decode(b"\x00" * 7) is None


def test_function_names_depend_on_industry_group_and_vehicle_system():
    assert j1939_name.function_name(DB, 1, 0, 3) == "Transmission"                 # 0-127: global
    assert j1939_name.function_name(DB, 1, 0, 130) == "Example IG1 Function"
    assert j1939_name.function_name(DB, 2, 1, 130) == "Example Implement Function"
    assert j1939_name.function_name(DB, 2, 0, 129) == "Example Global Tool"         # IG 0 fallback
    assert j1939_name.function_name(DB, 1, 0, 200) == "Function 200"


def test_describe_and_claimed_name():
    n = j1939_name.decode(name_bytes(function=0, fi=1, ig=1))
    d = j1939_name.describe(n, DB)
    assert d["Industry Group"] == "1 On-Highway Equipment" and d["Function"] == "0 Engine"
    assert d["Manufacturer"] == "683 Example Manufacturer Inc." and d["Arbitrary Address Capable"] == "Yes"
    assert d["NAME"] == name_bytes(function=0, fi=1, ig=1)[::-1].hex().upper()
    assert j1939_name.claimed_name(n, DB) == "Engine #2"


def test_source_names_by_industry_group_and_claim():
    assert j1939_name.source_name(DB, 0) == "Engine #1"                              # global range
    assert j1939_name.source_name(DB, 200, ig=1) == "Example IG1 Bridge"
    assert j1939_name.source_name(DB, 200, ig=2) == "Example IG2 Server"
    assert j1939_name.source_name(DB, 201, ig=2) == "IG2 address 201"
    assert j1939_name.source_name(DB, 0, ig=2) == "Engine #1"                        # 0-127 do not depend on it
    claim = j1939_name.decode(name_bytes(function=3, ig=1))
    assert j1939_name.source_name(DB, 200, ig=1, claim=claim) == "Transmission #1 (claimed)"
    assert j1939_name.source_name(DB, 254) == "Null address" and j1939_name.source_name(DB, 255) == "Global"
    # Without per-group tables (older databases) the main table is the on-highway one.
    old = {k: v for k, v in DB.items() if k != "J1939SATabledbByIG"}
    assert j1939_name.source_name(old, 200, ig=1) == "Example IG1 Bridge"
    assert j1939_name.source_name(old, 200, ig=2) == "IG2 address 200"


def test_generator_reads_industry_group_address_sheets(generated):
    import j1939db_tools
    by_ig = j1939db_tools.load(generated[j1939db_tools.METRIC])["J1939SATabledbByIG"]
    assert by_ig["1"]["130"] == "Reserved for future assignment"             # '128 | thru 135 are reserved ...'
    assert by_ig["1"]["200"] == "Example Trailer Bridge"
    assert by_ig["2"]["200"] == "Example Implement Server"                     # IG2 sheet: 'Function' column
    assert "136" not in by_ig["1"]


# ---- The J1939 tab ------------------------------------------------------------

class FakeRoot:
    def __init__(self):
        self.j1939db = DB
        self.data_package = defaultdict(dict)
        self.source_addresses = []
        self.client_ids = {"J1939": None}


def buffer(pgn, sa, data, da=0xFF):
    return {"current_time": 0.0,
            "data": struct.pack(">L", 0) + b"\x00" + struct.pack("<L", pgn)[:3] + bytes([6, sa, da]) + bytes(data)}


@pytest.fixture
def tab(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    monkeypatch.chdir(tmp_path)                                  # csu_settings.json goes here
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from J1939Tab import J1939Tab
    t = J1939Tab(FakeRoot(), QtWidgets.QTabWidget())
    yield t
    app.processEvents()


def sources(tab):
    return {int(r["SA"]): r["Source"] for r in tab.j1939_unique_ids.values()}


def test_industry_group_defaults_to_on_highway_and_renames_rows(tab, tmp_path):
    assert tab.industry_group == 1 and tab.industry_group_box.currentText() == "1 On-Highway Equipment"
    tab.fill_j1939_table(buffer(65280, 200, bytes(8)))
    assert sources(tab)[200] == "Example IG1 Bridge"
    tab.industry_group_box.setCurrentIndex(tab.industry_group_box.findData(2))
    assert sources(tab)[200] == "Example IG2 Server"
    assert '"j1939_industry_group": 2' in (tmp_path / "csu_settings.json").read_text()


def test_address_claim_overrides_and_is_recorded(tab):
    tab.fill_j1939_table(buffer(61444, 200, bytes(8)))
    assert sources(tab)[200] == "Example IG1 Bridge"
    tab.fill_j1939_table(buffer(60928, 200, name_bytes(function=0, fi=0, ig=1)))   # Address Claimed
    assert sources(tab)[200] == "Engine #1 (claimed)"
    claim = tab.root.data_package["Address Claims"]["200"]
    assert claim["Claimed As"] == "Engine #1" and claim["Source Address"] == 200
    assert claim["Manufacturer"] == "683 Example Manufacturer Inc."
    tab.industry_group_box.setCurrentIndex(tab.industry_group_box.findData(2))      # the claim still wins
    assert sources(tab)[200] == "Engine #1 (claimed)"


def test_database_search_includes_the_repository_for_a_dist_build(tmp_path, monkeypatch):
    pytest.importorskip("PyQt5.QtWidgets")
    import sys
    import CSU_RP1210
    dist = tmp_path / "repo" / "dist"
    dist.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(dist / "CSU_RP1210.exe"))
    monkeypatch.chdir(dist)
    folders = [os.path.normcase(f) for f in CSU_RP1210.database_directories()]
    assert folders == [os.path.normcase(str(dist)), os.path.normcase(str(tmp_path / "repo"))]
