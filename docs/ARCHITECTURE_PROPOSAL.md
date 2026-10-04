# CSU-RP1210 Next-Generation Architecture Proposal

Status: **Approved direction, Phase 1 started** · 2026-10-04

## Decisions and progress

| # | Decision | Outcome |
|---|---|---|
| 1 | Core language | **Rust core** approved. Workspace in `crates/`: `csu-bus`, `csu-j1939`, `csu-sec`, `csu` (CLI + web UI server). |
| 2 | GUI | **Open.** Recommendation: web UI in a Tauri desktop shell (see §3.3). A working web tree view (`csu serve`) is available to judge speed. |
| 3 | `J1939db.json` | Now a **skeleton** with the schema and illustrative proprietary-range examples only. The licensed database goes in `J1939db.licensed.json` (git-ignored), `$CSU_J1939DB`, or `--db`. Both the Python app and the Rust core follow that search order. |
| 4 | J1939-91C details | **Stubbed** (`TODO(J1939-91C)`). Only content from the public Golden Tester paper is implemented, and its four vectors pass. |
| 5 | Architecture model | SysML v2 textual model in [`model/sysml/`](../model/sysml/README.md). |

**Implemented so far** (`cargo test --workspace`: 43 tests pass):
- **Backends:** RP1210, PCAN-Basic (classic and FD), SocketCAN (FD), candump replay, virtual loopback. Plus discovery of installed RP1210 drivers, including whether each DLL is 32- or 64-bit.
- **J1939 stack:** identifier codec with the DP/EDP bits kept; J1939-21 TP with timeouts and aborts; DA decoder for the legacy and current schemas, with data-pack layering and J1939-71 not-available/error ranges; DM1-family decoding; NAME decoding; and the network tree with per-byte statistics.
- **J1939-91C:** CMAC tag, frame layout, freshness tracking. Formation, KDF and certificate checks are stubs.
- **`csu` command:** `devices`, `decode`, `summary`, `bench`, `serve`.

**Measured:**
- **Offline throughput:** 2.74 M frames/s (365 ns/frame) for ID decode, TP, all SPNs and tree statistics, release build.
- **Hardware smoke tests on the dev laptop:**
  - The PEAKRP32 x64 RP1210 DLL loads and `ClientConnect` succeeds.
  - The 32-bit-only DGDPA5MA DLL loads in the `i686` build, which is all the "RP1210 bridge" needs to be.
  - PCANBasic loads and reports driver status (no adapter was attached).
- **Linux:** the backends type-check for `x86_64-unknown-linux-gnu`. They haven't been run, because WSL here has no `vcan` module.

**Data note:** the legacy database has US customary units baked into Resolution/Offset (e.g. coolant in °F). Databases generated with pretty_j1939 are SI.

This document evaluates the current CSU-RP1210 code base and proposes a target architecture for:

- a faster, more condensed core (with a C++ vs. Rust evaluation)
- native transports: RP1210 (Windows), PEAK PCAN USB CAN FD, and SocketCAN (Linux)
- comprehensive diagnostics (J1939-73, ISO 15765 / UDS, J1587)
- a proprietary plugin architecture for forensic data interpretation
- an MCP server for network visualization and AI context sharing
- SAE J1939-91C (CAN FD secure messaging), informed by Zachos & Medam, *"The Golden Tester"*, SAE 2026-01-0092

It also summarizes what we can reuse from [canarchy](https://github.com/hexsecs/canarchy), [pretty_j1939](https://github.com/nmfta-repo/pretty_j1939) and [TruckDevil](https://github.com/LittleBlondeDevil/TruckDevil).

---

## 1. Current-state evaluation

The code base is about 5,460 lines of Python across 10 modules. It is a PyQt5 desktop app that requires **32-bit Python on Windows** (`CSU_RP1210.py:82`) and talks to vehicle networks only through RP1210 DLLs loaded with `ctypes.windll`.

### 1.1 Structural problems

| Problem | Where | Consequence |
|---|---|---|
| Protocol decoding lives inside Qt widgets | `J1939Tab.fill_j1939_table`, `look_up_spns`, `get_DM` | It can't run headless, from a CLI or MCP, or under unit test. |
| The hardware layer shows UI dialogs | `RP1210.py` (`QMessageBox`, `QInputDialog`) | The driver code can't be reused outside the GUI. |
| A single mutable `data_package` dict is shared everywhere | `CSU_RP1210.create_new` | There is no schema or provenance, and each tab writes into it. |
| The only source is a RP1210 J1939 client, which reassembles TP in the driver | `RP1210ReadMessageThread` | There is no J1939 TP stack of our own. Raw CAN, SocketCAN and PCAN need one, and J1939-22 FD transport doesn't exist at all. |
| Windows-only side effects | `os.system("TASKKILL /F /IM DGServer2.exe")` (`CSU_RP1210.py:192`) | Starting the app kills other vendors' processes, and the call fails off Windows. |

### 1.2 Performance hot spots

Most of the slowness is **algorithmic, not "because Python"**:

| Hot spot | Location | Cost |
|---|---|---|
| `data_package[...].update(self.j1939_unique_ids)` runs on **every frame** | `J1939Tab.py:636` | O(#PGNs) per frame |
| `data_package[...].update(self.unique_spns)` runs **inside the per-SPN loop** | `J1939Tab.py:878` | O(#SPNs²) per frame |
| `base64.b64encode` on every frame just to detect a change | `J1939Tab.py:482,540` | Allocates and encodes for every frame |
| `self.pgn_rows.index(pgn_key)` and `list(self.unique_spns.keys()).index(...)` | `J1939Tab.py:529,865` | O(n) list scans per frame |
| Bit masks are built one bit at a time in a Python loop | `J1939Tab.py:811-814` | Up to 64 iterations per SPN |
| String-keyed DB lookups: `"{}".format(spn)` and `repr((pgn, sa))` | throughout | String formatting and hashing on every lookup |
| `QCoreApplication.processEvents()` in the data path, and `layoutChanged` full model resets | `J1939Tab.py:517-525` | The UI re-lays out on the receive path. |
| The time-budget guard compares seconds to `update_rate = 100` (ms), so it never fires | `CSU_RP1210.py:993` | The UI timer can starve. |
| A 3.5 MB `J1939db.json` is parsed at startup | `CSU_RP1210.py:115` | Slow start, and memory goes to string-keyed dicts. |

**Takeaway:** fixing these in Python (precompiled SPN decoders, integer keys, incremental model updates, and no `data_package` copy per frame) would remove most of the live-view lag. A native core is still justified, for the reasons in §2. Speed of live display is not the main one.

### 1.3 Defects found during review

- `RP1210.py:607`: the firmware version shows the API string (`FW = chAPIVersionInfo.value`).
- `RP1210.py:657-682`: `get_last_error_msg` calls `QInputDialog.getInt(self, …)` on a non-widget and uses the undefined names `nclientID` and `clientID`, so it always raises.
- `RP1210.py:362`: "simultaneous CAN channels" reads `status_bytes[1]`, the client count.
- `CSU_RP1210.py:341,343`: `key == ["J1939"]` compares a string with a list, so the "Save as CSV" buttons are never connected (and `save_j1939_csv` doesn't exist).
- `CSU_RP1210.py:1025`: `close_clients` calls the nonexistent `RP1210.disconnectRP1210`. The caller swallows the `AttributeError`, so clients are never closed on reconnect.
- `CSU_RP1210.py:478-544`: `open_open_logger2` uses the undefined `self.title` and `self.export_path`, and `struct.unpack('8B', …)[0]` keeps only byte 0.
- `CSU_RP1210.py:425`: `"patch": CSU_RP1210_version["minor"]`.
- `ISO15765.py:86-93`: `transport_separate_data` refers to the undefined name `block`.
- `ISO15765.py:154`: ISO-TP sessions are keyed only by SA, so concurrent sessions to different DAs collide.
- `J1939Tab.py:703`: `get_DM` doesn't filter the "no DTC" placeholder (SPN 0 / FMI 0, or all `0xFF`) and ignores CM=1 (legacy SPN conversion).
- `J1939Tab.py:895-899`: **"Stop J1939 Broadcast" sends DM13 every 5 s.** This is an active, network-wide command with no confirmation or gating.
- **License inconsistency:** the `CSU_RP1210.py` header says GPL-3.0, but `LICENSE` is MIT. PyQt5 is itself GPL-3.0/commercial, which matters for proprietary plugins (see §5).
- **Data licensing:** `J1939db.json` is derived from the SAE J1939 Digital Annex. Confirm that public redistribution is allowed under CSU's DA license. The data-pack design in §5 removes the need to commit it.

---

## 2. C++ or Rust?

**Recommendation: a Rust core with a thin Python surface, migrated incrementally (strangler pattern). Not a big-bang rewrite, and not C++.**

### Why a native core at all

1. **Raw-frame protocol stacks with real timing.** On SocketCAN and PCAN, we own J1939-21 TP (BAM, RTS/CTS with T1–T4/Tr/Th timers), J1939-22 FD transport and multi-PG containers, ISO-TP flow control, and the J1939-91C response windows (250 ms send / 500 ms round trip). Python's GIL and GC pauses make that timing fragile.
2. **Forensic bulk ingest.** Hours of 500 kbit/s CAN or 2–5 Mbit/s FD logs are tens of millions of frames. A compiled decoder is typically 20–100× faster than per-frame Python dict code.
3. **Untrusted input.** Forensic captures and live buses are adversarial data. Rust's memory safety removes a whole class of parser bugs that C++ would keep.
4. **One headless binary.** A single static executable for a Linux laptop or a Raspberry Pi (the paper's ECU-B platform) or Windows, with no Python runtime to install.
5. **Crypto.** J1939-91C needs AES-CMAC, X25519, a CMAC-based KDF, X.509 parsing and key zeroization, all in well-maintained crates.

### Rust vs. C++ for this project

| Criterion | Rust | C++ |
|---|---|---|
| Memory safety on hostile frames/logs | Enforced by the compiler | Relies on discipline and sanitizers |
| Cross-compiling (Win x86/x64, Linux x64/ARM) | `cargo`, `cross` | CMake plus toolchain files per target |
| CAN backends | `socketcan` crate; `libloading` for RP1210 and PCANBasic DLLs | SocketCAN headers; manual DLL loading |
| Crypto | RustCrypto `aes`/`cmac`, `x25519-dalek`, `x509-cert`, `zeroize` | OpenSSL/mbedTLS (heavier, C APIs) |
| Python bindings | PyO3 + maturin (wheels for every platform) | pybind11 + scikit-build |
| MCP | Official Rust SDK (`rmcp`) | No official SDK |
| Plugin sandbox (WASM) | `wasmtime` is written in Rust (first-class host) | Has a C API, but it's second-class |
| Native Qt GUI in one language | Not idiomatic | Strong |
| AUTOSAR / vendor C stacks (paper's future work) | Via C FFI | Native |

C++ only wins if we want a single-language native Qt Widgets GUI, or deep AUTOSAR BSW integration. Rust can call C stacks through FFI when that's needed.

**The local toolchain is ready:** `rustc`/`cargo` 1.87 are installed. CMake isn't.

### What stays in Python

The GUI, notebooks and scripting, and quick experiments. The Python package becomes `import csu` on top of a PyO3 extension. We should move from PyQt5 to **PySide6 (LGPL)** so that in-process proprietary plugins and commercial distribution aren't bound by PyQt's GPL.

---

## 3. Target architecture

```
                     ┌───────────────────────────────────────────────────────┐
  Front ends         │ PySide6 GUI   CLI (csu)   MCP server   Web dashboard  │
                     └───────────────┬───────────────────────────────────────┘
                                     │ one JSON envelope / typed event stream
                     ┌───────────────▼───────────────────────────────────────┐
  Services           │ Session · Evidence store · Plugin host · Policy gate  │
                     ├───────────────────────────────────────────────────────┤
  Protocols          │ J1939-21/-22 TP · Address claim/NAME · DA decoder     │
                     │ J1939-73 DMs · ISO-TP (CAN/FD) · UDS · J1587/J1708    │
                     │ J1939-91C (passive monitor + lab "Golden Tester")     │
                     ├───────────────────────────────────────────────────────┤
  Frame model        │ Frame { ts_hw, ts_host, channel, id, ext, fd, brs,    │
                     │         esi, dir(rx/tx-echo), data[0..64] }           │
                     ├──────────┬──────────┬───────────┬─────────────────────┤
  Backends (trait)   │ RP1210   │ PCAN     │ SocketCAN │ Replay / Virtual    │
                     │ (Win)    │ Basic    │ (Linux)   │ candump, pcapng,    │
                     │          │ Win+Lin  │           │ MF4, Logger2 .bin   │
                     └──────────┴──────────┴───────────┴─────────────────────┘
```

### 3.1 Workspace layout (condensed: 7 crates plus 1 Python package)

```
crates/
  csu-bus/       Frame model, Bus trait, backends: rp1210, pcan, socketcan, replay, virtual
  csu-j1939/     ID codec, TP (BAM, RTS/CTS + timers), J1939-22 FD-TP & multi-PG,
                 address claim / NAME tracker, compiled DA decoder, J1939-73 DMs
  csu-uds/       ISO 15765-2 (classic + FD escape lengths), ISO 15765-3 over PGN 0xDA00,
                 UDS 14229 decode + client, J1587/J1708 decode
  csu-sec/       J1939-91C: security-overhead parse, FV tracking, CMAC verify,
                 X25519 + CMAC-KDF, X.509 VIN binding, golden vectors
  csu-evidence/  Append-only, hash-chained capture store + provenance records
  csu-plugin/    WASM component host (wasmtime) + out-of-process plugin protocol
  csu-mcp/       MCP server (rmcp), the same services the CLI and GUI use
python/csu/      PyO3 bindings, PySide6 GUI, scripting helpers
```

### 3.2 Transport matrix

| Adapter | Windows | Linux | CAN FD | J1708 |
|---|---|---|---|---|
| RP1210 VDAs (DG, Nexiq, Noregon, …) | RP1210 DLL via `libloading` | n/a | No (RP1210 C) | Yes, if the adapter supports it |
| **PEAK PCAN-USB FD / Pro FD** | **PCAN-Basic** (`PCANBasic.dll`) | **SocketCAN** (mainline `peak_usb` driver), or PCAN-Basic for Linux | **Yes** | No |
| Any SocketCAN device | n/a | `CAN_RAW` + `CAN_RAW_FD_FRAMES`; optional kernel `CAN_J1939` / `CAN_ISOTP` sockets | Yes | No |
| Replay / virtual | Yes | Yes | Yes | Yes |

**On "inheriting" the RP1210 driver for PEAK:** PEAK ships a free [PCAN-RP1210 API](https://www.peak-system.com/products/software/development-packages/pcan-rp1210-api/), an RP1210 **version C** implementation for CAN, J1939 and ISO-TP that installs with their Windows driver setup. It works today through our RP1210 backend, with no code changes, for classic CAN/J1939. **RP1210 C has no CAN FD**, though, so J1939-22 and J1939-91C over PEAK need the native PCAN-Basic backend on Windows. On Linux, PEAK FD adapters already appear as SocketCAN interfaces, so no PEAK-specific code is needed there.

**Bitness:** PEAK's RP1210 DLL supports x64. For vendors whose RP1210 DLLs are still 32-bit only, build a tiny `i686` Rust bridge process (`csu-rp1210-bridge.exe`) that exposes the `Bus` trait over a named pipe. Then the main app no longer needs 32-bit Python.

**Linux kernel stacks:** the kernel's `CAN_J1939` socket (address claiming, TP) and `CAN_ISOTP` are good for *active* sessions. For *forensic/passive* capture we always record raw `CAN_RAW` FD frames and reassemble in userspace, so the evidence holds the bytes that were actually on the wire. The kernel J1939 stack also doesn't implement J1939-22 FD transport.

### 3.3 GUI: Qt desktop or web UI?

**Recommendation: one web front end, shipped three ways:**
1. a **Tauri** desktop app, with the Rust core in-process and the OS WebView2/WebKit
2. `csu serve` for a browser or a Raspberry Pi kiosk, the BallastController HMI pattern
3. MCP `ui://` views inside an AI client

Why speed doesn't favor Qt here:
- **Bus load never reaches the UI.** The UI never sees per-frame traffic. The core aggregates (2.7 M frames/s of headroom) and pushes a bounded snapshot every 250 ms. Rendering cost depends on the number of tree nodes, not on bus load.
- **Updates are incremental.** The tree view updates DOM rows in place (keyed reconciliation), so a 4 Hz refresh of a few hundred nodes is cheap.
- **Qt's advantage is narrow.** Native Qt still wins for one case: a raw-frame trace scrolling through millions of rows. A virtualized canvas or WebGL table closes that gap, so the **raw trace view is the right next benchmark** before committing.
- **One codebase.** A PySide6 GUI would need a second, web-based UI for MCP visualization and kiosks anyway.

The tree view in `crates/csu/web/index.html` is a port of the BallastController `j1939_display` page:
- channel ▸ source address ▸ PGN
- per-byte value heat map with running σ and change counts, from server-side Welford statistics instead of client-side sums
- decoded SPNs and DTCs for the selected node
- NAME/VIN/component identity and a MIL badge per source
- filtering, light and dark themes

---

## 4. Diagnostics scope

| Area | Current | Target |
|---|---|---|
| J1939-21 | Relies on the RP1210 driver | Own TP stack with timeouts and abort reasons; passive and active (CTS) roles |
| J1939-22 (FD) | None | FD transport, multi-PG container parsing, FD frame decoding |
| J1939-81 | None | Address-claim monitor; NAME decode; **NAME following** across SA changes (forensic identity) |
| J1939-73 | DM1, DM2, DM4 (partial) | DM1–DM6, DM11/DM3 (gated), DM12, DM19 (CVN/CAL ID), DM20, DM21, DM23–DM31, lamp *flash* states, CM=1 conversion, FMI table |
| ISO 15765 / UDS | Partial RDBI decode, simple responder | ISO-TP classic + FD; normal fixed addressing over 0xDA00 and 11/29-bit; full service and NRC tables; 0x19 ReadDTCInformation, 0x22 RDBI, 0x31 RoutineControl (gated), session/security tracking |
| J1587/J1708 | Yes (RP1210 only) | Keep, ported to `csu-uds`; it only needs an RP1210 transport |
| Network map | PGN table | ECU inventory: SA → NAME, component ID, VIN, software ID, PGN set, rates, DM status, J1939-91C state |

**Active operations are policy-gated** (see §6.3): DM11/DM3 clears, DM13 stop-broadcast, DM14 memory access, UDS RoutineControl and SecurityAccess, and any fuzzing. The current always-available DM13 button moves behind this gate.

---

## 5. Proprietary plugin architecture for forensic interpretation

MIT licensing allows proprietary plugins, provided the core stays free of GPL code and the GUI binding is LGPL (PySide6). There are three tiers, from safest to most capable:

### Tier 1: Data packs (declarative)
Signed bundles of definitions:
- J1939DA-derived DB, generated from the user's own licensed DA with pretty_j1939's generator
- OEM proprietary PGN/SPN definitions (PropA 0xEF00, PropB 0xFF00–0xFFFF)
- DBC/ARXML
- UDS DID tables

They're compiled to a compact zero-copy binary form, so there is no 3.5 MB JSON parse at startup. Packs can be encrypted per licensee. **This also removes the need to commit SAE data to the public repo.**

### Tier 2: Interpreter plugins (WASM components, sandboxed)
- **Interface:** defined in WIT. The host gives the plugin read-only, time-bounded views: reassembled messages, decoded signals, and an ECU inventory. The plugin returns typed **findings**, such as an event record, a sudden-deceleration snapshot, an ECU trip summary, or an anomaly with confidence.
- **Why WASM:**
  - No filesystem, network or bus access unless granted.
  - **Deterministic**, so the same evidence plus the same plugin hash gives the same result, which matters for repeatable forensic opinions.
  - Language-agnostic (Rust, C, C++, Zig).
  - Ships as compiled bytecode, which gives reasonable IP protection.
- **Manifest:** `id`, `version`, `publisher`, `api_version`, required data packs, capabilities (`decode`, `inventory`, …), and an **ed25519 signature**. The host refuses unsigned plugins unless it's in developer mode.

### Tier 3: Out-of-process plugins (JSON-RPC over stdio or a named pipe)
For plugins that must **talk to the bus**, such as an OEM's request sequence to extract ECU event data, or that need native libraries. They speak the same JSON envelope as the CLI and MCP. They get process and license isolation (a GPL tool like TruckDevil could even be wrapped this way), crash isolation, and **bus access only through the host's policy gate**, never raw.

### Provenance (all tiers)
Every finding records:
- plugin id, version and binary SHA-256
- data-pack hashes
- the evidence segment hash range it read
- host version

The evidence store (`csu-evidence`) is append-only and SHA-256 hash-chained per block. That's a cryptographic upgrade of the CAN Logger 2 CRC block format, and it borrows the session/verify idea from canarchy. It can export to **pcapng (SocketCAN link type, Wireshark-dissectable)**, **candump**, and **ASAM MF4**, and decoded signals to **Parquet**.

---

## 6. MCP server: visualization and AI context

### 6.1 Resources (read-only context)
- `vehicle://network/topology`: nodes are ECUs (SA, NAME, make/model/serial, VIN, software ID, 91C state) and edges are PGN flows (DA-specific and broadcast) with rates. This feeds the graph view.
- `vehicle://dtc/active`, `vehicle://dtc/history`
- `vehicle://session/{id}/summary`: capture metadata, hashes, adapters, bus load, error counters
- `vehicle://security/j1939-91c`: formation/rekey state, FV health, rejected-frame counts

### 6.2 Tools
- **Passive (default):**
  - `list_ecus`, `get_pgn_stats`, `query_signals(spn|name, sa, t0, t1, max_points)` (server-side downsampling)
  - `get_dtcs`, `get_component_info`, `decode_frame`, `search_evidence`
  - `run_plugin(id, segment)`, `security_status`
- **Active (gated):**
  - `request_pgn`, `request_dm(n)`, `uds_read_did`
  - These require `allow_active` in the server config **and** a per-call `ack_active`. They default to `dry_run`, enforce a rate limit and an SA/PGN allowlist, and are logged into the evidence store. canarchy's MCP safety pattern is the model here.
- **Never exposed over MCP:** DTC clears, memory writes, RoutineControl, SecurityAccess key operations, fuzzing, J1939-91C key material.

### 6.3 Context discipline
- Responses are bounded (byte caps, server-side downsampling) and carry evidence hashes so the AI can cite exactly what it saw.
- Per-tool error isolation, so one failure doesn't kill the stdio session.

### 6.4 Visualization
- An MCP Apps UI resource (`ui://`) renders an interactive network graph and signal plots inline in the AI client.
- The same JSON drives the local web dashboard and the GUI's network tab, so there is one data path.

---

## 7. SAE J1939-91C support

### 7.1 Verified from the Golden Tester paper
The paper's golden vector (`vector_001_valid`) **reproduces exactly** with AES-128-CMAC:

```
key     = c60eb74833281d8404aa4e1db2aa1712
input   = PGN(3, MSB first) || SA(1) || FV(4, MSB first) || payload
        = 00EF00 || A7 || 0000003C || 1122334455667788
CMAC    = 57916E828A91D637152723F75900F939   ✔
on wire = payload(8) || FV(4) || E(1 bit) + CMAC[31 MSBs]   → 16-byte CAN FD frame
```

**Implementation note:** the **E bit is *not* part of the CMAC input** for this vector. Figure 2's `E + PGN + SA + FV + Data` notation suggests it is, and we tested that variant: it does *not* match. Confirm this against the standard text.

### 7.2 Roles
1. **Passive monitor (no keys).**
   - Parse the security overhead.
   - Track FV per (SA, PGN) and flag non-monotonic, replayed or stale FVs.
   - Follow network-formation, rekey and secure-exchange phases and time them against the Table 1 windows (250 ms / 500 ms).
   - Detect join/rekey storms, the DoS category the paper's STRIDE analysis says 91C doesn't mitigate.
   - Record **audit logs of certificate/VIN-binding failures**, the gap the paper recommends closing.
2. **Lab verifier (provisioned keys).** Verify CMAC tags in real time and run the paper's vectors plus the deferred ones (FV rollover, sliding window) as a CI-style conformance suite.
3. **Golden Tester (active, lab only).**
   - Act as leader or follower: X25519 ephemeral key exchange, AES-CMAC-KDF session keys, X.509 certificates with the VIN extension (OID `1.0.20828.3`).
   - Measure formation latency, secure-message latency and throughput for plug-fest scoring.
   - Keys live in an OS keystore or HSM interface, are zeroized after use, and are never exposed to plugins or MCP.

**Caveats:**
- The PGNs used in the paper's simulation (0xFA02 Announce Leader, 0xFA03 Join, 0x4740 Send SN, 0xFA06 SN_ACK) and the exact KDF label/context must be confirmed against the purchased J1939-91C document.
- CAN FD transmit requires PCAN-Basic or SocketCAN, not RP1210 (see §3.2).

---

## 8. What to take from the three reviewed repositories

| Repo | License | Reuse model | Ideas to adopt |
|---|---|---|---|
| **pretty_j1939** (NMFTA) | Apache-2.0 | **Code reusable** with LICENSE/NOTICE kept | <ul><li>`create_j1939db_json.py` DA→JSON generator and its per-PGN `SPNStartBits` schema (ours is an older schema that can't place one SPN at different positions in different PGNs)</li><li>NA/error/reserved checks (`is_spn_na`, `is_spn_error`, …)</li><li>`NameTracker` / `decode_j1939_name`</li><li>`J1939Filter.generate_can_filters` (maps directly to SocketCAN and PCAN acceptance filters)</li><li>candump parsers</li><li>its tests as golden vectors</li></ul> **Don't ship its bundled `J1939db.json`**: 99 entries are GPL-2.0 (from Wireshark). |
| **canarchy** (hexsecs) | GPL-3.0 | **Re-implement ideas only**; or run it as a separate process or MCP server | <ul><li>One JSON result envelope and event schema shared by CLI/TUI/MCP/web</li><li>Backend protocol plus a deterministic fake backend for CI</li><li>MCP safety model (`ack_active`, dry-run default, pinned argv, per-tool error isolation, output caps)</li><li>SHA-256 session provenance and `session verify`</li><li>Baseline-compare anomaly heuristics</li><li>An autouse test fixture that blocks real CAN interfaces</li></ul> It has no RP1210, J1939-22, 91C, DMs beyond DM1, or ISO-TP flow control. |
| **TruckDevil** | GPL-3.0 | **Re-implement ideas only** | <ul><li>Three-way module discovery: built-in package scan, user path, and `importlib.metadata` entry-point group (`truckdevil/truckdevil.py`). Mirror this for Python-side plugins.</li><li>`Device` abstraction covering a Macchina M2 serial bridge and python-can</li><li>python-can virtual-bus test fixtures</li><li>ECU discovery as a module</li></ul> Its fuzzer belongs in a separate, explicitly enabled security-testing build, never in MCP. |

### 8.1 Your repositories

| Repo | Reused | Notes |
|---|---|---|
| **SystemsCyber/CANParser** (Rust; no LICENSE file in the repo, so add one, e.g. MIT, before copying code) | <ul><li>Workspace shape: core library + CLI + PyO3 (abi3) + wasm-bindgen crates</li><li>the `Specification` trait for pluggable spec formats (JSON/XLSX/DBC)</li><li>protocol-detection flags</li><li>JSON/CSV/SQLite serializers</li><li>rayon parallel parsing</li></ul> | Re-implemented rather than copied, because the decode kernel has defects: <ul><li>**PGN is `u16`**, so DP/EDP are lost and `pdu_fmt >= 240` misclassifies DP=1 PDU1 messages, and the shift can overflow.</li><li>**SPN is `u16`** (SPNs are 19 bits).</li><li>Bit extraction loops bit by bit with `powf` in **`f32`** (precision loss above 24 bits).</li><li>Out-of-range values are wrapped (`value -= max`) instead of flagged.</li><li>The length clamp compares bytes with bits.</li><li>There is no TP reassembly and no live sources.</li></ul> All of these are fixed and tested in `csu-j1939` (`id.rs`, `db.rs`). |
| **Dr-Daily/BallastController** (MIT) | The HMI tree view design (`HMI/templates/j1939_display.html`), its color ramp, and its per-byte σ idea | Ported into `csu serve`. Its MCx30 captures served as real-world test data. Its DP=1 PGN (0x1F211) case is now a unit test. |

**None of the three** has RP1210, J1939-22 FD transport, J1939-91C, a J1939-73 DM suite, TP timers, or a native core. Those are CSU-RP1210's differentiators.

---

## 9. Phased roadmap

0. **Stabilize (Python):**
   - Fix the §1.3 defects and the §1.2 hot spots.
   - Split decoding out of the Qt widgets into a headless `csu` package.
   - Add candump fixtures and a throughput benchmark.
   - Resolve the license header and the DA-data question.
1. **Rust core:**
   - `csu-bus` (RP1210, PCAN-Basic, SocketCAN, replay) and `csu-j1939` (codec, TP, compiled DA decoder).
   - PyO3 bindings; the GUI switches to them.
   - Verify parity against Phase 0 fixtures and pretty_j1939 output.
2. **Diagnostics:** J1939-73 suite, address claim/NAME, ISO-TP/UDS, J1587 port, ECU inventory.
3. **Forensics:** evidence store, pcapng/MF4/Parquet export, plugin host (data packs → WASM → out-of-process), signing.
4. **MCP:** passive resources/tools, network graph UI, then gated active tools.
5. **J1939-91C:** passive monitor → lab verifier with the golden-vector suite → Golden Tester active role.

## 10. Open decisions

1. GUI: Tauri + web (recommended) or PySide6. Suggested deciding test: build the raw-frame trace view on the web stack and replay a fully loaded CAN FD log.
2. If redistribution of the previous DA-derived `J1939db.json` is a concern, its content is still in git history; removing it needs a history rewrite (e.g. `git filter-repo`), which is the repository owner's call.
3. License header in `CSU_RP1210.py` (GPL-3.0) vs `LICENSE` (MIT).
