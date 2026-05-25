"""Blender 実機用: コマ内フキダシのマスク混入と制御点過多を検証。"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_balloon_curve_mask_anchor",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_balloon_curve_mask_anchor"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _evaluated_bounds(obj) -> tuple[float, float, float]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        coords = [vertex.co.copy() for vertex in mesh.vertices]
        assert coords, "表示結果の頂点がありません"
        min_x = min(co.x for co in coords)
        max_x = max(co.x for co in coords)
        min_y = min(co.y for co in coords)
        max_y = max(co.y for co in coords)
        min_z = min(co.z for co in coords)
        max_z = max(co.z for co in coords)
        return max_x - min_x, max_y - min_y, max_z - min_z
    finally:
        evaluated.to_mesh_clear()


def _evaluated_faces_for_material(obj, material_index: int) -> list[list]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        coords = [vertex.co.copy() for vertex in mesh.vertices]
        faces = []
        for poly in mesh.polygons:
            if int(getattr(poly, "material_index", 0) or 0) != int(material_index):
                continue
            faces.append([coords[index] for index in poly.vertices])
        assert faces, f"線幅測定用の面がありません: material_index={material_index}"
        return faces
    finally:
        evaluated.to_mesh_clear()


def _spline_anchor_bounds(spline) -> tuple[float, float]:
    if getattr(spline, "type", "") == "BEZIER":
        coords = [point.co.copy() for point in spline.bezier_points]
    else:
        coords = [point.co.to_3d() for point in spline.points]
    assert coords, "輪郭の制御点がありません"
    min_x = min(co.x for co in coords)
    max_x = max(co.x for co in coords)
    min_y = min(co.y for co in coords)
    max_y = max(co.y for co in coords)
    return max_x - min_x, max_y - min_y


def _longest_bezier_anchor_segment(spline):
    points = list(getattr(spline, "bezier_points", []) or [])
    assert len(points) >= 2, "線幅測定用の制御点が不足しています"
    segment_count = len(points) if bool(getattr(spline, "use_cyclic_u", False)) else len(points) - 1
    best = (points[0].co, points[1 % len(points)].co)
    best_length = -1.0
    for index in range(segment_count):
        start = points[index].co
        end = points[(index + 1) % len(points)].co
        length = (end - start).length
        if length > best_length:
            best = (start, end)
            best_length = length
    return best


def _stroke_width_cross_section(obj, start, end, *, material_index: int = 0, distance_limit: float = 0.005) -> float:
    ax, ay = float(start.x), float(start.y)
    bx, by = float(end.x), float(end.y)
    dx = bx - ax
    dy = by - ay
    length = (dx * dx + dy * dy) ** 0.5
    assert length > 1.0e-9, "線幅測定用の線分が短すぎます"
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    sx = ax + dx * 0.5
    sy = ay + dy * 0.5
    intersections: list[float] = []
    for face in _evaluated_faces_for_material(obj, material_index):
        if len(face) < 2:
            continue
        prev = face[-1]
        prev_t = (float(prev.x) - sx) * ux + (float(prev.y) - sy) * uy
        prev_n = (float(prev.x) - sx) * nx + (float(prev.y) - sy) * ny
        for cur in face:
            cur_t = (float(cur.x) - sx) * ux + (float(cur.y) - sy) * uy
            cur_n = (float(cur.x) - sx) * nx + (float(cur.y) - sy) * ny
            if abs(cur_t - prev_t) > 1.0e-12 and (
                (prev_t <= 0.0 <= cur_t) or (cur_t <= 0.0 <= prev_t)
            ):
                factor = -prev_t / (cur_t - prev_t)
                if -1.0e-6 <= factor <= 1.0 + 1.0e-6:
                    distance = prev_n + (cur_n - prev_n) * factor
                    if abs(distance) <= distance_limit:
                        intersections.append(distance)
            prev_t = cur_t
            prev_n = cur_n
    unique: list[float] = []
    for value in sorted(intersections):
        if unique and abs(unique[-1] - value) <= 1.0e-7:
            continue
        unique.append(value)
    assert len(unique) >= 2, f"線幅を測定できる交点が不足しています: {unique}"
    return max(unique) - min(unique)


def _evaluated_world_bounds(obj) -> tuple[float, float, float, float]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        coords = [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
        assert coords, "表示結果の頂点がありません"
        return (
            min(co.x for co in coords),
            max(co.x for co in coords),
            min(co.y for co in coords),
            max(co.y for co in coords),
        )
    finally:
        evaluated.to_mesh_clear()


def _material_names(obj) -> set[str]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return {str(mat.name) for mat in getattr(mesh, "materials", []) if mat is not None}
    finally:
        evaluated.to_mesh_clear()


def _modifier_mask_values(obj, nodes_mod):
    modifier = obj.modifiers.get(nodes_mod.MODIFIER_NAME)
    assert modifier is not None and modifier.node_group is not None, "フキダシの表示補助がありません"
    enabled = None
    target = None
    clip_needed = None
    fill_clip_needed = None
    for item in modifier.node_group.interface.items_tree:
        if getattr(item, "item_type", "") != "SOCKET" or getattr(item, "in_out", "") != "INPUT":
            continue
        if getattr(item, "name", "") == "マスク使用":
            enabled = bool(modifier.get(item.identifier))
        elif getattr(item, "name", "") == "マスク対象":
            target = modifier.get(item.identifier)
        elif getattr(item, "name", "") == "塗り切り抜き必要":
            fill_clip_needed = bool(modifier.get(item.identifier))
        elif getattr(item, "name", "") == "切り抜き必要":
            clip_needed = bool(modifier.get(item.identifier))
    return enabled, target, clip_needed, fill_clip_needed


def _assert_curve_uses_opacity_mask(obj) -> None:
    from bname_dev_balloon_curve_mask_anchor.utils import mask_apply

    assert obj.modifiers.get(mask_apply.MOD_NAME_COMA_MASK) is None, "フキダシに古いコマ切り抜きが残っています"
    assert obj.modifiers.get(mask_apply.MOD_NAME_PAGE_MASK) is None, "フキダシに古いページ切り抜きが残っています"
    found = False
    for mat in getattr(getattr(obj, "data", None), "materials", []) or []:
        if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
            continue
        for node in mat.node_tree.nodes:
            if getattr(node, "label", "") == "コマ内容マスク":
                found = True
                break
    assert found, "フキダシにコマ内容マスクが接続されていません"


def _modifier_socket_value(obj, nodes_mod, socket_name: str):
    modifier = obj.modifiers.get(nodes_mod.MODIFIER_NAME)
    assert modifier is not None and modifier.node_group is not None, "フキダシの表示補助がありません"
    for item in modifier.node_group.interface.items_tree:
        if getattr(item, "item_type", "") != "SOCKET" or getattr(item, "in_out", "") != "INPUT":
            continue
        if getattr(item, "name", "") == socket_name:
            return modifier.get(item.identifier)
    raise AssertionError(f"フキダシの表示補助に {socket_name} がありません")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_curve_mask_anchor_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "BalloonMaskAnchor.bname"))
        assert "FINISHED" in result, result

        from bname_dev_balloon_curve_mask_anchor.core.work import get_work
        from bname_dev_balloon_curve_mask_anchor.utils import balloon_curve_object
        from bname_dev_balloon_curve_mask_anchor.utils import balloon_curve_render_nodes
        from bname_dev_balloon_curve_mask_anchor.utils import coma_border_object
        from bname_dev_balloon_curve_mask_anchor.utils import coma_plane
        from bname_dev_balloon_curve_mask_anchor.utils import geom
        from bname_dev_balloon_curve_mask_anchor.utils import mask_apply
        from bname_dev_balloon_curve_mask_anchor.utils import page_grid
        from bname_dev_balloon_curve_mask_anchor.utils.layer_hierarchy import coma_stack_key
        from bname_dev_balloon_curve_mask_anchor.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = 20.0
        coma.rect_y_mm = 35.0
        coma.rect_width_mm = 120.0
        coma.rect_height_mm = 150.0
        coma.background_color = (1.0, 1.0, 1.0, 1.0)
        parent_key = coma_stack_key(page, coma)
        coma_plane.ensure_coma_plane(scene, work, page, coma)
        coma_plane.ensure_coma_mask(scene, work, page, coma)

        entry = page.balloons.add()
        entry.id = "balloon_mask_anchor"
        entry.title = "フキダシ"
        entry.shape = "cloud"
        entry.x_mm = 58.0
        entry.y_mm = 80.0
        entry.width_mm = 45.0
        entry.height_mm = 36.0
        entry.parent_kind = "coma"
        entry.parent_key = parent_key
        entry.fill_color = (0.8, 1.0, 0.85, 1.0)
        entry.fill_opacity = 100.0
        entry.opacity = 100.0
        entry.line_width_mm = 1.2

        obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
        assert obj is not None and obj.type == "CURVE", "フキダシ実体がカーブではありません"
        enabled, target, clip_needed, fill_clip_needed = _modifier_mask_values(obj, balloon_curve_render_nodes)
        assert enabled and target is not None, "コマ内フキダシにコマ形状マスクが設定されていません"
        assert not clip_needed, "コマ内に収まっているフキダシまで切り抜き対象になっています"
        assert not fill_clip_needed, "コマ内に収まっているフキダシの塗りまで切り抜き対象になっています"
        _assert_curve_uses_opacity_mask(obj)
        mask_apply.apply_mask_to_layer_object(obj)
        bpy.context.view_layer.update()

        body_points = len(obj.data.splines[0].bezier_points)
        assert body_points <= 32, f"雲フキダシの制御点が細かすぎます: {body_points}"
        assert body_points >= 6, f"雲フキダシの制御点が不足しています: {body_points}"
        width_m, height_m, depth_m = _evaluated_bounds(obj)
        assert width_m < 0.07, f"コマ形状がフキダシ表示に混入しています: width={width_m}"
        assert height_m < 0.06, f"コマ形状がフキダシ表示に混入しています: height={height_m}"
        assert depth_m < 0.04, f"フキダシの表示レイヤーが想定以上に厚くなっています: depth={depth_m}"
        leaked = {name for name in _material_names(obj) if "ComaPlane" in name}
        assert not leaked, f"コマ用素材がフキダシ表示に混入しています: {sorted(leaked)}"

        entry2 = page.balloons.add()
        entry2.id = "balloon_mask_clip"
        entry2.title = "はみ出し確認"
        entry2.shape = "cloud"
        entry2.x_mm = 118.0
        entry2.y_mm = 98.0
        entry2.width_mm = 54.0
        entry2.height_mm = 50.0
        entry2.parent_kind = "coma"
        entry2.parent_key = parent_key
        entry2.fill_color = (1.0, 0.8, 0.9, 1.0)
        entry2.fill_opacity = 100.0
        entry2.opacity = 100.0
        entry2.line_width_mm = 1.2
        obj2 = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry2, page=page)
        assert obj2 is not None and obj2.type == "CURVE", "はみ出し確認フキダシが作成されていません"
        enabled2, target2, clip_needed2, fill_clip_needed2 = _modifier_mask_values(obj2, balloon_curve_render_nodes)
        assert enabled2 and target2 is not None, "はみ出し確認フキダシにコマ形状マスクが設定されていません"
        assert clip_needed2, "コマ外へはみ出すフキダシの線が切り抜き対象になっていません"
        assert fill_clip_needed2, "コマ外へはみ出すフキダシの塗りが切り抜き対象になっていません"
        _assert_curve_uses_opacity_mask(obj2)
        stale_mesh = bpy.data.meshes.new("balloon_clip_mask_balloon_mask_clip_mesh")
        stale_obj = bpy.data.objects.new("balloon_clip_mask_balloon_mask_clip", stale_mesh)
        stale_obj[balloon_curve_object.PROP_BALLOON_CLIP_MASK_KIND] = "coma_clip"
        stale_obj[balloon_curve_object.PROP_BALLOON_CLIP_MASK_OWNER_ID] = "balloon_mask_clip"
        bpy.context.collection.objects.link(stale_obj)
        with balloon_curve_object.suspend_auto_sync():
            entry2.line_width_mm = 1.4
            balloon_curve_object.on_balloon_entry_changed(entry2)
        assert bpy.data.objects.get("balloon_clip_mask_balloon_mask_clip") is not None, (
            "軽量更新後にコマ形状マスクが失われています"
        )
        enabled2b, target2b, clip_needed2b, fill_clip_needed2b = _modifier_mask_values(obj2, balloon_curve_render_nodes)
        assert enabled2b and target2b is not None, "軽量更新後にコマ形状マスクが設定されていません"
        assert clip_needed2b and fill_clip_needed2b, "軽量更新後にコマ外の切り抜きが有効になっていません"
        _assert_curve_uses_opacity_mask(obj2)
        bpy.context.view_layer.update()
        width2_m, height2_m, _depth2_m = _evaluated_bounds(obj2)
        assert width2_m < 0.09 and height2_m < 0.09, (
            f"透明度マスク後の表示にコマ全体が混入しています: width={width2_m}, height={height2_m}"
        )

        entry3 = page.balloons.add()
        entry3.id = "balloon_mask_line_only"
        entry3.title = "線だけ近接"
        entry3.shape = "ellipse"
        entry3.x_mm = 105.0
        entry3.y_mm = 92.0
        entry3.width_mm = 35.0
        entry3.height_mm = 30.0
        entry3.parent_kind = "coma"
        entry3.parent_key = parent_key
        entry3.fill_color = (1.0, 0.9, 0.95, 1.0)
        entry3.fill_opacity = 100.0
        entry3.opacity = 100.0
        entry3.line_width_mm = 6.0
        obj3 = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry3, page=page)
        assert obj3 is not None and obj3.type == "CURVE", "線だけ近接フキダシが作成されていません"
        enabled3, target3, clip_needed3, fill_clip_needed3 = _modifier_mask_values(obj3, balloon_curve_render_nodes)
        assert enabled3 and target3 is not None, "線だけ近接フキダシにコマ形状マスクが設定されていません"
        assert clip_needed3, "輪郭線がコマ枠へ近接するフキダシの線が切り抜き対象になっていません"
        assert not fill_clip_needed3, "輪郭線だけが近接するフキダシの塗りまで切り抜き対象になっています"
        _assert_curve_uses_opacity_mask(obj3)
        bpy.context.view_layer.update()
        width3_m, height3_m, depth3_m = _evaluated_bounds(obj3)
        assert width3_m < 0.05, f"線だけ近接時にコマ全体が混入しています: width={width3_m}"
        assert height3_m < 0.05, f"線だけ近接時にコマ全体が混入しています: height={height3_m}"
        assert depth3_m < 0.04, f"線だけ近接時の表示レイヤーが想定以上に厚くなっています: depth={depth3_m}"

        entry.shape = "ellipse"
        balloon_curve_object.ensure_balloon_curve_object(
            scene=scene,
            entry=entry,
            page=page,
            force_regenerate=True,
        )
        assert len(obj.data.splines[0].bezier_points) == 4, "楕円フキダシが4点ベジェになっていません"

        entry4 = page.balloons.add()
        entry4.id = "balloon_line_width_rect"
        entry4.title = "線幅確認"
        entry4.shape = "rect"
        entry4.x_mm = 20.0
        entry4.y_mm = 20.0
        entry4.width_mm = 40.0
        entry4.height_mm = 30.0
        entry4.parent_kind = "page"
        entry4.parent_key = page_stack_key(page)
        entry4.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry4.fill_opacity = 100.0
        entry4.opacity = 100.0
        entry4.line_width_mm = 0.3
        obj4 = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry4, page=page)
        assert obj4 is not None and obj4.type == "CURVE", "線幅確認フキダシが作成されていません"
        bpy.context.view_layer.update()
        rect_width = _stroke_width_cross_section(
            obj4,
            obj4.data.splines[0].bezier_points[0].co,
            obj4.data.splines[0].bezier_points[1].co,
        )
        assert 0.00027 <= rect_width <= 0.00033, f"矩形フキダシの0.3mm線幅が設定値通りではありません: width={rect_width}"

        entry5 = page.balloons.add()
        entry5.id = "balloon_line_width_thorn"
        entry5.title = "トゲ線幅確認"
        entry5.shape = "thorn"
        entry5.x_mm = 70.0
        entry5.y_mm = 20.0
        entry5.width_mm = 40.0
        entry5.height_mm = 30.0
        entry5.parent_kind = "page"
        entry5.parent_key = page_stack_key(page)
        entry5.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry5.fill_opacity = 100.0
        entry5.opacity = 100.0
        entry5.line_width_mm = 0.3
        obj5 = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry5, page=page)
        assert obj5 is not None and obj5.type == "CURVE", "トゲ線幅確認フキダシが作成されていません"
        bpy.context.view_layer.update()
        body5 = obj5.data.splines[0]
        assert all(abs(float(point.radius) - 1.0) <= 1.0e-6 for point in body5.bezier_points), (
            "トゲ（直線）本体の主線幅が制御点ごとに変化しています"
        )
        assert abs(float(_modifier_socket_value(obj5, balloon_curve_render_nodes, "線幅 (mm)") or 0.0) - 0.3) <= 1.0e-6, (
            "トゲ（直線）フキダシの表示補助に0.3mmの線幅が渡っていません"
        )
        thorn_start, thorn_end = _longest_bezier_anchor_segment(body5)
        thorn_width = _stroke_width_cross_section(
            obj5,
            thorn_start,
            thorn_end,
        )
        assert 0.00027 <= thorn_width <= 0.00036, (
            f"トゲ（直線）フキダシの0.3mm線幅が鋭角接合部を考慮しても太すぎます: width={thorn_width}"
        )

        entry6 = page.balloons.add()
        entry6.id = "balloon_line_width_ellipse_03"
        entry6.title = "楕円線幅確認"
        entry6.shape = "ellipse"
        entry6.x_mm = 120.0
        entry6.y_mm = 20.0
        entry6.width_mm = 40.0
        entry6.height_mm = 30.0
        entry6.parent_kind = "page"
        entry6.parent_key = page_stack_key(page)
        entry6.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry6.fill_opacity = 100.0
        entry6.opacity = 100.0
        entry6.line_width_mm = 0.3
        obj6 = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry6, page=page)
        assert obj6 is not None and obj6.type == "CURVE", "楕円線幅確認フキダシが作成されていません"
        bpy.context.view_layer.update()
        ellipse_width = _stroke_width_cross_section(
            obj6,
            obj6.data.splines[0].bezier_points[0].co,
            obj6.data.splines[0].bezier_points[1].co,
        )
        assert 0.00027 <= ellipse_width <= 0.00033, f"楕円フキダシの0.3mm線幅が設定値通りではありません: width={ellipse_width}"

        coma.border.width_mm = 0.3
        coma.border.style = "solid"
        coma.border.visible = True
        border_obj = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
        assert border_obj is not None, "コマ枠線が作成されていません"
        bpy.context.view_layer.update()
        border_width = _stroke_width_cross_section(
            border_obj,
            border_obj.data.splines[0].points[0].co.to_3d(),
            border_obj.data.splines[0].points[1].co.to_3d(),
        )
        assert 0.00027 <= border_width <= 0.00033, f"コマ枠線の0.3mm線幅が設定値通りではありません: width={border_width}"
        print("BNAME_BALLOON_CURVE_MASK_ANCHOR_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
