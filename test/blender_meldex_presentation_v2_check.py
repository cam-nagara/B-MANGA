"""Blender 5.1実機: Meldex本文・ルビ設定のオプトイン取込を確認する."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import traceback
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_meldex_presentation_v2"
FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    print(("OK: " if condition else "NG: ") + message, flush=True)
    if not condition:
        FAILURES.append(message)


def _close(actual: float, expected: float, tolerance: float = 1.0e-5) -> bool:
    return abs(float(actual) - float(expected)) <= tolerance


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


FULL_PRESENTATION = {
    "text": {
        "writingMode": "vertical",
        "fontSizePx": 18.0,
        "lineHeight": 2.0,
        "letterSpacingEm": 0.25,
        "bold": True,
        "italic": True,
        "color": "#8080FF80",
        "fontFamily": "Meldex Test Font, sans-serif",
        "strokeWidthPx": 2.0,
        "strokeColor": "#FF0000",
    },
    "ruby": {
        "writingMode": "vertical",
        "sizePercent": 60.0,
        "gapEm": 0.5,
        "letterSpacingEm": 0.2,
        "lineHeight": 2.2,
        "align": "start",
        "smallKana": "fullsize",
        "fontPreset": "serif-jp",
        "defaultStyle": "jukugo",
    },
}

PRESET = {
    "font": r"C:\Windows\Fonts\msgothic.ttc",
    "font_size_unit": "q",
    "font_size_value": 22.0,
    "font_bold": False,
    "font_italic": False,
    "color": (0.1, 0.2, 0.3, 1.0),
    "writing_mode": "horizontal",
    "line_height": 1.4,
    "letter_spacing": 0.05,
    "ruby_size_percent": 52.0,
    "ruby_gap_em": -0.05,
    "ruby_letter_spacing": 0.03,
    "ruby_line_height": 1.6,
    "ruby_align": "center",
    "ruby_small_kana": "keep",
    "ruby_font_preset": "gothic-jp",
    "ruby_default_style": "group",
    "stroke_enabled": False,
    "stroke_width_mm": 0.2,
    "stroke_color": (1.0, 1.0, 1.0, 1.0),
    "linked_balloon_preset": "",
}


def _rubies(version: int) -> list[dict]:
    if version < 2:
        return [{"start": 0, "length": 2, "rubyText": "とうきょう", "style": "group"}]
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


def _document(document_id: str, version: int, presentation: dict | None = None) -> dict:
    payload = {
        "contract": "meldex-bmanga-scenario",
        "version": version,
        "source": {"documentId": document_id},
        "pages": [{"rows": [{
            "rowId": "r1", "type": "セリフ", "body": "東京です", "rubies": _rubies(version),
        }]}],
    }
    if version >= 2:
        payload.update(indexUnit="unicode-code-point", normalization="none")
    if presentation is not None:
        payload["presentation"] = presentation
    return payload


def main() -> int:
    addon = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_meldex_presentation_v2_"))
    try:
        from bmanga_dev_meldex_presentation_v2.io import (
            balloon_presets,
            meldex_contract,
            meldex_scenario_import,
            meldex_text_presentation,
            page_io,
            text_presets,
        )
        from bmanga_dev_meldex_presentation_v2.utils import color_space

        balloon_presets.list_all_presets = lambda _path: []
        text_presets.list_all_presets = lambda _path: [
            SimpleNamespace(name="セリフ", data=PRESET)
        ]
        resolved_font = r"C:\Windows\Fonts\YuGothM.ttc"
        meldex_text_presentation.resolve_installed_font_family = lambda _family: resolved_font

        work = bpy.context.scene.bmanga_work
        work.loaded = True
        work.work_dir = str(temp_root)
        page = page_io.register_new_page(work)
        page_io.ensure_page_dir(temp_root, page.id)
        page.detail_loaded = True
        work.active_page_index = 0

        annotations = addon.preferences.BMangaPreferences.__annotations__
        _check("meldex_apply_text_presentation" in annotations, "新しい本文・ルビ共通チェックが登録される")
        _check("meldex_apply_ruby_presentation" not in annotations, "旧ルビ専用チェックは復活しない")
        prop = addon.preferences.BMangaPreferences.bl_rna.properties["meldex_apply_text_presentation"]
        _check(prop.default is False, "Meldexテキスト設定適用の初期値はオフ")
        _check(
            meldex_text_presentation._local_source_path(r"\\server\share\scenario.scriptnote.json") is None,
            "直接送信の保存元補完でUNC共有へアクセスしない",
        )
        _check(
            meldex_text_presentation._local_source_path("relative.scriptnote.json") is None,
            "直接送信の保存元補完で相対パスへアクセスしない",
        )

        def text_for(document_id: str):
            return next(
                item for item in work.pages[0].texts
                if item.meldex_source_document_id == document_id
            )

        # オフ: 旧ルビ専用の保存値が真でもB-MANGAプリセットを守る。
        addon.preferences.get_preferences = lambda _context=None: SimpleNamespace(
            meldex_apply_text_presentation=False,
            meldex_apply_ruby_presentation=True,
        )
        meldex_scenario_import.import_payload(
            bpy.context, work, _document("off", 2, FULL_PRESENTATION)
        )
        off = text_for("off")
        _check(off.writing_mode == "horizontal", "オフ時はプリセットの書字方向")
        _check(off.font == PRESET["font"], "オフ時はプリセットのフォント")
        _check(_close(off.font_size_q, 22.0), "オフ時はプリセットの本文サイズ")
        _check(_close(off.line_height, 1.4) and _close(off.letter_spacing, 0.05), "オフ時は本文行間・字間を維持")
        _check(_close(off.ruby_size_percent, 52.0) and _close(off.ruby_gap_em, -0.05), "オフ時はルビ設定を維持")

        # オン: 本文・ルビの全共通項目をプリセットの上へ適用する。
        addon.preferences.get_preferences = lambda _context=None: SimpleNamespace(
            meldex_apply_text_presentation=True
        )
        meldex_scenario_import.import_payload(
            bpy.context, work, _document("on", 2, FULL_PRESENTATION)
        )
        on = text_for("on")
        _check(on.writing_mode == "vertical", "オン時はMeldexの書字方向")
        _check(on.font == resolved_font, "オン時は論理フォント名をこのPCのフォントへ解決")
        _check(_close(on.font_size_q, 18.0 * 127.0 / 120.0), "CSS pxを物理サイズ同等のQへ変換")
        _check(_close(on.line_height, 2.0) and _close(on.letter_spacing, 0.25), "本文行間・字間を適用")
        _check(on.font_bold and on.font_italic, "本文の太字・斜体を適用")
        expected_rgb = color_space.srgb_to_linear_rgb((128 / 255, 128 / 255, 1.0))
        _check(all(_close(on.color[i], expected_rgb[i]) for i in range(3)) and _close(on.color[3], 128 / 255), "本文色をsRGBからBlender色へ変換")
        _check(on.stroke_enabled and _close(on.stroke_width_mm, 2.0 * 25.4 / 96.0), "本文フチ幅をpxからmmへ変換")
        _check(_close(on.stroke_color[0], 1.0) and _close(on.stroke_color[1], 0.0), "本文フチ色を適用")
        _check(
            on.ruby_size_percent == 60.0 and _close(on.ruby_gap_em, 0.5)
            and _close(on.ruby_letter_spacing, 0.2) and _close(on.ruby_line_height, 2.2),
            "ルビのサイズ・距離・字間・行間を適用",
        )
        _check(
            on.ruby_align == "start" and on.ruby_small_kana == "fullsize"
            and on.ruby_font_preset == "serif-jp" and on.ruby_default_style == "jukugo",
            "ルビ配置・小書き・論理フォント・既定種類を適用",
        )
        _check(len(on.ruby_spans) == 1 and len(on.ruby_spans[0].segments) == 2, "ルビ内容・優先順位・内訳は設定切替と無関係に維持")

        # 現行Meldexの直接送信payloadが本文設定をまだ含まない場合も、保存元
        # シナリオの表示設定だけを補完し、送信payloadの本文・ルビ内容は守る。
        source_path = temp_root / "direct.scriptnote.json"
        source_path.write_text(json.dumps({
            "fileType": "meldex-scriptnote",
            "version": 2,
            "title": "直接送信",
            "layoutMode": "manga",
            "editor": {
                "viewMode": "vertical",
                "baseTextFontSize": 20,
                "baseTextLineHeightV": 1.9,
                "baseTextLetterSpacingV": 0.3,
            },
            "rubyPresentation": FULL_PRESENTATION["ruby"],
            "characters": [{"name": "セリフ"}],
            "rows": [{"id": "r1", "role": "セリフ", "text": "保存元とは異なる本文"}],
        }, ensure_ascii=False), encoding="utf-8")
        meldex_scenario_import.import_payload(
            bpy.context,
            work,
            _document(str(source_path), 2, {"ruby": FULL_PRESENTATION["ruby"]}),
        )
        direct = text_for(str(source_path))
        _check(direct.body == "東京です", "直接送信payloadの本文を保存元で上書きしない")
        _check(
            direct.writing_mode == "vertical"
            and _close(direct.font_size_q, 20.0 * 127.0 / 120.0)
            and _close(direct.line_height, 1.9)
            and _close(direct.letter_spacing, 0.3),
            "直接送信で不足する本文設定を保存元シナリオから補完",
        )

        # 行別設定は文書設定より優先する。
        row_payload = _document("row", 2, FULL_PRESENTATION)
        row_payload["pages"][0]["rows"][0]["presentation"] = {
            "text": {"writingMode": "horizontal", "fontSizePx": 24.0, "bold": False},
            "ruby": {"sizePercent": 88.0, "gapEm": -0.1},
        }
        meldex_scenario_import.import_payload(bpy.context, work, row_payload)
        row = text_for("row")
        _check(row.writing_mode == "horizontal" and _close(row.font_size_q, 25.4), "行別本文設定が文書設定より優先")
        _check(not row.font_bold and row.font_italic, "行別の部分上書き後も文書設定の残りを維持")
        _check(_close(row.ruby_size_percent, 88.0) and _close(row.ruby_gap_em, -0.1), "行別ルビ設定が文書設定より優先")

        # 既存行をオフで再取込した時は手動設定を上書きしない。
        row.line_height = 1.23
        addon.preferences.get_preferences = lambda _context=None: SimpleNamespace(
            meldex_apply_text_presentation=False
        )
        changed = _document("row", 2, {**FULL_PRESENTATION, "text": {**FULL_PRESENTATION["text"], "lineHeight": 3.0}})
        meldex_scenario_import.import_payload(bpy.context, work, changed)
        _check(_close(text_for("row").line_height, 1.23), "オフでの再取込は既存の手動設定を維持")

        # v1はオンでも従来のプリセットを使う。
        addon.preferences.get_preferences = lambda _context=None: SimpleNamespace(
            meldex_apply_text_presentation=True
        )
        meldex_scenario_import.import_payload(bpy.context, work, _document("v1", 1))
        v1 = text_for("v1")
        _check(v1.writing_mode == "horizontal" and _close(v1.font_size_q, 22.0), "v1は従来どおりプリセットを使用")

        # 異常な本文設定は変更前に拒否する。
        before = len(work.pages[0].texts)
        invalid = _document("invalid", 2, {"text": {"fontSizePx": 999.0}})
        try:
            meldex_contract.validate_payload(invalid)
        except meldex_contract.ContractError:
            pass
        else:
            FAILURES.append("範囲外の本文サイズをContractErrorで拒否する")
        try:
            meldex_scenario_import.import_payload(bpy.context, work, invalid)
        except meldex_contract.ContractError:
            pass
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(f"異常値で想定外の例外: {exc!r}")
        else:
            FAILURES.append("異常値の取込が成功してしまった")
        _check(len(work.pages[0].texts) == before, "異常値拒否時は作品を変更しない")
    finally:
        try:
            addon.unregister()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)

    print(f"\n結果: 失敗 {len(FAILURES)} 件", flush=True)
    if not FAILURES:
        print("BMANGA_MELDEX_PRESENTATION_V2_OK", flush=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
