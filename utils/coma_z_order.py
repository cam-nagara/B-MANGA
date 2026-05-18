"""コマ実体の重なり順 Z 座標."""

from __future__ import annotations

COMA_PLANE_BASE_Z_M = 0.01
COMA_STACK_STEP_Z_M = 0.004
COMA_WHITE_MARGIN_OFFSET_Z_M = 0.001
COMA_BORDER_OFFSET_Z_M = 0.002


def stack_index(coma) -> int:
    try:
        return max(0, int(getattr(coma, "z_order", 0) or 0))
    except Exception:  # noqa: BLE001
        return 0


def plane_z(coma) -> float:
    return COMA_PLANE_BASE_Z_M + stack_index(coma) * COMA_STACK_STEP_Z_M


def white_margin_z(coma) -> float:
    return plane_z(coma) + COMA_WHITE_MARGIN_OFFSET_Z_M


def border_z(coma) -> float:
    return plane_z(coma) + COMA_BORDER_OFFSET_Z_M
