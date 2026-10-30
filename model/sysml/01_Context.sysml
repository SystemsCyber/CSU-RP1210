package CSU_Context {
    doc /* System context and use cases for the CSU-RP1210 heavy-vehicle network
         * diagnostic, forensic and security-evaluation tool. */

    private import ScalarValues::*;
    private import CSU_Logical::CsuSystem;
    private import CSU_Interfaces::*;

    // ---- External actors and systems -------------------------------------

    part def Analyst {
        doc /* Diagnostic technician, forensic engineer or researcher. */
    }
    part def AiModel {
        doc /* An AI model (e.g. Claude) that reaches the system through an MCP client. */
    }
    part def VehicleNetwork {
        doc /* J1939 / CAN / CAN FD / J1708 networks on the vehicle (one or more ECUs). */
        port can : CanPhysicalPort[1..*];
    }
    part def Ecu {
        doc /* An electronic control unit with a J1939 source address. */
        attribute sourceAddress : Natural;
        attribute supportsJ1939_91C : Boolean;
    }
    part def VehicleDiagnosticAdapter {
        doc /* RP1210 VDA (e.g. DG DPA5, PEAK via PEAKRP32) or a PEAK PCAN-USB (Pro) FD
             * reached through PCAN-Basic or SocketCAN. */
        port vehicleSide : ~CanPhysicalPort;
        port hostSide : AdapterDriverPort;
        attribute canFdCapable : Boolean;
    }
    part def LicensedDatabaseProvider {
        doc /* SAE J1939 Digital Annex licensee. Supplies J1939db.licensed.json; the
             * repository ships only a skeleton. */
    }
    part def PluginVendor {
        doc /* Third party that ships signed, possibly proprietary, forensic interpreter plugins. */
    }

    // ---- Context ---------------------------------------------------------

    part context {
        part analyst : Analyst;
        part ai : AiModel;
        part vehicle : VehicleNetwork {
            part ecus : Ecu[1..*];
        }
        part adapter : VehicleDiagnosticAdapter[1..*];
        part csu : CsuSystem;
        part dbProvider : LicensedDatabaseProvider[0..1];
        part pluginVendor : PluginVendor[0..*];

        connect vehicle.can to adapter.vehicleSide;
        connect adapter.hostSide to csu.adapterPort;
        connect ai to csu.mcpPort;
        connect analyst to csu.uiPort;
    }

    // ---- Use cases -------------------------------------------------------

    use case def MonitorNetwork {
        doc /* Observe live traffic as a channel > source address > PGN tree with
             * rates, byte statistics and decoded SPNs. */
        subject system : CsuSystem;
        actor analyst : Analyst;
        objective { doc /* The analyst identifies every node and message on the bus within seconds. */ }
    }

    use case def DiagnoseVehicle {
        doc /* Read J1939-73 DMs, UDS DTCs and component identity; perform gated actions. */
        subject system : CsuSystem;
        actor analyst : Analyst;
    }

    use case def ForensicCapture {
        doc /* Record a hash-chained evidence log and interpret it with signed plugins,
             * preserving provenance for every finding. */
        subject system : CsuSystem;
        actor analyst : Analyst;
        actor vendor : PluginVendor;
    }

    use case def ReverseEngineerProprietary {
        doc /* Use per-byte variance and change counts to find signals in PropA/PropB messages
             * and save them as a data pack. */
        subject system : CsuSystem;
        actor analyst : Analyst;
    }

    use case def EvaluateJ1939_91C {
        doc /* Passive monitoring, lab verification and Golden Tester conformance
             * evaluation of J1939-91C secure networks (CAN FD). */
        subject system : CsuSystem;
        actor analyst : Analyst;
    }

    use case def AiAssistedAnalysis {
        doc /* An AI model queries bounded, provenance-tagged network context and
             * visualizations over MCP; transmit actions require explicit human enablement. */
        subject system : CsuSystem;
        actor ai : AiModel;
        actor analyst : Analyst;
    }
}
