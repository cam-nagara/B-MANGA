"""Blender 5.1実機: 効果線詳細の固定対象とリンク非伝播を検証する。

生成した2本の効果線だけを使い、右クリック対象Bを開く直前に対象Aが
選択されている状況を再現する。
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_detail_effect_fixed_target_test"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _make_effect(stable_id: str, max_line_count: int):
    gpencil = importlib.import_module(f"{MODULE_NAME}.utils.gpencil")
    naming = importlib.import_module(f"{MODULE_NAME}.utils.object_naming")
    effect_core = importlib.import_module(f"{MODULE_NAME}.core.effect_line")
    effect_op = importlib.import_module(f"{MODULE_NAME}.operators.effect_line_op")

    data = gpencil.ensure_gpencil(f"FixedTarget_{stable_id}_Data")
    layer = gpencil.ensure_layer(data, "content")
    obj = bpy.data.objects.new(f"FixedTarget_{stable_id}", data)
    bpy.context.scene.collection.objects.link(obj)
    naming.stamp_identity(
        obj,
        kind="effect",
        bmanga_id=stable_id,
        title=stable_id,
        z_index=10,
        parent_key="p0001",
    )
    values = effect_core.effect_params_to_dict(
        bpy.context.scene.bmanga_effect_line_params
    )
    values["max_line_count"] = int(max_line_count)
    effect_op._write_effect_meta(
        obj,
        {
            "content": {
                "x": 10.0,
                "y": 20.0,
                "w": 60.0,
                "h": 70.0,
                "center_x": 40.0,
                "center_y": 55.0,
                "seed": 1,
                "params": values,
            }
        },
    )
    return obj, layer


def _same_rna(left, right) -> bool:
    try:
        return int(left.as_pointer()) == int(right.as_pointer())
    except Exception:
        # RNA以外のテストdoubleでも比較できるよう、最後だけPython同一性を使う。
        return left is right


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    effect_op = importlib.import_module(f"{MODULE_NAME}.operators.effect_line_op")
    runtime = importlib.import_module(f"{MODULE_NAME}.operators.detail_dialog_runtime")
    resolver = importlib.import_module(f"{MODULE_NAME}.utils.detail_target_resolver")
    original_write = effect_op._write_effect_strokes
    calls: list[tuple[object, object, bool]] = []
    try:
        obj_a, layer_a = _make_effect("effect_fixed_a", 111)
        obj_b, layer_b = _make_effect("effect_fixed_b", 222)
        scene = bpy.context.scene
        scene.bmanga_active_layer_kind = "effect"
        scene.bmanga_active_effect_layer_name = "effect_fixed_a"
        effect_op._load_layer_params_to_scene(bpy.context, obj_a, layer_a)
        assert int(scene.bmanga_effect_line_params.max_line_count) == 111

        def _record_write(_context, obj, layer, _bounds, **kwargs):
            calls.append((obj, layer, bool(kwargs.get("propagate_link", True))))
            return 1

        effect_op._write_effect_strokes = _record_write
        target_b = resolver.resolve_target_from_object(bpy.context, obj_b)
        session = runtime.begin_actual_session(bpy.context, target_b)

        assert scene.bmanga_active_layer_kind == "effect"
        assert scene.bmanga_active_effect_layer_name == "effect_fixed_b"
        assert int(scene.bmanga_effect_line_params.max_line_count) == 222

        # RNA更新コールバックは、画面を開く前に選択されていたAではなく、
        # invokeで固定したBだけを書き、リンク相手へは伝播しない。
        scene.bmanga_effect_line_params.max_line_count = 223
        assert calls, "効果線設定の更新コールバックが実行されていません"
        assert all(
            _same_rna(obj, obj_b) and _same_rna(layer, layer_b)
            for obj, layer, _ in calls
        ), [(getattr(obj, "name", ""), getattr(layer, "name", "")) for obj, layer, _ in calls]
        assert all(propagate is False for _, _, propagate in calls)

        runtime.sync_actual_session(bpy.context, session)
        assert all(
            _same_rna(obj, obj_b) and _same_rna(layer, layer_b)
            for obj, layer, _ in calls
        )
        assert all(propagate is False for _, _, propagate in calls)

        # 詳細設定Bを開いたまま画面上でAを選ぼうとしても、Scene共有値・
        # アクティブID・生成対象をAへ切り替えない。
        call_count = len(calls)
        assert effect_op._select_effect_layer(bpy.context, obj_a, layer_a) is False
        assert scene.bmanga_active_effect_layer_name == "effect_fixed_b"
        assert int(scene.bmanga_effect_line_params.max_line_count) == 223
        assert len(calls) == call_count

        # 拒否後も編集は固定対象Bだけへ届く。
        scene.bmanga_effect_line_params.max_line_count = 224
        assert len(calls) > call_count
        assert all(
            _same_rna(obj, obj_b) and _same_rna(layer, layer_b)
            for obj, layer, _ in calls[call_count:]
        )

        # 同じSceneを表示する別Window／別WindowManager相当から2画面目を
        # 開いても、共有設定をAへ読み替える前に日本語で拒否する。
        target_a = resolver.resolve_target_from_object(bpy.context, obj_a)
        second_window_context = SimpleNamespace(
            scene=scene,
            window_manager=SimpleNamespace(),
            window=SimpleNamespace(width=1400),
        )
        try:
            runtime.begin_actual_session(second_window_context, target_a)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            assert "同時に2つ" in message and "先に開いている詳細設定" in message, message
        else:
            raise AssertionError("同じ作品画面で効果線の詳細設定を2つ開けました")
        assert scene.bmanga_active_effect_layer_name == "effect_fixed_b"
        assert int(scene.bmanga_effect_line_params.max_line_count) == 224

        runtime.cancel_actual_session(bpy.context, session)
        assert scene.bmanga_active_layer_kind == "effect"
        assert scene.bmanga_active_effect_layer_name == "effect_fixed_a"
        assert int(scene.bmanga_effect_line_params.max_line_count) == 111
        assert all(not _same_rna(obj, obj_a) for obj, _, _ in calls)
        assert all(propagate is False for _, _, propagate in calls)

        # 先の画面を閉じた後は、同じSceneで次の効果線を正常に開ける。
        calls.clear()
        session_a = runtime.begin_actual_session(bpy.context, target_a)
        runtime.cancel_actual_session(bpy.context, session_a)
        print("DETAIL_EFFECT_FIXED_TARGET_CHECK_OK", flush=True)
    finally:
        effect_op._write_effect_strokes = original_write
        addon.unregister()


if __name__ == "__main__":
    main()
