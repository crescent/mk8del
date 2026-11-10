#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
//! MK8-online Launcher: a native Tauri window over the local API server.
//! `--headless` (or the older `--no-browser`) keeps only the server, for tests.
mod console;
mod game_session;
mod game_settings;
mod installer;
mod language;
mod relay;
mod report;
mod server;
mod setup;
mod update;

use mk8_manifest::{load, save, Config};
use serde_json::json;
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Arc, OnceLock},
};
use tauri::{webview::NewWindowResponse, WebviewUrl, WebviewWindowBuilder};
use tiny_http::Server;

pub(crate) type Error = Box<dyn std::error::Error + Send + Sync>;

fn argument(args: &[String], name: &str) -> Option<String> {
    args.iter()
        .position(|v| v == name)
        .and_then(|n| args.get(n + 1))
        .cloned()
}

/// `canonicalize` on Windows returns `\\?\C:\…`. In that verbatim form `/` is not a separator, so
/// `local.join("imports/mk8-4.0.1")`, the build's `data_root` and the game's paths stop resolving.
/// Keep the ordinary drive/UNC spelling; other prefixes (or non-UTF-8 paths) stay as they are.
pub(crate) fn canonical(path: &Path) -> std::io::Result<PathBuf> {
    let path = path.canonicalize()?;
    #[cfg(windows)]
    {
        use std::path::{Component, Prefix};
        let kind = match path.components().next() {
            Some(Component::Prefix(prefix)) => prefix.kind(),
            _ => return Ok(path),
        };
        if let Some(text) = path.to_str() {
            match kind {
                Prefix::VerbatimDisk(_) => return Ok(PathBuf::from(&text[4..])),
                Prefix::VerbatimUNC(..) => return Ok(PathBuf::from(format!(r"\\{}", &text[8..]))),
                _ => {}
            }
        }
    }
    Ok(path)
}

pub(crate) fn fatal(message: &str) {
    eprintln!("MK8-online: {message}");
    rfd::MessageDialog::new()
        .set_title("MK8-online")
        .set_level(rfd::MessageLevel::Error)
        .set_description(message)
        .show();
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if let Some(mode) = args
        .get(1)
        .filter(|m| *m == "--task-supervisor" || *m == "--game-supervisor")
    {
        let result = if mode == "--task-supervisor" {
            mk8_build_manager::supervisor::run_task()
        } else {
            game_session::run()
        };
        let code = match result {
            Ok(code) => code,
            Err(error) => {
                println!("{error}");
                1
            }
        };
        std::process::exit(code);
    }
    if let Some(mode) = installer::mode(&args) {
        if let Err(error) = installer::run(mode, &args) {
            fatal(&format!(
                "The installer could not continue / O instalador não pôde continuar: {error}"
            ));
            std::process::exit(1);
        }
        return;
    }
    let headless = args
        .iter()
        .any(|v| v == "--headless" || v == "--no-browser");
    if headless {
        console::attach_parent();
    }
    if let Err(error) = run(&args, headless) {
        if headless {
            eprintln!("MK8-online: {error}");
        } else {
            fatal(&format!(
                "The Launcher could not start / O Launcher não pôde iniciar: {error}"
            ));
        }
        std::process::exit(1);
    }
}

fn run(args: &[String], headless: bool) -> Result<(), Error> {
    let root = project_root()?;
    run_at(root, args, headless)
}

#[cfg(not(mk8_portable))]
fn project_root() -> Result<PathBuf, Error> {
    Ok(Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .ok_or("raiz do projeto")?
        .to_path_buf())
}

#[cfg(mk8_portable)]
fn project_root() -> Result<PathBuf, Error> {
    let base = match home_from_setup()? {
        Some(home) => home,
        None => PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA ausente")?)
            .join("MK8-online"),
    };
    unpack_payload(&base)
}

/// This version's sources under `<base>/<payload id>` (unpacked once; the installer does it ahead of the first
/// start), with the shared tool folder set up.
#[cfg(mk8_portable)]
pub(crate) fn unpack_payload(base: &Path) -> Result<PathBuf, Error> {
    use std::{os::windows::process::CommandExt, process::Command};
    const PAYLOAD: &[u8] = include_bytes!(env!("MK8_LAUNCHER_PAYLOAD"));
    let root = base.join(&env!("MK8_LAUNCHER_PAYLOAD_ID")[..16]);
    if root.is_dir() {
        std::env::set_var("MK8_NATIVE_TOOLS", share_tools(base, &root)?);
        return Ok(root);
    }
    fs::create_dir_all(base)?;
    let attempt = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_nanos();
    let stage = base.join(format!("preparando-{}-{attempt}", std::process::id()));
    fs::create_dir(&stage)?;
    let archive = stage.join("payload.zip");
    fs::write(&archive, PAYLOAD)?;
    let tar = Path::new(&std::env::var_os("SystemRoot").ok_or("SystemRoot ausente")?)
        .join("System32/tar.exe");
    let output = Command::new(tar)
        .arg("-xf")
        .arg(&archive)
        .current_dir(&stage)
        .creation_flags(0x08000000)
        .output()?;
    fs::remove_file(archive)?;
    if !output.status.success() {
        return Err(format!(
            "Falha ao preparar o launcher: {}",
            String::from_utf8_lossy(&output.stderr)
        )
        .into());
    }
    publish_payload(&stage, &root, std::time::Duration::from_secs(15))?;
    std::env::set_var("MK8_NATIVE_TOOLS", share_tools(base, &root)?);
    Ok(root)
}

#[cfg_attr(not(any(mk8_portable, test)), allow(dead_code))]
fn publish_payload(stage: &Path, root: &Path, timeout: std::time::Duration) -> Result<(), Error> {
    let deadline = std::time::Instant::now() + timeout;
    loop {
        match fs::rename(stage, root) {
            Ok(()) => return Ok(()),
            Err(error)
                if matches!(error.raw_os_error(), Some(5 | 32 | 33))
                    && std::time::Instant::now() < deadline =>
            {
                // Scanners may still hold the freshly extracted directory. Never delete to retry.
                std::thread::sleep(std::time::Duration::from_millis(200));
            }
            Err(error) => return Err(format!(
                "Não foi possível concluir a preparação de {} para {}: {error}. Os arquivos preparados foram preservados. Confira permissões e arquivos em uso e tente novamente.",
                stage.display(), root.display()
            ).into()),
        }
    }
}

/// Each launcher version unpacks into its own `<base>/<id>`, and the pinned Zig, graphics
/// libraries and FFmpeg (~0.9 GiB) used to be downloaded again inside every one of them. All
/// versions now share `<base>/native-tools`, given to the setup and build scripts as
/// `MK8_NATIVE_TOOLS`; a junction in `<id>/local` was refused by Python (WinError 448, untrusted
/// mount point). While the shared folder does not exist, the download of this version, or else of
/// an older one, is moved into it, so an update does not download again; the scripts still verify
/// every archive by SHA-256. Only a junction to the shared folder (made by an earlier build of this
/// launcher) is removed: the link itself, never its target.
#[cfg_attr(not(any(mk8_portable, test)), allow(dead_code))]
fn share_tools(base: &Path, root: &Path) -> Result<PathBuf, Error> {
    let shared = base.join("native-tools");
    let tools = |dir: &Path| dir.join("local").join("native-tools");
    let own = tools(root);
    if let Ok(target) = fs::read_link(&own) {
        let target = target.to_string_lossy().replace(r"\\?\", "");
        if Path::new(&target) == shared {
            fs::remove_dir(&own)?;
        }
    }
    if !shared.exists() {
        let previous = std::iter::once(root.to_path_buf())
            .chain(fs::read_dir(base)?.filter_map(|entry| entry.ok().map(|e| e.path())))
            .map(|dir| tools(&dir))
            .find(|dir| fs::read_link(dir).is_err() && dir.is_dir());
        if let Some(previous) = previous {
            // Two launchers may start together (multiplayer tests): one of them moves it.
            if let Err(error) = fs::rename(&previous, &shared) {
                if !shared.is_dir() {
                    return Err(error.into());
                }
            }
        }
    }
    fs::create_dir_all(&shared)?;
    Ok(shared)
}

/// The folder chosen in setup.exe for the unpacked versions and the data (`mk8-online.json` next to the
/// executable, `{"home": "D:\\MK8-online"}`); `None` keeps the default under `%LOCALAPPDATA%`.
#[cfg(mk8_portable)]
fn home_from_setup() -> Result<Option<PathBuf>, Error> {
    let exe = std::env::current_exe()?;
    let file = exe.with_file_name("mk8-online.json");
    if !file.is_file() {
        return Ok(None);
    }
    let value: serde_json::Value = serde_json::from_str(&fs::read_to_string(&file)?)
        .map_err(|e| format!("{}: {e}", file.display()))?;
    let home = value["home"]
        .as_str()
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .ok_or_else(|| format!("{}: \"home\" deve ser um caminho absoluto", file.display()))?;
    Ok(Some(home))
}

fn run_at(root: PathBuf, args: &[String], headless: bool) -> Result<(), Error> {
    // The packaged launcher unpacks each version beside the others; its data stays in one folder for all
    // versions, so an update keeps the import, tools, builds and saves.
    let default_data = if cfg!(mk8_portable) {
        root.parent().ok_or("pasta do launcher")?.join("data")
    } else {
        root.join("local")
    };
    let local = argument(args, "--data-dir")
        .map(PathBuf::from)
        .unwrap_or(default_data);
    fs::create_dir_all(&local)?;
    let local = canonical(&local)?;
    let mut config = if local.join("config.json").exists() {
        load(&local.join("config.json"))?
    } else {
        Config {
            upstream: root.join("upstream/smm2recomp").display().to_string(),
            ..Config::default()
        }
    };
    if cfg!(mk8_portable) {
        // (a data folder written by an earlier version names that version's sources)
        config.upstream = root.join("upstream/smm2recomp").display().to_string();
    }
    let port = argument(args, "--port")
        .unwrap_or_else(|| "0".into())
        .parse::<u16>()?;
    let server = Server::http(("127.0.0.1", port))?;
    let authority = server.server_addr().to_string();
    let url = format!("http://{authority}");
    let token = mk8_transport::random::<32>()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();
    let parent = (!headless).then(|| Arc::new(OnceLock::new()));
    let app = server::App::new(root, local.clone(), config, parent.clone());
    save(
        &local.join("launcher.json"),
        &json!({"url":url,"pid":std::process::id()}),
    )?;
    println!("MK8-online: {url}");
    let Some(parent) = parent else {
        server::serve(app, server, authority, url, token);
        return Ok(());
    };
    let page = url.clone();
    std::thread::spawn(move || server::serve(app, server, authority, url, token));
    let target: tauri::Url = page.parse()?;
    let origin = target.origin();
    tauri::Builder::default()
        .setup(move |tauri_app| {
            let window = WebviewWindowBuilder::new(tauri_app, "main", WebviewUrl::External(target))
                .title("MK8-online")
                .inner_size(1280.0, 880.0)
                .min_inner_size(400.0, 560.0)
                .center()
                .disable_drag_drop_handler()
                // Only the local launcher page; no external sites, no popups.
                .on_navigation(move |url| url.origin() == origin)
                .on_new_window(|_, _| NewWindowResponse::Deny)
                .build()?;
            let _ = parent.set(window);
            Ok(())
        })
        .run(tauri::generate_context!())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{canonical, share_tools};
    use std::{fs, process::Command};

    #[test]
    fn versions_share_one_tool_folder() {
        let base = std::env::temp_dir().join(format!("mk8-share-tools-{}", std::process::id()));
        let _ = fs::remove_dir_all(&base);
        let old = base.join("aaaa");
        fs::create_dir_all(old.join("local/native-tools/deps")).unwrap();
        fs::write(old.join("local/native-tools/deps/x.done"), "hash").unwrap();
        // An update unpacks a new version first: it adopts the older version's download.
        let new = base.join("bbbb");
        fs::create_dir_all(new.join("local")).unwrap();
        let shared = share_tools(&base, &new).unwrap();
        assert_eq!(shared, base.join("native-tools"));
        assert_eq!(
            fs::read_to_string(shared.join("deps/x.done")).unwrap(),
            "hash"
        );
        assert!(!old.join("local/native-tools").exists());
        // Reopening is a no-op and keeps the shared download.
        assert_eq!(share_tools(&base, &new).unwrap(), shared);
        assert!(shared.join("deps/x.done").is_file());
        // A junction left by the earlier build is removed (only the link); its target stays.
        // `cmd` reads a `/` as an option: build the path with `\`.
        let link = new.join("local").join("native-tools");
        let made = Command::new("cmd")
            .arg("/d")
            .arg("/c")
            .arg("mklink")
            .arg("/J")
            .arg(&link)
            .arg(&shared)
            .output()
            .unwrap();
        assert!(made.status.success(), "{:?}", made);
        assert!(fs::read_link(&link).is_ok());
        share_tools(&base, &new).unwrap();
        assert!(fs::symlink_metadata(&link).is_err());
        assert!(shared.join("deps/x.done").is_file());
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn canonical_data_folder_accepts_slash_joins() {
        let base = std::env::temp_dir().join(format!("mk8-canonical-{}", std::process::id()));
        fs::create_dir_all(base.join("extracted").join("romfs")).unwrap();
        let path = canonical(&base).unwrap();
        let verbatim = path.to_string_lossy().starts_with(r"\\?\");
        let found = path.join("extracted/romfs").is_dir();
        fs::remove_dir_all(&base).unwrap();
        assert!(!verbatim, "{}", path.display());
        assert!(found, "{}", path.display());
    }

    #[test]
    fn payload_publication_waits_for_a_real_directory_lock_and_preserves_on_failure() {
        use std::{os::windows::fs::OpenOptionsExt, time::Duration};
        let base = std::env::temp_dir().join(format!("mk8-payload-lock-{}", std::process::id()));
        let stage = base.join("stage");
        let root = base.join("ready");
        fs::create_dir_all(&stage).unwrap();
        fs::write(stage.join("keep.txt"), b"prepared").unwrap();
        let handle = fs::OpenOptions::new()
            .read(true)
            .share_mode(1)
            .custom_flags(0x02000000)
            .open(&stage)
            .unwrap();
        let error = crate::publish_payload(&stage, &root, Duration::ZERO).unwrap_err();
        assert!(error.to_string().contains("preservados"));
        assert_eq!(fs::read(stage.join("keep.txt")).unwrap(), b"prepared");
        assert!(!root.exists());
        let unlock = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(250));
            drop(handle);
        });
        crate::publish_payload(&stage, &root, Duration::from_secs(5)).unwrap();
        unlock.join().unwrap();
        assert_eq!(fs::read(root.join("keep.txt")).unwrap(), b"prepared");
        assert!(!stage.exists());
        fs::remove_dir_all(base).unwrap();
    }
}
