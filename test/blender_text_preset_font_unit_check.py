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


def _check_dialog_enum_items() -> None:
    try:
        rna = bpy.ops.bmanga.preset_detail_edit.get_rna_type()
    except Exception:  # noqa: BLE001
        rna = None
    _check(rna is not None, "BMANGA_OT_preset_detail_edit が登録されている")
    if rna is None:
        return
    prop = rna.properties.get("font_size_unit")
    _check(prop is not None, "font_size_unit プロパティが存在する")
    if prop is None:
        return
    idents = tuple(item.identifier for item in prop.enum_items)
    _check(
        idents == ("q", "pt"),
        f"詳細設定の単位Enum識別子がエントリ側と一致 ('q','pt') (実際: {idents})",
    )


def _check_load_type_fields(preset_detail_op) -> None:
    op_cls = preset_detail_op.BMANGA_OT_preset_detail_edit

    class _Stub:
        preset_type = "text"
        _enum_or = op_cls.__dict__["_enum_or"]

    stub = _Stub()
    data = {
        "writing_mode": "sideways",  # 不正値
        "font_size_unit": "Q",  # 旧表記
        "font_size_value": 16.0,
        "color": [0.0, 0.0, 0.0, 1.0],
    }
    try:
        op_cls._load_type_fields(stub, data)
    except Exception:  # noqa: BLE001
        FAILURES.append("_load_type_fields が例外を出さない")
        traceback.print_exc()
        return
    _check(getattr(stub, "font_size_unit", "") == "q", "旧表記 'Q' が 'q' として読み込まれる")
    _check(
        getattr(stub, "writing_mode", "") == "vertical",
        "不正な writing_mode が既定値 vertical に丸められる",
    )


def main() -> int:
    mod = _load_addon()
    try:
        text_presets = _sub("io.text_presets")
        preset_detail_op = _sub("operators.preset_detail_op")
        _check_normalizer(text_presets)
        _check_loader_normalizes(text_presets)
        _check_bundled_presets(text_presets)
        _check_dialog_enum_items()
        _check_load_type_fields(preset_detail_op)
    finally:
        try:
            mod.unregister()
        except Exception:  # noqa: BLE001  (後片付け失敗はテスト結果に影響させない)
            traceback.print_exc()
    print(f"\n結果: 失敗 {len(FAILURES)} 件", flush=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    if code:
        sys.exit(code)
