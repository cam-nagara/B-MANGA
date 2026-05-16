"""B-Name と B-Name-Render の共通設定を双方向同期する.

両アドオンは同じ Scene に、同じ意味の設定を別名で二重に登録している
(魚眼FOV / ページ画像スケール / 魚眼モード / 縮小モード / 縮小率 /
元解像度X / 元解像度Y)。本モジュールは B-Name 側に常駐し、短い周期の
タイマーで「前回値からどちらが変わったか」を判定して、変わった側の値を
もう片方へ反映する。値が一致していれば何もしない。

設計判断:
- msgbus は ``--background`` で発火せず、更新コールバックの無い不活性な
  プロパティでは ``depsgraph_update_post`` も発火しないため、どちらも
  単独では信頼できない。タイマー巡回 + 前回値スナップショット差分なら、
  更新コールバックの有無やプロパティの種類に依存せず確実に同期できる。
- 同期は B-Name 側だけで完結させ、B-Name-Render のコードは一切変更
  しない (分離作業中のため)。参照は Scene 上の属性名のみ。
- ファイルを開いた直後は B-Name 側の値を正として B-Name-Render へ揃える。
"""

from __future__ import annotations

import bpy
from bpy.app.handlers import persistent

from . import log

_logger = log.get_logger(__name__)

_TICK = 0.25  # 秒。設定値の同期なのでこの程度の遅延で十分。
_FLOAT_EPS = 1e-6
_BUSY = False

# field名 -> 前回同期時の共通値
_snapshot: dict[str, object] = {}
# スナップショットはアクティブシーン 1 つぶんの履歴。シーンが切り替わったら
# 前シーンの記録で誤った向きに同期しないよう破棄し、新シーンは読込時と同じく
# B-Name 側を正として扱う。
_last_scene_ptr: int | None = None

# (キー名, kind, bname=(pointer, prop), render=(pointer, prop))
#   pointer=None  → scene 直下、 pointer="xxx" → scene.xxx.<prop>
_PAIRS: tuple[tuple, ...] = (
    ("fisheye_fov", "float",
     (None, "bname_coma_camera_fisheye_fov"),
     (None, "fisheye_fov")),
    ("bg_images_scale", "float",
     ("bname_coma_camera_settings", "bg_images_scale"),
     ("my_tool", "bg_images_scale")),
    ("fisheye_layout_mode", "bool",
     (None, "bname_coma_camera_fisheye_layout_mode"),
     (None, "fisheye_layout_mode")),
    ("reduction_mode", "bool",
     (None, "bname_coma_camera_reduction_mode"),
     (None, "reduction_mode")),
    ("preview_scale_percentage", "float",
     (None, "bname_coma_camera_preview_scale_percentage"),
     (None, "preview_scale_percentage")),
    ("original_resolution_x", "int",
     (None, "bname_coma_camera_original_resolution_x"),
     (None, "original_resolution_x")),
    ("original_resolution_y", "int",
     (None, "bname_coma_camera_original_resolution_y"),
     (None, "original_resolution_y")),
)


def _holder(scene, pointer):
    if pointer is None:
        return scene
    return getattr(scene, pointer, None)


def _available(scene, spec) -> bool:
    pointer, prop = spec
    holder = _holder(scene, pointer)
    return holder is not None and hasattr(holder, prop)


def _get(scene, spec):
    pointer, prop = spec
    return getattr(_holder(scene, pointer), prop)


def _coerce(kind: str, value):
    if kind == "int":
        return int(round(float(value)))
    if kind == "bool":
        return bool(value)
    return float(value)


def _set(scene, spec, kind: str, value) -> None:
    pointer, prop = spec
    setattr(_holder(scene, pointer), prop, _coerce(kind, value))


def _equal(kind: str, a, b) -> bool:
    if a is None or b is None:
        return a is b
    if kind == "float":
        try:
            return abs(float(a) - float(b)) <= _FLOAT_EPS
        except (TypeError, ValueError):
            return a == b
    if kind == "int":
        try:
            return int(round(float(a))) == int(round(float(b)))
        except (TypeError, ValueError):
            return a == b
    return bool(a) == bool(b)


def _reconcile(scene, *, force_bname_canonical: bool = False) -> None:
    """共通設定を 1 回ぶん突き合わせて同期する.

    ``force_bname_canonical`` のときは差分判定をせず B-Name 側を正とする
    (ファイル読込直後など)。
    """
    global _BUSY, _last_scene_ptr
    if scene is None or _BUSY:
        return
    try:
        ptr = scene.as_pointer()
    except (AttributeError, ReferenceError):
        ptr = None
    if ptr != _last_scene_ptr:
        # アクティブシーンが変わった (or 初回)。前シーンの履歴は使わず、
        # この新シーンは B-Name 側を正として一度揃える。
        _snapshot.clear()
        _last_scene_ptr = ptr
        force_bname_canonical = True
    _BUSY = True
    try:
        for name, kind, b_spec, r_spec in _PAIRS:
            if not _available(scene, b_spec) or not _available(scene, r_spec):
                continue
            try:
                b_val = _get(scene, b_spec)
                r_val = _get(scene, r_spec)
                if _equal(kind, b_val, r_val):
                    _snapshot[name] = b_val
                    continue
                snap = _snapshot.get(name)
                b_changed = snap is None or not _equal(kind, b_val, snap)
                r_changed = snap is None or not _equal(kind, r_val, snap)
                if force_bname_canonical or (b_changed and r_changed) or (
                    b_changed and not r_changed
                ):
                    _set(scene, r_spec, kind, b_val)
                    _snapshot[name] = _get(scene, b_spec)
                elif r_changed and not b_changed:
                    _set(scene, b_spec, kind, r_val)
                    _snapshot[name] = _get(scene, b_spec)
                else:
                    _snapshot[name] = b_val
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "cross-addon settings sync failed: %s", name
                )
    finally:
        _BUSY = False


def reconcile_now(*, force_bname_canonical: bool = False) -> None:
    _reconcile(
        getattr(bpy.context, "scene", None),
        force_bname_canonical=force_bname_canonical,
    )


def _timer() -> float:
    try:
        _reconcile(getattr(bpy.context, "scene", None))
    except Exception:  # noqa: BLE001
        _logger.exception("cross-addon settings timer failed")
    return _TICK


@persistent
def _on_load_post(_filepath) -> None:
    global _last_scene_ptr
    _snapshot.clear()
    _last_scene_ptr = None
    try:
        _reconcile(
            getattr(bpy.context, "scene", None),
            force_bname_canonical=True,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("cross-addon settings load reconcile failed")


def register() -> None:
    global _last_scene_ptr
    _snapshot.clear()
    _last_scene_ptr = None
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    try:
        if not bpy.app.timers.is_registered(_timer):
            bpy.app.timers.register(_timer, first_interval=_TICK)
    except Exception:  # noqa: BLE001
        _logger.debug("cross-addon settings timer not scheduled")


def unregister() -> None:
    global _last_scene_ptr
    _last_scene_ptr = None
    try:
        if bpy.app.timers.is_registered(_timer):
            bpy.app.timers.unregister(_timer)
    except Exception:  # noqa: BLE001
        pass
    if _on_load_post in bpy.app.handlers.load_post:
        try:
            bpy.app.handlers.load_post.remove(_on_load_post)
        except ValueError:
            pass
    _snapshot.clear()
