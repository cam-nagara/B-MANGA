"""Blender実機用: 手描き詳細の線／塗り色が別レイヤーへ波及しないことを確認する。"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _new_gp(context, title: str, parent_key: str):
    from bmanga_dev.utils import gp_object_layer, layer_object_model

    obj = gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bmanga_id=layer_object_model.make_stable_id("gp"),
        title=title,
        z_index=210,
        parent_kind="page",
        parent_key=parent_key,
    )
    assert obj is not None
    layer = layer_object_model.content_layer(obj)
    assert layer is not None
    return obj, layer


def _style_material(obj, layer):
    from bmanga_dev.utils import gpencil

    material = gpencil.ensure_layer_material(
        obj,
        layer,
        activate=True,
        assign_existing=True,
    )
    assert material is not None
    assert material.grease_pencil is not None
    return material


def _rgba(value) -> tuple[float, float, float, float]:
    return tuple(round(float(component), 6) for component in value[:4])


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_gp_detail_material_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "GP_Material.bmanga"))
        assert result == {"FINISHED"}, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert result == {"FINISHED"}, result

        context = bpy.context
        work = context.scene.bmanga_work
        page_key = str(work.pages[0].id)
        first, first_layer = _new_gp(context, "手描きA", page_key)
        second, second_layer = _new_gp(context, "手描きB", page_key)
        first_material = _style_material(first, first_layer)
        second_material = _style_material(second, second_layer)

        # 通常作成時点からObjectごとにMaterialが分離される。
        assert first.data is not second.data
        assert first_material is not second_material

        from bmanga_dev.utils import layer_object_model

        duplicate = layer_object_model.duplicate_gp_object(
            first,
            bmanga_id=layer_object_model.make_stable_id("gp"),
            title="手描きAの複製",
            z_order=220,
        )
        assert duplicate is not None
        assert duplicate.data is not first.data
        assert set(duplicate.data.materials).isdisjoint(set(first.data.materials))

        # 旧データ相当として、2対象がGPデータとMaterialを共有する不正状態を作る。
        # 移行処理と同じ正規化で、対象だけが専用コピーへ切り替わることを検証する。
        second.data = first.data
        assert second.data is first.data
        shared_material = first.active_material
        assert shared_material is not None
        shared_style = shared_material.grease_pencil
        assert shared_style is not None
        baseline_line = (0.13, 0.24, 0.35, 1.0)
        baseline_fill = (0.81, 0.72, 0.63, 0.9)
        shared_style.color = baseline_line
        shared_style.fill_color = baseline_fill

        from bmanga_dev.operators import detail_dialog_runtime
        from bmanga_dev.utils import detail_target_resolver, gpencil

        gpencil.ensure_unique_object_materials(first)
        assert first.data is not second.data
        assert first.active_material is not second.active_material
        assert second.active_material is shared_material
        target = detail_target_resolver.resolve_target_from_object(context, first)
        private_material = first.active_material
        session = detail_dialog_runtime.begin_actual_session(context, target)
        # 詳細画面を開くだけでは、確定済み構造を追加変更しない。
        assert first.active_material is private_material

        first_style = first.active_material.grease_pencil
        second_style = second.active_material.grease_pencil
        edited_line = (0.92, 0.12, 0.21, 1.0)
        edited_fill = (0.15, 0.85, 0.44, 0.75)
        first_style.color = edited_line
        first_style.fill_color = edited_fill
        detail_dialog_runtime.sync_actual_session(context, session)
        assert _rgba(first_style.color) == _rgba(edited_line)
        assert _rgba(first_style.fill_color) == _rgba(edited_fill)
        assert _rgba(second_style.color) == _rgba(baseline_line)
        assert _rgba(second_style.fill_color) == _rgba(baseline_fill)

        # キャンセルは開いた対象だけを開始時の色へ戻し、共有状態には戻さない。
        detail_dialog_runtime.cancel_actual_session(context, session)
        assert first.active_material is not second.active_material
        assert _rgba(first.active_material.grease_pencil.color) == _rgba(baseline_line), (
            first.active_material_index,
            first.active_material.name,
            _rgba(first.active_material.grease_pencil.color),
            [_rgba(mat.grease_pencil.color) for mat in first.data.materials if mat is not None],
        )
        assert _rgba(first.active_material.grease_pencil.fill_color) == _rgba(baseline_fill), (
            _rgba(first.active_material.grease_pencil.fill_color),
            [_rgba(mat.grease_pencil.fill_color) for mat in first.data.materials if mat is not None],
        )
        assert _rgba(second.active_material.grease_pencil.color) == _rgba(baseline_line)
        assert _rgba(second.active_material.grease_pencil.fill_color) == _rgba(baseline_fill)
    finally:
        if mod is not None:
            mod.unregister()

    print("BMANGA_GP_DETAIL_MATERIAL_ISOLATION_OK")


if __name__ == "__main__":
    main()
