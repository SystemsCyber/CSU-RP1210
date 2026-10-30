//! SAE J1939 protocol stack for CSU-RP1210.
//!
//! * [`id`]: 29-bit identifier codec (J1939-21)
//! * [`tp`]: transport protocol reassembly, BAM and RTS/CTS (J1939-21)
//! * [`db`]: Digital Annex database loader and compiled decoder
//! * [`dm`]: diagnostic messages (J1939-73)
//! * [`name`]: address claim and NAME decoding (J1939-81)
//! * [`summary`]: live network inventory that feeds the tree view and MCP
//! * [`fd`]: J1939-22 CAN FD data link (stubbed)

pub mod db;
pub mod dm;
pub mod fd;
pub mod id;
pub mod name;
pub mod summary;
pub mod tp;

pub use db::{CompiledDb, SpnStatus, SpnValue, UnitSystem};
pub use id::J1939Id;

/// A J1939 parameter group as seen by applications: either a single frame or
/// a reassembled transport-protocol message.
#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct Message {
    pub timestamp: f64,
    pub channel: u8,
    pub priority: u8,
    pub pgn: u32,
    pub sa: u8,
    pub da: u8,
    pub data: Vec<u8>,
    /// True when assembled from TP.DT packets.
    pub reassembled: bool,
}

/// Output of [`Stack::feed`].
#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub enum Event {
    Message(Message),
    TpAborted(tp::TpAbort),
}

/// Frame-to-message pipeline: J1939 identifier decoding plus TP reassembly.
///
/// Every extended frame (including TP.CM/TP.DT themselves) is emitted as a
/// single-frame [`Message`], and completed TP sessions are emitted in
/// addition, so both wire-level and application-level views are available.
#[derive(Default)]
pub struct Stack {
    tp: tp::Reassembler,
    scratch: Vec<tp::TpEvent>,
}

impl Stack {
    pub fn new(cfg: tp::TpConfig) -> Self {
        Stack { tp: tp::Reassembler::new(cfg), scratch: Vec::new() }
    }

    pub fn feed(&mut self, frame: &csu_bus::Frame, out: &mut Vec<Event>) {
        if !frame.is_extended() || frame.flags.has(csu_bus::FrameFlags::ERROR) {
            return;
        }
        let id = J1939Id::decode(frame.id);
        let data = frame.payload();
        out.push(Event::Message(Message {
            timestamp: frame.timestamp,
            channel: frame.channel,
            priority: id.priority,
            pgn: id.pgn,
            sa: id.sa,
            da: id.da,
            data: data.to_vec(),
            reassembled: false,
        }));
        if !frame.is_fd() && (id.pgn == pgn::TP_CM || id.pgn == pgn::TP_DT) {
            self.tp.process(frame.timestamp, frame.channel, id, data, &mut self.scratch);
        } else {
            self.tp.expire(frame.timestamp, &mut self.scratch);
        }
        for e in self.scratch.drain(..) {
            out.push(match e {
                tp::TpEvent::Complete(m) => Event::Message(m),
                tp::TpEvent::Aborted(a) => Event::TpAborted(a),
            });
        }
    }
}

/// Well-known PGNs used by the stack.
pub mod pgn {
    pub const REQUEST: u32 = 0xEA00;
    pub const ACK: u32 = 0xE800;
    pub const TP_CM: u32 = 0xEC00;
    pub const TP_DT: u32 = 0xEB00;
    pub const ADDRESS_CLAIMED: u32 = 0xEE00;
    pub const PROPRIETARY_A: u32 = 0xEF00;
    pub const ISO15765_DIAG: u32 = 0xDA00;
    pub const DM1: u32 = 65226;
    pub const DM2: u32 = 65227;
    pub const DM4: u32 = 65229;
    pub const COMPONENT_ID: u32 = 65259;
    pub const VEHICLE_ID: u32 = 65260;
    pub const SOFTWARE_ID: u32 = 65242;
    pub const TIME_DATE: u32 = 65254;
}
