"""B-MANGA Line: object-level settings lock keeps locked objects untouched.

計画書 docs/bml_bump_normal_line_and_lock_plan_2026-07-09.md の Part B-3 手順10
(a)-(e) を検証する:
  (a) 2オブジェクト選択で片方ロック→アクティブ側の設定変更がロック側へ伝搬しない
  (b) ロック中に update_all_visual_targets 実行→ロック側のモディファイアが変化しない
  (c) ロック中は pending 印が付かない
  (d) 交差ペアの片側ロックで refresh_scene_intersections を実行してもロック側所有の
      キャッシュが再構築・削除されない
  (e) 解除後は通常どおり更新される
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, intersection_lines, update_state  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _set_without_update(settings, prop_name: str, value) -> None:
    old = core._propagating
    core._propagating = True
    try:
        setattr(settings, prop_name, value)
    finally:
        core._propagating = old


def _make_camera() -> bpy.types.Object:
    scene = bpy.context.scene
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    bpy.ops.object.camera_add(location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.type = "PERSP"
    camera.data.lens = 50.0
    scene.camera = camera
    return camera


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(bpy.data.materials.new(name + "_surface"))
    return obj


def _select(active: bpy.types.Object, objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def _lock(obj: bpy.types.Object, locked: bool) -> None:
    obj.bmanga_line_settings.settings_locked = bool(locked)


def _assert_propagation_and_pending_respect_lock(
    active: bpy.types.Object, locked_other: bpy.types.Object,
) -> None:
    """(a) 伝搬しないこと + (c) ロック中は pending 印が付かないこと."""
    _select(active, [active, locked_other])
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
    _select(active, [active, locked_other])

    _lock(locked_other, True)
    locked_color_before = tuple(locked_other.bmanga_line_settings.outline_color)

    active.bmanga_line_settings.outline_color = (1.0, 0.0, 0.0, 1.0)

    assert tuple(active.bmanga_line_settings.outline_color) == (1.0, 0.0, 0.0, 1.0)
    assert tuple(locked_other.bmanga_line_settings.outline_color) == locked_color_before, (
        "アクティブ側の変更がロック側へ伝搬しています"
    )
    assert "outline" in update_state.pending_visual_targets(active), (
        "アクティブ側に更新待ち印が付いていません"
    )
    assert "outline" not in update_state.pending_visual_targets(locked_other), (
        "ロック側に更新待ち印が付いています"
    )

    # ロック中オブジェクト自身の設定変更でも pending 印は付かない
    locked_other.bmanga_line_settings.outline_thickness = (
        locked_other.bmanga_line_settings.outline_thickness * 2.0
    )
    assert not update_state.pending_visual_targets(locked_other), (
        "ロック中オブジェクト自身の変更で更新待ち印が付いています"
    )

    # mark_pending の直接呼び出しもロック中は no-op であること
    update_state.mark_pending(locked_other, ("outline",))
    assert not update_state.pending_create_targets(locked_other), (
        "mark_pending がロック中オブジェクトへ印を付けています"
    )

    # ロック解除も伝搬経路自身は妨げない（settings_locked プロパティは常に書き込み可能）
    _lock(locked_other, False)
    assert bool(locked_other.bmanga_line_settings.settings_locked) is False


def _assert_locked_excluded_from_update_all(
    active: bpy.types.Object, locked_other: bpy.types.Object,
) -> None:
    """(b) ロック中に「すべてのラインを更新」してもロック側のモディファイアが変化しないこと."""
    _select(active, [active, locked_other])
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}

    _lock(locked_other, True)

    active_mod = active.modifiers.get(core.MODIFIER_NAME)
    locked_mod = locked_other.modifiers.get(core.MODIFIER_NAME)
    assert active_mod is not None and locked_mod is not None

    active_thickness_before = float(active_mod.thickness)
    locked_thickness_before = float(locked_mod.thickness)

    # 更新フローを経ずに設定だけ変える（本来なら更新ボタンで反映されるはずの差分）
    _set_without_update(
        active.bmanga_line_settings,
        "outline_thickness",
        active.bmanga_line_settings.outline_thickness * 4.0,
    )
    _set_without_update(
        locked_other.bmanga_line_settings,
        "outline_thickness",
        locked_other.bmanga_line_settings.outline_thickness * 4.0,
    )

    _select(active, [active, locked_other])
    assert bpy.ops.bmanga_line.update_all_visual_targets("EXEC_DEFAULT") == {"FINISHED"}

    assert abs(float(active_mod.thickness) - active_thickness_before) > 1.0e-9, (
        "アクティブ側のモディファイアが更新されていません"
    )
    assert abs(float(locked_mod.thickness) - locked_thickness_before) < 1.0e-12, (
        "ロック側のモディファイアが更新中に変化しています",
        locked_thickness_before,
        float(locked_mod.thickness),
    )

    _lock(locked_other, False)


def _has_intersection_pair(obj: bpy.types.Object) -> bool:
    return any(core.iter_intersection_modifiers(obj))


def _assert_intersection_pair_lock_freezes_owner() -> None:
    """(d) ロック側所有の交差線キャッシュが削除・再構築されないこと.

    (e) 解除後は通常どおり更新される（保留していた変更が反映される）こと.
    """
    c = _make_cube("BML_lock_isect_c", (30.0, 0.0, -3.0))
    d = _make_cube("BML_lock_isect_d", (30.4, 0.0, -3.0))
    for obj in (c, d):
        settings = obj.bmanga_line_settings
        _set_without_update(settings, "intersection_enabled", True)
        _set_without_update(settings, "use_intersection_creation_limit", False)

    _select(c, [c, d])
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
    intersection_lines.refresh_scene_intersections(bpy.context.scene)

    if _has_intersection_pair(c):
        owner = c
    elif _has_intersection_pair(d):
        owner = d
    else:
        raise AssertionError("交差ペアが作成されていません（テスト前提が崩れています）")

    _lock(owner, True)

    # ロック中でなければ交差線が削除されるはずの変更（更新フローを経ずに設定だけ変える）
    _set_without_update(owner.bmanga_line_settings, "intersection_enabled", False)
    intersection_lines.refresh_scene_intersections(bpy.context.scene)
    assert _has_intersection_pair(owner), (
        "ロック中に交差線キャッシュの所有者(所有側)が削除・再構築されました"
    )

    # (e) 解除後は通常どおり更新される
    _lock(owner, False)
    intersection_lines.refresh_scene_intersections(bpy.context.scene)
    assert not _has_intersection_pair(owner), (
        "ロック解除後も保留していた変更（交差線オフ）が反映されませんでした"
    )


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        _make_camera()

        active_a = _make_cube("BML_lock_active_a", (0.0, 0.0, -3.0))
        locked_a = _make_cube("BML_lock_locked_a", (2.0, 0.0, -3.0))
        _assert_propagation_and_pending_respect_lock(active_a, locked_a)

        active_b = _make_cube("BML_lock_active_b", (5.0, 0.0, -3.0))
        locked_b = _make_cube("BML_lock_locked_b", (7.0, 0.0, -3.0))
        _assert_locked_excluded_from_update_all(active_b, locked_b)

        _assert_intersection_pair_lock_freezes_owner()

        print("BMANGA_LINE_SETTINGS_LOCK_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
