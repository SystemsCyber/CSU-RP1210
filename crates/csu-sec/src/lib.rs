//! SAE J1939-91C secure messaging support.
//!
//! **What is implemented** comes from public material: Zachos & Medam, *"The
//! Golden Tester"*, SAE Technical Paper 2026-01-0092. That covers the
//! benchmark frame layout (Table 2), AES-128-CMAC tag computation over
//! `PGN || SA || FV || payload`, 31-bit truncation, and receiver freshness
//! checks. The paper's four test vectors pass (see `tests/golden_vectors.rs`).
//!
//! **What is stubbed** needs the licensed J1939-91C document: network-formation
//! and rekey PGNs and message layouts, the KDF label/context, certificate
//! profile checks, and the normative state machine. Those APIs exist so callers
//! can be written now. They return [`SecError::NotImplemented`] and are marked
//! `TODO(J1939-91C)`.
//!
//! Key material never leaves this crate in serialized form, and [`SessionKey`]
//! zeroizes itself on drop.

pub mod formation;
pub mod freshness;
pub mod layout;
pub mod tag;

pub use freshness::FreshnessTracker;
pub use layout::SecureFrame;
pub use tag::{compute_cmac, tag31, verify, Verdict};

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum SecError {
    #[error("not implemented: {0} (requires the licensed J1939-91C document)")]
    NotImplemented(&'static str),
    #[error("frame too short for the secure-message layout ({0} bytes)")]
    TooShort(usize),
}

/// AES-128 session key Sₛ. Zeroized on drop; deliberately not `Debug`,
/// `Clone`, or serializable.
pub struct SessionKey([u8; 16]);

impl SessionKey {
    pub fn new(bytes: [u8; 16]) -> Self {
        SessionKey(bytes)
    }
    pub(crate) fn bytes(&self) -> &[u8; 16] {
        &self.0
    }
}

impl Drop for SessionKey {
    fn drop(&mut self) {
        for b in self.0.iter_mut() {
            // SAFETY: writing to an owned, aligned byte.
            unsafe { std::ptr::write_volatile(b, 0) };
        }
        std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
    }
}

/// X.509 extension OID that carries the VIN in J1939-91C NID certificates
/// (SCM-VIN, per ISO 20828 as cited in the paper).
pub const VIN_EXTENSION_OID: &str = "1.0.20828.3";

/// Derive Sₛ from an X25519 shared secret with AES-CMAC-KDF.
pub fn derive_session_key(_shared_secret: &[u8; 32], _context: &[u8]) -> Result<SessionKey, SecError> {
    // TODO(J1939-91C): KDF label, context composition and output length.
    Err(SecError::NotImplemented("AES-CMAC-KDF session key derivation"))
}

/// Check that a NID certificate's VIN extension matches the expected VIN.
pub fn verify_vin_binding(_cert_der: &[u8], _expected_vin: &str) -> Result<bool, SecError> {
    // TODO(J1939-91C): certificate profile (Table 5) and NID = VIN || network number.
    Err(SecError::NotImplemented("NID certificate VIN binding"))
}
