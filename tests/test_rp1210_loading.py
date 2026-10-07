import pytest

pytest.importorskip("PyQt5")

import RP1210


def test_rejected_bridge_target_prevents_driver_initialization(monkeypatch):
    class Bridge:
        @staticmethod
        def RP1210Bridge_SetTarget(target):
            return 130

    class Loader:
        @staticmethod
        def LoadLibrary(path):
            return Bridge()

    monkeypatch.setattr(RP1210, "windll", Loader(), raising=False)
    driver = RP1210.RP1210Class.__new__(RP1210.RP1210Class)
    driver.dll_path = "rp1210_bridge64.dll"
    driver.bridge_target = "VENDOR_B"

    assert driver.create_RP1210_functions() is False
    assert driver.ClientConnect is None
