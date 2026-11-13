//! Keeps the game and its resource locks alive after the UI process exits.
use mk8_build_manager::{acquire_game, supervisor};
use mk8_manifest::{Installation, Result};
use serde::{Deserialize, Serialize};
use std::{path::PathBuf, process::Child};

#[derive(Serialize, Deserialize)]
pub struct Request {
    pub root: PathBuf,
    pub local: PathBuf,
    pub install: Installation,
    pub network: Option<(String, u8)>,
    pub username: String,
    pub candidate: bool,
}

pub fn spawn(request: &Request) -> Result<Child> {
    supervisor::spawn_request("--game-supervisor", request)
}

pub fn run() -> Result<i32> {
    let request: Request = supervisor::read_request()?;
    let _guards = acquire_game(&request.root, &request.local)?;
    let signal = request.local.join("open-rooms");
    if signal.exists() {
        std::fs::remove_file(signal).map_err(|e| e.to_string())?;
    }
    let launch = if request.candidate {
        mk8_installer::launch_candidate
    } else {
        mk8_installer::launch
    };
    let mut ready = false;
    run_game(|| {
        let game = launch(
            &request.install,
            &request.local,
            request.network.clone(),
            &request.username,
        )?;
        // Once the game exists, a closed UI pipe must not kill it or release its locks.
        if !std::mem::replace(&mut ready, true) {
            let _ = supervisor::ready();
        }
        Ok(game)
    })
}

/// The runtime's exit code for the in-game "Restart the game" (toolchain/host.py `RESTART_EXIT`): MK8 does
/// not survive the upstream reboot inside one process, so it exits and is started again here, with the same
/// locks and network settings.
const RESTART_EXIT: i32 = 0x4D4B;

fn run_game(mut launch: impl FnMut() -> Result<Child>) -> Result<i32> {
    loop {
        let exit = launch()?.wait().map_err(|e| e.to_string())?;
        if exit.code() != Some(RESTART_EXIT) {
            return Ok(exit.code().unwrap_or(1));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;

    #[test]
    fn a_restart_exit_starts_the_game_again() {
        let mut codes = vec![3, RESTART_EXIT, RESTART_EXIT].into_iter().rev();
        let mut starts = 0;
        let code = run_game(|| {
            starts += 1;
            let code = codes.next().unwrap();
            Command::new("cmd")
                .args(["/C", &format!("exit {code}")])
                .spawn()
                .map_err(|e| e.to_string())
        })
        .unwrap();
        assert_eq!((code, starts), (3, 3));
    }
}
