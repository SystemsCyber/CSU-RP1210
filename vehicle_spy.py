"""
Read Intrepid Control Systems Vehicle Spy 3 bus traffic files (.csv) recorded with
a neoVI, and turn them into the forms the rest of CSU-RP1210 uses.

* read(path):        yield CANRecord and J1708Record entries in file order.
* J1939Reassembler:  rebuild J1939 transport-protocol messages (BAM and RTS/CTS),
                     as an RP1210 adapter does.
* rp1210_j1939() / rp1210_j1708():  RP1210 receive buffers for the J1939 and
                     J1587 tabs (File > Import Vehicle Spy Log).
* to_candump():      CAN traffic as a candump log for the csu command line tool
                     and CSUCAN replay devices.

    python vehicle_spy.py summary  LOG.csv
    python vehicle_spy.py candump  LOG.csv [--out LOG.candump] [--network "HS CAN"]

File layout: a header block (save date, notes, network table), then two column
header rows (one for CAN networks, one for J1708) and one row per message:

    Line, Abs Time(Sec), Rel Time (Sec), Status, Er, Tx, Description, Network, Node,
    Arb ID | MID, Remote, Xtd, B1..B8, Value, ...          (CAN)
    ... , PT, Trgt, Src, B1..B8, Value, ...                (J1708: data bytes start at PT)

CAN rows hold up to 8 data bytes in B1..B8. J1708 rows hold the bytes after the
MID in PT..B8 and continue in the space-separated Value column; the last byte is
the J1708 checksum.
"""

import argparse
import collections
import csv
import struct
import sys
from dataclasses import dataclass, field

J1939_TP_CM = 0xEC00
J1939_TP_DT = 0xEB00


@dataclass
class CANRecord:
    time: float
    network: str
    can_id: int
    extended: bool
    data: bytes
    tx: bool = False
    error: bool = False
    line: int = 0

    @property
    def j1939(self):
        """(priority, pgn, sa, da) of a 29-bit identifier."""
        cid = self.can_id
        priority, pf, ps, sa = (cid >> 26) & 7, (cid >> 16) & 0xFF, (cid >> 8) & 0xFF, cid & 0xFF
        dp = (cid >> 24) & 3
        if pf < 240:
            return priority, (dp << 16) | (pf << 8), sa, ps
        return priority, (dp << 16) | (pf << 8) | ps, sa, 0xFF


@dataclass
class J1708Record:
    time: float
    network: str
    mid: int
    data: bytes                      # bytes after the MID, without the checksum
    checksum_ok: bool = True
    tx: bool = False
    error: bool = False
    line: int = 0

    @property
    def message(self):
        return bytes([self.mid]) + self.data


@dataclass
class Header:
    fields: dict = field(default_factory=dict)
    networks: dict = field(default_factory=dict)   # name -> (protocol, baud)


def _hexbytes(cells):
    out = []
    for cell in cells:
        for token in str(cell).split():
            out.append(int(token, 16))
    return bytes(out)


def read_header(path):
    header = Header()
    with open(path, newline="", encoding="latin-1") as f:
        rows = csv.reader(f)
        in_networks = False
        for row in rows:
            if not row:
                in_networks = False
                continue
            if row[0] == "Line":
                break
            if row[0] == "Network Description":
                in_networks = True
                continue
            if in_networks and len(row) >= 5:
                baud = row[4].strip()
                header.networks[row[0]] = (row[3], int(baud) if baud.isdigit() else None)
            elif len(row) >= 2:
                header.fields[row[0]] = row[1]
    return header


def read(path, networks=None):
    """Yield CANRecord / J1708Record for each message row (optionally only some networks)."""
    j1708_networks = None
    with open(path, newline="", encoding="latin-1") as f:
        rows = csv.reader(f)
        protocols = {}
        for row in rows:
            if row and row[0] == "Network Description":
                for net in rows:
                    if not net or not net[0]:
                        break
                    if len(net) >= 4:
                        protocols[net[0]] = net[3]
            if row and row[0] == "Line":
                break
        j1708_networks = {name for name, proto in protocols.items() if proto.upper() == "J1708"}
        for row in rows:
            if not row or row[0] == "Line" or len(row) < 12:
                continue
            network = row[7]
            if networks and network not in networks:
                continue
            try:
                line, t = int(row[0]), float(row[1])
            except ValueError:
                continue
            tx, error = row[5] == "T", row[4] == "T"
            if network in j1708_networks or network.upper().startswith("J1708"):
                body = _hexbytes(row[10:21])
                mid = int(row[9], 16)
                ok = (mid + sum(body)) & 0xFF == 0 if body else False
                yield J1708Record(t, network, mid, body[:-1] if ok else body, ok, tx, error, line)
            else:
                data = _hexbytes(row[12:20])
                yield CANRecord(t, network, int(row[9], 16), row[11] == "T", data, tx, error, line)


class J1939Reassembler:
    """J1939-21 transport protocol reassembly for recorded traffic (BAM and RTS/CTS).

    feed(record) returns a list of (priority, pgn, sa, da, data) messages: single
    frames pass through, TP.CM/TP.DT frames are consumed and the completed
    multi-packet message is returned when its last packet arrives.
    """

    def __init__(self):
        self.sessions = {}

    def feed(self, record):
        priority, pgn, sa, da = record.j1939
        data = record.data
        if pgn == J1939_TP_CM and len(data) >= 8:
            control = data[0]
            if control in (0x20, 0x10):            # BAM or RTS
                size = data[1] | data[2] << 8
                packets = data[3]
                target = data[5] | data[6] << 8 | data[7] << 16
                self.sessions[(sa, da)] = {"pgn": target, "size": size, "packets": packets,
                                           "priority": priority, "data": {}}
            elif control == 0xFF:                  # abort
                self.sessions.pop((da, sa), None)
                self.sessions.pop((sa, da), None)
            return []
        if pgn == J1939_TP_DT and len(data) >= 2:
            session = self.sessions.get((sa, da))
            if session is None:
                return []
            session["data"][data[0]] = bytes(data[1:8])
            if len(session["data"]) >= session["packets"]:
                payload = b"".join(session["data"].get(i, b"\xff" * 7) for i in range(1, session["packets"] + 1))
                del self.sessions[(sa, da)]
                return [(session["priority"], session["pgn"], sa, da, payload[:session["size"]])]
            return []
        return [(priority, pgn, sa, da, bytes(data))]


def rp1210_j1939(timestamp_us, priority, pgn, sa, da, data, echo=False):
    """RP1210 J1939 receive buffer: timestamp, echo, PGN (3, LSB first), priority, SA, DA, data."""
    return (struct.pack(">L", timestamp_us & 0xFFFFFFFF) + bytes([1 if echo else 0])
            + struct.pack("<L", pgn)[:3] + bytes([priority, sa, da]) + bytes(data))


def rp1210_j1708(timestamp_us, record, echo=False):
    """RP1210 J1708 receive buffer: timestamp, echo, MID and data (no checksum)."""
    return struct.pack(">L", timestamp_us & 0xFFFFFFFF) + bytes([1 if echo else 0]) + record.message


def to_candump(path, out, network=None, interface="can0"):
    """Write CAN records as a candump log. Returns the number of frames written."""
    n = 0
    with open(out, "w") as f:
        for r in read(path):
            if not isinstance(r, CANRecord) or r.error or (network and r.network != network):
                continue
            cid = f"{r.can_id:08X}" if r.extended else f"{r.can_id:03X}"
            f.write(f"({r.time:.6f}) {interface} {cid}#{r.data.hex().upper()}\n")
            n += 1
    return n


def summary(path):
    header = read_header(path)
    counts = collections.Counter()
    ids = collections.defaultdict(collections.Counter)
    bad = 0
    first = last = None
    for r in read(path):
        first = r.time if first is None else first
        last = r.time
        counts[r.network] += 1
        if isinstance(r, J1708Record):
            ids[r.network][r.mid] += 1
            bad += not r.checksum_ok
        else:
            ids[r.network][r.can_id] += 1
    return {"header": header.fields, "duration_s": (last - first) if first is not None else 0,
            "messages": dict(counts), "ids": {k: len(v) for k, v in ids.items()}, "bad_j1708_checksums": bad}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summary", help="networks, message counts and duration")
    s.add_argument("log")
    c = sub.add_parser("candump", help="write the CAN traffic as a candump log")
    c.add_argument("log")
    c.add_argument("--out")
    c.add_argument("--network", help='network name, e.g. "HS CAN" (default: all CAN networks)')
    args = p.parse_args(argv)
    if args.cmd == "summary":
        for k, v in summary(args.log).items():
            print(f"{k}: {v}")
    else:
        out = args.out or args.log.rsplit(".", 1)[0] + ".candump"
        print(f"Wrote {to_candump(args.log, out, args.network)} frames to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
