//! J1939-81 NAME (64-bit, sent little-endian in PGN 60928 Address Claimed).

use serde::Serialize;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize)]
pub struct Name {
    pub raw: u64,
    pub identity_number: u32,
    pub manufacturer_code: u16,
    pub ecu_instance: u8,
    pub function_instance: u8,
    pub function: u8,
    pub vehicle_system: u8,
    pub vehicle_system_instance: u8,
    pub industry_group: u8,
    pub arbitrary_address_capable: bool,
}

impl Name {
    pub fn from_bytes(b: &[u8]) -> Option<Self> {
        let raw = u64::from_le_bytes(b.get(..8)?.try_into().ok()?);
        Some(Self::from_raw(raw))
    }

    pub fn from_raw(raw: u64) -> Self {
        Name {
            raw,
            identity_number: (raw & 0x1F_FFFF) as u32,
            manufacturer_code: ((raw >> 21) & 0x7FF) as u16,
            ecu_instance: ((raw >> 32) & 0x7) as u8,
            function_instance: ((raw >> 35) & 0x1F) as u8,
            function: ((raw >> 40) & 0xFF) as u8,
            vehicle_system: ((raw >> 49) & 0x7F) as u8,
            vehicle_system_instance: ((raw >> 56) & 0xF) as u8,
            industry_group: ((raw >> 60) & 0x7) as u8,
            arbitrary_address_capable: raw >> 63 != 0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decode_fields() {
        // identity 0x12345, mfr 0x2AB, ecu inst 2, func inst 3, function 0x81,
        // vehicle system 0x10, vs inst 1, industry group 1 (on-highway), AAC.
        let raw = 0x12345u64
            | (0x2ABu64 << 21)
            | (2u64 << 32)
            | (3u64 << 35)
            | (0x81u64 << 40)
            | (0x10u64 << 49)
            | (1u64 << 56)
            | (1u64 << 60)
            | (1u64 << 63);
        let n = Name::from_bytes(&raw.to_le_bytes()).unwrap();
        assert_eq!(n.identity_number, 0x12345);
        assert_eq!(n.manufacturer_code, 0x2AB);
        assert_eq!((n.ecu_instance, n.function_instance, n.function), (2, 3, 0x81));
        assert_eq!((n.vehicle_system, n.vehicle_system_instance, n.industry_group), (0x10, 1, 1));
        assert!(n.arbitrary_address_capable);
    }
}
