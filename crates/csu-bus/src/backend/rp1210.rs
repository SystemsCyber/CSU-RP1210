//! TMC RP1210 backend (Windows).
//!
//! Opens a raw `CAN` client so the J1939/ISO-TP stacks in this project see
//! every frame (the driver's J1939 client would reassemble transport sessions
//! and hide the wire traffic, which is wrong for forensic capture).
//!
//! RP1210 DLLs must match the process bitness. A 64-bit build can load 64-bit
//! vendor DLLs from `System32` (e.g. PEAKRP32 x64). 32-bit-only DLLs (e.g. some
//! DG DPA5 installs live only in `SysWOW64`) need the planned
//! `csu-rp1210-bridge` helper built for `i686-pc-windows-msvc`.

use crate::{Bus, BusError, BusInfo, Frame, FrameFlags};
use libloading::Library;
use serde::Serialize;
use std::collections::HashMap;
use std::ffi::CString;
use std::os::raw::{c_char, c_long, c_short, c_void};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const BUFFER_SIZE: usize = 8192;
const CMD_SET_ALL_FILTERS_TO_PASS: c_short = 3;
const CMD_ECHO_TRANSMITTED_MESSAGES: c_short = 16;

type FnClientConnect = unsafe extern "system" fn(*mut c_void, c_short, *const c_char, c_long, c_long, c_short) -> c_short;
type FnClientDisconnect = unsafe extern "system" fn(c_short) -> c_short;
type FnSendMessage = unsafe extern "system" fn(c_short, *const c_char, c_short, c_short, c_short) -> c_short;
type FnReadMessage = unsafe extern "system" fn(c_short, *mut c_char, c_short, c_short) -> c_short;
type FnSendCommand = unsafe extern "system" fn(c_short, c_short, *mut c_char, c_short) -> c_short;
type FnGetErrorMsg = unsafe extern "system" fn(c_short, *mut c_char) -> c_short;

struct Api {
    _lib: Library,
    connect: FnClientConnect,
    disconnect: FnClientDisconnect,
    send: FnSendMessage,
    read: FnReadMessage,
    command: FnSendCommand,
    error_msg: Option<FnGetErrorMsg>,
}

impl Api {
    fn load(api_name: &str) -> Result<Self, BusError> {
        let file = format!("{api_name}.dll");
        // SAFETY: loading a vendor RP1210 DLL; RP1210 defines no special load rules.
        unsafe {
            let lib = Library::new(&file).map_err(|e| {
                let hint = match dll_bitness(api_name) {
                    (false, true) if cfg!(target_pointer_width = "64") => {
                        " (only a 32-bit DLL is installed; use a 32-bit build or the RP1210 bridge)"
                    }
                    _ => "",
                };
                BusError::Library(format!("{file}: {e}{hint}"))
            })?;
            macro_rules! sym {
                ($name:literal) => {
                    *lib.get($name).map_err(|e| BusError::Library(format!("{file}: {e}")))?
                };
            }
            Ok(Api {
                connect: sym!(b"RP1210_ClientConnect\0"),
                disconnect: sym!(b"RP1210_ClientDisconnect\0"),
                send: sym!(b"RP1210_SendMessage\0"),
                read: sym!(b"RP1210_ReadMessage\0"),
                command: sym!(b"RP1210_SendCommand\0"),
                error_msg: lib.get::<FnGetErrorMsg>(b"RP1210_GetErrorMsg\0").ok().map(|s| *s),
                _lib: lib,
            })
        }
    }

    fn text(&self, code: c_short) -> String {
        let Some(f) = self.error_msg else { return format!("RP1210 code {code}") };
        let mut buf = [0 as c_char; 81];
        // SAFETY: RP1210 specifies an 80-character description buffer.
        if unsafe { f(code, buf.as_mut_ptr()) } != 0 {
            return format!("RP1210 code {code}");
        }
        // SAFETY: NUL-terminated by the driver (buffer has a spare byte).
        unsafe { std::ffi::CStr::from_ptr(buf.as_ptr()) }.to_string_lossy().into_owned()
    }
}

pub struct Rp1210 {
    api: Api,
    client: c_short,
    name: String,
    bitrate: u32,
    /// Microseconds per timestamp count (vendor INI `TimeStampWeight`).
    ts_weight_us: f64,
    clock_offset: Option<f64>,
    buf: Box<[u8; BUFFER_SIZE]>,
}

impl Rp1210 {
    /// `api_name` is the RP1210 implementation name from `RP121032.ini`
    /// (e.g. `PEAKRP32`), `device` the vendor device ID.
    pub fn open(api_name: &str, device: i16, bitrate: u32) -> Result<Self, BusError> {
        let api = Api::load(api_name)?;
        let protocol = CString::new(format!("CAN:Baud={}", bitrate / 1000)).expect("ascii");
        // SAFETY: arguments follow the RP1210 C prototype; HWND may be null.
        let client = unsafe {
            (api.connect)(std::ptr::null_mut(), device, protocol.as_ptr(), BUFFER_SIZE as c_long, BUFFER_SIZE as c_long, 0)
        };
        if !(0..128).contains(&client) {
            return Err(BusError::Driver { call: "RP1210_ClientConnect", code: client as i64, text: api.text(client) });
        }
        let mut echo_on = [1 as c_char];
        // SAFETY: one-byte command buffer as defined for command 16.
        unsafe {
            (api.command)(CMD_ECHO_TRANSMITTED_MESSAGES, client, echo_on.as_mut_ptr(), 1);
        }
        // SAFETY: command 3 takes no buffer.
        let rc = unsafe { (api.command)(CMD_SET_ALL_FILTERS_TO_PASS, client, std::ptr::null_mut(), 0) };
        if rc != 0 {
            // SAFETY: client is a valid connected client ID.
            unsafe { (api.disconnect)(client) };
            return Err(BusError::Driver { call: "RP1210_SendCommand(SetAllFiltersToPass)", code: rc as i64, text: api.text(rc) });
        }
        let ts_weight_us = vendor_ini(api_name)
            .and_then(|ini| ini.get("VendorInformation").and_then(|s| s.get("timestampweight")).cloned())
            .and_then(|v| v.parse().ok())
            .unwrap_or(1000.0);
        Ok(Rp1210 {
            api,
            client,
            name: format!("{api_name}:{device}"),
            bitrate,
            ts_weight_us,
            clock_offset: None,
            buf: Box::new([0u8; BUFFER_SIZE]),
        })
    }

    fn try_read(&mut self) -> Result<Option<Frame>, BusError> {
        // SAFETY: buffer is BUFFER_SIZE bytes; non-blocking read.
        let n = unsafe { (self.api.read)(self.client, self.buf.as_mut_ptr() as *mut c_char, BUFFER_SIZE as c_short, 0) };
        if n == 0 {
            return Ok(None);
        }
        if n < 0 {
            let code = -n;
            return Err(BusError::Driver { call: "RP1210_ReadMessage", code: code as i64, text: self.api.text(code) });
        }
        let raw = &self.buf[..n as usize];
        let Some(mut frame) = parse_can_message(raw, true) else { return Ok(None) };
        let ticks = u32::from_be_bytes([raw[0], raw[1], raw[2], raw[3]]) as f64;
        let dev = ticks * self.ts_weight_us * 1e-6;
        let off = *self.clock_offset.get_or_insert_with(|| crate::host_time() - dev);
        frame.timestamp = dev + off;
        Ok(Some(frame))
    }
}

/// Parse an RP1210 CAN-protocol receive buffer:
/// `ts[4 BE] | echo[1] (if echo enabled) | type[1] (0 std, 1 ext) | id[2 or 4 BE] | data`.
pub fn parse_can_message(raw: &[u8], echo_enabled: bool) -> Option<Frame> {
    let mut i = 4;
    let echo = if echo_enabled {
        i += 1;
        *raw.get(4)? != 0
    } else {
        false
    };
    let extended = *raw.get(i)? != 0;
    i += 1;
    let id = if extended {
        let b = raw.get(i..i + 4)?;
        i += 4;
        u32::from_be_bytes([b[0], b[1], b[2], b[3]]) & 0x1FFF_FFFF
    } else {
        let b = raw.get(i..i + 2)?;
        i += 2;
        u16::from_be_bytes([b[0], b[1]]) as u32 & 0x7FF
    };
    let data = raw.get(i..)?;
    let len = data.len().min(8);
    let mut f = Frame::new(id, extended, &data[..len]);
    f.flags.set(FrameFlags::TX_ECHO, echo);
    Some(f)
}

impl Bus for Rp1210 {
    fn recv(&mut self, timeout: Duration) -> Result<Option<Frame>, BusError> {
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(f) = self.try_read()? {
                return Ok(Some(f));
            }
            if Instant::now() >= deadline {
                return Ok(None);
            }
            std::thread::sleep(Duration::from_micros(500));
        }
    }

    fn send(&mut self, frame: &Frame) -> Result<(), BusError> {
        if frame.is_fd() || frame.len > 8 {
            return Err(BusError::Unsupported("RP1210 C has no CAN FD; use pcan: or socketcan:"));
        }
        let mut msg = Vec::with_capacity(13);
        if frame.is_extended() {
            msg.push(1);
            msg.extend_from_slice(&frame.id.to_be_bytes());
        } else {
            msg.push(0);
            msg.extend_from_slice(&(frame.id as u16).to_be_bytes());
        }
        msg.extend_from_slice(frame.payload());
        // SAFETY: message buffer outlives the blocking call.
        let rc = unsafe { (self.api.send)(self.client, msg.as_ptr() as *const c_char, msg.len() as c_short, 0, 1) };
        if rc != 0 {
            return Err(BusError::Driver { call: "RP1210_SendMessage", code: rc as i64, text: self.api.text(rc) });
        }
        Ok(())
    }

    fn info(&self) -> BusInfo {
        BusInfo { backend: "rp1210", channel: self.name.clone(), fd_capable: false, bitrate: Some(self.bitrate), offline: false }
    }
}

impl Drop for Rp1210 {
    fn drop(&mut self) {
        // SAFETY: client ID came from a successful ClientConnect.
        unsafe { (self.api.disconnect)(self.client) };
    }
}

// --- Installed-driver discovery -------------------------------------------

type Ini = HashMap<String, HashMap<String, String>>;

/// Minimal INI reader: section names keep their case, keys are lower-cased.
pub fn parse_ini(text: &str) -> Ini {
    let mut out: Ini = HashMap::new();
    let mut section = String::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with(';') || line.starts_with('#') {
            continue;
        }
        if let Some(s) = line.strip_prefix('[').and_then(|l| l.strip_suffix(']')) {
            section = s.trim().to_string();
            continue;
        }
        if let Some((k, v)) = line.split_once('=') {
            out.entry(section.clone()).or_default().insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
        }
    }
    out
}

fn windir() -> PathBuf {
    std::env::var_os("WINDIR").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(r"C:\Windows"))
}

fn vendor_ini(api_name: &str) -> Option<Ini> {
    let text = std::fs::read(windir().join(format!("{api_name}.ini"))).ok()?;
    Some(parse_ini(&String::from_utf8_lossy(&text)))
}

/// PE machine type of a DLL: `Some(true)` for x64, `Some(false)` for x86.
fn pe_is_64bit(path: &Path) -> Option<bool> {
    let bytes = std::fs::read(path).ok()?;
    let pe = u32::from_le_bytes(bytes.get(0x3C..0x40)?.try_into().ok()?) as usize;
    if bytes.get(pe..pe + 4)? != b"PE\0\0" {
        return None;
    }
    match u16::from_le_bytes(bytes.get(pe + 4..pe + 6)?.try_into().ok()?) {
        0x8664 | 0xAA64 => Some(true),
        0x014C => Some(false),
        _ => None,
    }
}

/// (64-bit DLL present, 32-bit DLL present) for an RP1210 implementation.
pub fn dll_bitness(api_name: &str) -> (bool, bool) {
    let mut has64 = false;
    let mut has32 = false;
    for dir in ["System32", "SysWOW64", ""] {
        let p = windir().join(dir).join(format!("{api_name}.dll"));
        match pe_is_64bit(&p) {
            Some(true) => has64 = true,
            Some(false) => has32 = true,
            None => {}
        }
    }
    (has64, has32)
}

#[derive(Debug, Clone, Serialize)]
pub struct Rp1210Device {
    pub id: i16,
    pub name: String,
    pub description: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Rp1210Implementation {
    pub api_name: String,
    pub vendor: String,
    pub rp1210_version: String,
    pub devices: Vec<Rp1210Device>,
    pub protocols: Vec<String>,
    pub dll_64bit: bool,
    pub dll_32bit: bool,
    /// True when this process can load the DLL directly.
    pub loadable: bool,
}

/// Installed RP1210 implementations listed in `%WINDIR%\RP121032.ini`.
pub fn list_implementations() -> Vec<Rp1210Implementation> {
    let Ok(text) = std::fs::read(windir().join("RP121032.ini")) else { return vec![] };
    let main = parse_ini(&String::from_utf8_lossy(&text));
    let Some(list) = main.get("RP1210Support").and_then(|s| s.get("apiimplementations")) else { return vec![] };
    list.split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|api| {
            let ini = vendor_ini(api).unwrap_or_default();
            let vi = ini.get("VendorInformation");
            let mut devices: Vec<Rp1210Device> = ini
                .iter()
                .filter(|(k, _)| k.starts_with("DeviceInformation"))
                .filter_map(|(_, s)| {
                    Some(Rp1210Device {
                        id: s.get("deviceid")?.parse().ok()?,
                        name: s.get("devicename").cloned().unwrap_or_default(),
                        description: s.get("devicedescription").cloned().unwrap_or_default(),
                    })
                })
                .collect();
            devices.sort_by_key(|d| d.id);
            let mut protocols: Vec<String> = ini
                .iter()
                .filter(|(k, _)| k.starts_with("ProtocolInformation"))
                .filter_map(|(_, s)| s.get("protocolstring").cloned())
                .collect();
            protocols.sort();
            protocols.dedup();
            let (dll_64bit, dll_32bit) = dll_bitness(api);
            let loadable = if cfg!(target_pointer_width = "64") { dll_64bit } else { dll_32bit };
            Rp1210Implementation {
                api_name: api.to_string(),
                vendor: vi.and_then(|v| v.get("name")).cloned().unwrap_or_default(),
                rp1210_version: vi.and_then(|v| v.get("rp1210")).cloned().unwrap_or_default(),
                devices,
                protocols,
                dll_64bit,
                dll_32bit,
                loadable,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_can_buffer_with_echo() {
        // ts=0x00000010, echo=0, ext, id=0x18FEF100, 8 data bytes
        let raw = [0, 0, 0, 0x10, 0, 1, 0x18, 0xFE, 0xF1, 0x00, 1, 2, 3, 4, 5, 6, 7, 8];
        let f = parse_can_message(&raw, true).unwrap();
        assert_eq!(f.id, 0x18FEF100);
        assert!(f.is_extended() && !f.is_echo());
        assert_eq!(f.payload(), &[1, 2, 3, 4, 5, 6, 7, 8]);
        let std_raw = [0, 0, 0, 1, 1, 0, 0x07, 0xDF, 0x02, 0x01, 0x0D];
        let s = parse_can_message(&std_raw, true).unwrap();
        assert_eq!(s.id, 0x7DF);
        assert!(s.is_echo() && !s.is_extended());
    }

    #[test]
    fn ini_parsing() {
        let ini = parse_ini("[VendorInformation]\nName=PEAK\nTimeStampWeight=1000\n; c\n[DeviceInformation1]\nDeviceID=1\n");
        assert_eq!(ini["VendorInformation"]["timestampweight"], "1000");
        assert_eq!(ini["DeviceInformation1"]["deviceid"], "1");
    }
}
