"""Blender 実機(背景)用: コマ用 blend 魚眼レイアウト時のレターボックス
オーバーレイ描画関数を呼び出して例外なく完了することを確認する."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_fisheye_overlay",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_fisheye_overlay"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> int:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_addon()
    try:
        from bmanga_dev_fisheye_overlay.ui import coma_fisheye_overlay as fo

        # 1) blend ファイルパスが空ならコマ用 blend ではない
        ok_empty = not fo._is_coma_blend_file()
        assert ok_empty, "empty filepath should not be coma blend"
        print("[ok] empty filepath → not coma blend")

        # 2) _draw_callback はコマ用 blend でない時は即 return
        #    (例外を起こさないことを確認)
        try:
            fo._draw_callback()
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"draw_callback raised on non-coma blend: {exc}")
        print("[ok] draw_callback no-op on non-coma blend")

        # 3) register/unregister が確実に handle を on/off する
        assert fo._handle is not None, "handle should be set after register"
        print(f"[ok] handle registered: {fo._handle}")

        # 4) double register は safe
        fo.register()
        print("[ok] double register safe")

        # 5) unregister 後に handle が None
        fo.unregister()
        assert fo._handle is None, "handle should be None after unregister"
        print("[ok] handle cleared after unregister")

        # 6) double unregister は safe
        fo.unregister()
        print("[ok] double unregister safe")

        print("\nALL PASS")
        return 0
    finally:
        mod.unregister()


if __name__ == "__main__":
    sys.exit(main())
