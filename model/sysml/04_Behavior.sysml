package CSU_Behavior {
    doc /* Behavior: the capture-to-snapshot pipeline, J1939-21 transport sessions, and the
         * J1939-91C lifecycle as published in the Golden Tester paper (Figures 7 and 10).
         * Normative 91C transitions and timeouts are TBD from the licensed standard. */

    private import ScalarValues::*;
    private import CSU_Interfaces::*;

    // ---- Capture pipeline ------------------------------------------------

    action def ReceiveFrame {
        out item frame : CanFrame;
    }
    action def DecodeIdentifier {
        in item frame : CanFrame;
        out item message : J1939Message;
    }
    action def ReassembleTransport {
        doc /* Emits completed TP messages and TpAbort events (timeout, abort, replaced). */
        in item message : J1939Message;
        out item completed : J1939Message[0..1];
        out item abort : TpAbort[0..1];
    }
    action def UpdateNetworkTree {
        in item message : J1939Message;
    }
    action def RecordEvidence {
        in item frame : CanFrame;
    }
    action def PublishSnapshot {
        in item selection : NodeSelection[0..1];
        out item snapshot : NetworkSnapshot;
    }

    action def CaptureAndDecode {
        doc /* One capture thread; per frame: decode, reassemble, aggregate. Snapshots are
             * published asynchronously every 250 ms and never block capture. */
        in item selection : NodeSelection[0..1];
        out item snapshot : NetworkSnapshot;

        action receive : ReceiveFrame;
        action record : RecordEvidence;
        action decodeId : DecodeIdentifier;
        action reassemble : ReassembleTransport;
        action aggregate : UpdateNetworkTree;
        action publish : PublishSnapshot;

        flow from receive.frame to record.frame;
        flow from receive.frame to decodeId.frame;
        flow from decodeId.message to reassemble.message;
        flow from decodeId.message to aggregate.message;
        flow from reassemble.completed to aggregate.message;

        first receive then decodeId;
        first decodeId then reassemble;
        first reassemble then aggregate;
    }

    // ---- Transport session -----------------------------------------------

    item def TpCm;
    item def TpDt;
    item def TpAbortReceived;
    item def T1Expired;

    state def TransportSession {
        doc /* Passive observer view of one (channel, SA, DA) transport session. */
        entry; then Idle;

        state Idle;
        state Receiving;
        state Complete;
        state Aborted;

        transition announce first Idle accept TpCm then Receiving;
        transition data first Receiving accept TpDt then Receiving;
        transition lastPacket first Receiving accept TpDt then Complete;
        transition connAbort first Receiving accept TpAbortReceived then Aborted;
        transition timeout first Receiving accept T1Expired then Aborted;
        transition replaced first Receiving accept TpCm then Receiving;
        transition reset1 first Complete then Idle;
        transition reset2 first Aborted then Idle;
    }

    // ---- J1939-91C lifecycle ---------------------------------------------

    item def PowerOn;
    item def AnnounceLeader;
    item def JoinRequest;
    item def SessionKeyEstablished;
    item def RekeyTrigger;
    item def VerificationFailure;
    item def BusReset;
    item def MaintenanceRequest;

    state def SecureLifecycle {
        doc /* Simplified lifecycle (paper Fig. 7): Off > Rekeying > Exchange, returning to
             * Network Formation after a bus reset; Failed captures certificate / VIN-binding
             * and CMAC failures (paper Fig. 10). Software Update is out of scope.
             * TODO(J1939-91C): replace trigger items with the normative messages and timeouts. */
        entry; then Off;

        state Off;
        state NetworkFormation {
            doc /* Announce leader, join network, X25519 exchange, send SN, SN ACK. */
        }
        state Rekeying {
            doc /* New ephemeral keys; paper cites a 250 ms response window and 500 ms round trip. */
        }
        state Exchange {
            doc /* Phase 4: CMAC-authenticated messages with freshness values. */
        }
        state Failed {
            doc /* Paper recommendation: emit a diagnostic report, reset to network formation,
                 * audit-log certificate serial, VIN and stage. */
        }
        state SoftwareUpdate;

        transition a first Off accept PowerOn then Rekeying;
        transition b first Rekeying accept SessionKeyEstablished then Exchange;
        transition c first Exchange accept RekeyTrigger then Rekeying;
        transition d first Exchange accept BusReset then NetworkFormation;
        transition e first NetworkFormation accept SessionKeyEstablished then Rekeying;
        transition f1 first NetworkFormation accept VerificationFailure then Failed;
        transition f2 first Rekeying accept VerificationFailure then Failed;
        transition f3 first Exchange accept VerificationFailure then Failed;
        transition recover first Failed accept AnnounceLeader then NetworkFormation;
        transition s first Exchange accept MaintenanceRequest then SoftwareUpdate;
    }

    // ---- Secure message receive ------------------------------------------

    action def VerifySecureMessage {
        doc /* Receiver decision implemented in csu_sec::verify: authenticate, then enforce
             * freshness; the FV tracker only advances on Pass. */
        in item message : J1939Message;
        out attribute verdict : Verdict;

        action parse { in item message : J1939Message; out item frame : SecureFrame; }
        action computeTag;
        action checkFreshness;

        first parse then computeTag;
        first computeTag then checkFreshness;
    }
}
