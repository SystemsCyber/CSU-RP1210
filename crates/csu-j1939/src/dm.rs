//! J1939-73 diagnostic messages.
//!
//! Implemented: the DM1/DM2/DM6/DM12/DM23 family (lamp status + DTC list).
//! The DM catalog lists every DM this project plans to support, and the
//! policy gate uses it to tell passive requests from intrusive commands.

use serde::Serialize;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum LampState {
    Off,
    On,
    Error,
    NotAvailable,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum FlashState {
    Slow,
    Fast,
    Reserved,
    Unavailable,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct Lamps {
    pub mil: LampState,
    pub red_stop: LampState,
    pub amber_warning: LampState,
    pub protect: LampState,
    pub mil_flash: FlashState,
    pub red_stop_flash: FlashState,
    pub amber_warning_flash: FlashState,
    pub protect_flash: FlashState,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct Dtc {
    pub spn: u32,
    pub fmi: u8,
    /// SPN conversion method bit. `true` marks a legacy (pre-1996) bit layout
    /// that needs re-mapping; it is reported as-is rather than guessed.
    pub cm: bool,
    pub occurrences: u8,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct DiagnosticMessage {
    pub lamps: Lamps,
    pub dtcs: Vec<Dtc>,
}

fn lamp(bits: u8) -> LampState {
    match bits & 0x3 {
        0 => LampState::Off,
        1 => LampState::On,
        2 => LampState::Error,
        _ => LampState::NotAvailable,
    }
}

fn flash(bits: u8) -> FlashState {
    match bits & 0x3 {
        0 => FlashState::Slow,
        1 => FlashState::Fast,
        2 => FlashState::Reserved,
        _ => FlashState::Unavailable,
    }
}

impl Dtc {
    pub fn decode(b: &[u8; 4]) -> Self {
        Dtc {
            spn: b[0] as u32 | (b[1] as u32) << 8 | ((b[2] as u32) >> 5) << 16,
            fmi: b[2] & 0x1F,
            cm: b[3] & 0x80 != 0,
            occurrences: b[3] & 0x7F,
        }
    }

    pub fn encode(&self) -> [u8; 4] {
        [
            self.spn as u8,
            (self.spn >> 8) as u8,
            (((self.spn >> 16) as u8 & 0x7) << 5) | (self.fmi & 0x1F),
            ((self.cm as u8) << 7) | (self.occurrences & 0x7F),
        ]
    }
}

/// Parse a DM1-format payload (DM1, DM2, DM6, DM12, DM23, DM27, DM28, ...).
///
/// The "no DTC" placeholder (SPN 0, FMI 0) and all-`0xFF` padding are skipped.
pub fn parse_dtc_message(data: &[u8]) -> Option<DiagnosticMessage> {
    if data.len() < 2 {
        return None;
    }
    let (s, f) = (data[0], data[1]);
    let lamps = Lamps {
        mil: lamp(s >> 6),
        red_stop: lamp(s >> 4),
        amber_warning: lamp(s >> 2),
        protect: lamp(s),
        mil_flash: flash(f >> 6),
        red_stop_flash: flash(f >> 4),
        amber_warning_flash: flash(f >> 2),
        protect_flash: flash(f),
    };
    let dtcs = data[2..]
        .chunks_exact(4)
        .map(|c| Dtc::decode(c.try_into().expect("chunk of 4")))
        .filter(|d| !(d.spn == 0 && d.fmi == 0) && !(d.spn == 0x7FFFF && d.fmi == 0x1F))
        .collect();
    Some(DiagnosticMessage { lamps, dtcs })
}

/// How a DM affects the vehicle when requested or sent.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum DmClass {
    /// Broadcast or request-only; reading has no side effect.
    Passive,
    /// Changes ECU or network state (clears, stop-broadcast, memory access).
    Intrusive,
}

pub struct DmInfo {
    pub dm: u8,
    pub pgn: u32,
    pub name: &'static str,
    pub class: DmClass,
    pub implemented: bool,
}

/// DM catalog (J1939-73). PGNs are public identifiers, not DA content.
pub const DM_CATALOG: &[DmInfo] = &[
    DmInfo { dm: 1, pgn: 65226, name: "Active DTCs", class: DmClass::Passive, implemented: true },
    DmInfo { dm: 2, pgn: 65227, name: "Previously active DTCs", class: DmClass::Passive, implemented: true },
    DmInfo { dm: 3, pgn: 65228, name: "Clear previously active DTCs", class: DmClass::Intrusive, implemented: false },
    DmInfo { dm: 4, pgn: 65229, name: "Freeze frame parameters", class: DmClass::Passive, implemented: false },
    DmInfo { dm: 5, pgn: 65230, name: "Diagnostic readiness 1", class: DmClass::Passive, implemented: false },
    DmInfo { dm: 6, pgn: 65231, name: "Emission-related pending DTCs", class: DmClass::Passive, implemented: true },
    DmInfo { dm: 11, pgn: 65235, name: "Clear active DTCs", class: DmClass::Intrusive, implemented: false },
    DmInfo { dm: 12, pgn: 65236, name: "Emission-related active DTCs", class: DmClass::Passive, implemented: true },
    DmInfo { dm: 13, pgn: 57088, name: "Stop/start broadcast", class: DmClass::Intrusive, implemented: false },
    DmInfo { dm: 14, pgn: 55552, name: "Memory access request", class: DmClass::Intrusive, implemented: false },
    DmInfo { dm: 19, pgn: 54016, name: "Calibration information", class: DmClass::Passive, implemented: false },
    DmInfo { dm: 20, pgn: 49664, name: "Monitor performance ratio", class: DmClass::Passive, implemented: false },
    DmInfo { dm: 21, pgn: 49408, name: "Diagnostic readiness 2", class: DmClass::Passive, implemented: false },
    DmInfo { dm: 23, pgn: 64949, name: "Previously MIL-off DTCs", class: DmClass::Passive, implemented: true },
];

pub fn dm_for_pgn(pgn: u32) -> Option<&'static DmInfo> {
    DM_CATALOG.iter().find(|d| d.pgn == pgn)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dm1_single_dtc() {
        // MIL on, amber on; SPN 100 FMI 1 OC 4; padding.
        let m = parse_dtc_message(&[0x44, 0xFF, 0x64, 0x00, 0x01, 0x04, 0xFF, 0xFF]).unwrap();
        assert_eq!(m.lamps.mil, LampState::On);
        assert_eq!(m.lamps.amber_warning, LampState::On);
        assert_eq!(m.lamps.red_stop, LampState::Off);
        assert_eq!(m.dtcs, vec![Dtc { spn: 100, fmi: 1, cm: false, occurrences: 4 }]);
    }

    #[test]
    fn no_fault_placeholder_is_skipped() {
        let m = parse_dtc_message(&[0x00, 0xFF, 0, 0, 0, 0, 0xFF, 0xFF]).unwrap();
        assert!(m.dtcs.is_empty());
    }

    #[test]
    fn high_spn_bits_round_trip() {
        let d = Dtc { spn: 520_192, fmi: 31, cm: false, occurrences: 126 };
        assert_eq!(Dtc::decode(&d.encode()), d);
    }

    #[test]
    fn multi_dtc_from_tp() {
        let data = [0x04, 0xFF, 0x64, 0x00, 0x04, 0x01, 0x6E, 0x00, 0x03, 0x02, 0xBE, 0x00, 0x12, 0x05, 0x5B, 0x00, 0x0F, 0x01, 0x05, 0x02];
        let m = parse_dtc_message(&data).unwrap();
        assert_eq!(m.dtcs.len(), 4);
        assert_eq!(m.dtcs[1], Dtc { spn: 110, fmi: 3, cm: false, occurrences: 2 });
    }
}
