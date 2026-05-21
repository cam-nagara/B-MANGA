"""Blender 実機用: 効果線・フキダシの Geometry Nodes 同期を確認."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_gn_bridge",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_gn_bridge"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _modifier_socket_value(modifier, name: str):
    group = modifier.node_group
    assert group is not None, "Geometry Nodes グループがありません"
    for item in group.interface.items_tree:
        if getattr(item, "item_type", "") != "SOCKET":
            continue
        if getattr(item, "in_out", "") != "INPUT":
            continue
        if getattr(item, "name", "") != name:
            continue
        identifier = str(getattr(item, "identifier", "") or "")
        assert identifier, f"{name} の入力IDがありません"
        return modifier[identifier]
    raise AssertionError(f"Geometry Nodes 入力がありません: {name}")


def _assert_close(actual: float, expected: float, label: str, eps: float = 1.0e-5) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _evaluated_polygon_count(obj) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return len(getattr(mesh, "polygons", []) or [])
    finally:
        evaluated.to_mesh_clear()


def _assert_generated_group(group, *, kind: str) -> None:
    nodes = {node.bl_idname for node in group.nodes}
    required = {
        "effect_line": {"GeometryNodeCurvePrimitiveLine", "GeometryNodeCurveToMesh"},
        "balloon": {"GeometryNodeMeshCircle", "GeometryNodeCurveToMesh", "GeometryNodeSetMaterialIndex"},
    }[kind]
    assert required.issubset(nodes), f"{kind} の Geometry Nodes が生成ノードを持っていません: {nodes}"
    direct_links = [
        link
        for link in group.links
        if link.from_node.bl_idname == "NodeGroupInput"
        and link.to_node.bl_idname == "NodeGroupOutput"
        and link.from_socket.name == "Geometry"
        and link.to_socket.name == "Geometry"
    ]
    assert not direct_links, f"{kind} が入力形状をそのまま出力しています"
    if kind == "effect_line":
        input_geometry_links = [
            link
            for link in group.links
            if link.from_node.bl_idname == "NodeGroupInput" and link.from_socket.name == "Geometry"
        ]
        assert not input_geometry_links, f"{kind} が入力形状に依存しています"


def _assert_nodes(obj, *, kind: str, group_name: str):
    from bname_dev_gn_bridge.utils import geometry_nodes_bridge as gn

    modifier = obj.modifiers.get(gn.MODIFIER_NAME)
    assert modifier is not None, f"{getattr(obj, 'name', '')} に Geometry Nodes がありません"
    assert getattr(modifier, "type", "") == "NODES"
    assert modifier.node_group is not None
    assert modifier.node_group.name == group_name
    assert str(obj.get(gn.PROP_GN_KIND, "") or "") == kind
    _assert_generated_group(modifier.node_group, kind=kind)
    return modifier


def _create_legacy_passthrough_group() -> None:
    group = bpy.data.node_groups.new("BName_GN_Balloon", "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    input_node = group.nodes.new("NodeGroupInput")
    output_node = group.nodes.new("NodeGroupOutput")
    group.links.new(input_node.outputs["Geometry"], output_node.inputs["Geometry"])


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_gn_bridge_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _create_legacy_passthrough_group()
        mod = _load_addon()
        _assert_generated_group(bpy.data.node_groups["BName_GN_Balloon"], kind="balloon")
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "GeometryNodes.bname"))
        assert "FINISHED" in result, result

        from bname_dev_gn_bridge.core.work import get_work
        from bname_dev_gn_bridge.core import effect_line as effect_core
        from bname_dev_gn_bridge.operators import balloon_op, effect_line_op
        from bname_dev_gn_bridge.utils import balloon_curve_object
        from bname_dev_gn_bridge.utils import effect_line_object
        from bname_dev_gn_bridge.utils import geometry_nodes_bridge as gn_bridge
        from bname_dev_gn_bridge.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        page_key = page_stack_key(page)

        params = context.scene.bname_effect_line_params
        params.effect_type = "focus"
        params.brush_size_mm = 0.72
        params.opacity = 0.63
        params.max_line_count = 77
        effect_obj, effect_layer = effect_line_op._create_effect_layer(
            context,
            (15.0, 20.0, 64.0, 48.0),
            parent_key=page_key,
        )
        assert effect_obj is not None and effect_layer is not None
        effect_line_op._write_effect_strokes(
            context,
            effect_obj,
            effect_layer,
            (15.0, 20.0, 64.0, 48.0),
            seed=123,
        )
        effect_display = effect_line_object.find_effect_display_object(effect_obj)
        assert effect_display is not None, "効果線のGeometry Nodes表示実体がありません"
        assert effect_obj.hide_viewport, "効果線の制御用レイヤーが表示対象のままです"
        effect_modifier = _assert_nodes(
            effect_display,
            kind="effect_line",
            group_name="BName_GN_EffectLine",
        )
        _assert_close(_modifier_socket_value(effect_modifier, "線幅"), 0.72, "効果線 線幅")
        _assert_close(_modifier_socket_value(effect_modifier, "不透明度"), 0.63, "効果線 不透明度")
        assert int(_modifier_socket_value(effect_modifier, "本数")) == 77
        assert int(_modifier_socket_value(effect_modifier, "乱数")) == 123
        _assert_close(_modifier_socket_value(effect_modifier, "位置 X"), 15.0, "効果線 位置 X")
        _assert_close(_modifier_socket_value(effect_modifier, "位置 Y"), 20.0, "効果線 位置 Y")
        _assert_close(_modifier_socket_value(effect_modifier, "幅"), 64.0, "効果線 幅")
        _assert_close(_modifier_socket_value(effect_modifier, "高さ"), 48.0, "効果線 高さ")
        effect_socket_names = gn_bridge.effect_field_socket_names()
        missing = [
            field
            for field in effect_core.EFFECT_PARAM_FIELDS
            if field not in effect_socket_names
            or _modifier_socket_value(effect_modifier, effect_socket_names[field]) is None
        ]
        assert not missing, f"効果線の詳細設定がGeometry Nodes入力へ移植されていません: {missing}"
        assert _evaluated_polygon_count(effect_display) > 0, "効果線のGeometry Nodes表示結果が空です"

        balloon = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=30.0,
            y=50.0,
            w=42.0,
            h=23.0,
            parent_kind="page",
            parent_key=page_key,
        )
        balloon.line_width_mm = 0.55
        balloon.fill_opacity = 0.44
        tail1 = balloon.tails.add()
        tail1.type = "straight"
        tail1.direction_deg = 45.0
        tail1.length_mm = 8.0
        tail2 = balloon.tails.add()
        tail2.type = "curve"
        tail2.direction_deg = 135.0
        tail2.length_mm = 12.0
        tail2.root_width_mm = 4.0
        tail2.tip_width_mm = 1.0
        tail2.curve_bend = 0.35
        tail2.custom_points_enabled = True
        tail2.start_x_mm = -3.0
        tail2.start_y_mm = 2.0
        tail2.end_x_mm = 7.0
        tail2.end_y_mm = -5.0
        balloon_obj = balloon_curve_object.ensure_balloon_curve_object(
            scene=context.scene,
            entry=balloon,
            page=page,
        )
        assert balloon_obj is not None
        balloon_modifier = _assert_nodes(
            balloon_obj,
            kind="balloon",
            group_name="BName_GN_Balloon",
        )
        _assert_close(_modifier_socket_value(balloon_modifier, "線幅"), 0.55, "フキダシ 線幅")
        _assert_close(_modifier_socket_value(balloon_modifier, "塗り不透明度"), 0.44, "フキダシ 塗り")
        _assert_close(_modifier_socket_value(balloon_modifier, "幅"), 42.0, "フキダシ 幅")
        _assert_close(_modifier_socket_value(balloon_modifier, "高さ"), 23.0, "フキダシ 高さ")
        assert int(_modifier_socket_value(balloon_modifier, "形状")) == 2
        for socket_name in (
            "X",
            "Y",
            "回転",
            "角丸",
            "線種",
            "線色",
            "塗り色",
            "塗り輪郭ぼかし",
            "塗りグラデーション",
            "外側白フチ",
            "内側白フチ",
            "合成モード",
            "しっぽ数",
        ):
            _modifier_socket_value(balloon_modifier, socket_name)
        assert int(_modifier_socket_value(balloon_modifier, "しっぽ数")) == 2
        assert int(_modifier_socket_value(balloon_modifier, "しっぽ 種類")) == 1
        assert int(_modifier_socket_value(balloon_modifier, "しっぽ2 種類")) == 2
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 方向"), 135.0, "フキダシ しっぽ2 方向")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 長さ"), 12.0, "フキダシ しっぽ2 長さ")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 根元幅"), 4.0, "フキダシ しっぽ2 根元幅")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 先端幅"), 1.0, "フキダシ しっぽ2 先端幅")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 曲げ"), 0.35, "フキダシ しっぽ2 曲げ")
        assert bool(_modifier_socket_value(balloon_modifier, "しっぽ2 始点・終点を固定"))
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 始点 X"), -3.0, "フキダシ しっぽ2 始点 X")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 始点 Y"), 2.0, "フキダシ しっぽ2 始点 Y")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 終点 X"), 7.0, "フキダシ しっぽ2 終点 X")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 終点 Y"), -5.0, "フキダシ しっぽ2 終点 Y")
        assert _modifier_socket_value(balloon_modifier, "参照形状") is not None
        assert len(balloon_obj.data.polygons) == 0, "フキダシ本体にB-Name側の表示メッシュが残っています"
        assert _evaluated_polygon_count(balloon_obj) > 0, "フキダシのGeometry Nodes表示結果が空です"
        source_obj = bpy.data.objects.get(f"{balloon_curve_object.BALLOON_SOURCE_NAME_PREFIX}{balloon.id}")
        assert source_obj is not None, "フキダシの参照形状がありません"
        assert source_obj.hide_viewport and source_obj.hide_render and source_obj.hide_select, (
            "フキダシの参照形状が画面表示対象になっています"
        )

        balloon.line_width_mm = 0.91
        balloon_modifier = _assert_nodes(balloon_obj, kind="balloon", group_name="BName_GN_Balloon")
        _assert_close(_modifier_socket_value(balloon_modifier, "線幅"), 0.91, "フキダシ 線幅 更新")

        balloon_shape_ids = {
            str(getattr(item, "identifier", "") or "")
            for item in balloon.bl_rna.properties["shape"].enum_items
        }
        assert "uni_flash" not in balloon_shape_ids, "フキダシ形状にウニフラッシュが残っています"

        legacy = balloon_op._create_balloon_entry(
            context,
            page,
            shape="uni_flash",
            x=88.0,
            y=66.0,
            w=58.0,
            h=36.0,
            parent_kind="page",
            parent_key=page_key,
        )
        legacy.line_width_mm = 0.38
        legacy_obj = balloon_curve_object.ensure_balloon_curve_object(
            scene=context.scene,
            entry=legacy,
            page=page,
        )
        assert legacy_obj is not None
        legacy_modifier = _assert_nodes(legacy_obj, kind="balloon", group_name="BName_GN_Balloon")
        _assert_close(_modifier_socket_value(legacy_modifier, "線幅"), 0.38, "旧フキダシ 線幅")
        assert int(_modifier_socket_value(legacy_modifier, "形状")) == 2
        assert legacy_modifier.node_group.name != "BName_GN_UniFlash"

        print("BNAME_GEOMETRY_NODES_BRIDGE_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
