//! Attention-aware native placement and interruptible overlay motion.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::{Emitter, LogicalSize, PhysicalPosition, PhysicalSize, Position};

const ORB_EXTENT: u32 = 88;

#[derive(Debug, Clone)]
pub(crate) struct OverlayMotion {
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
pub(crate) struct OverlayScreen {
    pub(crate) rect: ScreenRect,
    pub(crate) bottom_margin: i32,
    pub(crate) anchor: OverlayAnchor,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct OverlayGeometry {
    pub(crate) x: i32,
    pub(crate) y: i32,
    pub(crate) width: u32,
    pub(crate) height: u32,
}

impl OverlayGeometry {
    pub(crate) const fn new(x: i32, y: i32, width: u32, height: u32) -> Self {
        Self {
            x,
            y,
            width,
            height,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum OverlayAnchor {
    Preserve,
    TopLeft,
    TopRight,
    MiddleLeft,
    MiddleRight,
    BottomLeft,
    BottomCenter,
    BottomRight,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum PlacementIntent {
    Conversation,
    Proactive,
}

impl PlacementIntent {
    pub(crate) fn parse(value: &str) -> Result<Self, String> {
        match value {
            "conversation" => Ok(Self::Conversation),
            "proactive" => Ok(Self::Proactive),
            _ => Err("overlay movement intent is not supported".to_owned()),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ScreenRect {
    pub(crate) left: i32,
    pub(crate) top: i32,
    pub(crate) right: i32,
    pub(crate) bottom: i32,
}

impl ScreenRect {
    pub(crate) fn new(left: i32, top: i32, width: u32, height: u32) -> Self {
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

pub(crate) fn monitor_index_at(point: (f64, f64), monitors: &[ScreenRect]) -> Option<usize> {
    monitors.iter().position(|monitor| monitor.contains(point))
}

pub(crate) fn rounded_i32(value: f64) -> i32 {
    #[allow(clippy::cast_possible_truncation)]
    let rounded = value
        .clamp(f64::from(i32::MIN), f64::from(i32::MAX))
        .round() as i32;
    rounded
}

pub(crate) fn rounded_u32(value: f64) -> u32 {
    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
    let rounded = value.clamp(0.0, f64::from(u32::MAX)).round() as u32;
    rounded
}

pub(crate) fn anchored_resize_geometry(
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

pub(crate) fn overlay_geometry_at_anchor(
    screen: ScreenRect,
    width: u32,
    height: u32,
    bottom_margin: i32,
    anchor: OverlayAnchor,
) -> OverlayGeometry {
    const EDGE_MARGIN: i32 = 32;
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
    let width_i32 = i32::try_from(width).unwrap_or(i32::MAX);
    let height_i32 = i32::try_from(height).unwrap_or(i32::MAX);
    let horizontal_slack = available_width.saturating_sub(width);
    let vertical_slack = available_height.saturating_sub(height);
    let horizontal_margin =
        i32::try_from((horizontal_slack / 2).min(EDGE_MARGIN as u32)).unwrap_or(EDGE_MARGIN);
    let vertical_margin =
        i32::try_from((vertical_slack / 2).min(EDGE_MARGIN as u32)).unwrap_or(EDGE_MARGIN);
    let left = screen.left.saturating_add(horizontal_margin);
    let right = screen
        .right
        .saturating_sub(horizontal_margin)
        .saturating_sub(width_i32);
    let top = screen.top.saturating_add(vertical_margin);
    let usable_bottom = screen.bottom.saturating_sub(bottom_margin);
    let bottom = usable_bottom
        .saturating_sub(vertical_margin)
        .saturating_sub(height_i32);
    let middle = screen
        .top
        .saturating_add(i32::try_from(vertical_slack / 2).unwrap_or(i32::MAX));
    let center = screen.left.saturating_add(
        screen
            .right
            .saturating_sub(screen.left)
            .saturating_sub(width_i32)
            / 2,
    );
    let (x, y) = match anchor {
        OverlayAnchor::TopLeft => (left, top),
        OverlayAnchor::TopRight => (right, top),
        OverlayAnchor::MiddleLeft => (left, middle),
        OverlayAnchor::MiddleRight => (right, middle),
        OverlayAnchor::BottomLeft => (left, bottom),
        OverlayAnchor::BottomCenter | OverlayAnchor::Preserve => (center, bottom),
        OverlayAnchor::BottomRight => (right, bottom),
    };
    OverlayGeometry::new(x, y, width, height)
}

pub(crate) fn geometry_is_inside_screen(geometry: OverlayGeometry, screen: ScreenRect) -> bool {
    let right = geometry
        .x
        .saturating_add(i32::try_from(geometry.width).unwrap_or(i32::MAX));
    let bottom = geometry
        .y
        .saturating_add(i32::try_from(geometry.height).unwrap_or(i32::MAX));
    geometry.x >= screen.left
        && geometry.y >= screen.top
        && right <= screen.right
        && bottom <= screen.bottom
}

pub(crate) fn geometry_is_inside_any_screen(
    geometry: OverlayGeometry,
    screens: &[ScreenRect],
) -> bool {
    screens
        .iter()
        .any(|screen| geometry_is_inside_screen(geometry, *screen))
}

fn geometry_is_clear_of_attention(
    geometry: OverlayGeometry,
    attention: (f64, f64),
    clearance: i32,
) -> bool {
    let width = i32::try_from(geometry.width).unwrap_or(i32::MAX);
    let height = i32::try_from(geometry.height).unwrap_or(i32::MAX);
    attention.0 < f64::from(geometry.x.saturating_sub(clearance))
        || attention.0 > f64::from(geometry.x.saturating_add(width).saturating_add(clearance))
        || attention.1 < f64::from(geometry.y.saturating_sub(clearance))
        || attention.1 > f64::from(geometry.y.saturating_add(height).saturating_add(clearance))
}

pub(crate) fn select_attention_anchor(
    screen: ScreenRect,
    current: OverlayGeometry,
    attention: (f64, f64),
    bottom_margin: i32,
    intent: PlacementIntent,
    overlay_visible: bool,
) -> OverlayAnchor {
    let current_is_safe = geometry_is_inside_screen(current, screen);
    if overlay_visible && current_is_safe {
        return OverlayAnchor::Preserve;
    }
    let clearance = match intent {
        PlacementIntent::Conversation => 112,
        PlacementIntent::Proactive => 180,
    };
    if current_is_safe && geometry_is_clear_of_attention(current, attention, clearance) {
        return OverlayAnchor::Preserve;
    }

    let candidates = [
        OverlayAnchor::TopLeft,
        OverlayAnchor::TopRight,
        OverlayAnchor::MiddleLeft,
        OverlayAnchor::MiddleRight,
        OverlayAnchor::BottomLeft,
        OverlayAnchor::BottomRight,
    ];
    let current_center = (
        f64::from(current.x) + f64::from(current.width) / 2.0,
        f64::from(current.y) + f64::from(current.height) / 2.0,
    );
    let mut nearest_clear: Option<(OverlayAnchor, f64)> = None;
    let mut safest_fallback = (candidates[0], f64::NEG_INFINITY);
    for anchor in candidates {
        let geometry = overlay_geometry_at_anchor(
            screen,
            current.width,
            current.height,
            bottom_margin,
            anchor,
        );
        let center = (
            f64::from(geometry.x) + f64::from(geometry.width) / 2.0,
            f64::from(geometry.y) + f64::from(geometry.height) / 2.0,
        );
        let attention_distance = (center.0 - attention.0).hypot(center.1 - attention.1);
        if attention_distance > safest_fallback.1 {
            safest_fallback = (anchor, attention_distance);
        }
        if geometry_is_clear_of_attention(geometry, attention, clearance) {
            let travel = (center.0 - current_center.0).hypot(center.1 - current_center.1);
            if nearest_clear.is_none_or(|(_, best_travel)| travel < best_travel) {
                nearest_clear = Some((anchor, travel));
            }
        }
    }
    nearest_clear.map_or(safest_fallback.0, |(anchor, _)| anchor)
}

pub(crate) fn place_overlay_for_attention(
    screen: ScreenRect,
    current: OverlayGeometry,
    attention: (f64, f64),
    bottom_margin: i32,
    intent: PlacementIntent,
    overlay_visible: bool,
) -> OverlayGeometry {
    let anchor = select_attention_anchor(
        screen,
        current,
        attention,
        bottom_margin,
        intent,
        overlay_visible,
    );
    if anchor == OverlayAnchor::Preserve {
        current
    } else {
        overlay_geometry_at_anchor(screen, current.width, current.height, bottom_margin, anchor)
    }
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

fn motion_frame_count(start: OverlayGeometry, target: OverlayGeometry) -> u32 {
    let delta_x = f64::from(target.x.saturating_sub(start.x));
    let delta_y = f64::from(target.y.saturating_sub(start.y));
    let distance = delta_x.hypot(delta_y);
    if distance < 640.0 {
        12
    } else if distance < 1_600.0 {
        16
    } else {
        20
    }
}

fn smoothstep(progress: f64) -> f64 {
    let progress = progress.clamp(0.0, 1.0);
    progress * progress * (3.0 - 2.0 * progress)
}

fn geometry_center(geometry: OverlayGeometry) -> (f64, f64) {
    (
        f64::from(geometry.x) + f64::from(geometry.width) / 2.0,
        f64::from(geometry.y) + f64::from(geometry.height) / 2.0,
    )
}

fn geometry_at_center(center: (f64, f64), width: u32, height: u32) -> OverlayGeometry {
    OverlayGeometry::new(
        rounded_i32(center.0 - f64::from(width) / 2.0),
        rounded_i32(center.1 - f64::from(height) / 2.0),
        width,
        height,
    )
}

fn interpolate_relocation(
    start: OverlayGeometry,
    target: OverlayGeometry,
    progress: f64,
) -> OverlayGeometry {
    let progress = progress.clamp(0.0, 1.0);
    if progress >= 1.0 {
        return target;
    }
    let start_center = geometry_center(start);
    let target_center = geometry_center(target);
    let center_at = |path_progress: f64| {
        let eased = smoothstep(path_progress);
        (
            start_center.0 + (target_center.0 - start_center.0) * eased,
            start_center.1 + (target_center.1 - start_center.1) * eased,
        )
    };
    let extent_at = |from: u32, to: u32, phase: f64| {
        rounded_u32(f64::from(from) + (f64::from(to) - f64::from(from)) * smoothstep(phase))
    };

    if progress <= 0.22 {
        let phase = progress / 0.22;
        geometry_at_center(
            center_at(0.15 * phase),
            extent_at(start.width, ORB_EXTENT, phase),
            extent_at(start.height, ORB_EXTENT, phase),
        )
    } else if progress <= 0.78 {
        let phase = (progress - 0.22) / 0.56;
        geometry_at_center(center_at(0.15 + phase * 0.7), ORB_EXTENT, ORB_EXTENT)
    } else {
        let phase = (progress - 0.78) / 0.22;
        geometry_at_center(
            center_at(0.85 + phase * 0.15),
            extent_at(ORB_EXTENT, target.width, phase),
            extent_at(ORB_EXTENT, target.height, phase),
        )
    }
}

fn relocation_frame_count(start: OverlayGeometry, target: OverlayGeometry) -> u32 {
    let start_center = geometry_center(start);
    let target_center = geometry_center(target);
    let distance = (target_center.0 - start_center.0).hypot(target_center.1 - start_center.1);
    if distance < 640.0 {
        22
    } else if distance < 1_600.0 {
        27
    } else if distance < 4_000.0 {
        32
    } else {
        36
    }
}

fn overlay_transit_kind(start: OverlayGeometry, target: OverlayGeometry) -> &'static str {
    if motion_frame_count(start, target) == 20 {
        "cross-monitor"
    } else {
        "local"
    }
}

impl OverlayMotion {
    pub(crate) fn remember_screen(&self, screen: OverlayScreen) {
        *self
            .target_screen
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(screen);
    }

    pub(crate) fn target_screen(&self) -> Option<OverlayScreen> {
        *self
            .target_screen
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    pub(crate) fn clear_screen(&self) {
        *self
            .target_screen
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = None;
    }

    pub(crate) fn retarget(
        &self,
        window: tauri::WebviewWindow,
        target: OverlayGeometry,
        animate: bool,
    ) -> Result<(), String> {
        restore_overlay_minimum(&window)?;
        let size = window.outer_size().map_err(|error| error.to_string())?;
        let position = window.outer_position().map_err(|error| error.to_string())?;
        let start = OverlayGeometry::new(position.x, position.y, size.width, size.height);
        let revision = self.revision.fetch_add(1, Ordering::AcqRel) + 1;
        if !animate || start == target {
            apply_overlay_geometry(&window, target)?;
            return Ok(());
        }

        let latest_revision = Arc::clone(&self.revision);
        let _ = window.emit("jarvis://overlay-transit", "resize");
        std::thread::spawn(move || {
            let frames = motion_frame_count(start, target);
            for frame in 1..=frames {
                if latest_revision.load(Ordering::Acquire) != revision {
                    return;
                }
                let geometry =
                    interpolate_geometry(start, target, f64::from(frame) / f64::from(frames));
                if apply_overlay_geometry(&window, geometry).is_err() {
                    return;
                }
                if frame < frames {
                    std::thread::sleep(Duration::from_millis(16));
                }
            }
            let _ = window.emit("jarvis://overlay-settled", ());
        });
        Ok(())
    }

    pub(crate) fn relocate(
        &self,
        window: tauri::WebviewWindow,
        target: OverlayGeometry,
        animate: bool,
    ) -> Result<(), String> {
        restore_overlay_minimum(&window)?;
        let size = window.outer_size().map_err(|error| error.to_string())?;
        let position = window.outer_position().map_err(|error| error.to_string())?;
        let start = OverlayGeometry::new(position.x, position.y, size.width, size.height);
        let revision = self.revision.fetch_add(1, Ordering::AcqRel) + 1;
        if !animate || start == target {
            apply_overlay_geometry(&window, target)?;
            return Ok(());
        }

        window
            .set_min_size(Some(PhysicalSize::new(ORB_EXTENT, ORB_EXTENT)))
            .map_err(|error| error.to_string())?;

        let latest_revision = Arc::clone(&self.revision);
        let _ = window.emit(
            "jarvis://overlay-transit",
            overlay_transit_kind(start, target),
        );
        std::thread::spawn(move || {
            let frames = relocation_frame_count(start, target);
            for frame in 1..=frames {
                if latest_revision.load(Ordering::Acquire) != revision {
                    return;
                }
                let geometry =
                    interpolate_relocation(start, target, f64::from(frame) / f64::from(frames));
                if apply_overlay_geometry(&window, geometry).is_err() {
                    let _ = restore_overlay_minimum(&window);
                    return;
                }
                if frame < frames {
                    std::thread::sleep(Duration::from_millis(16));
                }
            }
            let _ = restore_overlay_minimum(&window);
            let _ = window.emit("jarvis://overlay-settled", ());
        });
        Ok(())
    }
}

fn restore_overlay_minimum(window: &tauri::WebviewWindow) -> Result<(), String> {
    window
        .set_min_size(Some(LogicalSize::new(360.0, 224.0)))
        .map_err(|error| error.to_string())
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

#[cfg(test)]
mod tests {
    use super::*;

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
            anchor: OverlayAnchor::BottomRight,
        };

        motion.remember_screen(destination);

        assert_eq!(motion.target_screen(), Some(destination));
        motion.clear_screen();
        assert_eq!(motion.target_screen(), None);
    }

    #[test]
    fn a_clear_conversation_overlay_stays_spatially_stable() {
        let screen = ScreenRect::new(0, 0, 1_920, 1_080);
        let current = OverlayGeometry::new(580, 776, 760, 224);

        let target = place_overlay_for_attention(
            screen,
            current,
            (220.0, 180.0),
            48,
            PlacementIntent::Conversation,
            true,
        );

        assert_eq!(target, current);
    }

    #[test]
    fn typing_into_a_visible_conversation_never_makes_the_overlay_hop() {
        let screen = ScreenRect::new(0, 0, 1_920, 1_080);
        let current = OverlayGeometry::new(580, 776, 760, 224);

        let target = place_overlay_for_attention(
            screen,
            current,
            (960.0, 880.0),
            48,
            PlacementIntent::Conversation,
            true,
        );

        assert_eq!(target, current);
    }

    #[test]
    fn a_clear_proactive_overlay_remains_spatially_stable() {
        let screen = ScreenRect::new(-1_920, 0, 1_920, 1_080);
        let current = OverlayGeometry::new(-1_340, 776, 760, 224);

        let target = place_overlay_for_attention(
            screen,
            current,
            (-1_700.0, 180.0),
            48,
            PlacementIntent::Proactive,
            false,
        );

        assert_eq!(target, current);
    }

    #[test]
    fn an_obstructing_overlay_uses_the_nearest_clear_region() {
        let screen = ScreenRect::new(0, 0, 1_920, 1_080);
        let current = OverlayGeometry::new(580, 776, 760, 224);

        let target = place_overlay_for_attention(
            screen,
            current,
            (960.0, 880.0),
            48,
            PlacementIntent::Proactive,
            false,
        );

        assert_eq!(target, OverlayGeometry::new(32, 404, 760, 224));
        assert!(geometry_is_clear_of_attention(target, (960.0, 880.0), 180));
    }

    #[test]
    fn attention_placement_remains_inside_a_compact_monitor() {
        let screen = ScreenRect::new(0, 0, 800, 600);
        let current = OverlayGeometry::new(20, 20, 760, 520);

        let target = place_overlay_for_attention(
            screen,
            current,
            (400.0, 300.0),
            48,
            PlacementIntent::Proactive,
            false,
        );

        assert!(geometry_is_inside_screen(target, screen));
        assert!(target.y + i32::try_from(target.height).unwrap() <= 552);
    }

    #[test]
    fn cross_monitor_travel_is_smooth_but_bounded() {
        let start = OverlayGeometry::new(580, 776, 760, 224);
        let local_target = OverlayGeometry::new(32, 32, 760, 224);
        let cross_monitor_target = OverlayGeometry::new(-3_808, 32, 760, 224);
        let local = motion_frame_count(start, local_target);
        let cross_monitor = motion_frame_count(start, cross_monitor_target);

        assert!(cross_monitor > local);
        assert!(cross_monitor <= 20);
        assert_eq!(overlay_transit_kind(start, local_target), "local");
        assert_eq!(
            overlay_transit_kind(start, cross_monitor_target),
            "cross-monitor"
        );
    }

    #[test]
    fn a_content_aware_target_must_fit_entirely_on_one_monitor() {
        let monitors = [
            ScreenRect::new(0, 0, 1_920, 1_080),
            ScreenRect::new(1_920, 0, 1_920, 1_080),
        ];

        assert!(geometry_is_inside_any_screen(
            OverlayGeometry::new(24, 24, 760, 224),
            &monitors
        ));
        assert!(!geometry_is_inside_any_screen(
            OverlayGeometry::new(1_700, 24, 760, 224),
            &monitors
        ));
    }

    #[test]
    fn relocation_contracts_to_an_orb_then_reconstructs_the_destination() {
        let start = OverlayGeometry::new(580, 776, 760, 224);
        let target = OverlayGeometry::new(-5_690, 1_220, 420, 640);

        let contracted = interpolate_relocation(start, target, 0.22);
        let travelling = interpolate_relocation(start, target, 0.5);
        let expanding = interpolate_relocation(start, target, 0.88);

        assert_eq!(contracted.width, ORB_EXTENT);
        assert_eq!(contracted.height, ORB_EXTENT);
        assert_eq!(travelling.width, ORB_EXTENT);
        assert!(travelling.x < contracted.x);
        assert!(expanding.width > ORB_EXTENT);
        assert!(expanding.height > ORB_EXTENT);
        assert_eq!(interpolate_relocation(start, target, 1.0), target);
    }

    #[test]
    fn long_cross_monitor_travel_gets_enough_frames_to_read_as_continuous() {
        let start = OverlayGeometry::new(580, 776, 760, 224);
        let target = OverlayGeometry::new(-5_690, 1_220, 420, 640);

        assert!(relocation_frame_count(start, target) >= 30);
        assert!(relocation_frame_count(start, target) <= 36);
    }
}
