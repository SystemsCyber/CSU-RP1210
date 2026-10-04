//! J1939 Digital Annex database: loading, layering and decoding.
//!
//! The repository ships a **skeleton** `J1939db.json` with the schema and no
//! SAE content. Users supply their own licensed database, typically generated
//! from the J1939DA spreadsheet with pretty_j1939's `create_j1939db-json`. It
//! is found in this order:
//!
//! 1. an explicit path (`--db`)
//! 2. the `CSU_J1939DB` environment variable
//! 3. `J1939db.licensed.json` (git-ignored) in the working directory, then next to the executable
//! 4. `J1939db.json` in the working directory, then next to the executable
//!
//! Two schemas are accepted:
//! * **legacy** (the CSU-RP1210 file): each SPN carries its own `StartBit`
//! * **current** (pretty_j1939): each PGN carries `SPNStartBits` parallel to
//!   `SPNs`, so one SPN can sit at different positions in different PGNs
//!
//! Several files can be layered ([`CompiledDb::merge`]): base DA, then OEM
//! proprietary data packs that add or override definitions.

use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

#[derive(Debug, thiserror::Error)]
pub enum DbError {
    #[error("cannot read {0}: {1}")]
    Io(PathBuf, std::io::Error),
    #[error("invalid JSON in {0}: {1}")]
    Json(PathBuf, serde_json::Error),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum SpnKind {
    Numeric,
    /// Character data, possibly variable length and `*`-delimited.
    Ascii,
    /// Discrete states with a bit-decoding table.
    Discrete,
    /// Opaque bytes (no scaling information).
    Bytes,
}

#[derive(Clone, Debug, Serialize)]
pub struct SpnDef {
    pub spn: u32,
    pub name: String,
    pub acronym: String,
    pub units: String,
    /// Bit length; `None` for variable-length fields.
    pub length: Option<u32>,
    pub resolution: f64,
    pub offset: f64,
    pub op_low: Option<f64>,
    pub op_high: Option<f64>,
    pub kind: SpnKind,
    /// Legacy-schema start bit (0 = LSB of byte 1).
    #[serde(skip)]
    legacy_start: Option<u32>,
}

#[derive(Clone, Debug, Serialize)]
pub struct SpnSlot {
    pub spn: u32,
    /// Start bit within the PG (0 = LSB of byte 1); `None` when unknown or split.
    pub start_bit: Option<u32>,
}

#[derive(Clone, Debug, Serialize)]
pub struct PgnDef {
    pub pgn: u32,
    pub label: String,
    pub name: String,
    pub length: Option<usize>,
    pub rate: String,
    pub spns: Vec<SpnSlot>,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct DbMeta {
    pub sources: Vec<String>,
    /// True if only the skeleton (no licensed content) is loaded.
    pub skeleton_only: bool,
}

#[derive(Clone, Debug, Default)]
pub struct CompiledDb {
    pub pgns: HashMap<u32, PgnDef>,
    pub spns: HashMap<u32, SpnDef>,
    pub source_addresses: HashMap<u8, String>,
    pub fmi: HashMap<u8, (String, String)>,
    pub bit_decodings: HashMap<u32, HashMap<u64, String>>,
    pub manufacturers: HashMap<u16, String>,
    pub meta: DbMeta,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum SpnStatus {
    Valid,
    NotAvailable,
    Error,
    /// Parameter-specific or reserved indicator range.
    Reserved,
    OutOfRange,
    /// Field lies outside the received data.
    Missing,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct SpnValue {
    pub spn: u32,
    pub raw: Option<u64>,
    pub value: Option<f64>,
    pub status: SpnStatus,
    /// ASCII content or bit-decoding text.
    pub text: Option<String>,
}

fn num(v: Option<&Value>) -> Option<f64> {
    match v? {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

fn text(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        _ => String::new(),
    }
}

/// Resolve the database path using the documented search order.
pub fn locate(explicit: Option<&Path>) -> Option<PathBuf> {
    if let Some(p) = explicit {
        return Some(p.to_path_buf());
    }
    if let Some(p) = std::env::var_os("CSU_J1939DB") {
        return Some(PathBuf::from(p));
    }
    let exe_dir = std::env::current_exe().ok().and_then(|e| e.parent().map(Path::to_path_buf));
    for name in ["J1939db.licensed.json", "J1939db.json"] {
        let mut dirs = vec![PathBuf::from(".")];
        dirs.extend(exe_dir.clone());
        for d in dirs {
            let p = d.join(name);
            if p.is_file() {
                return Some(p);
            }
        }
    }
    None
}

impl CompiledDb {
    pub fn load(path: &Path) -> Result<Self, DbError> {
        let bytes = std::fs::read(path).map_err(|e| DbError::Io(path.to_path_buf(), e))?;
        let json: Value = serde_json::from_slice(&bytes).map_err(|e| DbError::Json(path.to_path_buf(), e))?;
        let mut db = Self::from_json(&json);
        db.meta.sources = vec![path.display().to_string()];
        Ok(db)
    }

    /// Load the database found by [`locate`]; an empty database if none.
    pub fn load_default(explicit: Option<&Path>) -> Result<Self, DbError> {
        match locate(explicit) {
            Some(p) => Self::load(&p),
            None => Ok(CompiledDb { meta: DbMeta { sources: vec![], skeleton_only: true }, ..Default::default() }),
        }
    }

    pub fn from_json(json: &Value) -> Self {
        let mut db = CompiledDb::default();
        db.meta.skeleton_only = json.pointer("/_meta/skeleton").and_then(Value::as_bool).unwrap_or(false);

        if let Some(spns) = json.get("J1939SPNdb").and_then(Value::as_object) {
            for (k, v) in spns {
                let Ok(spn) = k.parse::<u32>() else { continue };
                db.spns.insert(spn, parse_spn(spn, v));
            }
        }
        if let Some(bits) = json.get("J1939BitDecodings").and_then(Value::as_object) {
            for (k, table) in bits {
                let Ok(spn) = k.parse::<u32>() else { continue };
                let Some(table) = table.as_object() else { continue };
                let m = table
                    .iter()
                    .filter_map(|(raw, t)| Some((raw.trim().parse::<u64>().ok()?, t.as_str()?.trim().to_string())))
                    .collect();
                db.bit_decodings.insert(spn, m);
            }
        }
        for spn in db.bit_decodings.keys() {
            if let Some(d) = db.spns.get_mut(spn) {
                if d.kind == SpnKind::Numeric && d.length.is_some_and(|l| l <= 8) {
                    d.kind = SpnKind::Discrete;
                }
            }
        }
        if let Some(pgns) = json.get("J1939PGNdb").and_then(Value::as_object) {
            for (k, v) in pgns {
                let Ok(pgn) = k.parse::<u32>() else { continue };
                let def = parse_pgn(pgn, v, &db.spns);
                db.pgns.insert(pgn, def);
            }
        }
        if let Some(sa) = json.get("J1939SATabledb").and_then(Value::as_object) {
            for (k, v) in sa {
                if let (Ok(a), Some(n)) = (k.parse::<u8>(), v.as_str()) {
                    db.source_addresses.insert(a, n.to_string());
                }
            }
        }
        if let Some(fmi) = json.get("J1939FMITabledb").and_then(Value::as_object) {
            for (k, v) in fmi {
                if let Ok(f) = k.parse::<u8>() {
                    db.fmi.insert(f, (text(v.get("Name")), text(v.get("Severity"))));
                }
            }
        }
        if let Some(mfr) = json.get("J1939Manufacturerdb").and_then(Value::as_object) {
            for (k, v) in mfr {
                if let (Ok(c), Some(n)) = (k.parse::<u16>(), v.as_str()) {
                    db.manufacturers.insert(c, n.to_string());
                }
            }
        }
        db
    }

    /// Layer `other` on top of `self` (data packs override the base).
    pub fn merge(&mut self, other: CompiledDb) {
        self.pgns.extend(other.pgns);
        self.spns.extend(other.spns);
        self.source_addresses.extend(other.source_addresses);
        self.fmi.extend(other.fmi);
        self.bit_decodings.extend(other.bit_decodings);
        self.manufacturers.extend(other.manufacturers);
        self.meta.skeleton_only &= other.meta.skeleton_only;
        self.meta.sources.extend(other.meta.sources);
    }

    pub fn sa_name(&self, sa: u8) -> &str {
        self.source_addresses.get(&sa).map(String::as_str).unwrap_or(match sa {
            0xFE => "Null Address",
            0xFF => "Global",
            _ => "Unknown",
        })
    }

    pub fn pgn_label(&self, pgn: u32) -> (&str, &str) {
        self.pgns.get(&pgn).map(|p| (p.label.as_str(), p.name.as_str())).unwrap_or(("", ""))
    }

    /// Decode every SPN the database defines for `pgn`.
    pub fn decode(&self, pgn: u32, data: &[u8]) -> Vec<SpnValue> {
        let Some(def) = self.pgns.get(&pgn) else { return vec![] };
        let mut out = Vec::with_capacity(def.spns.len());
        let mut ascii_fields: Option<Vec<&[u8]>> = None;
        let mut ascii_index = 0;
        for slot in &def.spns {
            let Some(spn) = self.spns.get(&slot.spn) else { continue };
            let v = match spn.kind {
                SpnKind::Ascii => {
                    let fields = ascii_fields.get_or_insert_with(|| {
                        let start = slot.start_bit.map(|b| (b / 8) as usize).unwrap_or(0).min(data.len());
                        data[start..].split(|b| *b == b'*').collect()
                    });
                    let field = fields.get(ascii_index).copied().unwrap_or(&[]);
                    ascii_index += 1;
                    let s: String = field.iter().filter(|b| b.is_ascii_graphic() || **b == b' ').map(|b| *b as char).collect();
                    SpnValue {
                        spn: spn.spn,
                        raw: None,
                        value: None,
                        status: if field.is_empty() { SpnStatus::Missing } else { SpnStatus::Valid },
                        text: Some(s.trim().to_string()),
                    }
                }
                _ => self.decode_numeric(spn, slot.start_bit, data),
            };
            out.push(v);
        }
        out
    }

    fn decode_numeric(&self, spn: &SpnDef, start: Option<u32>, data: &[u8]) -> SpnValue {
        let missing = SpnValue { spn: spn.spn, raw: None, value: None, status: SpnStatus::Missing, text: None };
        let (Some(start), Some(len)) = (start, spn.length) else { return missing };
        let Some(raw) = extract_bits(data, start as usize, len as usize) else { return missing };
        if spn.kind == SpnKind::Bytes {
            let bytes = &data[(start / 8) as usize..((start + len).div_ceil(8) as usize).min(data.len())];
            return SpnValue { spn: spn.spn, raw: Some(raw), value: None, status: SpnStatus::Valid, text: Some(csu_bus::hex(bytes)) };
        }
        let mut status = classify(raw, len);
        let value = raw as f64 * spn.resolution + spn.offset;
        if status == SpnStatus::Valid {
            if spn.op_high.is_some_and(|h| value > h + 1e-9) || spn.op_low.is_some_and(|l| value < l - 1e-9) {
                status = SpnStatus::OutOfRange;
            }
        }
        let text = self.bit_decodings.get(&spn.spn).and_then(|t| t.get(&raw)).cloned();
        SpnValue {
            spn: spn.spn,
            raw: Some(raw),
            value: matches!(status, SpnStatus::Valid | SpnStatus::OutOfRange).then_some(value),
            status: if text.is_some() && spn.kind == SpnKind::Discrete { SpnStatus::Valid } else { status },
            text,
        }
    }
}

fn parse_spn(spn: u32, v: &Value) -> SpnDef {
    let units = text(v.get("Units"));
    let length = num(v.get("SPNLength")).filter(|l| *l > 0.0).map(|l| l as u32);
    let resolution = num(v.get("Resolution")).unwrap_or(0.0);
    // Legacy-schema resolution codes: -2 = ASCII, -3 = binary (raw integer).
    let binary = resolution == -3.0 || units.eq_ignore_ascii_case("binary");
    let kind = if units.eq_ignore_ascii_case("ASCII") || length.is_none() {
        SpnKind::Ascii
    } else if resolution > 0.0 || binary {
        SpnKind::Numeric
    } else if units.eq_ignore_ascii_case("bit") || units.eq_ignore_ascii_case("binary") {
        SpnKind::Discrete
    } else {
        SpnKind::Bytes
    };
    SpnDef {
        spn,
        name: text(v.get("Name")),
        acronym: text(v.get("Acronym")),
        units,
        length,
        resolution: if resolution > 0.0 { resolution } else { 1.0 },
        offset: num(v.get("Offset")).unwrap_or(0.0),
        op_low: num(v.get("OperationalLow")),
        op_high: num(v.get("OperationalHigh")),
        kind,
        legacy_start: num(v.get("StartBit")).filter(|b| *b >= 0.0).map(|b| b as u32),
    }
}

fn parse_pgn(pgn: u32, v: &Value, spns: &HashMap<u32, SpnDef>) -> PgnDef {
    let list: Vec<u32> = v
        .get("SPNs")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|x| num(Some(x)).map(|n| n as u32)).collect())
        .unwrap_or_default();
    let starts = v.get("SPNStartBits").and_then(Value::as_array);
    let slots = list
        .iter()
        .enumerate()
        .map(|(i, spn)| {
            let start_bit = match starts.and_then(|s| s.get(i)) {
                Some(Value::Array(parts)) if parts.len() == 1 => num(parts.first()),
                Some(Value::Array(_)) => None, // split across non-contiguous bytes: not yet supported
                Some(x) => num(Some(x)),
                None => spns.get(spn).and_then(|d| d.legacy_start.map(f64::from)),
            }
            .filter(|b| *b >= 0.0)
            .map(|b| b as u32);
            SpnSlot { spn: *spn, start_bit }
        })
        .collect();
    PgnDef {
        pgn,
        label: text(v.get("Label")),
        name: text(v.get("Name")),
        length: num(v.get("PGNLength")).map(|l| l as usize),
        rate: text(v.get("Rate")),
        spns: slots,
    }
}

/// Little-endian bit field extraction with J1939 numbering (bit 0 = LSB of
/// byte 0). Returns `None` if the field does not fit in `data` or exceeds 64 bits.
pub fn extract_bits(data: &[u8], start: usize, len: usize) -> Option<u64> {
    if len == 0 || len > 64 || start + len > data.len() * 8 {
        return None;
    }
    let first = start / 8;
    let last = (start + len - 1) / 8;
    let mut acc: u128 = 0;
    for (i, b) in data[first..=last].iter().enumerate() {
        acc |= (*b as u128) << (8 * i);
    }
    acc >>= start % 8;
    let mask = if len == 64 { u64::MAX as u128 } else { (1u128 << len) - 1 };
    Some((acc & mask) as u64)
}

/// J1939-71 "not available / error / parameter specific" indicator ranges.
pub fn classify(raw: u64, len: u32) -> SpnStatus {
    match len {
        0 | 1 => SpnStatus::Valid,
        2..=7 => {
            let all = (1u64 << len) - 1;
            if raw == all {
                SpnStatus::NotAvailable
            } else if raw == all - 1 {
                SpnStatus::Error
            } else {
                SpnStatus::Valid
            }
        }
        _ => match (raw >> (len - 8)) & 0xFF {
            0xFF => SpnStatus::NotAvailable,
            0xFE => SpnStatus::Error,
            0xFB..=0xFD => SpnStatus::Reserved,
            _ => SpnStatus::Valid,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn bit_extraction() {
        let d = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0];
        assert_eq!(extract_bits(&d, 0, 8), Some(0x12));
        assert_eq!(extract_bits(&d, 8, 16), Some(0x5634));
        assert_eq!(extract_bits(&d, 4, 4), Some(0x1));
        assert_eq!(extract_bits(&d, 0, 64), Some(0xF0DEBC9A78563412));
        assert_eq!(extract_bits(&d, 62, 2), Some(0b11));
        assert_eq!(extract_bits(&d, 60, 8), None);
    }

    #[test]
    fn indicator_ranges() {
        assert_eq!(classify(0xFF, 8), SpnStatus::NotAvailable);
        assert_eq!(classify(0xFE, 8), SpnStatus::Error);
        assert_eq!(classify(0xFA, 8), SpnStatus::Valid);
        assert_eq!(classify(0xFFFF, 16), SpnStatus::NotAvailable);
        assert_eq!(classify(0xFE12, 16), SpnStatus::Error);
        assert_eq!(classify(0xFB00_0000, 32), SpnStatus::Reserved);
        assert_eq!(classify(0b11, 2), SpnStatus::NotAvailable);
        assert_eq!(classify(0b10, 2), SpnStatus::Error);
    }

    /// Example definitions in the manufacturer-proprietary ranges (no SAE content).
    fn sample(schema_current: bool) -> Value {
        let mut pgn = json!({"Label": "EXPROP", "Name": "Example proprietary B", "PGNLength": "8",
                             "Rate": "100 ms", "SPNs": [520192, 520193, 520194]});
        if schema_current {
            pgn["SPNStartBits"] = json!([0, 16, 24]);
        }
        let mut spns = json!({
            "520192": {"Name": "Example speed", "SPNLength": 16, "Resolution": 0.125, "Offset": 0,
                       "OperationalLow": 0, "OperationalHigh": 8031.875, "Units": "rpm"},
            "520193": {"Name": "Example switch", "SPNLength": 2, "Resolution": 1, "Offset": 0,
                       "OperationalLow": 0, "OperationalHigh": 3, "Units": "bit"},
            "520194": {"Name": "Example temperature", "SPNLength": 8, "Resolution": 1, "Offset": -40,
                       "OperationalLow": -40, "OperationalHigh": 210, "Units": "C"}
        });
        if !schema_current {
            spns["520192"]["StartBit"] = json!(0);
            spns["520193"]["StartBit"] = json!(16);
            spns["520194"]["StartBit"] = json!(24);
        }
        json!({"J1939PGNdb": {"65280": pgn}, "J1939SPNdb": spns,
               "J1939BitDecodings": {"520193": {"0": "off", "1": "on", "2": "error", "3": "not available"}},
               "J1939SATabledb": {"0": "Example Engine"}})
    }

    #[test]
    fn decodes_both_schemas_identically() {
        let data = [0x40, 0x1F, 0x01, 0x82, 0xFF, 0xFF, 0xFF, 0xFF];
        for current in [false, true] {
            let db = CompiledDb::from_json(&sample(current));
            let v = db.decode(65280, &data);
            assert_eq!(v.len(), 3, "schema current={current}");
            assert_eq!(v[0].value, Some(0x1F40 as f64 * 0.125));
            assert_eq!(v[1].text.as_deref(), Some("on"));
            assert_eq!(v[2].value, Some(130.0 - 40.0));
            assert_eq!(db.sa_name(0), "Example Engine");
        }
    }

    #[test]
    fn not_available_has_no_value() {
        let db = CompiledDb::from_json(&sample(true));
        let v = db.decode(65280, &[0xFF; 8]);
        assert_eq!(v[0].status, SpnStatus::NotAvailable);
        assert_eq!(v[0].value, None);
    }

    #[test]
    fn layering_overrides() {
        let mut base = CompiledDb::from_json(&sample(true));
        let pack = CompiledDb::from_json(&json!({"J1939SATabledb": {"0": "OEM Engine Controller"}}));
        base.merge(pack);
        assert_eq!(base.sa_name(0), "OEM Engine Controller");
    }
}
