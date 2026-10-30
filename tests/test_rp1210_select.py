"""The RP1210 selection dialog picks vendor, device, protocol and speed by name from the vendor INIs.

Runs offscreen against a fake Windows directory (RP121032.ini plus two vendor INIs), so no
adapter drivers are needed.
"""

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt5.QtWidgets")

import RP1210Select  # noqa: E402

# Like the DG DPA XL: the NULL protocol comes first, and one section has no Devices key.
VEND_A = """[VendorInformation]
Name=Vendor A XL
[DeviceInformation1]
DeviceID=1
DeviceDescription=A XL USB
DeviceName=A XL
MultiCANChannels=4
MultiJ1939Channels=4
[ProtocolInformation1]
ProtocolDescription=The NULL Protocol
ProtocolString=NULL
ProtocolSpeed=
Devices=1
[ProtocolInformation100]
ProtocolDescription=SAE J1939 protocol
ProtocolString=J1939
ProtocolSpeed=125,250,500,666,1000,Auto
Devices=1
[ProtocolInformation102]
ProtocolDescription=CAN Network Protocol
ProtocolString=CAN
ProtocolSpeed=125,250,500,666,1000,Auto
Devices=1
[ProtocolInformation199]
ProtocolDescription=Section without a Devices key
ProtocolString=ODD
"""

# Like PEAK: J1939 first, two devices, no CAN on device 2.
VEND_B = """[VendorInformation]
Name=Vendor B
[DeviceInformation1]
DeviceID=1
DeviceDescription=B USB
DeviceName=B USB
[DeviceInformation2]
DeviceID=2
DeviceDescription=B PCI
DeviceName=B PCI
MultiJ1939Channels=2
[ProtocolInformation1]
ProtocolDescription=J1939 Link Layer
ProtocolString=J1939
ProtocolSpeed=250,500,Auto
Devices=1,2
[ProtocolInformation2]
ProtocolDescription=CAN
ProtocolString=CAN
ProtocolSpeed=250,500,1000
Devices=1, 2
"""


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def windir(tmp_path, monkeypatch):
    win = tmp_path / "Windows"
    win.mkdir()
    (win / "RP121032.ini").write_text("[RP1210Support]\nAPIImplementations=VENDB, VENDA\n")
    (win / "VENDA.ini").write_text(VEND_A)
    (win / "VENDB.ini").write_text(VEND_B)
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setenv("WINDIR", str(win))
    monkeypatch.chdir(storage)                                   # get_storage_path() is the working directory
    monkeypatch.setattr(RP1210Select, "find_csucan", lambda: None)
    return storage


def state(dlg):
    items = lambda c: [c.itemText(i) for i in range(c.count())]
    return dict(vendor=dlg.vendor_combo_box.currentText().split("-")[0].strip(),
                device=dlg.device_combo_box.currentText().split(":")[0],
                protocol=dlg.protocol_combo_box.currentText().split(":")[0],
                protocols=[p.split(":")[0] for p in items(dlg.protocol_combo_box)],
                speed=dlg.speed_combo_box.currentText(), speeds=items(dlg.speed_combo_box),
                channels=items(dlg.channel_combo_box))


def choose(combo, prefix):
    combo.setCurrentIndex([i for i in range(combo.count()) if combo.itemText(i).startswith(prefix)][0])


def test_defaults_to_j1939_at_250_not_the_null_protocol(app, windir):
    dlg = RP1210Select.SelectRP1210("test")
    s = state(dlg)
    assert s["vendor"] == "VENDA" and s["protocols"] == ["NULL", "J1939", "CAN"]   # ODD has no Devices
    assert (s["protocol"], s["speed"]) == ("J1939", "250")
    assert s["speeds"] == ["125", "250", "500", "666", "1000", "Auto"]                 # INI order
    assert s["channels"] == ["1", "2", "3", "4"]


def test_switching_adapters_reparses_the_vendor_ini(app, windir):
    dlg = RP1210Select.SelectRP1210("test")
    choose(dlg.vendor_combo_box, "VENDB"); dlg.fill_device()
    s = state(dlg)
    assert (s["vendor"], s["protocols"], s["protocol"], s["speeds"], s["speed"]) == \
        ("VENDB", ["J1939", "CAN"], "J1939", ["250", "500", "Auto"], "250")
    # A protocol and speed the user picks carry over to the next adapter when it offers them.
    choose(dlg.protocol_combo_box, "CAN:"); dlg.protocol_chosen()
    choose(dlg.speed_combo_box, "500"); dlg.speed_chosen()
    choose(dlg.vendor_combo_box, "VENDA"); dlg.fill_device()
    assert (state(dlg)["protocol"], state(dlg)["speed"]) == ("CAN", "500")


def test_last_connection_is_restored_by_name(app, windir):
    (windir / "RP1210_selection.txt").write_text("1,0,0")      # stale list positions are ignored
    (windir / "Last_RP1210_Connection.json").write_text(json.dumps(
        {"dll_name": "VENDB", "protocol": "CAN", "deviceID": 2, "speed": "1000", "channel": 2}))
    dlg = RP1210Select.SelectRP1210("test")
    s = state(dlg)
    assert (s["vendor"], s["device"], s["protocol"], s["speed"]) == ("VENDB", "2", "CAN", "1000")
    assert dlg.channel_combo_box.currentText() == "2"
    dlg.connect_RP1210()
    assert (dlg.dll_name, dlg.deviceID, dlg.protocol, dlg.speed, dlg.channel) == ("VENDB", 2, "CAN", "1000", 2)
    # The saved channel belongs to VENDB device 2; another adapter starts on channel 1.
    choose(dlg.vendor_combo_box, "VENDA"); dlg.fill_device()
    assert (state(dlg)["channels"], dlg.channel_combo_box.currentText()) == (["1", "2", "3", "4"], "1")
