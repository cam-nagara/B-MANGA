"""B-MANGA Line: camera distance button and display checkboxes."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, line_only_display, outline_setup, panels  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for datablocks in (bpy.data.meshes, bpy.data.materials, bpy.data.node_groups):
        for item in list(datablocks):
            if item.users == 0:
                datablocks.remove(item)


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def _select(active: bpy.types.Object, objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def _line_modifiers(obj: bpy.types.Object):
    return list(core.iter_line_modifiers(obj))


def _assert_line_visibility(obj: bpy.types.Object, visible: bool) -> None:
    assert bool(obj.bmanga_line_settings.lines_visible) is visible
    assert bool(obj.get(core.PROP_LINES_HIDDEN, False)) is (not visible)
    for mod in _line_modifiers(obj):
        assert bool(mod.show_viewport) is visible, (obj.name, mod.name, mod.show_viewport)
        assert bool(mod.show_render) is visible, (obj.name, mod.name, mod.show_render)


def _assert_distance_button(active: bpy.types.Object, other: bpy.types.Object) -> None:
    _select(active, [active, other])
    assert bpy.ops.bmanga_line.reset_camera_ref("EXEC_DEFAULT") == {"FINISHED"}
    expected = (
        bpy.context.scene.camera.matrix_world.translation
        - active.matrix_world.translation
    ).length
    for obj in (active, other):
        assert math.isclose(
            obj.bmanga_line_settings.line_width_reference_distance,
            expected,
            rel_tol=0.0,
            abs_tol=1.0e-6,
        ), obj.name


def _assert_visibility_checkboxes(active: bpy.types.Object, other: bpy.types.Object) -> None:
    _select(active, [active, other])
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
    _select(active, [active, other])
    for obj in (active, other):
        _assert_line_visibility(obj, True)

    active.bmanga_line_settings.lines_visible = False
    for obj in (active, other):
        _assert_line_visibility(obj, False)

    active.bmanga_line_settings.lines_visible = True
    for obj in (active, other):
        _assert_line_visibility(obj, True)

    assert bpy.ops.bmanga_line.set_visibility("EXEC_DEFAULT", visible=False) == {"FINISHED"}
    for obj in (active, other):
        _assert_line_visibility(obj, False)
    active.bmanga_line_settings.lines_visible = True


def _assert_line_only_checkbox(active: bpy.types.Object, other: bpy.types.Object) -> None:
    scene = bpy.context.scene
    _select(active, [active, other])
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}

    old_object_line_only = outline_setup.set_line_only
    old_set_line_visibility = core.set_line_visibility
    outline_setup.set_line_only = lambda _obj, _enabled: _raise_object_line_only_call()
    core.set_line_visibility = lambda _obj, _visible: _raise_object_line_only_call()
    try:
        scene.bmanga_line_line_only_visible = True
        assert bool(scene.bmanga_line_line_only_visible)
        _assert_material_outputs_in_line_only(True)
        for obj in (active, other):
            assert not bool(obj.get(core.PROP_LINE_ONLY, False)), obj.name

        scene.bmanga_line_line_only_visible = False
        assert not bool(scene.bmanga_line_line_only_visible)
        _assert_material_outputs_in_line_only(False)

        assert bpy.ops.bmanga_line.set_line_only("EXEC_DEFAULT", line_only=True) == {"FINISHED"}
        assert bool(scene.bmanga_line_line_only_visible)
        _assert_material_outputs_in_line_only(True)
        assert bpy.ops.bmanga_line.set_line_only("EXEC_DEFAULT", line_only=False) == {"FINISHED"}
        assert not bool(scene.bmanga_line_line_only_visible)
        _assert_material_outputs_in_line_only(False)
    finally:
        outline_setup.set_line_only = old_object_line_only
        core.set_line_visibility = old_set_line_visibility


def _raise_object_line_only_call():
    raise AssertionError("ラインのみ表示でオブジェクト処理が呼ばれました")


def _assert_material_outputs_in_line_only(enabled: bool) -> None:
    checked = 0
    for mat in bpy.data.materials:
        if not line_only_display.is_line_only_surface_material(mat):
            continue
        if not mat.use_nodes or mat.node_tree is None:
            assert not enabled, mat.name
            continue
        active = line_only_display.active_material_output(mat)
        assert active is not None, mat.name
        if enabled:
            assert active.name == line_only_display.LINE_ONLY_OUTPUT_NAME, mat.name
        else:
            assert active.name != line_only_display.LINE_ONLY_OUTPUT_NAME, mat.name
        checked += 1
    assert checked >= 2, checked


class _FakeUILayout:
    def __init__(self) -> None:
        self.props: list[str] = []
        self.operators: list[_FakeOperatorProps] = []
        self.separator_count = 0
        self.enabled = True
        self.scale_y = 1.0
        self.alignment = "LEFT"

    def row(self, *, align: bool = False):  # noqa: ARG002
        return self

    def column(self, *, align: bool = False):  # noqa: ARG002
        return self

    def grid_flow(self, **_kwargs):
        return self

    def operator(self, idname: str, **kwargs):
        op = _FakeOperatorProps(
            idname,
            text=str(kwargs.get("text", "") or ""),
            icon=str(kwargs.get("icon", "") or ""),
        )
        self.operators.append(op)
        return op

    def prop(self, _data, prop_name: str, **_kwargs) -> None:
        self.props.append(prop_name)

    def label(self, **_kwargs) -> None:
        return None

    def separator(self) -> None:
        self.separator_count += 1


class _FakeOperatorProps:
    def __init__(self, idname: str, *, text: str, icon: str) -> None:
        self.idname = idname
        self.text = text
        self.icon = icon


def _assert_panel_draw_uses_scene_line_only(active: bpy.types.Object, other: bpy.types.Object) -> None:
    from b_manga_line import viewport_aov

    _select(active, [active, other])
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}

    old_is_line_aov_active = viewport_aov.is_line_aov_active
    viewport_aov.is_line_aov_active = lambda _context: True
    try:
        layout = _FakeUILayout()
        panels._draw_actions(layout, bpy.context, active)
    finally:
        viewport_aov.is_line_aov_active = old_is_line_aov_active

    assert "bmanga_line_line_only_visible" in layout.props
    assert "line_only_visible" not in layout.props
    assert not bool(active.get(core.PROP_LINE_ONLY, False))


def _assert_update_buttons_are_in_line_settings(active: bpy.types.Object) -> None:
    action_layout = _FakeUILayout()
    panels._draw_actions(action_layout, bpy.context, active)
    assert not any(
        op.idname in {"bmanga_line.update_target", "bmanga_line.update_visual_target"}
        for op in action_layout.operators
    ), [op.idname for op in action_layout.operators]

    settings_layout = _FakeUILayout()
    panels._draw_line_settings(
        settings_layout,
        bpy.context,
        active.bmanga_line_settings,
    )
    assert settings_layout.operators, "ライン設定にボタンがありません"
    assert (
        settings_layout.operators[0].idname
        == "bmanga_line.update_all_visual_targets"
    ), [op.idname for op in settings_layout.operators]
    create_ops = [
        op for op in settings_layout.operators
        if op.idname == "bmanga_line.update_target"
    ]
    visual_ops = [
        op for op in settings_layout.operators
        if op.idname == "bmanga_line.update_visual_target"
    ]
    # バンプ線はモディファイア/マテリアルを生成しないため「作成」ボタンは
    # 出さない（create_ops は既存4種のまま）。「更新」ボタンのみ末尾に追加。
    assert [op.target for op in create_ops] == [
        "outline",
        "inner",
        "intersection",
        "selection",
    ], [(op.idname, getattr(op, "target", None)) for op in create_ops]
    assert [op.target for op in visual_ops] == [
        "outline",
        "inner",
        "intersection",
        "selection",
        "bump",
    ], [(op.idname, getattr(op, "target", None)) for op in visual_ops]
    assert all(op.text == "作成" for op in create_ops)
    assert all(op.text == "更新" for op in visual_ops)
    assert settings_layout.separator_count >= 5, settings_layout.separator_count


def _set_setting_without_update(settings, name: str, value) -> None:
    old = core._propagating
    core._propagating = True
    try:
        setattr(settings, name, value)
    finally:
        core._propagating = old


def _assert_subsurf_checkbox(active: bpy.types.Object, other: bpy.types.Object) -> None:
    for index, obj in enumerate((active, other), start=2):
        mod = obj.modifiers.new(f"ユーザーSubsurf_{index}", "SUBSURF")
        mod.levels = 0
        mod.render_levels = index

    _select(active, [active, other])
    active.bmanga_line_settings.match_subsurf_viewport_to_render = True
    for obj in (active, other):
        for mod in obj.modifiers:
            if mod.type == "SUBSURF":
                assert int(mod.levels) == 0, (obj.name, mod.name)
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
    for obj in (active, other):
        for mod in obj.modifiers:
            if mod.type == "SUBSURF":
                assert int(mod.levels) == int(mod.render_levels), (obj.name, mod.name)

    for obj in (active, other):
        _set_setting_without_update(
            obj.bmanga_line_settings,
            "match_subsurf_viewport_to_render",
            False,
        )
        for mod in obj.modifiers:
            if mod.type == "SUBSURF":
                mod.levels = int(mod.render_levels)
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
    for obj in (active, other):
        for mod in obj.modifiers:
            if mod.type == "SUBSURF":
                assert int(mod.levels) == 0, (obj.name, mod.name, mod.levels)

    for obj in (active, other):
        _set_setting_without_update(
            obj.bmanga_line_settings,
            "match_subsurf_viewport_to_render",
            True,
        )
        for mod in obj.modifiers:
            if mod.type == "SUBSURF":
                mod.levels = 0
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
    for obj in (active, other):
        for mod in obj.modifiers:
            if mod.type == "SUBSURF":
                assert int(mod.levels) == int(mod.render_levels), (obj.name, mod.name)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        bpy.ops.object.camera_add(location=(0.0, 0.0, 5.0))
        bpy.context.scene.camera = bpy.context.object
        active = _make_cube("BML_UI_active", (0.0, 0.0, 1.0))
        other = _make_cube("BML_UI_other", (2.0, 0.0, 0.0))

        _assert_distance_button(active, other)
        _assert_visibility_checkboxes(active, other)
        _assert_line_only_checkbox(active, other)
        _assert_panel_draw_uses_scene_line_only(active, other)
        _assert_update_buttons_are_in_line_settings(active)
        _assert_subsurf_checkbox(active, other)
        print("BMANGA_LINE_UI_CONTROLS_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
