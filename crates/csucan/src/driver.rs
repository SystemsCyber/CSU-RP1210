//! RP1210 C semantics over csu-bus.
//!
//! * One [`Hub`] per (device, channel) owns the bus and a reader thread; every
//!   client on that channel shares it (RP1210 apps typically open a CAN and a
//!   J1939 client on the same adapter).
//! * Transmissions are echoed in software (`Echo_Transmitted_Messages`) and
//!   delivered to sibling clients as received traffic, as on real hardware.
//! * J1939 clients get transport-protocol reassembly (BAM and RTS/CTS), BAM and
//!   RTS/CTS transmission for messages over 8 bytes, address claiming
//!   (`Protect_J1939_Address`) and CTS/EoMA responses for the claimed address.

use crate::devices::{self, Device};
use crate::errors::*;
use csu_bus::{Bus, BusError, Frame, FrameFlags};
use csu_j1939::id::{J1939Id, GLOBAL};
use csu_j1939::pgn;
use csu_j1939::tp::{Reassembler, TpEvent};
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Condvar, Mutex, OnceLock, Weak};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

pub const MAX_CLIENTS: usize = 128;
const T3: Duration = Duration::from_millis(1250);
const T4: Duration = Duration::from_millis(1050);

// RP1210_SendCommand numbers (RP1210C).
pub const CMD_RESET_DEVICE: i16 = 0;
pub const CMD_SET_ALL_FILTERS_TO_PASS: i16 = 3;
pub const CMD_ECHO_TRANSMITTED_MESSAGES: i16 = 16;
pub const CMD_SET_ALL_FILTERS_TO_DISCARD: i16 = 17;
pub const CMD_SET_MESSAGE_RECEIVE: i16 = 18;
pub const CMD_PROTECT_J1939_ADDRESS: i16 = 19;
pub const CMD_SET_J1939_INTERPACKET_TIME: i16 = 27;
pub const CMD_DISALLOW_FURTHER_CONNECTIONS: i16 = 29;
pub const CMD_RELEASE_J1939_ADDRESS: i16 = 31;
pub const CMD_FLUSH_TX_RX_BUFFERS: i16 = 39;
pub const CMD_SET_BLOCK_TIMEOUT: i16 = 215;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Protocol {
    Can,
    J1939,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ConnectRequest {
    pub protocol: Protocol,
    pub channel: u8,
    pub nominal: u32,
    pub data: Option<u32>,
    /// Report FD/BRS bits in the CAN message-type byte (CSUCAN extension).
    pub fd_flags: bool,
}

/// Parse an RP1210 protocol string such as `J1939:Baud=250,Channel=4` or
/// `CAN:Baud=500/2000,FDFlags=1`.
pub fn parse_protocol(s: &str) -> Result<ConnectRequest, i16> {
    let (name, params) = s.split_once(':').unwrap_or((s, ""));
    let protocol = match name.trim().to_ascii_uppercase().as_str() {
        "CAN" => Protocol::Can,
        "J1939" => Protocol::J1939,
        _ => return Err(ERR_INVALID_PROTOCOL),
    };
    let mut req = ConnectRequest { protocol, channel: 1, nominal: 250_000, data: None, fd_flags: false };
    for p in params.split([',', ';']).map(str::trim).filter(|p| !p.is_empty()) {
        let (k, v) = p.split_once('=').unwrap_or((p, ""));
        match k.trim().to_ascii_lowercase().as_str() {
            "baud" => {
                let v = v.trim();
                if v.eq_ignore_ascii_case("auto") {
                    continue;
                }
                let (nom, data) = v.split_once('/').unwrap_or((v, ""));
                req.nominal = kbps(nom).ok_or(ERR_INVALID_PROTOCOL)?;
                if !data.is_empty() {
                    req.data = Some(kbps(data).ok_or(ERR_INVALID_PROTOCOL)?);
                }
            }
            "channel" => req.channel = v.trim().parse().map_err(|_| ERR_INVALID_PROTOCOL)?,
            "fdflags" => req.fd_flags = v.trim() == "1" || v.trim().eq_ignore_ascii_case("true"),
            _ => {} // unknown parameters are ignored, as RP1210 drivers commonly do
        }
    }
    if req.channel == 0 {
        return Err(ERR_INVALID_PROTOCOL);
    }
    Ok(req)
}

/// "250" or "250k" (kbit/s) or "250000" (bit/s) to bit/s.
fn kbps(v: &str) -> Option<u32> {
    let v = v.trim().trim_end_matches(['k', 'K']);
    let n: u32 = v.parse().ok()?;
    Some(if n >= 10_000 { n } else { n * 1000 })
}

// ---------------------------------------------------------------------------
// Hub: one opened bus shared by the clients of a (device, channel)
// ---------------------------------------------------------------------------

pub struct Hub {
    key: (i16, u8),
    label: String,
    bus: Mutex<Box<dyn Bus>>,
    clients: Mutex<Vec<Weak<Client>>>,
    running: AtomicBool,
    reader: Mutex<Option<JoinHandle<()>>>,
    nominal: u32,
    data: Option<u32>,
    last_rx: Mutex<Option<Instant>>,
    bus_off: AtomicBool,
    /// Threads waiting to transmit. The reader steps aside while this is
    /// non-zero; std's Mutex is not fair and the reader would starve senders.
    pending_tx: AtomicUsize,
}

impl Hub {
    fn open(device: &Device, channel: u8, req: &ConnectRequest) -> Result<Arc<Hub>, (i16, String)> {
        let ch = device
            .channels
            .get(channel as usize - 1)
            .ok_or((ERR_INVALID_DEVICE, format!("device {} has no channel {channel}", device.id)))?;
        let mut spec = ch.spec.clone();
        if !ch.external_bitrate && !ch.fixed_spec {
            spec += &format!(",bitrate={}", req.nominal);
            if let Some(d) = req.data {
                if !ch.fd_capable {
                    return Err((ERR_INVALID_PROTOCOL, format!("{} is not CAN FD capable", ch.label)));
                }
                spec += &format!(",dbitrate={d}");
            }
        }
        let bus = csu_bus::open(&spec).map_err(|e| (ERR_HARDWARE_NOT_RESPONDING, format!("{}: {e}", ch.label)))?;
        let hub = Arc::new(Hub {
            key: (device.id, channel),
            label: ch.label.clone(),
            bus: Mutex::new(bus),
            clients: Mutex::new(Vec::new()),
            running: AtomicBool::new(true),
            reader: Mutex::new(None),
            nominal: req.nominal,
            data: req.data,
            last_rx: Mutex::new(None),
            bus_off: AtomicBool::new(false),
            pending_tx: AtomicUsize::new(0),
        });
        let weak = Arc::downgrade(&hub);
        let handle = std::thread::Builder::new()
            .name(format!("csucan-{}-{}", device.id, channel))
            .spawn(move || reader_loop(weak))
            .map_err(|e| (ERR_HARDWARE_NOT_RESPONDING, e.to_string()))?;
        *hub.reader.lock().unwrap() = Some(handle);
        Ok(hub)
    }

    fn live_clients(&self) -> Vec<Arc<Client>> {
        let mut list = self.clients.lock().unwrap();
        list.retain(|w| w.strong_count() > 0);
        list.iter().filter_map(Weak::upgrade).collect()
    }

    /// Transmit and deliver the frame to the other clients on this channel.
    fn transmit(&self, frame: &Frame, from: i16) -> Result<(), (i16, String)> {
        let mut f = *frame;
        f.timestamp = csu_bus::host_time();
        self.pending_tx.fetch_add(1, Ordering::SeqCst);
        let sent = self.bus.lock().unwrap().send(&f);
        self.pending_tx.fetch_sub(1, Ordering::SeqCst);
        sent.map_err(|e| (ERR_MESSAGE_NOT_SENT, e.to_string()))?;
        // Deliver to every other client first, then send their replies, so
        // observers see the same order as on a real bus (RTS before CTS).
        let mut replies = Vec::new();
        for c in self.live_clients() {
            if c.id != from {
                replies.extend(c.on_frame(&f).into_iter().map(|r| (c.id, r)));
            }
        }
        for (id, reply) in replies {
            let _ = self.transmit(&reply, id);
        }
        Ok(())
    }

    fn stop(&self) {
        self.running.store(false, Ordering::SeqCst);
        if let Some(h) = self.reader.lock().unwrap().take() {
            if h.thread().id() != std::thread::current().id() {
                let _ = h.join();
            }
        }
    }
}

fn reader_loop(hub: Weak<Hub>) {
    loop {
        let Some(h) = hub.upgrade() else { return };
        if !h.running.load(Ordering::SeqCst) {
            return;
        }
        if h.pending_tx.load(Ordering::SeqCst) > 0 {
            std::thread::sleep(Duration::from_micros(100));
            continue;
        }
        let result = h.bus.lock().unwrap().recv(Duration::from_millis(1));
        match result {
            Ok(Some(f)) => {
                // Own transmissions are echoed in software; ignore driver echoes.
                if f.is_echo() {
                    continue;
                }
                if f.flags.has(FrameFlags::ERROR) {
                    continue;
                }
                *h.last_rx.lock().unwrap() = Some(Instant::now());
                let mut replies = Vec::new();
                for c in h.live_clients() {
                    replies.extend(c.on_frame(&f).into_iter().map(|r| (c.id, r)));
                }
                for (id, reply) in replies {
                    let _ = h.transmit(&reply, id);
                }
            }
            Ok(None) => {}
            Err(BusError::EndOfInput) => std::thread::sleep(Duration::from_millis(20)),
            Err(_) => {
                h.bus_off.store(true, Ordering::SeqCst);
                std::thread::sleep(Duration::from_millis(20));
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

struct J1939State {
    tp: Reassembler,
    claimed: Option<u8>,
    name: [u8; 8],
    interpacket: Duration,
    /// Outgoing RTS/CTS sessions waiting for CTS/EoMA, keyed by peer address.
    tx_waiters: HashMap<u8, Sender<[u8; 8]>>,
}

struct State {
    queue: VecDeque<Vec<u8>>,
    connected: bool,
    echo: bool,
    pass: bool,
    receive: bool,
    block_timeout: Option<Duration>,
    capacity: usize,
    j1939: J1939State,
    last_error: String,
}

pub struct Client {
    pub id: i16,
    pub protocol: Protocol,
    fd_flags: bool,
    hub: Arc<Hub>,
    state: Mutex<State>,
    cv: Condvar,
}

fn us_timestamp(ts: f64) -> [u8; 4] {
    (((ts * 1e6) as u64) as u32).to_be_bytes()
}

impl Client {
    fn push(st: &mut State, msg: Vec<u8>) {
        if st.queue.len() >= st.capacity {
            st.queue.pop_front(); // oldest dropped, like a full RX ring
        }
        st.queue.push_back(msg);
    }

    fn encode_can(&self, f: &Frame, echo: Option<bool>) -> Vec<u8> {
        let mut m = Vec::with_capacity(16 + f.len as usize);
        m.extend_from_slice(&us_timestamp(f.timestamp));
        if let Some(e) = echo {
            m.push(e as u8);
        }
        let mut kind = f.is_extended() as u8;
        if self.fd_flags {
            kind |= (f.is_fd() as u8) << 1 | (f.flags.has(FrameFlags::BRS) as u8) << 2;
        }
        m.push(kind);
        if f.is_extended() {
            m.extend_from_slice(&f.id.to_be_bytes());
        } else {
            m.extend_from_slice(&(f.id as u16).to_be_bytes());
        }
        m.extend_from_slice(f.payload());
        m
    }

    fn encode_j1939(ts: f64, echo: Option<bool>, pgn: u32, priority: u8, sa: u8, da: u8, data: &[u8]) -> Vec<u8> {
        let mut m = Vec::with_capacity(11 + data.len());
        m.extend_from_slice(&us_timestamp(ts));
        if let Some(e) = echo {
            m.push(e as u8);
        }
        m.extend_from_slice(&[pgn as u8, (pgn >> 8) as u8, (pgn >> 16) as u8, priority & 7, sa, da]);
        m.extend_from_slice(data);
        m
    }

    /// A frame arrived on the bus (or from a sibling client). Returns frames
    /// this client must send in response (CTS, EoMA, Address Claimed).
    fn on_frame(&self, f: &Frame) -> Vec<Frame> {
        let mut actions = Vec::new();
        {
            let mut st = self.state.lock().unwrap();
            if !st.connected {
                return actions;
            }
            let deliver = st.pass && st.receive;
            let echo_field = st.echo.then_some(false);
            match self.protocol {
                Protocol::Can => {
                    if deliver {
                        let m = self.encode_can(f, echo_field);
                        Self::push(&mut st, m);
                    }
                }
                Protocol::J1939 => {
                    if !f.is_extended() || f.flags.has(FrameFlags::RTR) {
                        return actions;
                    }
                    let id = J1939Id::decode(f.id);
                    let data = f.payload();
                    if (id.pgn == pgn::TP_CM || id.pgn == pgn::TP_DT) && !f.is_fd() {
                        let claimed = st.j1939.claimed;
                        if id.pgn == pgn::TP_CM && data.len() >= 8 {
                            match data[0] {
                                16 if Some(id.da) == claimed => {
                                    // RTS to our address: clear to send everything.
                                    let cts = [17, data[3], 1, 0xFF, 0xFF, data[5], data[6], data[7]];
                                    // From the claimed address (id.da) back to the originator (id.sa).
                                    actions.push(j1939_frame(7, pgn::TP_CM, id.sa, id.da, &cts));
                                }
                                17 | 19 | 255 => {
                                    // Replies to an RTS this client sent to id.sa.
                                    if let Some(tx) = st.j1939.tx_waiters.get(&id.sa) {
                                        let _ = tx.send(data[..8].try_into().unwrap());
                                    }
                                }
                                _ => {}
                            }
                        }
                        let mut events = Vec::new();
                        st.j1939.tp.process(f.timestamp, f.channel, id, data, &mut events);
                        for e in events {
                            if let TpEvent::Complete(m) = e {
                                if Some(m.da) == claimed && m.da != GLOBAL {
                                    let size = m.data.len() as u16;
                                    let packets = size.div_ceil(7) as u8;
                                    let eoma = [19, size as u8, (size >> 8) as u8, packets, 0xFF, m.pgn as u8, (m.pgn >> 8) as u8, (m.pgn >> 16) as u8];
                                    actions.push(j1939_frame(7, pgn::TP_CM, m.sa, m.da, &eoma));
                                }
                                if deliver {
                                    let msg = Self::encode_j1939(m.timestamp, echo_field, m.pgn, m.priority, m.sa, m.da, &m.data);
                                    Self::push(&mut st, msg);
                                }
                            }
                        }
                    } else {
                        if id.pgn == pgn::REQUEST && data.len() >= 3 {
                            let requested = data[0] as u32 | (data[1] as u32) << 8 | (data[2] as u32) << 16;
                            if let Some(addr) = st.j1939.claimed {
                                if requested == pgn::ADDRESS_CLAIMED && (id.da == GLOBAL || id.da == addr) {
                                    let name = st.j1939.name;
                                    actions.push(j1939_frame(6, pgn::ADDRESS_CLAIMED, GLOBAL, addr, &name));
                                }
                            }
                        }
                        if deliver {
                            let msg = Self::encode_j1939(f.timestamp, echo_field, id.pgn, id.priority, id.sa, id.da, data);
                            Self::push(&mut st, msg);
                        }
                    }
                }
            }
            self.cv.notify_all();
        }
        actions
    }

    fn record_error(&self, text: String) {
        self.state.lock().unwrap().last_error = text;
    }

    pub fn read(&self, buf: &mut [u8], block: bool) -> i16 {
        let mut st = self.state.lock().unwrap();
        if block {
            let deadline = st.block_timeout.map(|t| Instant::now() + t);
            while st.connected && st.queue.is_empty() {
                match deadline {
                    None => st = self.cv.wait(st).unwrap(),
                    Some(d) => {
                        let now = Instant::now();
                        if now >= d {
                            return 0;
                        }
                        st = self.cv.wait_timeout(st, d - now).unwrap().0;
                    }
                }
            }
        }
        if !st.connected {
            return -ERR_CLIENT_DISCONNECTED;
        }
        let Some(msg) = st.queue.pop_front() else { return 0 };
        if msg.len() > buf.len() {
            return -ERR_MESSAGE_TOO_LONG;
        }
        buf[..msg.len()].copy_from_slice(&msg);
        msg.len() as i16
    }

    pub fn send(self: &Arc<Self>, msg: &[u8], block: bool) -> i16 {
        let result = match self.protocol {
            Protocol::Can => self.send_can(msg),
            Protocol::J1939 => self.send_j1939(msg, block),
        };
        match result {
            Ok(()) => NO_ERRORS,
            Err((code, text)) => {
                self.record_error(text);
                code
            }
        }
    }

    fn send_can(&self, msg: &[u8]) -> Result<(), (i16, String)> {
        let kind = *msg.first().ok_or((ERR_MESSAGE_TOO_LONG, "empty CAN message".to_string()))?;
        let extended = kind & 1 != 0;
        let id_len = if extended { 4 } else { 2 };
        if msg.len() < 1 + id_len {
            return Err((ERR_MESSAGE_TOO_LONG, "CAN message shorter than its identifier".into()));
        }
        let id = msg[1..1 + id_len].iter().fold(0u32, |a, b| a << 8 | *b as u32);
        let data = &msg[1 + id_len..];
        let fd = data.len() > 8 || kind & 2 != 0;
        if data.len() > 64 || (fd && self.hub.data.is_none()) {
            return Err((ERR_MESSAGE_TOO_LONG, "CAN FD payload on a classic CAN connection".into()));
        }
        let mut f = Frame::new(id & if extended { 0x1FFF_FFFF } else { 0x7FF }, extended, data);
        f.flags.set(FrameFlags::FD, fd);
        f.flags.set(FrameFlags::BRS, fd);
        self.hub.transmit(&f, self.id)?;
        self.echo_back(|c| c.encode_can(&f, Some(true)));
        Ok(())
    }

    fn echo_back(&self, make: impl FnOnce(&Self) -> Vec<u8>) {
        let mut st = self.state.lock().unwrap();
        if st.echo && st.pass && st.receive {
            let m = make(self);
            Self::push(&mut st, m);
            self.cv.notify_all();
        }
    }

    fn send_j1939(self: &Arc<Self>, msg: &[u8], block: bool) -> Result<(), (i16, String)> {
        if msg.len() < 6 {
            return Err((ERR_MESSAGE_TOO_LONG, "J1939 message shorter than its 6-byte header".into()));
        }
        let pgn = msg[0] as u32 | (msg[1] as u32) << 8 | (msg[2] as u32) << 16;
        let how = msg[3];
        let (sa, da) = (msg[4], msg[5]);
        let data = msg[6..].to_vec();
        if data.len() > csu_j1939::tp::MAX_TP_SIZE {
            return Err((ERR_MESSAGE_TOO_LONG, format!("{} bytes exceeds the J1939 transport limit", data.len())));
        }
        let priority = how & 7;
        let echo = Self::encode_j1939(csu_bus::host_time(), Some(true), pgn, priority, sa, da, &data);
        if data.len() <= 8 {
            self.hub.transmit(&j1939_frame(priority, pgn, da, sa, &data), self.id)?;
            self.echo_back(|_| echo);
            return Ok(());
        }
        let bam = da == GLOBAL || how & 0x80 != 0;
        let me = self.clone();
        let work = move || {
            let r = if bam { me.send_bam(pgn, sa, &data) } else { me.send_rts_cts(pgn, sa, da, &data) };
            if r.is_ok() {
                me.echo_back(|_| echo);
            }
            r
        };
        if block {
            work()
        } else {
            std::thread::spawn(move || {
                let _ = work();
            });
            Ok(())
        }
    }

    fn interpacket(&self) -> Duration {
        self.state.lock().unwrap().j1939.interpacket
    }

    fn send_bam(&self, target: u32, sa: u8, data: &[u8]) -> Result<(), (i16, String)> {
        let size = data.len() as u16;
        let packets = size.div_ceil(7) as u8;
        let cm = [32, size as u8, (size >> 8) as u8, packets, 0xFF, target as u8, (target >> 8) as u8, (target >> 16) as u8];
        self.hub.transmit(&j1939_frame(7, pgn::TP_CM, GLOBAL, sa, &cm), self.id)?;
        let gap = self.interpacket();
        for (i, chunk) in data.chunks(7).enumerate() {
            std::thread::sleep(gap);
            self.hub.transmit(&j1939_frame(7, pgn::TP_DT, GLOBAL, sa, &dt_payload(i as u8 + 1, chunk)), self.id)?;
        }
        Ok(())
    }

    fn send_rts_cts(&self, target: u32, sa: u8, da: u8, data: &[u8]) -> Result<(), (i16, String)> {
        let (tx, rx): (Sender<[u8; 8]>, Receiver<[u8; 8]>) = channel();
        self.state.lock().unwrap().j1939.tx_waiters.insert(da, tx);
        let result = (|| {
            let size = data.len() as u16;
            let packets = size.div_ceil(7) as u8;
            let rts = [16, size as u8, (size >> 8) as u8, packets, 0xFF, target as u8, (target >> 8) as u8, (target >> 16) as u8];
            self.hub.transmit(&j1939_frame(7, pgn::TP_CM, da, sa, &rts), self.id)?;
            let chunks: Vec<&[u8]> = data.chunks(7).collect();
            let mut timeout = T3;
            loop {
                let cm = rx.recv_timeout(timeout).map_err(|_| (ERR_MESSAGE_NOT_SENT, "RTS/CTS timeout (no CTS)".to_string()))?;
                match cm[0] {
                    17 => {
                        let (count, next) = (cm[1] as usize, cm[2] as usize);
                        if count == 0 {
                            timeout = T4; // hold: wait for the next CTS
                            continue;
                        }
                        for seq in next..next + count {
                            let chunk = chunks.get(seq.wrapping_sub(1)).ok_or((ERR_MESSAGE_NOT_SENT, "CTS for a packet out of range".to_string()))?;
                            self.hub.transmit(&j1939_frame(7, pgn::TP_DT, da, sa, &dt_payload(seq as u8, chunk)), self.id)?;
                        }
                        timeout = T3;
                    }
                    19 => return Ok(()),
                    _ => return Err((ERR_MESSAGE_NOT_SENT, format!("connection aborted by {da} (reason {})", cm[1]))),
                }
            }
        })();
        self.state.lock().unwrap().j1939.tx_waiters.remove(&da);
        result
    }

    pub fn command(&self, cmd: i16, buf: &[u8]) -> i16 {
        let mut st = self.state.lock().unwrap();
        match cmd {
            CMD_RESET_DEVICE | CMD_FLUSH_TX_RX_BUFFERS => {
                st.queue.clear();
            }
            CMD_SET_ALL_FILTERS_TO_PASS => st.pass = true,
            CMD_SET_ALL_FILTERS_TO_DISCARD => st.pass = false,
            CMD_ECHO_TRANSMITTED_MESSAGES => st.echo = buf.first().copied().unwrap_or(0) != 0,
            CMD_SET_MESSAGE_RECEIVE => st.receive = buf.first().copied().unwrap_or(1) != 0,
            CMD_SET_J1939_INTERPACKET_TIME if self.protocol == Protocol::J1939 => {
                let ms = buf.iter().take(4).enumerate().fold(0u32, |a, (i, b)| a | (*b as u32) << (8 * i));
                st.j1939.interpacket = Duration::from_millis(ms as u64);
            }
            CMD_SET_BLOCK_TIMEOUT => {
                let ms = buf.iter().take(4).enumerate().fold(0u32, |a, (i, b)| a | (*b as u32) << (8 * i));
                st.block_timeout = (ms > 0).then(|| Duration::from_millis(ms as u64));
            }
            CMD_PROTECT_J1939_ADDRESS if self.protocol == Protocol::J1939 => {
                if buf.len() < 9 {
                    return ERR_INVALID_COMMAND;
                }
                let addr = buf[0];
                if addr >= 254 {
                    return ERR_ADDRESS_CLAIM_FAILED;
                }
                st.j1939.claimed = Some(addr);
                st.j1939.name.copy_from_slice(&buf[1..9]);
                let name = st.j1939.name;
                drop(st);
                return match self.hub.transmit(&j1939_frame(6, pgn::ADDRESS_CLAIMED, GLOBAL, addr, &name), self.id) {
                    Ok(()) => NO_ERRORS,
                    Err((_, text)) => {
                        self.record_error(text);
                        ERR_COULD_NOT_TX_ADDRESS_CLAIMED
                    }
                };
            }
            CMD_RELEASE_J1939_ADDRESS if self.protocol == Protocol::J1939 => st.j1939.claimed = None,
            _ => return ERR_COMMAND_NOT_SUPPORTED,
        }
        NO_ERRORS
    }
}

fn j1939_frame(priority: u8, pgn: u32, da: u8, sa: u8, data: &[u8]) -> Frame {
    Frame::new(J1939Id { priority, pgn, sa, da }.encode(), true, data)
}

fn dt_payload(seq: u8, chunk: &[u8]) -> Vec<u8> {
    let mut p = vec![0xFF; 8];
    p[0] = seq;
    p[1..1 + chunk.len()].copy_from_slice(chunk);
    p
}

// ---------------------------------------------------------------------------
// Driver
// ---------------------------------------------------------------------------

#[derive(Default)]
pub struct Driver {
    clients: Mutex<BTreeMap<i16, Arc<Client>>>,
    hubs: Mutex<HashMap<(i16, u8), Weak<Hub>>>,
    devices: Mutex<Option<BTreeMap<i16, Device>>>,
    disallow: AtomicBool,
    last_error: Mutex<String>,
}

pub fn driver() -> &'static Driver {
    static D: OnceLock<Driver> = OnceLock::new();
    D.get_or_init(Driver::default)
}

impl Driver {
    pub fn refresh_devices(&self) -> BTreeMap<i16, Device> {
        let d = devices::discover();
        *self.devices.lock().unwrap() = Some(d.clone());
        d
    }

    fn device(&self, id: i16) -> Option<Device> {
        let mut cache = self.devices.lock().unwrap();
        if cache.as_ref().is_none_or(|d| !d.contains_key(&id)) {
            *cache = Some(devices::discover());
        }
        cache.as_ref().and_then(|d| d.get(&id).cloned())
    }

    pub fn last_error(&self) -> String {
        self.last_error.lock().unwrap().clone()
    }

    fn fail(&self, code: i16, text: String) -> i16 {
        *self.last_error.lock().unwrap() = text;
        code
    }

    pub fn connect(&self, device_id: i16, protocol: &str, rx_capacity: usize) -> i16 {
        if self.disallow.load(Ordering::SeqCst) {
            return self.fail(ERR_CONNECT_NOT_ALLOWED, "further connections disallowed".into());
        }
        let req = match parse_protocol(protocol) {
            Ok(r) => r,
            Err(code) => return self.fail(code, format!("unsupported protocol string \"{protocol}\" (CAN and J1939 are supported)")),
        };
        let Some(device) = self.device(device_id) else {
            return self.fail(ERR_INVALID_DEVICE, format!("device {device_id} is not attached"));
        };
        let key = (device_id, req.channel);
        let hub = {
            let mut hubs = self.hubs.lock().unwrap();
            match hubs.get(&key).and_then(Weak::upgrade) {
                Some(h) => {
                    if h.nominal != req.nominal || h.data != req.data {
                        return self.fail(ERR_DEVICE_IN_USE, format!("{} is already open at a different bit rate", h.label));
                    }
                    h
                }
                None => match Hub::open(&device, req.channel, &req) {
                    Ok(h) => {
                        hubs.insert(key, Arc::downgrade(&h));
                        h
                    }
                    Err((code, text)) => return self.fail(code, text),
                },
            }
        };
        let mut clients = self.clients.lock().unwrap();
        let Some(id) = (1..MAX_CLIENTS as i16).find(|i| !clients.contains_key(i)) else {
            return self.fail(ERR_CLIENT_AREA_FULL, "all client slots are in use".into());
        };
        let client = Arc::new(Client {
            id,
            protocol: req.protocol,
            fd_flags: req.fd_flags,
            hub: hub.clone(),
            state: Mutex::new(State {
                queue: VecDeque::new(),
                connected: true,
                echo: false,
                pass: false,
                receive: true,
                block_timeout: None,
                capacity: rx_capacity.clamp(64, 1 << 20),
                j1939: J1939State {
                    tp: Reassembler::default(),
                    claimed: None,
                    name: [0; 8],
                    interpacket: Duration::from_millis(50),
                    tx_waiters: HashMap::new(),
                },
                last_error: String::new(),
            }),
            cv: Condvar::new(),
        });
        hub.clients.lock().unwrap().push(Arc::downgrade(&client));
        clients.insert(id, client);
        id
    }

    pub fn client(&self, id: i16) -> Option<Arc<Client>> {
        self.clients.lock().unwrap().get(&id).cloned()
    }

    pub fn disconnect(&self, id: i16) -> i16 {
        let Some(client) = self.clients.lock().unwrap().remove(&id) else { return ERR_INVALID_CLIENT_ID };
        {
            let mut st = client.state.lock().unwrap();
            st.connected = false;
            client.cv.notify_all();
        }
        let hub = client.hub.clone();
        drop(client);
        if hub.live_clients().is_empty() {
            self.hubs.lock().unwrap().remove(&hub.key);
            hub.stop();
        }
        NO_ERRORS
    }

    pub fn disallow_connections(&self) {
        self.disallow.store(true, Ordering::SeqCst);
    }

    /// RP1210C GetHardwareStatus layout (16+ bytes).
    pub fn hardware_status(&self, id: i16, out: &mut [u8]) -> i16 {
        let Some(c) = self.client(id) else { return ERR_INVALID_CLIENT_ID };
        let clients: Vec<Arc<Client>> = self.clients.lock().unwrap().values().cloned().collect();
        let on_hub: Vec<&Arc<Client>> = clients.iter().filter(|x| Arc::ptr_eq(&x.hub, &c.hub)).collect();
        let traffic = c.hub.last_rx.lock().unwrap().is_some_and(|t| t.elapsed() < Duration::from_secs(1));
        let bus_off = c.hub.bus_off.load(Ordering::SeqCst);
        let mut s = [0u8; 18];
        s[0] = 0x01 | 0x04; // device located, external
        s[1] = on_hub.len() as u8;
        let j1939 = on_hub.iter().filter(|x| x.protocol == Protocol::J1939).count() as u8;
        let can = on_hub.iter().filter(|x| x.protocol == Protocol::Can).count() as u8;
        s[2] = (j1939 > 0) as u8 | (traffic as u8) << 1 | (bus_off as u8) << 2;
        s[3] = j1939;
        s[6] = (can > 0) as u8 | (traffic as u8) << 1 | (bus_off as u8) << 2;
        s[7] = can;
        let n = out.len().min(s.len());
        out[..n].copy_from_slice(&s[..n]);
        NO_ERRORS
    }

    pub fn client_label(&self, id: i16) -> Option<String> {
        self.client(id).map(|c| c.hub.label.clone())
    }

    pub fn client_error(&self, id: i16) -> Option<String> {
        self.client(id).map(|c| c.state.lock().unwrap().last_error.clone())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn protocol_strings() {
        let r = parse_protocol("J1939:Baud=250,Channel=4").unwrap();
        assert_eq!((r.protocol, r.channel, r.nominal, r.data), (Protocol::J1939, 4, 250_000, None));
        let r = parse_protocol("CAN:Channel=2,Baud=500/2000,FDFlags=1").unwrap();
        assert_eq!((r.nominal, r.data, r.fd_flags, r.channel), (500_000, Some(2_000_000), true, 2));
        assert_eq!(parse_protocol("CAN").unwrap().nominal, 250_000);
        assert_eq!(parse_protocol("J1939:Baud=Auto").unwrap().nominal, 250_000);
        assert_eq!(parse_protocol("J1708"), Err(ERR_INVALID_PROTOCOL));
        assert_eq!(parse_protocol("CAN:Channel=0"), Err(ERR_INVALID_PROTOCOL));
    }
}
