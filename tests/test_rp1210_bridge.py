"""End-to-end tests of the RP1210 32-to-64-bit bridge.

64-bit Python loads rp1210_bridge64.dll, which forwards every call to
rp1210_host32.exe, which loads the 32-bit loopback test DLL fake_rp1210.dll.
Build first with rp1210_bridge\\build.bat. Skipped elsewhere.
"""

import ctypes
import os
import subprocess
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "rp1210_bridge", "bin")
BRIDGE = os.path.join(BIN, "rp1210_bridge64.dll")
FAKE = os.path.join(BIN, "fake_rp1210.dll")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or sys.maxsize <= 2**32 or not (os.path.exists(BRIDGE) and os.path.exists(FAKE)),
    reason="needs 64-bit Python on Windows and rp1210_bridge\\build.bat output")

c_short, c_long, c_int, c_char = ctypes.c_short, ctypes.c_long, ctypes.c_int, ctypes.c_char


def load_bridge(target):
    """A fresh copy of the bridge per test (each copy runs its own host)."""
    import shutil, tempfile
    d = tempfile.mkdtemp(prefix="rp1210bridge-")
    for f in ("rp1210_bridge64.dll", "rp1210_host32.exe"):
        shutil.copy(os.path.join(BIN, f), d)
    dll = ctypes.WinDLL(os.path.join(d, "rp1210_bridge64.dll"))
    dll.RP1210Bridge_SetTarget.argtypes = [ctypes.c_char_p]
    dll.RP1210_ClientConnect.argtypes = [ctypes.c_void_p, c_short, ctypes.c_char_p, c_long, c_long, c_short]
    dll.RP1210_ClientConnect.restype = c_short
    for name in ("RP1210_ClientDisconnect", "RP1210_SendMessage", "RP1210_ReadMessage", "RP1210_SendCommand",
                 "RP1210_ReadDetailedVersion", "RP1210_GetHardwareStatus", "RP1210_GetHardwareStatusEx",
                 "RP1210_GetErrorMsg", "RP1210_GetLastErrorMsg", "RP1210_Ioctl", "RP1210Bridge_GetStatus"):
        getattr(dll, name).restype = c_short
    assert dll.RP1210Bridge_SetTarget(target.encode()) == 0
    return dll


@pytest.fixture
def bridge():
    return load_bridge(FAKE)


def connect(dll, proto=b"J1939:Baud=250"):
    return dll.RP1210_ClientConnect(None, c_short(1), proto, 8192, 8192, c_short(0))


def test_status_reports_loaded_32bit_dll(bridge):
    text = ctypes.create_string_buffer(512)
    assert bridge.RP1210Bridge_GetStatus(text, c_short(512)) == 0
    assert b"loaded" in text.value and b"fake_rp1210.dll" in text.value


def test_connect_send_read_disconnect(bridge):
    cid = connect(bridge)
    assert 1 <= cid < 128
    assert connect(bridge, b"J1708") == 136          # vendor error passes through
    msg = bytes(range(1, 30))
    buf = (c_char * 8192)()
    ctypes.memmove(buf, msg, len(msg))
    assert bridge.RP1210_SendMessage(c_short(cid), buf, c_short(len(msg)), c_short(0), c_short(0)) == 0
    out = (c_char * 8192)()
    n = bridge.RP1210_ReadMessage(c_short(cid), out, c_short(8192), c_short(0))
    assert n == len(msg) and out.raw[:n] == msg
    assert bridge.RP1210_ReadMessage(c_short(cid), out, c_short(8192), c_short(0)) == 0   # empty, non-blocking
    assert bridge.RP1210_ClientDisconnect(c_short(cid)) == 0
    assert bridge.RP1210_ClientDisconnect(c_short(cid)) == 129


def test_send_command_copies_buffer_back(bridge):
    cid = connect(bridge)
    cmd = (c_char * 16)(*[bytes([i]) for i in range(16)])
    assert bridge.RP1210_SendCommand(c_short(14), c_short(cid), cmd, c_short(16)) == 0
    assert cmd.raw == bytes(range(15, -1, -1))
    assert bridge.RP1210_SendCommand(c_short(3), c_short(cid), None, c_short(0)) == 0   # NULL buffer
    assert bridge.RP1210_SendCommand(c_short(999), c_short(cid), None, c_short(0)) == 144


def test_versions_status_and_errors(bridge):
    v = [ctypes.create_string_buffer(2) for _ in range(4)]
    bridge.RP1210_ReadVersion(v[0], v[1], v[2], v[3])
    assert [x.raw[:1] for x in v] == [b"7", b"3", b"1", b"C"]

    api, dll_v, fw = (ctypes.create_string_buffer(17) for _ in range(3))
    assert bridge.RP1210_ReadDetailedVersion(c_short(1), api, dll_v, fw) == 0
    assert (api.value, dll_v.value, fw.value) == (b"API-FAKE-1.0", b"DLL-FAKE-32BIT", b"FW-LOOPBACK")

    info = (c_char * 64)()
    assert bridge.RP1210_GetHardwareStatus(c_short(1), info, c_short(18), c_short(0)) == 0
    assert info.raw[:18] == bytes(i ^ 0x5A for i in range(18)) and info.raw[18:] == bytes(46)
    ex = (c_char * 256)()
    assert bridge.RP1210_GetHardwareStatusEx(c_short(1), ex) == 0
    assert ex.raw == bytes(255 - i for i in range(256))

    text = ctypes.create_string_buffer(80)
    assert bridge.RP1210_GetErrorMsg(c_short(142), text) == 0
    assert text.value == b"fake error 142"
    sub = c_int(0)
    assert bridge.RP1210_GetLastErrorMsg(c_short(146), ctypes.byref(sub), text, c_short(2)) == 0
    assert sub.value == 1146 and text.value == b"fake last error 146 on client 2"
    assert bridge.RP1210_Ioctl(c_short(1), c_long(0), None, None) == 143


def test_blocking_read_does_not_block_other_threads(bridge):
    """The CSU app blocks in ReadMessage on reader threads while sending from others."""
    cid = connect(bridge)
    got = {}

    def reader():
        out = (c_char * 8192)()
        n = bridge.RP1210_ReadMessage(c_short(cid), out, c_short(8192), c_short(1))   # blocking
        got["data"] = out.raw[:n]

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.3)
    assert t.is_alive(), "reader should still be blocked"
    msg = b"\x00\xEA\x00\x06\xF9\xFF\xEC\xFE\x00"
    buf = (c_char * 8192)()
    ctypes.memmove(buf, msg, len(msg))
    start = time.time()
    assert bridge.RP1210_SendMessage(c_short(cid), buf, c_short(len(msg)), c_short(0), c_short(0)) == 0
    assert time.time() - start < 1.0, "send must not wait behind the blocked read"
    t.join(2)
    assert got.get("data") == msg


def test_disconnect_releases_blocked_reader(bridge):
    cid = connect(bridge)
    result = {}

    def reader():
        out = (c_char * 64)()
        result["n"] = bridge.RP1210_ReadMessage(c_short(cid), out, c_short(64), c_short(1))

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.2)
    assert bridge.RP1210_ClientDisconnect(c_short(cid)) == 0
    t.join(2)
    assert result.get("n") == -148


def test_missing_vendor_dll_is_reported():
    dll = load_bridge("NO_SUCH_VENDOR_RP1210")
    assert connect(dll) == 128
    text = ctypes.create_string_buffer(512)
    assert dll.RP1210Bridge_GetStatus(text, c_short(512)) == 128
    assert b"cannot load 32-bit NO_SUCH_VENDOR_RP1210" in text.value
    out = (c_char * 64)()
    assert dll.RP1210_ReadMessage(c_short(1), out, c_short(64), c_short(0)) == -128


def test_renamed_copy_serves_its_own_name(tmp_path):
    """A copy named after the vendor DLL needs no SetTarget call."""
    import shutil
    shutil.copy(BRIDGE, tmp_path / "fake_rp1210.dll")
    shutil.copy(os.path.join(BIN, "rp1210_host32.exe"), tmp_path)
    shutil.copy(FAKE, tmp_path / "vendor_fake_rp1210.dll")   # where the host will look
    env = dict(os.environ, RP1210_BRIDGE_TARGET=str(tmp_path / "vendor_fake_rp1210.dll"))
    code = ("import ctypes,sys; d=ctypes.WinDLL(sys.argv[1]); t=ctypes.create_string_buffer(512);"
            "d.RP1210Bridge_GetStatus.restype=ctypes.c_short;"
            "print(d.RP1210Bridge_GetStatus(t, 512), t.value.decode())")
    out = subprocess.run([sys.executable, "-c", code, str(tmp_path / "fake_rp1210.dll")], env=env,
                         capture_output=True, text=True, timeout=30).stdout
    assert out.startswith("0 ") and "vendor_fake_rp1210.dll" in out


def test_host_exits_with_parent():
    """No orphaned host processes: the host watches its parent."""
    code = ("import ctypes,sys; d=ctypes.WinDLL(sys.argv[1]); d.RP1210Bridge_SetTarget(sys.argv[2].encode());"
            "t=ctypes.create_string_buffer(512); d.RP1210Bridge_GetStatus(t, 512)")
    before = _host_count()
    subprocess.run([sys.executable, "-c", code, BRIDGE, FAKE], timeout=30, check=True)
    time.sleep(1.0)
    assert _host_count() <= before


def _host_count():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq rp1210_host32.exe", "/NH"],
                         capture_output=True, text=True).stdout
    return out.lower().count("rp1210_host32.exe")
