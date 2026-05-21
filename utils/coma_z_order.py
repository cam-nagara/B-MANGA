"""コマ実体の重なり順 Z 座標."""

from __future__ import annotations

COMA_PLANE_BASE_Z_M = 0.01
COMA_STACK_STEP_Z_M = 0.004
COMA_CONTENT_MIN_OFFSET_Z_M = 0.00015
COMA_CONTENT_MAX_OFFSET_Z_M = 0.00085
COMA_WHITE_MARGIN_OFFSET_Z_M = 0.001
COMA_BORDER_OFFSET_Z_M = 0.002


def stack_index(coma) -> int:
    try:
        return max(0, int(getattr(coma, "z_order", 0) or 0))
    except Exception:  # noqa: BLE001
        return 0


def plane_z(coma) -> float:
    return COMA_PLANE_BASE_Z_M + stack_index(coma) * COMA_STACK_STEP_Z_M


def content_z(coma, rank: int, count: int) -> float:
    count = max(1, int(count))
    rank = max(1, min(int(rank), count))
    span = max(0.0, COMA_CONTENT_MAX_OFFSET_Z_M - COMA_CONTENT_MIN_OFFSET_Z_M)
    offset = COMA_CONTENT_MIN_OFFSET_Z_M + span * (rank / (count + 1))
    return plane_z(coma) + offset


def content_behind_plane_z(coma, rank: int, count: int) -> float:
    count = max(1, int(count))
    rank = max(1, min(int(rank), count))
    span = max(0.0, COMA_CONTENT_MAX_OFFSET_Z_M - COMA_CONTENT_MIN_OFFSET_Z_M)
    offset = COMA_CONTENT_MIN_OFFSET_Z_M + span * (rank / (count + 1))
    return plane_z(coma) - offset


def white_margin_z(coma) -> float:
    return plane_z(coma) + COMA_WHITE_MARGIN_OFFSET_Z_M


def border_z(coma) -> float:
    return plane_z(coma) + COMA_BORDER_OFFSET_Z_M
