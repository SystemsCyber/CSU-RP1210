//! J1939-91C lifecycle monitor. **Mostly stubbed.**
//!
//! States follow the simplified lifecycle published in the Golden Tester paper
//! (Figures 7 and 10): Off, Network formation, Rekeying, Exchange, Failed, and
//! Software update (out of scope). The transition rules, timeouts and message
//! classification come from the licensed standard and are not encoded yet.

use crate::SecError;
use serde::Serialize;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum State {
    Off,
    NetworkFormation,
    Rekeying,
    Exchange,
    Failed,
    SoftwareUpdate,
}

/// Role of the observed or emulated ECU.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum Role {
    Leader,
    Follower,
}

/// Formation/rekey message kinds named in the paper.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum FormationMessage {
    AnnounceLeader,
    JoinNetwork,
    JoinRequest,
    SendSecureNonce,
    SecureNonceAck,
    RekeyRequest,
    RekeyResponse,
}

/// PGNs used by the paper's *simulation*. They are reproduced only to help
/// correlate captures from that test bench, and must not be treated as
/// normative.
/// TODO(J1939-91C): replace with the PGN assignments from the standard.
pub const PAPER_SIMULATION_PGNS: &[(FormationMessage, u32)] = &[
    (FormationMessage::AnnounceLeader, 0xFA02),
    (FormationMessage::JoinNetwork, 0xFA03),
    (FormationMessage::SendSecureNonce, 0x4740),
    (FormationMessage::SecureNonceAck, 0xFA06),
];

/// Response windows the paper cites from J1939-91C Table 1 (seconds).
pub const PAPER_RESPONSE_WINDOW: f64 = 0.250;
pub const PAPER_ROUND_TRIP_MAX: f64 = 0.500;

pub struct LifecycleMonitor {
    pub state: State,
    pub transitions: Vec<(f64, State, State)>,
}

impl Default for LifecycleMonitor {
    fn default() -> Self {
        LifecycleMonitor { state: State::Off, transitions: Vec::new() }
    }
}

impl LifecycleMonitor {
    /// Classify a captured message as a formation/rekey message.
    pub fn classify(&self, _pgn: u32, _data: &[u8]) -> Result<Option<FormationMessage>, SecError> {
        // TODO(J1939-91C): normative PGNs and payload discrimination.
        Err(SecError::NotImplemented("formation message classification"))
    }

    /// Advance the lifecycle on an observed message.
    pub fn on_message(&mut self, _ts: f64, _msg: FormationMessage) -> Result<State, SecError> {
        // TODO(J1939-91C): transition table and timeout handling.
        Err(SecError::NotImplemented("lifecycle transitions"))
    }
}
