"""
Generate the CSU-RP1210 icon set.

The icons are original artwork drawn here as SVG (64x64, a white glyph on a
colored tile; one color per kind of action). They are part of this project and
carry its license; no third-party icons or attribution are needed.

    python tools/make_icons.py

writes icons/<name>.svg for every icon, icons/<name>.png (64 px) for the ones
used in rich-text labels, icons/splash.png and icons/csu_rp1210.ico.
The application renders the SVGs itself (app_icons.py).
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_DIR = os.path.join(ROOT, "icons")

WHITE = "#ffffff"
COLORS = {
    "file": "#35577a",      # import / quit
    "j1939": "#1f6fb8",     # J1939 database
    "j1587": "#2e8b57",     # J1587 database
    "convert": "#7a4fb5",   # export / convert
    "connect": "#2e8b57",
    "disconnect": "#c0392b",
    "info": "#556270",      # versions
    "hardware": "#b7791f",  # adapter status
    "help": "#1e4d2b",
}
CSU_GREEN, CSU_GOLD = "#1e4d2b", "#c8c372"

LINE = 'fill="none" stroke="#ffffff" stroke-linecap="round" stroke-linejoin="round"'
STROKE = LINE + ' stroke-width="4"'
TEXT = 'fill="#ffffff" font-family="Arial, Helvetica, sans-serif" font-weight="bold" text-anchor="middle"'


def tile(color, glyph, extra=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
            f'<rect x="4" y="4" width="56" height="56" rx="12" fill="{color}"/>'
            f'<g {STROKE}>{glyph}</g>{extra}</svg>')


def cylinder(label):
    return ('<ellipse cx="32" cy="17" rx="15" ry="5"/>'
            '<path d="M17 17 V45 A15 5 0 0 0 47 45 V17"/>'
            '<path d="M17 26 A15 5 0 0 0 47 26" stroke-width="2.5"/>',
            f'<text x="32" y="44" font-size="15" {TEXT}>{label}</text>')


def chip():
    pins = "".join(f'<path d="M{x} 10 V17 M{x} 47 V54 M10 {x} H17 M47 {x} H54" stroke-width="3"/>' for x in (24, 32, 40))
    return '<rect x="17" y="17" width="30" height="30" rx="4"/>' + pins


ICONS = {
    # File
    "import_logger": tile(COLORS["file"],
        '<path d="M21 11 H37 L45 19 V53 H21 Z"/><path d="M33 23 V41"/><path d="M26 34 L33 41 L40 34"/>'),
    "import_vehicle_spy": tile(COLORS["file"],
        '<path d="M17 10 H37 L46 19 V54 H17 Z"/><path d="M37 10 V19 H46" stroke-width="3"/>'
        '<path d="M21 38 H26 L29 28 L33 47 L36 33 L39 38 H42" stroke-width="3"/>'),
    "quit": tile(COLORS["file"], '<path d="M23 22 A14 14 0 1 0 41 22"/><path d="M32 13 V32"/>'),
    # Databases and conversions
    "j1939_database": tile(COLORS["j1939"], *cylinder("39")),
    "j1587_database": tile(COLORS["j1587"], *cylinder("87")),
    "export_dbc": tile(COLORS["convert"],
        '<path d="M13 10 H31 L39 18 V54 H13 Z"/><path d="M31 10 V18 H39" stroke-width="3"/>'
        '<path d="M27 36 H53"/><path d="M46 29 L53 36 L46 43"/>'),
    "convert_candump": tile(COLORS["convert"],
        '<path d="M15 24 H47"/><path d="M40 17 L47 24 L40 31"/><path d="M49 42 H17"/><path d="M24 35 L17 42 L24 49"/>'),
    # RP1210
    "connect": tile(COLORS["connect"],
        '<rect x="9" y="25" width="26" height="14" rx="7"/><rect x="29" y="25" width="26" height="14" rx="7"/>'),
    "disconnect": tile(COLORS["disconnect"],
        '<rect x="6" y="25" width="22" height="14" rx="7"/><rect x="36" y="25" width="22" height="14" rx="7"/>'
        '<path d="M32 13 V19 M32 45 V51" stroke-width="3"/>'),
    "driver_version": tile(COLORS["info"],
        '<path d="M10 32 L25 14 H54 V50 H25 Z"/><circle cx="23" cy="32" r="2.5" fill="#ffffff"/>',
        f'<text x="40" y="41" font-size="22" {TEXT}>v</text>'),
    "detailed_version": tile(COLORS["info"],
        '<path d="M10 32 L25 14 H54 V50 H25 Z"/><circle cx="23" cy="32" r="2.5" fill="#ffffff"/>'
        '<path d="M32 25 H47 M32 32 H47 M32 39 H44" stroke-width="3"/>'),
    "hardware_status": tile(COLORS["hardware"], chip()),
    "hardware_status_ex": tile(COLORS["hardware"], chip(),
        '<circle cx="47" cy="47" r="11" fill="#ffffff"/>'
        f'<path d="M47 41 V53 M41 47 H53" stroke="{COLORS["hardware"]}" stroke-width="4" stroke-linecap="round"/>'),
    # Help
    "help": tile(COLORS["help"], '', f'<text x="32" y="47" font-size="40" {TEXT}>?</text>'),
    "shortcuts": tile(COLORS["help"],
        '<rect x="9" y="19" width="46" height="28" rx="4"/>'
        '<path d="M17 28 H18 M25 28 H26 M33 28 H34 M41 28 H42 M47 28 H48 M20 34 H21 M28 34 H29 M36 34 H37 M44 34 H45" '
        'stroke-width="3.5"/><path d="M22 40 H42" stroke-width="3"/>'),
    "about": tile(COLORS["help"], '<circle cx="32" cy="18" r="1" stroke-width="6"/><path d="M32 28 V48"/>'),
}

# Network status images (used in rich-text labels, so also written as PNG)
ICONS["network_online"] = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
    '<circle cx="32" cy="32" r="27" fill="#2e8b57"/>'
    f'<path d="M19 33 L28 42 L45 23" {LINE} stroke-width="6"/></svg>')
ICONS["network_unavailable"] = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
    '<circle cx="32" cy="32" r="27" fill="#c0392b"/>'
    f'<circle cx="32" cy="32" r="15" {LINE} stroke-width="5"/><path d="M21 43 L43 21" {LINE} stroke-width="5"/></svg>')
ICONS["client_connected"] = ICONS["connect"]
ICONS["client_disconnected"] = ICONS["disconnect"]

# Application icon: a truck on a CAN waveform, in Colorado State green and gold.
TRUCK = (f'<rect x="8" y="17" width="28" height="19" rx="2" fill="{CSU_GOLD}"/>'
         f'<path d="M38 22 H47 L54 30 V36 H38 Z" fill="{CSU_GOLD}"/>'
         f'<path d="M41 25 H46 L50 30 H41 Z" fill="{CSU_GREEN}"/>'
         + "".join(f'<circle cx="{x}" cy="38" r="4.5" fill="{CSU_GOLD}" stroke="{CSU_GREEN}" stroke-width="2"/>'
                   for x in (16, 28, 46))
         + f'<path d="M8 51 H18 L21 46 L25 55 L29 47 L32 51 H56" fill="none" stroke="{CSU_GOLD}" '
           'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>')
ICONS["app"] = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
                f'<rect x="2" y="2" width="60" height="60" rx="14" fill="{CSU_GREEN}"/>{TRUCK}</svg>')

SPLASH = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 340" width="600" height="340">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    f'<stop offset="0" stop-color="{CSU_GREEN}"/><stop offset="1" stop-color="#0d2414"/></linearGradient></defs>'
    '<rect width="600" height="340" rx="18" fill="url(#g)"/>'
    f'<g transform="translate(40 70) scale(2.6)"><rect x="2" y="2" width="60" height="60" rx="14" '
    f'fill="#163a20" stroke="{CSU_GOLD}" stroke-width="1"/>{TRUCK}</g>'
    f'<text x="230" y="140" font-family="Arial, Helvetica, sans-serif" font-size="46" font-weight="bold" fill="#ffffff">CSU-RP1210</text>'
    f'<text x="232" y="176" font-family="Arial, Helvetica, sans-serif" font-size="19" fill="{CSU_GOLD}">Heavy vehicle network diagnostics</text>'
    f'<text x="232" y="204" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="#d8e4dc">J1939 · J1587 · RP1210 · CAN FD</text>'
    f'<path d="M40 290 H120 L130 272 L142 306 L152 280 L160 290 H560" fill="none" stroke="{CSU_GOLD}" '
    'stroke-width="3" stroke-linecap="round" stroke-linejoin="round" opacity="0.6"/>'
    '</svg>')

PNG_ICONS = ("network_online", "network_unavailable", "client_connected", "client_disconnected", "app")


def render(svg_text, width, height):
    from PyQt5.QtCore import QByteArray, Qt
    from PyQt5.QtGui import QImage, QPainter
    from PyQt5.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    renderer.render(painter)
    painter.end()
    return image


def main():
    if os.name != "nt":
        # Windows renders with the system fonts (Arial); elsewhere draw without a display.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    os.makedirs(ICON_DIR, exist_ok=True)
    for name, svg in ICONS.items():
        with open(os.path.join(ICON_DIR, name + ".svg"), "w", encoding="utf-8", newline="\n") as f:
            f.write(svg + "\n")
    for name in PNG_ICONS:
        render(ICONS[name], 64, 64).save(os.path.join(ICON_DIR, name + ".png"))
    with open(os.path.join(ICON_DIR, "splash.svg"), "w", encoding="utf-8", newline="\n") as f:
        f.write(SPLASH + "\n")
    render(SPLASH, 600, 340).save(os.path.join(ICON_DIR, "splash.png"))
    # Windows icon for the executable (several sizes in one .ico).
    big = os.path.join(ICON_DIR, "app_256.png")
    render(ICONS["app"], 256, 256).save(big)
    from PIL import Image
    Image.open(big).save(os.path.join(ICON_DIR, "csu_rp1210.ico"),
                         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    os.remove(big)
    print(f"Wrote {len(ICONS)} icons, {len(PNG_ICONS)} PNGs, splash.png and csu_rp1210.ico to {ICON_DIR}")
    del app


if __name__ == "__main__":
    main()
