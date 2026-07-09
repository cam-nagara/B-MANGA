"""B-MANGA Line: 魚眼（パノラマ）カメラでも線幅が発散しないことを検証する.

回帰対象: 視野角180°の魚眼で tan(半画角) が発散し、全ライン種の幅が
天文学的な値になって元形状を覆い隠すバグ（2026-07-09 報告）。

実行:
'/c/Program Files/Blender Foundation/Blender 5.1/blender.exe' --background \
    --python test/blender_b_manga_line_fisheye_width_check.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import camera_comp, core, presets  # noqa: E402
from mathutils import Vector  # noqa: E402

RES_X = 1920
RES_Y = 1080


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_camera(*, fisheye: bool) -> bpy.types.Object:
    scene = bpy.context.scene
    scene.render.resolution_x = RES_X
    scene.render.resolution_y = RES_Y
    bpy.ops.object.camera_add(location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.clip_start = 0.01
    camera.data.clip_end = 200.0
    if fisheye:
        camera.data.type = "PANO"
        camera.data.panorama_type = "FISHEYE_EQUIDISTANT"
        camera.data.fisheye_fov = math.radians(180.0)
    else:
        camera.data.type = "PERSP"
        camera.data.lens = 50.0
    scene.camera = camera
    return camera


def _assert_fisheye_world_per_pixel_matches_equidistant() -> None:
    """等距離射影の理論値（距離 × 視野角 / 縦横pxの幾何平均）と一致すること.

    Cycles の魚眼が横=視野角/横px・縦=視野角/縦px のアナモルフィック
    射影であることは実レンダーで確認済み
    （_verify/2026-07-09_bml_fisheye_width/ の射影プローブ）。
    線幅は両密度の幾何平均で換算する。
    """
    _clear_scene()
    camera = _make_camera(fisheye=True)
    scene = bpy.context.scene

    dist = 15.0
    wpp = camera_comp._world_per_pixel(scene, camera, Vector((0.0, 0.0, -dist)))
    expected = dist * math.pi / math.sqrt(RES_X * RES_Y)
    assert math.isclose(wpp, expected, rel_tol=1.0e-6), (
        f"魚眼の world/px が等距離射影の理論値とズレています: {wpp} != {expected}"
    )
    # 回帰ガード: 旧実装 (2*dist*tan(90°)/対角px) は ~1e14 に発散していた
    assert wpp < 1.0, f"魚眼の world/px が発散しています: {wpp}"


def _assert_fisheye_side_object_uses_radial_distance() -> None:
    """真横（光軸から90°）のオブジェクトも正面と同じ距離基準で換算されること."""
    _clear_scene()
    camera = _make_camera(fisheye=True)
    scene = bpy.context.scene

    front = camera_comp._world_per_pixel(scene, camera, Vector((0.0, 0.0, -15.0)))
    side = camera_comp._world_per_pixel(scene, camera, Vector((15.0, 0.0, 0.0)))
    assert math.isclose(front, side, rel_tol=1.0e-6), (
        f"魚眼で真横のオブジェクトの world/px が正面と一致しません: "
        f"front={front} side={side}"
    )


def _assert_outline_thickness_stays_sane_after_switch() -> None:
    """PERSP でライン適用 → 魚眼へ切替+更新後も線幅が正常域に収まること."""
    _clear_scene()
    camera = _make_camera(fisheye=False)
    scene = bpy.context.scene

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, -15.0))
    obj = bpy.context.object
    obj.name = "BML_fisheye_width_cube"
    settings = obj.bmanga_line_settings
    settings.use_uniform_line_width = False
    settings.use_camera_compensation = False
    settings.line_width_reference_distance = 15.0

    assert presets.apply_line_settings(
        obj,
        bpy.context,
        refresh_scene=False,
        transforms_fresh=True,
    ), "ラインの適用に失敗しました"

    mod = obj.modifiers.get(core.MODIFIER_NAME)
    assert mod is not None, "アウトラインモディファイアがありません"

    # PERSP のカメラ基準幅を確定させてから比較する
    camera_comp.refresh(bpy.context)
    thickness_persp = abs(float(mod.thickness))
    assert thickness_persp > 0.0, "PERSP の線幅が 0 です"

    # 魚眼へ切替してカメラ基準の線幅を更新
    camera.data.type = "PANO"
    camera.data.panorama_type = "FISHEYE_EQUIDISTANT"
    camera.data.fisheye_fov = math.radians(180.0)
    camera_comp.refresh(bpy.context)

    thickness_pano = abs(float(mod.thickness))
    expected_world = camera_comp._reference_width_for_distance(
        scene,
        camera,
        float(settings.outline_thickness),
        15.0,
    )
    assert math.isclose(thickness_pano, expected_world, rel_tol=1.0e-4), (
        f"魚眼切替後の線幅が理論値とズレています: "
        f"{thickness_pano} != {expected_world}"
    )
    # 50mm レンズ→魚眼180°の画角比は高々10倍程度。旧実装では ~1e13 倍だった
    assert thickness_pano < thickness_persp * 50.0, (
        f"魚眼切替後の線幅が発散しています: "
        f"persp={thickness_persp} pano={thickness_pano}"
    )

    # PERSP へ戻すと元の線幅に戻ること
    camera.data.type = "PERSP"
    camera.data.lens = 50.0
    camera_comp.refresh(bpy.context)
    thickness_back = abs(float(mod.thickness))
    assert math.isclose(thickness_back, thickness_persp, rel_tol=1.0e-4), (
        f"PERSP へ戻した線幅が元に戻りません: "
        f"{thickness_back} != {thickness_persp}"
    )


def main() -> None:
    b_manga_line.register()
    try:
        _assert_fisheye_world_per_pixel_matches_equidistant()
        _assert_fisheye_side_object_uses_radial_distance()
        _assert_outline_thickness_stays_sane_after_switch()
    finally:
        b_manga_line.unregister()
    print("blender_b_manga_line_fisheye_width_check: PASS")


if __name__ == "__main__":
    main()
