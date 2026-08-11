//! Thin Windows host for JARVIS. Product behavior lives in the Python core.

mod host;
mod supervisor;

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use host::CoreSupervisor;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{
    Emitter, Manager, Monitor, PhysicalPosition, PhysicalSize, Position, RunEvent, WindowEvent,
};
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

#[derive(Debug, Clone)]
struct OverlayMotion {
    revision: Arc<AtomicU64>,
    target_screen: Arc<Mutex<Option<OverlayScreen>>>,
}

impl Default for OverlayMotion {
    fn default() -> Self {
        Self {
            revision: Arc::new(AtomicU64::new(0)),
            target_screen: Arc::new(Mutex::new(None)),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct OverlayScreen {
    rect: ScreenRect,
    bottom_margin: i32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct OverlayGeometry {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
}

impl OverlayGeometry {
    const fn new(x: i32, y: i32, width: u32, height: u32) -> Self {
        Self {
            x,
            y,
            width,
            height,
        }
    }
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ScreenRect {
    left: i32,
    top: i32,
    right: i32,
    bottom: i32,
}

impl ScreenRect {
    fn new(left: i32, top: i32, width: u32, height: u32) -> Self {
        let width = i32::try_from(width).unwrap_or(i32::MAX);
        let height = i32::try_from(height).unwrap_or(i32::MAX);
        Self {
            left,
            top,
            right: left.saturating_add(width),
            bottom: top.saturating_add(height),
        }
    }

    fn contains(self, point: (f64, f64)) -> bool {
        point.0 >= f64::from(self.left)
            && point.0 < f64::from(self.right)
            && point.1 >= f64::from(self.top)
            && point.1 < f64::from(self.bottom)
    }
}

fn monitor_index_at(point: (f64, f64), monitors: &[ScreenRect]) -> Option<usize> {
    monitors.iter().position(|monitor| monitor.contains(point))
}

fn interpolate_geometry(
    start: OverlayGeometry,
    target: OverlayGeometry,
    progress: f64,
) -> OverlayGeometry {
    let progress = progress.clamp(0.0, 1.0);
    let eased = 1.0 - (1.0 - progress).powi(5);
    let interpolate_position = |from: i32, to: i32| {
        rounded_i32(f64::from(from) + f64::from(to.saturating_sub(from)) * eased)
    };
    let interpolate_extent = |from: u32, to: u32| {
        rounded_u32(f64::from(from) + (f64::from(to) - f64::from(from)) * eased)
    };
    OverlayGeometry::new(
        interpolate_position(start.x, target.x),
        interpolate_position(start.y, target.y),
        interpolate_extent(start.width, target.width),
        interpolate_extent(start.height, target.height),
    )
}

#[allow(clippy::cast_possible_truncation)]
fn rounded_i32(value: f64) -> i32 {
    value
        .clamp(f64::from(i32::MIN), f64::from(i32::MAX))
        .round() as i32
}

#[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
fn rounded_u32(value: f64) -> u32 {
    value.clamp(0.0, f64::from(u32::MAX)).round() as u32
}

fn anchored_resize_geometry(
    current: OverlayGeometry,
    target_height: u32,
    screen: ScreenRect,
    bottom_margin: i32,
) -> OverlayGeometry {
    let available_height = u32::try_from(
        screen
            .bottom
            .saturating_sub(screen.top)
            .saturating_sub(bottom_margin),
    )
    .unwrap_or(0);
    let height = target_height.min(available_height);
    let width = current
        .width
        .min(u32::try_from(screen.right.saturating_sub(screen.left)).unwrap_or(0));
    let width_i32 = i32::try_from(width).unwrap_or(i32::MAX);
    let height_i32 = i32::try_from(height).unwrap_or(i32::MAX);
    let max_x = screen.right.saturating_sub(width_i32);
    let usable_bottom = screen.bottom.saturating_sub(bottom_margin);
    let current_bottom = current
        .y
        .saturating_add(i32::try_from(current.height).unwrap_or(i32::MAX));
    let anchored_bottom = current_bottom.clamp(screen.top, usable_bottom);
    let max_y = usable_bottom.saturating_sub(height_i32);
    OverlayGeometry::new(
        current.x.clamp(screen.left, max_x),
        anchored_bottom
            .saturating_sub(height_i32)
            .clamp(screen.top, max_y),
        width,
        height,
    )
}

impl OverlayMotion {
    fn remember_screen(&self, screen: OverlayScreen) {
        *self
            .target_screen
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(screen);
    }

    fn target_screen(&self) -> Option<OverlayScreen> {
        *self
            .target_screen
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn clear_screen(&self) {
        *self
            .target_screen
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = None;
    }

    fn retarget(
        &self,
        window: tauri::WebviewWindow,
        target: OverlayGeometry,
        animate: bool,
    ) -> Result<(), String> {
        let size = window.outer_size().map_err(|error| error.to_string())?;
        let position = window.outer_position().map_err(|error| error.to_string())?;
        let start = OverlayGeometry::new(position.x, position.y, size.width, size.height);
        let revision = self.revision.fetch_add(1, Ordering::AcqRel) + 1;
        if !animate || start == target {
            apply_overlay_geometry(&window, target)?;
            return Ok(());
        }

        let latest_revision = Arc::clone(&self.revision);
        std::thread::spawn(move || {
            const FRAMES: u32 = 14;
            for frame in 1..=FRAMES {
                if latest_revision.load(Ordering::Acquire) != revision {
                    return;
                }
                let geometry =
                    interpolate_geometry(start, target, f64::from(frame) / f64::from(FRAMES));
                if apply_overlay_geometry(&window, geometry).is_err() {
                    return;
                }
                if frame < FRAMES {
                    std::thread::sleep(Duration::from_millis(16));
                }
            }
        });
        Ok(())
    }
}

fn apply_overlay_geometry(
    window: &tauri::WebviewWindow,
    geometry: OverlayGeometry,
) -> Result<(), String> {
    window
        .set_size(PhysicalSize::new(geometry.width, geometry.height))
        .map_err(|error| error.to_string())?;
    window
        .set_position(Position::Physical(PhysicalPosition::new(
            geometry.x, geometry.y,
        )))
        .map_err(|error| error.to_string())
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
    content_height.ceil().clamp(224.0, 520.0)
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
        });
        centered_overlay_geometry(
            destination.rect,
            current.width,
            target_height,
            destination.bottom_margin,
        )
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
            begin_overlay_drag,
            desktop_session_token,
            fit_overlay,
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
        if app.state::<OverlayPlacement>().should_auto_position() {
            position_overlay(&window)?;
        }
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
    });
    motion.retarget(window, target, animate)
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
        DesktopSession, OverlayGeometry, OverlayMotion, OverlayPlacement, OverlayScreen,
        ScreenRect, anchored_resize_geometry, clamp_overlay_height, interpolate_geometry,
        monitor_index_at, validate_notification_message,
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
    fn overlay_follows_the_monitor_containing_the_cursor() {
        let monitors = [
            ScreenRect::new(0, 0, 3_840, 2_160),
            ScreenRect::new(-3_840, 0, 1_920, 2_160),
            ScreenRect::new(-6_400, 1_007, 2_560, 1_440),
        ];

        assert_eq!(monitor_index_at((-3_200.0, 900.0), &monitors), Some(1));
        assert_eq!(monitor_index_at((-5_900.0, 1_200.0), &monitors), Some(2));
        assert_eq!(monitor_index_at((4_100.0, 900.0), &monitors), None);
    }

    #[test]
    fn overlay_expands_for_content_without_taking_over_the_screen() {
        assert!((clamp_overlay_height(90.0) - 224.0).abs() < f64::EPSILON);
        assert!((clamp_overlay_height(336.25) - 337.0).abs() < f64::EPSILON);
        assert!((clamp_overlay_height(900.0) - 520.0).abs() < f64::EPSILON);
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

    #[test]
    fn native_motion_eases_toward_the_latest_overlay_geometry() {
        let start = OverlayGeometry::new(100, 700, 760, 224);
        let target = OverlayGeometry::new(900, 300, 760, 480);

        let halfway = interpolate_geometry(start, target, 0.5);

        assert!(halfway.x > 500);
        assert!(halfway.y < 500);
        assert!(halfway.height > 352);
        assert_eq!(interpolate_geometry(start, target, 1.0), target);
    }

    #[test]
    fn a_manually_placed_overlay_grows_upward_and_remains_visible() {
        let screen = ScreenRect::new(0, 0, 1_920, 1_080);
        let current = OverlayGeometry::new(580, 700, 760, 224);

        let expanded = anchored_resize_geometry(current, 480, screen, 48);

        assert_eq!(expanded, OverlayGeometry::new(580, 444, 760, 480));
        assert_eq!(expanded.y + i32::try_from(expanded.height).unwrap(), 924);
    }

    #[test]
    fn live_content_retargets_keep_the_invocations_destination_screen() {
        let motion = OverlayMotion::default();
        let destination = OverlayScreen {
            rect: ScreenRect::new(-6_400, 1_008, 2_560, 1_440),
            bottom_margin: 72,
        };

        motion.remember_screen(destination);

        assert_eq!(motion.target_screen(), Some(destination));
        motion.clear_screen();
        assert_eq!(motion.target_screen(), None);
    }
}
