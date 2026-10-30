"""
Multiplexed J1939 parameter groups: PGNs whose first byte(s) select which message
the payload is (TP.CM control byte, ISO 11783 VT function code, ISO 15765 frame
type and UDS service, proprietary message IDs, the PGN a Request asks for).

The J1939 tab's "Expand Multiplexed PGNs" option uses selector() to give each
selector value its own row. The definitions are in j1939_mux.json (editable).
"""

import json
import os

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFINITIONS_FILE = os.path.join(MODULE_DIR, "j1939_mux.json")

ISOTP_FRAMES = {0: "SF", 1: "FF", 2: "CF", 3: "FC"}


class Multiplexers:
    def __init__(self, path=DEFINITIONS_FILE):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.pgns = {int(k): v for k, v in data.get("pgns", {}).items()}
        self.ranges = data.get("ranges", [])
        self.tables = data.get("tables", {})
        self.max_rows = int(data.get("max_rows_per_pgn", 64))

    def definition(self, pgn):
        """The multiplexer definition of a PGN, or None for a normal parameter group."""
        if pgn in self.pgns:
            return self.pgns[pgn]
        for r in self.ranges:
            if r["first"] <= pgn <= r["last"]:
                return r
        return None

    def labels(self, definition):
        if "labels" in definition:
            return definition["labels"]
        return self.tables.get(definition.get("labels_ref"), {})

    def selector(self, pgn, data, db=None):
        """(key, text) identifying the multiplexed message, or None.

        key is a short string for the table row key; text is shown in the
        Multiplexer column, e.g. ("16", "0x10 RTS") or ("SF34", "SF 0x22 ReadDataByIdentifier").
        """
        d = self.definition(pgn)
        if d is None or not data:
            return None
        kind = d.get("selector", "byte")
        if kind == "pgn":
            if len(data) < 3:
                return None
            requested = data[0] | data[1] << 8 | data[2] << 16
            label = ((db or {}).get("J1939PGNdb", {}).get(str(requested)) or {}).get("Label", "")
            return str(requested), f"PGN {requested} {label}".strip()
        if kind == "isotp":
            frame = data[0] >> 4
            name = ISOTP_FRAMES.get(frame)
            if name is None:
                return f"N{frame}", f"N_PCI {frame}"
            sid_index = {0: 1, 1: 2}.get(frame)
            if sid_index is None or len(data) <= sid_index:
                return name, name
            sid = data[sid_index]
            services = self.tables.get("uds_services", {})
            # Positive responses are the request SID + 0x40 (0x62 answers 0x22); 0x7F is the negative response.
            base = sid - 0x40 if 0x50 <= sid <= 0x7E or 0xC3 <= sid <= 0xC8 else sid
            service = services.get(str(base), "")
            if service and base != sid:
                service += " response"
            return f"{name}{sid}", f"{name} 0x{sid:02X} {service}".strip()
        index = int(d.get("byte", 0))
        if len(data) <= index:
            return None
        value = (data[index] & int(d.get("mask", 0xFF))) >> int(d.get("shift", 0))
        label = self.labels(d).get(str(value), "")
        return str(value), f"0x{value:02X} {label}".strip()


_default = None


def default():
    global _default
    if _default is None:
        _default = Multiplexers()
    return _default
