//! Linux SocketCAN backend (`CAN_RAW` with CAN FD frames enabled).
//!
//! PEAK PCAN-USB FD / Pro FD adapters appear here through the mainline
//! `peak_usb` driver, so Linux needs no PEAK-specific code. Configure the
//! interface first, e.g.
//! `ip link set can0 up type can bitrate 500000 dbitrate 2000000 fd on`.
//!
//! Kernel structs are declared locally (not taken from `libc`) so the ABI is
//! explicit and reviewable next to the kernel headers.

use crate::{Bus, BusError, BusInfo, Frame, FrameFlags, MAX_DATA};
use std::ffi::CString;
use std::io;
use std::os::raw::{c_int, c_void};
use std::time::Duration;

const AF_CAN: c_int = 29;
const CAN_RAW: c_int = 1;
const SOL_CAN_RAW: c_int = 101; // SOL_CAN_BASE + CAN_RAW
const CAN_RAW_RECV_OWN_MSGS: c_int = 4;
const CAN_RAW_FD_FRAMES: c_int = 5;

const CAN_EFF_FLAG: u32 = 0x8000_0000;
const CAN_RTR_FLAG: u32 = 0x4000_0000;
const CAN_ERR_FLAG: u32 = 0x2000_0000;
const CANFD_BRS: u8 = 0x01;
const CANFD_ESI: u8 = 0x02;
const CANFD_FDF: u8 = 0x04;

const CAN_MTU: usize = 16;
const CANFD_MTU: usize = 72;

/// `struct sockaddr_can` (linux/can.h), with the address union flattened.
#[repr(C)]
struct SockaddrCan {
    can_family: u16,
    can_ifindex: c_int,
    rx_id: u32,
    tx_id: u32,
    _union_tail: u64,
}

/// `struct canfd_frame`; a classic `struct can_frame` is a 16-byte prefix of it.
#[repr(C)]
struct CanFdFrame {
    can_id: u32,
    len: u8,
    flags: u8,
    res0: u8,
    res1: u8,
    data: [u8; MAX_DATA],
}

pub struct SocketCan {
    fd: c_int,
    iface: String,
}

fn last_err(call: &'static str) -> BusError {
    let e = io::Error::last_os_error();
    BusError::Driver { call, code: e.raw_os_error().unwrap_or(-1) as i64, text: e.to_string() }
}

impl SocketCan {
    pub fn open(iface: &str) -> Result<Self, BusError> {
        let name = CString::new(iface).map_err(|_| BusError::Spec("interface name has NUL".into()))?;
        // SAFETY: plain libc calls with checked return values.
        unsafe {
            let ifindex = libc::if_nametoindex(name.as_ptr());
            if ifindex == 0 {
                return Err(last_err("if_nametoindex"));
            }
            let fd = libc::socket(AF_CAN, libc::SOCK_RAW, CAN_RAW);
            if fd < 0 {
                return Err(last_err("socket(AF_CAN)"));
            }
            let on: c_int = 1;
            let sz = std::mem::size_of::<c_int>() as libc::socklen_t;
            if libc::setsockopt(fd, SOL_CAN_RAW, CAN_RAW_FD_FRAMES, &on as *const _ as *const c_void, sz) < 0 {
                libc::close(fd);
                return Err(last_err("setsockopt(CAN_RAW_FD_FRAMES)"));
            }
            // Own transmissions come back so the evidence log includes them.
            libc::setsockopt(fd, SOL_CAN_RAW, CAN_RAW_RECV_OWN_MSGS, &on as *const _ as *const c_void, sz);
            let addr = SockaddrCan { can_family: AF_CAN as u16, can_ifindex: ifindex as c_int, rx_id: 0, tx_id: 0, _union_tail: 0 };
            if libc::bind(fd, &addr as *const _ as *const libc::sockaddr, std::mem::size_of::<SockaddrCan>() as libc::socklen_t) < 0 {
                libc::close(fd);
                return Err(last_err("bind"));
            }
            Ok(SocketCan { fd, iface: iface.to_string() })
        }
    }
}

impl Bus for SocketCan {
    fn recv(&mut self, timeout: Duration) -> Result<Option<Frame>, BusError> {
        let mut pfd = libc::pollfd { fd: self.fd, events: libc::POLLIN, revents: 0 };
        let ms = timeout.as_millis().min(i32::MAX as u128) as c_int;
        // SAFETY: one valid pollfd.
        let rc = unsafe { libc::poll(&mut pfd, 1, ms) };
        if rc < 0 {
            return Err(last_err("poll"));
        }
        if rc == 0 {
            return Ok(None);
        }
        let mut raw = CanFdFrame { can_id: 0, len: 0, flags: 0, res0: 0, res1: 0, data: [0; MAX_DATA] };
        // TODO: use recvmsg + SO_TIMESTAMPING for kernel/hardware timestamps and
        // MSG_DONTROUTE to mark own-message echoes.
        // SAFETY: buffer is CANFD_MTU bytes.
        let n = unsafe { libc::read(self.fd, &mut raw as *mut _ as *mut c_void, CANFD_MTU) };
        if n < 0 {
            return Err(last_err("read"));
        }
        let n = n as usize;
        if n != CAN_MTU && n != CANFD_MTU {
            return Ok(None);
        }
        let mut flags = FrameFlags::default();
        let extended = raw.can_id & CAN_EFF_FLAG != 0;
        flags.set(FrameFlags::EXTENDED, extended);
        flags.set(FrameFlags::RTR, raw.can_id & CAN_RTR_FLAG != 0);
        flags.set(FrameFlags::ERROR, raw.can_id & CAN_ERR_FLAG != 0);
        if n == CANFD_MTU {
            flags.set(FrameFlags::FD, true);
            flags.set(FrameFlags::BRS, raw.flags & CANFD_BRS != 0);
            flags.set(FrameFlags::ESI, raw.flags & CANFD_ESI != 0);
        }
        let id = if extended { raw.can_id & 0x1FFF_FFFF } else { raw.can_id & 0x7FF };
        let len = (raw.len as usize).min(if n == CANFD_MTU { MAX_DATA } else { 8 });
        Ok(Some(Frame { timestamp: crate::host_time(), channel: 0, id, flags, len: len as u8, data: raw.data }))
    }

    fn send(&mut self, frame: &Frame) -> Result<(), BusError> {
        let mut can_id = frame.id;
        if frame.is_extended() {
            can_id |= CAN_EFF_FLAG;
        }
        if frame.flags.has(FrameFlags::RTR) {
            can_id |= CAN_RTR_FLAG;
        }
        let fd = frame.is_fd();
        let mut flags = 0;
        if fd {
            flags |= CANFD_FDF;
            if frame.flags.has(FrameFlags::BRS) {
                flags |= CANFD_BRS;
            }
        }
        let raw = CanFdFrame { can_id, len: frame.len, flags, res0: 0, res1: 0, data: frame.data };
        let mtu = if fd { CANFD_MTU } else { CAN_MTU };
        // SAFETY: writes the first `mtu` bytes of a repr(C) frame.
        let n = unsafe { libc::write(self.fd, &raw as *const _ as *const c_void, mtu) };
        if n as usize != mtu {
            return Err(last_err("write"));
        }
        Ok(())
    }

    fn info(&self) -> BusInfo {
        BusInfo { backend: "socketcan", channel: self.iface.clone(), fd_capable: true, bitrate: None, offline: false }
    }
}

impl Drop for SocketCan {
    fn drop(&mut self) {
        // SAFETY: fd is owned by this struct.
        unsafe { libc::close(self.fd) };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn layouts_match_kernel_headers() {
        assert_eq!(std::mem::size_of::<SockaddrCan>(), 24);
        assert_eq!(std::mem::size_of::<CanFdFrame>(), CANFD_MTU);
    }
}
