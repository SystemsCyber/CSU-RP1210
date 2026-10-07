# CSU-RP1210
 Heavy Vehicle diagnostic prototyping and training software for ATA/TMC RP1210 compatible devices.

## Portable Windows executable

`CSU_RP1210.exe` is a single-file build of the GUI. It needs no Python installation; copy it to any folder and run it.

```
python build_exe.py            # builds dist\CSU_RP1210.exe (64-bit) with PyInstaller
py -3.10-32 build_exe.py       # builds dist\CSU_RP1210_x86.exe (32-bit)
```

- **Which build to use:** the 64-bit exe loads 64-bit RP1210 drivers, such as PEAK's PEAKRP32 and DG's DPA XL (DGDPAXL), directly. It reaches 32-bit-only drivers, such as DG DPA5, through the bundled [RP1210 32-to-64-bit bridge](rp1210_bridge/README.md); build that first with `rp1210_bridge\build.bat`. `csu devices` shows which drivers you have and which go through the bridge.
- **Files kept next to the exe:** `J1939db.licensed.json` / `J1939db.us.licensed.json` (create them with Tools > J1939 Database), `J1587db.licensed.json` / `J1587db.us.licensed.json` (Tools > J1587 Database), `csu_settings.json`, `Last_RP1210_Connection.json` and `CSU_RP1210.log`. The folder is self-contained.
- **PEAK adapters without vendor setup:** the bundled [CSUCAN driver](crates/csucan/README.md) appears in the RP1210 dialog as **CSUCAN – CSU native CAN**. It lists every attached PEAK channel (PCAN-USB FD, PCAN-PCI Express FD, …) directly from PCAN-Basic and supports CAN FD (`250/2000` speeds). On Linux it serves SocketCAN interfaces.
- **Start-up and shortcuts:** a splash screen appears as soon as the program starts (and, in the exe, while it unpacks). Every menu command has a keyboard shortcut and every tab button an Alt key; Help > Keyboard Shortcuts (Ctrl+/) lists them, and Ctrl+1 ... Ctrl+7 switch tabs.
- **Icons:** the icon set is original artwork of this project (SVG in `icons/`, generated with `python tools/make_icons.py`), under the project's license; no third-party icons are used.
- **Multi-channel adapters:** the RP1210 dialog has a **Channel** selector (for example, channel 4 of a PEAK PCAN-PCI Express FD). It is sent to the driver as `Channel=N`.
- **Switching adapters:** the dialog reads each adapter's vendor INI and selects by name, not by list position. It keeps the protocol and speed you chose when the new adapter offers them, otherwise it selects J1939 at 250 kbit/s, and it never selects the NULL protocol. The last connection's device and channel are restored only for that adapter.

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

`J1939db.json` in this repository is a **skeleton**: it has the schema and no SAE J1939 Digital Annex content. Create the real databases from your licensed Digital Annex with the **J1939 Digital Annex** dialog: run `python DigitalAnnexSelect.py`, or use Tools > J1939 Database in CSU-RP1210.

- **Older releases:** workbooks are recognized by their columns rather than sheet names, so older layouts work too (e.g. `cs1939_012012.xls`, sheet `SPN & PGN` with columns `pos`, `Name`, `Description`, `PGN Length` and lengths in bits). They are converted to the current layout first; units are taken from the resolution text where the Units column disagrees (2012 lists SPN 245 in "m" with "0.125 km/bit"), `10^-7` exponents are written out, and free text the parser cannot read ("Request Dependent", "Manufacturer Determined") makes that SPN a raw value. The log lists every adjustment. All spreadsheets (`*.xls`, `*.xlsx`) are git-ignored.
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

### J1587 database (metric and US customary)

`J1587db.json` is also a **skeleton**. Tools > J1587 Database (or `python J1587DatabaseSelect.py`) reads your licensed **SAE J1587** document (PDF) and writes `J1587db.licensed.json` (metric) and `J1587db.us.licensed.json` (US customary) in the format the J1587 tab uses: MID names (Table 1), PID definitions (Appendix A: data length and type, resolution, offset, range, update period, priority), PID names (Table 2), FMIs (Table 6) and SIDs by MID (Table 7). Add the **SAE J1708** PDF to name MIDs 0-127 from its MID allocation table.

- J1587 is a US-units standard: temperatures are in deg F and most metric resolutions are rounded ("0.689 kPa (0.1 lbf/in2)"). Each parameter is converted from its exact value with `j1939_units.json`, so a metric temperature has resolution r*5/9 and offset -17.78 deg C. Both databases share the units preference with J1939.
- The per-page license stamp of the PDF is removed during extraction and checked for in the tests.
- **Validate** runs structural checks and `tests/j1587db_vectors.json`: 57 payloads recorded from a DDEC6 truck (28 PIDs, at most 3 each, no SAE text), with expected metric and US values. `tests/test_licensed_j1587.py` also checks the parsing independently: on the recorded excerpt, every engine value broadcast on both networks (coolant, oil and exhaust temperatures, battery, fuel and barometric pressure, engine speed) agrees between the J1587 and J1939 databases within one resolution step.

```
python j1587db_tools.py generate J1587.pdf J1708.pdf --out .
python j1587db_tools.py validate J1587db.us.licensed.json
python tests/build_j1587_vectors.py RECORDING.csv     # regenerate vectors and the fixture excerpt
```

### Tools menu and utilities

| Menu | Does | Command line |
|---|---|---|
| Tools > J1939 Database (Ctrl+D) | Digital Annex -> J1939 databases, validation, test vectors | `j1939db_tools.py` |
| Tools > J1587 Database (Ctrl+Shift+D) | SAE J1587 PDF -> J1587 databases, validation | `j1587db_tools.py` |
| Tools > Export J1939 DBC | the loaded J1939 database (metric or US) as a `.dbc` for SavvyCAN, cantools or CANalyzer | `j1939_dbc.py J1939db.licensed.json` |
| Tools > Convert Vehicle Spy Log to candump | a neoVI log's CAN traffic for `csu decode/summary` or a CSUCAN replay device | `vehicle_spy.py candump LOG.csv` |
| File > Import Vehicle Spy Log (Ctrl+Shift+I) | plays a Vehicle Spy 3 `.csv` (J1939 and J1708) through the J1939 and J1587 tabs, with J1939 BAM/RTS-CTS reassembly | `vehicle_spy.py summary LOG.csv` |

**J1939 PGN table.** Rows start with the source address and PGN; click any header to sort. **Expand Multiplexed PGNs** (next to *Dynamically Update Table*) splits PGNs whose first byte(s) select the message into one row per selector, shown in the *Multiplexer* column:
- TP.CM / ETP.CM control byte (RTS, CTS, BAM, EoMA, Abort) and the Acknowledgment control byte;
- Request, by the requested PGN;
- ISO 15765 (0xDA00), by frame type and UDS service (e.g. `SF 0x22 ReadDataByIdentifier`);
- ISO 11783 virtual terminal (0xE600, 0xE700) by function code (e.g. `0xFE VT Status`), process data, file server;
- Proprietary A (0xEF00), A2 and B (0xFF00-0xFFFF) by their first byte.

**Source addresses.** Addresses 128-247 mean different devices in each industry group, so the J1939 tab has an **Industry group** selector (default 1, On-Highway; saved in `csu_settings.json`). The per-group tables come from the Digital Annex sheets B3-B7 (`J1939SATabledbByIG`; create the database again to get them). A device's **Address Claimed** NAME (PGN 60928) overrides that interpretation for its address and is shown as e.g. `Transmission #2 (claimed)`. The Component Information tab lists every claim (industry group, vehicle system, function and instances, manufacturer, identity number) and has a **Request Address Claims** button.

**Which database is loaded** is shown at the right of the status bar, in red when only a skeleton is found. The licensed databases are searched next to the program, in the current folder and, for `dist\CSU_RP1210.exe`, in the repository folder above it.

Normal broadcast PGNs are never split. A PGN whose first byte turns out to be a signal is capped at 64 rows per source address, after which values share an *other* row. Add OEM or implement-specific multiplexers to `j1939_mux.json`. Turning the option on or off clears the PGN table.

The DBC exporter replaces the former J1939Converters scripts (J1939toDBC, J1939toJSON and their Tk GUI). It starts from the database the application already uses rather than re-reading the spreadsheet, so it has the same scaling corrections and units. Messages use the Digital Annex default priority, source address 254, J1939 attributes (PGN, SPN, VFrameFormat, cycle time) and value tables from the state decodings. SPNs a DBC cannot represent (text, variable length, split fields) are named in the message comment. DBC exports contain licensed content; `*.licensed.dbc` is git-ignored. J1939toJSON is not needed: `J1939db*.licensed.json` already holds the PGNs, SPNs, state decodings and source addresses.





