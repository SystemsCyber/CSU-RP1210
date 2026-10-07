"""
Export a CSU-RP1210 J1939 database (J1939db*.json) as a CAN database (.dbc) for
tools such as SavvyCAN, cantools, CANalyzer and python-can.

Replaces the J1939Converters utilities (J1939toDBC.py / J1939toJSON.py), which
re-read the Digital Annex spreadsheet by sheet number. This exporter starts from
the database the application already uses, so the DBC has the same scaling
(including the Digital Annex value-only corrections) and the same units as the
J1939 tab: export the metric or the US customary database.

    python j1939_dbc.py J1939db.licensed.json [--out J1939.dbc] [--sa 254] [--pgn 61444 65262 ...]

Each PGN becomes a message with the identifier priority | PGN | source address
(PDU1 PGNs keep destination 0, as in Vector's J1939 databases) and the extended
frame flag (bit 31). Each SPN with a fixed start bit and length becomes a signal
(Intel byte order, unsigned) with its resolution, offset, range and unit, an
"SPN" attribute and, where the database has them, value tables from the state
decodings. SPNs that are split across non-adjacent bits, variable length or
text are listed in the message comment instead.
"""

import argparse
import math
import re
import sys

import j1939db_tools as tools

DEFAULT_PRIORITY = 6
MAX_NAME = 32

NS = ["NS_DESC_", "CM_", "BA_DEF_", "BA_", "VAL_", "CAT_DEF_", "CAT_", "FILTER", "BA_DEF_DEF_", "EV_DATA_",
      "ENVVAR_DATA_", "SGTYPE_", "SGTYPE_VAL_", "BA_DEF_SGTYPE_", "BA_SGTYPE_", "SIG_TYPE_REF_", "VAL_TABLE_",
      "SIG_GROUP_", "SIG_VALTYPE_", "SIGTYPE_VALTYPE_", "BO_TX_BU_", "BA_DEF_REL_", "BA_REL_", "BA_DEF_DEF_REL_",
      "BU_SG_REL_", "BU_EV_REL_", "BU_BO_REL_", "SG_MUL_VAL_"]

ATTRIBUTES = [
    ('BA_DEF_ "ProtocolType" STRING ;', 'BA_DEF_DEF_ "ProtocolType" "";'),
    ('BA_DEF_ "BusType" STRING ;', 'BA_DEF_DEF_ "BusType" "";'),
    ('BA_DEF_ "DatabaseVersion" STRING ;', 'BA_DEF_DEF_ "DatabaseVersion" "";'),
    ('BA_DEF_ BO_ "VFrameFormat" ENUM "StandardCAN","ExtendedCAN","reserved","J1939PG";', 'BA_DEF_DEF_ "VFrameFormat" "J1939PG";'),
    ('BA_DEF_ BO_ "GenMsgCycleTime" INT 0 3600000;', 'BA_DEF_DEF_ "GenMsgCycleTime" 0;'),
    ('BA_DEF_ BO_ "PGN" INT 0 262143;', 'BA_DEF_DEF_ "PGN" 0;'),
    ('BA_DEF_ SG_ "SPN" INT 0 524287;', 'BA_DEF_DEF_ "SPN" 0;'),
]


def identifier(name, limit=MAX_NAME):
    """A DBC identifier from a label: 'Engine Speed' -> 'EngineSpeed'."""
    words = re.findall(r"[A-Za-z0-9]+", str(name or ""))
    text = "".join(w[:1].upper() + w[1:] for w in words) or "Unnamed"
    if text[0].isdigit():
        text = "_" + text
    return text[:limit]


def quote(text):
    return str(text or "").replace("\\", "/").replace('"', "'").replace("\r", " ").replace("\n", " ").strip()


def number(x):
    """Shortest exact text for a float (0.125 -> '0.125', 1e-05 -> '1E-05')."""
    if x is None:
        return "0"
    x = float(x)
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return repr(x).replace("e", "E")


def cycle_time_ms(rate):
    """'100 ms' -> 100, 'Every 5 s' -> 5000; None when on request or variable."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ms|s)\b", str(rate or ""))
    if not m:
        return None
    value = float(m.group(1)) * (1 if m.group(2) == "ms" else 1000)
    return int(value)


def can_id(pgn, priority=DEFAULT_PRIORITY, sa=0xFE):
    return 0x80000000 | ((priority & 7) << 26) | ((pgn & 0x3FFFF) << 8) | (sa & 0xFF)


def build(db, sa=0xFE, pgns=None, include_proprietary=True):
    """Return (dbc_text, stats)."""
    spn_db = db.get("J1939SPNdb", {})
    decodings = db.get("J1939BitDecodings", {})
    units_system = db.get("_meta", {}).get("units", tools.unit_system(db))
    messages, comments_out, attributes, values_out = [], [], [], []
    used_names = set()
    stats = {"messages": 0, "signals": 0, "skipped_spns": 0, "value_tables": 0}
    for pgn_key in sorted(db.get("J1939PGNdb", {}), key=int):
        pgn = int(pgn_key)
        if pgns and pgn not in pgns:
            continue
        entry = db["J1939PGNdb"][pgn_key]
        if not entry.get("SPNs"):
            continue
        signals, skipped, sig_names, end_bit = [], [], set(), 64
        comments, values = [], []
        for index, spn in enumerate(entry["SPNs"]):
            spn_entry = spn_db.get(str(spn))
            if spn_entry is None:
                continue
            length = spn_entry.get("SPNLength")
            start = tools.spn_start_in_pgn(db, pgn_key, index)
            if start is None or not isinstance(length, int) or length < 1 or length > 64 \
                    or tools.canonical_unit(spn_entry.get("Units")) == "ascii":
                skipped.append(spn)
                continue
            name = identifier(spn_entry.get("Name"))
            if name in sig_names:
                name = identifier(spn_entry.get("Name"), MAX_NAME - len(str(spn)) - 1) + f"_{spn}"
            sig_names.add(name)
            res = tools._num(spn_entry.get("Resolution")) or 1.0
            off = tools._num(spn_entry.get("Offset")) or 0.0
            low = tools._num(spn_entry.get("OperationalLow"))
            high = tools._num(spn_entry.get("OperationalHigh"))
            if low is None or high is None or low > high:
                low, high = off, off + res * ((1 << length) - 1)
            unit = quote(spn_entry.get("Units", ""))
            if tools.canonical_unit(unit) in ("bit", "binary", "states"):
                unit = ""
            signals.append(f' SG_ {name} : {start}|{length}@1+ ({number(res)},{number(off)}) '
                           f'[{number(low)}|{number(high)}] "{unit}" Vector__XXX')
            end_bit = max(end_bit, start + length)
            comments.append((name, spn, spn_entry.get("Name")))
            table = decodings.get(str(spn))
            if table:
                pairs = []
                for k, v in table.items():
                    try:
                        pairs.append((int(k), quote(v)[:120]))
                    except ValueError:
                        continue
                if pairs:
                    values.append((name, " ".join(f'{k} "{v}"' for k, v in sorted(pairs, reverse=True))))
        if not signals:
            stats["skipped_spns"] += len(skipped)
            continue
        priority = entry.get("DefaultPriority")
        priority = int(priority) if isinstance(priority, (int, float)) or str(priority).isdigit() else DEFAULT_PRIORITY
        mid = can_id(pgn, priority, sa)
        msg_name = identifier(entry.get("Label") or entry.get("Name"))
        if msg_name in used_names:
            msg_name = identifier(entry.get("Label") or entry.get("Name"), MAX_NAME - len(pgn_key) - 1) + f"_{pgn}"
        used_names.add(msg_name)
        dlc = max(8, math.ceil(end_bit / 8))
        messages.append(f"BO_ {mid} {msg_name}: {dlc} Vector__XXX\n" + "\n".join(signals) + "\n")
        note = quote(entry.get("Name"))
        if skipped:
            note += f" (not exported: SPN {', '.join(str(s) for s in skipped)})"
        comments_out.append(f'CM_ BO_ {mid} "{note}";')
        attributes.append(f'BA_ "PGN" BO_ {mid} {pgn};')
        attributes.append(f'BA_ "VFrameFormat" BO_ {mid} 3;')
        cycle = cycle_time_ms(entry.get("Rate"))
        if cycle:
            attributes.append(f'BA_ "GenMsgCycleTime" BO_ {mid} {cycle};')
        for name, spn, label in comments:
            comments_out.append(f'CM_ SG_ {mid} {name} "{quote(label)}";')
            attributes.append(f'BA_ "SPN" SG_ {mid} {name} {spn};')
        values_out += [f"VAL_ {mid} {name} {text} ;" for name, text in values]
        stats["value_tables"] += len(values)
        stats["messages"] += 1
        stats["signals"] += len(signals)
        stats["skipped_spns"] += len(skipped)
    out = ['VERSION ""', "", "", "NS_ :"] + [f"\t{n}" for n in NS] + ["", "BS_:", "", "BU_:", ""]
    out += messages
    out += comments_out
    out += [d for d, _ in ATTRIBUTES] + [d for _, d in ATTRIBUTES]
    out += ['BA_ "ProtocolType" "J1939";', 'BA_ "BusType" "CAN";',
            f'BA_ "DatabaseVersion" "CSU-RP1210 {quote(units_system)}";']
    out += attributes
    out += values_out
    return "\n".join(out) + "\n", stats


def export(db_path, out_path, sa=0xFE, pgns=None, log=print):
    db = tools.load(db_path)
    text, stats = build(db, sa=sa, pgns=pgns)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    log(f"Wrote {out_path}: {stats['messages']} messages, {stats['signals']} signals, "
        f"{stats['value_tables']} value tables ({stats['skipped_spns']} SPNs not representable)")
    return stats


def main(argv=None):
    p = argparse.ArgumentParser(description="Export a CSU-RP1210 J1939 database as a .dbc file.")
    p.add_argument("db", help="J1939db.licensed.json or J1939db.us.licensed.json")
    p.add_argument("--out", help="output .dbc (default: next to the database)")
    p.add_argument("--sa", type=lambda s: int(s, 0), default=0xFE, help="source address in identifiers (default 254)")
    p.add_argument("--pgn", type=int, nargs="*", help="only these PGNs")
    args = p.parse_args(argv)
    out = args.out or re.sub(r"\.json$", "", args.db) + ".dbc"
    export(args.db, out, sa=args.sa, pgns=set(args.pgn) if args.pgn else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
