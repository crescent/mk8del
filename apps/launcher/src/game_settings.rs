//! The runtime's own settings file (`mk8d.ini`: key bindings, display options) and save import.
//! Both live in the game's working folder and are only touched while the game is closed: the runtime
//! rewrites the whole settings file from memory when it saves.
use mk8_manifest::Result;
use serde_json::{json, Map, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

/// Switch inputs as the runtime names them (upstream `runtime/hle/bindings.c` `k_ini`); the first 16 are
/// also controller buttons.
pub const INPUTS: [&str; 24] = [
    "a", "b", "x", "y", "lstick", "rstick", "l", "r", "zl", "zr", "plus", "minus", "left", "up",
    "right", "down", "ls_up", "ls_down", "ls_left", "ls_right", "rs_up", "rs_down", "rs_left",
    "rs_right",
];
/// Controller buttons by position (`k_pad_ini`).
pub const PAD_BUTTONS: [&str; 28] = [
    "south",
    "east",
    "west",
    "north",
    "back",
    "guide",
    "start",
    "lstick",
    "rstick",
    "lshoulder",
    "rshoulder",
    "dpad_up",
    "dpad_down",
    "dpad_left",
    "dpad_right",
    "misc1",
    "rpaddle1",
    "lpaddle1",
    "rpaddle2",
    "lpaddle2",
    "touchpad",
    "misc2",
    "misc3",
    "misc4",
    "misc5",
    "misc6",
    "ltrigger",
    "rtrigger",
];
/// MK8 keyboard layout, in `INPUTS` order (SDL scancode names). Must match the `DEF_KEY` patch in
/// `toolchain/host.py`, so the in-game Reset and this one agree.
pub const MK8_KEYS: [&str; 24] = [
    "X",
    "Z",
    "C",
    "V",
    "F",
    "Keypad 0",
    "Space",
    "Left Shift",
    "Q",
    "E",
    "Return",
    "Backspace",
    "J",
    "I",
    "L",
    "K",
    "Up",
    "Down",
    "Left",
    "Right",
    "Keypad 8",
    "Keypad 5",
    "Keypad 4",
    "Keypad 6",
];
/// Default controller layout (`DEF_PAD`): Nintendo positions, A on the east button.
pub const DEFAULT_PAD: [&str; 16] = [
    "east",
    "south",
    "north",
    "west",
    "lstick",
    "rstick",
    "lshoulder",
    "rshoulder",
    "ltrigger",
    "rtrigger",
    "start",
    "back",
    "dpad_left",
    "dpad_up",
    "dpad_right",
    "dpad_down",
];
const VIDEO: [(&str, &[&str]); 3] = [
    ("frame_gen", &["off", "120"]),
    ("fullscreen", &["0", "1"]),
    ("show_fps", &["0", "1"]),
];

pub fn path(local: &Path) -> PathBuf {
    local.join("user-no-mii").join("mk8d.ini")
}

/// `key = value` lines, as the runtime writes them.
pub fn read(local: &Path) -> Map<String, Value> {
    let text = fs::read_to_string(path(local)).unwrap_or_default();
    text.lines()
        .filter(|line| !line.trim_start().starts_with('#'))
        .filter_map(|line| line.split_once('='))
        .map(|(k, v)| (k.trim().to_owned(), json!(v.trim())))
        .collect()
}

fn allowed(key: &str, value: &str) -> bool {
    if value.is_empty() || value.len() > 32 || value.chars().any(|c| c.is_control()) {
        return false;
    }
    if let Some(input) = key.strip_prefix("bind_key_") {
        return INPUTS.contains(&input);
    }
    if let Some(input) = key.strip_prefix("bind_pad_") {
        return INPUTS[..16].contains(&input) && PAD_BUTTONS.contains(&value);
    }
    VIDEO
        .iter()
        .any(|(k, values)| *k == key && values.contains(&value))
}

/// Replaces (or appends) the given keys and keeps every other line as it was.
pub fn write(local: &Path, changes: &Map<String, Value>) -> Result<()> {
    let mut pending = Vec::new();
    for (key, value) in changes {
        let value = value.as_str().unwrap_or("");
        if !allowed(key, value) {
            return Err(format!("Configuração inválida: {key}"));
        }
        pending.push((key.as_str(), value));
    }
    let file = path(local);
    let old = fs::read_to_string(&file).unwrap_or_default();
    let mut lines = Vec::new();
    for line in old.lines() {
        let key = line.split_once('=').map(|(k, _)| k.trim());
        match pending.iter().position(|(k, _)| Some(*k) == key) {
            Some(i) => {
                let (k, v) = pending.remove(i);
                lines.push(format!("{k} = {v}"));
            }
            None => lines.push(line.to_owned()),
        }
    }
    lines.extend(pending.iter().map(|(k, v)| format!("{k} = {v}")));
    fs::create_dir_all(file.parent().unwrap()).map_err(|e| e.to_string())?;
    let temp = file.with_extension("ini.new");
    fs::write(&temp, lines.join("\n") + "\n").map_err(|e| e.to_string())?;
    fs::rename(&temp, &file).map_err(|e| e.to_string())
}

/// The current bindings: the file's values over the defaults (the runtime only writes the ones that differ).
pub fn controls(local: &Path) -> Value {
    let ini = read(local);
    let get = |key: String, default: &str| {
        ini.get(&key)
            .and_then(Value::as_str)
            .unwrap_or(default)
            .to_owned()
    };
    json!({
        "keys": INPUTS.iter().zip(MK8_KEYS).map(|(i, d)| (i.to_string(), json!(get(format!("bind_key_{i}"), d)))).collect::<Map<_, _>>(),
        "pad": INPUTS.iter().zip(DEFAULT_PAD).map(|(i, d)| (i.to_string(), json!(get(format!("bind_pad_{i}"), d)))).collect::<Map<_, _>>(),
        "pad_buttons": PAD_BUTTONS,
    })
}

pub fn reset_controls(local: &Path) -> Result<()> {
    let mut defaults = Map::new();
    for (i, key) in INPUTS.iter().zip(MK8_KEYS) {
        defaults.insert(format!("bind_key_{i}"), json!(key));
    }
    for (i, button) in INPUTS.iter().zip(DEFAULT_PAD) {
        defaults.insert(format!("bind_pad_{i}"), json!(button));
    }
    write(local, &defaults)
}

pub fn video(local: &Path) -> Value {
    let ini = read(local);
    VIDEO
        .iter()
        .map(|(k, values)| {
            (
                k.to_string(),
                ini.get(*k).cloned().unwrap_or(json!(values[0])),
            )
        })
        .collect::<Map<_, _>>()
        .into()
}

pub fn save_dir(local: &Path) -> PathBuf {
    local.join("user-no-mii").join("save").join("save_0")
}

const SAVE_LIMIT: u64 = 256 << 20;

/// Files of a save dump (JKSV / Checkpoint folder), refusing links and anything that looks like game content.
fn save_files(dir: &Path, base: &Path, out: &mut Vec<PathBuf>, total: &mut u64) -> Result<()> {
    for entry in fs::read_dir(dir).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let kind = entry.file_type().map_err(|e| e.to_string())?;
        let name = entry.file_name().to_string_lossy().to_lowercase();
        if kind.is_symlink() {
            return Err(
                "A pasta do save contém atalhos; copie os arquivos para uma pasta comum.".into(),
            );
        }
        if ["exefs", "romfs"].contains(&name.as_str())
            || name.ends_with(".nca")
            || name.ends_with(".nsp")
        {
            return Err("Esta pasta parece conter o jogo, não um save.".into());
        }
        if kind.is_dir() {
            save_files(&entry.path(), base, out, total)?;
        } else {
            *total += entry.metadata().map_err(|e| e.to_string())?.len();
            if *total > SAVE_LIMIT {
                return Err("A pasta é grande demais para um save do MK8.".into());
            }
            out.push(entry.path().strip_prefix(base).unwrap().to_path_buf());
        }
    }
    Ok(())
}

/// Copies a save dump into the game's save folder. The current save is kept as `save_0.backup-<time>`.
/// A single file (such as `userdata.dat`) replaces only that file in a copy of the current save.
pub fn import_save(local: &Path, source: &Path) -> Result<Value> {
    let mut source = fs::canonicalize(source).map_err(|e| e.to_string())?;
    let target = save_dir(local);
    // (absolute source, path inside save_0)
    let copies: Vec<(PathBuf, PathBuf)> = if source.is_file() {
        let name = PathBuf::from(source.file_name().unwrap());
        if !name.to_string_lossy().to_lowercase().ends_with(".dat") {
            return Err("Escolha a pasta do save ou um arquivo .dat do save.".into());
        }
        if fs::metadata(&source).map_err(|e| e.to_string())?.len() > SAVE_LIMIT {
            return Err("A pasta é grande demais para um save do MK8.".into());
        }
        let mut files = Vec::new();
        if target.is_dir() {
            save_files(&target, &target, &mut files, &mut 0)?;
        }
        let mut copies: Vec<_> = files
            .into_iter()
            .filter(|file| *file != name)
            .map(|file| (target.join(&file), file))
            .collect();
        copies.push((source.clone(), name));
        copies
    } else {
        if !source.is_dir() {
            return Err("Escolha a pasta do save.".into());
        }
        // Dumps wrap the save in folders ("Mario Kart 8 Deluxe/Complete Save [v3.0.3]/"); the game
        // reads the files at the root of save_0, so step into folders that hold nothing but one folder.
        loop {
            let entries: Vec<_> = fs::read_dir(&source)
                .map_err(|e| e.to_string())?
                .collect::<std::io::Result<_>>()
                .map_err(|e| e.to_string())?;
            match entries.as_slice() {
                [only] if only.file_type().is_ok_and(|t| t.is_dir()) => source = only.path(),
                _ => break,
            }
        }
        if target.exists() && fs::canonicalize(&target).ok().as_ref() == Some(&source) {
            return Err("Esta já é a pasta de saves do jogo.".into());
        }
        let mut files = Vec::new();
        save_files(&source, &source, &mut files, &mut 0)?;
        if files.is_empty() {
            return Err("A pasta escolhida está vazia.".into());
        }
        files
            .into_iter()
            .map(|file| (source.join(&file), file))
            .collect()
    };
    let parent = target.parent().unwrap();
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    // Copy beside the target first, so a failed copy never touches the current save.
    let temp = parent.join(format!("save_0.import-{stamp}"));
    let copied = (|| -> std::io::Result<()> {
        for (from, file) in &copies {
            let to = temp.join(file);
            fs::create_dir_all(to.parent().unwrap())?;
            fs::copy(from, to)?;
        }
        Ok(())
    })();
    if let Err(error) = copied {
        // (only this call's own fresh copy)
        let _ = fs::remove_dir_all(&temp);
        return Err(format!("Falha ao copiar o save: {error}"));
    }
    let backup = target.exists().then(|| {
        (0..)
            .map(|n| match n {
                0 => parent.join(format!("save_0.backup-{stamp}")),
                n => parent.join(format!("save_0.backup-{stamp}-{n}")),
            })
            .find(|path| !path.exists())
            .unwrap()
    });
    if let Some(backup) = &backup {
        fs::rename(&target, backup).map_err(|e| e.to_string())?;
    }
    if let Err(error) = fs::rename(&temp, &target) {
        if let Some(backup) = &backup {
            let _ = fs::rename(backup, &target);
        }
        return Err(error.to_string());
    }
    Ok(json!({
        "files": copies.len(),
        "backup": backup,
        "message": format!("Save importado ({} arquivo(s)).", copies.len()),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("mk8-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn settings_edit_keeps_other_lines_and_rejects_unknown_keys() {
        let local = temp("ini");
        fs::create_dir_all(local.join("user-no-mii")).unwrap();
        fs::write(
            path(&local),
            "# header\nvolume = 0.80\nframe_gen = off\nbind_key_a = K\n",
        )
        .unwrap();
        let mut change = Map::new();
        change.insert("frame_gen".into(), json!("120"));
        change.insert("bind_key_a".into(), json!("Left Shift"));
        change.insert("bind_pad_zr".into(), json!("rtrigger"));
        write(&local, &change).unwrap();
        let text = fs::read_to_string(path(&local)).unwrap();
        assert_eq!(
            text,
            "# header\nvolume = 0.80\nframe_gen = 120\nbind_key_a = Left Shift\nbind_pad_zr = rtrigger\n"
        );
        for (key, value) in [
            ("volume", "1"),
            ("frame_gen", "999"),
            ("bind_key_a", "A\nfullscreen = 1"),
            ("bind_pad_ls_up", "south"),
        ] {
            let mut bad = Map::new();
            bad.insert(key.into(), json!(value));
            assert!(write(&local, &bad).is_err(), "{key}");
        }
        assert_eq!(fs::read_to_string(path(&local)).unwrap(), text);
        assert_eq!(controls(&local)["keys"]["a"], "Left Shift");
        assert_eq!(controls(&local)["keys"]["b"], "Z");
        assert_eq!(video(&local)["fullscreen"], "0");
        reset_controls(&local).unwrap();
        assert_eq!(controls(&local)["keys"]["a"], "X");
        assert_eq!(read(&local)["volume"], "0.80");
        fs::remove_dir_all(local).unwrap();
    }

    #[test]
    fn save_import_backs_up_the_current_save() {
        let local = temp("save-local");
        let dump = temp("save-dump");
        fs::create_dir_all(save_dir(&local)).unwrap();
        fs::write(save_dir(&local).join("old.dat"), b"old").unwrap();
        fs::create_dir_all(dump.join("sub")).unwrap();
        fs::write(dump.join("rp0.dat"), b"new").unwrap();
        fs::write(dump.join("sub/b.dat"), b"b").unwrap();
        let result = import_save(&local, &dump).unwrap();
        assert_eq!(result["files"], 2);
        assert_eq!(fs::read(save_dir(&local).join("rp0.dat")).unwrap(), b"new");
        assert_eq!(fs::read(save_dir(&local).join("sub/b.dat")).unwrap(), b"b");
        let backup = PathBuf::from(result["backup"].as_str().unwrap());
        assert_eq!(fs::read(backup.join("old.dat")).unwrap(), b"old");
        fs::create_dir_all(dump.join("romfs")).unwrap();
        assert!(import_save(&local, &dump).is_err());
        assert!(import_save(&local, &save_dir(&local)).is_err());
        let wrapped = temp("save-wrapped");
        fs::create_dir_all(wrapped.join("Mario Kart 8 Deluxe/Complete Save")).unwrap();
        fs::write(
            wrapped.join("Mario Kart 8 Deluxe/Complete Save/userdata.dat"),
            b"u",
        )
        .unwrap();
        import_save(&local, &wrapped).unwrap();
        assert_eq!(
            fs::read(save_dir(&local).join("userdata.dat")).unwrap(),
            b"u"
        );
        fs::remove_dir_all(wrapped).unwrap();
        // One file replaces only itself; the rest of the save stays.
        let single = temp("save-single").join("userdata.dat");
        fs::write(&single, b"v").unwrap();
        fs::write(save_dir(&local).join("sg00.dat"), b"g").unwrap();
        let result = import_save(&local, &single).unwrap();
        assert_eq!(
            fs::read(save_dir(&local).join("userdata.dat")).unwrap(),
            b"v"
        );
        assert_eq!(fs::read(save_dir(&local).join("sg00.dat")).unwrap(), b"g");
        let backup = PathBuf::from(result["backup"].as_str().unwrap());
        assert_eq!(fs::read(backup.join("userdata.dat")).unwrap(), b"u");
        fs::write(single.with_file_name("game.nsp"), b"x").unwrap();
        assert!(import_save(&local, &single.with_file_name("game.nsp")).is_err());
        fs::remove_dir_all(single.parent().unwrap()).unwrap();
        fs::remove_dir_all(local).unwrap();
        fs::remove_dir_all(dump).unwrap();
    }
}
