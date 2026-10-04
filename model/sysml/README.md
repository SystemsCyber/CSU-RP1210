# CSU-RP1210 SysML v2 architecture model

This is a textual SysML v2 model of the next-generation CSU-RP1210 system. The existing `SysML MSOSA Model for CSU-RP1210.mdzip` at the repository root is the earlier SysML v1 (Cameo/MSOSA) model of the Python application. This model describes the Rust-core architecture in `crates/` and the planned services in [`docs/ARCHITECTURE_PROPOSAL.md`](../../docs/ARCHITECTURE_PROPOSAL.md).

| File | Package | Contents |
|---|---|---|
| `01_Context.sysml` | `CSU_Context` | Actors (analyst, AI model, vehicle network, VDA, database provider, plugin vendor), the system context, use cases |
| `02_Interfaces.sysml` | `CSU_Interfaces` | Item definitions mirroring the Rust types (`CanFrame`, `J1939Message`, `SpnValue`, `SecureFrame`, …), port and interface definitions |
| `03_LogicalArchitecture.sysml` | `CSU_Logical` | Components with a `maturity` attribute (Implemented / Stubbed / Planned) and their crates, plus the `CsuSystem` composition and interface connections |
| `04_Behavior.sysml` | `CSU_Behavior` | Capture-to-snapshot pipeline, J1939-21 transport-session state machine, J1939-91C lifecycle (from the Golden Tester paper), secure-message verification |
| `05_Requirements.sysml` | `CSU_Requirements` | Requirement definitions `REQ-*` and the requirement set |
| `06_Deployment.sysml` | `CSU_Deployment` | Crates, Windows x64/x86 processes and driver DLLs, Linux host, allocations, `satisfy` links |
| `07_Verification.sysml` | `CSU_Verification` | Verification cases traced to the automated tests, the benchmark and the hardware smoke tests |

## Viewing and validating

Load all seven files together as one project. Packages refer to each other by qualified name.

- **SysIDE** (Sensmetry, VS Code extension), **SysON** (Eclipse), or the **SysML v2 Pilot Implementation** (Jupyter kernel or Eclipse) can parse and visualize it.
- MSOSA/Cameo 2024x+ with SysML v2 support can import textual notation.

The model uses only standard-library packages (`ScalarValues`).

> The model has not yet been machine-validated. It was written against the SysML v2 textual grammar but not parsed by a tool in this environment. Please run it through SysIDE or the Pilot Implementation and report any diagnostics.

## Conventions

- `maturity` on every `Component` shows what is built: run `cargo test --workspace` to exercise the implemented parts.
- J1939-91C elements contain only what is public in SAE 2026-01-0092. Normative details are marked `TODO(J1939-91C)`, matching the stubs in `crates/csu-sec`.
