use std::collections::VecDeque;
use std::time::Duration;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExitDecision {
    Stop,
    RestartAfter(Duration),
}

#[derive(Debug)]
pub struct RestartPolicy {
    recent_failures_ms: VecDeque<u64>,
    maximum_restarts: usize,
    restart_window_ms: u64,
    base_delay_ms: u64,
    maximum_delay_ms: u64,
}

impl Default for RestartPolicy {
    fn default() -> Self {
        Self {
            recent_failures_ms: VecDeque::new(),
            maximum_restarts: 5,
            restart_window_ms: 60_000,
            base_delay_ms: 250,
            maximum_delay_ms: 10_000,
        }
    }
}

impl RestartPolicy {
    pub fn decide(&mut self, now_ms: u64, clean_exit: bool, stopping: bool) -> ExitDecision {
        if clean_exit || stopping {
            self.recent_failures_ms.clear();
            return ExitDecision::Stop;
        }
        while self
            .recent_failures_ms
            .front()
            .is_some_and(|recorded| now_ms.saturating_sub(*recorded) > self.restart_window_ms)
        {
            self.recent_failures_ms.pop_front();
        }
        if self.recent_failures_ms.len() >= self.maximum_restarts {
            return ExitDecision::Stop;
        }
        self.recent_failures_ms.push_back(now_ms);
        let exponent = u32::try_from(self.recent_failures_ms.len().saturating_sub(1)).unwrap_or(31);
        let delay = self
            .base_delay_ms
            .saturating_mul(2_u64.saturating_pow(exponent))
            .min(self.maximum_delay_ms);
        ExitDecision::RestartAfter(Duration::from_millis(delay))
    }
}

#[cfg(test)]
mod tests {
    use super::{ExitDecision, RestartPolicy};
    use std::time::Duration;

    #[test]
    fn crash_restarts_use_bounded_exponential_backoff() {
        let mut policy = RestartPolicy::default();

        let decisions: Vec<_> = (0..5)
            .map(|index| policy.decide(index * 1_000, false, false))
            .collect();

        assert_eq!(
            decisions,
            vec![
                ExitDecision::RestartAfter(Duration::from_millis(250)),
                ExitDecision::RestartAfter(Duration::from_millis(500)),
                ExitDecision::RestartAfter(Duration::from_secs(1)),
                ExitDecision::RestartAfter(Duration::from_secs(2)),
                ExitDecision::RestartAfter(Duration::from_secs(4)),
            ]
        );
        assert_eq!(policy.decide(5_000, false, false), ExitDecision::Stop);
    }

    #[test]
    fn stable_period_resets_the_crash_window() {
        let mut policy = RestartPolicy::default();
        assert_eq!(
            policy.decide(0, false, false),
            ExitDecision::RestartAfter(Duration::from_millis(250))
        );
        assert_eq!(
            policy.decide(61_000, false, false),
            ExitDecision::RestartAfter(Duration::from_millis(250))
        );
    }

    #[test]
    fn clean_or_requested_shutdown_never_restarts() {
        let mut policy = RestartPolicy::default();
        assert_eq!(policy.decide(0, true, false), ExitDecision::Stop);
        assert_eq!(policy.decide(1, false, true), ExitDecision::Stop);
    }
}
