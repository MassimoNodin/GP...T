from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..udp.lap_data import CarLapData
from ..udp.models import PacketFormat
from .context import GameMode, RuleSet, SessionContext, SessionType


class LapDisposition(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class SessionContextSegment:
    from_frame_identifier: int
    context: SessionContext | None

    def to_dict(self) -> dict[str, object]:
        return {
            "from_frame_identifier": self.from_frame_identifier,
            "context": self.context.to_dict() if self.context is not None else None,
        }


@dataclass(frozen=True, slots=True)
class LapObservation:
    session_uid: int
    frame_identifier: int
    session_time_s: float
    car_index: int
    data: CarLapData
    session_context: SessionContext | None
    context_timeline: tuple[SessionContextSegment, ...]


@dataclass(frozen=True, slots=True)
class LapAttempt:
    attempt_id: str
    attempt_number: int
    session_uid: int
    car_index: int
    lap_number: int
    disposition: LapDisposition
    start_frame_identifier: int
    end_frame_identifier: int | None
    start_session_time_s: float
    end_session_time_s: float | None
    lap_time_ms: int | None
    game_valid: bool | None
    start_observed: bool
    pit_encountered: bool
    sample_count: int
    context_segments: tuple[SessionContextSegment, ...]
    exclusion_reasons: tuple[str, ...]
    reference_eligible: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "session_uid": self.session_uid,
            "car_index": self.car_index,
            "lap_number": self.lap_number,
            "disposition": self.disposition.value,
            "start_frame_identifier": self.start_frame_identifier,
            "end_frame_identifier": self.end_frame_identifier,
            "start_session_time_s": self.start_session_time_s,
            "end_session_time_s": self.end_session_time_s,
            "lap_time_ms": self.lap_time_ms,
            "game_valid": self.game_valid,
            "start_observed": self.start_observed,
            "pit_encountered": self.pit_encountered,
            "sample_count": self.sample_count,
            "context_segments": [segment.to_dict() for segment in self.context_segments],
            "exclusion_reasons": list(self.exclusion_reasons),
            "reference_eligible": self.reference_eligible,
        }


@dataclass(slots=True)
class _ActiveLap:
    attempt_number: int
    lap_number: int
    start_frame_identifier: int
    start_session_time_s: float
    start_observed: bool
    sample_count: int = 0
    invalid_seen: bool = False
    pit_encountered: bool = False
    last_current_lap_time_ms: int = 0
    end_frame_identifier: int | None = None
    end_session_time_s: float | None = None
    context_segments: list[SessionContextSegment] = field(default_factory=list)


class LapTracker:
    """Track player lap attempts without assuming every observed lap is usable."""

    def __init__(self) -> None:
        self.current_session_uid: int | None = None
        self.attempts: list[LapAttempt] = []
        self._attempt_number_by_car: dict[int, int] = {}
        self._previous: dict[int, LapObservation] = {}
        self._active: dict[int, _ActiveLap] = {}

    def active_start_frame(self, car_index: int) -> int | None:
        active = self._active.get(car_index)
        return active.start_frame_identifier if active is not None else None

    def active_attempt_id(self, car_index: int) -> str | None:
        active = self._active.get(car_index)
        if active is None or self.current_session_uid is None:
            return None
        return f"{self.current_session_uid}:{car_index}:{active.attempt_number}"

    def drain_attempts(self) -> tuple[LapAttempt, ...]:
        """Release finalized metadata after an importer has consumed it."""
        attempts = tuple(self.attempts)
        self.attempts.clear()
        return attempts

    def start_session(self, session_uid: int) -> None:
        if self.current_session_uid == session_uid:
            return
        if self.current_session_uid is not None:
            self.end_session(self.current_session_uid)
        self.current_session_uid = session_uid
        self._attempt_number_by_car.clear()
        self._previous.clear()
        self._active.clear()

    def end_session(
        self,
        session_uid: int,
        *,
        reason: str = "session_ended_before_lap_completion",
    ) -> tuple[LapAttempt, ...]:
        if self.current_session_uid != session_uid:
            return ()
        start = len(self.attempts)
        for car_index in tuple(self._active):
            self._finalize(
                car_index,
                LapDisposition.PARTIAL,
                reason=reason,
            )
        self._previous.clear()
        self._attempt_number_by_car.clear()
        self.current_session_uid = None
        return tuple(self.attempts[start:])

    def close_segment(self, session_uid: int, *, reason: str) -> tuple[LapAttempt, ...]:
        if self.current_session_uid != session_uid:
            return ()
        start = len(self.attempts)
        for car_index in tuple(self._active):
            self._finalize(car_index, LapDisposition.PARTIAL, reason=reason)
        self._previous.clear()
        return tuple(self.attempts[start:])

    def observe(self, observation: LapObservation) -> tuple[LapAttempt, ...]:
        if self.current_session_uid != observation.session_uid:
            self.start_session(observation.session_uid)

        start = len(self.attempts)
        car_index = observation.car_index
        previous = self._previous.get(car_index)
        active = self._active.get(car_index)
        lap_advanced = (
            previous is not None
            and observation.data.current_lap_number > previous.data.current_lap_number
        )
        lap_reset = (
            previous is not None
            and observation.data.current_lap_number < previous.data.current_lap_number
        )

        if active is not None:
            self._set_context_timeline(active, observation.context_timeline)

        if active is not None and (lap_advanced or lap_reset):
            confirmed_lap_advance = (
                observation.data.current_lap_number == previous.data.current_lap_number + 1
                and observation.data.last_lap_time_ms > 0
            )
            if lap_advanced and confirmed_lap_advance:
                self._finalize(
                    car_index,
                    LapDisposition.COMPLETED,
                    lap_time_ms=observation.data.last_lap_time_ms,
                    ended_at=observation,
                )
            else:
                self._finalize(
                    car_index,
                    LapDisposition.ABANDONED,
                    reason="lap_number_reset" if lap_reset else "unconfirmed_lap_transition",
                    ended_at=observation,
                )
            active = None

        if active is not None and previous is not None:
            regression = previous.data.lap_distance_m - observation.data.lap_distance_m
            track_length = _track_length(active.context_segments)
            distance_threshold = max(100.0, track_length * 0.05)
            timer_rewound = (
                previous.data.current_lap_time_ms >= 1_000
                and observation.data.current_lap_time_ms
                < previous.data.current_lap_time_ms - 1_000
            )
            if regression > distance_threshold or timer_rewound:
                reason = "distance_discontinuity" if regression > distance_threshold else "lap_timer_rewind"
                self._finalize(
                    car_index,
                    LapDisposition.ABANDONED,
                    reason=reason,
                    ended_at=observation,
                )
                active = None

        if active is not None:
            self._update_active(active, observation)
        elif _can_start(observation.data):
            line_crossing = (
                previous is not None
                and previous.data.current_lap_number == observation.data.current_lap_number
                and previous.data.lap_distance_m < 0 <= observation.data.lap_distance_m
            )
            confirmed_lap_advance = (
                previous is not None
                and observation.data.current_lap_number
                == previous.data.current_lap_number + 1
                and observation.data.last_lap_time_ms > 0
            )
            complete_start = line_crossing or confirmed_lap_advance
            self._start_attempt(observation, start_observed=complete_start)

        self._previous[car_index] = observation
        return tuple(self.attempts[start:])

    def finish(self) -> tuple[LapAttempt, ...]:
        if self.current_session_uid is None:
            return ()
        return self.end_session(
            self.current_session_uid, reason="capture_ended_before_lap_completion"
        )

    def _start_attempt(self, observation: LapObservation, *, start_observed: bool) -> None:
        car_index = observation.car_index
        attempt_number = self._attempt_number_by_car.get(car_index, 0) + 1
        self._attempt_number_by_car[car_index] = attempt_number
        active = _ActiveLap(
            attempt_number=attempt_number,
            lap_number=observation.data.current_lap_number,
            start_frame_identifier=observation.frame_identifier,
            start_session_time_s=observation.session_time_s,
            start_observed=start_observed,
        )
        self._active[car_index] = active
        self._update_active(active, observation)

    @staticmethod
    def _update_active(active: _ActiveLap, observation: LapObservation) -> None:
        data = observation.data
        active.sample_count += 1
        active.last_current_lap_time_ms = data.current_lap_time_ms
        if data.current_lap_invalid_id != 0:
            active.invalid_seen = True
        if data.pit_status_id != 0 or data.driver_status_id in (2, 3):
            active.pit_encountered = True
        if not active.context_segments or (
            active.context_segments[-1].context != observation.session_context
        ):
            active.context_segments.append(
                SessionContextSegment(
                    from_frame_identifier=observation.frame_identifier,
                    context=observation.session_context,
                )
            )
        active.end_frame_identifier = observation.frame_identifier
        active.end_session_time_s = observation.session_time_s

    @staticmethod
    def _set_context_timeline(
        active: _ActiveLap,
        context_timeline: tuple[SessionContextSegment, ...],
    ) -> None:
        normalized: list[SessionContextSegment] = []
        for segment in context_timeline:
            if not normalized or normalized[-1].context != segment.context:
                normalized.append(segment)
        active.context_segments[:] = normalized

    def _finalize(
        self,
        car_index: int,
        disposition: LapDisposition,
        *,
        lap_time_ms: int | None = None,
        reason: str | None = None,
        ended_at: LapObservation | None = None,
    ) -> None:
        active = self._active.pop(car_index)
        end_frame = (
            ended_at.frame_identifier
            if ended_at is not None
            else active.end_frame_identifier
        )
        end_time = (
            ended_at.session_time_s
            if ended_at is not None
            else active.end_session_time_s
        )
        game_valid: bool | None = (
            not active.invalid_seen if disposition is LapDisposition.COMPLETED else None
        )
        reasons: list[str] = []
        if disposition is not LapDisposition.COMPLETED:
            reasons.append("lap_not_completed")
        if reason is not None:
            reasons.append(reason)
        if active.invalid_seen:
            reasons.append("game_marked_invalid")
        if not active.start_observed:
            reasons.append("lap_start_not_observed")
        if active.pit_encountered:
            reasons.append("pit_or_in_lap")
        contexts = [segment.context for segment in active.context_segments]
        known_contexts = [context for context in contexts if context is not None]
        context_is_unknown = not contexts or len(known_contexts) != len(contexts) or any(
            not _has_known_mode(context) for context in known_contexts
        )
        if context_is_unknown:
            reasons.append("session_context_unknown")
        elif known_contexts:
            if any(not _is_time_trial_context(context) for context in known_contexts):
                reasons.append("mode_policy_not_implemented")
            if any(context.track_name is None for context in known_contexts):
                reasons.append("track_unknown")
            if any(
                _mode_track_signature(context) != _mode_track_signature(known_contexts[0])
                for context in known_contexts[1:]
            ):
                reasons.append("session_mode_or_track_changed")
        if disposition is LapDisposition.COMPLETED and (lap_time_ms is None or lap_time_ms <= 0):
            reasons.append("lap_time_unavailable")

        eligible = disposition is LapDisposition.COMPLETED and not reasons
        session_uid = self.current_session_uid or 0
        attempt = LapAttempt(
            attempt_id=f"{session_uid}:{car_index}:{active.attempt_number}",
            attempt_number=active.attempt_number,
            session_uid=session_uid,
            car_index=car_index,
            lap_number=active.lap_number,
            disposition=disposition,
            start_frame_identifier=active.start_frame_identifier,
            end_frame_identifier=end_frame,
            start_session_time_s=active.start_session_time_s,
            end_session_time_s=end_time,
            lap_time_ms=lap_time_ms,
            game_valid=game_valid,
            start_observed=active.start_observed,
            pit_encountered=active.pit_encountered,
            sample_count=active.sample_count,
            context_segments=tuple(active.context_segments),
            exclusion_reasons=tuple(dict.fromkeys(reasons)),
            reference_eligible=eligible,
        )
        self.attempts.append(attempt)


def _can_start(data: CarLapData) -> bool:
    return (
        data.lap_distance_m >= 0
        and data.pit_status_id == 0
        and data.driver_status_id in (1, 4)
        and data.result_status_id == 2
    )


def _track_length(segments: list[SessionContextSegment]) -> float:
    if not segments or segments[-1].context is None:
        return 0.0
    return float(segments[-1].context.track_length_m)


def _is_time_trial_context(context: SessionContext) -> bool:
    return (
        context.session_type is SessionType.TIME_TRIAL
        and context.game_mode is GameMode.TIME_TRIAL
        and context.rule_set is RuleSet.TIME_TRIAL
        and context.packet_format is PacketFormat.F1_25
    )


def _has_known_mode(context: SessionContext) -> bool:
    return (
        context.session_type is not None
        and context.session_type is not SessionType.UNKNOWN
        and context.game_mode is not None
        and context.rule_set is not None
    )


def _mode_track_signature(context: SessionContext) -> tuple[object, ...]:
    return (
        context.packet_format,
        context.session_type,
        context.game_mode,
        context.rule_set,
        context.track_id,
    )
