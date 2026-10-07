//! PEAK-System PCAN-Basic backend (Windows `PCANBasic.dll`, Linux `libpcanbasic.so`).
//!
//! This is the native path for PEAK CAN FD adapters. PEAK's RP1210 driver
//! (`PEAKRP32`) implements RP1210 C, which has no CAN FD, so J1939-22 and
//! J1939-91C traffic needs this backend on Windows. On Linux, PEAK USB
//! adapters are also available through SocketCAN (`peak_usb` kernel driver).

use crate::{dlc_to_len, len_to_dlc, Bus, BusError, BusInfo, Frame, FrameFlags, MAX_DATA};
use libloading::Library;
use std::ffi::CString;
use std::time::{Duration, Instant};

type Handle = u16;
type Status = u32;

const PCAN_ERROR_OK: Status = 0x0000_0000;
const PCAN_ERROR_QRCVEMPTY: Status = 0x0000_0020;

const MSG_RTR: u8 = 0x01;
const MSG_EXTENDED: u8 = 0x02;
const MSG_FD: u8 = 0x04;
const MSG_BRS: u8 = 0x08;
const MSG_ESI: u8 = 0x10;
const MSG_ECHO: u8 = 0x20;
const MSG_ERRFRAME: u8 = 0x40;
const MSG_STATUS: u8 = 0x80;

#[repr(C)]
#[derive(Default)]
struct TPCANMsg {
    id: u32,
    msgtype: u8,
    len: u8,
    data: [u8; 8],
}

#[repr(C)]
#[derive(Default)]
struct TPCANTimestamp {
    millis: u32,
    millis_overflow: u16,
    micros: u16,
}

#[repr(C)]
struct TPCANMsgFD {
    id: u32,
    msgtype: u8,
    dlc: u8,
    data: [u8; MAX_DATA],
}

type FnInitialize = unsafe extern "system" fn(Handle, u16, u8, u32, u16) -> Status;
type FnInitializeFd = unsafe extern "system" fn(Handle, *const std::os::raw::c_char) -> Status;
type FnUninitialize = unsafe extern "system" fn(Handle) -> Status;
type FnRead = unsafe extern "system" fn(Handle, *mut TPCANMsg, *mut TPCANTimestamp) -> Status;
type FnReadFd = unsafe extern "system" fn(Handle, *mut TPCANMsgFD, *mut u64) -> Status;
type FnWrite = unsafe extern "system" fn(Handle, *mut TPCANMsg) -> Status;
type FnWriteFd = unsafe extern "system" fn(Handle, *mut TPCANMsgFD) -> Status;
type FnErrorText = unsafe extern "system" fn(Status, u16, *mut std::os::raw::c_char) -> Status;
type FnGetValue = unsafe extern "system" fn(Handle, u8, *mut std::os::raw::c_void, u32) -> Status;

const PCAN_CHANNEL_CONDITION: u8 = 0x0D;
const PCAN_HARDWARE_NAME: u8 = 0x0E;
const PCAN_CHANNEL_FEATURES: u8 = 0x16;
const PCAN_CHANNEL_AVAILABLE: u32 = 0x01;
const PCAN_CHANNEL_OCCUPIED: u32 = 0x02;
const PCAN_CHANNEL_PCANVIEW: u32 = 0x03;
const FEATURE_FD_CAPABLE: u32 = 0x01;

struct Api {
    _lib: Library,
    initialize: FnInitialize,
    initialize_fd: FnInitializeFd,
    uninitialize: FnUninitialize,
    read: FnRead,
    read_fd: FnReadFd,
    write: FnWrite,
    write_fd: FnWriteFd,
    error_text: FnErrorText,
    get_value: FnGetValue,
}

#[cfg(windows)]
const LIB_NAME: &str = "PCANBasic.dll";
#[cfg(not(windows))]
const LIB_NAME: &str = "libpcanbasic.so";

impl Api {
    fn load() -> Result<Self, BusError> {
        // SAFETY: loading a vendor DLL runs its initializers; PCANBasic is the
        // documented PEAK API and has no unusual load-time requirements.
        unsafe {
            let lib = Library::new(LIB_NAME).map_err(|e| BusError::Library(format!("{LIB_NAME}: {e}")))?;
            macro_rules! sym {
                ($name:literal) => {
                    *lib.get($name).map_err(|e| BusError::Library(format!("{LIB_NAME}: {e}")))?
                };
            }
            Ok(Api {
                initialize: sym!(b"CAN_Initialize\0"),
                initialize_fd: sym!(b"CAN_InitializeFD\0"),
                uninitialize: sym!(b"CAN_Uninitialize\0"),
                read: sym!(b"CAN_Read\0"),
                read_fd: sym!(b"CAN_ReadFD\0"),
                write: sym!(b"CAN_Write\0"),
                write_fd: sym!(b"CAN_WriteFD\0"),
                error_text: sym!(b"CAN_GetErrorText\0"),
                get_value: sym!(b"CAN_GetValue\0"),
                _lib: lib,
            })
        }
    }

    fn text(&self, status: Status) -> String {
        let mut buf = [0 as std::os::raw::c_char; 256];
        // SAFETY: PCAN-Basic writes at most 256 bytes including the terminator.
        let rc = unsafe { (self.error_text)(status, 0x09, buf.as_mut_ptr()) };
        if rc != PCAN_ERROR_OK {
            return format!("status 0x{status:08X}");
        }
        // SAFETY: buffer is NUL-terminated by the driver.
        unsafe { std::ffi::CStr::from_ptr(buf.as_ptr()) }.to_string_lossy().into_owned()
    }

    fn check(&self, call: &'static str, status: Status) -> Result<(), BusError> {
        if status == PCAN_ERROR_OK {
            Ok(())
        } else {
            Err(BusError::Driver { call, code: status as i64, text: self.text(status) })
        }
    }
}

/// An attached PCAN channel, as reported by PCAN-Basic.
#[derive(Clone, Debug, serde::Serialize)]
pub struct PcanChannel {
    /// Channel name usable in a `pcan:` bus spec, e.g. `USBBUS1`.
    pub name: String,
    pub handle: u16,
    /// Adapter family: `USB`, `PCI` or `LAN`.
    pub family: &'static str,
    /// 1-based index within the family.
    pub index: u16,
    /// Hardware name reported by the driver, e.g. `PCAN-USB FD`.
    pub hardware: String,
    pub fd_capable: bool,
    /// Another application already has the channel open.
    pub occupied: bool,
}

/// All attached PCAN channels (USB, PCI and LAN, 1..16 each).
pub fn list_channels() -> Result<Vec<PcanChannel>, BusError> {
    let api = Api::load()?;
    let mut out = Vec::new();
    for (family, prefix, low, high) in [("USB", "USBBUS", 0x50u16, 0x500u16), ("PCI", "PCIBUS", 0x40, 0x400), ("LAN", "LANBUS", 0x800, 0x800)] {
        for index in 1..=16u16 {
            let handle = if family == "LAN" { low + index } else if index <= 8 { low + index } else { high + index };
            let mut condition: u32 = 0;
            // SAFETY: 4-byte output buffer for a DWORD parameter.
            let st = unsafe { (api.get_value)(handle, PCAN_CHANNEL_CONDITION, &mut condition as *mut u32 as *mut _, 4) };
            if st != PCAN_ERROR_OK || !matches!(condition, PCAN_CHANNEL_AVAILABLE | PCAN_CHANNEL_OCCUPIED | PCAN_CHANNEL_PCANVIEW) {
                continue;
            }
            let mut name = [0u8; 33];
            // SAFETY: PCAN-Basic writes at most MAX_LENGTH_HARDWARE_NAME (33) bytes.
            unsafe { (api.get_value)(handle, PCAN_HARDWARE_NAME, name.as_mut_ptr() as *mut _, name.len() as u32) };
            let end = name.iter().position(|b| *b == 0).unwrap_or(name.len());
            let mut features: u32 = 0;
            // SAFETY: 4-byte output buffer.
            unsafe { (api.get_value)(handle, PCAN_CHANNEL_FEATURES, &mut features as *mut u32 as *mut _, 4) };
            out.push(PcanChannel {
                name: format!("{prefix}{index}"),
                handle,
                family,
                index,
                hardware: String::from_utf8_lossy(&name[..end]).trim().to_string(),
                fd_capable: features & FEATURE_FD_CAPABLE != 0,
                occupied: condition != PCAN_CHANNEL_AVAILABLE,
            });
        }
    }
    Ok(out)
}

/// Map a channel name such as `USBBUS1` or `PCIBUS3` to a PCAN handle.
pub fn channel_handle(name: &str) -> Result<Handle, BusError> {
    let up = name.to_ascii_uppercase();
    let (prefix, n) = up
        .find(|c: char| c.is_ascii_digit())
        .map(|i| (&up[..i], up[i..].parse::<u16>().ok()))
        .ok_or_else(|| BusError::Spec(format!("PCAN channel '{name}' has no number")))?;
    let n = n.filter(|n| (1..=16).contains(n)).ok_or_else(|| BusError::Spec(format!("PCAN channel '{name}' out of range")))?;
    let (low, high) = match prefix {
        "USBBUS" | "USB" => (0x50, 0x500),
        "PCIBUS" | "PCI" => (0x40, 0x400),
        "LANBUS" | "LAN" => (0x800, 0x800),
        _ => return Err(BusError::Spec(format!("unsupported PCAN channel type '{prefix}'"))),
    };
    Ok(if n <= 8 { low + n } else { high + n })
}

/// BTR0/BTR1 code for classic CAN bit rates.
fn btr0btr1(bitrate: u32) -> Result<u16, BusError> {
    Ok(match bitrate {
        1_000_000 => 0x0014,
        800_000 => 0x0016,
        500_000 => 0x001C,
        250_000 => 0x011C,
        125_000 => 0x031C,
        100_000 => 0x432F,
        50_000 => 0x472F,
        20_000 => 0x532F,
        10_000 => 0x672F,
        5_000 => 0x7F7F,
        _ => return Err(BusError::Spec(format!("unsupported classic bit rate {bitrate}"))),
    })
}

/// PCAN-Basic FD bit-timing string for an 80 MHz clock. Nominal phase uses 80
/// time quanta (sample point 80%); data phase uses 20 or 16.
pub fn fd_timing(nominal: u32, data: u32) -> Result<String, BusError> {
    const CLOCK: u32 = 80_000_000;
    let nom_tq = 80;
    if CLOCK % (nominal * nom_tq) != 0 {
        return Err(BusError::Spec(format!("nominal bit rate {nominal} not reachable at 80 MHz")));
    }
    let nom_brp = CLOCK / (nominal * nom_tq);
    let (data_tq, data_tseg1, data_tseg2) = if CLOCK % (data * 20) == 0 { (20, 15, 4) } else { (16, 12, 3) };
    if CLOCK % (data * data_tq) != 0 {
        return Err(BusError::Spec(format!("data bit rate {data} not reachable at 80 MHz")));
    }
    let data_brp = CLOCK / (data * data_tq);
    Ok(format!(
        "f_clock_mhz=80,nom_brp={nom_brp},nom_tseg1=63,nom_tseg2=16,nom_sjw=16,\
         data_brp={data_brp},data_tseg1={data_tseg1},data_tseg2={data_tseg2},data_sjw={data_tseg2}"
    ))
}

pub struct Pcan {
    api: Api,
    handle: Handle,
    name: String,
    fd: bool,
    bitrate: u32,
    /// Host epoch minus device microseconds, fixed at the first frame.
    clock_offset: Option<f64>,
}

impl Pcan {
    /// `data_bitrate = Some(..)` opens the channel in CAN FD mode.
    pub fn open(channel: &str, bitrate: u32, data_bitrate: Option<u32>) -> Result<Self, BusError> {
        let api = Api::load()?;
        let handle = channel_handle(channel)?;
        match data_bitrate {
            Some(d) => {
                let timing = CString::new(fd_timing(bitrate, d)?).expect("no interior NUL");
                // SAFETY: valid handle constant and NUL-terminated timing string.
                api.check("CAN_InitializeFD", unsafe { (api.initialize_fd)(handle, timing.as_ptr()) })?;
            }
            None => {
                let btr = btr0btr1(bitrate)?;
                // SAFETY: plug-and-play channels ignore the last three arguments.
                api.check("CAN_Initialize", unsafe { (api.initialize)(handle, btr, 0, 0, 0) })?;
            }
        }
        Ok(Pcan { api, handle, name: channel.to_string(), fd: data_bitrate.is_some(), bitrate, clock_offset: None })
    }

    fn stamp(&mut self, device_us: u64) -> f64 {
        let dev = device_us as f64 * 1e-6;
        let off = *self.clock_offset.get_or_insert_with(|| crate::host_time() - dev);
        dev + off
    }

    fn try_read(&mut self) -> Result<Option<Frame>, BusError> {
        let (id, msgtype, payload_len, data, us) = if self.fd {
            let mut m = TPCANMsgFD { id: 0, msgtype: 0, dlc: 0, data: [0; MAX_DATA] };
            let mut ts: u64 = 0;
            // SAFETY: buffers are correctly sized repr(C) structs.
            let st = unsafe { (self.api.read_fd)(self.handle, &mut m, &mut ts) };
            if st == PCAN_ERROR_QRCVEMPTY {
                return Ok(None);
            }
            self.api.check("CAN_ReadFD", st)?;
            (m.id, m.msgtype, dlc_to_len(m.dlc), m.data, ts)
        } else {
            let mut m = TPCANMsg::default();
            let mut ts = TPCANTimestamp::default();
            // SAFETY: as above.
            let st = unsafe { (self.api.read)(self.handle, &mut m, &mut ts) };
            if st == PCAN_ERROR_QRCVEMPTY {
                return Ok(None);
            }
            self.api.check("CAN_Read", st)?;
            let us = ts.micros as u64 + 1000 * ts.millis as u64 + 1000 * (ts.millis_overflow as u64) * (1u64 << 32);
            let mut data = [0u8; MAX_DATA];
            data[..8].copy_from_slice(&m.data);
            (m.id, m.msgtype, (m.len as usize).min(8), data, us)
        };
        let mut flags = FrameFlags::default();
        flags.set(FrameFlags::EXTENDED, msgtype & MSG_EXTENDED != 0);
        flags.set(FrameFlags::FD, msgtype & MSG_FD != 0);
        flags.set(FrameFlags::BRS, msgtype & MSG_BRS != 0);
        flags.set(FrameFlags::ESI, msgtype & MSG_ESI != 0);
        flags.set(FrameFlags::RTR, msgtype & MSG_RTR != 0);
        flags.set(FrameFlags::TX_ECHO, msgtype & MSG_ECHO != 0);
        flags.set(FrameFlags::ERROR, msgtype & (MSG_ERRFRAME | MSG_STATUS) != 0);
        let timestamp = self.stamp(us);
        Ok(Some(Frame { timestamp, channel: 0, id, flags, len: payload_len as u8, data }))
    }
}

impl Bus for Pcan {
    fn recv(&mut self, timeout: Duration) -> Result<Option<Frame>, BusError> {
        // PCAN-Basic is non-blocking; poll at sub-millisecond granularity.
        // TODO: switch to PCAN_RECEIVE_EVENT for zero-latency wakeups.
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(f) = self.try_read()? {
                return Ok(Some(f));
            }
            if Instant::now() >= deadline {
                return Ok(None);
            }
            std::thread::sleep(Duration::from_micros(250));
        }
    }

    fn send(&mut self, frame: &Frame) -> Result<(), BusError> {
        let mut msgtype = 0u8;
        if frame.is_extended() {
            msgtype |= MSG_EXTENDED;
        }
        if frame.flags.has(FrameFlags::RTR) {
            msgtype |= MSG_RTR;
        }
        if self.fd {
            if frame.is_fd() {
                msgtype |= MSG_FD;
                if frame.flags.has(FrameFlags::BRS) {
                    msgtype |= MSG_BRS;
                }
            }
            let mut m = TPCANMsgFD { id: frame.id, msgtype, dlc: len_to_dlc(frame.len as usize), data: frame.data };
            // SAFETY: valid repr(C) message.
            self.api.check("CAN_WriteFD", unsafe { (self.api.write_fd)(self.handle, &mut m) })
        } else {
            if frame.len > 8 {
                return Err(BusError::Unsupported("CAN FD frame on a classic PCAN channel"));
            }
            let mut m = TPCANMsg { id: frame.id, msgtype, len: frame.len, data: [0; 8] };
            m.data.copy_from_slice(&frame.data[..8]);
            // SAFETY: valid repr(C) message.
            self.api.check("CAN_Write", unsafe { (self.api.write)(self.handle, &mut m) })
        }
    }

    fn info(&self) -> BusInfo {
        BusInfo { backend: "pcan", channel: self.name.clone(), fd_capable: self.fd, bitrate: Some(self.bitrate), offline: false }
    }
}

impl Drop for Pcan {
    fn drop(&mut self) {
        // SAFETY: handle was initialized in `open`.
        unsafe { (self.api.uninitialize)(self.handle) };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn handles() {
        assert_eq!(channel_handle("USBBUS1").unwrap(), 0x51);
        assert_eq!(channel_handle("usbbus9").unwrap(), 0x509);
        assert_eq!(channel_handle("PCIBUS2").unwrap(), 0x42);
        assert!(channel_handle("USBBUS17").is_err());
    }

    #[test]
    fn fd_timing_strings() {
        let t = fd_timing(500_000, 2_000_000).unwrap();
        assert!(t.contains("nom_brp=2") && t.contains("data_brp=2") && t.contains("data_tseg1=15"));
        let t = fd_timing(250_000, 5_000_000).unwrap();
        assert!(t.contains("nom_brp=4") && t.contains("data_brp=1") && t.contains("data_tseg1=12"));
    }

    #[test]
    fn struct_layouts_match_pcanbasic_h() {
        assert_eq!(std::mem::size_of::<TPCANMsg>(), 16);
        assert_eq!(std::mem::size_of::<TPCANMsgFD>(), 72);
        assert_eq!(std::mem::size_of::<TPCANTimestamp>(), 8);
    }
}
