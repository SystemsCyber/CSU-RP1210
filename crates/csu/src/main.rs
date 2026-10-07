//! `csu`: command-line front end for the CSU-RP1210 Rust core.
//!
//! Every subcommand takes a bus spec (see `csu_bus::backend`): a candump log
//! path, `pcan:USBBUS1,bitrate=500000,dbitrate=2000000`, `socketcan:can0`,
//! `rp1210:PEAKRP32,device=1,bitrate=250000`, or `virtual`.

mod serve;

use clap::{Parser, Subcommand};
use csu_bus::{Bus, BusError};
use csu_j1939::summary::NetworkTree;
use csu_j1939::{CompiledDb, Event, Stack};
use std::path::PathBuf;
use std::time::{Duration, Instant};

#[derive(Parser)]
#[command(name = "csu", version, about = "CSU-RP1210 heavy-vehicle network tool")]
struct Cli {
    /// J1939 database file (default search: $CSU_J1939DB, then J1939db.licensed.json or
    /// J1939db.us.licensed.json per --units, then the skeleton J1939db.json).
    #[arg(long, global = true)]
    db: Option<PathBuf>,
    /// Additional data packs layered over the database (repeatable).
    #[arg(long = "pack", global = true)]
    packs: Vec<PathBuf>,
    /// Unit system of the licensed database to load: metric or us
    /// (defaults: $CSU_UNITS, csu_settings.json, metric).
    #[arg(long, global = true, value_parser = parse_units)]
    units: Option<csu_j1939::UnitSystem>,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// List installed adapters and drivers.
    Devices,
    /// Decode traffic to JSON lines (one J1939 message per line).
    Decode {
        bus: String,
        /// Stop after N messages.
        #[arg(long)]
        limit: Option<u64>,
        /// Only emit reassembled TP messages and decoded PGNs.
        #[arg(long)]
        decoded_only: bool,
    },
    /// Print the network inventory (source addresses, PGNs, rates).
    Summary { bus: String },
    /// Measure decode throughput on a log file.
    Bench { file: PathBuf },
    /// Serve the web UI (tree view) for a live bus or a replayed log.
    Serve {
        /// Bus spec. Logs replay in real time unless `,speed=0` is given.
        bus: String,
        /// Address to listen on. Use 0.0.0.0:8210 to allow other devices (kiosk).
        #[arg(long, default_value = "127.0.0.1:8210")]
        bind: String,
        /// Append every received frame to a candump log.
        #[arg(long)]
        record: Option<PathBuf>,
    },
}

fn parse_units(s: &str) -> Result<csu_j1939::UnitSystem, String> {
    csu_j1939::UnitSystem::parse(s).ok_or_else(|| format!("expected 'metric' or 'us', got '{s}'"))
}

fn load_db(cli: &Cli) -> CompiledDb {
    let units = cli.units.unwrap_or_else(csu_j1939::db::preferred_units);
    let mut db = match CompiledDb::load_default_with_units(cli.db.as_deref(), units) {
        Ok(db) => db,
        Err(e) => {
            eprintln!("warning: {e}; continuing without a database");
            CompiledDb::default()
        }
    };
    for p in &cli.packs {
        match CompiledDb::load(p) {
            Ok(pack) => db.merge(pack),
            Err(e) => eprintln!("warning: data pack {e}"),
        }
    }
    if let Some(src) = db.meta.sources.first() {
        let declared = db.meta.units.map(|u| u.name()).unwrap_or("undeclared");
        eprintln!("J1939 database: {src} (units: {declared})");
    }
    if db.meta.skeleton_only {
        eprintln!(
            "note: only the skeleton J1939 database is loaded. Create a licensed database from your \
             Digital Annex with DigitalAnnexSelect.py (or j1939db_tools.py generate) to decode SPNs."
        );
    }
    db
}

/// Pull frames until the source ends (logs) or `max` messages were produced.
fn drain(bus: &mut dyn Bus, mut on_frame: impl FnMut(&csu_bus::Frame) -> bool) -> Result<(), BusError> {
    loop {
        match bus.recv(Duration::from_millis(200)) {
            Ok(Some(f)) => {
                if !on_frame(&f) {
                    return Ok(());
                }
            }
            Ok(None) => {}
            Err(BusError::EndOfInput) => return Ok(()),
            Err(e) => return Err(e),
        }
    }
}

fn main() {
    let cli = Cli::parse();
    let result = match &cli.cmd {
        Cmd::Devices => {
            devices();
            Ok(())
        }
        Cmd::Decode { bus, limit, decoded_only } => decode(&cli, bus, *limit, *decoded_only),
        Cmd::Summary { bus } => summary(&cli, bus),
        Cmd::Bench { file } => bench(&cli, file),
        Cmd::Serve { bus, bind, record } => serve::run(load_db(&cli), bus, bind, record.clone()),
    };
    if let Err(e) = result {
        eprintln!("error: {e}");
        std::process::exit(1);
    }
}

fn devices() {
    #[cfg(windows)]
    {
        println!("RP1210 implementations (%WINDIR%\\RP121032.ini):");
        let list = csu_bus::backend::rp1210::list_implementations();
        if list.is_empty() {
            println!("  none found");
        }
        for imp in list {
            let bits = match (imp.dll_64bit, imp.dll_32bit) {
                (true, true) => "32+64-bit",
                (true, false) => "64-bit",
                (false, true) => "32-bit only",
                _ => "DLL not found",
            };
            let via_bridge = cfg!(target_pointer_width = "64") && !imp.dll_64bit && imp.dll_32bit && imp.loadable;
            let note = if via_bridge {
                "  [via RP1210 32-to-64-bit bridge]"
            } else if imp.loadable {
                ""
            } else {
                "  [not loadable by this build]"
            };
            println!("  {} — {} (RP1210 {}, {}){}", imp.api_name, imp.vendor, imp.rp1210_version, bits, note);
            for d in &imp.devices {
                println!("      device {}: {} {}", d.id, d.name, d.description);
            }
            println!("      protocols: {}", imp.protocols.join(", "));
            println!("      open with: rp1210:{},device=<id>,bitrate=250000", imp.api_name);
        }
    }
    #[cfg(any(windows, target_os = "linux"))]
    {
        println!("PEAK PCAN-Basic: open with pcan:USBBUS1,bitrate=500000[,dbitrate=2000000] (CAN FD)");
    }
    #[cfg(target_os = "linux")]
    println!("SocketCAN: open with socketcan:<iface> (list with `ip -details link show type can`)");
}

fn decode(cli: &Cli, spec: &str, limit: Option<u64>, decoded_only: bool) -> Result<(), BusError> {
    let db = load_db(cli);
    let mut bus = csu_bus::open(spec)?;
    let mut stack = Stack::default();
    let mut events = Vec::new();
    let mut emitted = 0u64;
    let stdout = std::io::stdout();
    let mut out = std::io::BufWriter::new(stdout.lock());
    use std::io::Write;
    drain(bus.as_mut(), |f| {
        if !f.is_extended() {
            return true;
        }
        stack.feed(f, &mut events);
        for e in events.drain(..) {
            let line = match &e {
                Event::Message(m) => {
                    let spns = db.decode(m.pgn, &m.data);
                    if decoded_only && spns.is_empty() && !m.reassembled {
                        continue;
                    }
                    let (label, _) = db.pgn_label(m.pgn);
                    serde_json::json!({
                        "ts": m.timestamp, "ch": m.channel, "pgn": m.pgn, "label": label,
                        "sa": m.sa, "da": m.da, "tp": m.reassembled,
                        "data": csu_bus::hex(&m.data), "spns": spns,
                    })
                }
                Event::TpAborted(a) => serde_json::json!({ "tp_abort": a }),
            };
            let _ = writeln!(out, "{line}");
            emitted += 1;
            if limit.is_some_and(|l| emitted >= l) {
                return false;
            }
        }
        true
    })
}

fn summary(cli: &Cli, spec: &str) -> Result<(), BusError> {
    let db = load_db(cli);
    let mut bus = csu_bus::open(spec)?;
    let (tree, frames, elapsed) = build_tree(bus.as_mut())?;
    let span = tree.last_ts - tree.first_ts.unwrap_or(tree.last_ts);
    println!("{frames} frames over {span:.1} s (processed in {:.1} ms)", elapsed.as_secs_f64() * 1e3);
    for (ch, c) in &tree.channels {
        println!("channel {ch} {}: {} frames, {} TP aborts, {} error/status frames", c.name, c.count, c.tp_aborts, c.error_frames);
        for s in c.sources.values() {
            let mut extra = String::new();
            if let Some(n) = &s.claimed_name {
                extra += &format!(" NAME={:016X}", n.raw);
            }
            if let Some(v) = &s.vin {
                extra += &format!(" VIN={v}");
            }
            if let Some(d) = &s.dm1 {
                extra += &format!(" DTCs={}", d.dtcs.len());
            }
            println!("  SA {:3} (0x{:02X}) {:<32} {:>8} frames{extra}", s.sa, s.sa, db.sa_name(s.sa), s.count);
            for n in s.pgns.values() {
                let (label, name) = db.pgn_label(n.pgn);
                let period = match (n.period, n.min_interval, n.max_interval) {
                    (Some(p), Some(lo), Some(hi)) if hi - lo >= 0.001 => {
                        format!("{:8.1} ms ({:.0}-{:.0})", p * 1e3, lo * 1e3, hi * 1e3)
                    }
                    (Some(p), _, _) => format!("{:8.1} ms", p * 1e3),
                    _ => "       — ".into(),
                };
                let dest = if n.da == 0xFF { String::new() } else { format!(" → {}", n.da) };
                let tp = if n.reassembled { " [TP]" } else { "" };
                println!(
                    "      PGN {:6} (0x{:05X}){dest:<6} {:>7}x {period}  {label} {name}{tp}",
                    n.pgn, n.pgn, n.count
                );
            }
        }
        if !c.standard.is_empty() {
            println!("  11-bit identifiers:");
            for n in c.standard.values() {
                println!("      ID 0x{:03X} {:>7}x", n.can_id.unwrap_or(0), n.count);
            }
        }
    }
    Ok(())
}

fn build_tree(bus: &mut dyn Bus) -> Result<(NetworkTree, u64, Duration), BusError> {
    let start = Instant::now();
    let mut tree = NetworkTree::default();
    let mut stack = Stack::default();
    let mut events = Vec::new();
    let mut frames = 0u64;
    drain(bus, |f| {
        frames += 1;
        if f.flags.has(csu_bus::FrameFlags::ERROR) {
            tree.observe_error(f);
        } else if f.is_extended() {
            stack.feed(f, &mut events);
            for e in events.drain(..) {
                match e {
                    Event::Message(m) => tree.observe(&m),
                    Event::TpAborted(a) => tree.observe_abort(&a),
                }
            }
        } else {
            tree.observe_standard(f);
        }
        true
    })?;
    for (i, name) in bus.channel_names().iter().enumerate() {
        tree.set_channel_name(i as u8, name);
    }
    Ok((tree, frames, start.elapsed()))
}

fn bench(cli: &Cli, file: &PathBuf) -> Result<(), BusError> {
    let db = load_db(cli);
    // Parse once into memory so the measurement is decode cost, not disk I/O.
    let frames: Vec<csu_bus::Frame> = csu_bus::candump::CandumpReader::open(file, 0.0, false)?.collect();
    let n = frames.len() as f64;
    let reps = (2_000_000.0 / n.max(1.0)).ceil().max(1.0) as usize;
    let mut stack = Stack::default();
    let mut tree = NetworkTree::default();
    let mut events = Vec::new();
    let mut spns = 0usize;
    let start = Instant::now();
    for _ in 0..reps {
        for f in &frames {
            stack.feed(f, &mut events);
            for e in events.drain(..) {
                if let Event::Message(m) = e {
                    spns += db.decode(m.pgn, &m.data).len();
                    tree.observe(&m);
                }
            }
        }
    }
    let secs = start.elapsed().as_secs_f64();
    let total = n * reps as f64;
    println!(
        "{total:.0} frames in {secs:.3} s → {:.2} M frames/s ({:.0} ns/frame), {spns} SPN values decoded",
        total / secs / 1e6,
        secs / total * 1e9
    );
    Ok(())
}
