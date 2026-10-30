//! J1939-21 29-bit identifier: priority | EDP | DP | PF | PS | SA.

use serde::Serialize;

pub const GLOBAL: u8 = 0xFF;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize)]
pub struct J1939Id {
    pub priority: u8,
    /// 18-bit PGN (EDP, DP, PF, and PS for PDU2). PDU1 PGNs have PS = 0.
    pub pgn: u32,
    pub sa: u8,
    /// Destination; [`GLOBAL`] for PDU2 (broadcast) groups.
    pub da: u8,
}

impl J1939Id {
    pub fn decode(id: u32) -> Self {
        let priority = ((id >> 26) & 0x7) as u8;
        let edp_dp = (id >> 24) & 0x3;
        let pf = (id >> 16) & 0xFF;
        let ps = ((id >> 8) & 0xFF) as u8;
        let sa = (id & 0xFF) as u8;
        if pf < 240 {
            J1939Id { priority, pgn: (edp_dp << 16) | (pf << 8), sa, da: ps }
        } else {
            J1939Id { priority, pgn: (edp_dp << 16) | (pf << 8) | ps as u32, sa, da: GLOBAL }
        }
    }

    pub fn encode(&self) -> u32 {
        let pf = (self.pgn >> 8) & 0xFF;
        let ps = if pf < 240 { self.da as u32 } else { self.pgn & 0xFF };
        ((self.priority as u32 & 0x7) << 26) | ((self.pgn & 0x3_0000) << 8) | (pf << 16) | (ps << 8) | self.sa as u32
    }

    /// PDU1 (destination-specific) format.
    pub fn is_pdu1(&self) -> bool {
        (self.pgn >> 8) & 0xFF < 240
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pdu2_broadcast() {
        let id = J1939Id::decode(0x18FEF100);
        assert_eq!((id.priority, id.pgn, id.sa, id.da), (6, 0xFEF1, 0x00, 0xFF));
        assert_eq!(id.encode(), 0x18FEF100);
    }

    #[test]
    fn pdu1_destination_specific() {
        let id = J1939Id::decode(0x18EA00F9);
        assert_eq!((id.pgn, id.sa, id.da), (0xEA00, 0xF9, 0x00));
        assert!(id.is_pdu1());
        assert_eq!(id.encode(), 0x18EA00F9);
    }

    #[test]
    fn data_page_is_kept() {
        // DP=1 PDU2: PGN 0x1F211 (seen in the BallastController captures).
        let id = J1939Id::decode(0x19F21139);
        assert_eq!(id.pgn, 0x1F211);
        assert_eq!(id.da, GLOBAL);
        // DP=1 PDU1 must stay PDU1 (the DP bit is not part of the PF test).
        let id = J1939Id::decode(0x19EF0017);
        assert_eq!((id.pgn, id.da), (0x1EF00, 0x00));
        assert_eq!(id.encode(), 0x19EF0017);
    }
}
