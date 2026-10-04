//! Device discovery and the generated RP1210 vendor INI.
//!
//! RP1210 devices are adapter families; RP1210C/D "Channel=N" selects a
//! channel within the family:
//!
//! | DeviceID | Family | Channel N maps to |
//! |---|---|---|
//! | 1 | PEAK USB (PCAN-USB, PCAN-USB FD, Pro FD, ...) | the N-th attached `USBBUSx` |
//! | 2 | PEAK PCI/PCIe (e.g. PCAN-PCI Express FD) | the N-th attached `PCIBUSx` |
//! | 3 | PEAK LAN gateways | the N-th attached `LANBUSx` |
//! | 10 | Linux SocketCAN | the N-th CAN interface (sorted by name) |
//! | 99 | Virtual loopback (testing, demos) | an independent in-process bus |
//!
//! `CSUCAN_DEVICE_<id>=<csu bus spec>` (e.g. `candump:run.log,speed=1`) adds or
//! replaces a single-channel device, which is how tests attach replays.

use std::collections::BTreeMap;
use std::fmt::Write as _;

pub const VIRTUAL_DEVICE: i16 = 99;
pub const VIRTUAL_CHANNELS: u8 = 8;

#[derive(Clone, Debug)]
pub struct Channel {
    /// csu-bus spec without bit-rate options, e.g. `pcan:USBBUS1`.
    pub spec: String,
    /// Human-readable label, e.g. `USBBUS1: PCAN-USB FD`.
    pub label: String,
    pub fd_capable: bool,
    /// The bit rate is configured outside the driver (SocketCAN `ip link`).
    pub external_bitrate: bool,
    /// The spec already carries everything (environment overrides).
    pub fixed_spec: bool,
}

#[derive(Clone, Debug)]
pub struct Device {
    pub id: i16,
    pub name: String,
    pub description: String,
    pub channels: Vec<Channel>,
}

fn pcan_devices(out: &mut BTreeMap<i16, Device>) {
    #[cfg(any(windows, target_os = "linux"))]
    {
        let Ok(list) = csu_bus::backend::pcan::list_channels() else { return };
        for (id, family, name) in [(1i16, "USB", "PEAK PCAN-USB"), (2, "PCI", "PEAK PCAN-PCI"), (3, "LAN", "PEAK PCAN-LAN")] {
            // The driver does not report the hardware name of a channel another
            // application holds; borrow it from a sibling channel.
            let family_hw = list.iter().filter(|c| c.family == family && !c.hardware.is_empty()).map(|c| c.hardware.clone()).next();
            let channels: Vec<Channel> = list
                .iter()
                .filter(|c| c.family == family)
                .map(|c| Channel {
                    spec: format!("pcan:{}", c.name),
                    label: format!(
                        "{}: {}{}",
                        c.name,
                        if c.hardware.is_empty() { family_hw.clone().unwrap_or_else(|| "PCAN".into()) } else { c.hardware.clone() },
                        if c.occupied { " (in use)" } else { "" }
                    ),
                    fd_capable: c.fd_capable,
                    external_bitrate: false,
                    fixed_spec: false,
                })
                .collect();
            if channels.is_empty() {
                continue;
            }
            let labels: Vec<&str> = channels.iter().map(|c| c.label.as_str()).collect();
            out.insert(id, Device { id, name: name.into(), description: labels.join("; "), channels });
        }
    }
    #[cfg(not(any(windows, target_os = "linux")))]
    let _ = out;
}

fn socketcan_devices(out: &mut BTreeMap<i16, Device>) {
    #[cfg(target_os = "linux")]
    {
        // ARPHRD_CAN = 280 in /sys/class/net/<if>/type.
        let Ok(dir) = std::fs::read_dir("/sys/class/net") else { return };
        let mut names: Vec<String> = dir
            .filter_map(|e| e.ok())
            .filter(|e| std::fs::read_to_string(e.path().join("type")).is_ok_and(|t| t.trim() == "280"))
            .filter_map(|e| e.file_name().into_string().ok())
            .collect();
        names.sort();
        if names.is_empty() {
            return;
        }
        let channels: Vec<Channel> = names
            .iter()
            .map(|n| Channel {
                spec: format!("socketcan:{n},recv_own=false"),
                label: n.clone(),
                fd_capable: true,
                external_bitrate: true,
                fixed_spec: false,
            })
            .collect();
        out.insert(10, Device { id: 10, name: "SocketCAN".into(), description: names.join("; "), channels });
    }
    #[cfg(not(target_os = "linux"))]
    let _ = out;
}

/// Enumerate devices now (hardware may be plugged in or removed between calls).
pub fn discover() -> BTreeMap<i16, Device> {
    let mut out = BTreeMap::new();
    pcan_devices(&mut out);
    socketcan_devices(&mut out);
    out.insert(
        VIRTUAL_DEVICE,
        Device {
            id: VIRTUAL_DEVICE,
            name: "Virtual loopback".into(),
            description: "In-process loopback bus for testing (no hardware)".into(),
            channels: (1..=VIRTUAL_CHANNELS)
                .map(|n| Channel {
                    spec: "virtual:".into(),
                    label: format!("virtual {n}"),
                    fd_capable: true,
                    external_bitrate: true,
                    fixed_spec: false,
                })
                .collect(),
        },
    );
    for (key, spec) in std::env::vars() {
        let Some(id) = key.strip_prefix("CSUCAN_DEVICE_").and_then(|s| s.parse::<i16>().ok()) else { continue };
        out.insert(
            id,
            Device {
                id,
                name: format!("Configured device {id}"),
                description: spec.clone(),
                channels: vec![Channel { spec, label: format!("device {id}"), fd_capable: true, external_bitrate: true, fixed_spec: true }],
            },
        );
    }
    out
}

pub const PROTOCOL_SPEEDS: &str = "125,250,500,1000,250/2000,500/2000,500/4000";

/// RP1210 vendor INI text describing the currently attached devices.
pub fn vendor_ini(devices: &BTreeMap<i16, Device>) -> String {
    let ids: Vec<String> = devices.keys().map(|d| d.to_string()).collect();
    let ids = ids.join(",");
    let mut s = String::new();
    let _ = writeln!(s, ";===============================================================================");
    let _ = writeln!(s, "; CSUCAN - RP1210 C driver for PEAK PCAN-Basic and SocketCAN (CSU-RP1210)");
    let _ = writeln!(s, "; Generated by csucan from the attached hardware. Do not edit; it is rewritten.");
    let _ = writeln!(s, "; CAN FD: use Baud=<nominal>/<data> in kbit/s, e.g. \"J1939:Baud=250/2000,Channel=1\".");
    let _ = writeln!(s, ";===============================================================================");
    let _ = writeln!(s, "[VendorInformation]");
    let _ = writeln!(s, "Name=CSU native CAN (PEAK PCAN-Basic, SocketCAN)");
    let _ = writeln!(s, "Address1=Colorado State University");
    let _ = writeln!(s, "VendorURL=https://github.com/SystemsCyber/CSU-RP1210");
    let _ = writeln!(s, "MessageString=CSUCAN_MSG");
    let _ = writeln!(s, "ErrorString=CSUCAN_ERR");
    let _ = writeln!(s, "TimeStampWeight=1");
    let _ = writeln!(s, "AutoDetectCapable=No");
    let _ = writeln!(s, "Version={}", env!("CARGO_PKG_VERSION"));
    let _ = writeln!(s, "RP1210=C");
    let _ = writeln!(s, "CANFormatsSupported=4,5");
    let _ = writeln!(s, "J1939FormatsSupported=1,2");
    let _ = writeln!(s, "J1939Addresses=1");
    let _ = writeln!(s, "CANAutoBaud=FALSE");
    let _ = writeln!(s, "Devices={ids}");
    let _ = writeln!(s, "Protocols=1,2");
    for d in devices.values() {
        let _ = writeln!(s);
        let _ = writeln!(s, "[DeviceInformation{}]", d.id);
        let _ = writeln!(s, "DeviceID={}", d.id);
        let _ = writeln!(s, "DeviceDescription={}", d.description);
        let _ = writeln!(s, "DeviceName={}", d.name);
        let _ = writeln!(s, "DeviceParams=");
        let _ = writeln!(s, "MultiCANChannels={}", d.channels.len());
        let _ = writeln!(s, "MultiJ1939Channels={}", d.channels.len());
        let _ = writeln!(s, "MultiISO15765Channels=0");
    }
    for (n, (proto, desc)) in [("CAN", "Controller Area Network (classic and CAN FD)"), ("J1939", "SAE J1939 (transport protocol in the driver)")]
        .iter()
        .enumerate()
    {
        let _ = writeln!(s);
        let _ = writeln!(s, "[ProtocolInformation{}]", n + 1);
        let _ = writeln!(s, "ProtocolString={proto}");
        let _ = writeln!(s, "ProtocolDescription={desc}");
        let _ = writeln!(s, "ProtocolSpeed={PROTOCOL_SPEEDS}");
        let _ = writeln!(s, "ProtocolParams=Baud=,Channel=,FDFlags=");
        let _ = writeln!(s, "Devices={ids}");
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn virtual_device_always_present_and_ini_lists_it() {
        let d = discover();
        assert!(d.contains_key(&VIRTUAL_DEVICE));
        let ini = vendor_ini(&d);
        assert!(ini.contains("[DeviceInformation99]"));
        assert!(ini.contains("MultiCANChannels=8"));
        assert!(ini.contains("ProtocolString=J1939"));
        assert!(ini.contains("RP1210=C"));
    }
}
