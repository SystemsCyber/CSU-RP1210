package CSU_Verification {
    doc /* Verification cases, each traced to the automated test or measurement that
         * exercises it. Run all automated cases with `cargo test --workspace`. */

    private import ScalarValues::*;
    private import CSU_Logical::*;
    private import CSU_Requirements::*;

    enum def Method { Test; Analysis; Inspection; Demonstration; }

    verification def GoldenVectorTest {
        doc /* crates/csu-sec/tests/golden_vectors.rs: vectors 001-004 from SAE 2026-01-0092
             * plus a wire-format round trip. */
        subject verifier : TagVerifier;
        attribute method : Method = Method::Test;
        objective { verify reqVectors; }
    }

    verification def TransportProtocolTest {
        doc /* crates/csu-j1939/src/tp.rs tests: BAM in order, out of order with duplicates,
             * timeout, RTS/CTS with connection abort, inconsistent announcement. */
        subject tp : TransportReassembler;
        attribute method : Method = Method::Test;
        objective { verify reqTp; }
    }

    verification def PipelineTest {
        doc /* crates/csu/tests/pipeline.rs: candump fixture through stack and tree with the
             * skeleton database; identity, DM1 via BAM, VIN via RTS/CTS, 11-bit frames, and a
             * J1939-91C secure CAN FD frame. */
        subject system : CsuSystem;
        attribute method : Method = Method::Test;
        objective { verify reqDm; verify reqDb; verify reqVectors; }
    }

    verification def ThroughputBenchmark {
        doc /* `csu bench <log>` (release build). 2026-10-04 result on the development laptop:
             * 2.74 M frames/s, 365 ns/frame, with a full legacy database. */
        subject system : CsuSystem;
        attribute method : Method = Method::Analysis;
        attribute measuredFramesPerSecond : Real = 2.74E6;
        objective { verify reqThroughput; }
    }

    verification def DriverAbiTest {
        doc /* Struct-layout tests against PCANBasic.h and linux/can.h, RP1210 buffer parsing,
             * and 2026-10-04 smoke tests: PEAKRP32 (x64) ClientConnect succeeded; DGDPA5MA
             * loaded by the i686 build; PCANBasic loaded and reported driver status. */
        subject system : CsuSystem;
        attribute method : Method = Method::Demonstration;
        objective { verify reqRp1210; verify reqPeak; verify reqSocketCan; verify reqPlat; }
    }

    verification def TreeViewDemonstration {
        doc /* `csu serve <log>,speed=1,loop` replaying the BallastController MCx30 captures:
             * tree, per-byte statistics and SPN decoding inspected in a browser. */
        subject ui : WebUiServer;
        attribute method : Method = Method::Demonstration;
        objective { verify reqTree; }
    }

    verification def DatabaseToolTests {
        doc /* tests/test_j1939db_tools.py, tests/test_digital_annex_gui.py and
             * tests/test_licensed_db.py (pytest): generation from a synthetic Digital Annex,
             * metric/US conversion and cross-check, SLOT cross-check against the Digital Annex,
             * validation, version comparison, realistic test vectors, licensed-content guards,
             * the dialog driven offscreen, and Rust/Python decode agreement in both unit systems. */
        subject system : CsuSystem;
        attribute method : Method = Method::Test;
        objective { verify reqDb; verify reqUnits; verify reqDbValidation; }
    }

    verification goldenVectors : GoldenVectorTest;
    verification databaseTools : DatabaseToolTests;
    verification transportProtocol : TransportProtocolTest;
    verification pipeline : PipelineTest;
    verification throughput : ThroughputBenchmark;
    verification driverAbi : DriverAbiTest;
    verification treeView : TreeViewDemonstration;
}
