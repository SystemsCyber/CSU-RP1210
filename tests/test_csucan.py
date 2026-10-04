"""Tests of the CSUCAN RP1210 driver through its C API (as RP1210 applications call it).

Uses the virtual loopback device (99) and a candump replay device, so no
hardware is needed. Build first: cargo build --release -p csucan.
"""

import ctypes
import os
import struct
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "csucan.dll" if os.name == "nt" else "libcsucan.so"
DLL_PATH = os.path.join(ROOT, "target", "release", NAME)
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "sample_j1939.log")
REPLAY_DEVICE = 50

pytestmark = pytest.mark.skipif(not os.path.exists(DLL_PATH), reason="build csucan first (cargo build --release -p csucan)")

c_short, c_long, c_int, c_char = ctypes.c_short, ctypes.c_long, ctypes.c_int, ctypes.c_char
_channel = iter(range(2, 9))   # each test gets its own virtual channel (2..8; 1 is used by the error test)


@pytest.fixture(scope="module")
def dll():
    os.environ[f"CSUCAN_DEVICE_{REPLAY_DEVICE}"] = f"candump:{FIXTURE}"
    loader = ctypes.WinDLL if os.name == "nt" else ctypes.CDLL
    d = loader(DLL_PATH)
    d.RP1210_ClientConnect.argtypes = [ctypes.c_void_p, c_short, ctypes.c_char_p, c_long, c_long, c_short]
    for f in ("RP1210_ClientConnect", "RP1210_ClientDisconnect", "RP1210_SendMessage", "RP1210_ReadMessage",
              "RP1210_SendCommand", "RP1210_GetHardwareStatus", "RP1210_GetErrorMsg", "RP1210_GetLastErrorMsg",
              "RP1210_ReadDetailedVersion", "CSUCAN_WriteIni"):
        getattr(d, f).restype = c_short
    return d


class Client:
    def __init__(self, dll, protocol, device=99, echo=True, passall=True):
        self.dll = dll
        self.id = dll.RP1210_ClientConnect(None, c_short(device), protocol.encode(), 8192, 8192, c_short(0))
        assert 0 < self.id < 128, f"connect {protocol} -> {self.id}"
        if echo:
            assert self.command(16, b"\x01") == 0
        if passall:
            assert self.command(3) == 0

    def command(self, cmd, data=b""):
        buf = (c_char * max(1, len(data)))(*[bytes([b]) for b in data]) if data else None
        return self.dll.RP1210_SendCommand(c_short(cmd), c_short(self.id), buf, c_short(len(data)))

    def send(self, data, block=True):
        buf = (c_char * len(data)).from_buffer_copy(data)
        return self.dll.RP1210_SendMessage(c_short(self.id), buf, c_short(len(data)), c_short(0), c_short(int(block)))

    def read(self, block=False, size=8192):
        buf = (c_char * size)()
        n = self.dll.RP1210_ReadMessage(c_short(self.id), buf, c_short(size), c_short(int(block)))
        return n if n <= 0 else buf.raw[:n]

    def read_all(self, wait=0.3):
        out, end = [], time.time() + wait
        while time.time() < end:
            m = self.read()
            if isinstance(m, bytes):
                out.append(m)
            else:
                time.sleep(0.005)
        return out

    def close(self):
        return self.dll.RP1210_ClientDisconnect(c_short(self.id))


def j1939(msg):
    """Parse a J1939 receive buffer (echo on): ts, echo, pgn, priority, sa, da, data."""
    return dict(echo=msg[4], pgn=msg[5] | msg[6] << 8 | msg[7] << 16, priority=msg[8], sa=msg[9], da=msg[10], data=msg[11:])


def can(msg):
    ext = msg[5] & 1
    cid = struct.unpack(">L", msg[6:10])[0] if ext else struct.unpack(">H", msg[6:8])[0]
    return dict(echo=msg[4], ext=ext, id=cid, data=msg[10:] if ext else msg[8:])


def test_ini_lists_virtual_and_configured_devices(dll, tmp_path):
    path = tmp_path / "CSUCAN.ini"
    assert dll.CSUCAN_WriteIni(str(path).encode()) == 0
    text = path.read_text()
    assert "[DeviceInformation99]" in text and f"[DeviceInformation{REPLAY_DEVICE}]" in text
    assert "ProtocolString=J1939" in text and "250/2000" in text


def test_formats_echo_and_sibling_delivery(dll):
    ch = next(_channel)
    can_c = Client(dll, f"CAN:Baud=250,Channel={ch}")
    j_c = Client(dll, f"J1939:Baud=250,Channel={ch}")
    # CAN client sends EEC1 from SA 0x00 as a raw 29-bit frame.
    payload = bytes.fromhex("FF7D7D401FFFFFFF")
    assert can_c.send(b"\x01" + struct.pack(">L", 0x0CF00400) + payload) == 0
    echo = can(can_c.read_all()[0])
    assert (echo["echo"], echo["ext"], echo["id"], echo["data"]) == (1, 1, 0x0CF00400, payload)
    m = j1939(j_c.read_all()[0])
    assert (m["echo"], m["pgn"], m["priority"], m["sa"], m["da"], m["data"]) == (0, 61444, 3, 0, 255, payload)
    # J1939 client sends a request; the CAN client sees the raw frame.
    assert j_c.send(bytes([0x00, 0xEA, 0x00, 6, 0xF9, 0x00, 0xEC, 0xFE, 0x00])) == 0
    assert j1939(j_c.read_all()[0])["echo"] == 1
    f = can(can_c.read_all()[0])
    assert (f["echo"], f["id"], f["data"]) == (0, 0x18EA00F9, b"\xEC\xFE\x00")
    can_c.close(); j_c.close()


def test_filters_block_until_pass(dll):
    ch = next(_channel)
    quiet = Client(dll, f"J1939:Channel={ch}", passall=False)
    talker = Client(dll, f"J1939:Channel={ch}")
    talker.send(bytes([0x04, 0xF0, 0x00, 3, 0x00, 0xFF]) + bytes(8))
    assert quiet.read_all(0.2) == []
    assert quiet.command(3) == 0
    talker.send(bytes([0x04, 0xF0, 0x00, 3, 0x00, 0xFF]) + bytes(8))
    assert len(quiet.read_all(0.2)) == 1
    quiet.close(); talker.close()


def test_bam_transmit_and_reassembly(dll):
    ch = next(_channel)
    sender = Client(dll, f"J1939:Channel={ch}")
    receiver = Client(dll, f"J1939:Channel={ch}")
    raw = Client(dll, f"CAN:Channel={ch}")
    assert sender.command(27, struct.pack("<L", 0)) == 0          # interpacket time: as fast as possible
    dm1 = bytes.fromhex("04FF6400040116000302BE0012055B000F010502")
    assert sender.send(bytes([0xCA, 0xFE, 0x00, 0x86, 0x00, 0xFF]) + dm1) == 0   # 0x80 = BAM, priority 6
    got = [j1939(m) for m in receiver.read_all()]
    assert [(g["pgn"], g["sa"], g["da"], g["data"]) for g in got] == [(65226, 0, 255, dm1)]
    frames = [can(m) for m in raw.read_all()]
    assert [f["id"] for f in frames] == [0x1CECFF00, 0x1CEBFF00, 0x1CEBFF00, 0x1CEBFF00]
    assert frames[0]["data"][:4] == bytes([32, 20, 0, 3])
    echoed = [j1939(m) for m in sender.read_all()]
    assert echoed and echoed[0]["echo"] == 1 and echoed[0]["data"] == dm1
    sender.close(); receiver.close(); raw.close()


def test_rts_cts_with_claimed_address(dll):
    ch = next(_channel)
    ecu = Client(dll, f"J1939:Channel={ch}")
    tool = Client(dll, f"J1939:Channel={ch}")
    raw = Client(dll, f"CAN:Channel={ch}")
    name = struct.pack("<Q", 0x8000_0000_0012_3456)
    assert ecu.command(19, bytes([0x80]) + name + b"\x00") == 0     # Protect_J1939_Address 0x80
    claim = can(raw.read_all()[0])
    assert claim["id"] == 0x18EEFF80 and claim["data"] == name
    vin = b"1XKYDP9X0LJ123456*ABCDEFGHIJKL"
    assert tool.send(bytes([0xEC, 0xFE, 0x00, 6, 0xF9, 0x80]) + vin, block=True) == 0   # RTS/CTS to 0x80
    rx = [j1939(m) for m in ecu.read_all()]
    assert [(r["pgn"], r["sa"], r["da"], r["data"]) for r in rx if r["pgn"] == 65260] == [(65260, 0xF9, 0x80, vin)]
    control = [can(m)["data"][0] for m in raw.read_all() if can(m)["id"] & 0xFFFF0000 == 0x1CEC0000]
    assert control == [16, 17, 19], control                     # RTS, CTS, EoMA
    # The ECU answers a request for Address Claimed with its NAME.
    tool.send(bytes([0x00, 0xEA, 0x00, 6, 0xF9, 0xFF, 0x00, 0xEE, 0x00]))
    assert any(can(m)["id"] == 0x18EEFF80 for m in raw.read_all())
    ecu.close(); tool.close(); raw.close()


def test_rts_without_listener_times_out(dll):
    ch = next(_channel)
    tool = Client(dll, f"J1939:Channel={ch}")
    start = time.time()
    rc = tool.send(bytes([0xEC, 0xFE, 0x00, 6, 0xF9, 0x33]) + bytes(20), block=True)
    assert rc == 159 and 1.0 < time.time() - start < 3.0          # ERR_MESSAGE_NOT_SENT after T3
    text = ctypes.create_string_buffer(80)
    sub = c_int(0)
    dll.RP1210_GetLastErrorMsg(c_short(159), ctypes.byref(sub), text, c_short(tool.id))
    assert b"timeout" in text.value.lower()
    tool.close()


def test_errors_and_blocking_read(dll):
    assert dll.RP1210_ClientConnect(None, c_short(99), b"J1708", 8192, 8192, c_short(0)) == 136
    assert dll.RP1210_ClientConnect(None, c_short(7), b"J1939", 8192, 8192, c_short(0)) == 134
    c1 = Client(dll, "J1939:Baud=250,Channel=1")
    assert dll.RP1210_ClientConnect(None, c_short(99), b"J1939:Baud=500,Channel=1", 8192, 8192, c_short(0)) == 135
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("n", c1.read(block=True)), daemon=True)
    t.start()
    time.sleep(0.2)
    assert c1.close() == 0
    t.join(2)
    assert result.get("n") == -148
    assert dll.RP1210_ClientDisconnect(c_short(c1.id)) == 129
    text = ctypes.create_string_buffer(80)
    dll.RP1210_GetErrorMsg(c_short(142), text)
    assert text.value == b"Hardware not responding"


def test_replay_device_reassembles_like_a_vda(dll):
    c = Client(dll, "J1939", device=REPLAY_DEVICE)
    msgs = [j1939(m) for m in c.read_all(1.0)]
    by_pgn = {m["pgn"]: m for m in msgs}
    assert len(by_pgn[65226]["data"]) == 20                       # DM1 reassembled from BAM
    assert by_pgn[65260]["data"].startswith(b"1XKYDP9X0LJ123456")   # VIN reassembled from RTS/CTS
    assert 60416 not in by_pgn and 60160 not in by_pgn            # TP frames are consumed by the driver
    info = (c_char * 18)()
    assert dll.RP1210_GetHardwareStatus(c_short(c.id), info, c_short(18), c_short(0)) == 0
    assert info.raw[0] & 1 and info.raw[3] == 1                   # device located, one J1939 client
    api, ver, fw = (ctypes.create_string_buffer(17) for _ in range(3))
    assert dll.RP1210_ReadDetailedVersion(c_short(c.id), api, ver, fw) == 0
    assert api.value == b"RP1210C" and ver.value.startswith(b"CSUCAN")
    c.close()
