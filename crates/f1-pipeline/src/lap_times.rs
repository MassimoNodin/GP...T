//! Track observed completed lap times without doing work on the ingest threads.

use f1_wire::{HEADER_LEN, PacketHeader, PacketId};
use serde::Serialize;

use crate::Consumer;

const CARS: usize = 22;
const CAR_LAP_LEN: usize = 57;
const LAP_NUMBER_OFFSET: usize = 33;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum LapTimeEvent {
    LapSessionStarted { session_uid: u64 },
    LapTimeCompleted { session_uid: u64, car_index: u8, lap_number: u8, time_ms: u32 },
    LapTimeRetracted { session_uid: u64, car_index: u8, lap_number: u8 },
}

struct CarState {
    lap: u8,
    current_ms: u32,
    completed: [u32; 256],
}

impl Default for CarState {
    fn default() -> Self {
        Self { lap: 0, current_ms: 0, completed: [0; 256] }
    }
}

/// A consumer of Lap Data v1 that reports observed completed laps for all cars.
/// A nonzero completed time is only recorded after observing a consecutive lap
/// transition. Previously reported laps are retracted on a backwards move.
pub struct LapTimeTracker<F> {
    session: Option<u64>,
    cars: [CarState; CARS],
    emit: F,
}

impl<F: FnMut(LapTimeEvent)> LapTimeTracker<F> {
    pub fn new(emit: F) -> Self {
        Self { session: None, cars: std::array::from_fn(|_| CarState::default()), emit }
    }

    fn observe(&mut self, header: &PacketHeader, payload: &[u8]) {
        if header.packet_version != 1 || payload.len() != PacketId::LapData.size() {
            return;
        }
        if self.session != Some(header.session_uid) {
            self.session = Some(header.session_uid);
            self.cars = std::array::from_fn(|_| CarState::default());
            (self.emit)(LapTimeEvent::LapSessionStarted { session_uid: header.session_uid });
        }
        for (index, car) in self.cars.iter_mut().enumerate() {
            let offset = HEADER_LEN + index * CAR_LAP_LEN;
            let last_ms = u32::from_le_bytes(payload[offset..offset + 4].try_into().unwrap());
            let current_ms = u32::from_le_bytes(payload[offset + 4..offset + 8].try_into().unwrap());
            let lap = payload[offset + LAP_NUMBER_OFFSET];
            if lap == 0 {
                continue;
            }
            if car.lap != 0 && (lap < car.lap || (lap == car.lap && current_ms < car.current_ms)) {
                for (number, time) in car.completed.iter_mut().enumerate().skip(usize::from(lap)) {
                    if *time != 0 {
                        *time = 0;
                        (self.emit)(LapTimeEvent::LapTimeRetracted {
                            session_uid: header.session_uid,
                            car_index: index as u8,
                            lap_number: number as u8,
                        });
                    }
                }
            } else if car.lap.checked_add(1) == Some(lap) && car.lap != 0 && last_ms != 0 {
                car.completed[usize::from(car.lap)] = last_ms;
                (self.emit)(LapTimeEvent::LapTimeCompleted {
                    session_uid: header.session_uid,
                    car_index: index as u8,
                    lap_number: car.lap,
                    time_ms: last_ms,
                });
            }
            car.lap = lap;
            car.current_ms = current_ms;
        }
    }
}

impl<F: FnMut(LapTimeEvent) + Send + 'static> Consumer for LapTimeTracker<F> {
    fn name(&self) -> String {
        "lap-times-all-cars".into()
    }

    fn on_packet(&mut self, _ts_ns: u64, header: &PacketHeader, id: PacketId, payload: &[u8]) {
        if id == PacketId::LapData {
            self.observe(header, payload);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};

    fn packet(uid: u64, cars: &[(usize, u8, u32, u32)]) -> Vec<u8> {
        let mut bytes = vec![0; PacketId::LapData.size()];
        bytes[0..2].copy_from_slice(&2025u16.to_le_bytes());
        bytes[5] = 1;
        bytes[6] = PacketId::LapData as u8;
        bytes[7..15].copy_from_slice(&uid.to_le_bytes());
        for &(index, lap, current, last) in cars {
            let offset = HEADER_LEN + index * CAR_LAP_LEN;
            bytes[offset..offset + 4].copy_from_slice(&last.to_le_bytes());
            bytes[offset + 4..offset + 8].copy_from_slice(&current.to_le_bytes());
            bytes[offset + LAP_NUMBER_OFFSET] = lap;
        }
        bytes
    }

    fn feed(tracker: &mut impl Consumer, bytes: &[u8]) {
        let (header, id) = f1_wire::inspect(bytes).unwrap();
        tracker.on_packet(0, &header, id, bytes);
    }

    #[test]
    fn tracks_each_car_only_after_a_consecutive_observed_transition() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let output = Arc::clone(&events);
        let mut tracker = LapTimeTracker::new(move |event| output.lock().unwrap().push(event));
        feed(&mut tracker, &packet(1, &[(0, 1, 5000, 0), (21, 3, 6000, 0)]));
        feed(&mut tracker, &packet(1, &[(0, 2, 1000, 90000), (21, 4, 1000, 100000)]));
        feed(&mut tracker, &packet(1, &[(0, 2, 2000, 90000), (21, 6, 1000, 98000)]));
        assert_eq!(
            *events.lock().unwrap(),
            vec![
                LapTimeEvent::LapSessionStarted { session_uid: 1 },
                LapTimeEvent::LapTimeCompleted { session_uid: 1, car_index: 0, lap_number: 1, time_ms: 90000 },
                LapTimeEvent::LapTimeCompleted { session_uid: 1, car_index: 21, lap_number: 3, time_ms: 100000 },
            ]
        );
    }

    #[test]
    fn retracts_rewound_laps_and_resets_on_session_change() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let output = Arc::clone(&events);
        let mut tracker = LapTimeTracker::new(move |event| output.lock().unwrap().push(event));
        feed(&mut tracker, &packet(1, &[(2, 1, 5000, 0)]));
        feed(&mut tracker, &packet(1, &[(2, 2, 1000, 90000)]));
        feed(&mut tracker, &packet(1, &[(2, 1, 4000, 0)]));
        feed(&mut tracker, &packet(1, &[(2, 2, 1000, 91000)]));
        feed(&mut tracker, &packet(2, &[(2, 2, 1000, 91000)]));
        assert_eq!(
            *events.lock().unwrap(),
            vec![
                LapTimeEvent::LapSessionStarted { session_uid: 1 },
                LapTimeEvent::LapTimeCompleted { session_uid: 1, car_index: 2, lap_number: 1, time_ms: 90000 },
                LapTimeEvent::LapTimeRetracted { session_uid: 1, car_index: 2, lap_number: 1 },
                LapTimeEvent::LapTimeCompleted { session_uid: 1, car_index: 2, lap_number: 1, time_ms: 91000 },
                LapTimeEvent::LapSessionStarted { session_uid: 2 },
            ]
        );
    }

    #[test]
    fn rejects_unsupported_versions_and_zero_lap_times() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let output = Arc::clone(&events);
        let mut tracker = LapTimeTracker::new(move |event| output.lock().unwrap().push(event));
        let mut unsupported = packet(1, &[(0, 1, 1000, 0)]);
        unsupported[5] = 2;
        feed(&mut tracker, &unsupported);
        assert!(events.lock().unwrap().is_empty());
        feed(&mut tracker, &packet(1, &[(0, 1, 1000, 0)]));
        feed(&mut tracker, &packet(1, &[(0, 2, 1000, 0)]));
        assert_eq!(*events.lock().unwrap(), vec![LapTimeEvent::LapSessionStarted { session_uid: 1 }]);
    }
}
