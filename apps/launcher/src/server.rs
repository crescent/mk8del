use mk8_build_manager::Manager;
use mk8_manifest::{load, save, Config, Installation, Result};
use mk8_session::{discover, Gateway, Host, Invite, DISCOVERY_PORT};
use serde_json::{json, Value};
use std::{
    fs,
    io::{Read, Seek, SeekFrom},
    net::SocketAddr,
    path::{Path, PathBuf},
    process::Child,
    sync::{Arc, OnceLock},
};
use tiny_http::{Header, Method, Request, Response, Server};

use crate::{
    console,
    relay::{self, InternetRoom},
    report, setup, update,
};

/// Window used as parent of native file dialogs; empty in headless mode.
pub type DialogParent = Arc<OnceLock<tauri::WebviewWindow>>;

pub struct App {
    root: PathBuf,
    local: PathBuf,
    config: Config,
    build: Manager,
    setup: setup::Setup,
    tools: Vec<Value>,
    host: Option<Host>,
    internet_room: Option<InternetRoom>,
    gateway: Option<Gateway>,
    game: Option<Child>,
    game_exit: Option<String>,
    username: String,
    prepare_after_setup: bool,
    room_menu: u64,
    window: Option<DialogParent>,
    /// Set by a started update: the server exits after answering, so the installer can replace the launcher.
    exit_requested: bool,
    last_report: Option<String>,
    /// Why `--play` opened the window instead of only the game (shown once the page loads).
    pub startup_error: Option<String>,
}
/// The console keys beside the base package (or in its `keys` folder) or in `%USERPROFILE%\.switch`.
fn detect_keys(config: &Config) -> Option<PathBuf> {
    let base = Path::new(&config.base_package);
    [
        base.parent().map(|p| p.join("prod.keys")),
        base.parent().map(|p| p.join("keys/prod.keys")),
        std::env::var_os("USERPROFILE")
            .map(PathBuf::from)
            .map(|p| p.join(".switch/prod.keys")),
    ]
    .into_iter()
    .flatten()
    .find(|p| p.is_file())
}

/// The three files of step 2 of the install screen, as the page shows them.
fn game_files(config: &Config, imported: bool) -> Value {
    let file = |path: &str| !path.is_empty() && Path::new(path).is_file();
    let keys = if config.prod_keys.is_empty() {
        detect_keys(config).map(|p| p.to_string_lossy().into_owned())
    } else {
        Some(config.prod_keys.clone())
    };
    let keys_ok = keys.as_deref().is_some_and(file);
    let packages = file(&config.base_package)
        && file(&config.update_package)
        && config.base_package != config.update_package;
    json!({
        "base_ok": file(&config.base_package),
        "update_ok": file(&config.update_package),
        "keys": keys,
        "keys_ok": keys_ok,
        "keys_detected": config.prod_keys.is_empty() && keys_ok,
        "folder": config.base_package.is_empty() && config.update_package.is_empty() && !config.exefs.is_empty(),
        "imported": imported,
        "ready": imported || (packages && keys_ok),
    })
}

fn valid_username(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 32
        && name.chars().count() <= 24
        && !name.chars().any(char::is_control)
}

impl App {
    pub fn new(
        root: PathBuf,
        local: PathBuf,
        config: Config,
        window: Option<DialogParent>,
    ) -> Self {
        let tools = setup::tool_checks(&root);
        let username = load(&local.join("username.json"))
            .ok()
            .filter(|name: &String| valid_username(name))
            .unwrap_or_else(|| {
                if crate::language::read(&local) == "pt-BR" {
                    "Jogador".into()
                } else {
                    "Player".into()
                }
            });
        Self {
            root,
            local,
            config,
            build: Manager::default(),
            setup: setup::Setup::default(),
            tools,
            host: None,
            internet_room: None,
            gateway: None,
            game: None,
            game_exit: None,
            username,
            prepare_after_setup: false,
            room_menu: 0,
            window,
            exit_requested: false,
            last_report: None,
            startup_error: None,
        }
    }

    /// The Play button's solo path: the installed game, else the prepared candidate.
    pub fn play_solo(&mut self) -> Result<u32> {
        self.status();
        if self.game.is_some() {
            return Err("O jogo já está aberto.".into());
        }
        if self.setup.running() || self.build.poll().state == "running" {
            return Err("Aguarde a preparação.".into());
        }
        self.start_game(None)
    }

    fn start_game(&mut self, network: Option<(String, u8)>) -> Result<u32> {
        let install = self.installation();
        console::ensure_hidden();
        let candidate = install.is_none();
        let install = match install {
            Some(install) => install,
            None => load(&self.local.join("candidate.json"))
                .map_err(|_| "Nenhuma build pronta. Prepare o jogo no Launcher.".to_string())?,
        };
        let child = crate::game_session::spawn(&crate::game_session::Request {
            root: self.root.clone(),
            local: self.local.clone(),
            install,
            network,
            username: self.username.clone(),
            candidate,
        })?;
        let pid = child.id();
        self.game_exit = None;
        self.game = Some(child);
        Ok(pid)
    }

    /// Waits for the game started by `play_solo`; true when it closed normally. A crash is recorded (and its
    /// report written) as for a game started from the window.
    pub fn wait_game(&mut self) -> bool {
        let Some(game) = self.game.as_mut() else {
            return false;
        };
        let closed = game.wait().is_ok_and(|exit| exit.success());
        self.status();
        closed
    }

    fn store_config(&mut self, config: Config) -> Result<()> {
        if self.prepare_after_setup || self.build.poll().state == "running" {
            return Err("Aguarde a preparação para alterar os caminhos.".into());
        }
        let _guards = mk8_build_manager::acquire_operation(&self.root, &self.local)?;
        save(&self.local.join("config.json"), &config)?;
        self.config = config;
        Ok(())
    }

    fn start_build(&mut self) -> Result<()> {
        {
            let _guards = mk8_build_manager::acquire_operation(&self.root, &self.local)?;
            save(&self.local.join("config.json"), &self.config)?;
        }
        self.build
            .start(&self.root, &self.local.join("config.json"), &self.local)
    }

    /// The game's settings and saves change only while it is closed (the runtime rewrites them from memory).
    fn game_closed(&mut self) -> Result<Vec<fs::File>> {
        self.status();
        if self.game.is_some() {
            return Err("Feche o jogo antes de alterar configurações ou saves.".into());
        }
        mk8_build_manager::acquire_operation(&self.root, &self.local)
    }

    fn log_tail(&self, name: &str) -> Result<String> {
        let mut text = String::new();
        if let Ok(mut file) = fs::File::open(self.local.join(name)) {
            let size = file.metadata().map_err(|e| e.to_string())?.len();
            file.seek(SeekFrom::Start(size.saturating_sub(32000)))
                .map_err(|e| e.to_string())?;
            let mut bytes = Vec::new();
            file.take(32000)
                .read_to_end(&mut bytes)
                .map_err(|e| e.to_string())?;
            text = String::from_utf8_lossy(&bytes).into_owned();
        }
        Ok(text)
    }

    fn installation(&self) -> Option<Installation> {
        load::<Installation>(&self.local.join("active.json"))
            .ok()
            .filter(|install| {
                install.schema == 2
                    && install.profile == "mk8d-smm2-qualified"
                    && install.gameplay_verified
            })
    }
    fn identity(&self) -> String {
        self.installation()
            .or_else(|| self.candidate())
            .and_then(|install| {
                // The build identity contains local paths and differs between PCs.
                let hashes = ["npdm", "main", "sdk", "subsdk0"]
                    .iter()
                    .map(|key| install.files.get(*key).map(|file| file.sha256.as_str()))
                    .collect::<Option<Vec<_>>>()?;
                Some(format!("mk8-lan-v1:{}", hashes.join(":")))
            })
            .unwrap_or_else(|| "transport-fixture-v1".into())
    }
    fn candidate(&self) -> Option<Installation> {
        load::<Installation>(&self.local.join("candidate.json"))
            .ok()
            .filter(|install| {
                install.schema == 2
                    && install.profile == "mk8d-smm2-experimental"
                    && !install.gameplay_verified
            })
    }
    fn status(&mut self) -> Value {
        if let Some(exit) = self.game.as_mut().and_then(|c| c.try_wait().ok().flatten()) {
            self.game_exit = Some(exit.to_string());
            self.game = None;
            if !exit.success() {
                // A crash leaves its report ready to send, before the next launch rotates the log.
                let summary =
                    json!({"game_exit": exit.to_string(), "build": self.build.poll().state});
                self.last_report = report::create(&self.local, "game-exit", summary)
                    .ok()
                    .map(|p| p.display().to_string());
            }
        }
        let signal = self.local.join("open-rooms");
        if self.game.is_some() && fs::remove_file(signal).is_ok() {
            self.room_menu += 1;
            if let Some(window) = self.window.as_ref().and_then(|parent| parent.get()) {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }
        if self.setup.poll() {
            self.tools = setup::tool_checks(&self.root);
            if std::mem::take(&mut self.prepare_after_setup)
                && self.setup.status()["state"] == "done"
            {
                let started = self.start_build();
                self.build.record_start(started);
            }
        }
        // A build records the launcher version that made it: the runtime fixes come with each version, so a game built
        // by an earlier one (an update kept the data folder) is offered for a rebuild, which reuses the finished stages.
        let build = self.build.poll();
        let built_with = self.local.join("built-with.json");
        if build.state == "candidate"
            && load::<Value>(&built_with)
                .ok()
                .and_then(|v| v["version"].as_str().map(str::to_owned))
                != Some(update::CURRENT.to_owned())
        {
            let _ = save(&built_with, &json!({ "version": update::CURRENT }));
        }
        let candidate_exists = self.local.join("candidate.json").is_file();
        let candidate_version = load::<Value>(&built_with)
            .ok()
            .and_then(|v| v["version"].as_str().map(str::to_owned));
        let candidate_stale =
            candidate_exists && candidate_version.as_deref() != Some(update::CURRENT);
        let install = self.installation();
        let package_import =
            !self.config.base_package.is_empty() || !self.config.update_package.is_empty();
        let imported = self.local.join("imports/mk8-4.0.1");
        let mut paths = self.config.clone();
        if package_import && imported.is_dir() {
            paths.exefs = imported.join("exefs").to_string_lossy().into_owned();
            paths.romfs = imported.join("romfs").to_string_lossy().into_owned();
            paths.nacp = imported
                .join("control/control.nacp")
                .to_string_lossy()
                .into_owned();
        }
        json!({"package_import":package_import,"import_ready":package_import && imported.is_dir(),"room_menu":self.room_menu,"setup":self.setup.status(),"tools":self.tools,"tools_ready":self.tools.iter().all(|c|c["ok"]==true),"native_dialogs":self.window.is_some(),"build":build,"candidate":candidate_exists,"candidate_stale":candidate_stale,"candidate_version":candidate_version,"game_exit":self.game_exit,"installed":install.is_some(),"profile":install.as_ref().map(|i|&i.profile),"gameplay_verified":install.as_ref().is_some_and(|i|i.gameplay_verified),"game_pid":self.game.as_ref().map(|c|c.id()),"session":{"hosting":self.host.is_some(),"internet_available":relay::url().is_some(),"room_code":self.internet_room.as_ref().map(|room|&room.code),"relay":self.host.as_ref().map(|h|h.address.to_string()),"participants":self.host.as_ref().map(|h|h.connected()).unwrap_or(0),"connected":self.gateway.as_ref().is_some_and(|g|g.connected()),"slot":self.gateway.as_ref().map(|g|g.slot),"bridge_port":self.gateway.as_ref().map(|g|g.local_addr.port()),"diagnostic_only":install.is_none() && self.candidate().is_none()},"checks":mk8_installer::inspect(&paths),"files":game_files(&self.config, package_import && imported.is_dir()),"upstream":"Runtime local","root":self.root,"version":update::CURRENT,"updates":{"configured":update::repository().is_some(),"installable":update::installable()},"last_report":self.last_report,"startup_error":self.startup_error,"data_dir":self.local,"windowed":self.window.is_some()})
    }
    fn handle(&mut self, method: &Method, path: &str, body: &str) -> Result<Value> {
        match (method.as_str(), path) {
            ("GET", "/api/status") => Ok(self.status()),
            ("GET", "/api/config") => Ok(serde_json::to_value(&self.config).unwrap()),
            ("GET", "/api/username") => Ok(json!(self.username)),
            ("POST", "/api/language") => {
                let value: Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
                Ok(json!(crate::language::write(&self.local, &value)?))
            }
            ("POST", "/api/username") => {
                let value: Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
                let name = value["name"].as_str().unwrap_or("").trim();
                if !valid_username(name) {
                    return Err("Informe um nome de até 24 caracteres e 32 bytes.".into());
                }
                if self.host.is_some() {
                    return Err("Encerre sua sala antes de alterar o nome.".into());
                }
                save(&self.local.join("username.json"), &name)?;
                self.username = name.into();
                Ok(json!({"message":"Nome salvo neste computador."}))
            }
            ("GET", "/api/profile") => {
                let mii = mk8_installer::mii::read(&self.local)?;
                Ok(json!({"name":self.username,"mii":mii.map(|p| p.appearance)}))
            }
            ("POST", "/api/profile") => {
                let value: Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
                let appearance: mk8_installer::mii::Appearance =
                    serde_json::from_value(value["mii"].clone()).map_err(|e| e.to_string())?;
                let _guards = self.game_closed()?;
                mk8_installer::mii::store(&self.local, appearance)?;
                Ok(json!({"message":"Perfil Mii salvo. Vale na próxima vez que o jogo abrir."}))
            }
            ("GET", "/api/settings") => Ok(json!({
                "controls": crate::game_settings::controls(&self.local),
                "video": crate::game_settings::video(&self.local),
            })),
            ("POST", "/api/settings") => {
                let value: Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
                let changes = value["changes"].as_object().ok_or("Nada para salvar.")?;
                let _guards = self.game_closed()?;
                crate::game_settings::write(&self.local, changes)?;
                Ok(json!({"message":"Configuração salva."}))
            }
            ("POST", "/api/settings/reset-controls") => {
                let _guards = self.game_closed()?;
                crate::game_settings::reset_controls(&self.local)?;
                Ok(json!({"message":"Controles restaurados para o padrão do MK8."}))
            }
            ("POST", "/api/saves/import") => {
                let value: Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
                let path = value["path"]
                    .as_str()
                    .filter(|p| !p.is_empty())
                    .ok_or("Escolha a pasta do save.")?;
                let _guards = self.game_closed()?;
                crate::game_settings::import_save(&self.local, Path::new(path))
            }
            ("POST", "/api/game-folder") => {
                let value: Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
                let path = value["path"]
                    .as_str()
                    .filter(|p| !p.is_empty())
                    .ok_or("Escolha uma pasta")?;
                let root = crate::canonical(Path::new(path)).map_err(|e| e.to_string())?;
                let exefs = root.join("exefs");
                let romfs = root.join("romfs");
                let nacp = [root.join("control.nacp"), root.join("control/control.nacp")]
                    .into_iter()
                    .find(|p| p.is_file())
                    .ok_or("control.nacp ausente na pasta selecionada.")?;
                if !exefs.join("main.npdm").is_file() || !romfs.join("Course").is_dir() {
                    return Err("A pasta deve conter exefs/main.npdm e romfs/Course.".into());
                }
                let mut config = self.config.clone();
                config.exefs = exefs.to_string_lossy().into_owned();
                config.romfs = romfs.to_string_lossy().into_owned();
                config.nacp = nacp.to_string_lossy().into_owned();
                config.base_package.clear();
                config.update_package.clear();
                self.store_config(config)?;
                Ok(json!({"config":self.config,"message":"Pasta do jogo salva."}))
            }
            ("POST", "/api/config") => {
                self.store_config(serde_json::from_str(body).map_err(|e| e.to_string())?)?;
                Ok(json!({"message":"Caminhos salvos."}))
            }
            ("POST", "/api/prepare") => {
                self.status();
                if self.game.is_some()
                    || self.setup.running()
                    || self.build.poll().state == "running"
                {
                    return Err("Feche o jogo ou aguarde a operação atual.".into());
                }
                if self.config.base_package.is_empty() && self.config.update_package.is_empty() {
                    let check = mk8_installer::architecture_check(&self.config);
                    if !check.ok {
                        return Err(check.detail);
                    }
                } else if !self.local.join("imports/mk8-4.0.1").is_dir() {
                    let base = Path::new(&self.config.base_package);
                    let patch = Path::new(&self.config.update_package);
                    if !base.is_file() || !patch.is_file() || base == patch {
                        return Err(
                            "Selecione dois arquivos: jogo base e atualização 4.0.1.".into()
                        );
                    }
                    if self.config.prod_keys.is_empty() {
                        if let Some(keys) = detect_keys(&self.config) {
                            self.config.prod_keys = keys.to_string_lossy().into_owned();
                        }
                    }
                    if !Path::new(&self.config.prod_keys).is_file() {
                        return Err("prod.keys não encontrado. Selecione as chaves do seu console no campo da importação.".into());
                    }
                }
                self.tools = setup::tool_checks(&self.root);
                if self.tools.iter().all(|tool| tool["ok"] == true) {
                    self.start_build()?;
                    Ok(json!({"message":"Preparando o jogo."}))
                } else {
                    self.setup.start_guarded(&self.root, &self.local)?;
                    self.prepare_after_setup = true;
                    Ok(json!({"message":"Instalando ferramentas. A preparação começa em seguida."}))
                }
            }
            ("POST", "/api/setup") => {
                self.status();
                if self.game.is_some() {
                    return Err("Feche o jogo antes de instalar ferramentas.".into());
                }
                if self.build.poll().state == "running" {
                    return Err(
                        "Aguarde ou cancele a preparação antes de instalar ferramentas.".into(),
                    );
                }
                self.setup.start_guarded(&self.root, &self.local)?;
                Ok(json!({"message":"Instalação de ferramentas iniciada. Acompanhe o progresso."}))
            }
            ("POST", "/api/setup/cancel") => {
                self.setup.cancel()?;
                self.prepare_after_setup = false;
                self.tools = setup::tool_checks(&self.root);
                Ok(json!({"message":"Instalação de ferramentas cancelada."}))
            }
            ("POST", "/api/setup/check") => {
                if self.setup.running() {
                    return Err("Aguarde a instalação de ferramentas terminar.".into());
                }
                self.tools = setup::tool_checks(&self.root);
                let missing = self.tools.iter().filter(|c| c["ok"] != true).count();
                Ok(json!({"message": if missing == 0 {
                    "Ferramentas encontradas.".to_string()
                } else {
                    format!("{missing} item(ns) de ferramentas ausente(s) ou incompleto(s).")
                }}))
            }
            ("GET", "/api/setup/log") => Ok(json!({"text":self.log_tail("setup.log")?})),
            ("POST", "/api/build") => {
                if self.game.is_some() {
                    return Err("Feche o jogo antes de preparar uma instalação.".into());
                }
                if self.setup.running() {
                    return Err("Aguarde a instalação de ferramentas terminar.".into());
                }
                self.start_build()?;
                Ok(json!({"message":"Preparação iniciada. Acompanhe o diagnóstico."}))
            }
            ("POST", "/api/build/cancel") => {
                console::ensure_hidden();
                self.build.cancel()?;
                Ok(json!({"message":"Preparação cancelada."}))
            }
            ("GET", "/api/log") | ("GET", "/api/game/log") => {
                let text = self.log_tail(if path == "/api/game/log" {
                    "game.log"
                } else {
                    "build.log"
                })?;
                Ok(json!({"text":text}))
            }
            ("POST", "/api/session/discover") => Ok(json!(discover(&self.identity())?)),
            ("POST", "/api/session/host") => {
                if self.gateway.is_some() || self.host.is_some() || self.game.is_some() {
                    return Err("Encerre a sessão atual antes de criar outra.".into());
                }
                let value: Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
                let explicit = value["bind"].as_str();
                let default = format!("0.0.0.0:{DISCOVERY_PORT}");
                let bind = explicit
                    .unwrap_or(&default)
                    .parse::<SocketAddr>()
                    .map_err(|e| e.to_string())?;
                let host =
                    Host::start_named(bind, &self.identity(), &self.username, explicit.is_none())?;
                let local = SocketAddr::new(
                    if !host.address.ip().is_unspecified() {
                        host.address.ip()
                    } else if host.address.is_ipv4() {
                        "127.0.0.1".parse().unwrap()
                    } else {
                        "::1".parse().unwrap()
                    },
                    host.address.port(),
                );
                let gateway = Gateway::start(host.invite(local)?)?;
                self.gateway = Some(gateway);
                self.host = Some(host);
                Ok(
                    json!({"message":"Sala de transporte criada. Isso ainda não confirma uma corrida."}),
                )
            }
            ("POST", "/api/session/host-internet") => {
                if self.gateway.is_some()
                    || self.host.is_some()
                    || self.internet_room.is_some()
                    || self.game.is_some()
                {
                    return Err("Encerre o jogo e a sessão atual antes de criar uma sala.".into());
                }
                let (room, invite) = InternetRoom::create(&self.identity())?;
                let gateway = match Gateway::start(invite) {
                    Ok(gateway) => gateway,
                    Err(error) => {
                        let _ = room.close();
                        return Err(error);
                    }
                };
                let code = room.code.clone();
                self.gateway = Some(gateway);
                self.internet_room = Some(room);
                Ok(
                    json!({"code":code,"message":"Sala criada no relay. Aguarde a confirmação da conexão."}),
                )
            }
            ("POST", "/api/session/invite") => {
                let value: Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
                let host = self.host.as_ref().ok_or("Crie uma sala primeiro")?;
                let endpoint = match value["endpoint"].as_str().filter(|s| !s.is_empty()) {
                    Some(value) => value.parse::<SocketAddr>().map_err(|e| e.to_string())?,
                    None => host.invite_endpoint().ok_or(
                        "Sem endereço automático. Confira a rede ou use Endereço alternativo.",
                    )?,
                };
                let invite = host.invite(endpoint)?;
                Ok(
                    json!({"invite":invite.encode(),"message":"Convite individual criado. Válido por quatro horas; não publique."}),
                )
            }
            ("POST", "/api/session/join") => {
                if self.gateway.is_some() || self.host.is_some() || self.game.is_some() {
                    return Err("Encerre a sessão atual antes de entrar em outra.".into());
                }
                let value: Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
                let text = value["invite"]
                    .as_str()
                    .ok_or("Convite ou código ausente")?
                    .trim();
                let identity = self.identity();
                let invite = if text.starts_with("MK8-") {
                    InternetRoom::join(text, &identity)?
                } else {
                    Invite::decode(text, &identity)?
                };
                self.gateway = Some(Gateway::start(invite)?);
                Ok(json!({"message":"Conectando ao anfitrião. Aguarde confirmação do transporte."}))
            }
            ("POST", "/api/session/stop") => {
                if self.game.is_some() {
                    return Err("Feche o jogo antes de encerrar seu transporte.".into());
                }
                self.gateway = None;
                self.host = None;
                if let Some(room) = self.internet_room.take() {
                    room.close().map_err(|_| {
                        "Sessão local encerrada. O relay não confirmou o fechamento da sala."
                    })?;
                }
                Ok(json!({"message":"Sessão encerrada."}))
            }
            ("POST", "/api/play") => {
                self.status();
                if self.game.is_some() {
                    return Err("O jogo já está aberto.".into());
                }
                if self.setup.running() || self.build.poll().state == "running" {
                    return Err("Aguarde a preparação.".into());
                }
                let value: Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
                let online = value["online"].as_bool().unwrap_or(false);
                let net = if online {
                    let gateway = self.gateway.as_ref().ok_or("Crie ou entre em uma sala")?;
                    if !gateway.connected() {
                        return Err("Anfitrião ainda não confirmou a conexão".into());
                    }
                    gateway.reset_game();
                    Some((gateway.local_addr.to_string(), gateway.slot))
                } else {
                    None
                };
                let pid = self.start_game(net)?;
                Ok(
                    json!({"message":format!("Processo iniciado ({pid}). Corrida ainda não verificada.")}),
                )
            }
            ("POST", "/api/report") => {
                let summary = self.status();
                let path = report::create(&self.local, "manual", summary)?;
                self.last_report = Some(path.display().to_string());
                Ok(
                    json!({"path":path,"message":"Relatório de logs criado. Envie o arquivo .zip junto com a descrição do problema."}),
                )
            }
            ("POST", "/api/open") => {
                let value: Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
                let folder = match value["what"].as_str() {
                    Some("reports") => report::folder(&self.local),
                    Some("logs") => self.local.join("logs"),
                    Some("data") => self.local.clone(),
                    Some("saves") => crate::game_settings::save_dir(&self.local),
                    _ => return Err("Pasta desconhecida.".into()),
                };
                if self.window.is_none() {
                    return Err("Disponível apenas na janela do Launcher.".into());
                }
                fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
                std::process::Command::new("explorer.exe")
                    .arg(&folder)
                    .spawn()
                    .map_err(|e| e.to_string())?;
                Ok(json!({"message":"Pasta aberta."}))
            }
            ("POST", "/api/update/check") => update::check(),
            ("POST", "/api/update/install") => {
                self.status();
                if self.game.is_some()
                    || self.setup.running()
                    || self.build.poll().state == "running"
                {
                    return Err("Feche o jogo e aguarde a preparação antes de atualizar.".into());
                }
                if self.host.is_some() || self.gateway.is_some() {
                    return Err("Encerre a sessão multijogador antes de atualizar.".into());
                }
                let _guards = mk8_build_manager::acquire_operation(&self.root, &self.local)?;
                let setup = update::download_and_start(&self.local)?;
                self.exit_requested = true;
                Ok(
                    json!({"path":setup,"message":"Instalando a atualização. O Launcher vai reabrir sozinho."}),
                )
            }
            _ => Err("Rota não encontrada".into()),
        }
    }
}

pub(crate) fn response(req: Request, code: u16, text: String, mime: &str) {
    let mut res = Response::from_string(text).with_status_code(code);
    for (key,value) in [("Content-Type",mime),("Cache-Control","no-store"),("X-Content-Type-Options","nosniff"),("Referrer-Policy","no-referrer"),("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")] {res.add_header(Header::from_bytes(key,value).unwrap());}
    let _ = req.respond(res);
}
/// Opens a native folder/file picker on its own thread so the API keeps
/// answering status requests while the dialog is open.
fn browse(req: Request, body: &str, parent: &DialogParent) {
    let value: Value = serde_json::from_str(body).unwrap_or_default();
    let english = value["language"] == "en";
    let current = value["current"].as_str().unwrap_or("").to_string();
    let filter = match value["kind"].as_str() {
        Some("nacp") => Some((
            if english {
                "NACP metadata"
            } else {
                "Metadados NACP"
            },
            &["nacp"][..],
        )),
        Some("package") => Some((
            if english {
                "MK8 packages"
            } else {
                "Pacotes MK8"
            },
            &["nsz", "nsp"][..],
        )),
        Some("keys") => Some((
            if english {
                "Console keys"
            } else {
                "Chaves do console"
            },
            &["keys"][..],
        )),
        Some("save") => Some((
            if english {
                "MK8 save files"
            } else {
                "Arquivos de save do MK8"
            },
            &["dat"][..],
        )),
        _ => None,
    };
    let title = value["title"]
        .as_str()
        .unwrap_or(if english { "Select" } else { "Selecionar" })
        .chars()
        .take(120)
        .collect::<String>();
    let parent = parent.clone();
    std::thread::spawn(move || {
        let mut dialog = rfd::FileDialog::new().set_title(title);
        if let Some(window) = parent.get() {
            dialog = dialog.set_parent(window);
        }
        let start = Path::new(&current);
        let start = if start.is_dir() {
            Some(start)
        } else {
            start.parent().filter(|p| p.is_dir())
        };
        if let Some(dir) = start {
            dialog = dialog.set_directory(dir);
        }
        let picked = if let Some((name, extensions)) = filter {
            dialog
                .add_filter(name, extensions)
                .add_filter(
                    if english {
                        "All files"
                    } else {
                        "Todos os arquivos"
                    },
                    &["*"],
                )
                .pick_file()
        } else {
            dialog.pick_folder()
        };
        let data = json!({"ok":true,"data":{"path":picked.map(|p| p.display().to_string())}});
        response(
            req,
            200,
            data.to_string(),
            "application/json; charset=utf-8",
        );
    });
}

pub fn serve(mut app: App, server: Server, authority: String, url: String, token: String) {
    for mut req in server.incoming_requests() {
        let host = req
            .headers()
            .iter()
            .find(|h| h.field.equiv("Host"))
            .map(|h| h.value.as_str());
        if host != Some(authority.as_str()) {
            response(req, 403, "Host inválido".into(), "text/plain");
            continue;
        }
        let path = req.url().to_string();
        let method = req.method().clone();
        if method == Method::Get && path == "/" {
            response(
                req,
                200,
                include_str!("../web/index.html")
                    .replace("__TOKEN__", &token)
                    .replace("__LANGUAGE__", &crate::language::read(&app.local)),
                "text/html; charset=utf-8",
            );
            continue;
        }
        if method == Method::Get && path == "/i18n.js" {
            response(
                req,
                200,
                include_str!("../web/i18n.js").into(),
                "text/javascript; charset=utf-8",
            );
            continue;
        }
        if method == Method::Get && path == "/en.json" {
            response(
                req,
                200,
                include_str!("../web/en.json").into(),
                "application/json; charset=utf-8",
            );
            continue;
        }
        if method == Method::Get && path == "/app.js" {
            response(
                req,
                200,
                include_str!("../web/app.js").into(),
                "text/javascript; charset=utf-8",
            );
            continue;
        }
        if method == Method::Get && path == "/style.css" {
            response(
                req,
                200,
                include_str!("../web/style.css").into(),
                "text/css; charset=utf-8",
            );
            continue;
        }
        let authenticated = req
            .headers()
            .iter()
            .any(|h| h.field.equiv("X-MK8-Token") && h.value.as_str() == token);
        let origin = req
            .headers()
            .iter()
            .find(|h| h.field.equiv("Origin"))
            .map(|h| h.value.as_str());
        if !authenticated || origin.is_some_and(|value| value != url) {
            response(
                req,
                403,
                "{\"ok\":false,\"error\":\"Sessão local inválida\"}".into(),
                "application/json",
            );
            continue;
        }
        if req.body_length().is_some_and(|n| n > 32768) {
            response(req, 413, "{}".into(), "application/json");
            continue;
        }
        let mut body = String::new();
        if req
            .as_reader()
            .take(32769)
            .read_to_string(&mut body)
            .is_err()
            || body.len() > 32768
        {
            response(req, 400, "{}".into(), "application/json");
            continue;
        }
        if method == Method::Post && path == "/api/browse" {
            match app.window.as_ref() {
                Some(parent) => browse(req, &body, parent),
                None => response(
                    req,
                    400,
                    json!({"ok":false,"error":"Seletor de pastas disponível apenas na janela do Launcher."}).to_string(),
                    "application/json; charset=utf-8",
                ),
            }
            continue;
        }
        let (code, data) = match app.handle(&method, &path, &body) {
            Ok(value) => (200, json!({"ok":true,"data":value})),
            Err(error) => (400, json!({"ok":false,"error":error})),
        };
        response(
            req,
            code,
            data.to_string(),
            "application/json; charset=utf-8",
        );
        if app.exit_requested {
            // (the installer waits for this process; give the answer time to reach the page)
            std::thread::sleep(std::time::Duration::from_millis(500));
            std::process::exit(0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn play_without_a_build_explains_and_starts_nothing() {
        let root = std::env::temp_dir().join(format!("mk8-play-empty-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let mut app = App::new(root.clone(), root.clone(), Config::default(), None);
        let error = app.play_solo().unwrap_err();
        assert_eq!(error, "Nenhuma build pronta. Prepare o jogo no Launcher.");
        assert!(app.game.is_none());
        assert!(!app.wait_game());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn candidate_network_identity_uses_content_and_launch_still_checks_hashes() {
        let root =
            std::env::temp_dir().join(format!("mk8-network-manifest-{}", std::process::id()));
        fs::create_dir_all(root.join("extracted/romfs")).unwrap();
        let path = root.join("fixture");
        fs::write(&path, b"authored fixture").unwrap();
        let file = mk8_manifest::hash_file(&path).unwrap();
        let mut install = Installation {
            schema: 2,
            identity: "machine-specific-build".into(),
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
        save(&root.join("candidate.json"), &install).unwrap();
        let mut app = App::new(root.clone(), root.clone(), Config::default(), None);
        let identity = app.identity();
        assert!(identity.starts_with("mk8-lan-v1:"));
        assert!(app.installation().is_none());
        assert_eq!(app.status()["session"]["diagnostic_only"], false);
        install.identity = "other-machine".into();
        install.files.get_mut("main").unwrap().path = root.join("different-path");
        save(&root.join("candidate.json"), &install).unwrap();
        assert_eq!(app.identity(), identity);
        install.files.get_mut("main").unwrap().sha256 = "changed".into();
        save(&root.join("candidate.json"), &install).unwrap();
        assert_ne!(app.identity(), identity);
        assert!(mk8_installer::launch_candidate(
            &install,
            &root,
            Some(("127.0.0.1:47820".into(), 0)),
            "Test"
        )
        .is_err());
        drop(app);
        fs::remove_dir_all(root).unwrap();
    }
}
