//! End-to-end: candump fixture → J1939 stack → network tree, decoded with the
//! skeleton database, plus a J1939-91C secure frame checked with csu-sec.

use csu_bus::candump::CandumpReader;
use csu_j1939::summary::{NetworkTree, Selection};
use csu_j1939::{CompiledDb, Event, Stack};
use std::path::PathBuf;

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn run() -> (NetworkTree, Vec<Event>) {
    let reader = CandumpReader::open(repo().join("tests/fixtures/sample_j1939.log"), 0.0, false).unwrap();
    let mut stack = Stack::default();
    let mut tree = NetworkTree::default();
    let mut all = Vec::new();
    let mut events = Vec::new();
    for f in reader {
        if !f.is_extended() {
            tree.observe_standard(&f);
            continue;
        }
        stack.feed(&f, &mut events);
        for e in events.drain(..) {
            match &e {
                Event::Message(m) => tree.observe(m),
                Event::TpAborted(a) => tree.observe_abort(a),
            }
            all.push(e);
        }
    }
    (tree, all)
}

#[test]
fn skeleton_database_is_marked_and_decodes_examples() {
    let db = CompiledDb::load(&repo().join("J1939db.json")).unwrap();
    assert!(db.meta.skeleton_only);
    let v = db.decode(65280, &[0x40, 0x1F, 0x01, 0x82, 0xFF, 0xFF, 0xFF, 0xFF]);
    assert_eq!(v[0].value, Some(1000.0));
    assert_eq!(v[1].text.as_deref(), Some("on"));
    assert_eq!(v[2].value, Some(90.0));
}

#[test]
fn tree_contains_identity_diagnostics_and_tp() {
    let (tree, events) = run();
    let ch = &tree.channels[&0];
    let engine = &ch.sources[&0x00];
    assert_eq!(engine.claimed_name.unwrap().manufacturer_code, 0x2AB);
    let dm1 = engine.dm1.as_ref().expect("DM1 reassembled from BAM");
    assert_eq!(dm1.dtcs.len(), 4);
    assert_eq!(engine.vin.as_deref(), Some("1XKYDP9X0LJ123456"));
    assert_eq!(ch.standard.len(), 1);
    let reassembled = events.iter().filter(|e| matches!(e, Event::Message(m) if m.reassembled)).count();
    assert_eq!(reassembled, 2);

    let db = CompiledDb::load(&repo().join("J1939db.json")).unwrap();
    let sel = Selection { ch: 0, sa: 0, pgn: 65280, da: 0xFF, reassembled: false, standard: false };
    let snap = tree.snapshot(&db, Some(&sel));
    let detail = snap.detail.expect("selected node");
    assert_eq!(detail.node.count, 3);
    assert_eq!(detail.spns.len(), 3);
    let json = serde_json::to_string(&tree.snapshot(&db, None)).unwrap();
    assert!(json.contains("\"db_skeleton_only\":true"));
}

#[test]
fn secure_fd_frame_verifies() {
    let (_, events) = run();
    let m = events
        .iter()
        .find_map(|e| match e {
            Event::Message(m) if m.data.len() == 16 => Some(m),
            _ => None,
        })
        .expect("CAN FD secure frame");
    assert_eq!((m.pgn, m.sa), (0xEF00, 0xA7));
    let frame = csu_sec::SecureFrame::parse(&m.data).unwrap();
    let key = csu_sec::SessionKey::new([
        0xc6, 0x0e, 0xb7, 0x48, 0x33, 0x28, 0x1d, 0x84, 0x04, 0xaa, 0x4e, 0x1d, 0xb2, 0xaa, 0x17, 0x12,
    ]);
    let mut fv = csu_sec::FreshnessTracker::new();
    let verdict = csu_sec::verify(&key, m.pgn, m.sa, frame.fv, &frame.payload, frame.tag31, &mut fv);
    assert_eq!(verdict, csu_sec::Verdict::Pass);
}
