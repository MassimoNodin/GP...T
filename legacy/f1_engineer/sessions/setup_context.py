from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from ..udp.car_setups import CarSetupData


SetupObservationStatus = Literal["observed", "unavailable", "truncated"]


@dataclass(frozen=True, slots=True)
class PlayerCarSetupObservation:
    """Reported player Car Setups evidence from an admitted assembled frame."""

    session_uid: int
    frame_ordinal: int
    frame_identifier: int
    overall_frame_identifier: int
    packet_format: int | None
    association_epoch: int
    association_scope_assessable: bool
    player_car_index: int | None
    session_time_s: float | None
    status: SetupObservationStatus
    reason: str | None
    setup: CarSetupData | None
    next_front_wing_value: float | None
    source_packet_count: int

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["setup"] = asdict(self.setup) if self.setup is not None else None
        return value
