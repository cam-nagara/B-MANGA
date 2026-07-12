"""Blender実機用: テキストプリセットのサイズ単位 (font_size_unit) 正規化の回帰確認.

v0.6.497 実機で、テキストプリセットの詳細設定を開くと
`TypeError: enum "q" not found in ('Q', 'pt', 'mm')` で落ちる問題が発生した。

原因は単位 Enum の識別子が2系統に割れていたこと:
  - 正規形 (core/text_entry.py の _FONT_SIZE_UNIT_ITEMS): ("q", "pt") 小文字
  - preset_detail_op.py のダイアログ定義: ("Q", "pt", "mm") 大文字 + 存在しない mm
同梱プリセット JSON も "Q" で保存されており、エントリへの適用
(io/text_presets.py apply_to_entry) は素の setattr + 握りつぶしのため
単位だけ無言で適用失敗していた。

この回帰テストは以下を確認する:
  1. normalize_font_size_unit が大文字・不正値・欠損を正規形へ丸める。
  2. プリセット読込 (_list_in_dir) が旧 "Q" 表記を正規化して返す。
  3. 同梱テキストプリセットの font_size_unit がすべて正規形。
  4. 詳細設定ダイアログの Enum 識別子がエントリ側と同じ ("q", "pt")。
  5. _load_type_fields が "Q" / 不正な writing_mode を含むデータでも
     例外を出さず正規値を設定する。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import tempfile
import traceback
from pathlib import Path

import bpy  # noqa: F401  (Blender 実機での実行を前提とする)


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_text_preset_font_unit"

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)
    else:
        print(f"OK: {message}", flush=True)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    return importlib.import_module(f"{MOD_NAME}.{path}")


def _check_normalizer(text_presets) -> None:
    cases = [
        ("Q", "q"),
        ("q", "q"),
        ("PT", "pt"),
        ("pt", "pt"),
        ("mm", "q"),
        ("", "q"),
        (None, "q"),
        (" q ", "q"),
    ]
    for raw, expected in cases:
        got = text_presets.normalize_font_size_unit(raw)
        _check(got == expected, f"normalize_font_size_unit({raw!r}) == {expected!r} (実際: {got!r})")


def _check_loader_normalizes(text_presets) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        payload = {
            "schemaVersion": 1,
            "presetType": "text",
            "presetName": "旧表記プリセット",
            "writing_mode": "vertical",
            "font_size_unit": "Q",
            "font_size_value": 20.0,
        }
        (base / "legacy.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        loaded = text_presets._list_in_dir(base, source="user")
        _check(len(loaded) == 1, "旧表記プリセットが読み込める")
        if loaded:
            unit = loaded[0].data.get("font_size_unit")
            _check(unit == "q", f"読込時に 'Q' が 'q' へ正規化される (実際: {unit!r})")


def _check_bundled_presets(text_presets) -> None:
    presets = text_presets.list_global_presets()
    _check(len(presets) >= 1, "同梱テキストプリセットが1件以上ある")
    for preset in presets:
        unit = preset.data.get("font_size_unit", "q")
        _check(
            unit in {"q", "pt"},
            f"同梱プリセット {preset.name} の font_size_unit が正規形 (実際: {unit!r})",
        )


def _check_dialog_enum_items(context) -> None:
    """プリセット詳細編集ダイアログの font_size_unit Enum を確認する.

    2026-07-13 の「各ツールの通常の詳細設定ダイアログを共用する」改修で、
    プリセット詳細編集専用の Enum 定義 (BMANGA_OT_preset_detail_edit 自身の
    font_size_unit プロパティ) は廃止された。ダイアログは
    core/text_entry.py の BMangaTextEntry をそのまま (WindowManager 上の
    スクラッチインスタンスとして) 編集するため、単位 Enum の二重管理その
    ものが構造的に無くなっている。ここではスクラッチインスタンスの型が
    実際に BMangaTextEntry であること・その font_size_unit の識別子が
    ('q', 'pt') であることを確認する。
    """
    wm = context.window_manager
    scratch = getattr(wm, "bmanga_preset_scratch_text", None)
    _check(
        scratch is not None,
        "プリセット詳細編集用のテキストスクラッチ (bmanga_preset_scratch_text) が登録されている",
    )
    if scratch is None:
        return
    _check(
        type(scratch).__name__ == "BMangaTextEntry",
        "プリセット詳細編集のテキストスクラッチが core.text_entry.BMangaTextEntry そのもの"
        f" (実際: {type(scratch).__name__}) — 別定義の Enum を持ち得ない構造になっている",
    )
    prop = scratch.bl_rna.properties.get("font_size_unit")
    _check(prop is not None, "font_size_unit プロパティが存在する")
    if prop is None:
        return
    idents = tuple(item.identifier for item in prop.enum_items)
    _check(
        idents == ("q", "pt"),
        f"詳細設定の単位Enum識別子がエントリ側と一致 ('q','pt') (実際: {idents})",
    )


def _check_load_type_fields(context, text_presets, preset_detail_op) -> None:
    """旧表記 'Q' / 不正な writing_mode を含む legacy プリセットの読込確認 (v0.6.497 回帰).

    専用ダイアログの ``_load_type_fields`` / ``_enum_or`` は廃止された。
    実際に (BMANGA_USER_CONFIG_DIR 配下の) ローカルプリセットとして legacy
    形式のファイルを書き、本番と同じ経路
    (``text_presets.load_preset_by_name`` → 正規化 →
    ``preset_detail_op._load_text`` → ``text_presets.apply_to_entry``) を
    通して、単位の正規化と不正値の安全な扱いを確認する。
    """
    import json

    target_dir = text_presets._local_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": 1,
        "presetType": "text",
        "presetName": "legacy_probe",
        "description": "legacy probe",
        "writing_mode": "sideways",  # 不正値
        "font_size_unit": "Q",  # 旧表記
        "font_size_value": 16.0,
        "color": [0.0, 0.0, 0.0, 1.0],
    }
    (target_dir / "legacy_probe.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    try:
        description = preset_detail_op._load_text(context, "legacy_probe")
    except Exception:  # noqa: BLE001
        FAILURES.append("_load_text が legacy データで例外を出さない")
        traceback.print_exc()
        return
    _check(description == "legacy probe", f"_load_text が説明を返す (実際: {description!r})")
    scratch = context.window_manager.bmanga_preset_scratch_text
    _check(getattr(scratch, "font_size_unit", "") == "q", "旧表記 'Q' が 'q' として読み込まれる")
    entry_writing_mode = getattr(scratch, "writing_mode", None)
    _check(
        entry_writing_mode == "vertical",
        f"不正な writing_mode が既定値 vertical のまま残る (実際: {entry_writing_mode!r})",
    )


def main() -> int:
    import os
    import shutil

    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_text_preset_font_unit_"))
    old_config = os.environ.get("BMANGA_USER_CONFIG_DIR")
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        context = bpy.context
        text_presets = _sub("io.text_presets")
        preset_detail_op = _sub("operators.preset_detail_op")
        _check_normalizer(text_presets)
        _check_loader_normalizes(text_presets)
        _check_bundled_presets(text_presets)
        _check_dialog_enum_items(context)
        _check_load_type_fields(context, text_presets, preset_detail_op)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001  (後片付け失敗はテスト結果に影響させない)
                traceback.print_exc()
        if old_config is None:
            os.environ.pop("BMANGA_USER_CONFIG_DIR", None)
        else:
            os.environ["BMANGA_USER_CONFIG_DIR"] = old_config
        shutil.rmtree(temp_root, ignore_errors=True)
    print(f"\n結果: 失敗 {len(FAILURES)} 件", flush=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    if code:
        sys.exit(code)
