//! Secure frame layout used by the Golden Tester benchmark (paper Table 2):
//!
//! | Item | Size | Order |
//! |---|---|---|
//! | Payload (PT) | n − 8 bytes | as sent |
//! | Freshness value (FV) | 4 bytes | MSB first |
//! | E_Tag | 4 bytes | MSB first: bit 31 = E, bits 30..0 = CMAC[31 MSBs] |
//!
//! The benchmark fixes the CAN FD length at 16 bytes (8-byte payload).
//! TODO(J1939-91C): confirm the trailer layout for other payload lengths and
//! for encrypted (E = 1) messages.

use crate::SecError;
use serde::Serialize;

pub const OVERHEAD: usize = 8;

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct SecureFrame {
    pub payload: Vec<u8>,
    pub fv: u32,
    pub encrypted: bool,
    pub tag31: u32,
}

impl SecureFrame {
    pub fn parse(data: &[u8]) -> Result<Self, SecError> {
        if data.len() < OVERHEAD + 1 {
            return Err(SecError::TooShort(data.len()));
        }
        let n = data.len();
        let fv = u32::from_be_bytes(data[n - 8..n - 4].try_into().expect("4 bytes"));
        let e_tag = u32::from_be_bytes(data[n - 4..].try_into().expect("4 bytes"));
        Ok(SecureFrame { payload: data[..n - 8].to_vec(), fv, encrypted: e_tag >> 31 != 0, tag31: e_tag & 0x7FFF_FFFF })
    }

    pub fn encode(&self) -> Vec<u8> {
        let mut out = self.payload.clone();
        out.extend_from_slice(&self.fv.to_be_bytes());
        out.extend_from_slice(&(((self.encrypted as u32) << 31) | (self.tag31 & 0x7FFF_FFFF)).to_be_bytes());
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip() {
        let f = SecureFrame { payload: vec![1, 2, 3, 4, 5, 6, 7, 8], fv: 0x3C, encrypted: false, tag31: 0x2BC8_B741 };
        let bytes = f.encode();
        assert_eq!(bytes.len(), 16);
        assert_eq!(SecureFrame::parse(&bytes).unwrap(), f);
    }
}
