//! Backend selection from a compact text spec.
//!
//! | Spec | Backend |
//! |---|---|
//! | `candump:FILE[,speed=1.0][,loop]` or a bare path | log replay |
//! | `virtual` | in-process loopback (tests, demos) |
//! | `socketcan:can0[,recv_own=false]` | Linux SocketCAN, CAN FD enabled |
//! | `pcan:USBBUS1[,bitrate=250000][,dbitrate=2000000]` | PEAK PCAN-Basic (Windows, Linux) |
//! | `rp1210:PEAKRP32[,device=1][,bitrate=250000]` | TMC RP1210 DLL (Windows) |

use crate::{Bus, BusError};
use std::collections::HashMap;

pub mod virtual_bus;

#[cfg(any(windows, target_os = "linux"))]
pub mod pcan;
#[cfg(windows)]
pub mod rp1210;
#[cfg(target_os = "linux")]
pub mod socketcan;

/// Parsed `kind:target,key=value,...` spec.
#[derive(Debug, Clone, PartialEq)]
pub struct BusSpec {
    pub kind: String,
    pub target: String,
    pub options: HashMap<String, String>,
}

impl BusSpec {
    pub fn parse(spec: &str) -> Result<Self, BusError> {
        let (kind, rest) = match spec.split_once(':') {
            // A Windows drive letter ("C:\...") is a path, not a backend.
            Some((k, r)) if k.len() > 1 => (k.to_ascii_lowercase(), r),
            _ => ("candump".to_string(), spec),
        };
        let mut parts = rest.split(',');
        let target = parts.next().unwrap_or("").trim().to_string();
        let mut options = HashMap::new();
        for p in parts {
            let p = p.trim();
            if p.is_empty() {
                continue;
            }
            match p.split_once('=') {
                Some((k, v)) => options.insert(k.trim().to_ascii_lowercase(), v.trim().to_string()),
                None => options.insert(p.to_ascii_lowercase(), "true".to_string()),
            };
        }
        Ok(BusSpec { kind, target, options })
    }

    pub fn opt<T: std::str::FromStr>(&self, key: &str) -> Result<Option<T>, BusError> {
        match self.options.get(key) {
            None => Ok(None),
            Some(v) => v
                .parse()
                .map(Some)
                .map_err(|_| BusError::Spec(format!("bad value for {key}: {v}"))),
        }
    }

    /// True when `key` is explicitly set to false/0.
    pub fn flag_false(&self, key: &str) -> bool {
        self.options.get(key).is_some_and(|v| v == "false" || v == "0")
    }

    pub fn flag(&self, key: &str) -> bool {
        self.options.get(key).is_some_and(|v| v != "false" && v != "0")
    }
}

/// Open a bus from a spec string. See the module docs for the syntax.
pub fn open(spec: &str) -> Result<Box<dyn Bus>, BusError> {
    let s = BusSpec::parse(spec)?;
    match s.kind.as_str() {
        "candump" | "file" | "replay" => {
            let speed = s.opt::<f64>("speed")?.unwrap_or(0.0);
            Ok(Box::new(crate::candump::CandumpReader::open(&s.target, speed, s.flag("loop"))?))
        }
        "virtual" => Ok(Box::new(virtual_bus::VirtualBus::new())),
        #[cfg(target_os = "linux")]
        "socketcan" => Ok(Box::new(socketcan::SocketCan::open_with(&s.target, !s.flag_false("recv_own"))?)),
        #[cfg(any(windows, target_os = "linux"))]
        "pcan" => Ok(Box::new(pcan::Pcan::open(
            &s.target,
            s.opt("bitrate")?.unwrap_or(250_000),
            s.opt("dbitrate")?,
        )?)),
        #[cfg(windows)]
        "rp1210" => Ok(Box::new(rp1210::Rp1210::open(
            &s.target,
            s.opt("device")?.unwrap_or(1),
            s.opt("bitrate")?.unwrap_or(250_000),
        )?)),
        other => Err(BusError::Spec(format!("unknown or unavailable backend '{other}' on this platform"))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_specs() {
        let s = BusSpec::parse("pcan:USBBUS1,bitrate=500000,dbitrate=2000000").unwrap();
        assert_eq!(s.kind, "pcan");
        assert_eq!(s.target, "USBBUS1");
        assert_eq!(s.opt::<u32>("dbitrate").unwrap(), Some(2_000_000));

        let s = BusSpec::parse(r"C:\logs\run.log").unwrap();
        assert_eq!(s.kind, "candump");
        assert_eq!(s.target, r"C:\logs\run.log");

        let s = BusSpec::parse("candump:run.log,speed=2,loop").unwrap();
        assert!(s.flag("loop"));
        assert_eq!(s.opt::<f64>("speed").unwrap(), Some(2.0));
    }
}
