package CSU_Interfaces {
    doc /* Items exchanged between components, and the ports and interfaces that carry them.
         * Item definitions mirror the Rust types in crates/csu-bus, csu-j1939 and csu-sec. */

    private import ScalarValues::*;

    // ---- Items -----------------------------------------------------------

    enum def FrameFlag {
        Extended; Fd; BitRateSwitch; ErrorStateIndicator; Rtr; ErrorFrame; TxEcho;
    }

    item def CanFrame {
        doc /* csu_bus::Frame: classic CAN or CAN FD (0..64 data bytes). */
        attribute timestamp : Real;
        attribute channel : Natural;
        attribute identifier : Natural;
        attribute flags : FrameFlag[0..*];
        attribute length : Natural;
        attribute data : Natural[0..64] ordered;
    }

    item def J1939Message {
        doc /* csu_j1939::Message: single frame or TP-reassembled parameter group. */
        attribute timestamp : Real;
        attribute priority : Natural;
        attribute pgn : Natural;
        attribute sourceAddress : Natural;
        attribute destinationAddress : Natural;
        attribute data : Natural[0..1785] ordered;
        attribute reassembled : Boolean;
    }

    item def TpAbort {
        attribute pgn : Natural;
        attribute reason : String;
    }

    enum def SpnStatus { Valid; NotAvailable; Error; Reserved; OutOfRange; Missing; }

    item def SpnValue {
        attribute spn : Natural;
        attribute raw : Natural[0..1];
        attribute value : Real[0..1];
        attribute status : SpnStatus;
        attribute text : String[0..1];
    }

    item def Dtc {
        attribute spn : Natural;
        attribute fmi : Natural;
        attribute occurrenceCount : Natural;
        attribute conversionMethod : Boolean;
    }

    item def NetworkSnapshot {
        doc /* Bounded tree view: channels > sources > parameter groups with rates,
             * per-byte mean / standard deviation / change counts, plus one decoded detail node. */
        attribute totalFrames : Natural;
        attribute skeletonDatabaseOnly : Boolean;
    }

    item def NodeSelection {
        attribute channel : Natural;
        attribute sourceAddress : Natural;
        attribute pgn : Natural;
    }

    item def J1939Database {
        doc /* J1939db.json skeleton or licensed database (legacy or current schema), plus
             * layered data packs. */
        attribute skeletonOnly : Boolean;
        attribute sources : String[0..*];
    }

    item def SecureFrame {
        doc /* J1939-91C benchmark layout: payload || FV(4, MSB first) || E(1 bit) + tag(31 bits). */
        attribute freshnessValue : Natural;
        attribute encrypted : Boolean;
        attribute tag31 : Natural;
    }

    enum def Verdict { Pass; TagMismatch; FreshnessReplay; }

    item def EvidenceRecord {
        doc /* Planned: append-only, SHA-256 hash-chained capture block with provenance. */
        attribute blockHash : String;
        attribute previousHash : String;
    }

    item def Finding {
        doc /* Planned: typed output of an interpreter plugin, with plugin id, version,
             * binary hash and the evidence hash range it read. */
        attribute pluginId : String;
        attribute pluginHash : String;
        attribute evidenceRange : String;
    }

    item def McpRequest;
    item def McpResult;

    // ---- Ports -----------------------------------------------------------

    port def CanPhysicalPort {
        doc /* Physical CAN / CAN FD medium (ISO 11898). */
        inout item frames : CanFrame[0..*];
    }

    port def AdapterDriverPort {
        doc /* Vendor driver API boundary: RP1210 DLL, PCANBasic, or Linux AF_CAN socket. */
        inout item frames : CanFrame[0..*];
    }

    port def FramePort {
        out item frame : CanFrame;
    }

    port def MessagePort {
        out item message : J1939Message;
        out item abort : TpAbort[0..1];
    }

    port def DatabasePort {
        in item db : J1939Database;
    }

    port def SnapshotPort {
        out item snapshot : NetworkSnapshot;
        in item selection : NodeSelection[0..1];
    }

    port def McpPort {
        in item request : McpRequest;
        out item result : McpResult;
    }

    port def UiPort {
        out item snapshot : NetworkSnapshot;
        in item selection : NodeSelection;
    }

    port def PluginAbiPort {
        doc /* WIT-defined WASM component interface (read-only views in, findings out). */
        out item views : J1939Message[0..*];
        in item findings : Finding[0..*];
    }

    // ---- Interfaces ------------------------------------------------------

    interface def FrameLink {
        end port source : FramePort;
        end port sink : ~FramePort;
    }

    interface def MessageLink {
        end port source : MessagePort;
        end port sink : ~MessagePort;
    }

    interface def SnapshotLink {
        doc /* WebSocket: snapshot pushed every 250 ms; client sends its selection. */
        end port server : SnapshotPort;
        end port client : ~SnapshotPort;
        attribute pushPeriod : Real = 0.25;
    }
}
