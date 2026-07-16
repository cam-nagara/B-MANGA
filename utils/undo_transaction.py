"""B-MANGA の手動 Undo 境界を一貫させる小さな共通部品.

モーダル操作はドラッグ距離ではなく、操作開始時と確定時の実データを比較して
履歴化する。これにより微小な編集を取りこぼさず、いったん動かして元へ戻した
操作では空の Undo ステップを作らない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
import math
from numbers import Real


DEFAULT_ABS_TOL = 1.0e-7


def states_differ(before, after, *, abs_tol: float = DEFAULT_ABS_TOL) -> bool:
    """入れ子のスナップショットを比較し、実質的な差があれば ``True``.

    Blender の座標・角度に含まれる丸め誤差だけで空の Undo が増えないよう、
    実数は絶対誤差つきで比較する。bool は数値として扱わない。
    """

    if before is after:
        return False
    if isinstance(before, bool) or isinstance(after, bool):
        return type(before) is not type(after) or before != after
    if isinstance(before, Real) and isinstance(after, Real):
        return not math.isclose(
            float(before),
            float(after),
            rel_tol=0.0,
            abs_tol=max(0.0, float(abs_tol)),
        )
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        if set(before) != set(after):
            return True
        return any(
            states_differ(before[key], after[key], abs_tol=abs_tol)
            for key in before
        )
    if isinstance(before, Set) and isinstance(after, Set):
        return before != after
    if (
        isinstance(before, Sequence)
        and not isinstance(before, (str, bytes, bytearray))
        and isinstance(after, Sequence)
        and not isinstance(after, (str, bytes, bytearray))
    ):
        if len(before) != len(after):
            return True
        return any(
            states_differ(left, right, abs_tol=abs_tol)
            for left, right in zip(before, after)
        )
    return type(before) is not type(after) or before != after


def push_undo(message: str, *, logger=None) -> bool:
    """現在の確定済み状態を Blender Undo 履歴へ1回だけ追加する."""

    try:
        import bpy

        result = bpy.ops.ed.undo_push(message=str(message))
        if "FINISHED" in result:
            return True
        if logger is not None:
            logger.error("undo_push did not finish: %s (%s)", message, result)
    except Exception:  # noqa: BLE001
        if logger is not None:
            logger.exception("undo_push failed: %s", message)
    return False
