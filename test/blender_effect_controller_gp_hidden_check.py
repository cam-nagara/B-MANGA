"""効果線の編集用 GP (controller) が常にビューポート非表示であることを確認.

表示状態のグリースペンシルは Blender がビューポートを毎フレーム再描画させ続け、
用紙ガイド線・効果線などの細線がずっと点滅する (TAA が settle しない)。効果線の
画面表示は表示用 Mesh が担うため、編集用 GP は作成時・同期時とも非表示でなければ
ならない。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_effect_gp_hidden",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_effect_gp_hidden"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_effect_gp_hidden_"))
    mod = None
    try:
        mod = _load_addon()
        if "FINISHED" not in bpy.ops.bmanga.work_new(filepath=str(temp_root / "EffectGPHidden.bmanga")):
            raise AssertionError("作品作成に失敗しました")

        from bmanga_dev_effect_gp_hidden.core.work import get_work
        from bmanga_dev_effect_gp_hidden.utils import effect_line_object as elo

        scene = bpy.context.scene
        work = get_work(bpy.context)
        page = work.pages[0]
        page_id = str(page.id)

        # 効果線の編集用 GP を作成。
        controller = elo.create_effect_line_object(
            scene=scene,
            bmanga_id="effect_test_0001",
            title="効果線テスト",
            z_index=210,
            parent_kind="page",
            parent_key=page_id,
        )
        if controller is None:
            raise AssertionError("効果線の作成に失敗しました")
        if str(getattr(controller, "type", "")) != "GREASEPENCIL":
            raise AssertionError(f"効果線コントローラが GP ではありません: {controller.type}")

        # 1) 作成直後に非表示であること。
        if not bool(controller.hide_viewport):
            raise AssertionError(
                "作成直後の効果線編集用 GP が表示状態です "
                "(表示 GP は Blender のビューポートを連続再描画させ、細線が点滅する)"
            )

        # 2) 何らかの理由で表示状態に戻されても、同期で非表示へ戻ること
        #    (既存ファイルに表示状態のまま残った編集用 GP の救済)。
        controller.hide_viewport = False
        controller.hide_render = False
        elo.sync_effect_display_transform(controller)
        if not bool(controller.hide_viewport):
            raise AssertionError(
                "同期後も効果線編集用 GP が表示状態のままです "
                "(sync_effect_display_transform で非表示へ戻していない)"
            )

        print("BMANGA_EFFECT_CONTROLLER_GP_HIDDEN_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
