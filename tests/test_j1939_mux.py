"""Multiplexed PGNs: selector rules (j1939_mux) and the J1939 tab's "Expand Multiplexed PGNs" view."""

import os
import struct
from collections import defaultdict

import pytest

import j1939_mux

MUX = j1939_mux.Multiplexers()
DB = {"J1939PGNdb": {"65260": {"Label": "VI", "Name": "Vehicle Identification", "SPNs": [], "SPNStartBits": []}},
      "J1939SPNdb": {}, "J1939SATabledb": {}, "J1939BitDecodings": {}}


def test_transport_and_acknowledgment_control_bytes():
    assert MUX.selector(0xEC00, bytes([0x10, 20, 0, 3, 3, 0xEC, 0xFE, 0])) == ("16", "0x10 RTS")
    assert MUX.selector(0xEC00, bytes([0x20, 20, 0, 3, 0xFF, 0xCA, 0xFE, 0])) == ("32", "0x20 BAM")
    assert MUX.selector(0xC800, bytes([0x16] + [0] * 7)) == ("22", "0x16 DPO")
    assert MUX.selector(0xE800, bytes([1] + [0xFF] * 7)) == ("1", "0x01 NACK")


def test_request_is_split_by_requested_pgn():
    assert MUX.selector(0xEA00, bytes([0xEC, 0xFE, 0x00]), DB) == ("65260", "PGN 65260 VI")
    assert MUX.selector(0xEA00, bytes([0x00, 0xEE, 0x00]), DB) == ("60928", "PGN 60928")
    assert MUX.selector(0xEA00, b"\x00") is None


def test_iso15765_frame_type_and_uds_service():
    assert MUX.selector(0xDA00, bytes([0x03, 0x22, 0xF1, 0x90])) == ("SF34", "SF 0x22 ReadDataByIdentifier")
    assert MUX.selector(0xDA00, bytes([0x10, 0x14, 0x62, 0xF1, 0x90, 0x31, 0x32, 0x33])) == \
        ("FF98", "FF 0x62 ReadDataByIdentifier response")
    assert MUX.selector(0xDA00, bytes([0x03, 0x7F, 0x22, 0x31])) == ("SF127", "SF 0x7F NegativeResponse")
    assert MUX.selector(0xDA00, bytes([0x21, 1, 2, 3])) == ("CF", "CF")
    assert MUX.selector(0xDA00, bytes([0x30, 0, 0])) == ("FC", "FC")


def test_virtual_terminal_process_data_and_proprietary():
    assert MUX.selector(0xE600, bytes([0xFE] + [0xFF] * 7)) == ("254", "0xFE VT Status")
    assert MUX.selector(0xE700, bytes([0xFF, 0, 0x03] + [0xFF] * 5)) == ("255", "0xFF Working Set Maintenance")
    assert MUX.selector(0xE700, bytes([0xA8, 1, 0, 0xFF, 5, 0, 0, 0])) == ("168", "0xA8 Change Numeric Value")
    assert MUX.selector(0xCB00, bytes([0x23, 0x10, 0, 0, 0, 0, 0, 0])) == ("3", "0x03 Value")   # command = low nibble
    assert MUX.selector(0xEF00, bytes([0x42, 1, 2])) == ("66", "0x42")
    assert MUX.selector(0xFF12, bytes([0x07, 1, 2])) == ("7", "0x07")                 # Proprietary B range


def test_normal_broadcast_pgns_are_not_multiplexed():
    for pgn in (61444, 65262, 65265, 0xEB00, 0xFEEC):                                # incl. TP.DT and VIN
        assert MUX.definition(pgn) is None and MUX.selector(pgn, bytes(8)) is None


# ---- The J1939 tab ------------------------------------------------------------

class FakeRoot:
    """The parts of the main window the J1939 tab uses."""
    def __init__(self):
        self.j1939db = DB
        self.data_package = defaultdict(dict)
        self.source_addresses = []
        self.client_ids = {"J1939": None}


def buffer(pgn, sa, data, da=0xFF, priority=6):
    return {"current_time": 0.0,
            "data": struct.pack(">L", 0) + b"\x00" + struct.pack("<L", pgn)[:3] + bytes([priority, sa, da]) + bytes(data)}


TRAFFIC = [
    (61444, 0, bytes([0xF0, 0x7D, 0x84, 0xD8, 0x12, 0x00, 0xF0, 0x84])),    # EEC1 broadcast
    (61444, 0, bytes([0xF1, 0x7D, 0x85, 0xD8, 0x12, 0x00, 0xF0, 0x84])),    # same PGN, other first byte
    (0xEC00, 0x26, bytes([0x10, 20, 0, 3, 3, 0x00, 0xE7, 0])),               # TP.CM RTS (ECU -> VT)
    (0xEC00, 0x26, bytes([0x13, 20, 0, 3, 0xFF, 0x00, 0xE7, 0])),            # TP.CM EoMA
    (0xE600, 0x26, bytes([0xFE] + [0xFF] * 7)),                             # VT Status
    (0xE600, 0x26, bytes([0x00, 1, 2, 3, 4, 5, 6, 7])),                     # Soft Key Activation
    (0xDA00, 0xF9, bytes([0x03, 0x22, 0xF1, 0x90])),                        # UDS request
]


@pytest.fixture
def tab():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from J1939Tab import J1939Tab
    t = J1939Tab(FakeRoot(), QtWidgets.QTabWidget())
    yield t
    app.processEvents()


def rows(tab):
    view = tab.pgn_table_proxy
    cols = [view.headerData(c, 1, 0) for c in range(view.columnCount())]
    return cols, [{cols[c]: view.data(view.index(r, c)) for c in range(len(cols))} for r in range(view.rowCount())]


def test_columns_start_with_source_address_and_sort_by_it(tab):
    for pgn, sa, data in TRAFFIC:
        tab.fill_j1939_table(buffer(pgn, sa, data))
    cols, table = rows(tab)
    assert cols[:3] == ["SA", "Source", "PGN"] and "Multiplexer" in cols
    assert [int(r["SA"]) for r in table] == sorted(int(r["SA"]) for r in table)
    # Collapsed (default): one row per PGN and source address, 0xDA00 included.
    assert sorted((int(r["SA"]), int(r["PGN"])) for r in table) == [(0, 61444), (0x26, 0xE600), (0x26, 0xEC00), (0xF9, 0xDA00)]
    assert all(r["Multiplexer"] == "" for r in table)


def test_expanding_splits_only_multiplexed_pgns(tab):
    tab.fill_j1939_table(buffer(61444, 0, TRAFFIC[0][2]))
    tab.expand_mux_button.setChecked(True)                   # clears the PGN table
    assert rows(tab)[1] == []
    for pgn, sa, data in TRAFFIC:
        tab.fill_j1939_table(buffer(pgn, sa, data))
    _, table = rows(tab)
    found = sorted((int(r["PGN"]), r["Multiplexer"]) for r in table)
    assert found == [(0xDA00, "SF 0x22 ReadDataByIdentifier"), (0xE600, "0x00 Soft Key Activation"),
                     (0xE600, "0xFE VT Status"), (0xEC00, "0x10 RTS"), (0xEC00, "0x13 EoMA"), (61444, "")]
    eec1 = next(r for r in table if r["PGN"].strip() == "61444")
    assert eec1["Message Count"].strip() == "2"               # broadcast PGN stays one row


def test_row_cap_for_signals_in_the_first_byte(tab):
    tab.expand_mux_button.setChecked(True)
    for value in range(100):                                  # Proprietary B whose first byte is a signal
        tab.fill_j1939_table(buffer(0xFF20, 0x31, bytes([value, 0, 0, 0, 0, 0, 0, 0])))
    _, table = rows(tab)
    assert len(table) == MUX.max_rows + 1
    other = next(r for r in table if r["Multiplexer"].startswith("other"))
    assert other["Message Count"].strip() == str(100 - MUX.max_rows)
