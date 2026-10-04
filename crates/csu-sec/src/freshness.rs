//! Freshness value (FV) tracking.
//!
//! The paper tracks FV per security association, scoped to the source address,
//! with finer per-PGN tracking left to the implementation. This tracker keys on
//! (SA, PGN) and accepts strictly increasing values. Any session reset must
//! call [`FreshnessTracker::reset`].
//!
//! TODO(J1939-91C): rollover and acceptance-window policy.

use std::collections::HashMap;

#[derive(Default, Debug)]
pub struct FreshnessTracker {
    last: HashMap<(u8, u32), u32>,
    rejected: u64,
}

impl FreshnessTracker {
    pub fn new() -> Self {
        Self::default()
    }

    /// Seed the last accepted value (e.g. from a test vector's assumption).
    pub fn seed(&mut self, sa: u8, pgn: u32, fv: u32) {
        self.last.insert((sa, pgn), fv);
    }

    /// `true` and remember `fv` if it is newer than the last accepted value.
    pub fn accept(&mut self, sa: u8, pgn: u32, fv: u32) -> bool {
        match self.last.get(&(sa, pgn)) {
            Some(&prev) if fv <= prev => {
                self.rejected += 1;
                false
            }
            _ => {
                self.last.insert((sa, pgn), fv);
                true
            }
        }
    }

    /// Passive check without updating state (monitor mode, no keys).
    pub fn is_fresh(&self, sa: u8, pgn: u32, fv: u32) -> bool {
        self.last.get(&(sa, pgn)).is_none_or(|&prev| fv > prev)
    }

    pub fn rejected(&self) -> u64 {
        self.rejected
    }

    /// Forget all state (session end, bus-off, rekey).
    pub fn reset(&mut self) {
        self.last.clear();
    }
}
