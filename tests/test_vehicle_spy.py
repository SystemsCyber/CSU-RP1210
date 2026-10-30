"""Vehicle Spy 3 log reader: the 2 s excerpt of a neoVI recording of a DDEC6 truck (J1939 and J1708)."""

import os
import struct

import vehicle_spy as vs

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "neovi_ddec6_excerpt.csv")


def test_header_and_networks():
    header = vs.read_header(FIXTURE)
    assert header.fields["Save Date"] == "7/01/2013"
    assert header.networks["J1708"] == ("J1708", 9600)
    assert header.networks["HS CAN"][0] == "CAN"


def test_records_and_j1708_checksums():
    records = list(vs.read(FIXTURE))
    can = [r for r in records if isinstance(r, vs.CANRecord)]
    j1708 = [r for r in records if isinstance(r, vs.J1708Record)]
    assert len(records) == 1103 and len(can) > 1000 and len(j1708) > 30
    assert all(r.checksum_ok for r in j1708)                     # checksum verified and removed
    assert {r.mid for r in j1708} >= {128, 136}
    eec1 = next(r for r in can if r.can_id == 0x0CF00400)
    assert eec1.extended and len(eec1.data) == 8
    assert eec1.j1939 == (3, 61444, 0, 255)
    assert records[0].time < records[-1].time <= records[0].time + 2.0


def test_bam_reassembly():
    reassembler = vs.J1939Reassembler()
    long_messages = []
    for r in vs.read(FIXTURE):
        if isinstance(r, vs.CANRecord):
            long_messages += [m for m in reassembler.feed(r) if len(m[4]) > 8]
    assert [(pgn, sa, da, len(data)) for _, pgn, sa, da, data in long_messages] == [(65251, 0, 255, 34)]


def test_rp1210_buffers():
    rec = next(r for r in vs.read(FIXTURE) if isinstance(r, vs.J1708Record))
    buf = vs.rp1210_j1708(0x01020304, rec)
    assert buf[:4] == b"\x01\x02\x03\x04" and buf[4] == 0 and buf[5] == rec.mid and buf[6:] == rec.data
    j = vs.rp1210_j1939(5, 3, 61444, 0, 255, bytes(range(8)), echo=True)
    assert struct.unpack(">L", j[:4])[0] == 5 and j[4] == 1
    assert j[5] | j[6] << 8 | j[7] << 16 == 61444 and tuple(j[8:11]) == (3, 0, 255) and j[11:] == bytes(range(8))


def test_candump_and_summary(tmp_path):
    out = tmp_path / "x.candump"
    n = vs.to_candump(FIXTURE, str(out))
    lines = out.read_text().splitlines()
    assert n == len(lines) > 1000
    assert any(" can0 0CF00400#" in l for l in lines)
    s = vs.summary(FIXTURE)
    assert s["bad_j1708_checksums"] == 0 and set(s["messages"]) == {"HS CAN", "J1708"}
