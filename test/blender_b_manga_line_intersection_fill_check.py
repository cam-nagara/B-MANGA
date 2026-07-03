"""Blender実機用: B-MANGA Line 交差対象ライン厚みの生成確認."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

_CYLINDER_RADIUS = 0.50
_OUTLINE_THICKNESS = 0.24

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    core,
    intersection_lines,
    intersection_shell,
    outline_setup,
    presets,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for collection in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.node_groups,
        bpy.data.cameras,
        bpy.data.lights,
    ):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def _emission_material(name: str, color: tuple[float, float, float, float]):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.0
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def _make_source_slab(white_mat):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "交差確認_白い面"
    obj.dimensions = (3.0, 3.0, 0.10)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(white_mat)
    return obj


def _make_target_cylinder(white_mat):
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=96,
        radius=_CYLINDER_RADIUS,
        depth=1.20,
        location=(0.0, 0.0, 0.0),
    )
    obj = bpy.context.object
    obj.name = "交差確認_対象"
    obj.data.materials.append(white_mat)
    outline_setup.apply_outline(
        obj,
        thickness=_OUTLINE_THICKNESS,
        color=(0.0, 0.0, 0.0, 1.0),
        scene=bpy.context.scene,
    )
    return obj


def _make_gap_cube(name: str, location: tuple[float, float, float], white_mat):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(white_mat)
    settings = obj.bmanga_line_settings
    settings.outline_thickness_mm = 0.6
    settings.intersection_thickness_mm = 0.2
    settings.intersection_enabled = True
    settings.intersection_method = "BOOLEAN"
    return obj


def _evaluated_line_stats(
    obj,
    line_mat: bpy.types.Material,
    surface_mat: bpy.types.Material,
):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    try:
        line_index = None
        surface_index = None
        for index, mat in enumerate(mesh.materials):
            if mat and mat.name.startswith(line_mat.name):
                line_index = index
            if mat and mat.name.startswith(surface_mat.name):
                surface_index = index
        assert line_index is not None, "線用素材が評価済みメッシュにありません"
        assert surface_index is not None, "元面素材が評価済みメッシュにありません"

        line_vertices = set()
        line_polygons = 0
        surface_polygons = 0
        for poly in mesh.polygons:
            if poly.material_index == line_index:
                line_polygons += 1
                line_vertices.update(poly.vertices)
            elif poly.material_index == surface_index:
                surface_polygons += 1

        coords = [mesh.vertices[index].co.copy() for index in line_vertices]
        return line_polygons, surface_polygons, coords
    finally:
        bpy.data.meshes.remove(mesh)


def _target_names(obj: bpy.types.Object) -> set[str]:
    names: set[str] = set()
    for mod in core.iter_intersection_modifiers(obj):
        if intersection_shell.is_shell_modifier(mod):
            names.update(
                target.name for target in intersection_shell.modifier_targets(mod)
            )
            continue
        target = intersection_lines._modifier_target(mod)
        if target is not None:
            names.add(target.name)
    return names


def _assert_near_contact_uses_camera_width_before_generation() -> None:
    _clear_scene()
    scene = bpy.context.scene
    scene.render.resolution_x = 1000
    scene.render.resolution_y = 1000
    scene.render.resolution_percentage = 100
    bpy.ops.object.camera_add(location=(0.0, 0.0, 5.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 4.0
    scene.camera = camera

    white_mat = _emission_material("確認用_近接白", (1.0, 1.0, 1.0, 1.0))
    source = _make_gap_cube("交差確認_A_近接元", (0.0, 0.0, 0.0), white_mat)
    target = _make_gap_cube("交差確認_B_近接対象", (1.02, 0.0, 0.0), white_mat)
    bpy.context.view_layer.objects.active = source
    for obj in (source, target):
        assert presets.apply_line_settings(
            obj,
            bpy.context,
            refresh_scene=False,
            transforms_fresh=True,
        )
    presets._refresh_after_line_settings(bpy.context)

    assert target.name in _target_names(source), (
        "カメラ基準の線幅なら届く近接交差対象が作成対象から落ちています"
    )


def main() -> None:
    b_manga_line.register()
    _clear_scene()

    white_mat = _emission_material("確認用_白", (1.0, 1.0, 1.0, 1.0))
    source = _make_source_slab(white_mat)
    target = _make_target_cylinder(white_mat)
    # SHELL 方式はソース側アウトラインのソリッド厚みを交差判定に使う
    outline_setup.apply_outline(
        source,
        thickness=_OUTLINE_THICKNESS,
        color=(0.0, 0.0, 0.0, 1.0),
        scene=bpy.context.scene,
    )
    line_mat = outline_setup.get_line_material(source, "intersection")
    assert line_mat is not None, "線の素材が作成されていません"

    # 2026-07-03 確定仕様: 生成方式は「ライン素材（高速）」のみ
    # （BOOLEAN 指定しても SHELL として適用される）。
    # ペアの持ち主は面数の少ない側（この場合スラブ）に決定的に決まる。
    bpy.context.view_layer.objects.active = source
    assert intersection_lines.apply_intersection_lines(
        source,
        target=target,
        thickness=0.015,
        material=line_mat,
        method="BOOLEAN",
    )

    tree = bpy.data.node_groups.get(intersection_shell.SHELL_TREE_NAME)
    assert tree is not None, "交差線の生成設定が作成されていません"
    mod = source.modifiers.get(intersection_shell.SHELL_MODIFIER_NAME)
    assert mod is not None, "高速交差線モディファイアが作成されていません"
    sid_target_width = intersection_shell._find_socket_id(tree, "交差対象の線幅")
    assert sid_target_width is not None, "交差対象のライン厚みを塗る入力がありません"
    assert float(mod[sid_target_width]) > 0.0, (
        "交差対象のライン厚みが反映されていません"
    )

    bpy.context.view_layer.update()
    line_polygons, surface_polygons, coords = _evaluated_line_stats(
        source,
        line_mat,
        white_mat,
    )
    assert line_polygons > 100, f"交差線の面が少なすぎます: {line_polygons}"
    assert surface_polygons > 0, "元面が交差線で置き換わっています"
    assert coords, "交差線の頂点が生成されていません"

    min_x = min(co.x for co in coords)
    max_x = max(co.x for co in coords)
    min_y = min(co.y for co in coords)
    max_y = max(co.y for co in coords)
    # 交差線の中心（曲線位置）は「元の面」との実際の交差位置（半径0.5の
    # 円柱表面）。旧仕様の「殻の厚みぶん外側への塗りつぶし」は二重線と
    # 位置ズレの原因だったため単一曲線化した(2026-07-03)。
    # その上で 2026-07-03 追加要望: 背面法ハルと元メッシュの間に隙間が
    # 見えないよう、塗りつぶしチューブの半径は自分・交差対象の
    # アウトライン幅(_OUTLINE_THICKNESS)のうち大きい方まで広がる
    # （交差線幅の設定値はもはや上限ではなく下限）。
    covered_width = _OUTLINE_THICKNESS * intersection_shell.SHELL_GAP_COVERAGE_FACTOR
    inner = _CYLINDER_RADIUS - covered_width
    outer = _CYLINDER_RADIUS + covered_width
    margin = 0.08
    for label, low, high in (
        ("min_x", -outer - margin, -inner + margin),
        ("max_x", inner - margin, outer + margin),
        ("min_y", -outer - margin, -inner + margin),
        ("max_y", inner - margin, outer + margin),
    ):
        value = {"min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y}[label]
        assert low < value < high, (label, value)

    # 交差線幅の設定値（0.015、極めて小さい）ではなく、双方のアウトライン幅
    # (_OUTLINE_THICKNESS=0.24) に実効太さが支配されていることを明示的に確認
    # する（2026-07-03: 隙間塗りつぶし要望の中核）。
    outward = max_x - _CYLINDER_RADIUS
    assert outward > _OUTLINE_THICKNESS * 1.02, (
        "交差線の実効太さがアウトライン幅由来の隙間カバー幅に届いていません",
        outward,
    )

    _assert_near_contact_uses_camera_width_before_generation()

    print("[OK] 交差対象ライン厚みの評価済みジオメトリを確認", flush=True)


if __name__ == "__main__":
    main()
