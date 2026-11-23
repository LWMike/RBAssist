//! RBAssist shell. The audit itself runs in the Python engine (`engine/audit.py`)
//! through `uv`; this side starts it, relays progress and keeps review decisions.
//! Writing to Rekordbox is `engine/writeback.py`, which refuses while Rekordbox
//! is open and backs up first.

use serde_json::Value;
use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use tauri::{AppHandle, Emitter, Manager};

fn engine_script(name: &str) -> PathBuf {
    // RBASSIST_ENGINE points at audit.py; the other scripts sit beside it.
    if let Ok(p) = std::env::var("RBASSIST_ENGINE") {
        return PathBuf::from(p).with_file_name(name);
    }
    // Development layout: app/src-tauri -> ../../engine
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../engine").join(name)
}

fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_default())
}

/// Apps started from Finder get a bare PATH, so look in the usual places.
fn tool_path() -> String {
    let extra = [
        home().join(".local/bin"),
        home().join(".cargo/bin"),
        PathBuf::from("/opt/homebrew/bin"),
        PathBuf::from("/usr/local/bin"),
    ];
    let mut parts: Vec<String> = extra.iter().map(|p| p.to_string_lossy().into_owned()).collect();
    parts.push(std::env::var("PATH").unwrap_or_default());
    parts.join(":")
}

fn engine_command() -> Result<Command, String> {
    script_command("audit.py")
}

fn script_command(name: &str) -> Result<Command, String> {
    let script = engine_script(name);
    if !script.exists() {
        return Err(format!("Audit engine not found at {}", script.display()));
    }
    let mut c = Command::new("uv");
    c.env("PATH", tool_path()).arg("run").arg("-q").arg(script);
    Ok(c)
}

fn data_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let d = app.path().app_data_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&d).map_err(|e| e.to_string())?;
    Ok(d)
}

fn spawn_error(e: std::io::Error) -> String {
    if e.kind() == std::io::ErrorKind::NotFound {
        "Could not start the audit engine: `uv` is not installed (https://docs.astral.sh/uv/).".into()
    } else {
        format!("Could not start the audit engine: {e}")
    }
}

#[tauri::command]
async fn list_playlists() -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let out = engine_command()?.arg("--list-playlists").output().map_err(spawn_error)?;
        if !out.status.success() {
            return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
        }
        serde_json::from_slice(&out.stdout).map_err(|e| format!("Unreadable playlist list: {e}"))
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn run_audit(app: AppHandle, playlist_ids: Vec<String>, crate_xml: Option<String>) -> Result<Value, String> {
    let out_file = data_dir(&app)?.join("audit.json");
    tauri::async_runtime::spawn_blocking(move || {
        let mut c = engine_command()?;
        for id in &playlist_ids {
            c.arg("--playlist-id").arg(id);
        }
        if let Some(x) = crate_xml.filter(|x| !x.trim().is_empty()) {
            c.arg("--crate-xml").arg(x.trim());
        }
        c.arg("--previews").arg("--cues").arg("--progress").arg("--out").arg(&out_file);
        let mut child = c.stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().map_err(spawn_error)?;
        if let Some(stdout) = child.stdout.take() {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if let Ok(v) = serde_json::from_str::<Value>(&line) {
                    let _ = app.emit("audit-progress", v);
                }
            }
        }
        let mut err = String::new();
        if let Some(mut e) = child.stderr.take() {
            let _ = e.read_to_string(&mut err);
        }
        let status = child.wait().map_err(|e| e.to_string())?;
        if !status.success() {
            let last = err.trim().lines().last().unwrap_or("the audit engine failed").to_string();
            return Err(last);
        }
        let text = std::fs::read_to_string(&out_file).map_err(|e| e.to_string())?;
        serde_json::from_str(&text).map_err(|e| e.to_string())
    })
    .await
    .map_err(|e| e.to_string())?
}

/// The most recent audit, if there is one.
#[tauri::command]
fn last_audit(app: AppHandle) -> Result<Option<Value>, String> {
    let f = data_dir(&app)?.join("audit.json");
    match std::fs::read_to_string(f) {
        Ok(t) => serde_json::from_str(&t).map(Some).map_err(|e| e.to_string()),
        Err(_) => Ok(None),
    }
}

/// Bytes of a preview clip. Only files inside this app's previews folder are served.
#[tauri::command]
fn read_preview(app: AppHandle, path: String) -> Result<tauri::ipc::Response, String> {
    let root = data_dir(&app)?.join("previews").canonicalize().map_err(|e| e.to_string())?;
    let p = PathBuf::from(path).canonicalize().map_err(|e| e.to_string())?;
    if !p.starts_with(&root) {
        return Err("not a preview file".into());
    }
    std::fs::read(p).map(tauri::ipc::Response::new).map_err(|e| e.to_string())
}

/// Bytes of a track's audio, for playing from a cue. Only files that are in
/// the latest audit are served.
#[tauri::command]
fn read_audio(app: AppHandle, path: String) -> Result<tauri::ipc::Response, String> {
    let audit = std::fs::read_to_string(data_dir(&app)?.join("audit.json")).map_err(|e| e.to_string())?;
    let rows: Value = serde_json::from_str(&audit).map_err(|e| e.to_string())?;
    let known = rows.as_array().map(|a| a.iter().any(|r| r["path"].as_str() == Some(path.as_str()))).unwrap_or(false);
    if !known {
        return Err("not a track from the latest audit".into());
    }
    std::fs::read(&path).map(tauri::ipc::Response::new).map_err(|e| format!("Could not read the audio file: {e}"))
}

/// Run the write-back engine and return its JSON answer.
fn writeback(app: &AppHandle, action: &str, plan: Option<Value>, force: bool) -> Result<Value, String> {
    let d = data_dir(app)?;
    let mut c = script_command("writeback.py")?;
    c.arg(action).arg("--backups").arg(d.join("backups"));
    if let Some(plan) = plan {
        let pf = d.join("plan.json");
        std::fs::write(&pf, serde_json::to_vec(&plan).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
        c.arg("--audit").arg(d.join("audit.json")).arg("--plan").arg(pf);
    }
    if force {
        c.arg("--force");
    }
    let out = c.output().map_err(spawn_error)?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(err.trim().lines().last().unwrap_or("the write-back engine failed").to_string());
    }
    serde_json::from_slice(&out.stdout).map_err(|e| format!("Unreadable answer from the write-back engine: {e}"))
}

/// What a write would do, checked against the live library. Changes nothing.
#[tauri::command]
async fn write_check(app: AppHandle, plan: Value) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || writeback(&app, "check", Some(plan), false)).await.map_err(|e| e.to_string())?
}

/// Back up, then write the plan into Rekordbox. Refused while Rekordbox is open.
#[tauri::command]
async fn write_apply(app: AppHandle, plan: Value) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || writeback(&app, "write", Some(plan), false)).await.map_err(|e| e.to_string())?
}

/// Whether Rekordbox is open, and the last write that can be undone.
#[tauri::command]
async fn write_status(app: AppHandle) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || writeback(&app, "status", None, false)).await.map_err(|e| e.to_string())?
}

/// Put the last backup back.
#[tauri::command]
async fn write_undo(app: AppHandle, force: bool) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || writeback(&app, "undo", None, force)).await.map_err(|e| e.to_string())?
}

/// Run a script with arguments; its last stdout line is the JSON answer, and
/// any earlier JSON lines are progress, relayed as `event`.
fn run_json(app: &AppHandle, script: &str, args: &[String], event: Option<&str>) -> Result<Value, String> {
    let mut c = script_command(script)?;
    c.args(args);
    let mut child = c.stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().map_err(spawn_error)?;
    let mut last = String::new();
    if let Some(stdout) = child.stdout.take() {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if !last.is_empty() {
                if let (Some(ev), Ok(v)) = (event, serde_json::from_str::<Value>(&last)) {
                    let _ = app.emit(ev, v);
                }
            }
            last = line;
        }
    }
    let mut err = String::new();
    if let Some(mut e) = child.stderr.take() {
        let _ = e.read_to_string(&mut err);
    }
    if !child.wait().map_err(|e| e.to_string())?.success() {
        return Err(err.trim().lines().last().unwrap_or("the engine failed").to_string());
    }
    serde_json::from_str(&last).map_err(|e| format!("Unreadable answer from {script}: {e}"))
}

fn crate_args(action: &str, crate_xml: &str, root: &Option<String>, playlists: &[String]) -> Vec<String> {
    let mut a = vec![action.to_string(), "--crate-xml".into(), crate_xml.trim().to_string()];
    if let Some(r) = root.as_ref().filter(|r| !r.trim().is_empty()) {
        a.push("--root".into());
        a.push(r.trim().to_string());
    }
    for p in playlists {
        a.push("--playlist".into());
        a.push(p.clone());
    }
    a
}

/// Crate's playlists, each track remapped to this Mac and checked.
#[tauri::command]
async fn crate_scan(app: AppHandle, crate_xml: String, root: Option<String>) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || run_json(&app, "intake.py", &crate_args("scan", &crate_xml, &root, &[]), None))
        .await
        .map_err(|e| e.to_string())?
}

/// The Rekordbox XML for the chosen playlists, written to the app data folder.
#[tauri::command]
async fn crate_build(app: AppHandle, crate_xml: String, root: Option<String>, playlists: Vec<String>) -> Result<Value, String> {
    let out = data_dir(&app)?.join("crate-import.xml");
    tauri::async_runtime::spawn_blocking(move || {
        let mut a = crate_args("build", &crate_xml, &root, &playlists);
        a.push("--out".into());
        a.push(out.to_string_lossy().into_owned());
        run_json(&app, "intake.py", &a, None)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Fetch the chosen playlists' online-only Dropbox files.
#[tauri::command]
async fn crate_download(app: AppHandle, crate_xml: String, root: Option<String>, playlists: Vec<String>) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut a = crate_args("download", &crate_xml, &root, &playlists);
        a.push("--progress".into());
        run_json(&app, "intake.py", &a, Some("download-progress"))
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Review decisions and settings, one JSON file.
#[tauri::command]
fn load_state(app: AppHandle) -> Result<Value, String> {
    let f = data_dir(&app)?.join("state.json");
    Ok(std::fs::read_to_string(f)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_else(|| serde_json::json!({})))
}

#[tauri::command]
fn save_state(app: AppHandle, state: Value) -> Result<(), String> {
    let d = data_dir(&app)?;
    let tmp = d.join("state.json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(&state).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    std::fs::rename(tmp, d.join("state.json")).map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            list_playlists,
            run_audit,
            last_audit,
            read_preview,
            read_audio,
            load_state,
            save_state,
            write_check,
            write_apply,
            write_status,
            write_undo,
            crate_scan,
            crate_build,
            crate_download
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
