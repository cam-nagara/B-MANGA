"""Blender実機用: Meldex表示設定を無視してB-MANGA設定を守ることを確認する。

contract v2のpresentation.rubyは互換入力として検証するが、書字方向とルビ表示設定は
B-MANGAのテキストプリセットを正本とし、取込時には適用しない。文書・行別presentation、
旧ユーザー設定のオン値が残る場合、v1入力、ルビ範囲・由来・優先順位・segments、異常値の
事前拒否をBlender実機で確認する。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_meldex_presentation_v2"

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)
    else:
        print(f"OK: {message}", flush=True)


def _load_addon():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


# text_entry.py 上の既定値 (core/text_entry.py 参照)。
RUBY_FIELD_DEFAULTS: dict[str, object] = {
    "writing_mode": "horizontal",
    "ruby_size_percent": 50.0,
    "ruby_gap_em": 0.0,
    "ruby_letter_spacing": 0.0,
    "ruby_line_height": 1.8,
    "ruby_align": "center",
    "ruby_small_kana": "keep",
    "ruby_font_preset": "inherit",
    "ruby_default_style": "group",
}

FULL_RUBY_PRESENTATION: dict[str, object] = {
    "writingMode": "vertical",
    "sizePercent": 60.0,
    "gapEm": 0.5,
    "letterSpacingEm": 0.2,
    "lineHeight": 2.2,
    "align": "start",
    "smallKana": "fullsize",
    "fontPreset": "serif-jp",
    "defaultStyle": "jukugo",
}


def _rubies_for(version: int) -> list[dict]:
    if version >= 2:
        return [
            {
                "start": 0, "length": 1, "rubyText": "とう", "style": "mono",
                "origin": "local-auto-dictionary", "priority": 1,
            },
            {
                "start": 0, "length": 2, "rubyText": "とうきょう", "style": "jukugo",
                "origin": "manual", "priority": 5,
                "segments": [
                    {"start": 0, "length": 1, "rubyText": "とう"},
                    {"start": 1, "length": 1, "rubyText": "きょう"},
                ],
            },
        ]
    return [{"start": 0, "length": 2, "rubyText": "とうきょう", "style": "group"}]


def _document(document_id: str, row_id: str, version: int, *, presentation: dict | None = None) -> dict:
    payload: dict = {
        "contract": "meldex-bmanga-scenario",
        "version": version,
        "source": {"documentId": document_id},
        "pages": [{"rows": [
            {"rowId": row_id, "type": "", "body": "東京です", "rubies": _rubies_for(version)},
        ]}],
    }
    if version >= 2:
        payload["indexUnit"] = "unicode-code-point"
        payload["normalization"] = "none"
    if presentation is not None:
        payload["presentation"] = {"ruby": presentation}
    return payload


def _assert_defaults(text, *, tag: str = "") -> None:
    for prop, default in RUBY_FIELD_DEFAULTS.items():
        value = getattr(text, prop)
        if isinstance(default, float):
            _check(abs(float(value) - default) < 1.0e-6, f"{tag}{prop} は既定値 {default} のままであるべき (実際: {value})")
        else:
            _check(value == default, f"{tag}{prop} は既定値 {default!r} のままであるべき (実際: {value!r})")


def main() -> int:
    addon = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_meldex_presentation_v2_"))
    try:
        from bmanga_dev_meldex_presentation_v2.io import balloon_presets, meldex_contract, meldex_scenario_import, page_io, text_presets

        # 実際のユーザープリセット (グローバル設定フォルダ) に依存させない。
        balloon_presets.list_all_presets = lambda _path: []
        text_presets.list_all_presets = lambda _path: []

        work = bpy.context.scene.bmanga_work
        work.loaded = True
        work.work_dir = str(temp_root)
        page = page_io.register_new_page(work)
        page_io.ensure_page_dir(temp_root, page.id)
        page.detail_loaded = True
        work.active_page_index = 0

        annotations = addon.preferences.BMangaPreferences.__annotations__
        _check(
            "meldex_apply_ruby_presentation" not in annotations,
            "[UI] 廃止したMeldexルビ表示設定プロパティが登録されていない",
        )

        def _text_for(document_id: str, row_id: str = "r1"):
            return next(
                item for item in work.pages[0].texts
                if item.meldex_source_document_id == document_id and item.meldex_source_row_id == row_id
            )

        # --- 1. 旧環境にオン値が残っていても文書presentationは適用されない ---
        addon.preferences.get_preferences = lambda _context=None: SimpleNamespace(
            meldex_apply_ruby_presentation=True
        )
        doc_id = "scenario-full-v2"
        result1 = meldex_scenario_import.import_payload(
            bpy.context, work, _document(doc_id, "r1", 2, presentation=FULL_RUBY_PRESENTATION)
        )
        _check(result1["created"] == 1, f"[1] 新規行が1件作成されるはず (実際: {result1})")
        text = _text_for(doc_id)
        _assert_defaults(text, tag="[1] ")

        # 6. ルビ本体 (ruby_spans) が v2 でも優先度解決 + segments 込みで正しく生成される。
        _check(len(text.ruby_spans) == 1, f"[6] 重複解決後は1件のはず (実際: {len(text.ruby_spans)})")
        if text.ruby_spans:
            span = text.ruby_spans[0]
            _check(span.start == 0 and span.length == 2, f"[6] span範囲不一致: start={span.start} length={span.length}")
            _check(span.ruby_text == "とうきょう", f"[6] rubyText不一致: {span.ruby_text!r}")
            _check(getattr(span, "priority", None) == 5, f"[6] priorityによる重複解決に失敗: {getattr(span, 'priority', None)!r}")
            _check(getattr(span, "origin", None) == "manual", f"[6] origin不一致: {getattr(span, 'origin', None)!r}")
            segments = list(getattr(span, "segments", []))
            _check(len(segments) == 2, f"[6] segmentsは2件のはず (実際: {len(segments)})")
            if len(segments) == 2:
                _check(
                    segments[0].ruby_text == "とう" and segments[1].ruby_text == "きょう",
                    f"[6] segmentsのrubyText不一致: {[s.ruby_text for s in segments]!r}",
                )

        # --- 2. 行別presentationも適用されない ---
        doc_id2 = "scenario-row-presentation"
        row_document = _document(doc_id2, "r1", 2, presentation=FULL_RUBY_PRESENTATION)
        row_document["pages"][0]["rows"][0]["presentation"] = {
            "ruby": {**FULL_RUBY_PRESENTATION, "sizePercent": 88.0}
        }
        meldex_scenario_import.import_payload(bpy.context, work, row_document)
        text2 = _text_for(doc_id2)
        _assert_defaults(text2, tag="[2] ")

        # --- 3. version 1 (presentationなし) -> 従来どおり既定値のまま ---
        doc_id3 = "scenario-v1"
        meldex_scenario_import.import_payload(bpy.context, work, _document(doc_id3, "r1", 1))
        text3 = _text_for(doc_id3)
        _assert_defaults(text3, tag="[3] ")
        _check(
            len(text3.ruby_spans) == 1 and text3.ruby_spans[0].ruby_text == "とうきょう",
            "[3] v1 でも従来どおりルビ本体は生成されるはず",
        )

        # --- 4. v2の部分presentationも適用されない ---
        doc_id4 = "scenario-partial-v2"
        meldex_scenario_import.import_payload(
            bpy.context, work, _document(doc_id4, "r1", 2, presentation={"sizePercent": 133.0})
        )
        text4 = _text_for(doc_id4)
        _assert_defaults(text4, tag="[4] ")

        # --- 5. contract 範囲外の値 (sizePercent=300) -> reject されること ---
        before_texts = len(work.pages[0].texts)
        before_balloons = len(work.pages[0].balloons)
        invalid_doc = _document("scenario-invalid", "r1", 2, presentation={"sizePercent": 300.0})
        rejected = False
        try:
            meldex_scenario_import.import_payload(bpy.context, work, invalid_doc)
        except meldex_contract.ContractError:
            rejected = True
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(f"[5] 想定外の例外型: {exc!r}")
            traceback.print_exc()
        _check(rejected, "[5] 範囲外の sizePercent=300 は ContractError で reject されるはず (クランプではない)")
        _check(len(work.pages[0].texts) == before_texts, "[5] reject 時はテキストが新規作成されないはず")
        _check(len(work.pages[0].balloons) == before_balloons, "[5] reject 時はフキダシが新規作成されないはず")
        try:
            meldex_contract.validate_payload(invalid_doc)
            FAILURES.append("[5] validate_payload 単体でも ContractError を送出するはず")
        except meldex_contract.ContractError:
            pass

    finally:
        try:
            addon.unregister()
        except Exception:  # noqa: BLE001  (後片付け失敗はテスト結果に影響させない)
            traceback.print_exc()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)

    print(f"\n結果: 失敗 {len(FAILURES)} 件", flush=True)
    if not FAILURES:
        print("BMANGA_MELDEX_PRESENTATION_V2_OK", flush=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    if code:
        sys.exit(code)
