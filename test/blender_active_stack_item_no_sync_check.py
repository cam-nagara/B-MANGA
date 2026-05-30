"""active_stack_item が「読み取りなのに同期(書き込み)」しないことを確認.

active_stack_item はアクティブなレイヤー項目を読むだけの関数で、パネルの draw・
ツール・ハンドラから高頻度に呼ばれる。以前はここで毎回 sync_layer_stack を呼んで
おり、これはレイヤー一覧を作り直して Scene に書き込む副作用がある。書き込みは
depsgraph 更新 → ビューポート再描画 → また呼ばれる、の連鎖になり、「B-Name パネルを
開いている間ずっと細線が点滅する」再描画ループ(実測 約15回/秒)の真因だった。

読み取りでは sync_layer_stack を呼ばない(書き込まない)ことを保証する。
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
        "bname_dev_active_no_sync",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_active_no_sync"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_active_no_sync_"))
    mod = None
    try:
        mod = _load_addon()
        bpy.context.scene.bname_overview_mode = True
        if "FINISHED" not in bpy.ops.bname.work_new(
            filepath=str(temp_root / "ActiveNoSync.bname")
        ):
            raise AssertionError("作品作成に失敗しました")

        from bname_dev_active_no_sync.utils import layer_stack as LS

        # 初期同期 (この時点では一覧は最新)。
        LS.sync_layer_stack(bpy.context)

        calls = {"n": 0}
        orig = LS.sync_layer_stack

        def counting_sync(*args, **kwargs):
            calls["n"] += 1
            return orig(*args, **kwargs)

        LS.sync_layer_stack = counting_sync
        try:
            for _ in range(10):
                LS.active_stack_item(bpy.context)
        finally:
            LS.sync_layer_stack = orig

        if calls["n"] != 0:
            raise AssertionError(
                "active_stack_item が読み取りなのに sync_layer_stack を呼んでいます "
                f"(10 回の読み取りで {calls['n']} 回同期 = 書き込み → 再描画ループの原因)"
            )

        print("BNAME_ACTIVE_STACK_ITEM_NO_SYNC_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
