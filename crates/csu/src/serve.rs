//! Web UI server: one capture thread feeds the network tree; WebSocket clients
//! get a bounded snapshot every 250 ms plus full decoding for the node they
//! select. The UI is read-only: no endpoint transmits on the bus.

use axum::extract::ws::{Message as WsMessage, WebSocket, WebSocketUpgrade};
use axum::extract::{Query, State};
use axum::response::{Html, IntoResponse};
use axum::routing::get;
use axum::{Json, Router};
use csu_bus::{Bus, BusError};
use csu_j1939::summary::{NetworkTree, Selection};
use csu_j1939::{CompiledDb, Event, Stack};
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

const INDEX_HTML: &str = include_str!("../web/index.html");
const PUSH_PERIOD: Duration = Duration::from_millis(250);

struct Shared {
    tree: Mutex<NetworkTree>,
    db: CompiledDb,
    source: String,
    live: AtomicBool,
    error: Mutex<Option<String>>,
}

pub fn run(db: CompiledDb, spec: &str, bind: &str, record: Option<PathBuf>) -> Result<(), BusError> {
    // A log given without options replays in real time.
    let spec = if csu_bus::backend::BusSpec::parse(spec)?.kind == "candump" && !spec.contains("speed=") {
        format!("{spec},speed=1")
    } else {
        spec.to_string()
    };
    let bus = csu_bus::open(&spec)?;
    let info = bus.info();
    let shared = Arc::new(Shared {
        tree: Mutex::new(NetworkTree::default()),
        db,
        source: if info.offline {
            let name = std::path::Path::new(&info.channel).file_name().map(|n| n.to_string_lossy().into_owned());
            format!("replay {}", name.unwrap_or(info.channel.clone()))
        } else {
            format!("{} {}", info.backend, info.channel)
        },
        live: AtomicBool::new(true),
        error: Mutex::new(None),
    });
    if !info.offline {
        shared.tree.lock().unwrap().set_channel_name(0, &info.channel);
    }
    let cap = shared.clone();
    std::thread::Builder::new().name("capture".into()).spawn(move || capture(bus, cap, record))?;

    let rt = tokio::runtime::Runtime::new()?;
    rt.block_on(async move {
        let app = Router::new()
            .route("/", get(|| async { Html(INDEX_HTML) }))
            .route("/api/snapshot", get(snapshot))
            .route("/ws", get(ws_upgrade))
            .with_state(shared);
        let listener = tokio::net::TcpListener::bind(bind).await?;
        eprintln!("csu web UI on http://{} (source: {spec})", listener.local_addr()?);
        axum::serve(listener, app)
            .with_graceful_shutdown(async {
                let _ = tokio::signal::ctrl_c().await;
            })
            .await
    })?;
    Ok(())
}

fn capture(mut bus: Box<dyn Bus>, shared: Arc<Shared>, record: Option<PathBuf>) {
    let mut stack = Stack::default();
    let mut events = Vec::new();
    let mut log = record.and_then(|p| match std::fs::OpenOptions::new().create(true).append(true).open(&p) {
        Ok(f) => Some(std::io::BufWriter::new(f)),
        Err(e) => {
            *shared.error.lock().unwrap() = Some(format!("cannot record to {}: {e}", p.display()));
            None
        }
    });
    let iface = bus.info().channel;
    let mut named: u64 = 0;
    loop {
        match bus.recv(Duration::from_millis(100)) {
            Ok(Some(f)) => {
                if let Some(w) = log.as_mut() {
                    let _ = writeln!(w, "{}", csu_bus::candump::format_line(&f, &iface));
                }
                let mut tree = shared.tree.lock().unwrap();
                if f.channel < 64 && named & (1 << f.channel) == 0 {
                    named |= 1 << f.channel;
                    if let Some(n) = bus.channel_names().get(f.channel as usize) {
                        tree.set_channel_name(f.channel, n);
                    }
                }
                if f.flags.has(csu_bus::FrameFlags::ERROR) {
                    tree.observe_error(&f);
                } else if f.is_extended() {
                    stack.feed(&f, &mut events);
                    for e in events.drain(..) {
                        match e {
                            Event::Message(m) => tree.observe(&m),
                            Event::TpAborted(a) => tree.observe_abort(&a),
                        }
                    }
                } else {
                    tree.observe_standard(&f);
                }
            }
            Ok(None) => {
                if let Some(w) = log.as_mut() {
                    let _ = w.flush();
                }
            }
            Err(BusError::EndOfInput) => break,
            Err(e) => {
                *shared.error.lock().unwrap() = Some(e.to_string());
                break;
            }
        }
    }
    if let Some(mut w) = log {
        let _ = w.flush();
    }
    shared.live.store(false, Ordering::Relaxed);
}

fn render(shared: &Shared, sel: Option<&Selection>) -> serde_json::Value {
    let snap = shared.tree.lock().unwrap().snapshot(&shared.db, sel);
    serde_json::json!({
        "source": shared.source,
        "live": shared.live.load(Ordering::Relaxed),
        "error": *shared.error.lock().unwrap(),
        "db_sources": shared.db.meta.sources,
        "snapshot": snap,
    })
}

async fn snapshot(
    State(shared): State<Arc<Shared>>,
    sel: Result<Query<Selection>, axum::extract::rejection::QueryRejection>,
) -> impl IntoResponse {
    Json(render(&shared, sel.as_ref().ok().map(|q| &q.0)))
}

async fn ws_upgrade(ws: WebSocketUpgrade, State(shared): State<Arc<Shared>>) -> impl IntoResponse {
    ws.on_upgrade(move |socket| client(socket, shared))
}

async fn client(mut socket: WebSocket, shared: Arc<Shared>) {
    let mut selection: Option<Selection> = None;
    let mut tick = tokio::time::interval(PUSH_PERIOD);
    loop {
        tokio::select! {
            _ = tick.tick() => {
                let body = render(&shared, selection.as_ref()).to_string();
                if socket.send(WsMessage::Text(body.into())).await.is_err() {
                    return;
                }
            }
            msg = socket.recv() => match msg {
                Some(Ok(WsMessage::Text(t))) => {
                    selection = serde_json::from_str(t.as_str()).ok();
                    let body = render(&shared, selection.as_ref()).to_string();
                    if socket.send(WsMessage::Text(body.into())).await.is_err() {
                        return;
                    }
                }
                Some(Ok(_)) => {}
                _ => return,
            }
        }
    }
}
