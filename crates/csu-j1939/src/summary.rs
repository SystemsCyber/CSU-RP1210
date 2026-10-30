//! Live network inventory: channel → source address → parameter group.
//!
//! This is the data model behind the tree view (ported from the
//! BallastController HMI's SA → PGN → byte drill-down) and the MCP
//! `vehicle://network/topology` resource. It keeps running per-byte
//! statistics (Welford mean/variance and change counts), which make
//! varying fields stand out when reverse engineering proprietary messages.

use crate::db::{CompiledDb, SpnStatus};
use crate::dm::{parse_dtc_message, DiagnosticMessage};
use crate::id::GLOBAL;
use crate::name::Name;
use crate::tp::TpAbort;
use crate::{pgn, Message};
use serde::Serialize;
use std::collections::BTreeMap;

/// Byte statistics are kept for at most this many leading bytes.
const STATS_BYTES: usize = 64;

#[derive(Clone, Debug, Default)]
pub struct PgnNode {
    pub pgn: u32,
    pub da: u8,
    pub can_id: Option<u32>,
    pub reassembled: bool,
    pub count: u64,
    pub first_ts: f64,
    pub last_ts: f64,
    /// Average inter-arrival time in seconds over the session.
    pub period: Option<f64>,
    /// Most recent, shortest and longest inter-arrival times in seconds.
    /// Some senders alternate intervals (e.g. 68/68/93 ms), so the last
    /// interval alone (what PCAN-View shows as cycle time) can mislead.
    pub last_interval: Option<f64>,
    pub min_interval: Option<f64>,
    pub max_interval: Option<f64>,
    pub last_data: Vec<u8>,
    n: u64,
    mean: Vec<f64>,
    m2: Vec<f64>,
    changes: Vec<u64>,
}

impl PgnNode {
    fn observe(&mut self, ts: f64, data: &[u8]) {
        if self.count > 0 {
            let dt = ts - self.last_ts;
            if dt >= 0.0 {
                self.period = Some((ts - self.first_ts) / self.count as f64);
                self.last_interval = Some(dt);
                self.min_interval = Some(self.min_interval.map_or(dt, |m| m.min(dt)));
                self.max_interval = Some(self.max_interval.map_or(dt, |m| m.max(dt)));
            }
        } else {
            self.first_ts = ts;
        }
        self.count += 1;
        self.last_ts = ts;
        let k = data.len().min(STATS_BYTES);
        if k != self.mean.len() {
            // Length changed (or first frame): restart statistics.
            self.n = 0;
            self.mean = vec![0.0; k];
            self.m2 = vec![0.0; k];
            self.changes = vec![0; k];
        } else {
            for (i, (old, new)) in self.last_data.iter().zip(data).take(k).enumerate() {
                if old != new {
                    self.changes[i] += 1;
                }
            }
        }
        self.n += 1;
        for (i, b) in data.iter().take(k).enumerate() {
            let x = *b as f64;
            let d = x - self.mean[i];
            self.mean[i] += d / self.n as f64;
            self.m2[i] += d * (x - self.mean[i]);
        }
        self.last_data.clear();
        self.last_data.extend_from_slice(data);
    }

    pub fn std_dev(&self) -> Vec<f64> {
        if self.n < 2 {
            return vec![0.0; self.m2.len()];
        }
        self.m2.iter().map(|m| (m / (self.n - 1) as f64).sqrt()).collect()
    }
}

#[derive(Clone, Debug, Default)]
pub struct SourceNode {
    pub sa: u8,
    pub count: u64,
    pub claimed_name: Option<Name>,
    pub vin: Option<String>,
    pub component_id: Option<String>,
    pub software_id: Option<String>,
    pub dm1: Option<DiagnosticMessage>,
    pub pgns: BTreeMap<(u32, u8, bool), PgnNode>,
}

#[derive(Clone, Debug, Default)]
pub struct ChannelNode {
    pub name: String,
    pub count: u64,
    pub sources: BTreeMap<u8, SourceNode>,
    /// 11-bit identifiers (not J1939), keyed by CAN ID.
    pub standard: BTreeMap<u32, PgnNode>,
    pub tp_aborts: u64,
    /// Error and adapter status frames (not counted as traffic).
    pub error_frames: u64,
}

#[derive(Clone, Debug, Default)]
pub struct NetworkTree {
    pub channels: BTreeMap<u8, ChannelNode>,
    pub total: u64,
    pub first_ts: Option<f64>,
    pub last_ts: f64,
}

fn printable(data: &[u8]) -> String {
    data.iter()
        .filter(|b| b.is_ascii_graphic() || **b == b' ')
        .map(|b| *b as char)
        .collect::<String>()
        .trim_end_matches('*')
        .trim()
        .to_string()
}

impl NetworkTree {
    pub fn set_channel_name(&mut self, ch: u8, name: &str) {
        self.channels.entry(ch).or_default().name = name.to_string();
    }

    fn touch(&mut self, ts: f64) {
        self.first_ts.get_or_insert(ts);
        if ts > self.last_ts {
            self.last_ts = ts;
        }
    }

    /// Record an error or adapter status frame.
    pub fn observe_error(&mut self, frame: &csu_bus::Frame) {
        self.channels.entry(frame.channel).or_default().error_frames += 1;
    }

    /// Record an 11-bit (non-J1939) frame. Error/status frames are counted separately.
    pub fn observe_standard(&mut self, frame: &csu_bus::Frame) {
        if frame.flags.has(csu_bus::FrameFlags::ERROR) {
            self.observe_error(frame);
            return;
        }
        self.total += 1;
        self.touch(frame.timestamp);
        let ch = self.channels.entry(frame.channel).or_default();
        ch.count += 1;
        let node = ch.standard.entry(frame.id).or_insert_with(|| PgnNode { can_id: Some(frame.id), ..Default::default() });
        node.observe(frame.timestamp, frame.payload());
    }

    /// Record a J1939 message (single frame or reassembled).
    pub fn observe(&mut self, m: &Message) {
        self.touch(m.timestamp);
        let ch = self.channels.entry(m.channel).or_default();
        if !m.reassembled {
            self.total += 1;
            ch.count += 1;
        }
        let src = ch.sources.entry(m.sa).or_insert_with(|| SourceNode { sa: m.sa, ..Default::default() });
        if !m.reassembled {
            src.count += 1;
        }
        let da = if (m.pgn >> 8) & 0xFF < 240 { m.da } else { GLOBAL };
        let node = src.pgns.entry((m.pgn, da, m.reassembled)).or_insert_with(|| PgnNode {
            pgn: m.pgn,
            da,
            reassembled: m.reassembled,
            can_id: (!m.reassembled).then(|| crate::J1939Id { priority: m.priority, pgn: m.pgn, sa: m.sa, da }.encode()),
            ..Default::default()
        });
        node.observe(m.timestamp, &m.data);

        match m.pgn {
            pgn::ADDRESS_CLAIMED => src.claimed_name = Name::from_bytes(&m.data),
            pgn::VEHICLE_ID => src.vin = Some(printable(&m.data)),
            pgn::COMPONENT_ID => src.component_id = Some(printable(&m.data)),
            pgn::SOFTWARE_ID => src.software_id = Some(printable(m.data.get(1..).unwrap_or(&[]))),
            pgn::DM1 => src.dm1 = parse_dtc_message(&m.data),
            _ => {}
        }
    }

    pub fn observe_abort(&mut self, a: &TpAbort) {
        self.channels.entry(a.channel).or_default().tp_aborts += 1;
    }

    /// Serializable view. `detail` selects one (channel, SA, PGN, DA, reassembled)
    /// node to decode fully.
    pub fn snapshot(&self, db: &CompiledDb, detail: Option<&Selection>) -> Snapshot {
        let channels = self
            .channels
            .iter()
            .map(|(ch, c)| ChannelView {
                ch: *ch,
                name: if c.name.is_empty() { format!("ch{ch}") } else { c.name.clone() },
                count: c.count,
                tp_aborts: c.tp_aborts,
                error_frames: c.error_frames,
                sources: c.sources.values().map(|s| source_view(s, db)).collect(),
                standard: c.standard.values().map(|n| pgn_view(n, db)).collect(),
            })
            .collect();
        let detail = detail.and_then(|sel| self.detail(sel, db));
        Snapshot {
            first_ts: self.first_ts.unwrap_or(0.0),
            last_ts: self.last_ts,
            total: self.total,
            db_skeleton_only: db.meta.skeleton_only,
            channels,
            detail,
        }
    }

    fn detail(&self, sel: &Selection, db: &CompiledDb) -> Option<DetailView> {
        let ch = self.channels.get(&sel.ch)?;
        let node = if sel.standard {
            ch.standard.get(&sel.pgn)?
        } else {
            ch.sources.get(&sel.sa)?.pgns.get(&(sel.pgn, sel.da, sel.reassembled))?
        };
        let spns = if sel.standard {
            vec![]
        } else {
            db.decode(node.pgn, &node.last_data)
                .into_iter()
                .map(|v| {
                    let def = db.spns.get(&v.spn);
                    SpnView {
                        spn: v.spn,
                        name: def.map(|d| d.name.clone()).unwrap_or_default(),
                        units: def.map(|d| d.units.clone()).unwrap_or_default(),
                        value: v.value,
                        raw: v.raw,
                        status: v.status,
                        text: v.text,
                    }
                })
                .collect()
        };
        let dm = if node.pgn == pgn::DM1 || node.pgn == pgn::DM2 {
            parse_dtc_message(&node.last_data).map(|m| {
                m.dtcs
                    .iter()
                    .map(|d| DtcView {
                        spn: d.spn,
                        fmi: d.fmi,
                        occurrences: d.occurrences,
                        spn_name: db.spns.get(&d.spn).map(|s| s.name.clone()).unwrap_or_default(),
                        fmi_text: db.fmi.get(&d.fmi).map(|f| f.0.clone()).unwrap_or_default(),
                    })
                    .collect()
            })
        } else {
            None
        };
        Some(DetailView { selection: sel.clone(), node: pgn_view(node, db), spns, dtcs: dm })
    }
}

fn source_view(s: &SourceNode, db: &CompiledDb) -> SourceView {
    SourceView {
        sa: s.sa,
        name: db.sa_name(s.sa).to_string(),
        count: s.count,
        claimed_name: s.claimed_name.map(|n| NameView {
            raw: format!("{:016X}", n.raw),
            manufacturer_code: n.manufacturer_code,
            manufacturer: db.manufacturers.get(&n.manufacturer_code).cloned().unwrap_or_default(),
            function: n.function,
            identity_number: n.identity_number,
            industry_group: n.industry_group,
        }),
        vin: s.vin.clone(),
        component_id: s.component_id.clone(),
        software_id: s.software_id.clone(),
        mil: s.dm1.as_ref().map(|d| d.lamps.mil == crate::dm::LampState::On).unwrap_or(false),
        dtc_count: s.dm1.as_ref().map(|d| d.dtcs.len()).unwrap_or(0),
        pgns: s.pgns.values().map(|n| pgn_view(n, db)).collect(),
    }
}

/// Seconds to milliseconds, one decimal place.
fn ms(s: f64) -> f64 {
    (s * 1000.0 * 10.0).round() / 10.0
}

fn pgn_view(n: &PgnNode, db: &CompiledDb) -> PgnView {
    let (label, name) = db.pgn_label(n.pgn);
    PgnView {
        pgn: n.pgn,
        da: n.da,
        reassembled: n.reassembled,
        can_id: n.can_id,
        label: label.to_string(),
        name: name.to_string(),
        count: n.count,
        period_ms: n.period.map(ms),
        last_ms: n.last_interval.map(ms),
        min_ms: n.min_interval.map(ms),
        max_ms: n.max_interval.map(ms),
        last_ts: n.last_ts,
        data: csu_bus::hex(&n.last_data),
        mean: n.mean.iter().map(|m| (m * 10.0).round() / 10.0).collect(),
        std: n.std_dev().iter().map(|s| (s * 100.0).round() / 100.0).collect(),
        changes: n.changes.clone(),
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, serde::Deserialize)]
pub struct Selection {
    pub ch: u8,
    #[serde(default)]
    pub sa: u8,
    pub pgn: u32,
    #[serde(default = "global")]
    pub da: u8,
    #[serde(default)]
    pub reassembled: bool,
    /// Select an 11-bit identifier (`pgn` holds the CAN ID).
    #[serde(default)]
    pub standard: bool,
}

fn global() -> u8 {
    GLOBAL
}

#[derive(Serialize)]
pub struct Snapshot {
    pub first_ts: f64,
    pub last_ts: f64,
    pub total: u64,
    pub db_skeleton_only: bool,
    pub channels: Vec<ChannelView>,
    pub detail: Option<DetailView>,
}

#[derive(Serialize)]
pub struct ChannelView {
    pub ch: u8,
    pub name: String,
    pub count: u64,
    pub tp_aborts: u64,
    pub error_frames: u64,
    pub sources: Vec<SourceView>,
    pub standard: Vec<PgnView>,
}

#[derive(Serialize)]
pub struct NameView {
    pub raw: String,
    pub manufacturer_code: u16,
    pub manufacturer: String,
    pub function: u8,
    pub identity_number: u32,
    pub industry_group: u8,
}

#[derive(Serialize)]
pub struct SourceView {
    pub sa: u8,
    pub name: String,
    pub count: u64,
    pub claimed_name: Option<NameView>,
    pub vin: Option<String>,
    pub component_id: Option<String>,
    pub software_id: Option<String>,
    pub mil: bool,
    pub dtc_count: usize,
    pub pgns: Vec<PgnView>,
}

#[derive(Serialize)]
pub struct PgnView {
    pub pgn: u32,
    pub da: u8,
    pub reassembled: bool,
    pub can_id: Option<u32>,
    pub label: String,
    pub name: String,
    pub count: u64,
    pub period_ms: Option<f64>,
    pub last_ms: Option<f64>,
    pub min_ms: Option<f64>,
    pub max_ms: Option<f64>,
    pub last_ts: f64,
    pub data: String,
    pub mean: Vec<f64>,
    pub std: Vec<f64>,
    pub changes: Vec<u64>,
}

#[derive(Serialize)]
pub struct SpnView {
    pub spn: u32,
    pub name: String,
    pub units: String,
    pub value: Option<f64>,
    pub raw: Option<u64>,
    pub status: SpnStatus,
    pub text: Option<String>,
}

#[derive(Serialize)]
pub struct DtcView {
    pub spn: u32,
    pub fmi: u8,
    pub occurrences: u8,
    pub spn_name: String,
    pub fmi_text: String,
}

#[derive(Serialize)]
pub struct DetailView {
    pub selection: Selection,
    pub node: PgnView,
    pub spns: Vec<SpnView>,
    pub dtcs: Option<Vec<DtcView>>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn msg(ts: f64, pgn: u32, sa: u8, data: &[u8]) -> Message {
        Message { timestamp: ts, channel: 0, priority: 6, pgn, sa, da: 0xFF, data: data.to_vec(), reassembled: false }
    }

    #[test]
    fn statistics_and_identity() {
        let mut t = NetworkTree::default();
        for i in 0..10u8 {
            t.observe(&msg(i as f64 * 0.1, 0xF004, 0, &[0xFF, 0x7D, i, 0, 0, 0, 0xFF, 0xFF]));
        }
        t.observe(&msg(1.0, pgn::VEHICLE_ID, 0, b"1XKYDP9X0LJ123456*"));
        let src = &t.channels[&0].sources[&0];
        let node = &src.pgns[&(0xF004, 0xFF, false)];
        assert_eq!(node.count, 10);
        assert!((node.period.unwrap() - 0.1).abs() < 1e-9);
        assert!((node.last_interval.unwrap() - 0.1).abs() < 1e-9);
        let std = node.std_dev();
        assert_eq!(std[0], 0.0);
        assert!(std[2] > 2.9 && std[2] < 3.1);
        assert_eq!(node.changes[2], 9);
        assert_eq!(src.vin.as_deref(), Some("1XKYDP9X0LJ123456"));

        let snap = t.snapshot(&CompiledDb::default(), Some(&Selection { ch: 0, sa: 0, pgn: 0xF004, da: 0xFF, reassembled: false, standard: false }));
        assert_eq!(snap.total, 11);
        assert_eq!(snap.detail.unwrap().node.can_id, Some(0x18F00400));
    }

    #[test]
    fn alternating_intervals_report_average_min_max_last() {
        let mut t = NetworkTree::default();
        // 68, 68, 93 ms pattern seen from a GPS on the live bus.
        let mut ts = 0.0;
        for dt in [0.0, 0.068, 0.068, 0.093, 0.068, 0.068, 0.093] {
            ts += dt;
            t.observe(&msg(ts, 127251, 44, &[0x42, 0xFF, 0xFF, 0xFF, 0x7F, 0xFF, 0xFF, 0xFF]));
        }
        let n = &t.channels[&0].sources[&44].pgns[&(127251, 0xFF, false)];
        assert!((n.period.unwrap() - 0.07633).abs() < 1e-4);
        assert!((n.min_interval.unwrap() - 0.068).abs() < 1e-9);
        assert!((n.max_interval.unwrap() - 0.093).abs() < 1e-9);
        assert!((n.last_interval.unwrap() - 0.093).abs() < 1e-9);
    }

    #[test]
    fn status_frames_are_not_traffic() {
        let mut t = NetworkTree::default();
        let mut f = csu_bus::Frame::new(0x001, false, &[0, 0, 0, 0]);
        f.flags.set(csu_bus::FrameFlags::ERROR, true);
        t.observe_standard(&f);
        assert_eq!(t.total, 0);
        assert!(t.channels[&0].standard.is_empty());
        assert_eq!(t.channels[&0].error_frames, 1);
    }
}
