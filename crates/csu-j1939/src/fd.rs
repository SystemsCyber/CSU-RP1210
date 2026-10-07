//! SAE J1939-22 (CAN FD data link layer). **Stub.**
//!
//! J1939-22 carries one or more parameter groups per CAN FD frame inside a
//! multi-PG container and adds an FD transport protocol (FD.TP). The container
//! layout, the C-PG header fields, and the FD.TP control PGNs must be
//! implemented from the licensed J1939-22 document; nothing here encodes them
//! yet.
//!
//! Until then, CAN FD frames are passed to applications unchanged as single
//! [`crate::Message`]s carrying the full FD payload, which is enough for
//! J1939-91C passive monitoring (the secure frame layout comes from the
//! J1939-91C side, see `csu-sec`).

use crate::Message;

/// A contained parameter group extracted from a multi-PG container.
#[derive(Clone, Debug, PartialEq)]
pub struct ContainedPg {
    pub pgn: u32,
    pub data: Vec<u8>,
}

#[derive(Debug, thiserror::Error, PartialEq)]
pub enum FdError {
    #[error("J1939-22 parsing is not implemented yet ({0})")]
    NotImplemented(&'static str),
}

/// Split a J1939-22 multi-PG container into its contained PGs.
pub fn parse_multi_pg(_msg: &Message) -> Result<Vec<ContainedPg>, FdError> {
    // TODO(J1939-22): implement C-PG header parsing (TOS, trailer format,
    // contained PGN, payload length) from the licensed standard.
    Err(FdError::NotImplemented("multi-PG container"))
}

/// Reassemble J1939-22 FD.TP sessions.
pub struct FdTransport;

impl FdTransport {
    pub fn process(&mut self, _msg: &Message) -> Result<Option<Message>, FdError> {
        // TODO(J1939-22): FD.TP connection management and data transfer.
        Err(FdError::NotImplemented("FD transport protocol"))
    }
}
