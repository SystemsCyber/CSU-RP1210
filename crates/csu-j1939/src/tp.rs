//! J1939-21 transport protocol reassembly (passive observer).
//!
//! Handles BAM and RTS/CTS sessions concurrently per (channel, SA, DA), out of
//! order and duplicated TP.DT packets, connection aborts, and the J1939-21
//! timeouts (T1 = 750 ms between BAM/DT packets; 1250 ms for RTS/CTS, which
//! covers T2/T3 on a passive tap).

use crate::id::{J1939Id, GLOBAL};
use crate::{pgn, Message};
use serde::Serialize;
use std::collections::HashMap;

/// Largest TP payload: 255 packets x 7 bytes.
pub const MAX_TP_SIZE: usize = 1785;

const CM_RTS: u8 = 16;
const CM_CTS: u8 = 17;
const CM_EOMA: u8 = 19;
const CM_BAM: u8 = 32;
const CM_ABORT: u8 = 255;

#[derive(Clone, Copy, Debug)]
pub struct TpConfig {
    pub bam_timeout: f64,
    pub cmdt_timeout: f64,
}

impl Default for TpConfig {
    fn default() -> Self {
        TpConfig { bam_timeout: 0.750, cmdt_timeout: 1.250 }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", content = "code")]
pub enum AbortReason {
    /// No TP.DT/CTS within the J1939-21 timeout.
    Timeout,
    /// TP.Conn_Abort with the J1939-21 abort reason byte.
    ConnectionAbort(u8),
    /// A new BAM/RTS from the same originator replaced an unfinished session.
    Replaced,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct TpAbort {
    pub timestamp: f64,
    pub channel: u8,
    pub sa: u8,
    pub da: u8,
    pub pgn: u32,
    pub received_packets: u8,
    pub total_packets: u8,
    pub reason: AbortReason,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub enum TpEvent {
    Complete(Message),
    Aborted(TpAbort),
}

struct Session {
    pgn: u32,
    priority: u8,
    size: usize,
    total: u8,
    got: u8,
    seen: [u64; 4],
    buf: Vec<u8>,
    last: f64,
    bam: bool,
}

impl Session {
    fn mark(&mut self, seq: u8) -> bool {
        let (w, b) = ((seq / 64) as usize, seq % 64);
        let fresh = self.seen[w] & (1 << b) == 0;
        self.seen[w] |= 1 << b;
        fresh
    }
}

#[derive(Default)]
pub struct Reassembler {
    sessions: HashMap<(u8, u8, u8), Session>,
    cfg: TpConfig,
}

impl Reassembler {
    pub fn new(cfg: TpConfig) -> Self {
        Reassembler { sessions: HashMap::new(), cfg }
    }

    pub fn active_sessions(&self) -> usize {
        self.sessions.len()
    }

    /// Feed one TP.CM or TP.DT frame. Other PGNs are ignored.
    pub fn process(&mut self, ts: f64, channel: u8, id: J1939Id, data: &[u8], out: &mut Vec<TpEvent>) {
        self.expire(ts, out);
        match id.pgn {
            pgn::TP_CM => self.control(ts, channel, id, data, out),
            pgn::TP_DT => self.data(ts, channel, id, data, out),
            _ => {}
        }
    }

    fn control(&mut self, ts: f64, ch: u8, id: J1939Id, d: &[u8], out: &mut Vec<TpEvent>) {
        if d.len() < 8 {
            return;
        }
        let target_pgn = d[5] as u32 | (d[6] as u32) << 8 | (d[7] as u32) << 16;
        match d[0] {
            CM_BAM | CM_RTS => {
                let size = u16::from_le_bytes([d[1], d[2]]) as usize;
                let total = d[3];
                if !(9..=MAX_TP_SIZE).contains(&size) || total as usize != size.div_ceil(7) {
                    return;
                }
                let bam = d[0] == CM_BAM;
                let da = if bam { GLOBAL } else { id.da };
                let key = (ch, id.sa, da);
                if let Some(old) = self.sessions.remove(&key) {
                    out.push(abort(ts, key, &old, AbortReason::Replaced));
                }
                self.sessions.insert(
                    key,
                    Session {
                        pgn: target_pgn,
                        priority: id.priority,
                        size,
                        total,
                        got: 0,
                        seen: [0; 4],
                        buf: vec![0xFF; total as usize * 7],
                        last: ts,
                        bam,
                    },
                );
            }
            CM_CTS | CM_EOMA => {
                // Sent by the receiver; keeps the originator's session alive.
                if let Some(s) = self.sessions.get_mut(&(ch, id.da, id.sa)) {
                    s.last = ts;
                }
            }
            CM_ABORT => {
                for key in [(ch, id.sa, id.da), (ch, id.da, id.sa)] {
                    if let Some(s) = self.sessions.remove(&key) {
                        out.push(abort(ts, key, &s, AbortReason::ConnectionAbort(d[1])));
                    }
                }
            }
            _ => {}
        }
    }

    fn data(&mut self, ts: f64, ch: u8, id: J1939Id, d: &[u8], out: &mut Vec<TpEvent>) {
        if d.len() < 2 {
            return;
        }
        let key = (ch, id.sa, id.da);
        let Some(s) = self.sessions.get_mut(&key) else { return };
        let seq = d[0];
        if seq == 0 || seq > s.total {
            return;
        }
        s.last = ts;
        if s.mark(seq) {
            let start = (seq as usize - 1) * 7;
            let chunk = &d[1..d.len().min(8)];
            s.buf[start..start + chunk.len()].copy_from_slice(chunk);
            s.got += 1;
        }
        if s.got == s.total {
            let s = self.sessions.remove(&key).expect("present");
            let mut data = s.buf;
            data.truncate(s.size);
            out.push(TpEvent::Complete(Message {
                timestamp: ts,
                channel: ch,
                priority: s.priority,
                pgn: s.pgn,
                sa: key.1,
                da: key.2,
                data,
                reassembled: true,
            }));
        }
    }

    /// Drop sessions that exceeded their timeout as of `now`.
    pub fn expire(&mut self, now: f64, out: &mut Vec<TpEvent>) {
        if self.sessions.is_empty() {
            return;
        }
        let cfg = self.cfg;
        let stale: Vec<_> = self
            .sessions
            .iter()
            .filter(|(_, s)| now - s.last > if s.bam { cfg.bam_timeout } else { cfg.cmdt_timeout })
            .map(|(k, _)| *k)
            .collect();
        for key in stale {
            let s = self.sessions.remove(&key).expect("present");
            out.push(abort(now, key, &s, AbortReason::Timeout));
        }
    }
}

fn abort(ts: f64, key: (u8, u8, u8), s: &Session, reason: AbortReason) -> TpEvent {
    TpEvent::Aborted(TpAbort {
        timestamp: ts,
        channel: key.0,
        sa: key.1,
        da: key.2,
        pgn: s.pgn,
        received_packets: s.got,
        total_packets: s.total,
        reason,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cm(sa: u8, da: u8) -> J1939Id {
        J1939Id { priority: 7, pgn: pgn::TP_CM, sa, da }
    }
    fn dt(sa: u8, da: u8) -> J1939Id {
        J1939Id { priority: 7, pgn: pgn::TP_DT, sa, da }
    }

    /// A 20-byte DM1 broadcast from SA 0x00 over BAM.
    fn bam_frames() -> Vec<(J1939Id, Vec<u8>)> {
        vec![
            (cm(0x00, 0xFF), vec![CM_BAM, 20, 0, 3, 0xFF, 0xCA, 0xFE, 0x00]),
            (dt(0x00, 0xFF), vec![1, 0x04, 0xFF, 0x64, 0x00, 0x04, 0x01, 0x6E]),
            (dt(0x00, 0xFF), vec![2, 0x00, 0x03, 0x02, 0xBE, 0x00, 0x12, 0x05]),
            (dt(0x00, 0xFF), vec![3, 0x5B, 0x00, 0x0F, 0x01, 0x05, 0x02, 0xFF]),
        ]
    }

    #[test]
    fn bam_in_order() {
        let mut r = Reassembler::default();
        let mut out = vec![];
        for (i, (id, d)) in bam_frames().into_iter().enumerate() {
            r.process(i as f64 * 0.05, 0, id, &d, &mut out);
        }
        assert_eq!(out.len(), 1);
        let TpEvent::Complete(m) = &out[0] else { panic!("{out:?}") };
        assert_eq!((m.pgn, m.sa, m.da, m.data.len()), (65226, 0, 0xFF, 20));
        assert_eq!(&m.data[..3], &[0x04, 0xFF, 0x64]);
        assert_eq!(m.data[19], 0x02);
        assert_eq!(r.active_sessions(), 0);
    }

    #[test]
    fn bam_out_of_order_with_duplicate() {
        let f = bam_frames();
        let mut r = Reassembler::default();
        let mut out = vec![];
        for (id, d) in [&f[0], &f[3], &f[1], &f[1], &f[2]] {
            r.process(0.1, 0, *id, d, &mut out);
        }
        assert!(matches!(&out[..], [TpEvent::Complete(m)] if m.data.len() == 20));
    }

    #[test]
    fn timeout_aborts_session() {
        let f = bam_frames();
        let mut r = Reassembler::default();
        let mut out = vec![];
        r.process(0.0, 0, f[0].0, &f[0].1, &mut out);
        r.process(0.1, 0, f[1].0, &f[1].1, &mut out);
        r.expire(1.0, &mut out);
        assert!(matches!(&out[..], [TpEvent::Aborted(a)] if a.reason == AbortReason::Timeout && a.received_packets == 1));
        // A late packet no longer matches any session.
        r.process(1.05, 0, f[2].0, &f[2].1, &mut out);
        assert_eq!(out.len(), 1);
    }

    #[test]
    fn rts_cts_and_abort() {
        let mut r = Reassembler::default();
        let mut out = vec![];
        // ECU 0x00 -> tool 0xF9, 9 bytes in 2 packets.
        r.process(0.0, 0, cm(0x00, 0xF9), &[CM_RTS, 9, 0, 2, 2, 0xEC, 0xFE, 0x00], &mut out);
        r.process(0.01, 0, cm(0xF9, 0x00), &[CM_CTS, 2, 1, 0xFF, 0xFF, 0xEC, 0xFE, 0x00], &mut out);
        r.process(0.02, 0, dt(0x00, 0xF9), &[1, b'1', b'X', b'K', b'Y', b'D', b'P', b'9'], &mut out);
        r.process(0.03, 0, dt(0x00, 0xF9), &[2, b'X', b'*', 0xFF, 0xFF, 0xFF, 0xFF, 0xFF], &mut out);
        let TpEvent::Complete(m) = &out[0] else { panic!() };
        assert_eq!((m.pgn, m.da), (65260, 0xF9));
        assert_eq!(&m.data, b"1XKYDP9X*");

        out.clear();
        r.process(1.0, 0, cm(0x00, 0xF9), &[CM_RTS, 9, 0, 2, 2, 0xEC, 0xFE, 0x00], &mut out);
        r.process(1.01, 0, cm(0xF9, 0x00), &[CM_ABORT, 1, 0xFF, 0xFF, 0xFF, 0xEC, 0xFE, 0x00], &mut out);
        assert!(matches!(&out[..], [TpEvent::Aborted(a)] if a.reason == AbortReason::ConnectionAbort(1)));
    }

    #[test]
    fn rejects_inconsistent_announcement() {
        let mut r = Reassembler::default();
        let mut out = vec![];
        r.process(0.0, 0, cm(0x00, 0xFF), &[CM_BAM, 20, 0, 9, 0xFF, 0xCA, 0xFE, 0x00], &mut out);
        assert_eq!(r.active_sessions(), 0);
    }
}
