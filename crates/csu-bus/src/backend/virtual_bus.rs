//! In-process loopback bus. Every clone shares one medium; a frame sent on any
//! handle is received by all other handles (and echoed to the sender with
//! `TX_ECHO` set, like a hardware adapter with echo enabled).

use crate::{Bus, BusError, BusInfo, Frame, FrameFlags};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::time::Duration;

#[derive(Default)]
struct Medium {
    taps: Vec<(usize, Sender<Frame>)>,
    next_id: usize,
}

pub struct VirtualBus {
    id: usize,
    medium: Arc<Mutex<Medium>>,
    rx: Receiver<Frame>,
}

impl VirtualBus {
    pub fn new() -> Self {
        Self::attach(Arc::new(Mutex::new(Medium::default())))
    }

    fn attach(medium: Arc<Mutex<Medium>>) -> Self {
        let (tx, rx) = channel();
        let id = {
            let mut m = medium.lock().unwrap();
            let id = m.next_id;
            m.next_id += 1;
            m.taps.push((id, tx));
            id
        };
        VirtualBus { id, medium, rx }
    }

    /// Another node on the same medium.
    pub fn connect(&self) -> Self {
        Self::attach(self.medium.clone())
    }
}

impl Default for VirtualBus {
    fn default() -> Self {
        Self::new()
    }
}

impl Drop for VirtualBus {
    fn drop(&mut self) {
        if let Ok(mut m) = self.medium.lock() {
            m.taps.retain(|(id, _)| *id != self.id);
        }
    }
}

impl Bus for VirtualBus {
    fn recv(&mut self, timeout: Duration) -> Result<Option<Frame>, BusError> {
        match self.rx.recv_timeout(timeout) {
            Ok(f) => Ok(Some(f)),
            Err(RecvTimeoutError::Timeout) => Ok(None),
            Err(RecvTimeoutError::Disconnected) => Err(BusError::EndOfInput),
        }
    }

    fn send(&mut self, frame: &Frame) -> Result<(), BusError> {
        let mut f = *frame;
        f.timestamp = crate::host_time();
        let m = self.medium.lock().unwrap();
        for (id, tap) in &m.taps {
            let mut copy = f;
            copy.flags.set(FrameFlags::TX_ECHO, *id == self.id);
            let _ = tap.send(copy);
        }
        Ok(())
    }

    fn info(&self) -> BusInfo {
        BusInfo { backend: "virtual", channel: format!("node{}", self.id), fd_capable: true, bitrate: None, offline: false }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frames_reach_other_nodes_and_echo() {
        let mut a = VirtualBus::new();
        let mut b = a.connect();
        a.send(&Frame::new(0x18EAFFF9, true, &[0x00, 0xEE, 0x00])).unwrap();
        let got = b.recv(Duration::from_millis(50)).unwrap().unwrap();
        assert!(!got.is_echo());
        let echo = a.recv(Duration::from_millis(50)).unwrap().unwrap();
        assert!(echo.is_echo());
    }
}
