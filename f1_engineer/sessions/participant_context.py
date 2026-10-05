from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from ..udp.participants import ParticipantData


ParticipantObservationStatus = Literal["observed", "unavailable", "truncated"]


@dataclass(frozen=True, slots=True)
class PlayerParticipantObservation:
    """Reported player Participant evidence from one admitted assembled frame."""

    session_uid: int
    frame_ordinal: int
    frame_identifier: int
    overall_frame_identifier: int
    packet_format: int | None
    association_epoch: int
    association_scope_assessable: bool
    player_car_index: int | None
    session_time_s: float | None
    status: ParticipantObservationStatus
    reason: str | None
    active_car_count: int | None
    participant: ParticipantData | None
    source_packet_count: int

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["participant"] = (
            asdict(self.participant) if self.participant is not None else None
        )
        return value
