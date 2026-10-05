"""Build a small synthetic J1939 Digital Annex workbook for tests.

The workbook mimics the layout of the SAE J1939DA spreadsheet (sheet names,
title rows and column headers, including the SLOT columns and the SLOTs sheet)
but contains only made-up parameters in the manufacturer-proprietary ranges
(PGN 0xFF00-0xFF03, SPNs 520192+, SLOT identifiers 9001+). It has no SAE
content and is generated at test time rather than committed.

SPN 520200 uses a unit with a digit in its scaling text ("0.1 m/s² per bit"),
which pretty_j1939's text parser reads as 0.05; generation must correct it from
the value-only columns.
"""

import openpyxl

SPS_HEADER = [
    "PGN", "PG Label", "PG Acronym", "PG Data Length", "Transmission Rate",
    "SP Position in PG", "SPN", "SP Label", "SP Length", "Scaling", "Offset",
    "Data Range", "Operational Range", "Unit", "SP Description",
    "SLOT Identifier", "SLOT Name", "Scale Factor\n(value only)", "Offset\n(value only)",
    "Length Minimum\n(bits)", "Length Maximum\n(bits)", "Default Priority",
]
PRIORITIES = {65280: 3}   # others 6

# SLOT identifier -> (name, type, unit, transfer function, scale, offset, length min, length max)
SLOTS = {
    9001: ("EX_Speed_rpm", "Velocity, rotational", "rpm", "numeric", 0.125, 0, 16, 16),
    9002: ("EX_Temp_1C", "Temperature", "°C", "numeric", 1, -40, 8, 8),
    9003: ("EX_Press_4kPa", "Pressure", "kPa", "numeric", 4, 0, 8, 8),
    9004: ("EX_Vel_kph", "Velocity, linear", "km/h", "numeric", 0.00390625, 0, 16, 16),
    9005: ("EX_State_2bit", "Bit Field", "", "statevalue", None, None, 2, 2),
    9006: ("EX_Distance_km", "Distance", "km", "numeric", 0.125, 0, 32, 32),
    9007: ("EX_Volume_L", "Volume", "L", "numeric", 0.5, 0, 32, 32),
    9008: ("EX_ASCII", "ASCII, text (variable, \"*\" delimited)", "ASCII", "ascii_asterisk", None, None, 8, 1600),
    9009: ("EX_Accel", "Acceleration", "m/s²", "numeric", 0.1, -12.5, 16, 16),
}

# (PGN, PG label, acronym, length, rate, position, SPN, name, length, scaling text,
#  offset text, data range, operational range, unit, description, SLOT id)
SPS_ROWS = [
    (65280, "Example Proprietary B 1", "EXPB1", 8, "100 ms", "1-2", 520192, "Example Shaft Speed",
     "2 bytes", "0.125 rpm/bit", "0", "0 to 8,031.875 rpm", "", "rpm", "Example speed.", 9001),
    (65280, "Example Proprietary B 1", "EXPB1", 8, "100 ms", "3", 520193, "Example Fluid Temperature",
     "1 byte", "1 °C/bit", "-40", "-40 to 210 °C", "", "°C", "Example temperature.", 9002),
    (65280, "Example Proprietary B 1", "EXPB1", 8, "100 ms", "4", 520194, "Example Fluid Pressure",
     "1 byte", "4 kPa/bit", "0", "0 to 1000 kPa", "", "kPa", "Example pressure.", 9003),
    (65280, "Example Proprietary B 1", "EXPB1", 8, "100 ms", "5-6", 520195, "Example Road Speed",
     "2 bytes", "1/256 km/h per bit", "0", "0 to 250.996 km/h", "", "km/h", "Example speed.", 9004),
    (65280, "Example Proprietary B 1", "EXPB1", 8, "100 ms", "7.1", 520196, "Example Switch",
     "2 bits", "4 states/2 bit", "0", "0 to 3", "", "bit",
     "00 = Off\n01 = On\n10 = Error\n11 = Not available", 9005),
    (65281, "Example Proprietary B 2", "EXPB2", 8, "1 s", "1-4", 520197, "Example Distance",
     "4 bytes", "0.125 km/bit", "0", "0 to 526,385,151.9 km", "", "km", "Example distance.", 9006),
    (65281, "Example Proprietary B 2", "EXPB2", 8, "1 s", "5-8", 520198, "Example Fluid Volume",
     "4 bytes", "0.5 L per bit", "0", "0 to 2,105,540,607.5 L", "", "L", "Example volume.", 9007),
    (65282, "Example Proprietary B 3", "EXPB3", "Variable", "On request", "1-n", 520199,
     "Example Identification", "Variable - up to 200 bytes followed by an \"*\" delimiter",
     "ASCII", "0", "0 to 255 per byte", "", "ASCII", "Example text field.", 9008),
    (65283, "Example Proprietary B 4", "EXPB4", 8, "100 ms", "1-2", 520200, "Example Acceleration",
     "2 bytes", "0.1 m/s² per bit", "-12.5", "-12.5 to 6,412.5 m/s²", "", "m/s²", "Example acceleration.", 9009),
]

SA_ROWS = [(0, "Example Engine Controller"), (249, "Example Service Tool")]
MFR_ROWS = [(683, "Example Manufacturer Inc.")]


LEGACY_HEADER = ["PGN", "Parameter Group Label", "PGN Length", "Transmission Rate", "Acronym", "pos",
                 "SPN length", "SPN", "Name", "Description", "Data Range", "Operational Range", "Resolution",
                 "Offset", "Units"]
# The 2012-era layout (sheet 'SPN & PGN', lengths in bits) with the quirks older releases have.
LEGACY_ROWS = [
    (65280, "Example Proprietary B 1", "8", "100 ms", "EXPB1", "1-2", 16.0, 520192, "Example Shaft Speed",
     "Example speed.", "0 to 8,031.875 rpm", "", "0.125 rpm/bit", 0.0, "rpm"),
    (65280, "Example Proprietary B 1", "8", "100 ms", "EXPB1", "3", 8.0, 520193, "Example Fluid Temperature",
     "Example temperature.", "-40 to 210 deg C", "", "1 deg C/bit", -40.0, "C"),
    (65280, "Example Proprietary B 1", "8", "100 ms", "EXPB1", "5-8", 32.0, 520197, "Example Distance",
     "Example distance; the Units column has the base unit.", "0 to 526,385,151.9 km", "", "0.125 km/bit", 0.0, "m"),
    (65281, "Example Proprietary B 2", "8", "1 s", "EXPB2", "1-4", 32.0, 520201, "Example Latitude",
     "Example position.", "-210 to 211.1008122 deg", "", "10^-7 deg/bit", -210.0, "deg"),
    (65281, "Example Proprietary B 2", "8", "1 s", "EXPB2", "5", 8.0, 520202, "Example Request Field",
     "Free text in the resolution.", "Manufacturer Determined", "", "Request Dependent", 0.0, "Request Dependent"),
    ("", "", "", "", "", "", 8.0, 520203, "Example SPN without a PGN", "", "0 to 250", "", "1 count/bit", 0.0, "count"),
]


def build_legacy(path):
    wb = openpyxl.Workbook()
    doc = wb.active
    doc.title = "Documentation"
    doc.append(["", "COMPANION SPREADSHEET (synthetic, no SAE content)"])
    ws = wb.create_sheet("SPN & PGN")
    ws.append(LEGACY_HEADER)
    for row in LEGACY_ROWS:
        ws.append(list(row))
    sa = wb.create_sheet("Global Source Addresses")
    sa.append(["Source Address ID", "Hx", "Name", "Notes"])
    for addr, name in SA_ROWS:
        sa.append([addr, f"{addr:X}", name, ""])
    wb.save(path)
    return path


def build(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SPs & PGs"
    ws.append(["SPs and PGs"])
    ws.append(["Synthetic test workbook (no SAE content)."])
    ws.append([])
    ws.append(SPS_HEADER)
    for row in SPS_ROWS:
        slot = SLOTS[row[-1]]
        ws.append(list(row[:-1]) + [row[-1], slot[0], slot[4], slot[5], slot[6], slot[7], PRIORITIES.get(row[0], 6)])

    slots = wb.create_sheet("SLOTs")
    slots.append(["SLOTs"])
    slots.append(["Synthetic SLOT listing (no SAE content)."])
    slots.append([])
    slots.append(["Revised", "SLOT Identifier", "SLOT Name", "SLOT Type", "Scaling", "Range", "Offset", "Length",
                  "Unit", "Transfer Function\nType", "Scale Factor\n(value only)", "Offset\n(value only)",
                  "Range Maximum\n(value only)", "Length Minimum\n(bits)", "Length Maximum\n(bits)",
                  "Date Created or Last Modified"])
    for sid, (name, kind, unit, transfer, scale, offset, lmin, lmax) in SLOTS.items():
        slots.append(["", sid, name, kind, "", "", "", "", unit, transfer, scale, offset, None, lmin, lmax, ""])

    sa = wb.create_sheet("Global Source Addresses (B2)")
    sa.append(["Source Address ID", "Name", "Notes"])
    for row in SA_ROWS:
        sa.append(list(row) + [""])
    # Industry-group preferred addresses (128-247 differ per industry group).
    ig1 = wb.create_sheet("IG1 Source Addresses (B3)")
    ig1.append(["Preferred Addresses: synthetic industry group 1"])
    ig1.append([])
    ig1.append(["Revised", "Source Address ID", "Name", "Notes"])
    ig1.append(["", 128, "thru 135 are reserved for future assignment", "Used for dynamic address assignment"])
    ig1.append(["", 200, "Example Trailer Bridge", ""])
    ig2 = wb.create_sheet("IG2 Source Addresses (B4)")
    ig2.append(["Preferred Addresses: synthetic industry group 2"])
    ig2.append([])
    ig2.append(["Revised", "Source Address ID", "Device Class", "Device Class Instance", "Function", "Description"])
    ig2.append(["", 200, 0, 0, "Example Implement Server", ""])
    mfr = wb.create_sheet("Manufacturer IDs (B10)")
    mfr.append(["Manufacturer Code", "Manufacturer Name"])
    for row in MFR_ROWS:
        mfr.append(list(row))
    wb.save(path)
    return path
