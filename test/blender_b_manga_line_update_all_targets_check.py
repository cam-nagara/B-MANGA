"""B-MANGA Line: update-all button refreshes every line target and auto subsurf."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    core,
    intersection_lines,
    presets,
    subdivision_lod,
    update_state,
)


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


def _make_triangulated_cube(
    name: str,
    location: tuple[float, float, float],
) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(
        [
            (-0.5, -0.5, -0.5),
            (0.5, -0.5, -0.5),
            (0.5, 0.5, -0.5),
            (-0.5, 0.5, -0.5),
            (-0.5, -0.5, 0.5),
            (0.5, -0.5, 0.5),
            (0.5, 0.5, 0.5),
            (-0.5, 0.5, 0.5),
        ],
        [],
        [
            (0, 1, 2), (0, 2, 3),
            (4, 6, 5), (4, 7, 6),
            (0, 4, 5), (0, 5, 1),
            (1, 5, 6), (1, 6, 2),
            (2, 6, 7), (2, 7, 3),
            (3, 7, 4), (3, 4, 0),
        ],
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    return obj


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def _enable_lines(obj: bpy.types.Object) -> None:
    obj.data.materials.append(bpy.data.materials.new(obj.name + "_surface"))
    settings = obj.bmanga_line_settings
    _set_without_update(settings, "inner_line_enabled", True)
    _set_without_update(settings, "intersection_enabled", True)
    _set_without_update(settings, "use_inner_line_creation_limit", True)
    _set_without_update(settings, "inner_line_creation_max_distance", 10.0)
    _set_without_update(settings, "use_intersection_creation_limit", True)
    _set_without_update(settings, "intersection_creation_max_distance", 10.0)


def _select_all(active: bpy.types.Object, objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def _auto_subsurf(obj: bpy.types.Object) -> bpy.types.Modifier | None:
    for mod in obj.modifiers:
        if subdivision_lod.is_auto_subsurf_modifier(mod):
            return mod
    return None


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        _make_camera()

        tri_cube = _make_triangulated_cube("BML_update_all_tri", (0.0, 0.0, -5.0))
        quad_cube = _make_cube("BML_update_all_quad", (0.35, 0.0, -5.0))
        objects = [tri_cube, quad_cube]
        for obj in objects:
            _enable_lines(obj)

        presets._update_view_layer(bpy.context)
        for obj in objects:
            assert presets.apply_line_settings(
                obj,
                bpy.context,
                refresh_scene=False,
                transforms_fresh=True,
            ), obj.name
        intersection_lines.refresh_scene_intersections(bpy.context.scene)

        # 前提: 自動サブディビジョンOFFで作成済み。三角面のまま・Subsurfなし
        assert all(len(poly.vertices) == 3 for poly in tri_cube.data.polygons)
        assert _auto_subsurf(tri_cube) is None
        assert tri_cube.modifiers.get(core.MODIFIER_NAME) is not None
        assert tri_cube.modifiers.get(core.GN_MODIFIER_NAME) is not None
        assert any(
            any(core.iter_intersection_modifiers(obj)) for obj in objects
        ), "交差線が作成されていません"

        _select_all(tri_cube, objects)
        outline_mod = tri_cube.modifiers.get(core.MODIFIER_NAME)
        thickness_before = float(outline_mod.thickness)

        # UI操作相当: チェックONと線幅変更は更新ボタンまで反映されない
        settings = tri_cube.bmanga_line_settings
        settings.auto_subdivision_for_midpoint = True
        settings.outline_thickness = settings.outline_thickness * 2.0
        assert _auto_subsurf(tri_cube) is None
        assert all(len(poly.vertices) == 3 for poly in tri_cube.data.polygons)
        assert abs(float(outline_mod.thickness) - thickness_before) < 1.0e-9
        pending = set(update_state.pending_visual_targets(tri_cube))
        assert pending == {"outline", "inner", "intersection", "selection"}, pending

        # すべてのラインを更新
        assert bpy.ops.bmanga_line.reflect_all("EXEC_DEFAULT") == {
            "FINISHED"
        }

        for obj in objects:
            auto_mod = _auto_subsurf(obj)
            assert auto_mod is not None, obj.name
            assert (
                auto_mod.subdivision_type
                == subdivision_lod.AUTO_SUBSURF_SUBDIVISION_TYPE
            )
        assert all(
            len(poly.vertices) == 4 for poly in tri_cube.data.polygons
        ), "三角面が四角面化されていません"
        assert (
            tri_cube.data.attributes.get(subdivision_lod.CREASE_EDGE_ATTR) is not None
        )
        assert abs(float(outline_mod.thickness)) > abs(thickness_before) * 1.5, (
            thickness_before,
            float(outline_mod.thickness),
        )
        for obj in objects:
            assert not update_state.pending_visual_targets(obj), obj.name

        # チェックOFF→更新ボタンで自動Subsurfが除去される
        settings.auto_subdivision_for_midpoint = False
        assert _auto_subsurf(tri_cube) is not None
        assert bpy.ops.bmanga_line.reflect_all("EXEC_DEFAULT") == {
            "FINISHED"
        }
        for obj in objects:
            assert _auto_subsurf(obj) is None, obj.name

        # reflect_all はメッシュ選択さえあれば有効（未適用オブジェクトへの
        # 新規作成もこのボタン1つで行うため）。旧 update_all_visual_targets は
        # 既存ラインが無いと無効だったが、ボタン再編でその制約は撤廃された。
        plain = _make_cube("BML_update_all_plain", (3.0, 3.0, -5.0))
        _select_all(plain, [plain])
        assert bpy.ops.bmanga_line.reflect_all.poll()

        # 何も選択していなければ引き続き無効。
        bpy.ops.object.select_all(action="DESELECT")
        assert not bpy.ops.bmanga_line.reflect_all.poll()

        print("BMANGA_LINE_UPDATE_ALL_TARGETS_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
