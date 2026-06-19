"""Blender ヘッドレス実行スクリプト（連続実行アプリのワーカーが呼び出す）。

連続実行アプリ（tools/render_batch）の worker が、PCごとに

    blender --background "<file.blend>" --python runner.py -- <サブコマンド> ...

の形で起動する。Blender の UI は使わず、B-MANGA Render の
プリセットを名前指定で1件実行する。レンダー時間の計測ログは
アドオン側 ``batch_log`` が環境変数 ``BMANGA_BATCH_LOG`` のパスへ
書き出す（worker がそのパスを env で渡す）。

サブコマンド:
  --list-presets            : この .blend のプリセット名一覧を JSON で出力
  --run --preset "<名前>"   : 指定プリセットを1件実行

共通オプション:
  --result "<path>"         : 実行結果サマリ JSON の書き出し先
  --addon-dir "<path>"      : b_manga_render を含む addons ディレクトリ
                              （拡張として未導入の環境向け。省略時は導入済み拡張を探す）
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

import bpy

ADDON_PKG = "b_manga_render"


def _args_after_ddash() -> list[str]:
    argv = sys.argv
    return argv[argv.index("--") + 1 :] if "--" in argv else []


def _parse(args: list[str]) -> dict:
    opts = {
        "mode": "",  # "list" or "run"
        "preset": "",
        "result": "",
        "addon_dir": "",
    }
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--list-presets":
            opts["mode"] = "list"
        elif a == "--run":
            opts["mode"] = "run"
        elif a == "--preset":
            i += 1
            opts["preset"] = args[i] if i < len(args) else ""
        elif a == "--result":
            i += 1
            opts["result"] = args[i] if i < len(args) else ""
        elif a == "--addon-dir":
            i += 1
            opts["addon_dir"] = args[i] if i < len(args) else ""
        i += 1
    return opts


def _find_loaded_module():
    """すでに読み込み済みの b_manga_render パッケージ本体を探す。

    Blender 4.2+ の拡張はパッケージ名が ``bl_ext.<repo>.b_manga_render``
    などになるため、末尾一致で柔軟に探す。
    """
    for name, module in list(sys.modules.items()):
        if name == ADDON_PKG or name.endswith("." + ADDON_PKG):
            if hasattr(module, "register"):
                return module
    return None


def _ensure_addon(addon_dir: str):
    """B-MANGA Render を有効化し、パッケージ本体モジュールを返す。

    1) 既に拡張として読み込み済みならそれを使う。
    2) addon_dir 指定があれば sys.path 経由で import して register。
    3) どちらも無ければ、導入済みアドオンを addon_utils 経由で enable。
    """
    module = _find_loaded_module()
    if module is not None:
        return module

    if addon_dir:
        addons_path = str(Path(addon_dir).resolve())
        if addons_path not in sys.path:
            sys.path.insert(0, addons_path)
        import importlib

        module = importlib.import_module(ADDON_PKG)
        if hasattr(module, "register"):
            try:
                module.register()
            except Exception:  # noqa: BLE001
                # 既に登録済みなら無害だが、本物の登録失敗(クラス重複・プロパティ
                # 定義エラー等)まで黙殺すると原因が見えなくなる。診断のため出力する。
                traceback.print_exc()
        return module

    # 導入済み拡張/アドオンの有効化を試みる。
    try:
        import addon_utils

        for mod in addon_utils.modules():
            mod_name = str(getattr(mod, "__name__", "") or "")
            if mod_name == ADDON_PKG or mod_name.endswith("." + ADDON_PKG):
                addon_utils.enable(mod_name, default_set=False, persistent=False)
                return _find_loaded_module()
    except Exception:  # noqa: BLE001
        pass
    return _find_loaded_module()


def _get_submodule(module, sub: str):
    """パッケージ本体から子モジュール（core / command_runner）を取り出す。"""
    direct = getattr(module, sub, None)
    if direct is not None:
        return direct
    pkg = str(getattr(module, "__name__", "") or "")
    full = pkg + "." + sub
    if full in sys.modules:
        return sys.modules[full]
    import importlib

    return importlib.import_module(full)


def _state():
    scene = bpy.context.scene
    return getattr(scene, "bmanga_render_state", None)


def _write_result(path: str, data: dict) -> None:
    if not path:
        print(json.dumps(data, ensure_ascii=False))
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(p))


def main() -> int:
    opts = _parse(_args_after_ddash())
    blend_path = str(getattr(bpy.data, "filepath", "") or "")

    try:
        module = _ensure_addon(opts["addon_dir"])
        if module is None:
            raise RuntimeError("B-MANGA Render を有効化できません（拡張が見つかりません）")
        state = _state()
        if state is None:
            raise RuntimeError("このファイルに B-MANGA Render のデータがありません")

        if opts["mode"] == "list":
            names = [str(p.name) for p in state.presets]
            _write_result(opts["result"], {"ok": True, "blend_path": blend_path, "presets": names})
            print(f"[runner] presets={names}")
            return 0

        if opts["mode"] == "run":
            if not opts["preset"]:
                raise RuntimeError("--preset でプリセット名を指定してください")
            command_runner = _get_submodule(module, "command_runner")
            count = command_runner.run_preset_by_name(bpy.context, opts["preset"])
            _write_result(
                opts["result"],
                {
                    "ok": True,
                    "blend_path": blend_path,
                    "preset": opts["preset"],
                    "exec_count": int(count),
                },
            )
            print(f"[runner] ran preset={opts['preset']!r} exec_count={count}")
            return 0

        raise RuntimeError("サブコマンドが指定されていません（--list-presets か --run）")

    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _write_result(
            opts["result"],
            {"ok": False, "blend_path": blend_path, "preset": opts.get("preset", ""), "error": str(exc)},
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
