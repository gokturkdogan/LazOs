//! Supervises the local Python AI backend (FastAPI on 127.0.0.1).
//!
//! On launch we reuse a healthy LAZOS backend if one is already running (e.g. started by
//! `scripts/dev.sh`), otherwise we spawn it from the project's virtualenv. The backend is
//! stopped gracefully (SIGTERM) when the app exits, but only if we started it.

use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

const DEFAULT_PORT: u16 = 47811;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Clone, Serialize)]
pub struct BackendInfo {
    pub status: &'static str, // "starting" | "ready" | "error"
    pub url: String,
    pub token: Option<String>,
    pub error: Option<String>,
    pub managed: bool,
    pub log_path: Option<String>,
}

pub struct BackendState {
    pub info: Mutex<BackendInfo>,
    child: Mutex<Option<Child>>,
}

impl BackendState {
    pub fn new() -> Self {
        Self {
            info: Mutex::new(BackendInfo {
                status: "starting",
                url: format!("http://127.0.0.1:{}", port()),
                token: None,
                error: None,
                managed: false,
                log_path: None,
            }),
            child: Mutex::new(None),
        }
    }
}

fn port() -> u16 {
    std::env::var("LAZOS_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(DEFAULT_PORT)
}

fn data_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("LAZOS_DATA_DIR") {
        return PathBuf::from(dir);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join("Library/Application Support/LAZOS")
}

/// Location of `services/ai-backend`. Override with LAZOS_BACKEND_DIR; defaults to the repository
/// this binary was built from (development builds).
fn backend_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("LAZOS_BACKEND_DIR") {
        return PathBuf::from(dir);
    }
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../services/ai-backend")
}

fn read_token() -> Option<String> {
    fs::read_to_string(data_dir().join("auth.token"))
        .ok()
        .map(|t| t.trim().to_string())
        .filter(|t| t.len() >= 32)
}

enum Probe {
    Lazos,
    Foreign,
    Closed,
}

/// Minimal HTTP probe (no extra dependencies): is our backend listening on the port?
fn probe() -> Probe {
    let addr: SocketAddr = ([127, 0, 0, 1], port()).into();
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(400)) else {
        return Probe::Closed;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1200)));
    let request = format!("GET /health HTTP/1.0\r\nHost: 127.0.0.1:{}\r\n\r\n", port());
    if stream.write_all(request.as_bytes()).is_err() {
        return Probe::Foreign;
    }
    let mut body = String::new();
    let _ = stream.read_to_string(&mut body);
    if body.contains("\"app\":\"lazos\"") {
        Probe::Lazos
    } else {
        Probe::Foreign
    }
}

fn set_info(app: &AppHandle, update: impl FnOnce(&mut BackendInfo)) {
    let state = app.state::<BackendState>();
    let snapshot = {
        let mut info = state.info.lock().unwrap();
        update(&mut info);
        info.clone()
    };
    let _ = app.emit("backend://status", snapshot);
}

fn fail(app: &AppHandle, message: String) {
    log::error!("backend: {message}");
    set_info(app, |i| {
        i.status = "error";
        i.error = Some(message);
    });
}

fn spawn(app: &AppHandle) -> Result<(), String> {
    let dir = backend_dir();
    let python = dir.join(".venv/bin/python");
    if !python.exists() {
        return Err(format!(
            "Python ortamı bulunamadı ({}). Kurulum için `scripts/setup.sh` çalıştır.",
            python.display()
        ));
    }
    let logs = data_dir().join("logs");
    fs::create_dir_all(&logs).map_err(|e| e.to_string())?;
    let log_path = logs.join("backend-process.log");
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|e| e.to_string())?;
    let log_err = log.try_clone().map_err(|e| e.to_string())?;

    let child = Command::new(&python)
        .args(["-m", "app"])
        .current_dir(&dir)
        .env("LAZOS_PORT", port().to_string())
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(log_err))
        .spawn()
        .map_err(|e| format!("Arka uç başlatılamadı: {e}"))?;

    log::info!("backend spawned (pid {})", child.id());
    *app.state::<BackendState>().child.lock().unwrap() = Some(child);
    set_info(app, |i| {
        i.managed = true;
        i.log_path = Some(log_path.display().to_string());
    });
    Ok(())
}

fn child_exited(app: &AppHandle) -> Option<String> {
    let state = app.state::<BackendState>();
    let mut guard = state.child.lock().unwrap();
    if let Some(child) = guard.as_mut() {
        if let Ok(Some(status)) = child.try_wait() {
            *guard = None;
            return Some(format!("Arka uç beklenmedik şekilde kapandı ({status})."));
        }
    }
    None
}

/// Start (or adopt) the backend and wait until it is healthy. Runs on a background thread.
pub fn start(app: AppHandle) {
    thread::spawn(move || {
        set_info(&app, |i| {
            i.status = "starting";
            i.error = None;
        });
        match probe() {
            Probe::Lazos => log::info!("adopting running LAZOS backend"),
            Probe::Foreign => {
                return fail(
                    &app,
                    format!("{} portu başka bir uygulama tarafından kullanılıyor. LAZOS_PORT ile farklı bir port seç.", port()),
                )
            }
            Probe::Closed => {
                if let Err(e) = spawn(&app) {
                    return fail(&app, e);
                }
            }
        }

        let started = Instant::now();
        loop {
            if let Some(err) = child_exited(&app) {
                let log_hint = app.state::<BackendState>().info.lock().unwrap().log_path.clone();
                return fail(&app, format!("{err} Ayrıntılar: {}", log_hint.unwrap_or_default()));
            }
            if matches!(probe(), Probe::Lazos) {
                if let Some(token) = read_token() {
                    log::info!("backend ready after {:?}", started.elapsed());
                    set_info(&app, |i| {
                        i.status = "ready";
                        i.token = Some(token);
                        i.error = None;
                    });
                    break;
                }
            }
            if started.elapsed() > STARTUP_TIMEOUT {
                return fail(&app, "Arka uç zamanında yanıt vermedi.".into());
            }
            thread::sleep(Duration::from_millis(250));
        }

        // Watchdog: report crashes of a backend we manage.
        loop {
            thread::sleep(Duration::from_secs(2));
            if let Some(err) = child_exited(&app) {
                return fail(&app, err);
            }
        }
    });
}

/// Graceful shutdown of a backend we spawned: SIGTERM, then SIGKILL after a grace period.
pub fn stop(app: &AppHandle) {
    let state = app.state::<BackendState>();
    let Some(mut child) = state.child.lock().unwrap().take() else {
        return;
    };
    let pid = child.id().to_string();
    let _ = Command::new("kill").args(["-TERM", &pid]).status();
    let deadline = Instant::now() + Duration::from_secs(4);
    while Instant::now() < deadline {
        if let Ok(Some(_)) = child.try_wait() {
            log::info!("backend stopped");
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    let _ = child.wait();
}

#[tauri::command]
pub fn backend_info(state: tauri::State<'_, BackendState>) -> BackendInfo {
    state.info.lock().unwrap().clone()
}

#[tauri::command]
pub fn restart_backend(app: AppHandle) {
    stop(&app);
    start(app);
}
