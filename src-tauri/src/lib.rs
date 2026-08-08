//! Thin Windows host for JARVIS. Product behavior lives in the Python core.

mod host;
mod supervisor;

use host::CoreSupervisor;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Emitter, Manager, PhysicalPosition, Position, RunEvent, WindowEvent};
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_global_shortcut::ShortcutState;
use uuid::Uuid;

#[derive(Debug, Clone)]
struct DesktopSession {
    token: String,
}

impl DesktopSession {
    fn new() -> Self {
        Self {
            token: format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple()),
        }
    }

    fn token(&self) -> &str {
        &self.token
    }
}

#[tauri::command]
#[allow(clippy::needless_pass_by_value)] // Tauri command extraction requires owned State.
fn desktop_session_token(session: tauri::State<'_, DesktopSession>) -> String {
    session.token().to_owned()
}

/// Starts the native JARVIS host.
///
/// # Panics
///
/// Panics when immutable application configuration is invalid or the native host cannot start.
pub fn run() {
    let global_shortcut = tauri_plugin_global_shortcut::Builder::new()
        .with_shortcut("Ctrl+Shift+Space")
        .expect("JARVIS fallback shortcut is invalid")
        .with_handler(|app, _shortcut, event| {
            if event.state() == ShortcutState::Pressed {
                let _ = show_overlay(app);
                let _ = app.emit("jarvis://activate", "shortcut");
            }
        })
        .build();
    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![desktop_session_token])
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            let _ = show_overlay(app);
            let _ = app.emit("jarvis://activate", "shortcut");
        }))
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(global_shortcut)
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            let desktop_session = DesktopSession::new();
            app.autolaunch().enable()?;
            let overlay = app
                .get_webview_window("overlay")
                .ok_or("JARVIS overlay window was not created")?;
            overlay.set_content_protected(true)?;
            position_overlay(&overlay)?;

            let show = MenuItem::with_id(app, "show", "Show JARVIS", true, None::<&str>)?;
            let hide = MenuItem::with_id(app, "hide", "Hide JARVIS", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit JARVIS", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &hide, &quit])?;
            let mut tray = TrayIconBuilder::with_id("jarvis")
                .tooltip("JARVIS")
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        let _ = show_overlay(app);
                    }
                    "hide" => {
                        if let Some(window) = app.get_webview_window("overlay") {
                            let _ = window.hide();
                        }
                    }
                    "quit" => {
                        app.state::<CoreSupervisor>().stop();
                        app.exit(0);
                    }
                    _ => {}
                });
            if let Some(icon) = app.default_window_icon().cloned() {
                tray = tray.icon(icon);
            }
            tray.build(app)?;
            let supervisor =
                CoreSupervisor::start(app.handle().clone(), desktop_session.token().to_owned());
            app.manage(desktop_session);
            app.manage(supervisor);
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("JARVIS native host failed to build");

    app.run(|app, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            app.state::<CoreSupervisor>().stop();
        }
    });
}

fn show_overlay(app: &tauri::AppHandle) -> tauri::Result<()> {
    if let Some(window) = app.get_webview_window("overlay") {
        position_overlay(&window)?;
        window.show()?;
        window.set_focus()?;
    }
    Ok(())
}

fn position_overlay(window: &tauri::WebviewWindow) -> tauri::Result<()> {
    let Some(monitor) = window.current_monitor()? else {
        return Ok(());
    };
    let monitor_position = monitor.position();
    let monitor_size = monitor.size();
    let window_size = window.outer_size()?;
    let horizontal_space = monitor_size.width.saturating_sub(window_size.width);
    let x_offset = i32::try_from(horizontal_space / 2).unwrap_or(i32::MAX);
    let vertical_space = monitor_size.height.saturating_sub(window_size.height);
    let y_offset = i32::try_from(vertical_space.saturating_sub(48)).unwrap_or(i32::MAX);
    window.set_position(Position::Physical(PhysicalPosition::new(
        monitor_position.x.saturating_add(x_offset),
        monitor_position.y.saturating_add(y_offset),
    )))
}

#[cfg(test)]
mod tests {
    use super::DesktopSession;

    #[test]
    fn desktop_session_tokens_are_high_entropy_and_unique() {
        let first = DesktopSession::new();
        let second = DesktopSession::new();

        assert!(first.token().len() >= 64);
        assert_ne!(first.token(), second.token());
        assert!(
            first
                .token()
                .chars()
                .all(|character| character.is_ascii_hexdigit())
        );
    }
}
