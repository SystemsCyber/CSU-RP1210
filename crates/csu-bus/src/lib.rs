//! Frame model and CAN bus backends for CSU-RP1210.
//!
//! Every backend (RP1210, PCAN-Basic, SocketCAN, file replay, virtual) yields the
//! same [`Frame`], so protocol stacks above this crate never care where frames
//! came from. Classic CAN and CAN FD share one representation.

use serde::Serialize;
use std::fmt;
use std::time::Duration;

pub mod backend;
pub mod candump;

pub use backend::open;

/// Maximum CAN FD payload.
pub const MAX_DATA: usize = 64;

/// Frame attribute bits.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash, Serialize)]
#[serde(transparent)]
pub struct FrameFlags(pub u8);

impl FrameFlags {
    /// 29-bit identifier.
    pub const EXTENDED: u8 = 0x01;
    /// CAN FD frame.
    pub const FD: u8 = 0x02;
    /// CAN FD bit-rate switch.
    pub const BRS: u8 = 0x04;
    /// CAN FD error-state indicator.
    pub const ESI: u8 = 0x08;
    /// Remote transmission request (classic only).
    pub const RTR: u8 = 0x10;
    /// Error frame reported by the controller.
    pub const ERROR: u8 = 0x20;
    /// Echo of a frame this application transmitted.
    pub const TX_ECHO: u8 = 0x40;

    pub fn has(self, bit: u8) -> bool {
        self.0 & bit != 0
    }
    pub fn set(&mut self, bit: u8, on: bool) {
        if on {
            self.0 |= bit
        } else {
            self.0 &= !bit
        }
    }
}

/// One CAN or CAN FD frame.
#[derive(Clone, Copy, PartialEq)]
pub struct Frame {
    /// Seconds. Hardware time when the backend provides it, otherwise host time.
    pub timestamp: f64,
    /// Logical channel index within the opened bus.
    pub channel: u8,
    /// Identifier without flag bits (11 or 29 bits).
    pub id: u32,
    pub flags: FrameFlags,
    /// Payload length in bytes (0..=64).
    pub len: u8,
    pub data: [u8; MAX_DATA],
}

impl Frame {
    pub fn new(id: u32, extended: bool, payload: &[u8]) -> Self {
        let len = payload.len().min(MAX_DATA);
        let mut data = [0u8; MAX_DATA];
        data[..len].copy_from_slice(&payload[..len]);
        let mut flags = FrameFlags::default();
        flags.set(FrameFlags::EXTENDED, extended);
        flags.set(FrameFlags::FD, len > 8);
        Frame { timestamp: 0.0, channel: 0, id, flags, len: len as u8, data }
    }

    pub fn payload(&self) -> &[u8] {
        &self.data[..self.len as usize]
    }
    pub fn is_extended(&self) -> bool {
        self.flags.has(FrameFlags::EXTENDED)
    }
    pub fn is_fd(&self) -> bool {
        self.flags.has(FrameFlags::FD)
    }
    pub fn is_echo(&self) -> bool {
        self.flags.has(FrameFlags::TX_ECHO)
    }
}

impl fmt::Debug for Frame {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "({:.6}) ch{} {}", self.timestamp, self.channel, candump::format_frame_body(self))
    }
}

impl Serialize for Frame {
    fn serialize<S: serde::Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeStruct;
        let mut st = s.serialize_struct("Frame", 5)?;
        st.serialize_field("ts", &self.timestamp)?;
        st.serialize_field("ch", &self.channel)?;
        st.serialize_field("id", &self.id)?;
        st.serialize_field("flags", &self.flags)?;
        st.serialize_field("data", &hex(self.payload()))?;
        st.end()
    }
}

/// Upper-case hex without separators.
pub fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789ABCDEF";
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push(DIGITS[(b >> 4) as usize] as char);
        s.push(DIGITS[(b & 0xF) as usize] as char);
    }
    s
}

/// CAN FD DLC code to byte length.
pub fn dlc_to_len(dlc: u8) -> usize {
    const TABLE: [usize; 16] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64];
    TABLE[(dlc & 0x0F) as usize]
}

/// Byte length to the smallest CAN FD DLC code that holds it.
pub fn len_to_dlc(len: usize) -> u8 {
    match len {
        0..=8 => len as u8,
        9..=12 => 9,
        13..=16 => 10,
        17..=20 => 11,
        21..=24 => 12,
        25..=32 => 13,
        33..=48 => 14,
        _ => 15,
    }
}

#[derive(Debug, thiserror::Error)]
pub enum BusError {
    #[error("driver library could not be loaded: {0}")]
    Library(String),
    #[error("driver call {call} failed with code {code}: {text}")]
    Driver { call: &'static str, code: i64, text: String },
    #[error("backend does not support this operation: {0}")]
    Unsupported(&'static str),
    #[error("invalid bus specification: {0}")]
    Spec(String),
    #[error("end of input")]
    EndOfInput,
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

/// Static description of an opened bus.
#[derive(Clone, Debug, Serialize)]
pub struct BusInfo {
    pub backend: &'static str,
    pub channel: String,
    pub fd_capable: bool,
    /// Nominal bit rate, when known.
    pub bitrate: Option<u32>,
    /// True for recorded sources (no live bus behind them).
    pub offline: bool,
}

/// A source and sink of frames.
///
/// `recv` returns `Ok(None)` on timeout and `Err(BusError::EndOfInput)` when a
/// finite source (a log file) is exhausted.
pub trait Bus: Send {
    fn recv(&mut self, timeout: Duration) -> Result<Option<Frame>, BusError>;
    fn send(&mut self, frame: &Frame) -> Result<(), BusError>;
    fn info(&self) -> BusInfo;
    /// Interface names by channel index, when the source knows them (logs).
    fn channel_names(&self) -> Vec<String> {
        Vec::new()
    }
}

impl<B: Bus + ?Sized> Bus for Box<B> {
    fn recv(&mut self, timeout: Duration) -> Result<Option<Frame>, BusError> {
        (**self).recv(timeout)
    }
    fn send(&mut self, frame: &Frame) -> Result<(), BusError> {
        (**self).send(frame)
    }
    fn info(&self) -> BusInfo {
        (**self).info()
    }
    fn channel_names(&self) -> Vec<String> {
        (**self).channel_names()
    }
}

/// Host clock in seconds since the Unix epoch.
pub fn host_time() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dlc_round_trip() {
        for len in 0..=64 {
            assert!(dlc_to_len(len_to_dlc(len)) >= len);
        }
        assert_eq!(dlc_to_len(15), 64);
        assert_eq!(len_to_dlc(16), 10);
    }

    #[test]
    fn frame_marks_fd_by_length() {
        assert!(!Frame::new(0x18FEF100, true, &[0; 8]).is_fd());
        assert!(Frame::new(0x18FEF100, true, &[0; 16]).is_fd());
    }
}
