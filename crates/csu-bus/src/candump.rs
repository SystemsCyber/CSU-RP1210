//! Linux `can-utils` text formats: read and write.
//!
//! Supported input lines:
//! * log format (`candump -L`): `(1708107705.888290) can1 0CF00400#FF7D7D000000FFFF`
//! * CAN FD log format: `(ts) can0 18FF0001##1112233...` (one flags nibble after `##`)
//! * RTR: `123#R` / `123#R5`
//! * default/ASCII format, with or without `-t a` timestamps:
//!   `(ts)  can0  18FEF100   [8]  FF 00 00 F5 FF 11 DF FF`
//!
//! A trailing ` T` (transmitted) or ` R` (received) direction marker is honored.

use crate::{Bus, BusError, BusInfo, Frame, FrameFlags, MAX_DATA};
use std::collections::HashMap;
use std::fmt::Write as _;
use std::fs::File;
use std::io::{BufRead, BufReader, Lines};
use std::path::Path;
use std::time::{Duration, Instant};

const CAN_ERR_FLAG: u32 = 0x2000_0000;

/// Parse one line. `channels` maps interface names to channel indexes and is
/// extended as new names appear. Returns `None` for blank, comment or
/// unparseable lines.
pub fn parse_line(line: &str, channels: &mut HashMap<String, u8>) -> Option<Frame> {
    let line = line.trim();
    if line.is_empty() || line.starts_with('#') {
        return None;
    }
    let mut tokens = line.split_whitespace().peekable();
    let mut timestamp = 0.0;
    if let Some(t) = tokens.peek() {
        if t.starts_with('(') {
            timestamp = t.trim_matches(|c| c == '(' || c == ')').parse().ok()?;
            tokens.next();
        }
    }
    let iface = tokens.next()?;
    let ch = channel_index(iface, channels);
    let body = tokens.next()?;
    let mut frame = if body.contains('#') {
        parse_log_body(body)?
    } else {
        // ASCII format: ID  [len]  bytes...
        let id_txt = body;
        let len_tok = tokens.next()?;
        let len: usize = len_tok.trim_matches(|c| c == '[' || c == ']').parse().ok()?;
        let mut data = Vec::with_capacity(len);
        for _ in 0..len {
            data.push(u8::from_str_radix(tokens.next()?, 16).ok()?);
        }
        let id = u32::from_str_radix(id_txt, 16).ok()?;
        Frame::new(id & 0x1FFF_FFFF, id_txt.len() > 3, &data)
    };
    if let Some(dir) = tokens.next() {
        frame.flags.set(FrameFlags::TX_ECHO, dir == "T");
    }
    frame.timestamp = timestamp;
    frame.channel = ch;
    Some(frame)
}

fn channel_index(iface: &str, channels: &mut HashMap<String, u8>) -> u8 {
    if let Some(&c) = channels.get(iface) {
        return c;
    }
    let c = channels.len().min(u8::MAX as usize) as u8;
    channels.insert(iface.to_string(), c);
    c
}

fn parse_log_body(body: &str) -> Option<Frame> {
    let (id_txt, rest) = body.split_once('#')?;
    let raw_id = u32::from_str_radix(id_txt, 16).ok()?;
    let extended = id_txt.len() > 3;
    let mut flags = FrameFlags::default();
    flags.set(FrameFlags::EXTENDED, extended);
    flags.set(FrameFlags::ERROR, extended && raw_id & CAN_ERR_FLAG != 0);

    let (fd, data_txt) = match rest.strip_prefix('#') {
        Some(fd_rest) => {
            let mut chars = fd_rest.chars();
            let nibble = chars.next()?.to_digit(16)? as u8;
            flags.set(FrameFlags::FD, true);
            flags.set(FrameFlags::BRS, nibble & 0x1 != 0);
            flags.set(FrameFlags::ESI, nibble & 0x2 != 0);
            (true, chars.as_str())
        }
        None => (false, rest),
    };

    let mut data = [0u8; MAX_DATA];
    let mut len = 0usize;
    if !fd && (data_txt.starts_with('R') || data_txt.starts_with('r')) {
        flags.set(FrameFlags::RTR, true);
        len = data_txt[1..].parse::<usize>().unwrap_or(0).min(8);
    } else {
        let digits: Vec<u8> = data_txt.bytes().filter(|b| *b != b'.').collect();
        if digits.len() % 2 != 0 || digits.len() / 2 > MAX_DATA {
            return None;
        }
        for pair in digits.chunks(2) {
            let s = std::str::from_utf8(pair).ok()?;
            data[len] = u8::from_str_radix(s, 16).ok()?;
            len += 1;
        }
    }
    let id = if extended { raw_id & 0x1FFF_FFFF } else { raw_id & 0x7FF };
    Some(Frame { timestamp: 0.0, channel: 0, id, flags, len: len as u8, data })
}

/// `ID#DATA` or `ID##FDATA` (no timestamp or interface).
pub fn format_frame_body(f: &Frame) -> String {
    let mut s = String::with_capacity(16 + f.len as usize * 2);
    if f.is_extended() {
        let _ = write!(s, "{:08X}", f.id);
    } else {
        let _ = write!(s, "{:03X}", f.id);
    }
    if f.is_fd() {
        let nib = (f.flags.has(FrameFlags::BRS) as u8) | ((f.flags.has(FrameFlags::ESI) as u8) << 1);
        let _ = write!(s, "##{:X}", nib);
    } else if f.flags.has(FrameFlags::RTR) {
        s.push_str("#R");
        return s;
    } else {
        s.push('#');
    }
    s.push_str(&crate::hex(f.payload()));
    s
}

/// A full `candump -L` line.
pub fn format_line(f: &Frame, iface: &str) -> String {
    format!("({:.6}) {} {}", f.timestamp, iface, format_frame_body(f))
}

/// Replays a candump log as a [`Bus`].
///
/// With `speed > 0` frames are released in real time scaled by `speed`;
/// with `speed == 0` they are delivered as fast as they are read.
pub struct CandumpReader {
    path: String,
    lines: Lines<BufReader<File>>,
    channels: HashMap<String, u8>,
    speed: f64,
    looping: bool,
    start: Option<(Instant, f64)>,
    pending: Option<Frame>,
    /// Offset added to timestamps on each loop so time keeps increasing.
    loop_offset: f64,
    last_ts: f64,
}

impl CandumpReader {
    pub fn open(path: impl AsRef<Path>, speed: f64, looping: bool) -> Result<Self, BusError> {
        let p = path.as_ref();
        let file = File::open(p)?;
        Ok(CandumpReader {
            path: p.display().to_string(),
            lines: BufReader::new(file).lines(),
            channels: HashMap::new(),
            speed,
            looping,
            start: None,
            pending: None,
            loop_offset: 0.0,
            last_ts: 0.0,
        })
    }

    /// Interface names seen so far, indexed by channel number.
    pub fn channel_names(&self) -> Vec<String> {
        let mut v: Vec<(u8, String)> = self.channels.iter().map(|(k, v)| (*v, k.clone())).collect();
        v.sort();
        v.into_iter().map(|(_, k)| k).collect()
    }

    fn next_frame(&mut self) -> Result<Frame, BusError> {
        loop {
            match self.lines.next() {
                Some(line) => {
                    if let Some(mut f) = parse_line(&line?, &mut self.channels) {
                        f.timestamp += self.loop_offset;
                        self.last_ts = f.timestamp;
                        return Ok(f);
                    }
                }
                None if self.looping => {
                    let file = File::open(&self.path)?;
                    self.lines = BufReader::new(file).lines();
                    self.loop_offset = self.last_ts;
                    self.start = None;
                    // Re-anchor so the first frame of the next pass is not
                    // rebased onto the old origin.
                    self.loop_offset -= self.peek_first_timestamp()?;
                }
                None => return Err(BusError::EndOfInput),
            }
        }
    }

    fn peek_first_timestamp(&mut self) -> Result<f64, BusError> {
        let file = File::open(&self.path)?;
        for line in BufReader::new(file).lines() {
            if let Some(f) = parse_line(&line?, &mut self.channels) {
                return Ok(f.timestamp);
            }
        }
        Err(BusError::EndOfInput)
    }
}

impl Iterator for CandumpReader {
    type Item = Frame;
    fn next(&mut self) -> Option<Frame> {
        self.next_frame().ok()
    }
}

impl Bus for CandumpReader {
    fn recv(&mut self, timeout: Duration) -> Result<Option<Frame>, BusError> {
        let frame = match self.pending.take() {
            Some(f) => f,
            None => self.next_frame()?,
        };
        if self.speed <= 0.0 {
            return Ok(Some(frame));
        }
        let (t0, ts0) = *self.start.get_or_insert((Instant::now(), frame.timestamp));
        let due = Duration::from_secs_f64(((frame.timestamp - ts0) / self.speed).max(0.0));
        let elapsed = t0.elapsed();
        if due <= elapsed {
            return Ok(Some(frame));
        }
        let wait = due - elapsed;
        if wait > timeout {
            std::thread::sleep(timeout);
            self.pending = Some(frame);
            return Ok(None);
        }
        std::thread::sleep(wait);
        Ok(Some(frame))
    }

    fn send(&mut self, _frame: &Frame) -> Result<(), BusError> {
        Err(BusError::Unsupported("cannot transmit on a replayed log"))
    }

    fn info(&self) -> BusInfo {
        BusInfo { backend: "candump", channel: self.path.clone(), fd_capable: true, bitrate: None, offline: true }
    }

    fn channel_names(&self) -> Vec<String> {
        CandumpReader::channel_names(self)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(s: &str) -> Frame {
        parse_line(s, &mut HashMap::new()).expect(s)
    }

    #[test]
    fn log_format_classic() {
        let f = p("(1708107705.888290) can1 0CF00400#FF7D7D000000FFFF");
        assert_eq!(f.id, 0x0CF00400);
        assert!(f.is_extended() && !f.is_fd());
        assert_eq!(f.payload(), &[0xFF, 0x7D, 0x7D, 0, 0, 0, 0xFF, 0xFF]);
        assert!((f.timestamp - 1708107705.888290).abs() < 1e-6);
    }

    #[test]
    fn log_format_fd_and_standard() {
        let f = p("(1.0) can0 18FF0001##3" .to_string().add_hex(16).as_str());
        assert!(f.is_fd() && f.flags.has(FrameFlags::BRS) && f.flags.has(FrameFlags::ESI));
        assert_eq!(f.len, 16);
        let s = p("(1.0) vcan0 7DF#0201050000000000");
        assert!(!s.is_extended());
        assert_eq!(s.id, 0x7DF);
    }

    #[test]
    fn ascii_format_and_direction() {
        let f = p("(2.5)  can0  18FEF100   [8]  FF 00 00 F5 FF 11 DF FF");
        assert_eq!(f.id, 0x18FEF100);
        assert_eq!(f.payload()[3], 0xF5);
        let t = p("(2.5) can0 18EAFFF9#00EE00 T");
        assert!(t.is_echo());
    }

    #[test]
    fn round_trip() {
        let line = "(1708107705.888290) can1 0CF00400#FF7D7D000000FFFF";
        assert_eq!(format_line(&p(line), "can1"), line);
        let fd = "(1.000000) can0 18FF0001##1112233445566778899AABBCCDDEEFF00";
        assert_eq!(format_line(&p(fd), "can0"), fd);
    }

    #[test]
    fn rejects_garbage() {
        assert!(parse_line("hello world", &mut HashMap::new()).is_none());
        assert!(parse_line("(1.0) can0 123#ABC", &mut HashMap::new()).is_none());
    }

    trait AddHex {
        fn add_hex(self, n: usize) -> String;
    }
    impl AddHex for String {
        fn add_hex(mut self, n: usize) -> String {
            for i in 0..n {
                self.push_str(&format!("{:02X}", i));
            }
            self
        }
    }
}
