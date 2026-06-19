"""Blender 実機用: 効果線の終点形状下地塗りを確認。"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_effect_end_fill",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_effect_end_fill"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _drawing(layer):
    frame = layer.frames[0] if len(layer.frames) else None
    return getattr(frame, "drawing", None)


def _stroke_count(layer) -> int:
    drawing = _drawing(layer)
    if drawing is None:
        return 0
    return len(getattr(drawing, "strokes", []) or [])


def _evaluated_polygon_count(obj) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return len(getattr(mesh, "polygons", []) or [])
    finally:
        evaluated.to_mesh_clear()


def _filled_shape_polygon_count(obj) -> int:
    return sum(
        1
        for poly in getattr(obj.data, "polygons", []) or []
        if int(poly.material_index) == 1 and len(getattr(poly, "vertices", []) or []) > 4
    )


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_effect_end_fill_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "EffectEndFill.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_effect_end_fill.core.work import get_work
        from bmanga_dev_effect_end_fill.operators import effect_line_op
        from bmanga_dev_effect_end_fill.utils import effect_line_object
        from bmanga_dev_effect_end_fill.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        params = context.scene.bmanga_effect_line_params
        params.effect_type = "focus"
        params.end_shape = "cloud"
        params.fill_base_shape = True
        params.fill_color = (1.0, 0.0, 0.0, 1.0)
        params.fill_opacity = 50.0

        obj, layer = effect_line_op._create_effect_layer(
            context,
            (40.0, 60.0, 80.0, 60.0),
            parent_key=page_stack_key(page),
        )
        assert obj is not None and layer is not None
        assert _stroke_count(layer) == 0, "効果線の制御用レイヤーにB-MANGA生成ストロークが残っています"
        display = effect_line_object.find_effect_display_object(obj)
        assert display is not None, "効果線の表示実体がありません"
        assert len(display.data.polygons) > 0, "効果線の表示実体メッシュが空です"
        assert len(display.data.materials) >= 2, "効果線の表示実体に線と塗りの素材がありません"
        modifier = display.modifiers.get("B-MANGA Geometry Nodes")
        assert modifier is None, "効果線の表示実体に重いGeometry Nodesが残っています"
        fill_polygons = _filled_shape_polygon_count(display)
        assert fill_polygons > 0, "終点形状の下地塗りが表示実体へ反映されていません"
        assert _evaluated_polygon_count(display) > 0, "効果線の表示結果が空です"

        params.fill_base_shape = False
        effect_line_op._write_effect_strokes(
            context,
            obj,
            layer,
            (40.0, 60.0, 80.0, 60.0),
            params_override=params,
        )
        modifier = display.modifiers.get("B-MANGA Geometry Nodes")
        assert modifier is None, "設定OFF後も重いGeometry Nodesが残っています"
        assert _filled_shape_polygon_count(display) == 0, "設定OFFでも終点形状の下地塗りが残っています"
        assert _stroke_count(layer) == 0, "設定OFF後にB-MANGA生成ストロークが作られています"

        params.effect_type = "uni_flash"
        params.fill_base_shape = True
        effect_line_op._write_effect_strokes(
            context,
            obj,
            layer,
            (40.0, 60.0, 80.0, 60.0),
            params_override=params,
        )
        modifier = display.modifiers.get("B-MANGA Geometry Nodes")
        assert modifier is None, "ウニフラ効果線に重いGeometry Nodesが残っています"
        assert _filled_shape_polygon_count(display) > 0, "ウニフラ効果線で終点形状の下地塗りが表示されていません"
        assert _stroke_count(layer) == 0, "ウニフラ効果線でB-MANGA生成ストロークが作られています"
        print("BMANGA_EFFECT_LINE_END_FILL_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
