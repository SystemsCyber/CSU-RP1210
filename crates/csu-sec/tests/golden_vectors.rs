//! Test vectors from Zachos & Medam, "The Golden Tester", SAE 2026-01-0092,
//! Table 1 and the listings that follow it (encryption disabled, E = 0).

use csu_sec::tag::nonce;
use csu_sec::{compute_cmac, tag31, verify, FreshnessTracker, SecureFrame, Verdict};

const KEY: &str = "c60eb74833281d8404aa4e1db2aa1712";
const EXPECTED_CMAC: &str = "57916E828A91D637152723F75900F939";
const PGN: u32 = 0x00EF00;
const PAYLOAD: [u8; 8] = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88];

fn key() -> csu_sec::SessionKey {
    let mut k = [0u8; 16];
    for (i, b) in k.iter_mut().enumerate() {
        *b = u8::from_str_radix(&KEY[2 * i..2 * i + 2], 16).unwrap();
    }
    csu_sec::SessionKey::new(k)
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02X}")).collect()
}

fn transmitted_tag() -> u32 {
    tag31(&compute_cmac(&key(), PGN, 0xA7, 0x3C, &PAYLOAD))
}

#[test]
fn vector_001_valid() {
    assert_eq!(hex(&nonce(PGN, 0xA7, 0x3C)), "00EF00A70000003C");
    assert_eq!(hex(&compute_cmac(&key(), PGN, 0xA7, 0x3C, &PAYLOAD)), EXPECTED_CMAC);
    let mut fv = FreshnessTracker::new();
    assert_eq!(verify(&key(), PGN, 0xA7, 0x3C, &PAYLOAD, transmitted_tag(), &mut fv), Verdict::Pass);
}

#[test]
fn vector_002_wrong_sa() {
    assert_eq!(hex(&nonce(PGN, 0xA6, 0x3C)), "00EF00A60000003C");
    let mut fv = FreshnessTracker::new();
    assert_eq!(verify(&key(), PGN, 0xA6, 0x3C, &PAYLOAD, transmitted_tag(), &mut fv), Verdict::TagMismatch);
}

#[test]
fn vector_003_replay_fv() {
    let mut fv = FreshnessTracker::new();
    fv.seed(0xA7, PGN, 0x3C); // assumption_receiver_last_accepted_fv
    assert_eq!(verify(&key(), PGN, 0xA7, 0x3C, &PAYLOAD, transmitted_tag(), &mut fv), Verdict::FreshnessReplay);
}

#[test]
fn vector_004_fv_endianness() {
    assert_eq!(hex(&nonce(PGN, 0xA7, 0x3C00_0000)), "00EF00A73C000000");
    let mut fv = FreshnessTracker::new();
    assert_eq!(verify(&key(), PGN, 0xA7, 0x3C00_0000, &PAYLOAD, transmitted_tag(), &mut fv), Verdict::TagMismatch);
}

#[test]
fn wire_frame_round_trip() {
    let frame = SecureFrame { payload: PAYLOAD.to_vec(), fv: 0x3C, encrypted: false, tag31: transmitted_tag() };
    let wire = frame.encode();
    assert_eq!(wire.len(), 16);
    // E = 0, followed by the 31 MSBs of 0x57916E82.
    assert_eq!(hex(&wire[12..]), format!("{:08X}", 0x5791_6E82u32 >> 1));
    let parsed = SecureFrame::parse(&wire).unwrap();
    let mut fv = FreshnessTracker::new();
    assert_eq!(verify(&key(), PGN, 0xA7, parsed.fv, &parsed.payload, parsed.tag31, &mut fv), Verdict::Pass);
}

#[test]
fn standard_dependent_parts_are_stubbed() {
    assert!(csu_sec::derive_session_key(&[0; 32], b"").is_err());
    assert!(csu_sec::verify_vin_binding(&[], "1XKYDP9X0LJ123456").is_err());
}
