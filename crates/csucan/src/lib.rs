//! CSUCAN: an RP1210 C driver (`csucan.dll` / `libcsucan.so`) for
//!
//! * PEAK PCAN-Basic adapters (PCAN-USB, PCAN-USB FD/Pro FD, PCAN-PCI Express FD,
//!   ...), enumerated live, so no vendor RP1210 configuration is needed;
//! * Linux SocketCAN interfaces;
//! * an in-process virtual loopback device for testing.
//!
//! It exports the standard RP1210 API plus `CSUCAN_WriteIni`, which writes an
//! RP1210 vendor INI describing the attached devices. CAN FD is an extension:
//! `Baud=<nominal>/<data>` (kbit/s) in the protocol string.

pub mod devices;
pub mod driver;
pub mod errors;

use driver::driver;
use errors::*;
use std::ffi::{c_char, c_int, c_long, c_short, c_void, CStr};

unsafe fn slice<'a>(p: *const u8, n: c_short) -> &'a [u8] {
    if p.is_null() || n <= 0 {
        &[]
    } else {
        // SAFETY: caller guarantees `n` readable bytes at `p`.
        unsafe { std::slice::from_raw_parts(p, n as usize) }
    }
}

unsafe fn write_cstr(dst: *mut c_char, cap: usize, text: &str) {
    if dst.is_null() || cap == 0 {
        return;
    }
    let bytes = text.as_bytes();
    let n = bytes.len().min(cap - 1);
    // SAFETY: caller guarantees `cap` writable bytes at `dst`.
    unsafe {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), dst as *mut u8, n);
        *dst.add(n) = 0;
    }
}

/// # Safety
/// `protocol` must be a NUL-terminated string or null.
#[no_mangle]
pub unsafe extern "system" fn RP1210_ClientConnect(
    _hwnd: *mut c_void,
    device: c_short,
    protocol: *const c_char,
    _tx_size: c_long,
    rx_size: c_long,
    _app_packetizing: c_short,
) -> c_short {
    if protocol.is_null() {
        return ERR_INVALID_PROTOCOL;
    }
    // SAFETY: checked non-null; RP1210 passes a C string.
    let proto = unsafe { CStr::from_ptr(protocol) }.to_string_lossy();
    // Queue depth in messages, derived from the requested buffer size.
    let capacity = if rx_size > 0 { (rx_size as usize / 16).max(256) } else { 4096 };
    driver().connect(device, &proto, capacity)
}

#[no_mangle]
pub extern "system" fn RP1210_ClientDisconnect(client: c_short) -> c_short {
    driver().disconnect(client)
}

/// # Safety
/// `msg` must point to `size` readable bytes.
#[no_mangle]
pub unsafe extern "system" fn RP1210_SendMessage(
    client: c_short,
    msg: *const u8,
    size: c_short,
    _notify: c_short,
    block: c_short,
) -> c_short {
    let Some(c) = driver().client(client) else { return ERR_INVALID_CLIENT_ID };
    // SAFETY: per the RP1210 contract.
    c.send(unsafe { slice(msg, size) }, block != 0)
}

/// # Safety
/// `buf` must point to `size` writable bytes.
#[no_mangle]
pub unsafe extern "system" fn RP1210_ReadMessage(client: c_short, buf: *mut u8, size: c_short, block: c_short) -> c_short {
    let Some(c) = driver().client(client) else { return -ERR_INVALID_CLIENT_ID };
    if buf.is_null() || size <= 0 {
        return -ERR_MESSAGE_TOO_LONG;
    }
    // SAFETY: per the RP1210 contract.
    let out = unsafe { std::slice::from_raw_parts_mut(buf, size as usize) };
    c.read(out, block != 0)
}

/// # Safety
/// `buf` must point to `size` readable bytes (or be null with size 0).
#[no_mangle]
pub unsafe extern "system" fn RP1210_SendCommand(cmd: c_short, client: c_short, buf: *mut u8, size: c_short) -> c_short {
    if cmd == driver::CMD_DISALLOW_FURTHER_CONNECTIONS {
        driver().disallow_connections();
        return NO_ERRORS;
    }
    let Some(c) = driver().client(client) else { return ERR_INVALID_CLIENT_ID };
    // SAFETY: per the RP1210 contract.
    c.command(cmd, unsafe { slice(buf, size) })
}

/// # Safety
/// Each pointer must address at least one writable byte (or be null).
#[no_mangle]
pub unsafe extern "system" fn RP1210_ReadVersion(dll_major: *mut c_char, dll_minor: *mut c_char, api_major: *mut c_char, api_minor: *mut c_char) {
    for (p, v) in [(dll_major, b'0'), (dll_minor, b'1'), (api_major, b'3'), (api_minor, b'0')] {
        if !p.is_null() {
            // SAFETY: single character, per RP1210.
            unsafe { *p = v as c_char };
        }
    }
}

/// # Safety
/// Each buffer must hold 17 bytes (or be null).
#[no_mangle]
pub unsafe extern "system" fn RP1210_ReadDetailedVersion(client: c_short, api: *mut c_char, dll: *mut c_char, fw: *mut c_char) -> c_short {
    let Some(label) = driver().client_label(client) else { return ERR_INVALID_CLIENT_ID };
    // SAFETY: RP1210 specifies 17-byte buffers.
    unsafe {
        write_cstr(api, 17, "RP1210C");
        write_cstr(dll, 17, concat!("CSUCAN ", env!("CARGO_PKG_VERSION")));
        write_cstr(fw, 17, &label);
    }
    NO_ERRORS
}

/// # Safety
/// `info` must point to `size` writable bytes.
#[no_mangle]
pub unsafe extern "system" fn RP1210_GetHardwareStatus(client: c_short, info: *mut u8, size: c_short, _block: c_short) -> c_short {
    if info.is_null() || size <= 0 {
        return ERR_INVALID_COMMAND;
    }
    // SAFETY: per the RP1210 contract.
    let out = unsafe { std::slice::from_raw_parts_mut(info, size as usize) };
    out.fill(0);
    driver().hardware_status(client, out)
}

/// # Safety
/// `info` must point to 256 writable bytes.
#[no_mangle]
pub unsafe extern "system" fn RP1210_GetHardwareStatusEx(client: c_short, info: *mut u8) -> c_short {
    if info.is_null() {
        return ERR_INVALID_COMMAND;
    }
    // SAFETY: RP1210C specifies 256 bytes.
    let out = unsafe { std::slice::from_raw_parts_mut(info, 256) };
    out.fill(0);
    driver().hardware_status(client, out)
}

/// # Safety
/// `text` must hold 80 bytes.
#[no_mangle]
pub unsafe extern "system" fn RP1210_GetErrorMsg(code: c_short, text: *mut c_char) -> c_short {
    // SAFETY: RP1210 description buffers are 80 bytes.
    unsafe { write_cstr(text, 80, describe(code.abs())) };
    NO_ERRORS
}

/// Returns the detailed driver message for the last failure (e.g. the
/// PCAN-Basic error for a failed ClientConnect) with the RP1210 code as sub-code.
///
/// # Safety
/// `text` must hold 80 bytes; `sub` must be writable or null.
#[no_mangle]
pub unsafe extern "system" fn RP1210_GetLastErrorMsg(code: c_short, sub: *mut c_int, text: *mut c_char, client: c_short) -> c_short {
    let detail = driver().client_error(client).filter(|t| !t.is_empty()).unwrap_or_else(|| driver().last_error());
    let msg = if detail.is_empty() { describe(code.abs()).to_string() } else { format!("{}: {detail}", describe(code.abs())) };
    // SAFETY: as documented.
    unsafe {
        if !sub.is_null() {
            *sub = code as c_int;
        }
        write_cstr(text, 80, &msg);
    }
    NO_ERRORS
}

#[no_mangle]
pub extern "system" fn RP1210_Ioctl(_client: c_short, _id: c_long, _input: *mut c_void, _output: *mut c_void) -> c_short {
    ERR_COMMAND_NOT_SUPPORTED
}

/// Write an RP1210 vendor INI describing the currently attached devices.
/// Returns 0 on success.
///
/// # Safety
/// `path` must be a NUL-terminated path.
#[no_mangle]
pub unsafe extern "system" fn CSUCAN_WriteIni(path: *const c_char) -> c_short {
    if path.is_null() {
        return ERR_INVALID_COMMAND;
    }
    // SAFETY: checked non-null C string.
    let path = unsafe { CStr::from_ptr(path) }.to_string_lossy().into_owned();
    let devices = driver().refresh_devices();
    match std::fs::write(&path, devices::vendor_ini(&devices)) {
        Ok(()) => NO_ERRORS,
        Err(_) => ERR_INVALID_COMMAND,
    }
}

/// Copy the detailed text of the last driver error (e.g. the PCAN-Basic message).
///
/// # Safety
/// `text` must hold `size` bytes.
#[no_mangle]
pub unsafe extern "system" fn CSUCAN_LastError(text: *mut c_char, size: c_short) -> c_short {
    // SAFETY: as documented.
    unsafe { write_cstr(text, size.max(0) as usize, &driver().last_error()) };
    NO_ERRORS
}
