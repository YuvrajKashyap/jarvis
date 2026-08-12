//! Thin Windows host for JARVIS. Product behavior lives in the Python core.

mod host;
mod placement;
mod supervisor;

use std::sync::atomic::{AtomicBool, Ordering};

use host::CoreSupervisor;
use placement::{
    OverlayAnchor, OverlayGeometry, OverlayMotion, OverlayScreen, PlacementIntent, ScreenRect,
    anchored_resize_geometry, geometry_is_inside_any_screen, geometry_is_inside_screen,
    monitor_index_at, overlay_geometry_at_anchor, place_overlay_for_attention, rounded_i32,
    rounded_u32, select_attention_anchor,
};
use serde::Serialize;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Emitter, Manager, Monitor, PhysicalPosition, Position, RunEvent, WindowEvent};
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_global_shortcut::ShortcutState;
use tauri_plugin_notification::NotificationExt;
use uuid::Uuid;

#[derive(Debug, Clone)]
struct DesktopSession {
    token: String,
}

#[derive(Debug, Default)]
struct OverlayPlacement {
    manually_positioned: AtomicBool,
}

#[derive(Debug, Serialize)]
struct PlacementPoint {
    x: f64,
    y: f64,
}

#[derive(Debug, Serialize)]
struct PlacementRect {
    left: i32,
    top: i32,
    width: u32,
    height: u32,
}

#[derive(Debug, Serialize)]
struct PlacementMonitor {
    name: String,
    bounds: PlacementRect,
    work_area: PlacementRect,
}

#[derive(Debug, Serialize)]
struct PlacementOverlay {
    left: i32,
    top: i32,
    width: u32,
    height: u32,
    visible: bool,
}

#[derive(Debug, Serialize)]
struct PlacementContext {
    overlay: PlacementOverlay,
    pointer: PlacementPoint,
    monitors: Vec<PlacementMonitor>,
    auto_position: bool,
}

impl OverlayPlacement {
    fn mark_manual(&self) {
        self.manually_positioned.store(true, Ordering::Release);
    }

    fn reset(&self) {
        self.manually_positioned.store(false, Ordering::Release);
    }

    fn should_auto_position(&self) -> bool {
        !self.manually_positioned.load(Ordering::Acquire)
    }
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

fn clamp_overlay_height(content_height: f64) -> f64 {
    content_height.ceil().clamp(224.0, 760.0)
}

#[tauri::command]
#[allow(clippy::needless_pass_by_value)] // Tauri command extraction injects an owned window.
fn fit_overlay(
    window: tauri::WebviewWindow,
    placement: tauri::State<'_, OverlayPlacement>,
    motion: tauri::State<'_, OverlayMotion>,
    content_height: f64,
    animate: bool,
) -> Result<(), String> {
    if !content_height.is_finite() {
        return Err("overlay content height must be finite".to_owned());
    }
    let scale = window.scale_factor().map_err(|error| error.to_string())?;
    let current = current_overlay_geometry(&window)?;
    let target_height = rounded_u32(clamp_overlay_height(content_height) * scale);
    let (monitor, screen) = monitor_for_overlay(&window, current)?;
    let target = if placement.should_auto_position() {
        let destination = motion.target_screen().unwrap_or(OverlayScreen {
            rect: screen,
            bottom_margin: physical_bottom_margin(&monitor),
            anchor: OverlayAnchor::BottomCenter,
        });
        if destination.anchor == OverlayAnchor::Preserve {
            anchored_resize_geometry(
                current,
                target_height,
                destination.rect,
                destination.bottom_margin,
            )
        } else {
            overlay_geometry_at_anchor(
                destination.rect,
                current.width,
                target_height,
                destination.bottom_margin,
                destination.anchor,
            )
        }
    } else {
        anchored_resize_geometry(
            current,
            target_height,
            screen,
            physical_bottom_margin(&monitor),
        )
    };
    motion.retarget(window, target, animate)
}

#[tauri::command]
#[allow(clippy::needless_pass_by_value)]
fn begin_overlay_drag(
    window: tauri::WebviewWindow,
    placement: tauri::State<'_, OverlayPlacement>,
    motion: tauri::State<'_, OverlayMotion>,
) -> Result<(), String> {
    placement.mark_manual();
    motion.clear_screen();
    window.start_dragging().map_err(|error| error.to_string())
}

#[tauri::command]
#[allow(clippy::needless_pass_by_value)]
fn reset_overlay_position(
    window: tauri::WebviewWindow,
    placement: tauri::State<'_, OverlayPlacement>,
    motion: tauri::State<'_, OverlayMotion>,
    animate: bool,
) -> Result<(), String> {
    placement.reset();
    move_overlay_to_cursor(window, &motion, animate)
}

#[tauri::command]
#[allow(clippy::needless_pass_by_value)]
fn move_overlay_for_attention(
    window: tauri::WebviewWindow,
    placement: tauri::State<'_, OverlayPlacement>,
    motion: tauri::State<'_, OverlayMotion>,
    intent: String,
    animate: bool,
) -> Result<(), String> {
    let intent = PlacementIntent::parse(&intent)?;
    if !placement.should_auto_position() {
        return Ok(());
    }
    move_overlay_to_attention(window, &motion, intent, animate)
}

#[tauri::command]
#[allow(clippy::needless_pass_by_value)]
fn overlay_placement_context(
    window: tauri::WebviewWindow,
    placement: tauri::State<'_, OverlayPlacement>,
) -> Result<PlacementContext, String> {
    let current = current_overlay_geometry(&window)?;
    let visible = window.is_visible().map_err(|error| error.to_string())?;
    let pointer = window
        .cursor_position()
        .map_err(|error| error.to_string())?;
    let monitors = window
        .available_monitors()
        .map_err(|error| error.to_string())?
        .into_iter()
        .enumerate()
        .map(|(index, monitor)| {
            let rect = screen_rect(&monitor);
            let bottom_margin = physical_bottom_margin(&monitor);
            PlacementMonitor {
                name: monitor
                    .name()
                    .map_or_else(|| format!("display-{}", index + 1), ToOwned::to_owned),
                bounds: placement_rect(rect),
                work_area: placement_rect(ScreenRect {
                    bottom: rect.bottom.saturating_sub(bottom_margin),
                    ..rect
                }),
            }
        })
        .collect();
    Ok(PlacementContext {
        overlay: PlacementOverlay {
            left: current.x,
            top: current.y,
            width: current.width,
            height: current.height,
            visible,
        },
        pointer: PlacementPoint {
            x: pointer.x,
            y: pointer.y,
        },
        monitors,
        auto_position: placement.should_auto_position(),
    })
}

#[tauri::command]
#[allow(clippy::too_many_arguments, clippy::needless_pass_by_value)]
fn apply_content_aware_placement(
    window: tauri::WebviewWindow,
    placement: tauri::State<'_, OverlayPlacement>,
    motion: tauri::State<'_, OverlayMotion>,
    intent: String,
    disposition: String,
    target_left: i32,
    target_top: i32,
    target_width: u32,
    target_height: u32,
    animate: bool,
) -> Result<bool, String> {
    let intent = PlacementIntent::parse(&intent)?;
    if !placement.should_auto_position() {
        return Ok(true);
    }
    if disposition == "defer" {
        if intent != PlacementIntent::Proactive {
            return Err("an active conversation cannot be deferred".to_owned());
        }
        window.hide().map_err(|error| error.to_string())?;
        return Ok(false);
    }
    if disposition != "place" {
        return Err("overlay placement disposition is not supported".to_owned());
    }
    if target_width < 320 || target_height < 160 {
        return Err("content-aware target is too small for the conversation surface".to_owned());
    }
    let target = OverlayGeometry::new(target_left, target_top, target_width, target_height);
    let monitors = window
        .available_monitors()
        .map_err(|error| error.to_string())?;
    let screens = monitors.iter().map(screen_rect).collect::<Vec<_>>();
    if !geometry_is_inside_any_screen(target, &screens) {
        return Err("content-aware target must fit on one display".to_owned());
    }
    let center = (
        f64::from(target.x) + f64::from(target.width) / 2.0,
        f64::from(target.y) + f64::from(target.height) / 2.0,
    );
    let monitor_index = monitor_index_at(center, &screens)
        .ok_or_else(|| "content-aware target display is unavailable".to_owned())?;
    motion.remember_screen(OverlayScreen {
        rect: screens[monitor_index],
        bottom_margin: physical_bottom_margin(&monitors[monitor_index]),
        anchor: OverlayAnchor::Preserve,
    });
    motion.relocate(window, target, animate)?;
    Ok(true)
}

fn placement_rect(rect: ScreenRect) -> PlacementRect {
    PlacementRect {
        left: rect.left,
        top: rect.top,
        width: u32::try_from(rect.right.saturating_sub(rect.left)).unwrap_or(0),
        height: u32::try_from(rect.bottom.saturating_sub(rect.top)).unwrap_or(0),
    }
}

fn validate_notification_message(message: &str) -> Result<&str, &'static str> {
    let normalized = message.trim();
    if normalized.is_empty() || normalized.len() > 1_000 {
        return Err("notification message must contain between 1 and 1000 bytes");
    }
    Ok(normalized)
}

#[tauri::command]
#[allow(clippy::needless_pass_by_value)] // Tauri command extraction requires owned inputs.
fn show_reminder_notification(app: tauri::AppHandle, message: String) -> Result<(), String> {
    let normalized = validate_notification_message(&message).map_err(str::to_owned)?;
    app.notification()
        .builder()
        .title("JARVIS reminder")
        .body(normalized)
        .show()
        .map_err(|error| error.to_string())
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
        .invoke_handler(tauri::generate_handler![
            apply_content_aware_placement,
            begin_overlay_drag,
            desktop_session_token,
            fit_overlay,
            move_overlay_for_attention,
            overlay_placement_context,
            reset_overlay_position,
            show_reminder_notification
        ])
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
        .manage(OverlayPlacement::default())
        .manage(OverlayMotion::default())
        .setup(|app| {
            let desktop_session = DesktopSession::new();
            app.autolaunch().enable()?;
            let overlay = app
                .get_webview_window("overlay")
                .ok_or("JARVIS overlay window was not created")?;
            position_overlay(&overlay)?;

            let show = MenuItem::with_id(app, "show", "Show JARVIS", true, None::<&str>)?;
            let hide = MenuItem::with_id(app, "hide", "Hide JARVIS", true, None::<&str>)?;
            let reset =
                MenuItem::with_id(app, "reset", "Reset overlay position", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit JARVIS", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &hide, &reset, &quit])?;
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
                    "reset" => {
                        app.state::<OverlayPlacement>().reset();
                        if let Some(window) = app.get_webview_window("overlay") {
                            let _ = move_overlay_to_cursor(
                                window.clone(),
                                &app.state::<OverlayMotion>(),
                                true,
                            );
                            let _ = window.show();
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
        window.show()?;
        window.set_focus()?;
    }
    Ok(())
}

fn current_overlay_geometry(window: &tauri::WebviewWindow) -> Result<OverlayGeometry, String> {
    let size = window.outer_size().map_err(|error| error.to_string())?;
    let position = window.outer_position().map_err(|error| error.to_string())?;
    Ok(OverlayGeometry::new(
        position.x,
        position.y,
        size.width,
        size.height,
    ))
}

fn move_overlay_to_cursor(
    window: tauri::WebviewWindow,
    motion: &OverlayMotion,
    animate: bool,
) -> Result<(), String> {
    let current = current_overlay_geometry(&window)?;
    let (monitor, screen) = monitor_for_cursor(&window)?;
    let target = centered_overlay_geometry(
        screen,
        current.width,
        current.height,
        physical_bottom_margin(&monitor),
    );
    motion.remember_screen(OverlayScreen {
        rect: screen,
        bottom_margin: physical_bottom_margin(&monitor),
        anchor: OverlayAnchor::BottomCenter,
    });
    motion.relocate(window, target, animate)
}

fn move_overlay_to_attention(
    window: tauri::WebviewWindow,
    motion: &OverlayMotion,
    intent: PlacementIntent,
    animate: bool,
) -> Result<(), String> {
    let current = current_overlay_geometry(&window)?;
    let overlay_visible = window.is_visible().map_err(|error| error.to_string())?;
    let (monitor, screen) = if intent == PlacementIntent::Conversation || !overlay_visible {
        monitor_for_cursor(&window)?
    } else {
        monitor_for_overlay(&window, current)?
    };
    let cursor = window
        .cursor_position()
        .map_err(|error| error.to_string())?;
    let bottom_margin = physical_bottom_margin(&monitor);
    let attention = (cursor.x, cursor.y);
    let visible_on_target = overlay_visible && geometry_is_inside_screen(current, screen);
    let anchor = select_attention_anchor(
        screen,
        current,
        attention,
        bottom_margin,
        intent,
        visible_on_target,
    );
    let target = place_overlay_for_attention(
        screen,
        current,
        attention,
        bottom_margin,
        intent,
        visible_on_target,
    );
    motion.remember_screen(OverlayScreen {
        rect: screen,
        bottom_margin,
        anchor,
    });
    motion.relocate(window, target, animate)
}

fn screen_rect(monitor: &Monitor) -> ScreenRect {
    ScreenRect::new(
        monitor.position().x,
        monitor.position().y,
        monitor.size().width,
        monitor.size().height,
    )
}

fn physical_bottom_margin(monitor: &Monitor) -> i32 {
    rounded_i32(48.0 * monitor.scale_factor())
}

fn centered_overlay_geometry(
    screen: ScreenRect,
    width: u32,
    height: u32,
    bottom_margin: i32,
) -> OverlayGeometry {
    let available_width = u32::try_from(screen.right.saturating_sub(screen.left)).unwrap_or(0);
    let available_height = u32::try_from(
        screen
            .bottom
            .saturating_sub(screen.top)
            .saturating_sub(bottom_margin),
    )
    .unwrap_or(0);
    let width = width.min(available_width);
    let height = height.min(available_height);
    let horizontal_space = available_width.saturating_sub(width);
    let x_offset = i32::try_from(horizontal_space / 2).unwrap_or(i32::MAX);
    let y_offset = i32::try_from(available_height.saturating_sub(height)).unwrap_or(i32::MAX);
    OverlayGeometry::new(
        screen.left.saturating_add(x_offset),
        screen.top.saturating_add(y_offset),
        width,
        height,
    )
}

fn monitor_for_cursor(window: &tauri::WebviewWindow) -> Result<(Monitor, ScreenRect), String> {
    let monitors = window
        .available_monitors()
        .map_err(|error| error.to_string())?;
    let rectangles = monitors.iter().map(screen_rect).collect::<Vec<_>>();
    let cursor = window
        .cursor_position()
        .map_err(|error| error.to_string())?;
    if let Some(index) = monitor_index_at((cursor.x, cursor.y), &rectangles) {
        return Ok((monitors[index].clone(), rectangles[index]));
    }
    let monitor = window
        .primary_monitor()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "no monitor is available for the JARVIS overlay".to_owned())?;
    Ok((monitor.clone(), screen_rect(&monitor)))
}

fn monitor_for_overlay(
    window: &tauri::WebviewWindow,
    current: OverlayGeometry,
) -> Result<(Monitor, ScreenRect), String> {
    let monitors = window
        .available_monitors()
        .map_err(|error| error.to_string())?;
    let rectangles = monitors.iter().map(screen_rect).collect::<Vec<_>>();
    let center = (
        f64::from(current.x) + f64::from(current.width) / 2.0,
        f64::from(current.y) + f64::from(current.height) / 2.0,
    );
    if let Some(index) = monitor_index_at(center, &rectangles) {
        return Ok((monitors[index].clone(), rectangles[index]));
    }
    monitor_for_cursor(window)
}

fn position_overlay(window: &tauri::WebviewWindow) -> tauri::Result<()> {
    let monitors = window.available_monitors()?;
    let rectangles = monitors
        .iter()
        .map(|monitor| {
            ScreenRect::new(
                monitor.position().x,
                monitor.position().y,
                monitor.size().width,
                monitor.size().height,
            )
        })
        .collect::<Vec<_>>();
    let cursor = window.cursor_position()?;
    if let Some(index) = monitor_index_at((cursor.x, cursor.y), &rectangles) {
        return position_overlay_on_monitor(window, &monitors[index]);
    }
    let Some(monitor) = window.primary_monitor()? else {
        return Ok(());
    };
    position_overlay_on_monitor(window, &monitor)
}

fn position_overlay_on_monitor(
    window: &tauri::WebviewWindow,
    monitor: &Monitor,
) -> tauri::Result<()> {
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
    use super::{
        DesktopSession, OverlayPlacement, clamp_overlay_height, validate_notification_message,
    };

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

    #[test]
    fn notification_messages_are_bounded_and_non_empty() {
        assert_eq!(
            validate_notification_message(" Leave for practice now. ").unwrap(),
            "Leave for practice now."
        );
        assert!(validate_notification_message("   ").is_err());
        assert!(validate_notification_message(&"x".repeat(1_001)).is_err());
    }

    #[test]
    fn overlay_expands_for_content_without_taking_over_the_screen() {
        assert!((clamp_overlay_height(90.0) - 224.0).abs() < f64::EPSILON);
        assert!((clamp_overlay_height(336.25) - 337.0).abs() < f64::EPSILON);
        assert!((clamp_overlay_height(900.0) - 760.0).abs() < f64::EPSILON);
    }

    #[test]
    fn manually_moved_overlay_stays_put_until_position_is_reset() {
        let placement = OverlayPlacement::default();

        assert!(placement.should_auto_position());
        placement.mark_manual();
        assert!(!placement.should_auto_position());
        placement.reset();
        assert!(placement.should_auto_position());
    }
}
