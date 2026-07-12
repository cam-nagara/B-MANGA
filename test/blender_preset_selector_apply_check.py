"""Blender 実機用: プリセットリスト選択の「選択中レイヤーへの即時適用」確認.

詳細設定ダイアログやツールパネルのプリセットリストでプリセットを選ぶと、
選択中のレイヤー (テキスト / 囲い塗り / グラデーション / パターンカーブ /
フキダシ) へ即時適用されることと、リネーム・削除等の後始末でセレクタが
プログラム的に書き換わった時には適用されないこと (suppress_selector_apply)
を検証する。2026-07-13 のユーザー報告「詳細設定ダイアログ上でプリセットを
切り替えても内容が切り替わらない」の回帰テスト。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
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


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_preset_apply_"))
    # ユーザーの共通プリセット保存先を汚さないよう一時フォルダへ隔離する。
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PresetApply.bmanga"))
        assert result == {"FINISHED"}, result
        context = bpy.context
        scene = context.scene
        wm = context.window_manager
        work = scene.bmanga_work
        page = work.pages[0]

        from bmanga_dev.io import (
            fill_presets,
            gradient_presets,
            image_path_presets,
            text_presets,
        )
        from bmanga_dev.operators import (
            balloon_preset_op,
            preset_op,
            text_preset_op,
        )

        # ---- テキスト ----
        text_presets.save_local_preset(None, "適用テストA", "", {
            "writing_mode": "horizontal",
            "font_size_value": 30.0,
            "font_size_unit": "q",
            "line_height": 2.0,
            "letter_spacing": 0.25,
            "color": [1.0, 0.0, 0.0, 1.0],
            "font_bold": True,
            "font_italic": False,
            "stroke_enabled": True,
        })
        text_presets.save_local_preset(None, "適用テストB", "", {
            "writing_mode": "vertical",
            "line_height": 1.1,
            "font_bold": False,
        })
        entry = page.texts.add()
        entry.id = "text_apply_check"
        entry.body = "テスト"
        entry.x_mm = 10.0
        entry.y_mm = 10.0
        entry.width_mm = 30.0
        entry.height_mm = 30.0
        page.active_text_index = 0
        scene.bmanga_active_layer_kind = "text"

        wm.bmanga_text_tool_preset_selector = "適用テストA"
        entry = page.texts[0]
        assert entry.writing_mode == "horizontal", entry.writing_mode
        assert abs(float(entry.line_height) - 2.0) < 1e-6, entry.line_height
        assert bool(entry.font_bold) is True

        # リネーム・削除等の後始末経路 (_set_text_preset_selector) では適用しない
        text_preset_op._set_text_preset_selector(context, "適用テストB")
        entry = page.texts[0]
        assert str(wm.bmanga_text_tool_preset_selector) == "適用テストB"
        assert entry.writing_mode == "horizontal", "後始末経路の再設定で適用されてしまった"

        # アクティブレイヤー種別が text 以外なら適用しない
        scene.bmanga_active_layer_kind = "gp"
        wm.bmanga_text_tool_preset_selector = "適用テストA"
        wm.bmanga_text_tool_preset_selector = "適用テストB"
        entry = page.texts[0]
        assert entry.writing_mode == "horizontal", "テキスト非選択時に適用されてしまった"

        # ---- 囲い塗り (ベタ塗り) ----
        fill_presets.save_local_preset("塗り適用テスト", "", {
            "color": [0.2, 0.3, 0.4, 1.0],
            "opacity": 55,
        })
        fill = scene.bmanga_fill_layers.add()
        fill.id = "fill_apply_check"
        scene.bmanga_active_fill_layer_index = 0
        scene.bmanga_active_layer_kind = "fill"
        wm.bmanga_fill_tool_preset_selector = "塗り適用テスト"
        fill = scene.bmanga_fill_layers[0]
        assert abs(float(fill.opacity) - 55.0) < 1e-3, fill.opacity
        assert abs(float(fill.color[0]) - 0.2) < 1e-5, tuple(fill.color)

        # ---- グラデーション ----
        gradient_presets.save_local_preset("グラデ適用テスト", "", {
            "gradient_type": "radial",
            "color": [0.0, 0.0, 0.0, 1.0],
            "color2": [1.0, 1.0, 0.0, 1.0],
            "opacity": 70,
        })
        grad = scene.bmanga_fill_layers.add()
        grad.id = "gradient_apply_check"
        grad.fill_type = "gradient"
        scene.bmanga_active_fill_layer_index = 1
        scene.bmanga_active_layer_kind = "fill"
        wm.bmanga_gradient_tool_preset_selector = "グラデ適用テスト"
        grad = scene.bmanga_fill_layers[1]
        assert str(grad.gradient_type) == "radial", grad.gradient_type
        assert abs(float(grad.opacity) - 70.0) < 1e-3, grad.opacity
        # ベタ塗りレイヤーがアクティブな時にグラデプリセットを選んでも適用されない
        scene.bmanga_active_fill_layer_index = 0
        solid_before = str(scene.bmanga_fill_layers[0].fill_type)
        gradient_presets.save_local_preset("グラデ適用テスト2", "", {
            "gradient_type": "linear",
            "opacity": 10,
        })
        wm.bmanga_gradient_tool_preset_selector = "グラデ適用テスト2"
        assert str(scene.bmanga_fill_layers[0].fill_type) == solid_before
        assert abs(float(scene.bmanga_fill_layers[0].opacity) - 55.0) < 1e-3, (
            "ベタ塗りレイヤーへグラデプリセットが適用されてしまった"
        )

        # ---- パターンカーブ ----
        path_entry = scene.bmanga_image_path_layers.add()
        path_entry.id = "image_path_apply_check"
        path_entry.opacity = 40.0
        path_entry.spacing_percent = 120.0
        image_path_presets.save_local_preset(None, path_entry, "パス適用テスト", "")
        path_entry.opacity = 100.0
        path_entry.spacing_percent = 10.0
        scene.bmanga_active_image_path_layer_index = 0
        scene.bmanga_active_layer_kind = "image_path"
        wm.bmanga_image_path_tool_preset_selector = "パス適用テスト"
        path_entry = scene.bmanga_image_path_layers[0]
        assert abs(float(path_entry.opacity) - 40.0) < 1e-3, path_entry.opacity
        assert abs(float(path_entry.spacing_percent) - 120.0) < 1e-3, path_entry.spacing_percent

        # ---- フキダシ (基本形状) ----
        balloon = page.balloons.add()
        balloon.id = "balloon_apply_check"
        balloon.x_mm = 20.0
        balloon.y_mm = 20.0
        balloon.width_mm = 40.0
        balloon.height_mm = 30.0
        page.active_balloon_index = 0
        scene.bmanga_active_layer_kind = "balloon"
        wm.bmanga_balloon_tool_preset_selector = "shape:ellipse"
        balloon = page.balloons[0]
        assert str(balloon.shape) == "ellipse", balloon.shape
        # 「なめらか自由形状」は作成方式の切替であり、形状には適用しない
        wm.bmanga_balloon_tool_preset_selector = preset_op.BALLOON_TOOL_NURBS_PRESET
        balloon = page.balloons[0]
        assert str(balloon.shape) == "ellipse", "なめらか自由形状で形状が変わってしまった"
        # 後始末経路では適用しない ("DEFAULT" へ再設定されても形状は保持)
        balloon_preset_op._set_balloon_preset_selector(context, "")
        balloon = page.balloons[0]
        assert str(balloon.shape) == "ellipse", "後始末経路の再設定で形状が変わってしまった"

        print("BMANGA_PRESET_SELECTOR_APPLY_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        os.environ.pop("BMANGA_USER_CONFIG_DIR", None)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
