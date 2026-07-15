"""Blender check: inner/selection/intersection line Join Geometry material slot order.

背景: 境界チューブ(outline_setup.py)と同根の不具合が、稜谷線(inner_lines.py /
inner_line_cache.py)・選択線(inner_lines.py)・交差線(intersection_lines.py /
intersection_shell.py / intersection_cache.py)の各GNツリーにも存在した。
Join Geometry のマルチ入力ソケットは「後から接続したリンクが先頭（先に評価）」
という挙動を持つが、各ツリーが「①元メッシュ→②ライン(Set Material済み)」の順で
links.new していたため、実際の結合順が「ライン→元メッシュ」になり、評価後
メッシュの素材スロット表が [ライン素材, 元素材...] に並べ替わっていた。Cycles の
レンダー同期は面の material_index をオブジェクト側のスロット順で解決するため、
稜谷線・交差線・選択線を使った瞬間にオブジェクトの見た目が壊れる
（テクスチャが消えて単色化する・別の色の縁が出る）不具合があった。

詳細な実機調査の経緯: _verify/2026-07-09_tokyo0004_line_texture_visual/

このテストは以下の6箇所の Join Geometry 接続順修正を、実際に GN ツリーを
適用して評価後メッシュで検証する（境界チューブ用テスト
test/blender_b_manga_line_boundary_tube_material_order_check.py と同じ方式）:

1. inner_line_cache.py（稜谷線の既定適用経路。標準の稜谷線は常にこの
   保存済みキャッシュツリー経由で表示される）
2. inner_lines.py（選択線が使う GN ツリービルダー。稜谷線本体は
   inner_line_cache.py へリダイレクトされるため通常は未使用だが、
   選択線(selection_lines.py)はこのビルダーを直接使う）
3. intersection_cache.py（交差線の既定適用経路。標準の交差線は常に
   この保存済みキャッシュツリー経由で表示される）
4. intersection_shell.py（交差線の「ライン素材」方式の実装。現状の
   intersection_lines.apply_intersection_lines はコード監査の結果、
   同名関数がファイル後半で再定義されて上書きされており、この方式・
   下記5のBoolean/SDF方式ともにUIの通常操作からは到達できない
   （実装は保持されているため回帰だけは防ぐ）。
5. intersection_lines.py の単一対象 Join（_add_tube_nodes。Boolean/SDF
   共通、UIからは現状未到達 — 上記4のコメント参照）
6. intersection_lines.py の複数対象 Join（_create_boolean_multi_tree。
   UIからは現状未到達 — 上記4のコメント参照）

4-6 は現状のUI操作からは到達しない内部実装だが、モジュールとして保持
されている以上は将来の再利用や保存済み.blend互換のために内部整合性を
保つ必要があるため、GNツリービルダーを直接呼び出すホワイトボックス方式で
検証する。1-3 は presets.apply_line_settings 経由の実利用相当の検証。
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    core,
    inner_line_cache,
    intersection_cache,
    intersection_lines,
    intersection_shell,
    outline_setup,
    presets,
    selection_lines,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for datablocks in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.node_groups,
        bpy.data.cameras,
        bpy.data.collections,
    ):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def _make_camera() -> bpy.types.Object:
    bpy.ops.object.camera_add(location=(0.0, -6.0, 3.0), rotation=(1.1, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 4.0
    bpy.context.scene.camera = camera
    return camera


def _select(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _evaluated_mesh_materials_and_indices(
    obj: bpy.types.Object,
) -> tuple[list[str], list[int]]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    mesh = ev.to_mesh()
    try:
        names = [mat.name if mat else "" for mat in mesh.materials]
        indices = [poly.material_index for poly in mesh.polygons]
        return names, indices
    finally:
        ev.to_mesh_clear()


def _assert_slot_order_preserved(obj: bpy.types.Object) -> None:
    """評価後メッシュの素材スロット順 == オブジェクトのスロット順."""
    obj_names = [slot.material.name if slot.material else "" for slot in obj.material_slots]
    eval_names, _indices = _evaluated_mesh_materials_and_indices(obj)
    assert eval_names == obj_names, (
        f"{obj.name}: 評価後メッシュの素材スロット順がオブジェクトと一致しません "
        f"(obj={obj_names}, eval={eval_names}). "
        "Join Geometryの接続順（後接続が先に評価される）の退行の疑い。"
    )


def _make_plain_cube(
    name: str, location: tuple[float, float, float], *, size: float = 1.0,
) -> bpy.types.Object:
    """単一素材の裸キューブ（ライン未適用）を作成."""
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    mat = bpy.data.materials.new(f"{name}_surface")
    mat.diffuse_color = (0.7, 0.7, 0.8, 1.0)
    obj.data.materials.append(mat)
    return obj


def _disable_all_limits(settings) -> None:
    settings.use_outline_creation_limit = False
    settings.use_intersection_creation_limit = False
    settings.use_outline_distance_limit = False
    settings.use_intersection_distance_limit = False
    settings.use_camera_culling = False
    settings.use_camera_compensation = True


# ------------------------------------------------------------------
# 1. 稜谷線（inner_line_cache.py 経由の既定適用経路）
# ------------------------------------------------------------------

def _test_inner_line_material_order() -> None:
    _clear_scene()
    _make_camera()
    obj = _make_plain_cube("BML_mo_inner_cube", (0.0, 0.0, 0.0))
    settings = obj.bmanga_line_settings
    settings.outline_enabled = False
    settings.inner_line_enabled = True
    settings.selection_line_enabled = False
    settings.intersection_enabled = False
    _disable_all_limits(settings)
    _select(obj)
    assert presets.apply_line_settings(obj, bpy.context), "稜谷線の適用に失敗しました"

    mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
    assert mod is not None, "稜谷線モディファイアが作成されていません"
    assert mod.node_group is not None
    assert mod.node_group.name.startswith(inner_line_cache.CACHE_TREE_NAME), (
        "稜谷線が保存済みキャッシュツリー(inner_line_cache.py)経由で適用されて"
        f"いません（想定と異なる実装経路: {mod.node_group.name}）"
    )
    _assert_slot_order_preserved(obj)


def _test_combined_outline_inner_material_order() -> None:
    """複数素材で輪郭線の後ろへ稜谷線を積んでも素材を維持する."""
    _clear_scene()
    _make_camera()
    obj = _make_plain_cube("BML_mo_outline_inner_cube", (0.0, 0.0, 0.0))
    second = bpy.data.materials.new(f"{obj.name}_surface_second")
    obj.data.materials.append(second)
    for polygon in list(obj.data.polygons)[::2]:
        polygon.material_index = 1
    settings = obj.bmanga_line_settings
    settings.outline_enabled = True
    settings.inner_line_enabled = True
    settings.selection_line_enabled = False
    settings.intersection_enabled = False
    settings.auto_subdivision_for_midpoint = False
    _disable_all_limits(settings)
    _select(obj)
    assert presets.apply_line_settings(obj, bpy.context), (
        "輪郭線＋稜谷線の適用に失敗しました"
    )
    assert obj.modifiers.get(core.MODIFIER_NAME) is not None
    assert obj.modifiers.get(core.GN_MODIFIER_NAME) is not None
    _assert_slot_order_preserved(obj)


# ------------------------------------------------------------------
# 2. 選択線（inner_lines.py の GN ツリービルダーを直接使う唯一の経路）
# ------------------------------------------------------------------

def _test_selection_line_material_order() -> None:
    _clear_scene()
    _make_camera()
    obj = _make_plain_cube("BML_mo_selection_cube", (0.0, 0.0, 0.0))
    settings = obj.bmanga_line_settings
    settings.outline_enabled = False
    settings.inner_line_enabled = False
    settings.selection_line_enabled = True
    settings.intersection_enabled = False
    _disable_all_limits(settings)

    # 少なくとも1辺をFreestyleマークして選択線の生成対象にする。
    # このBlenderバージョンでは MeshEdge.use_freestyle_mark は存在せず、
    # "freestyle_edge" BOOLEAN属性(EDGEドメイン)経由でのみ設定できる
    # （selection_lines._source_freestyle_value の getattr フォールバックが
    # 示唆する通り。実機確認済み: 2026-07-09）。
    attr = obj.data.attributes.get("freestyle_edge")
    if attr is None:
        attr = obj.data.attributes.new("freestyle_edge", "BOOLEAN", "EDGE")
    attr.data[0].value = True
    obj.data.update()

    mat = bpy.data.materials.new("BML_mo_selection_line_mat")
    assert selection_lines.apply_selection_lines(
        obj, angle=0.5236, thickness=0.001, material=mat,
    ), "選択線の適用に失敗しました"

    mod = obj.modifiers.get(core.SELECTION_LINE_MODIFIER_NAME)
    assert mod is not None, "選択線モディファイアが作成されていません"
    assert mod.node_group is not None
    assert mod.node_group.name == core.SELECTION_LINE_TREE_NAME, (
        f"選択線ツリー名が想定と異なります: {mod.node_group.name}"
    )
    _assert_slot_order_preserved(obj)


# ------------------------------------------------------------------
# 3. 交差線（intersection_cache.py 経由の既定適用経路）
# ------------------------------------------------------------------

def _make_intersection_ready_cube(
    name: str, location: tuple[float, float, float],
) -> bpy.types.Object:
    obj = _make_plain_cube(name, location)
    settings = obj.bmanga_line_settings
    settings.outline_enabled = True
    settings.inner_line_enabled = False
    settings.selection_line_enabled = False
    settings.intersection_enabled = True
    settings.outline_thickness_mm = 0.7
    settings.intersection_thickness_mm = 0.7
    _disable_all_limits(settings)
    return obj


def _test_intersection_cache_material_order() -> None:
    _clear_scene()
    _make_camera()
    source = _make_intersection_ready_cube("BML_mo_cache_source", (0.0, 0.0, 0.0))
    target = _make_intersection_ready_cube("BML_mo_cache_target", (0.35, 0.0, 0.0))
    for obj in (source, target):
        assert presets.apply_line_settings(obj, bpy.context, refresh_scene=False), obj.name
    presets._refresh_after_line_settings(bpy.context)
    bpy.context.view_layer.update()

    mod = source.modifiers.get(core.INTERSECTION_MODIFIER_NAME)
    assert mod is not None, "保存済み交差線の表示モディファイアがありません"
    assert mod.node_group is not None
    assert mod.node_group.name.startswith(intersection_cache.CACHE_TREE_NAME), (
        "交差線が保存済みキャッシュツリー(intersection_cache.py)経由で適用されて"
        f"いません（想定と異なる実装経路: {mod.node_group.name}）"
    )
    _assert_slot_order_preserved(source)


def _test_combined_inner_intersection_material_order() -> None:
    """稜谷線の後ろへ交差線を積んでも、既存面の素材番号を維持する."""
    _clear_scene()
    _make_camera()
    source = _make_intersection_ready_cube(
        "BML_mo_combined_source", (0.0, 0.0, 0.0)
    )
    target = _make_intersection_ready_cube(
        "BML_mo_combined_target", (0.35, 0.0, 0.0)
    )
    for obj in (source, target):
        second = bpy.data.materials.new(f"{obj.name}_surface_second")
        obj.data.materials.append(second)
        for polygon in list(obj.data.polygons)[::2]:
            polygon.material_index = 1
        settings = obj.bmanga_line_settings
        settings.inner_line_enabled = True
        settings.auto_subdivision_for_midpoint = False
        assert presets.apply_line_settings(
            obj,
            bpy.context,
            refresh_scene=False,
        ), obj.name
    presets._refresh_after_line_settings(bpy.context)
    bpy.context.view_layer.update()

    assert source.modifiers.get(core.GN_MODIFIER_NAME) is not None
    assert source.modifiers.get(core.INTERSECTION_MODIFIER_NAME) is not None
    _assert_slot_order_preserved(source)

    slot_pointers = tuple(
        slot.material.as_pointer() if slot.material else 0
        for slot in source.material_slots
    )
    material_count = len(bpy.data.materials)
    assert presets.apply_line_settings(
        source,
        bpy.context,
        refresh_scene=False,
        line_targets=("outline",),
    )
    assert tuple(
        slot.material.as_pointer() if slot.material else 0
        for slot in source.material_slots
    ) == slot_pointers, "再反映でパディング素材が作り直されています"
    assert len(bpy.data.materials) == material_count, "再反映で素材が増殖しています"

    material_names = tuple(
        slot.material.name if slot.material else "" for slot in source.material_slots
    )
    assert outline_setup.set_line_only(source, True)
    assert outline_setup.set_line_only(source, False)
    assert tuple(
        slot.material.name if slot.material else "" for slot in source.material_slots
    ) == material_names, "ラインのみ表示の往復で素材スロットが変わりました"
    _assert_slot_order_preserved(source)


# ------------------------------------------------------------------
# 4. 交差線「ライン素材」方式（intersection_shell.py, 現状UI未到達）
# ------------------------------------------------------------------

def _test_intersection_shell_material_order() -> None:
    _clear_scene()
    _make_camera()
    # outline(通常アウトライン)だけを持たせ、intersection_enabledは無効のまま
    # にして intersection_cache.py 経由の既定経路を通らないようにし、
    # intersection_shell.apply_intersection_shell を直接呼んで検証する。
    source = _make_plain_cube("BML_mo_shell_source", (0.0, 0.0, 0.0))
    target = _make_plain_cube("BML_mo_shell_target", (0.35, 0.0, 0.0))
    for obj in (source, target):
        settings = obj.bmanga_line_settings
        settings.outline_enabled = True
        settings.inner_line_enabled = False
        settings.selection_line_enabled = False
        settings.intersection_enabled = False
        settings.outline_thickness_mm = 0.7
        _disable_all_limits(settings)
        _select(obj)
        assert presets.apply_line_settings(obj, bpy.context), (
            f"{obj.name}: 通常アウトラインの適用に失敗しました"
        )
    bpy.context.view_layer.update()

    mat = bpy.data.materials.new("BML_mo_shell_line_mat")
    assert intersection_shell.apply_intersection_shell(
        source, 0.0007, 0.0, mat, bpy.context.scene,
    ), "intersection_shell.apply_intersection_shell に失敗しました"

    mod = source.modifiers.get(intersection_shell.SHELL_MODIFIER_NAME)
    assert mod is not None, "交差線シェルモディファイアが作成されていません"
    assert mod.node_group is not None
    assert mod.node_group.name == intersection_shell.SHELL_TREE_NAME, (
        f"交差線シェルツリー名が想定と異なります: {mod.node_group.name}"
    )
    _assert_slot_order_preserved(source)


# ------------------------------------------------------------------
# 5-6. intersection_lines.py の単一対象 / 複数対象 Join
# （_create_boolean_tree / _create_boolean_multi_tree, 現状UI未到達）
# ------------------------------------------------------------------

def _test_intersection_lines_boolean_single_target_material_order() -> None:
    _clear_scene()
    _make_camera()
    source = _make_plain_cube("BML_mo_il_single_source", (0.0, 0.0, 0.0))
    target = _make_plain_cube("BML_mo_il_single_target", (0.35, 0.0, 0.0))

    mat = bpy.data.materials.new("BML_mo_il_single_mat")
    intersection_lines._ensure_material_slot(source, mat)
    tree = intersection_lines._get_or_create_tree("BOOLEAN")
    mod = source.modifiers.new(name="BML_mo_TestBooleanIntersection", type="NODES")
    mod.node_group = tree
    intersection_lines._set_modifier_parameters(mod, target, 0.01, 0.0, mat)
    bpy.context.view_layer.update()

    _assert_slot_order_preserved(source)


def _test_intersection_lines_boolean_multi_target_material_order() -> None:
    _clear_scene()
    _make_camera()
    source = _make_plain_cube("BML_mo_il_multi_source", (0.0, 0.0, 0.0))
    target_a = _make_plain_cube("BML_mo_il_multi_target_a", (0.35, 0.0, 0.0))
    target_b = _make_plain_cube("BML_mo_il_multi_target_b", (-0.35, 0.0, 0.0))
    targets = [target_a, target_b]

    mat = bpy.data.materials.new("BML_mo_il_multi_mat")
    intersection_lines._ensure_material_slot(source, mat)
    tree = intersection_lines._get_or_create_multi_tree(len(targets))
    mod = source.modifiers.new(name="BML_mo_TestBooleanMultiIntersection", type="NODES")
    mod.node_group = tree
    intersection_lines._set_multi_modifier_parameters(
        mod, targets, 0.01, 0.0, mat, 0.01,
    )
    bpy.context.view_layer.update()

    _assert_slot_order_preserved(source)


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    _test_inner_line_material_order()
    _test_combined_outline_inner_material_order()
    _test_selection_line_material_order()
    _test_intersection_cache_material_order()
    _test_combined_inner_intersection_material_order()
    _test_intersection_shell_material_order()
    _test_intersection_lines_boolean_single_target_material_order()
    _test_intersection_lines_boolean_multi_target_material_order()
    print(
        "[PASS] B-MANGA Line inner/selection/intersection line material slot order"
    )


if __name__ == "__main__":
    main()
