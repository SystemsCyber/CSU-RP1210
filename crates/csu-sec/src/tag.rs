//! AES-128-CMAC authentication tag.
//!
//! CMAC input = `PGN (3 bytes, MSB first) || SA (1) || FV (4, MSB first) || payload`.
//! The encryption bit E is *not* part of the input. Figure 2 of the paper writes
//! `E + PGN + SA + FV + Data`, but its golden vector only reproduces without E.
//! TODO(J1939-91C): confirm the CMAC input composition against the standard.

use crate::{FreshnessTracker, SessionKey};
use aes::Aes128;
use cmac::{Cmac, Mac};
use serde::Serialize;

/// The 8-byte nonce `PGN || SA || FV`.
pub fn nonce(pgn: u32, sa: u8, fv: u32) -> [u8; 8] {
    let p = pgn.to_be_bytes();
    let f = fv.to_be_bytes();
    [p[1], p[2], p[3], sa, f[0], f[1], f[2], f[3]]
}

pub fn compute_cmac(key: &SessionKey, pgn: u32, sa: u8, fv: u32, payload: &[u8]) -> [u8; 16] {
    let mut mac = <Cmac<Aes128> as Mac>::new_from_slice(key.bytes()).expect("16-byte key");
    mac.update(&nonce(pgn, sa, fv));
    mac.update(payload);
    mac.finalize().into_bytes().into()
}

/// The 31 most significant bits of the CMAC (right-aligned).
pub fn tag31(cmac: &[u8; 16]) -> u32 {
    u32::from_be_bytes([cmac[0], cmac[1], cmac[2], cmac[3]]) >> 1
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum Verdict {
    Pass,
    TagMismatch,
    FreshnessReplay,
}

/// Receiver decision: authenticate, then enforce freshness. The tracker is
/// only advanced on [`Verdict::Pass`].
pub fn verify(
    key: &SessionKey,
    pgn: u32,
    sa: u8,
    fv: u32,
    payload: &[u8],
    received_tag31: u32,
    tracker: &mut FreshnessTracker,
) -> Verdict {
    let expected = tag31(&compute_cmac(key, pgn, sa, fv, payload));
    // Constant-time comparison of the truncated tags.
    if (expected ^ (received_tag31 & 0x7FFF_FFFF)) != 0 {
        return Verdict::TagMismatch;
    }
    if !tracker.accept(sa, pgn, fv) {
        return Verdict::FreshnessReplay;
    }
    Verdict::Pass
}
