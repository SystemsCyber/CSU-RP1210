# CSU-RP1210
 Heavy Vehicle diagnostic prototyping and training software for ATA/TMC RP1210 compatible devices.

## Portable Windows executable

`CSU_RP1210.exe` is a single-file build of the GUI. It needs no Python installation; copy it to any folder and run it.

```
python build_exe.py            # builds dist\CSU_RP1210.exe (64-bit) with PyInstaller
py -3.10-32 build_exe.py       # builds dist\CSU_RP1210_x86.exe (32-bit)
```

- **Which build to use:** the 64-bit exe loads 64-bit RP1210 drivers, such as PEAK's PEAKRP32, directly. It reaches 32-bit-only drivers, such as DG DPA5, through the bundled [RP1210 32-to-64-bit bridge](rp1210_bridge/README.md); build that first with `rp1210_bridgeuild.bat`. `csu devices` shows which drivers you have and which go through the bridge.
- **Files kept next to the exe:** `J1939db.licensed.json` / `J1939db.us.licensed.json` (create them with File > J1939 Database), `csu_settings.json`, `Last_RP1210_Connection.json` and `CSU_RP1210.log`. The folder is self-contained.
- **Multi-channel adapters:** the RP1210 dialog has a **Channel** selector (for example, channel 4 of a PEAK PCAN-PCI Express FD). It is sent to the driver as `Channel=N`.

## Rust core (next generation, in progress)

A cross-platform Rust core lives in `crates/`. It supports RP1210 on Windows, PEAK PCAN-Basic with CAN FD on Windows and Linux, SocketCAN on Linux, and candump log replay. See [docs/ARCHITECTURE_PROPOSAL.md](docs/ARCHITECTURE_PROPOSAL.md) for the architecture and [model/sysml](model/sysml/README.md) for the SysML v2 model.

```
cargo build --release
target/release/csu devices                                   # installed RP1210 drivers and adapters
target/release/csu summary path/to/capture.log               # network inventory
target/release/csu decode pcan:USBBUS1,bitrate=500000 --limit 100
target/release/csu serve socketcan:can0                      # web tree view at http://127.0.0.1:8210
target/release/csu serve rp1210:PEAKRP32,device=1,bitrate=250000
```

Build a 32-bit binary (`cargo build --release --target i686-pc-windows-msvc`) for vendors whose RP1210 DLLs are 32-bit only.

### J1939 database (metric and US customary)

`J1939db.json` in this repository is a **skeleton**: it has the schema and no SAE J1939 Digital Annex content. Create the real databases from your licensed Digital Annex with the **J1939 Digital Annex** dialog: run `python DigitalAnnexSelect.py`, or use File > J1939 Database in CSU-RP1210.

- **Create:** select the Digital Annex workbook(s) (`.xlsx` or `.xls`) and write either or both databases. You also choose which units CSU-RP1210 uses; that choice is saved in `csu_settings.json`.
  - `J1939db.licensed.json`: metric (SI), as published in the Digital Annex
  - `J1939db.us.licensed.json`: US customary (deg F, psi, mph, miles, gallons, lb, hp, ...), converted with the editable table in `j1939_units.json`
- **Validate:** check any database version: structure, SPN references, start bits, field bounds, overlaps, numeric fields, Python-app compatibility, and unit consistency. Optionally compare it with another version (added/removed/changed PGNs and SPNs), or with the other unit system (every conversion is cross-checked).
- **Validate, SLOT cross-check:** given the Digital Annex workbook, every numeric SPN in the metric file is checked against its SLOT definition (scale factor, offset, unit, length limits). The US file is checked against the SLOT values converted with `j1939_units.json`. The check also lists which SLOT units are converted and which are kept as published. Generation takes scaling from the Digital Annex's numeric "value only" columns, which corrects SPNs whose unit text contains digits (such as m/s² or km²/h²); pretty_j1939's text parser misreads those.
- **Test vectors:** edit decode test cases (PGN, payload, SPN, expected metric and US values, or expected status/text) and run them against a database. They are stored in `tests/j1939db_vectors.json` and regenerated with `python tests/build_vectors.py`. That script encodes a realistic highway-cruise operating point and real key-on frames (`tests/fixtures/mcx30_keyon_excerpt.log`) using your local licensed databases.

**Licensing guard:** the vectors hold payloads and expected results only. They cover at most 40 widely published SPNs, with at most 3 vectors each, and contain no scaling, bit positions or Digital Annex text. `tests/test_no_licensed_content.py` enforces this on every test run, and fails if a workbook or licensed database is staged. With the workbook present, `tests/test_licensed_db.py` also scans all tracked files for Digital Annex description text.

Both generated files are git-ignored. Never commit them. The Python application and the Rust core (`csu --units metric|us`, or `$CSU_UNITS`) load the file for the preferred units, falling back to the other one, then to the skeleton. `CSU_J1939DB` or `--db` overrides the search.

Command-line equivalents and the test suite:

```
python j1939db_tools.py generate path/to/J1939DA.xlsx --out .
python j1939db_tools.py validate J1939db.us.licensed.json --baseline J1939db.licensed.json --da J1939DA*.xlsx
python -m pytest            # includes checks of your licensed databases when present
```

## Setup
Install a 32-bit version of Python onto a Windows computer.

Be sure to have pip installed.

Open the Command Prompt and confirm the Python install by typing `python`. 

If the following shows up

```Python 3.8.5 (tags/v3.8.5:580fbb0, Jul 20 2020, 15:57:54) [MSC v.1924 64 bit (AMD64)] on win32```

Then you'll have to use the windows python launcher `py -3.7` (or whatever your 32-bit version is). You should get a response at the command prompt like this:
``` 
C:\Users\Jeremy>py -3.7
Python 3.7.4 (tags/v3.7.4:e09359112e, Jul  8 2019, 19:29:22) [MSC v.1916 32 bit (Intel)] on win32
Type "help", "copyright", "credits" or "license" for more information.
>>>
```

Type `exit()` to quit the interpeter. The imortant thing is to check for 32-bit python.

### Install the Requirements
From the command prompt, navigate to the directory of this application (after you've cloned or downloaded it). Then execute the command:

`py -3.7 -m pip install -r requirements.txt`

Once the requirements are installed, run the program:

`py -3.7 -m CSU_RP1210`





