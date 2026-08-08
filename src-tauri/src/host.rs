use std::io;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, Manager};

use crate::supervisor::{ExitDecision, RestartPolicy};

#[derive(Debug, Clone)]
pub struct CoreSupervisor {
    stopping: Arc<AtomicBool>,
}

impl CoreSupervisor {
    pub fn start(app: AppHandle, desktop_session_token: String) -> Self {
        let stopping = Arc::new(AtomicBool::new(false));
        let worker_flag = Arc::clone(&stopping);
        thread::Builder::new()
            .name("jarvis-core-supervisor".into())
            .spawn(move || supervise_core(&app, &worker_flag, &desktop_session_token))
            .expect("failed to start JARVIS core supervisor");
        Self { stopping }
    }

    pub fn stop(&self) {
        self.stopping.store(true, Ordering::Release);
    }
}

fn supervise_core(app: &AppHandle, stopping: &AtomicBool, desktop_session_token: &str) {
    let started = Instant::now();
    let mut policy = RestartPolicy::default();
    loop {
        if stopping.load(Ordering::Acquire) {
            return;
        }
        let mut child = match spawn_core(app, desktop_session_token) {
            Ok(child) => child,
            Err(error) => {
                tracing::error!(%error, "failed to start Python core");
                if !wait_for_restart(&mut policy, started, stopping) {
                    let _ = app.emit("jarvis://core-status", "unavailable");
                    return;
                }
                continue;
            }
        };
        let _ = app.emit("jarvis://core-status", "starting");
        loop {
            if stopping.load(Ordering::Acquire) {
                terminate_child(&mut child);
                return;
            }
            match child.try_wait() {
                Ok(Some(status)) => {
                    let clean_exit = status.success();
                    let decision = policy.decide(
                        elapsed_millis(started),
                        clean_exit,
                        stopping.load(Ordering::Acquire),
                    );
                    match decision {
                        ExitDecision::Stop => {
                            let _ = app.emit("jarvis://core-status", "unavailable");
                            return;
                        }
                        ExitDecision::RestartAfter(delay) => {
                            let _ = app.emit("jarvis://core-status", "restarting");
                            if !interruptible_wait(delay, stopping) {
                                return;
                            }
                            break;
                        }
                    }
                }
                Ok(None) => thread::sleep(Duration::from_millis(100)),
                Err(error) => {
                    tracing::error!(%error, "failed to observe Python core");
                    terminate_child(&mut child);
                    if !wait_for_restart(&mut policy, started, stopping) {
                        return;
                    }
                    break;
                }
            }
        }
    }
}

fn spawn_core(app: &AppHandle, desktop_session_token: &str) -> io::Result<Child> {
    let mut command = if cfg!(debug_assertions) {
        let mut command = Command::new("uv");
        let workspace = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("Tauri directory must have a workspace parent")
            .to_path_buf();
        command.current_dir(workspace).args(["run", "jarvis-core"]);
        command
    } else {
        let resource_directory = app.path().resource_dir().map_err(io::Error::other)?;
        Command::new(
            resource_directory
                .join("jarvis-core")
                .join("jarvis-core.exe"),
        )
    };
    command
        .env("JARVIS_DESKTOP_SESSION_TOKEN", desktop_session_token)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    command.spawn()
}

fn wait_for_restart(policy: &mut RestartPolicy, started: Instant, stopping: &AtomicBool) -> bool {
    match policy.decide(
        elapsed_millis(started),
        false,
        stopping.load(Ordering::Acquire),
    ) {
        ExitDecision::Stop => false,
        ExitDecision::RestartAfter(delay) => interruptible_wait(delay, stopping),
    }
}

fn interruptible_wait(delay: Duration, stopping: &AtomicBool) -> bool {
    let deadline = Instant::now() + delay;
    while Instant::now() < deadline {
        if stopping.load(Ordering::Acquire) {
            return false;
        }
        thread::sleep(
            Duration::from_millis(50).min(deadline.saturating_duration_since(Instant::now())),
        );
    }
    !stopping.load(Ordering::Acquire)
}

fn terminate_child(child: &mut Child) {
    if let Err(error) = child.kill() {
        tracing::warn!(%error, "failed to stop Python core");
    }
    let _ = child.wait();
}

fn elapsed_millis(started: Instant) -> u64 {
    u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX)
}
