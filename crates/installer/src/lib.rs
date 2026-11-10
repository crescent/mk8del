use mk8_manifest::{verify, Config, Installation, Result};
use serde::Serialize;
use std::{
    fs,
    path::Path,
    process::{Child, Command, Stdio},
};

pub mod avatar;
pub mod mii;

#[derive(Serialize)]
pub struct Check {
    pub name: String,
    pub ok: bool,
    pub detail: String,
}

pub fn executable_architecture(exefs: &Path) -> Result<&'static str> {
    let raw = fs::read(exefs.join("main.npdm")).map_err(|e| format!("NPDM indisponível: {e}"))?;
    npdm_architecture(&raw)
}

fn npdm_architecture(raw: &[u8]) -> Result<&'static str> {
    if raw.len() < 0x80 || &raw[..4] != b"META" {
        return Err("main.npdm inválido ou truncado.".into());
    }
    let offset = u32::from_le_bytes(raw[0x70..0x74].try_into().unwrap()) as usize;
    let size = u32::from_le_bytes(raw[0x74..0x78].try_into().unwrap()) as usize;
    if offset < 0x80
        || size < 0x38
        || offset.checked_add(size).is_none_or(|end| end > raw.len())
        || raw.get(offset..offset + 4) != Some(b"ACI0".as_slice())
    {
        return Err("Seção ACI0 inválida no NPDM.".into());
    }
    let title = u64::from_le_bytes(raw[offset + 0x10..offset + 0x18].try_into().unwrap());
    if title != 0x0100152000022000 {
        return Err(format!(
            "Title ID {title:016X} não corresponde ao MK8 Deluxe."
        ));
    }
    Ok(if raw[0xc] & 1 == 1 {
        "aarch64"
    } else {
        "aarch32"
    })
}

pub fn architecture_check(config: &Config) -> Check {
    let (ok, detail) = match executable_architecture(Path::new(&config.exefs)) {
        Ok("aarch64") => (true, "ExeFS AArch64 (64 bits); arquitetura compatível com o tradutor. Gameplay ainda não qualificado.".into()),
        Ok(_) => (false, "Este ExeFS é AArch32 (32 bits). O tradutor exige AArch64. Importe o ExeFS de uma atualização de 64 bits do MK8.".into()),
        Err(e) => (false, e),
    };
    Check {
        name: "Arquitetura do executável".into(),
        ok,
        detail,
    }
}

pub fn inspect(config: &Config) -> Vec<Check> {
    let mut checks = vec![architecture_check(config)];
    for (name, raw, directory) in [
        ("Fontes do runtime", &config.upstream, true),
        ("ExeFS", &config.exefs, true),
        ("RomFS extraído", &config.romfs, true),
        ("Metadados NACP", &config.nacp, false),
        ("Fontes do jogo", &config.fonts, true),
    ] {
        let p = Path::new(raw);
        let ok = !raw.is_empty() && if directory { p.is_dir() } else { p.is_file() };
        checks.push(Check {
            name: name.into(),
            ok,
            detail: if ok {
                "Encontrado; identidade será validada na preparação.".into()
            } else {
                "Informe um caminho existente.".into()
            },
        });
    }
    for module in ["main", "sdk", "subsdk0", "main.npdm"] {
        let p = Path::new(&config.exefs).join(module);
        checks.push(Check {
            name: format!("ExeFS / {module}"),
            ok: !config.exefs.is_empty() && p.is_file(),
            detail: p.display().to_string(),
        });
    }
    checks
}

pub fn validate_installation(install: &Installation) -> Result<()> {
    if install.schema != 2 || install.profile != "mk8d-smm2-qualified" || !install.gameplay_verified
    {
        return Err("Instalação ainda não qualificada para o runtime MK8.".into());
    }
    validate_files(install)
}

pub fn validate_candidate(install: &Installation) -> Result<()> {
    if install.schema != 2
        || install.profile != "mk8d-smm2-experimental"
        || install.gameplay_verified
    {
        return Err("Manifesto experimental inválido.".into());
    }
    validate_files(install)
}

fn validate_files(install: &Installation) -> Result<()> {
    verify(&install.executable)?;
    for key in ["npdm", "main", "sdk", "subsdk0", "gamelib"] {
        if !install.files.contains_key(key) {
            return Err(format!("Instalação sem {key}"));
        }
    }
    for file in install.files.values() {
        verify(file)?;
    }
    let romfs = install.data_root.join("extracted").join("romfs");
    if !romfs.is_dir() {
        // Builds before the hard-link mirror left a junction here, which the launcher's
        // RedirectionTrust mitigation refuses to follow (see toolchain/build.py `mirror`).
        if fs::symlink_metadata(&romfs).is_ok_and(|m| m.file_type().is_symlink()) {
            return Err("RomFS ligado por um atalho que o Windows bloqueia para o launcher. Clique em Preparar novamente.".into());
        }
        return Err("Instalação incompleta: RomFS ausente".into());
    }
    Ok(())
}

pub fn launch(
    install: &Installation,
    local: &Path,
    network: Option<(String, u8)>,
    username: &str,
) -> Result<Child> {
    validate_installation(install)?;
    spawn_game(install, local, network, username)
}

pub fn launch_candidate(
    install: &Installation,
    local: &Path,
    network: Option<(String, u8)>,
    username: &str,
) -> Result<Child> {
    validate_candidate(install)?;
    spawn_game(install, local, network, username)
}

/// Number of previous game logs kept in `logs/` (the current one stays at `game.log`).
pub const GAME_LOG_HISTORY: usize = 10;

/// Moves the previous session's `game.log` to `logs/game-<unix time>.log` and keeps the newest
/// [`GAME_LOG_HISTORY`] of them, so a crash report can still be collected after the next launch.
pub fn rotate_game_log(local: &Path) -> Result<()> {
    let current = local.join("game.log");
    let Ok(meta) = fs::metadata(&current) else {
        return Ok(());
    };
    if meta.len() == 0 {
        return Ok(());
    }
    let logs = local.join("logs");
    fs::create_dir_all(&logs).map_err(|e| e.to_string())?;
    let stamp = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map_or(0, |d| d.as_secs());
    let mut target = logs.join(format!("game-{stamp}.log"));
    let mut n = 1;
    while target.exists() {
        target = logs.join(format!("game-{stamp}-{n}.log"));
        n += 1;
    }
    fs::rename(&current, &target).map_err(|e| e.to_string())?;
    let mut old: Vec<_> = fs::read_dir(&logs)
        .map_err(|e| e.to_string())?
        .filter_map(|entry| entry.ok().map(|e| e.path()))
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("game-") && n.ends_with(".log"))
        })
        .collect();
    old.sort_by_key(|p| fs::metadata(p).and_then(|m| m.modified()).ok());
    let excess = old.len().saturating_sub(GAME_LOG_HISTORY);
    for path in old.into_iter().take(excess) {
        fs::remove_file(path).map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn spawn_game(
    install: &Installation,
    local: &Path,
    network: Option<(String, u8)>,
    username: &str,
) -> Result<Child> {
    let saves = local.join("user-no-mii");
    fs::create_dir_all(&saves).map_err(|e| e.to_string())?;
    rotate_game_log(local)?;
    let log = fs::File::create(local.join("game.log")).map_err(|e| e.to_string())?;
    let mut cmd = Command::new(&install.executable.path);
    // The runtime uses LARECOMP_DATA_ROOT as cwd; keep saves out of versioned builds.
    cmd.current_dir(&saves)
        .env("MK8_EXPERIMENTAL_NO_MII", "1")
        .env("RECOMP_USER_NAME", username)
        .env("MK8_ROOM_MENU_SIGNAL", local.join("open-rooms"))
        .env("LARECOMP_DATA_ROOT", &saves)
        .env("LARECOMP_GAME_LIB", &install.files["gamelib"].path)
        .arg(install.data_root.join("extracted"))
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone().map_err(|e| e.to_string())?))
        .stderr(Stdio::from(log));
    for env in [
        "MK8_MII_PROFILE_PATH",
        "MK8_MII_ID",
        "MK8_MII_NAME",
        "MK8_MII_COLOR",
        "MK8_MII_GENDER",
        "MK8_MII_FACE",
        "NX_LAN_RELAY",
        "NX_LAN_ROOM",
        "NX_LAN_SLOT",
        "LARECOMP_LAN_RELAY",
        "LARECOMP_LAN_ROOM",
        "LARECOMP_LAN_SLOT",
    ] {
        cmd.env_remove(env);
    }
    // Name, colour and face only (CharInfo and the in-game name editor); MK8_EXPERIMENTAL_NO_MII keeps the
    // Mii renderer off, so no graphics consumer is re-enabled.
    mii::configure(&mut cmd, local)?;
    if let Some((addr, slot)) = network {
        cmd.env("LARECOMP_LAN_RELAY", addr)
            .env("LARECOMP_LAN_ROOM", "MK8ONLINE")
            .env("LARECOMP_LAN_SLOT", slot.to_string());
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // The independent supervisor has no console for the runtime to inherit.
        cmd.creation_flags(0x08000000);
    }
    cmd.spawn().map_err(|e| format!("Falha ao abrir jogo: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn game_log_rotation_keeps_a_bounded_history() {
        let root = std::env::temp_dir().join(format!("mk8-logs-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        rotate_game_log(&root).unwrap(); // nothing to rotate yet
        fs::write(root.join("game.log"), b"").unwrap();
        rotate_game_log(&root).unwrap(); // an empty log is not history
        assert!(!root.join("logs").exists());
        for i in 0..GAME_LOG_HISTORY + 3 {
            fs::write(root.join("game.log"), format!("session {i}")).unwrap();
            rotate_game_log(&root).unwrap();
            assert!(!root.join("game.log").exists());
        }
        let kept: Vec<String> = fs::read_dir(root.join("logs"))
            .unwrap()
            .map(|e| fs::read_to_string(e.unwrap().path()).unwrap())
            .collect();
        assert_eq!(kept.len(), GAME_LOG_HISTORY);
        assert!(kept.contains(&format!("session {}", GAME_LOG_HISTORY + 2)));
        assert!(!kept.contains(&"session 0".to_string()));
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn experimental_build_never_passes_qualified_gate_and_checks_hashes() {
        let root = std::env::temp_dir().join(format!("mk8-candidate-{}", std::process::id()));
        fs::create_dir_all(root.join("extracted/romfs")).unwrap();
        let path = root.join("fixture");
        fs::write(&path, b"authored test fixture").unwrap();
        let file = mk8_manifest::hash_file(&path).unwrap();
        let mut install = Installation {
            schema: 2,
            identity: "fixture".into(),
            profile: "mk8d-smm2-experimental".into(),
            executable: file.clone(),
            files: ["npdm", "main", "sdk", "subsdk0", "gamelib"]
                .into_iter()
                .map(|key| (key.into(), file.clone()))
                .collect(),
            data_root: root.clone(),
            shader_root: root.clone(),
            gameplay_verified: false,
        };
        assert!(validate_candidate(&install).is_ok());
        assert!(validate_installation(&install).is_err());
        install.gameplay_verified = true;
        assert!(validate_candidate(&install).is_err());
        install.gameplay_verified = false;
        fs::write(&path, b"corrupted").unwrap();
        assert!(validate_candidate(&install).is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn npdm_rejects_wrong_title_and_distinguishes_architectures() {
        let mut raw = vec![0u8; 0xc0];
        raw[..4].copy_from_slice(b"META");
        raw[0x70..0x74].copy_from_slice(&0x80u32.to_le_bytes());
        raw[0x74..0x78].copy_from_slice(&0x40u32.to_le_bytes());
        raw[0x80..0x84].copy_from_slice(b"ACI0");
        raw[0x90..0x98].copy_from_slice(&0x0100152000022000u64.to_le_bytes());
        assert_eq!(npdm_architecture(&raw).unwrap(), "aarch32");
        raw[0xc] = 1;
        assert_eq!(npdm_architecture(&raw).unwrap(), "aarch64");
        raw[0x90] = 1;
        assert!(npdm_architecture(&raw).is_err());
        raw[0x70..0x74].copy_from_slice(&u32::MAX.to_le_bytes());
        assert!(npdm_architecture(&raw).is_err());
    }
}
