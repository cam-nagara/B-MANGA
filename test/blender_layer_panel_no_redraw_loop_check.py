"""レイヤーパネルの draw が「解消できない placeholder 行」で再描画ループしないことを確認.

レイヤーパネルの draw() は毎回 ``schedule_layer_stack_draw_maintenance`` を呼ぶ。
placeholder 行 (空ラベル/重複 UID) があると sync をタイマー予約し、その sync は
``tag_view3d_redraw`` を繰り返すため、placeholder が sync で解消できない場合
「B-MANGA パネルを開いている間ずっとビューポートが再描画され続ける」点滅ループに
なっていた (実測 約16回/秒)。

signature が前回スケジュール時から変わっていなければ再予約しない、という
ガードにより、解消不能な placeholder 状態でも sync は一度しか予約されず
(= ループしない) ことを保証する。
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
        "bmanga_dev_layer_loop",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_layer_loop"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_layer_loop_"))
    mod = None
    try:
        mod = _load_addon()
        bpy.context.scene.bmanga_overview_mode = True
        if "FINISHED" not in bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "LayerLoop.bmanga")
        ):
            raise AssertionError("作品作成に失敗しました")

        from bmanga_dev_layer_loop.utils import layer_stack as LS

        # 「sync しても解消できない placeholder」かつ「signature 不変」を強制し、
        # パネルの draw が繰り返し呼ばれる状況を模擬する。
        LS._stack_has_placeholder_rows = lambda stack: True
        LS._stack_signature = lambda scene: ("FIXED",)
        LS._draw_stack_signatures.clear()

        scheduled = 0
        for _ in range(8):
            # タイマー完了相当: 次の予約が可能な状態に戻す。
            LS._sync_scheduled = False
            if LS.schedule_layer_stack_draw_maintenance(bpy.context):
                scheduled += 1

        if scheduled != 1:
            raise AssertionError(
                "レイヤーパネルの draw 維持処理が placeholder で再描画ループしています "
                f"(8 回の draw で sync を {scheduled} 回予約。1 回が正 = ループ無し)"
            )

        print("BMANGA_LAYER_PANEL_NO_REDRAW_LOOP_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
