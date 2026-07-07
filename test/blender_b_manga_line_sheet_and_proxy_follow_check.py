"""B-MANGA Line: sheet outline visibility, sheet pair ownership, proxy follow."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    core,
    intersection_shell,
    outline_setup,
    plane_filter,
    presets,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_camera() -> bpy.types.Object:
    bpy.ops.object.camera_add(location=(0.0, 0.0, 6.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    bpy.context.scene.camera = camera
    return camera


def _select(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _apply(obj: bpy.types.Object) -> None:
    _select(obj)
    assert presets.apply_line_settings(obj, bpy.context)


def _outline_material(obj: bpy.types.Object) -> bpy.types.Material:
    mat = outline_setup.get_outline_material(obj)
    assert mat is not None, f"{obj.name} のアウトラインマテリアルがありません"
    return mat


def _eval_poly_count(obj: bpy.types.Object) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    mesh = ev.to_mesh()
    try:
        return len(mesh.polygons)
    finally:
        ev.to_mesh_clear()


def _eval_material_poly_count(obj: bpy.types.Object, material_prefix: str) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    mesh = ev.to_mesh()
    try:
        materials = [mat.name if mat else "" for mat in mesh.materials]
        count = 0
        for poly in mesh.polygons:
            if poly.material_index >= len(materials):
                continue
            if materials[poly.material_index].startswith(material_prefix):
                count += 1
        return count
    finally:
        ev.to_mesh_clear()


def _setup_pair() -> tuple[bpy.types.Object, bpy.types.Object]:
    """交差するキューブ（立体）と平面（シート）を作る."""
    scene = bpy.context.scene
    scene.render.resolution_x = 1000
    scene.render.resolution_y = 1000
    _make_camera()

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    cube = bpy.context.object
    cube.name = "BML_sheet_pair_cube"

    bpy.ops.mesh.primitive_plane_add(size=3.0, location=(0.0, 0.0, 0.1))
    plane = bpy.context.object
    plane.name = "BML_sheet_pair_plane"

    for obj in (cube, plane):
        settings = obj.bmanga_line_settings
        settings.outline_thickness_mm = 0.5
        settings.exclude_sheet_meshes = False
        settings.intersection_enabled = True
        settings.intersection_thickness_mm = 0.3
        _apply(obj)
    return cube, plane


def _test_sheet_outline_is_double_sided() -> None:
    _clear_scene()
    cube, plane = _setup_pair()

    assert plane_filter.is_sheet_mesh(plane), "平面がシート判定されていません"
    assert not plane_filter.is_sheet_mesh(cube)

    plane_mat = _outline_material(plane)
    assert plane_mat.use_backface_culling is False, (
        "シートのアウトラインは両面表示（カリング無効）であるべき"
    )
    assert bool(plane_mat.get(outline_setup.PROP_DOUBLE_SIDED, False))

    cube_mat = _outline_material(cube)
    assert cube_mat.use_backface_culling is True, (
        "立体のアウトラインは背面法のままであるべき"
    )
    assert not bool(cube_mat.get(outline_setup.PROP_DOUBLE_SIDED, False))

    assert plane.modifiers.get(core.MODIFIER_NAME) is None, (
        "板ポリに通常アウトラインが作成されています"
    )
    assert plane.modifiers.get(core.GN_MODIFIER_NAME) is None, (
        "板ポリに内部線が作成されています"
    )
    assert plane.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME) is not None, (
        "板ポリに境界チューブが作成されていません"
    )


def _test_sheet_outline_tube() -> None:
    """シートの輪郭が全方向チューブとして生成されること."""
    _clear_scene()
    cube, plane = _setup_pair()

    tube_mod = plane.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME)
    assert tube_mod is not None, "シートに境界チューブモディファイアがありません"
    assert tube_mod.node_group is not None
    assert tube_mod.node_group.name.startswith(outline_setup.SHEET_OUTLINE_TREE_NAME)
    assert cube.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME) is None, (
        "立体オブジェクトにチューブが作られています"
    )

    assert plane.modifiers.get(core.MODIFIER_NAME) is None, (
        "板ポリに通常アウトラインが作成されています"
    )
    assert plane.modifiers.get(core.GN_MODIFIER_NAME) is None, (
        "板ポリに内部線が作成されています"
    )

    # チューブ太さはアウトラインの線幅設定と同期する
    sid = outline_setup._find_socket_identifier(
        tube_mod.node_group, "線の太さ"
    )
    assert float(tube_mod[sid]) > 0.0, "境界チューブの太さが設定されていません"
    outline_setup.update_modifier_thickness(plane, 0.123)
    assert abs(float(tube_mod[sid]) - 0.123) < 1.0e-6

    # 評価済みメッシュにチューブのジオメトリが乗っている
    # （3m四方plane: 境界4辺 × 円周8分割 = 32面以上増える）
    base = len(plane.data.polygons)
    assert _eval_poly_count(plane) >= base + 32, (
        _eval_poly_count(plane),
        base,
    )
    assert _eval_material_poly_count(plane, outline_setup.MATERIAL_NAME) >= 32, (
        "板ポリ境界チューブがライン素材で評価されていません"
    )
    safe_nodes = [
        node for node in tube_mod.node_group.nodes
        if getattr(node, "label", "") == outline_setup._SHEET_TUBE_SAFE_SCALE_LABEL
    ]
    assert safe_nodes, "板ポリ境界チューブの線幅下限ノードがありません"
    assert abs(
        float(safe_nodes[0].inputs[1].default_value)
        - float(outline_setup._MIN_CURVE_TO_MESH_SCALE)
    ) < 1.0e-7, "板ポリ境界チューブの線幅下限値が古いままです"


def _test_sheet_midpoint_adjustment_keeps_tube_visible() -> None:
    """中間頂点の線幅調整だけを動かしても板ポリのアウトラインが消えないこと."""
    _clear_scene()
    _cube, plane = _setup_pair()
    settings = plane.bmanga_line_settings
    settings.edge_smooth_factor = -1.0
    settings.edge_width_curve_25 = 0.0
    settings.edge_width_curve_50 = 0.0
    settings.edge_width_curve_75 = 0.0
    settings.edge_midpoint_jitter_percent = 25.0
    assert presets.apply_line_settings(
        plane,
        bpy.context,
        refresh_scene=False,
        line_targets=("outline",),
    )

    tube_mod = plane.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME)
    assert tube_mod is not None, "シートに境界チューブモディファイアがありません"
    assert tube_mod.show_viewport and tube_mod.show_render, (
        "中間頂点の線幅調整で境界チューブが非表示になっています"
    )
    factor_sid = outline_setup._find_socket_identifier(
        tube_mod.node_group,
        outline_setup._SHEET_TUBE_MIDPOINT_FACTOR_SOCKET,
    )
    assert factor_sid is not None
    assert abs(float(tube_mod[factor_sid]) + 1.0) < 1.0e-7
    assert _eval_material_poly_count(plane, outline_setup.MATERIAL_NAME) >= 32, (
        "中間頂点の線幅調整後に板ポリ境界チューブが評価されていません"
    )


def _test_sheet_never_owns_intersection_pair() -> None:
    _clear_scene()
    cube, plane = _setup_pair()

    cube_shell = cube.modifiers.get(intersection_shell.SHELL_MODIFIER_NAME)
    plane_shell = plane.modifiers.get(intersection_shell.SHELL_MODIFIER_NAME)
    assert cube_shell is not None, "非シート側が交差線ペアを持つべき"
    assert plane_shell is None, "シート側は交差線ペアを持たないべき"

    targets = intersection_shell.modifier_targets(cube_shell)
    assert plane in targets, [t.name for t in targets]

    # 交差ライン面が実際に生成されていること
    assert _eval_poly_count(cube) > len(cube.data.polygons) + 6, (
        "シートとの交差線ジオメトリが生成されていません"
    )


def _test_remove_lines_cleans_sheet() -> None:
    """「ラインを削除」でシートのチューブ・非表示リム素材まで消えること."""
    _clear_scene()
    cube, plane = _setup_pair()

    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)
    plane.select_set(True)
    bpy.context.view_layer.objects.active = plane
    assert bpy.ops.bmanga_line.remove() == {"FINISHED"}

    for obj in (cube, plane):
        leftover = [m.name for m in obj.modifiers if m.name.startswith("BML_")]
        assert not leftover, f"{obj.name} にモディファイアが残っています: {leftover}"
        mats = [
            slot.material.name
            for slot in obj.material_slots
            if slot.material
            and (
                outline_setup._is_line_material(slot.material)
                or slot.material.name.startswith(
                    outline_setup.SHEET_RIM_HIDDEN_MATERIAL_NAME
                )
            )
        ]
        assert not mats, f"{obj.name} にライン素材が残っています: {mats}"
        assert not core.has_line(obj)
    strays = [
        o.name for o in bpy.data.objects
        if o.name.startswith("BML_IntersectionProxy")
    ]
    assert not strays, f"交差プロキシが残っています: {strays}"
    stray_colls = [
        c.name for c in bpy.data.collections
        if c.name.startswith("BML_IntersectionTargets")
    ]
    assert not stray_colls, f"交差対象コレクションが残っています: {stray_colls}"


def _test_outline_toggle_hides_sheet_tube() -> None:
    """「アウトラインを追加」オフでシートのチューブも非表示になること."""
    _clear_scene()
    cube, plane = _setup_pair()
    tube = plane.modifiers[core.SHEET_OUTLINE_MODIFIER_NAME]

    _select(plane)
    plane.bmanga_line_settings.outline_enabled = False
    assert not tube.show_viewport and not tube.show_render, (
        "アウトラインオフでもチューブが表示されています"
    )
    plane.bmanga_line_settings.outline_enabled = True
    assert tube.show_viewport and tube.show_render


def _test_line_only_keeps_sheet_tube_only() -> None:
    """「ラインのみ表示」の往復でも板ポリは境界チューブのみであること."""
    _clear_scene()
    cube, plane = _setup_pair()
    assert plane.modifiers.get(core.MODIFIER_NAME) is None
    assert plane.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME) is not None
    assert outline_setup.set_line_only(plane, True)
    assert plane.modifiers.get(core.MODIFIER_NAME) is None, (
        "ラインのみ表示中に通常アウトラインが復活しています"
    )
    assert plane.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME) is not None
    assert outline_setup.set_line_only(plane, False)
    assert plane.modifiers.get(core.MODIFIER_NAME) is None, (
        "通常表示へ戻した後に通常アウトラインが復活しています"
    )
    assert plane.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME) is not None


def _test_pair_ownership_is_deterministic() -> None:
    """アクティブオブジェクトに関係なく交差ペアの持ち主が一意に決まる."""
    _clear_scene()
    scene = bpy.context.scene
    scene.render.resolution_x = 1000
    scene.render.resolution_y = 1000
    _make_camera()

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    owner_cube = bpy.context.object
    owner_cube.name = "BML_pair_owner"  # 面数が少ない側が持ち主になる
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.6, location=(0.4, 0.0, 0.0))
    heavy = bpy.context.object
    heavy.name = "BML_pair_heavy"
    for obj in (owner_cube, heavy):
        settings = obj.bmanga_line_settings
        settings.outline_thickness_mm = 0.5
        settings.intersection_enabled = True

    # わざと「相手側をアクティブ」にした状態で適用しても持ち主は変わらない
    _select(heavy)
    assert presets.apply_line_settings(owner_cube, bpy.context)
    _select(owner_cube)
    assert presets.apply_line_settings(heavy, bpy.context)

    owner_mod = owner_cube.modifiers.get(intersection_shell.SHELL_MODIFIER_NAME)
    assert owner_mod is not None, "面数の少ない側がペアを持っていません"
    heavy_mod = heavy.modifiers.get(intersection_shell.SHELL_MODIFIER_NAME)
    assert heavy_mod is None or not intersection_shell.modifier_targets(heavy_mod), (
        "両側が同じ交差ペアを持っています（二重線の原因）"
    )

    # 過去の非決定的判定で残った相手側ペアの残骸が掃除されること
    stale_coll = intersection_shell._get_or_create_target_collection(heavy)
    stale_proxy = intersection_shell._get_or_create_proxy(heavy, owner_cube)
    if stale_proxy.name not in stale_coll.objects:
        stale_coll.objects.link(stale_proxy)
    stale_proxy_name = stale_proxy.name
    _select(owner_cube)
    assert presets.apply_line_settings(owner_cube, bpy.context)
    remaining = bpy.data.objects.get(stale_proxy_name)
    assert remaining is None or not remaining.users_collection, (
        "相手側に残った古いペア（ミラープロキシ）が掃除されていません"
    )


def _test_proxy_follows_object_move() -> None:
    _clear_scene()
    cube, plane = _setup_pair()

    assert (
        intersection_shell._on_depsgraph_update
        in bpy.app.handlers.depsgraph_update_post
    ), "移動追従ハンドラが未登録"

    plane.location.x += 0.5
    bpy.context.view_layer.update()
    intersection_shell._sync_proxy_matrices(
        bpy.context.scene, {plane.name_full}
    )

    collection_name = str(cube.get("bml_intersection_shell_target_collection"))
    collection = bpy.data.collections[collection_name]
    proxy = next(iter(collection.objects))
    expected = cube.matrix_world.inverted() @ plane.matrix_world
    for i in range(4):
        for j in range(4):
            assert abs(proxy.matrix_world[i][j] - expected[i][j]) <= 1.0e-4, (
                "プロキシ行列が移動へ追従していません"
            )

    # 交差しない位置まで離すとペアが解消されること
    plane.location.x += 100.0
    bpy.context.view_layer.update()
    intersection_shell._do_pair_resync(
        bpy.context.scene, {plane.name_full, cube.name_full}
    )
    assert cube.modifiers.get(intersection_shell.SHELL_MODIFIER_NAME) is None, (
        "交差が無くなってもペアが残っています"
    )

    # 戻すとペアが復活すること
    plane.location.x -= 100.0
    bpy.context.view_layer.update()
    intersection_shell._do_pair_resync(
        bpy.context.scene, {plane.name_full, cube.name_full}
    )
    assert cube.modifiers.get(intersection_shell.SHELL_MODIFIER_NAME) is not None, (
        "交差が戻ってもペアが復活しません"
    )


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    _test_sheet_outline_is_double_sided()
    _test_sheet_outline_tube()
    _test_sheet_midpoint_adjustment_keeps_tube_visible()
    _test_sheet_never_owns_intersection_pair()
    _test_pair_ownership_is_deterministic()
    _test_proxy_follows_object_move()
    _test_remove_lines_cleans_sheet()
    _test_outline_toggle_hides_sheet_tube()
    _test_line_only_keeps_sheet_tube_only()
    print("[PASS] B-MANGA Line sheet outline/ownership and proxy follow")


if __name__ == "__main__":
    main()
