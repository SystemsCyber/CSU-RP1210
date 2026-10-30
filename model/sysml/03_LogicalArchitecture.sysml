package CSU_Logical {
    doc /* Logical architecture. Elements marked «implemented» exist in the Rust workspace
         * (crates/); «planned» elements are specified in docs/ARCHITECTURE_PROPOSAL.md. */

    private import ScalarValues::*;
    private import CSU_Interfaces::*;

    enum def Maturity { Implemented; Stubbed; Planned; }

    abstract part def Component {
        attribute maturity : Maturity;
        attribute crate : String[0..1];
    }

    // ---- Bus layer (crates/csu-bus) ---------------------------------------

    abstract part def BusBackend :> Component {
        doc /* csu_bus::Bus trait: recv(timeout), send(frame), info(). */
        port driver : ~AdapterDriverPort;
        port frameOut : FramePort;
        attribute fdCapable : Boolean;
        attribute offline : Boolean;
    }

    part def Rp1210Backend :> BusBackend {
        doc /* Windows RP1210 DLL through libloading; raw CAN client with echo enabled.
             * RP1210 C has no CAN FD. 32-bit-only vendor DLLs run in the i686 build. */
        attribute redefines maturity = Maturity::Implemented;
        attribute redefines fdCapable = false;
        attribute apiName : String;
        attribute deviceId : Natural;
    }

    part def PcanBackend :> BusBackend {
        doc /* PEAK PCAN-Basic (Windows PCANBasic.dll, Linux libpcanbasic.so); classic or FD. */
        attribute redefines maturity = Maturity::Implemented;
        attribute redefines fdCapable = true;
        attribute nominalBitrate : Natural;
        attribute dataBitrate : Natural[0..1];
    }

    part def SocketCanBackend :> BusBackend {
        doc /* Linux AF_CAN CAN_RAW with CAN_RAW_FD_FRAMES. PEAK USB FD via peak_usb driver. */
        attribute redefines maturity = Maturity::Implemented;
        attribute redefines fdCapable = true;
        attribute interfaceName : String;
    }

    part def ReplayBackend :> BusBackend {
        doc /* candump log reader (log, FD and ASCII formats), real-time pacing, looping. */
        attribute redefines maturity = Maturity::Implemented;
        attribute redefines offline = true;
        attribute speed : Real;
    }

    part def VirtualBackend :> BusBackend {
        doc /* In-process loopback medium for tests and demos. */
        attribute redefines maturity = Maturity::Implemented;
    }

    part def BusLayer :> Component {
        attribute redefines crate = "csu-bus";
        part backends : BusBackend[1..*];
        port frameOut : FramePort;
    }

    // ---- J1939 stack (crates/csu-j1939) -----------------------------------

    part def IdCodec :> Component {
        doc /* 29-bit identifier: priority, 18-bit PGN (EDP/DP kept), SA, DA. */
        attribute redefines maturity = Maturity::Implemented;
    }

    part def TransportReassembler :> Component {
        doc /* J1939-21 BAM and RTS/CTS; out-of-order DT, aborts, T1 = 750 ms / 1250 ms. */
        attribute redefines maturity = Maturity::Implemented;
        attribute bamTimeout : Real = 0.75;
        attribute cmdtTimeout : Real = 1.25;
        attribute maxPayload : Natural = 1785;
    }

    part def FdDataLink :> Component {
        doc /* J1939-22 multi-PG containers and FD.TP. */
        attribute redefines maturity = Maturity::Stubbed;
    }

    part def DigitalAnnexDecoder :> Component {
        doc /* Compiled DA database (legacy and current schemas, layered data packs);
             * J1939-71 not-available / error / reserved classification. */
        attribute redefines maturity = Maturity::Implemented;
        port dbIn : DatabasePort;
    }

    part def DiagnosticMessages :> Component {
        doc /* J1939-73: DM1/2/6/12/23 implemented; DM catalog marks intrusive DMs
             * (DM3, DM11, DM13, DM14) for the policy gate. */
        attribute redefines maturity = Maturity::Implemented;
    }

    part def NameTracker :> Component {
        doc /* J1939-81 address claim and NAME decoding. */
        attribute redefines maturity = Maturity::Implemented;
    }

    part def NetworkTree :> Component {
        doc /* Channel > SA > PGN inventory with Welford per-byte statistics, identity
             * (NAME, VIN, component, software) and DM1 state. Feeds UI and MCP. */
        attribute redefines maturity = Maturity::Implemented;
        port snapshotOut : SnapshotPort;
    }

    part def J1939Stack :> Component {
        attribute redefines crate = "csu-j1939";
        port frameIn : ~FramePort;
        port messageOut : MessagePort;
        port snapshotOut : SnapshotPort;
        port dbIn : DatabasePort;

        part codec : IdCodec;
        part tp : TransportReassembler;
        part fd : FdDataLink;
        part decoder : DigitalAnnexDecoder;
        part dm : DiagnosticMessages;
        part names : NameTracker;
        part tree : NetworkTree;

        bind dbIn = decoder.dbIn;
        bind snapshotOut = tree.snapshotOut;
    }

    // ---- J1939-91C (crates/csu-sec) ---------------------------------------

    part def TagVerifier :> Component {
        doc /* AES-128-CMAC over PGN || SA || FV || payload, 31-bit truncation.
             * Verified against the Golden Tester paper's four vectors. */
        attribute redefines maturity = Maturity::Implemented;
    }

    part def FreshnessTracker :> Component {
        doc /* Strictly increasing FV per (SA, PGN); rollover/window policy TBD from the standard. */
        attribute redefines maturity = Maturity::Implemented;
    }

    part def LifecycleMonitor :> Component {
        doc /* Network formation / rekey / exchange lifecycle (see CSU_Behavior::SecureLifecycle). */
        attribute redefines maturity = Maturity::Stubbed;
    }

    part def KeyAgreement :> Component {
        doc /* X25519 + AES-CMAC-KDF, NID certificate VIN binding (OID 1.0.20828.3). */
        attribute redefines maturity = Maturity::Stubbed;
    }

    part def SecurityModule :> Component {
        attribute redefines crate = "csu-sec";
        port messageIn : ~MessagePort;
        part tagVerifier : TagVerifier;
        part freshness : FreshnessTracker;
        part lifecycle : LifecycleMonitor;
        part keys : KeyAgreement;
    }

    // ---- Planned services -------------------------------------------------

    part def UdsStack :> Component {
        doc /* ISO 15765-2 (classic + FD), ISO 15765-3 over PGN 0xDA00, ISO 14229 UDS; J1587/J1708. */
        attribute redefines maturity = Maturity::Planned;
        port messageIn : ~MessagePort;
    }

    part def EvidenceStore :> Component {
        doc /* Append-only SHA-256 hash-chained capture; pcapng / candump / MF4 / Parquet export. */
        attribute redefines maturity = Maturity::Planned;
        port frameIn : ~FramePort;
    }

    part def PolicyGate :> Component {
        doc /* Every transmit path passes here: passive by default, explicit enablement,
             * per-call acknowledgement, rate limits, SA/PGN allowlists, audit log. */
        attribute redefines maturity = Maturity::Planned;
        attribute activeEnabled : Boolean = false;
    }

    part def PluginHost :> Component {
        doc /* Tier 1 signed data packs, tier 2 sandboxed WASM interpreters (wasmtime),
             * tier 3 out-of-process plugins over JSON-RPC through the policy gate. */
        attribute redefines maturity = Maturity::Planned;
        port abi : PluginAbiPort;
    }

    part def McpServer :> Component {
        doc /* Resources (topology, DTCs, session, 91C state), passive tools, gated active
             * tools, and ui:// network-graph views. Bounded, hash-tagged responses. */
        attribute redefines maturity = Maturity::Planned;
        port mcp : McpPort;
        port snapshotIn : ~SnapshotPort;
    }

    part def WebUiServer :> Component {
        doc /* csu serve: axum HTTP + WebSocket; read-only tree view (ported from the
             * BallastController HMI). */
        attribute redefines maturity = Maturity::Implemented;
        attribute redefines crate = "csu";
        port snapshotIn : ~SnapshotPort;
        port ui : UiPort;
    }

    part def DesktopShell :> Component {
        doc /* Tauri desktop window hosting the same web UI with the Rust core in-process. */
        attribute redefines maturity = Maturity::Planned;
    }

    part def CommandLine :> Component {
        doc /* csu devices | decode | summary | bench | serve */
        attribute redefines maturity = Maturity::Implemented;
        attribute redefines crate = "csu";
    }

    // ---- System -------------------------------------------------------------

    part def CsuSystem {
        port adapterPort : ~AdapterDriverPort;
        port mcpPort : McpPort;
        port uiPort : UiPort;
        port dbPort : DatabasePort;

        part bus : BusLayer;
        part j1939 : J1939Stack;
        part security : SecurityModule;
        part uds : UdsStack;
        part evidence : EvidenceStore;
        part policy : PolicyGate;
        part plugins : PluginHost;
        part mcp : McpServer;
        part web : WebUiServer;
        part desktop : DesktopShell[0..1];
        part cli : CommandLine;

        interface : FrameLink connect source ::> bus.frameOut to sink ::> j1939.frameIn;
        interface : FrameLink connect source ::> bus.frameOut to sink ::> evidence.frameIn;
        interface : MessageLink connect source ::> j1939.messageOut to sink ::> security.messageIn;
        interface : MessageLink connect source ::> j1939.messageOut to sink ::> uds.messageIn;
        interface : SnapshotLink connect server ::> j1939.snapshotOut to client ::> web.snapshotIn;
        interface : SnapshotLink connect server ::> j1939.snapshotOut to client ::> mcp.snapshotIn;

        bind dbPort = j1939.dbIn;
        bind mcpPort = mcp.mcp;
        bind uiPort = web.ui;
    }
}
