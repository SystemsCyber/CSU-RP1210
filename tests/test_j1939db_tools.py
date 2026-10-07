"""Tests for j1939db_tools: generation, unit conversion, validation, comparison, vectors."""

import copy
import json
import os
import shutil
import subprocess

import pytest

import j1939db_tools as tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def table():
    return tools.UnitTable()


def checks_by_name(checks):
    return {c.name: c for c in checks}


# ---- Units ------------------------------------------------------------------

@pytest.mark.parametrize("raw,canon", [("°C", "degc"), ("deg C", "degc"), ("degc", "degc"), ("kPa", "kpa"),
                                       ("N·m", "nm"), ("N m", "nm"), ("km/h", "km/h"), ("m/s²", "m/s2")])
def test_canonical_unit(raw, canon):
    assert tools.canonical_unit(raw) == canon


def test_unit_table_systems(table):
    assert table.system_of("°C") == tools.METRIC
    assert table.system_of("deg F") == tools.US
    assert table.system_of("psi") == tools.US
    assert table.system_of("rpm") is None
    assert table.metric_label("degc") == "deg C"
    assert table.metric_label("ascii") == "ASCII"


def test_temperature_conversion_matches_legacy_database(table):
    db = {"J1939SPNdb": {"110": {"Units": "deg C", "Resolution": 1, "Offset": -40, "OperationalLow": -40,
                                 "OperationalHigh": 210, "DataRange": "-40 to 210 deg C"}}}
    us, counts = tools.to_us_customary(db, table)
    spn = us["J1939SPNdb"]["110"]
    # Identical to the original CSU-RP1210 (US) database entry for SPN 110.
    assert (spn["Units"], spn["Resolution"], spn["Offset"], spn["OperationalHigh"]) == ("deg F", 1.8, -40.0, 410.0)
    assert spn["DataRange"] == "-40 to 410 deg F"
    assert spn["MetricUnits"] == "deg C"
    assert counts == {"deg C -> deg F": 1}
    assert db["J1939SPNdb"]["110"]["Units"] == "deg C", "input must not be modified"


def test_unconvertible_units_untouched(table):
    db = {"J1939SPNdb": {"190": {"Units": "rpm", "Resolution": 0.125, "Offset": 0}}}
    us, counts = tools.to_us_customary(db, table)
    assert us["J1939SPNdb"]["190"] == db["J1939SPNdb"]["190"]
    assert counts == {}


# ---- Start bits ---------------------------------------------------------------

@pytest.mark.parametrize("entry,length,expected", [
    ([0], 16, 0), ([0, 24], 32, 0), ([32, 56], 32, 32), ([12, 16], 12, 12),
    ([40, 621], 12, None), ([-1], 8, None), (24, 8, 24), ([0, 8], 8, None),
])
def test_resolve_start_bit(entry, length, expected):
    assert tools.resolve_start_bit(entry, length) == expected


def test_extract_and_classify():
    data = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0])
    assert tools.extract_bits(data, 8, 16) == 0x5634
    assert tools.extract_bits(data, 60, 8) is None
    assert tools.classify(0xFF, 8) == "NotAvailable"
    assert tools.classify(0xFE12, 16) == "Error"
    assert tools.classify(0b11, 2) == "NotAvailable"


# ---- Generation ---------------------------------------------------------------

def test_generate_writes_both_unit_systems(generated):
    assert set(generated) == {tools.METRIC, tools.US}
    for system, path in generated.items():
        assert os.path.basename(path) == tools.OUTPUT_NAMES[system]
        db = tools.load(path)
        assert db["_meta"]["units"] == system
        assert db["_meta"]["skeleton"] is False
        assert db["_meta"]["sources"][0]["file"] == "synthetic_da.xlsx"
        assert len(db["_meta"]["sources"][0]["sha256"]) == 64


def test_generated_metric_and_us_values(generated):
    metric = tools.load(generated[tools.METRIC])["J1939SPNdb"]
    us = tools.load(generated[tools.US])["J1939SPNdb"]
    assert metric["520193"]["Units"] == "deg C" and us["520193"]["Units"] == "deg F"
    assert metric["520194"]["Units"] == "kPa" and us["520194"]["Units"] == "psi"
    assert us["520194"]["Resolution"] == pytest.approx(4 * 0.1450377377)
    assert metric["520195"]["Units"] == "km/h" and us["520195"]["Units"] == "mph"
    assert metric["520198"]["Units"] == "L" and us["520198"]["Units"] == "gallons"
    assert metric["520192"]["Units"] == us["520192"]["Units"] == "rpm"


def test_generated_has_legacy_fields_for_python_app(generated):
    db = tools.load(generated[tools.METRIC])
    spns = db["J1939SPNdb"]
    assert spns["520192"]["StartBit"] == 0 and spns["520192"]["EndBit"] == 15
    assert spns["520197"]["StartBit"] == 0, "4-byte field from [0, 24] start list"
    assert spns["520198"]["StartBit"] == 32
    assert spns["520199"]["Units"] == "ASCII"
    for table_name in ("J1939FMITabledb", "J1939SAHWTabledb", "J1939OBDTabledb", "J1939LampFlashTabledb"):
        assert table_name in db
    assert db["J1939BitDecodings"]["520196"]["1"] == "on"


def test_generate_rejects_non_digital_annex(tmp_path):
    pytest.importorskip("pretty_j1939")
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["not", "a", "digital", "annex"])
    path = str(tmp_path / "other.xlsx")
    wb.save(path)
    with pytest.raises(ValueError):
        tools.generate([path], str(tmp_path / "out"), log=lambda *_: None)


# ---- Validation -----------------------------------------------------------------

def test_generated_databases_validate(generated, table):
    for path in generated.values():
        checks = tools.validate(tools.load(path), table)
        assert not [c for c in checks if c.level == tools.FAIL], checks
        named = checks_by_name(checks)
        assert named["Content"].level == tools.WARN  # synthetic DA is tiny
        assert named["Python app compatibility"].level == tools.PASS
        assert named["Unit system"].level == tools.PASS


def test_skeleton_validates_with_warning(table):
    checks = checks_by_name(tools.validate(tools.load(os.path.join(ROOT, "J1939db.json")), table))
    assert checks["Content"].level == tools.WARN
    assert checks["SPN references"].level == tools.PASS


def test_validation_detects_broken_database(generated, table):
    db = tools.load(generated[tools.METRIC])
    db["J1939PGNdb"]["65280"]["SPNs"].append(529999)
    db["J1939PGNdb"]["65281"]["SPNStartBits"].pop()
    db["J1939SPNdb"]["520192"]["Resolution"] = "fast"
    db["J1939SPNdb"]["520193"]["Units"] = "deg F"  # wrong system for a metric file
    checks = checks_by_name(tools.validate(db, table))
    assert checks["SPN references"].level == tools.FAIL
    assert checks["Start-bit lists"].level == tools.FAIL
    assert checks["Numeric fields"].level == tools.FAIL
    assert checks["Unit system"].level == tools.FAIL


def test_missing_core_tables_fail(table):
    assert tools.validate({"J1939PGNdb": {}}, table)[0].level == tools.FAIL


# ---- Comparison -----------------------------------------------------------------

def test_compare_metric_and_us_conversion(generated, table):
    us, metric = tools.load(generated[tools.US]), tools.load(generated[tools.METRIC])
    checks = checks_by_name(tools.compare(us, metric, table))
    assert checks["Unit conversion"].level == tools.PASS
    assert "6 converted SPNs consistent" in checks["Unit conversion"].message
    assert checks["Scaling changes"].level == tools.PASS


def test_compare_detects_bad_conversion(generated, table):
    us, metric = tools.load(generated[tools.US]), tools.load(generated[tools.METRIC])
    us["J1939SPNdb"]["520194"]["Resolution"] = 0.6  # should be 0.5801509508
    checks = checks_by_name(tools.compare(us, metric, table))
    assert checks["Unit conversion"].level == tools.FAIL


def test_compare_versions(generated, table):
    old = tools.load(generated[tools.METRIC])
    new = copy.deepcopy(old)
    new["J1939PGNdb"]["65290"] = {"Label": "N", "Name": "New", "PGNLength": "8", "SPNs": []}
    del new["J1939PGNdb"]["65282"]
    new["J1939SPNdb"]["520192"]["Resolution"] = 0.25
    checks = checks_by_name(tools.compare(new, old, table))
    assert "1 PGNs not in baseline" in checks["PGNs added"].message
    assert checks["PGNs removed"].level == tools.WARN
    assert checks["Scaling changes"].level == tools.WARN


# ---- Digital Annex SLOT cross-check ---------------------------------------------------

def test_value_only_columns_correct_text_parsing(generated):
    """'0.1 m/s² per bit' is parsed as 0.05 by pretty_j1939; the value-only column wins."""
    metric = tools.load(generated[tools.METRIC])
    assert metric["J1939SPNdb"]["520200"]["Resolution"] == 0.1
    assert metric["_meta"]["scaling_corrections"] == [
        {"spn": 520200, "parsed": [0.05, -12.5], "value_only": [0.1, -12.5]}]
    us = tools.load(generated[tools.US])
    assert us["J1939SPNdb"]["520200"]["Resolution"] == pytest.approx(0.1 * 3.280839895)


def test_slot_crosscheck_passes(synthetic_workbook, generated, table):
    checks = checks_by_name(tools.slot_crosscheck([synthetic_workbook], tools.load(generated[tools.METRIC]),
                                                  tools.load(generated[tools.US]), table))
    for name in ("DA rows vs SLOT sheet", "Metric scaling vs SLOT", "Metric units vs SLOT",
                 "Length vs SLOT limits", "US scaling vs SLOT conversion"):
        assert checks[name].level == tools.PASS, checks[name]
    assert "6 numeric SLOTs in 6 units" in checks["SLOT units converted to US"].message
    assert checks["SLOT units kept as published"].details == ["rpm: 1 SLOTs"]


def test_slot_crosscheck_detects_errors(synthetic_workbook, generated, table):
    metric, us = tools.load(generated[tools.METRIC]), tools.load(generated[tools.US])
    metric["J1939SPNdb"]["520192"]["Resolution"] = 0.25
    metric["J1939SPNdb"]["520194"]["Units"] = "bar"
    us["J1939SPNdb"]["520193"]["Offset"] = -39.0
    checks = checks_by_name(tools.slot_crosscheck([synthetic_workbook], metric, us, table))
    assert checks["Metric scaling vs SLOT"].level == tools.FAIL
    assert checks["Metric units vs SLOT"].level == tools.WARN
    assert checks["US scaling vs SLOT conversion"].level == tools.FAIL


def test_cli_validate_with_slots(synthetic_workbook, generated):
    rc = tools.main(["validate", generated[tools.US], "--baseline", generated[tools.METRIC],
                     "--da", synthetic_workbook])
    assert rc == 0


@pytest.mark.parametrize("a,b", [("km⁻¹", "1/km"), ("mg/m³", "mg/m3"), ("kg•m²", "kg*m2"), ("µs", "us")])
def test_superscript_units_compare_equal(table, a, b):
    assert tools._same_unit(a, b, table)


# ---- Vectors ------------------------------------------------------------------------

def test_vectors_pass_in_both_unit_systems(generated, table):
    vectors = tools.load_vectors()
    for system, path in generated.items():
        results = tools.run_vectors(tools.load(path), vectors, table)
        statuses = [r[1] for r in results]
        assert "FAIL" not in statuses, [(v["name"], s, m) for v, s, m in results if s == "FAIL"]
        assert statuses.count("PASS") >= 6, system


def test_vector_failure_detected(generated, table):
    v = {"name": "wrong", "pgn": 65280, "spn": 520193, "data": "401F3C82006400FF",
         "expected": {"metric": 25.0, "us": 77.0}, "tolerance": 0.01}
    status, msg = tools.run_vector(tools.load(generated[tools.METRIC]), v, table)
    assert status == "FAIL" and "20" in msg


def test_vectors_round_trip(tmp_path):
    vectors = tools.load_vectors()
    path = str(tmp_path / "v.json")
    tools.save_vectors(vectors, path)
    assert tools.load_vectors(path) == vectors


# ---- Settings and search order ------------------------------------------------------

def test_unit_preference_and_candidates(tmp_path, monkeypatch):
    monkeypatch.delenv("CSU_UNITS", raising=False)
    assert tools.read_unit_preference(str(tmp_path)) == tools.METRIC
    tools.write_unit_preference(tools.US, str(tmp_path))
    assert tools.read_unit_preference(str(tmp_path)) == tools.US
    names = [os.path.basename(p) for p in tools.database_candidates(str(tmp_path), tools.US)]
    assert names == ["J1939db.us.licensed.json", "J1939db.licensed.json", "J1939db.json"]
    monkeypatch.setenv("CSU_UNITS", "metric")
    assert tools.read_unit_preference(str(tmp_path)) == tools.METRIC


def test_licensed_outputs_are_git_ignored():
    if shutil.which("git") is None:
        pytest.skip("git not available")
    for name in tools.OUTPUT_NAMES.values():
        result = subprocess.run(["git", "check-ignore", "-q", name], cwd=ROOT)
        assert result.returncode == 0, f"{name} must be git-ignored"


def test_cli_validate(generated):
    rc = tools.main(["validate", generated[tools.US], "--baseline", generated[tools.METRIC]])
    assert rc == 0


# ---- Rust and Python decoders agree ----------------------------------------------------

def _csu_binary():
    for build in ("debug", "release"):
        exe = os.path.join(ROOT, "target", build, "csu.exe" if os.name == "nt" else "csu")
        if os.path.exists(exe):
            return exe
    return None


@pytest.mark.parametrize("system", [tools.METRIC, tools.US])
def test_rust_core_decodes_like_python(generated, tmp_path, system):
    exe = _csu_binary()
    if exe is None:
        pytest.skip("build the Rust core first (cargo build)")
    log = tmp_path / "frames.log"
    log.write_text("(1.0) can0 18FF0000#401F3C64006400FF\n(1.1) can0 18FF0100#00350C00C8000000\n")
    out = subprocess.run([exe, "--db", generated[system], "decode", str(log)],
                         capture_output=True, text=True, check=True).stdout
    db = tools.load(generated[system])
    for line in out.splitlines():
        msg = json.loads(line)
        data = bytes.fromhex(msg["data"])
        for spn in msg["spns"]:
            if spn["value"] is None:
                continue
            expected = tools.decode_spn(db, msg["pgn"], spn["spn"], data)["value"]
            assert spn["value"] == pytest.approx(expected), (system, spn)
