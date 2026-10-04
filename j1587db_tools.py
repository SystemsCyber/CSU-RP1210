"""
Tools for building and checking CSU-RP1210 J1587 databases.

* generate():  SAE J1587 PDF -> J1587db.licensed.json (metric) and
               J1587db.us.licensed.json (US customary), in the format the J1587
               tab reads (MID, PID, PIDNames, SID, FMI).
* decode_pid(): reference decoder for one PID's data bytes.
* validate():  structural checks for any J1587db*.json version.
* run_vectors(): decode test vectors (MID, PID, data -> expected value in each
               unit system) against a database.

The SAE document is licensed to the user. Generated databases stay next to the
program and are git-ignored; nothing from the document is committed.

    python j1587db_tools.py generate J1587.pdf [--out DIR]
    python j1587db_tools.py validate J1587db.licensed.json [--vectors tests/j1587db_vectors.json]

J1587 is a US-units standard: temperatures are in deg F, and most metric
resolutions are rounded from an exact US value ("0.689 kPa (0.1 lbf/in2)").
Each parameter is converted with j1939_units.json from its exact (native)
value, so the metric and US databases are consistent; a temperature in the
metric database has a resolution of r*5/9 and an offset of -17.78 deg C.
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import struct
import sys

import j1939db_tools as j1939tools
from j1939db_tools import METRIC, US, PASS, WARN, FAIL, INFO, Check, UnitTable, canonical_unit

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
VECTORS_FILE = os.path.join(MODULE_DIR, "tests", "j1587db_vectors.json")
OUTPUT_NAMES = {METRIC: "J1587db.licensed.json", US: "J1587db.us.licensed.json"}
SKELETON_NAME = "J1587db.json"

# US unit spellings in J1587 -> the US labels of j1939_units.json.
US_UNITS = {
    "degf": "deg F", "f": "deg F", "mph": "mph", "lbf/in2": "psi", "psi": "psi", "lb/in2": "psi",
    "gal": "gallons", "gallons": "gallons", "gal/h": "gallons/h", "gal/s": "gallons/s", "lb": "lb", "lbf": "lbf",
    "mi": "miles", "miles": "miles", "mile": "miles", "ft": "ft", "in": "in", "mpg": "miles/gallon",
    "lb/h": "lb/h", "hp": "hp", "lbft": "lb-ft", "lbfft": "lb-ft", "ft/s": "ft/s", "inh2o": "inH2O",
    "ft/s2": "ft/s^2", "mph/s": "mph/s", "degf/s": "deg F/s", "psi/s": "psi/s",
}

DATA_TYPES = {
    "unsignedshortinteger": "Unsigned Short Integer", "unsignedinteger": "Unsigned Integer",
    "unsignedlonginteger": "Unsigned Long Integer", "signedshortinteger": "Signed Short Integer",
    "signedinteger": "Signed Integer", "signedlonginteger": "Signed Long Integer",
    "longinteger": "Signed Long Integer", "binarybitmapped": "Binary Bit-Mapped",
    "binary": "Binary Bit-Mapped", "alphanumeric": "Alphanumeric", "alpha": "Alphanumeric",
}
# struct format and size for the numeric data types (little-endian).
NUMERIC = {"Unsigned Short Integer": ("<B", 1), "Signed Short Integer": ("<b", 1),
           "Unsigned Integer": ("<H", 2), "Signed Integer": ("<h", 2),
           "Unsigned Long Integer": ("<L", 4), "Signed Long Integer": ("<l", 4)}

# Page furniture: running headers and the per-page license stamp (personal data).
FURNITURE = re.compile(r"^\s*(SAE\s+J1587\b.*Revised|SAE\s+J1708\b.*Revised|Licensed (to|from)\b|"
                       r"E-mailing, copying|Downloaded\s|Author:|-\s*\d+\s*-\s*$)", re.I)
# The same stamp can also be merged into a line of page text.
STAMP = re.compile(r"(Licensed to\b.*|Licensed from the SAE\b.*|E-mailing, copying\b.*|"
                   r"Downloaded\s+\w+day,.*|Author:\S*(-SID:|GUID:)\S*)", re.I)


# --------------------------------------------------------------------------
# PDF text
# --------------------------------------------------------------------------

def extract_lines(pdf_path):
    """Text lines of a J1587 PDF in layout mode, without page furniture.

    Layout mode keeps table columns apart (two or more spaces) and avoids the
    split words of the default extractor ("Cool ant").
    """
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    lines = []
    for page in reader.pages:
        text = page.extract_text(extraction_mode="layout") or ""
        for line in text.splitlines():
            if FURNITURE.match(line):
                continue
            line = STAMP.sub("", line).rstrip()
            if line.strip():
                lines.append(line)
    return lines


def _cells(line):
    return [c.strip() for c in re.split(r"\s{2,}", line.strip()) if c.strip()]


def _text(line):
    return re.sub(r"\s+", " ", line).strip()


def _fix(text):
    """Normalize dashes, superscripts and quotes from the PDF."""
    text = (text.replace("—", "-").replace("–", "-").replace("−", "-")
            .replace("’", "'").replace("“", '"').replace("”", '"'))
    return re.sub(r"\s+", " ", text).strip()


def _title(text):
    """'BRAKES, TRAILER (USED BY SAE J2497)' -> 'Brakes, Trailer (Used By SAE J2497)'; keeps acronyms with digits."""
    def word(w):
        if any(c.isdigit() for c in w) or w.strip("(),") in ("SAE", "ABS", "ECU", "PTO", "PLC", "ATC", "EGR"):
            return w
        i = next((k for k, c in enumerate(w) if c.isalpha()), len(w))
        return w[:i] + w[i:i + 1].upper() + w[i + 1:].lower()
    return " ".join(word(w) for w in text.split()) if text.isupper() else text


def _section(lines, start_pattern, end_pattern, skip_toc=True):
    """Lines after the first start match (outside the table of contents) up to end."""
    out, inside = [], False
    for line in lines:
        t = _text(line)
        if not inside:
            if re.search(start_pattern, t) and not (skip_toc and re.search(r"\.{5,}|\s\d+$", t) and "...." in t):
                inside = True
            continue
        if re.search(end_pattern, t):
            break
        out.append(line)
    return out


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------

def parse_mids(lines):
    """Table 1 (MID assignment list): {mid: name} from the Basic Heavy Duty column."""
    rows = _section(lines, r"^TABLE 1 - MESSAGE ID ASSIGNMENT LIST", r"^3\.3 Parameter Identification|^TABLE 2 -")
    mids, last = {}, None
    for line in rows:
        t = _text(line)
        if t.startswith(("TABLE 1", "MID #")) or "...." in t:
            continue
        if t.startswith("NOTE"):
            break
        cells = _cells(line)
        m = re.match(r"^(\d+)(?:\s+(.*))?$", cells[0])
        if m and int(m.group(1)) <= 255:
            # The MID is its own cell ("175", "Engine #2", ...) or shares one ("157 (Reclaimed)", ...).
            name = m.group(2) if m.group(2) else (cells[1] if len(cells) > 1 else "")
            mids[int(m.group(1))] = _fix(name)
            last = int(m.group(1))
        elif last is not None and not re.match(r"^\d+-\d+", t):
            # Second line of a multi-line Basic Heavy Duty cell.
            mids[last] = _fix(mids[last] + " " + cells[0])
    for num, name in mids.items():
        # A reclaimed MID that was reassigned: "(Reclaimed) Park Brake Controller".
        mids[num] = re.sub(r"^\(Reclaimed\)\s+(?=\S)", "", name)
    return {str(k): v for k, v in sorted(mids.items())}


def parse_pid_names(lines):
    """Table 2 (PID assignment list): {pid: name}."""
    rows = _section(lines, r"^TABLE 2 - PARAMETER IDENTIFICATION ASSIGNMENT LIST", r"^3\.4 Parameter Data Types|^TABLE 3 -")
    names = {}
    for line in rows:
        t = _fix(_text(line))
        m = re.match(r"^(\d+)((?:\(\d+\))*)\s+(?:\(\d+\)\s+)?(.+)$", t)
        if m and not t.startswith("TABLE"):
            pid = int(m.group(1))
            if pid <= 1023 and not re.match(r"^\d", m.group(3)):
                names[pid] = m.group(3).strip()
    return {str(k): v for k, v in sorted(names.items())}


def parse_fmis(lines):
    """Table 6 (failure mode identifiers): {fmi: text}."""
    rows = _section(lines, r"^TABLE 6 - FAILURE MODE IDENTIFIERS", r"^3\.10 |^TABLE 7 -|SAE Procedure for MID")
    fmis, last = {}, None
    for line in rows:
        t = _fix(_text(line))
        m = re.match(r"^(\d{1,2})\s+(.+)$", t)
        if m and int(m.group(1)) <= 15 and int(m.group(1)) not in fmis:
            last = int(m.group(1))
            fmis[last] = m.group(2)
        elif last is not None and t.startswith("("):
            fmis[last] += " " + t
    return {str(k): v for k, v in sorted(fmis.items())}


def parse_sids(lines):
    """Table 7 (SID assignment list): {"-1": common SIDs, "<mid>": SIDs for that MID}.

    SIDs 151-255 are common to all MIDs and are also copied into each MID group,
    so a lookup by MID alone finds them. Page-2 SIDs ("281 (25)") use 256+.
    """
    rows = _section(lines, r"^TABLE 7 - SUBSYSTEM IDENTIFICATION \(SID\) ASSIGNMENT LIST", r"^4\. NOTES|^4\.1 Marginal")
    groups = {"-1": {}}
    current = ["-1"]
    last = None
    for line in rows:
        t = _fix(_text(line))
        if t.startswith("TABLE 7") or not t:
            continue
        g = re.search(r"SIDs.*\(MIDs?\s*=\s*([\d,\s]+)\)", t)
        if g:
            current = [m.strip() for m in g.group(1).split(",") if m.strip()]
            for mid in current:
                groups.setdefault(mid, {})
            last = None
            continue
        if re.match(r"^Common SIDs$", t):
            current, last = ["-1"], None
            continue
        m = re.match(r"^(\d+)(?:\s*-\s*(\d+))?(?:\s*\((\d+)\))?\s+(.+)$", t)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
            name = m.group(4).strip()
            if hi - lo > 64 or name.lower().startswith(("sids ", "sid ")):
                continue           # "156-202 Reserved for future assignment"
            target = ["-1"] if lo >= 151 and lo <= 255 else current
            for sid in range(lo, hi + 1):
                for g_key in target:
                    groups.setdefault(g_key, {})[str(sid)] = name
            last = (target, lo)
        elif last is not None and re.match(r"^\(", t):
            target, sid = last
            for g_key in target:
                groups[g_key][str(sid)] += " " + t
    common = groups["-1"]
    for key, sids in groups.items():
        if key != "-1":
            for sid, name in common.items():
                sids.setdefault(sid, name)
    return groups


# --------------------------------------------------------------------------
# Parameter definitions (Appendix A)
# --------------------------------------------------------------------------

_NUM = r"[-+]?\d[\d,]*(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?(?:\s*[x×]\s*10\s*[-]\s*\d+)?"


def parse_number(text):
    """'0.805' -> 0.805, '1/512' -> 0.001953125, '16.428 x 10-6' -> 1.6428e-05."""
    t = text.replace(",", "").replace("–", "-").replace("−", "-").strip()
    m = re.match(r"^([-+]?\d+(?:\.\d+)?)(?:\s*/\s*(\d+(?:\.\d+)?))?(?:\s*[x×]\s*10\s*-\s*(\d+))?$", t)
    if not m:
        return None
    value = float(m.group(1))
    if m.group(2):
        value /= float(m.group(2))
    if m.group(3):
        value *= 10 ** -int(m.group(3))
    return value


def parse_quantity(text):
    """'0.805 km/h (0.5 mph)' -> [(0.805, 'km/h'), (0.5, 'mph')]; units without '/bit'."""
    out = []
    t = _fix(text)
    for part in re.split(r"\(|\bor\b", t):
        part = part.strip(" )")
        m = re.match(rf"^({_NUM})\s*(.*)$", part)
        if not m:
            continue
        value = parse_number(m.group(1))
        unit = re.sub(r"\s*(?:per bit|/\s*bit)$", "", m.group(2).strip(" .,;"), flags=re.I).strip()
        if value is not None:
            out.append((value, unit))
    return out


def parse_range(text):
    """'0.0 to 205.2 km/h (0.0 to 127.5 mph)' -> [(0.0, 205.2, 'km/h'), (0.0, 127.5, 'mph')]."""
    out = []
    for m in re.finditer(rf"({_NUM})\s+to\s+\+?({_NUM})\s*([^()]*)", _fix(text)):
        lo, hi = parse_number(m.group(1)), parse_number(m.group(2))
        if lo is not None and hi is not None:
            out.append((lo, hi, m.group(3).strip(" .,;")))
    return out


def _field(block, *names):
    for name in names:
        m = re.search(rf"^{name}:\s*(.*)$", block, re.M | re.I)
        if m:
            return m.group(1).strip()
    return None


def _data_type(text):
    if not text:
        return None
    key = re.sub(r"[^a-z]", "", text.lower())
    return DATA_TYPES.get(key, _fix(text))


def _data_length(text):
    if not text:
        return None
    m = re.match(r"^(\d+)\s*Characters?", text, re.I)
    if m:
        return int(m.group(1))
    if re.match(r"^No data", text, re.I):
        return 0
    return "Variable" if text.lower().startswith("variable") else _fix(text)


def parse_parameters(lines, names=None):
    """Appendix A parameter definitions: {pid: dict of the native (as written) fields}."""
    body = []
    started = False
    for line in lines:
        t = _text(line)
        if not started:
            if re.match(r"^APPENDIX A - PARAMETER DEFINITIONS$", t):
                started = True
            continue
        if re.match(r"^APPENDIX B\b", t):
            break
        body.append(_fix(t))
    text = "\n".join(body)
    params = {}
    for block in re.split(r"\n(?=A\.\d+ [A-Z])", "\n" + text):
        m = re.match(r"^A\.(\d+) (.+)", block)
        if not m:
            continue
        pid = int(m.group(1))
        heading = m.group(2).strip()
        lines_ = block.splitlines()
        desc = []
        for l in lines_[1:]:
            if re.match(r"^(Parameter Data Length|Data Type|NOTE|Format):", l):
                break
            desc.append(l)
        fmt = re.search(r"^PID Data\s*$\n^(\d+)\s+(.+)$", block, re.M)
        entry = {
            "Name": (names or {}).get(str(pid)) or _title(heading),
            "Description": _fix(" ".join(desc))[:400],
            "DataLength": _data_length(_field(block, "Parameter Data Length")),
            "DataType": _data_type(_field(block, "Data Type")),
            "ResolutionText": _field(block, "Bit Resolution", "Resolution"),
            "RangeText": _field(block, "Maximum Range", "Maximum range", "Valid Range", "Operating Range"),
            "Period": _field(block, "Transmission Update Period", "Transmission Rate"),
            "Priority": None,
            "DataForm": fmt.group(2).strip() if fmt and int(fmt.group(1)) % 256 == pid % 256 else "",
        }
        prio = _field(block, "Message Priority")
        if prio and re.match(r"^\d+", prio):
            entry["Priority"] = int(re.match(r"^\d+", prio).group(0))
        params[pid] = entry
    return params


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

def _metric_conversion(unit, table):
    """(conversion entry, 'metric'|'us') for a unit, or (None, None).

    For a US unit shared by several metric units (psi: kPa, MPa, bar) the entry
    is only one of them; compare US labels with _same_us().
    """
    conv = table.lookup(unit)
    if conv:
        return conv, METRIC
    us_label = US_UNITS.get(canonical_unit(unit))
    if us_label:
        key = table.us_labels.get(canonical_unit(us_label))
        if key:
            return table.conversions[key], US
    return None, None


def _same_us(unit, conv, table):
    """True if unit is the US unit of conversion conv."""
    c, system = _metric_conversion(unit, table)
    return system == US and canonical_unit(c["us"]) == canonical_unit(conv["us"])


def scale_parameter(entry, table):
    """Resolution, offset, limits and unit of one parameter in both unit systems.

    Returns {"metric": {...}, "us": {...}, "native": "metric"|"us"|None, "note": str}.
    The native value is the exact one: the US value when the text gives both and
    they agree within 1%, otherwise the first value.
    """
    quantities = parse_quantity(entry.get("ResolutionText") or "")
    ranges = parse_range(entry.get("RangeText") or "")
    plain = {"BitResolution": None, "Offset": 0.0, "Unit": "", "Minimum": None, "Maximum": None}
    result = {METRIC: dict(plain), US: dict(plain), "native": None, "note": ""}
    if not quantities:
        if re.match(r"^binary\b", entry.get("ResolutionText") or "", re.I) and entry.get("DataType") in NUMERIC:
            for s in (METRIC, US):
                result[s]["BitResolution"] = 1          # a raw number (e.g. the PID being requested)
        return result
    value, unit = quantities[0]
    conv, system = _metric_conversion(unit, table)
    if conv is None:
        # Unit-less, or a unit both systems share (rpm, V, %, h...).
        label = table.metric_label(unit) if unit else ""
        for s in (METRIC, US):
            result[s].update(BitResolution=value, Unit=label)
            if ranges:
                result[s].update(Minimum=ranges[0][0], Maximum=ranges[0][1])
        return result
    scale, off = conv["scale"], conv["offset"]
    us_value = value * scale if system == METRIC else value
    us_range = None
    if system == METRIC and len(quantities) > 1:
        v2, u2 = quantities[1]
        if _same_us(u2, conv, table) and abs(v2 - value * scale) <= 0.01 * abs(v2):
            us_value = v2                       # the exact US resolution
        elif _same_us(u2, conv, table):
            result["note"] = f"metric {value} {unit} and US {v2} {u2} differ by more than 1%; used the metric value"
    for lo, hi, u in ranges:
        if _same_us(u, conv, table):
            us_range = (lo, hi)
        elif table.lookup(u) is conv and us_range is None:
            us_range = (lo * scale + off, hi * scale + off)
    if us_range is None and ranges and not ranges[0][2]:
        us_range = (ranges[0][0], ranges[0][1]) if system == US else (ranges[0][0] * scale + off, ranges[0][1] * scale + off)
    result["native"] = system
    result[US].update(BitResolution=_clean(us_value), Offset=0.0 if system == US else _clean(off), Unit=conv["us"])
    result[METRIC].update(BitResolution=_clean(us_value / scale),
                          Offset=_clean(-off / scale) if system == US else 0.0, Unit=conv["metric"])
    if us_range:
        result[US].update(Minimum=_clean(us_range[0]), Maximum=_clean(us_range[1]))
        result[METRIC].update(Minimum=_clean((us_range[0] - off) / scale), Maximum=_clean((us_range[1] - off) / scale))
    return result


def _clean(x, digits=10):
    if x is None:
        return None
    y = round(float(x), digits)
    return int(y) if y == int(y) and abs(y) < 1e15 else y


def _format_str(resolution):
    """Decimals that show one resolution step: 0.25 -> %0.2f, a converted 0.5556 deg C -> %0.2f."""
    if not resolution:
        return "%s"
    decimals = 0
    r = abs(float(resolution))
    while decimals < 6 and abs(r * 10 ** decimals - round(r * 10 ** decimals)) > 1e-9:
        decimals += 1
    if decimals == 6:            # not a short decimal (a converted resolution): one digit below the step
        decimals = min(6, max(0, math.ceil(-math.log10(r))) + 1)
    return f"%0.{decimals}f"


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(lines, table, mid_lines=None):
    """Metric and US databases (plus a report) from the extracted PDF lines."""
    mids = parse_mids(lines)
    if mid_lines:
        for k, v in parse_j1708_mids(mid_lines).items():
            mids.setdefault(k, v)
    names = parse_pid_names(lines)
    params = parse_parameters(lines, names)
    fmis = parse_fmis(lines)
    sids = parse_sids(lines)
    dbs = {}
    report = {"converted": {}, "notes": [], "unparsed_resolution": []}
    for system in (METRIC, US):
        dbs[system] = {"MID": dict(sorted(mids.items(), key=lambda kv: int(kv[0]))), "MIDAlias": {},
                       "PID": {}, "PIDNames": dict(names), "SID": sids, "FMI": fmis}
    for pid, entry in sorted(params.items()):
        scaled = scale_parameter(entry, table)
        if scaled["note"]:
            report["notes"].append(f"PID {pid}: {scaled['note']}")
        if entry.get("DataType") in NUMERIC and scaled[METRIC]["BitResolution"] is None:
            report["unparsed_resolution"].append(pid)
        if scaled["native"]:
            key = f"{scaled[METRIC]['Unit']} <-> {scaled[US]['Unit']}"
            report["converted"][key] = report["converted"].get(key, 0) + 1
        for system in (METRIC, US):
            out = {k: entry[k] for k in ("Name", "Description", "DataLength", "DataType", "DataForm",
                                         "Period", "Priority", "ResolutionText", "RangeText")}
            out.update(scaled[system])
            out["FormatStr"] = _format_str(out["BitResolution"])
            if scaled["native"] == US and system == METRIC or scaled["native"] == METRIC and system == US:
                out["Converted"] = True
            dbs[system]["PID"][str(pid)] = out
            dbs[system]["PIDNames"].setdefault(str(pid), entry["Name"])
    return dbs, report


def parse_j1708_mids(lines):
    """MIDs 0-127 from SAE J1708 Table 3 (message identification character allocation).

    Rows are ranges by transmitter category ("00-07 ENGINE"); each MID gets the
    category name, e.g. MID 3 -> "Engine (J1708 MIDs 0-7)".
    """
    rows = _section(lines, r"^TABLE 3 - MESSAGE IDENTIFICATION CHARACTER ALLOCATION", r"^6\.3\.3\.2|^TABLE 4 -")
    mids = {}
    for line in rows:
        t = _fix(_text(line))
        m = re.match(r"^(\d{1,3})(?:-(\d{1,3}))?\s+(.+)$", t)
        if not m:
            continue
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if lo > 127:
            continue
        category = _title(m.group(3).strip())
        label = category if lo == hi else f"{category} (J1708 MIDs {lo}-{hi})"
        for mid in range(lo, min(hi, 127) + 1):
            mids[str(mid)] = label
    return mids


def generate(pdf_paths, out_dir, systems=(METRIC, US), units_path=j1939tools.UNITS_FILE, log=print):
    """Build licensed J1587 databases from the SAE J1587 PDF (and optionally the
    SAE J1708 PDF for MIDs 0-127). Returns {system: path}."""
    table = UnitTable(units_path)
    j1587, j1708 = [], []
    for path in pdf_paths:
        log(f"Reading {os.path.basename(path)}...")
        lines = extract_lines(path)
        head = " ".join(_text(l) for l in lines[:80])
        (j1708 if re.search(r"\bJ1708\b", head) and not re.search(r"\bJ1587\b", head) else j1587).append(lines)
    if not j1587:
        raise ValueError("No SAE J1587 document found among the selected files.")
    lines = [l for doc in j1587 for l in doc]
    dbs, report = build(lines, table, [l for doc in j1708 for l in doc] or None)
    if len(dbs[METRIC]["PID"]) < 50:
        raise ValueError("Too few parameter definitions found. Is this the SAE J1587 document (Appendix A)?")
    revision = re.search(r"J1587\s+([A-Z]{3}\d{4})", " ".join(_text(l) for l in lines[:120]))
    meta = {
        "skeleton": False,
        "generated_by": "CSU-RP1210 j1587db_tools",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "standard": f"SAE J1587 {revision.group(1)}" if revision else "SAE J1587",
        "sources": [{"file": os.path.basename(p), "sha256": _sha256(p)} for p in pdf_paths],
        "unit_conversions": report["converted"],
        "notes": report["notes"],
        "unparsed_resolution": report["unparsed_resolution"],
        "license_notice": "Derived from SAE J1587 under the user's license. Do not redistribute or commit.",
    }
    os.makedirs(out_dir, exist_ok=True)
    outputs = {}
    for system in systems:
        db = dict(dbs[system], _meta=dict(meta, units=system))
        path = os.path.join(out_dir, OUTPUT_NAMES[system])
        with open(path, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=1)
        outputs[system] = path
        log(f"Wrote {path}: {len(db['MID'])} MIDs, {len(db['PID'])} PIDs, "
            f"{sum(len(v) for v in db['SID'].values())} SIDs, {len(db['FMI'])} FMIs")
    return outputs


def database_candidates(directory, units):
    first, second = (OUTPUT_NAMES[US], OUTPUT_NAMES[METRIC]) if units == US else (OUTPUT_NAMES[METRIC], OUTPUT_NAMES[US])
    return [os.path.join(directory, n) for n in (first, second, SKELETON_NAME)]


# --------------------------------------------------------------------------
# Decoding (reference implementation used by the J1587 tab and the vectors)
# --------------------------------------------------------------------------

def split_pids(message):
    """Split a J1587 message (MID + parameters, no checksum) into (pid, data) pairs.

    PIDs 0-127 carry one byte, 128-191 two bytes and 192-253 a count byte plus
    that many bytes; 255 escapes to page 2 (PID + 256). Returns (mid, pairs).
    """
    if not message:
        return None, []
    mid, pairs, i = message[0], [], 1
    while i < len(message):
        pid = message[i]
        i += 1
        if pid == 255 and i < len(message):
            pid = 256 + message[i]
            i += 1
        low = pid % 256
        if low < 128:
            n = 1
        elif low < 192:
            n = 2
        elif i < len(message):
            n = message[i] + 1
        else:
            break
        if i + n > len(message):
            break
        pairs.append((pid, bytes(message[i:i + n])))
        i += n
    return mid, pairs


def decode_pid(db, pid, data):
    """Decode one parameter. Returns dict(value, raw, units, text, status, format) or None if undefined."""
    entry = db.get("PID", {}).get(str(pid))
    if entry is None:
        return None
    data = bytes(data)
    units = entry.get("Unit", "")
    result = {"value": None, "raw": None, "units": units, "text": None, "status": "Missing",
              "format": entry.get("FormatStr", "%s")}
    payload = data
    if pid % 256 >= 192 and data:
        payload = data[1:1 + data[0]]            # count byte
    dtype = entry.get("DataType")
    if dtype == "Alphanumeric":
        result["text"] = payload.split(b"\x00")[0].decode("latin-1").strip()
        result["status"] = "Valid"
        return result
    if dtype in NUMERIC:
        fmt, size = NUMERIC[dtype]
        length = entry.get("DataLength")
        if isinstance(length, int) and length != size and len(payload) == length:
            return result                         # multi-field parameter: leave to the caller
        if len(payload) < size:
            return result
        raw = struct.unpack(fmt, payload[:size])[0]
        res = entry.get("BitResolution")
        result["raw"] = raw
        if isinstance(res, (int, float)) and res:
            result["value"] = _clean(raw * res + (entry.get("Offset") or 0), 9)
            result["status"] = "Valid"
        return result
    if payload:
        result["raw"] = int.from_bytes(payload, "little") if len(payload) <= 8 else None
        result["text"] = payload.hex(" ").upper()
        result["status"] = "Valid"
    return result


def format_value(result):
    if result is None:
        return ""
    if result["value"] is not None:
        try:
            return result["format"] % result["value"]
        except (TypeError, ValueError):
            return str(result["value"])
    return result["text"] or ""


# --------------------------------------------------------------------------
# Validation and vectors
# --------------------------------------------------------------------------

def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate(db):
    checks = []
    for key in ("MID", "PID", "PIDNames", "SID", "FMI"):
        if key not in db:
            checks.append(Check(f"section {key}", FAIL, "missing"))
    if any(c.level == FAIL for c in checks):
        return checks
    skeleton = db.get("_meta", {}).get("skeleton")
    level = INFO if skeleton else WARN
    pids = db["PID"]
    checks.append(Check("counts", PASS if len(pids) >= 300 or skeleton else level,
                        f"{len(db['MID'])} MIDs, {len(pids)} PIDs, {sum(len(v) for v in db['SID'].values())} SIDs, {len(db['FMI'])} FMIs"))
    bad = [p for p, e in pids.items() if e.get("DataType") in NUMERIC and not isinstance(e.get("BitResolution"), (int, float))]
    checks.append(Check("numeric resolution", PASS if not bad else WARN,
                        f"{len(bad)} numeric PIDs without a resolution", bad[:12]))
    size_mismatch = [p for p, e in pids.items()
                     if e.get("DataType") in NUMERIC and isinstance(e.get("DataLength"), int)
                     and int(p) % 256 < 192 and e["DataLength"] != (1 if int(p) % 256 < 128 else 2)]
    checks.append(Check("data length vs PID range", PASS if not size_mismatch else WARN,
                        f"{len(size_mismatch)} fixed-length PIDs whose length disagrees with the PID range", size_mismatch[:12]))
    units = {e.get("Unit") for e in pids.values()}
    checks.append(Check("units", INFO, f"{len(units)} distinct units: " + ", ".join(sorted(u for u in units if u)[:30])))
    fmis = [str(i) for i in range(16) if str(i) not in db["FMI"]]
    checks.append(Check("FMI 0-15", PASS if not fmis or skeleton else WARN, "all present" if not fmis else f"missing {fmis}"))
    return checks


def load_vectors(path=VECTORS_FILE):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["vectors"]


def save_vectors(vectors, path=VECTORS_FILE, doc=None):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"_doc": doc or [], "vectors": vectors}, f, indent=1)


def run_vector(db, vector, system=None):
    """(ok, message) for one vector.

    A vector is {name, mid, pid, data (hex, the PID's data bytes), expected:
    {metric: value, us: value}, tolerance} or {..., expected_text: "..."}, as in
    tests/j1939db_vectors.json: payloads and results only, no scaling.
    """
    system = system or db.get("_meta", {}).get("units", METRIC)
    result = decode_pid(db, vector["pid"], bytes.fromhex(vector["data"]))
    if result is None:
        return False, "PID not in database"
    if "expected_text" in vector:
        return result["text"] == vector["expected_text"], f"text {result['text']!r} expected {vector['expected_text']!r}"
    expected = vector.get("expected", {}).get(system)
    if expected is None:
        return True, "no expectation for this unit system"
    tolerance = vector.get("tolerance", 1e-6)
    ok = result["value"] is not None and abs(result["value"] - expected) <= tolerance
    return ok, f"{result['value']} {result['units']} expected {expected}"


def run_vectors(db, vectors):
    return [(v, *run_vector(db, v)) for v in vectors]


def main(argv=None):
    p = argparse.ArgumentParser(description="Build and check CSU-RP1210 J1587 databases.")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="SAE J1587 PDF (and optional J1708 PDF) -> J1587db.licensed.json / .us")
    g.add_argument("pdf", nargs="+")
    g.add_argument("--out", default=".")
    v = sub.add_parser("validate", help="check a J1587 database and run test vectors")
    v.add_argument("db")
    v.add_argument("--vectors", default=VECTORS_FILE)
    args = p.parse_args(argv)
    if args.cmd == "generate":
        generate(args.pdf, args.out)
        return 0
    db = load(args.db)
    failed = 0
    for c in validate(db):
        print(repr(c))
        failed += c.level == FAIL
    if os.path.exists(args.vectors):
        results = run_vectors(db, load_vectors(args.vectors))
        bad = [(v, m) for v, ok, m in results if not ok]
        print(f"vectors: {len(results) - len(bad)}/{len(results)} pass")
        for v, m in bad[:20]:
            print(f"  FAIL MID {v['mid']} PID {v['pid']} {v['data']}: {m}")
        failed += bool(bad)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
