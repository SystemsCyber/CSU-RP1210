"""The icon set (original artwork from tools/make_icons.py) and the keyboard shortcuts."""

import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = ("CSU_RP1210.py", "app_icons.py", "ComponentInfoTab.py", "J1939Tab.py", "J1587Tab.py")


def source(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def used_icons():
    names = set()
    for name in SOURCES:
        text = source(name)
        names |= set(re.findall(r'make_action\(\s*"(\w+)"', text))
        names |= set(re.findall(r'app_icons\.icon\("(\w+)"\)', text))
        names |= set(re.findall(r'status_html\("(\w+)"', text))
    return names


def test_every_icon_in_use_exists_and_matches_the_generator():
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import make_icons
    names = used_icons()
    assert {"app", "connect", "disconnect", "j1939_database", "network_online"} <= names
    for name in names:
        path = os.path.join(ROOT, "icons", name + ".svg")
        assert os.path.exists(path), name
        with open(path, encoding="utf-8") as f:
            assert f.read().strip() == make_icons.ICONS[name], f"{name}.svg differs from tools/make_icons.py"


def test_icons_render(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    import app_icons
    for name in used_icons():
        image = app_icons.render(name, 32)
        opaque = sum(image.pixelColor(x, y).alpha() > 0 for x in range(0, 32, 2) for y in range(0, 32, 2))
        assert opaque > 40, f"{name} rendered empty"
        assert not app_icons.icon(name).isNull()
    for status in ("network_online", "network_unavailable", "client_connected", "client_disconnected"):
        assert os.path.exists(app_icons.image_path(status))
    assert os.path.exists(os.path.join(ROOT, "icons", "csu_rp1210.ico"))
    splash = app_icons.Splash("1.2.3")
    assert splash.pixmap().width() >= 600
    splash.close()


def test_menu_shortcuts_are_unique():
    text = source("CSU_RP1210.py")
    keys = re.findall(r"make_action\(\s*\"\w+\",\s*'[^']*',\s*'([^']+)'", text)
    assert len(keys) >= 15
    duplicates = {k for k in keys if keys.count(k) > 1}
    assert not duplicates, f"shortcuts used twice: {duplicates}"


def test_no_third_party_icons_are_tracked():
    if shutil.which("git") is None:
        pytest.skip("git not available")
    tracked = subprocess.run(["git", "ls-files", "icons"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert not [f for f in tracked if "icons8" in f.lower()]
