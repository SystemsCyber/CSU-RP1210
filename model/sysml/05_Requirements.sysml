package CSU_Requirements {
    doc /* System requirements. IDs are stable; "satisfy" links are in CSU_Allocation and
         * "verify" links in CSU_Verification. */

    private import ScalarValues::*;
    private import CSU_Logical::*;

    requirement def <'REQ-PLAT-1'> CrossPlatform {
        doc /* The core shall build and run on Windows x64, Windows x86 (for 32-bit-only RP1210
             * drivers) and Linux (x64 and ARM64). */
        subject system : CsuSystem;
    }

    requirement def <'REQ-HW-1'> Rp1210Support {
        doc /* The system shall communicate through any installed RP1210 C implementation
             * listed in RP121032.ini, using a raw CAN client so that all wire traffic is visible. */
        subject backend : Rp1210Backend;
    }

    requirement def <'REQ-HW-2'> PeakCanFdSupport {
        doc /* The system shall natively support PEAK PCAN-USB FD / Pro FD adapters in CAN FD
             * mode: PCAN-Basic on Windows; SocketCAN or PCAN-Basic on Linux. */
        subject backend : PcanBackend;
        attribute dataBitrate : Natural = 2000000;
    }

    requirement def <'REQ-HW-3'> SocketCanSupport {
        doc /* The system shall support Linux SocketCAN, including CAN FD frames, without RP1210. */
        subject backend : SocketCanBackend;
    }

    requirement def <'REQ-PERF-1'> OfflineThroughput {
        doc /* Offline decoding (ID, TP, all DA SPNs, tree statistics) shall sustain at least
             * 1,000,000 frames/s on a current laptop. Measured: 2.74 M frames/s (csu bench). */
        subject system : CsuSystem;
        attribute minFramesPerSecond : Real = 1.0E6;
    }

    requirement def <'REQ-PERF-2'> LiveLatency {
        doc /* Live views shall refresh at least every 250 ms with no frame loss at
             * 100 % load of a 500 kbit/s CAN bus (about 4,400 frames/s). */
        subject system : CsuSystem;
        attribute refreshPeriod : Real = 0.25;
    }

    requirement def <'REQ-J1939-1'> TransportProtocol {
        doc /* The stack shall reassemble J1939-21 BAM and RTS/CTS sessions, tolerate
             * out-of-order and duplicate packets, and report aborts and T1 timeouts. */
        subject tp : TransportReassembler;
    }

    requirement def <'REQ-J1939-2'> J1939_22Support {
        doc /* The stack shall parse J1939-22 multi-PG containers and FD transport. (Stubbed.) */
        subject fd : FdDataLink;
    }

    requirement def <'REQ-DIAG-1'> DmDecoding {
        doc /* The system shall decode J1939-73 DTC messages (DM1, DM2, DM6, DM12, DM23) including
             * lamp and flash status, and classify intrusive DMs for the policy gate. */
        subject dm : DiagnosticMessages;
    }

    requirement def <'REQ-DIAG-2'> UdsDiagnostics {
        doc /* The system shall support ISO 15765 / ISO 14229 UDS over J1939 (PGN 0xDA00) and
             * over 11/29-bit CAN, including CAN FD. (Planned.) */
        subject uds : UdsStack;
    }

    requirement def <'REQ-DB-1'> SkeletonDatabase {
        doc /* The repository shall ship only a skeleton J1939db.json without SAE content; a
             * user-supplied licensed database (J1939db.licensed.json, CSU_J1939DB or --db)
             * shall take precedence, and the UI shall show when only the skeleton is loaded. */
        subject decoder : DigitalAnnexDecoder;
    }

    requirement def <'REQ-DB-2'> DualUnitDatabases {
        doc /* The system shall build metric (SI) and US customary databases from the licensed
             * Digital Annex, using an editable conversion table, and shall load the one matching
             * the user's unit preference in both the Python application and the Rust core. */
        subject decoder : DigitalAnnexDecoder;
    }

    requirement def <'REQ-DB-3'> DatabaseValidation {
        doc /* The system shall validate any database version (structure, references, positions,
             * numeric fields, unit consistency), compare versions and unit systems, and run
             * user-editable decode test vectors. */
        subject decoder : DigitalAnnexDecoder;
    }

    requirement def <'REQ-UI-1'> TreeView {
        doc /* The UI shall present a channel > source address > PGN tree with message counts
             * and periods, per-byte value / standard deviation / change counts, and decoded SPNs
             * for the selected node (BallastController HMI design). */
        subject ui : WebUiServer;
    }

    requirement def <'REQ-SAFE-1'> PassiveByDefault {
        doc /* No user interface, MCP tool or plugin shall transmit on a vehicle network unless
             * active operation was explicitly enabled and acknowledged, and each transmission
             * is rate-limited and audit-logged. DTC clears, memory writes, routine control,
             * security access and fuzzing shall never be reachable from MCP. */
        subject policy : PolicyGate;
    }

    requirement def <'REQ-SEC-1'> J1939_91C_Vectors {
        doc /* The tag verifier shall reproduce all Golden Tester benchmark vectors
             * (valid, wrong SA, replayed FV, FV endianness). */
        subject verifier : TagVerifier;
    }

    requirement def <'REQ-SEC-2'> KeyHygiene {
        doc /* Session keys shall never be serialized, logged, sent to plugins or MCP, and shall be
             * zeroized when dropped. */
        subject security : SecurityModule;
    }

    requirement def <'REQ-FOR-1'> Provenance {
        doc /* Every finding shall record plugin identity and hash, data-pack hashes, and the
             * evidence hash range read; evidence shall be append-only and hash-chained. (Planned.) */
        subject evidence : EvidenceStore;
    }

    requirement def <'REQ-PLUG-1'> ProprietaryPlugins {
        doc /* Third parties shall be able to ship signed proprietary interpreters that run
             * sandboxed and deterministically without source disclosure. (Planned.) */
        subject host : PluginHost;
    }

    requirement def <'REQ-AI-1'> McpContext {
        doc /* The MCP server shall expose bounded, provenance-tagged network context and an
             * interactive network visualization. (Planned.) */
        subject mcp : McpServer;
    }

    // Requirement usages: the system requirement set. Each keeps the subject type of
    // its definition; CSU_Allocation binds subjects with satisfy relationships.
    requirement reqPlat : CrossPlatform;
    requirement reqRp1210 : Rp1210Support;
    requirement reqPeak : PeakCanFdSupport;
    requirement reqSocketCan : SocketCanSupport;
    requirement reqThroughput : OfflineThroughput;
    requirement reqLatency : LiveLatency;
    requirement reqTp : TransportProtocol;
    requirement reqFd : J1939_22Support;
    requirement reqDm : DmDecoding;
    requirement reqUds : UdsDiagnostics;
    requirement reqDb : SkeletonDatabase;
    requirement reqUnits : DualUnitDatabases;
    requirement reqDbValidation : DatabaseValidation;
    requirement reqTree : TreeView;
    requirement reqPassive : PassiveByDefault;
    requirement reqVectors : J1939_91C_Vectors;
    requirement reqKeys : KeyHygiene;
    requirement reqProvenance : Provenance;
    requirement reqPlugins : ProprietaryPlugins;
    requirement reqAi : McpContext;
}
