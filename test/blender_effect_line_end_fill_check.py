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
        "bname_dev_effect_end_fill",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_effect_end_fill"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _drawing(layer):
    frame = layer.frames[0] if len(layer.frames) else None
    return getattr(frame, "drawing", None)


def _material_index(obj, name_part: str) -> int:
    for index, mat in enumerate(getattr(obj.data, "materials", []) or []):
        if name_part in str(getattr(mat, "name", "") or ""):
            return index
    raise AssertionError(f"素材がありません: {name_part}")


def _count_strokes_with_material(layer, material_index: int) -> int:
    drawing = _drawing(layer)
    assert drawing is not None
    return sum(1 for stroke in getattr(drawing, "strokes", []) if int(getattr(stroke, "material_index", -1)) == material_index)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_effect_end_fill_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "EffectEndFill.bname"))
        assert "FINISHED" in result, result

        from bname_dev_effect_end_fill.core.work import get_work
        from bname_dev_effect_end_fill.operators import effect_line_gen, effect_line_op
        from bname_dev_effect_end_fill.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        params = context.scene.bname_effect_line_params
        params.effect_type = "focus"
        params.end_shape = "cloud"
        params.fill_base_shape = True
        params.fill_color = (1.0, 0.0, 0.0, 1.0)
        params.fill_opacity = 0.5

        fill_stroke = effect_line_gen.generate_end_shape_fill_stroke(
            params,
            (90.0, 120.0),
            32.0,
            24.0,
            seed=11,
        )
        assert fill_stroke is not None, "終点形状の下地ストロークが生成されていません"
        assert fill_stroke.role == "end_fill"
        assert fill_stroke.cyclic
        assert fill_stroke.curve_type == "BEZIER"
        assert fill_stroke.bezier_smooth
        assert len(fill_stroke.points_xyz) >= 24, "終点形状の下地が粗すぎます"

        obj, layer = effect_line_op._create_effect_layer(
            context,
            (40.0, 60.0, 80.0, 60.0),
            parent_key=page_stack_key(page),
        )
        assert obj is not None and layer is not None
        fill_index = _material_index(obj, "EndShape_Fill")
        fill_mat = obj.data.materials[fill_index]
        gp_style = getattr(fill_mat, "grease_pencil", None)
        assert gp_style is not None
        assert bool(getattr(gp_style, "show_fill", False)), "終点形状の下地素材で塗りが有効になっていません"
        assert not bool(getattr(gp_style, "show_stroke", True)), "終点形状の下地素材で線が有効になっています"
        assert _count_strokes_with_material(layer, fill_index) == 1, "終点形状の下地ストローク数が不正です"

        params.fill_base_shape = False
        effect_line_op._write_effect_strokes(
            context,
            obj,
            layer,
            (40.0, 60.0, 80.0, 60.0),
            params_override=params,
        )
        assert _count_strokes_with_material(layer, fill_index) == 0, "設定OFFでも終点形状の下地が残っています"

        params.effect_type = "uni_flash"
        params.fill_base_shape = True
        effect_line_op._write_effect_strokes(
            context,
            obj,
            layer,
            (40.0, 60.0, 80.0, 60.0),
            params_override=params,
        )
        assert _count_strokes_with_material(layer, fill_index) == 1, "ウニフラ効果線で終点形状の下地が生成されていません"
        print("BNAME_EFFECT_LINE_END_FILL_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
