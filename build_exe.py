"""
Build a portable, single-file CSU_RP1210.exe with PyInstaller.

    python build_exe.py            # 64-bit exe with the current Python
    py -3.10-32 build_exe.py       # 32-bit exe (for 32-bit-only RP1210 drivers such as DG DPA5)

Output: dist/CSU_RP1210.exe (64-bit) or dist/CSU_RP1210_x86.exe (32-bit).

Bundled: icons, version.json, the skeleton J1939db.json, j1939_units.json and the
decode test vectors. NOT bundled: licensed databases or Digital Annex workbooks.
Put J1939db.licensed.json / J1939db.us.licensed.json next to the exe (or create
them with File > J1939 Database); settings, the last RP1210 connection and
CSU_RP1210.log are written there too, so the folder stays portable.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SEP = ";" if os.name == "nt" else ":"

DATA = [
    ("icons", "icons"),
    ("version.json", "."),
    ("J1939db.json", "."),
    ("j1939_units.json", "."),
    (os.path.join("tests", "j1939db_vectors.json"), "tests"),
]


def main():
    is_64bit = sys.maxsize > 2**32
    name = "CSU_RP1210" if is_64bit else "CSU_RP1210_x86"
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
            "--name", name, "--collect-data", "pretty_j1939",
            "--exclude-module", "pytest", "--exclude-module", "tkinter"]
    for src, dest in DATA:
        args += ["--add-data", f"{os.path.join(ROOT, src)}{SEP}{dest}"]
    args.append(os.path.join(ROOT, "CSU_RP1210.py"))
    print(" ".join(args))
    subprocess.run(args, cwd=ROOT, check=True)
    exe = os.path.join(ROOT, "dist", name + (".exe" if os.name == "nt" else ""))
    print(f"\nBuilt {exe} ({os.path.getsize(exe) / 1e6:.1f} MB, {'64' if is_64bit else '32'}-bit)")


if __name__ == "__main__":
    main()
