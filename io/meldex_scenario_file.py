"""Meldex Scenario保存ファイルをB-MANGA取込契約へ変換する純粋ロジック."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
from typing import Any


CONTRACT_NAME = "meldex-bmanga-scenario"
CONTRACT_VERSION = 2
MAX_FILE_BYTES = 32 * 1024 * 1024
SUPPORTED_SUFFIXES = (".mel-scenario", ".scriptnote.json")

_DEFAULT_PRESENTATION = {
    "writingMode": "horizontal",
    "sizePercent": 50.0,
    "gapEm": 0.0,
    "letterSpacingEm": 0.0,
    "lineHeight": 1.8,
    "align": "center",
    "smallKana": "keep",
    "fontPreset": "inherit",
    "defaultStyle": "group",
}
# Meldex旧描画の承認済み実測値。旧CSSは14pxの親文字span（交差方向は
# 横書き14px／縦書き18px）に対し `100% - legacyOffsetPx` でルビを
# 置いていた。新しい相対距離は仮想親文字端 `(crossSize + baseEm) / 2` から
# 測るため、この値を使って
# 一度だけgapEmへ変換する。固定の符号反転ではなく旧レンダー座標の変換である。
_MELDEX_LEGACY_BASE_EM_PX = 14.0
_MELDEX_LEGACY_HORIZONTAL_CROSS_SIZE_PX = 14.0
_MELDEX_LEGACY_VERTICAL_CROSS_SIZE_PX = 18.0
_BREAK_NAMES = {
    "manga": {"めくり", "改ページ", "柱"},
    "drama": {"シーン見出し", "場面転換", "柱"},
    "afureko": {"シーン見出し", "場面転換", "Aパート", "Bパート", "Cパート", "柱"},
    "stage": {"第一幕", "第二幕", "第三幕", "場", "柱"},
}
_SUMMARY_NAMES = {"プロット"}
_RUBY_INNER = r"(?:\\[\\{|}]|[^{|}\\])+"
_LINK_LABEL = r"(?:\\.|[^\]])+"
_VISIBLE_RE = re.compile(
    rf"(?P<ruby>\{{(?P<base>{_RUBY_INNER})\|(?P<reading>{_RUBY_INNER})\}})"
    rf"|(?P<link>\[(?P<label>{_LINK_LABEL})\]\(ml:(?P<target>[^)]+)\))"
)
_RUBY_ESCAPE_RE = re.compile(r"\\([\\{|}])")
_PLAIN_ESCAPE_RE = re.compile(r"\\([\\{|}\[\]])")
_LINK_LABEL_ESCAPE_RE = re.compile(r"\\([\[\]\\])")


class ScenarioFileError(ValueError):
    """選択されたファイルをMeldexシナリオとして読めない場合に送出する."""


def load_contract_payload(filepath: str | Path) -> dict[str, Any]:
    """Meldexシナリオを読み、検証前のB-MANGA契約payloadへ変換する."""
    path = Path(filepath)
    if not _is_supported_path(path):
        raise ScenarioFileError(".mel-scenario または .scriptnote.json を選択してください")
    if not path.is_file():
        raise ScenarioFileError(f"ファイルが見つかりません: {path}")
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise ScenarioFileError(f"ファイル情報を取得できません: {path}") from exc
    if file_size > MAX_FILE_BYTES:
        raise ScenarioFileError("シナリオファイルが大きすぎます（上限32MB）")
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except UnicodeError as exc:
        raise ScenarioFileError("シナリオファイルをUTF-8として読めません") from exc
    except json.JSONDecodeError as exc:
        raise ScenarioFileError(f"シナリオファイルのJSONが壊れています（{exc.lineno}行目）") from exc
    if not isinstance(document, dict):
        raise ScenarioFileError("シナリオファイルの内容が正しい形式ではありません")
    file_type = document.get("fileType")
    if file_type not in (None, "", "meldex-scriptnote"):
        raise ScenarioFileError("Meldexのシナリオファイルではありません")
    if not isinstance(document.get("rows"), list):
        raise ScenarioFileError("シナリオの行データが見つかりません")
    return build_contract_payload(document, str(path.resolve()))


def build_contract_payload(document: dict[str, Any], document_id: str) -> dict[str, Any]:
    """MeldexのbuildPayload既定動作と同じcontract v2を構築する."""
    presentation = _document_presentation(document)
    default_style = presentation["defaultStyle"]
    break_names, summary_names = _role_categories(document)
    rules = document.get("rubyRules") if isinstance(document.get("rubyRules"), list) else []
    rows = document.get("rows") if isinstance(document.get("rows"), list) else []
    pages: list[dict[str, Any]] = [{"pageIndex": 0, "rows": []}]
    for index, source_row in enumerate(rows):
        row = source_row if isinstance(source_row, dict) else {}
        role = _string(row.get("role"))
        is_break = bool(role) and role in break_names
        if role and role in summary_names:
            continue
        if is_break and index > 0:
            pages.append({"pageIndex": len(pages), "rows": []})
        body, rubies = resolve_ruby_spans(
            _string(row.get("text")), rules, default_style=default_style
        )
        if is_break:
            continue
        row_payload: dict[str, Any] = {
            "rowId": _string(row.get("id")) or f"row-{index}",
            "type": role,
            "body": body,
            "rubies": rubies,
        }
        row_presentation = _row_presentation(row, presentation)
        if row_presentation is not None:
            row_payload["presentation"] = {"ruby": row_presentation}
        pages[-1]["rows"].append(row_payload)
    return {
        "contract": CONTRACT_NAME,
        "version": CONTRACT_VERSION,
        "source": {
            "documentId": str(document_id or "").strip(),
            "title": _string(document.get("title")),
        },
        "pages": pages,
        "indexUnit": "unicode-code-point",
        "normalization": "none",
        "presentation": {"ruby": presentation},
    }


def resolve_ruby_spans(
    raw: str,
    rules: list[Any] | tuple[Any, ...] = (),
    *,
    default_style: str = "group",
) -> tuple[str, list[dict[str, Any]]]:
    """手動ルビ・手動リンク・文書ルールを可視本文の座標へ展開する."""
    body = ""
    rubies: list[dict[str, Any]] = []
    protected: list[dict[str, int]] = []
    for segment in _visible_segments(str(raw or "")):
        start = len(body)
        if segment["type"] == "ruby":
            base = segment["plain"]
            reading = segment["ruby"]
            body += base
            if base and reading:
                rubies.append({
                    "start": start,
                    "length": len(base),
                    "rubyText": reading,
                    "style": _ruby_style(default_style),
                    "origin": "manual",
                    "priority": 400,
                })
        elif segment["type"] == "manual-link":
            nested_body, nested_rubies = resolve_ruby_spans(
                segment["label"], (), default_style=default_style
            )
            body += nested_body
            rubies.extend({**item, "start": start + int(item["start"])} for item in nested_rubies)
            protected.append({"start": start, "length": len(nested_body)})
        else:
            body += _unescape_plain(segment["raw"])
    used = [False] * len(body)
    for item in [*rubies, *protected]:
        start = int(item["start"])
        for offset in range(int(item["length"])):
            if 0 <= start + offset < len(used):
                used[start + offset] = True
    _apply_document_rules(body, rules, used, rubies, default_style)
    rubies.sort(key=lambda item: (
        int(item["start"]), -int(item.get("priority", 0)), -int(item["length"])
    ))
    return body, rubies


def _visible_segments(raw: str) -> list[dict[str, str]]:
    segments: list[dict[str, str]] = []
    last = 0
    for match in _VISIBLE_RE.finditer(raw):
        if match.start() > last:
            segments.append({"type": "plain", "raw": raw[last:match.start()]})
        if match.group("ruby") is not None:
            segments.append({
                "type": "ruby",
                "plain": _unescape_ruby(match.group("base") or ""),
                "ruby": _unescape_ruby(match.group("reading") or ""),
            })
        else:
            segments.append({
                "type": "manual-link",
                "label": _decode_link_label(match.group("label") or ""),
            })
        last = match.end()
    if last < len(raw):
        segments.append({"type": "plain", "raw": raw[last:]})
    return segments


def _apply_document_rules(
    body: str,
    rules: list[Any] | tuple[Any, ...],
    used: list[bool],
    rubies: list[dict[str, Any]],
    default_style: str,
) -> None:
    normalized: list[dict[str, Any]] = []
    for index, source in enumerate(rules):
        if not isinstance(source, dict):
            continue
        text = _string(source.get("text"))
        reading = _string(source.get("rubyText", source.get("ruby")))
        if not text or not reading:
            continue
        rule = {
            "text": text,
            "rubyText": reading,
            "style": _ruby_style(source.get("style"), default_style),
            "index": index,
        }
        if isinstance(source.get("segments"), list):
            rule["segments"] = list(source["segments"])
        normalized.append(rule)
    position = 0
    while position < len(body):
        candidates = [
            rule for rule in normalized
            if body.startswith(rule["text"], position)
            and all(not used[position + offset] for offset in range(len(rule["text"])))
        ]
        candidates.sort(key=lambda rule: (-len(rule["text"]), int(rule["index"])))
        if not candidates:
            position += 1
            continue
        match = candidates[0]
        for offset in range(len(match["text"])):
            used[position + offset] = True
        span = {
            "start": position,
            "length": len(match["text"]),
            "rubyText": match["rubyText"],
            "style": match["style"],
            "origin": "document-rule",
            "priority": 200,
        }
        if "segments" in match:
            span["segments"] = match["segments"]
        rubies.append(span)
        position += len(match["text"])


def _document_presentation(document: dict[str, Any]) -> dict[str, Any]:
    editor = document.get("editor") if isinstance(document.get("editor"), dict) else {}
    stored = document.get("rubyPresentation")
    if not isinstance(stored, dict):
        stored = editor.get("rubyPresentation")
    writing_mode = "vertical" if editor.get("viewMode") == "vertical" else "horizontal"
    if isinstance(stored, dict):
        return _normalize_presentation(stored, writing_mode=writing_mode)
    rows = document.get("rows") if isinstance(document.get("rows"), list) else []
    has_content = any(
        isinstance(row, dict) and bool(_string(row.get("text"))) for row in rows
    )
    legacy_size = editor.get("rubyFontSize")
    legacy_offset = editor.get("rubyOffset")
    if legacy_size not in (None, "") or legacy_offset not in (None, "") or has_content:
        size_em = _finite(legacy_size, 0.55, 0.05, 2.0)
        offset_px = _finite(legacy_offset, 3.5, -100.0, 100.0)
        return _normalize_presentation({
            "writingMode": writing_mode,
            "sizePercent": size_em * 100.0,
            "gapEm": 0.0,
            "lineHeight": 1.0,
            "compatibility": {
                "legacySizeEm": size_em,
                "legacyOffsetPx": offset_px,
                "useLegacySize": True,
                "useLegacyGap": True,
            },
        }, writing_mode=writing_mode)
    return _normalize_presentation({"writingMode": writing_mode}, writing_mode=writing_mode)


def _row_presentation(
    row: dict[str, Any], document_presentation: dict[str, Any]
) -> dict[str, Any] | None:
    override = row.get("rubyPresentation")
    if not isinstance(override, dict):
        presentation = row.get("presentation")
        override = presentation.get("ruby") if isinstance(presentation, dict) else None
    if not isinstance(override, dict):
        return None
    return _normalize_presentation(
        {**document_presentation, **override},
        writing_mode=document_presentation["writingMode"],
    )


def _normalize_presentation(
    source: dict[str, Any], *, writing_mode: str = "horizontal"
) -> dict[str, Any]:
    compatibility = (
        source.get("compatibility") if isinstance(source.get("compatibility"), dict) else {}
    )
    normalized_writing_mode = _choice(
        source.get("writingMode"), {"horizontal", "vertical"}, writing_mode
    )
    size_percent = _finite(source.get("sizePercent"), 50.0, 5.0, 200.0)
    if compatibility.get("useLegacySize") is True:
        size_percent = _finite(compatibility.get("legacySizeEm"), 0.55, 0.05, 2.0) * 100.0
    gap_em = _finite(source.get("gapEm"), 0.0, -2.0, 4.0)
    if compatibility.get("useLegacyGap") is True:
        gap_em = _legacy_gap_em(compatibility, writing_mode=normalized_writing_mode)
    return {
        "writingMode": normalized_writing_mode,
        "sizePercent": size_percent,
        "gapEm": gap_em,
        "letterSpacingEm": _finite(source.get("letterSpacingEm"), 0.0, -0.9, 3.0),
        "lineHeight": _finite(source.get("lineHeight"), 1.8, 0.5, 5.0),
        "align": _choice(source.get("align"), {"center", "start"}, "center"),
        "smallKana": _choice(source.get("smallKana"), {"keep", "fullsize"}, "keep"),
        "fontPreset": _choice(
            source.get("fontPreset"), {"inherit", "sans-jp", "serif-jp", "gothic-jp"}, "inherit"
        ),
        "defaultStyle": _ruby_style(source.get("defaultStyle")),
    }


def _legacy_gap_em(
    compatibility: dict[str, Any], *, writing_mode: str
) -> float:
    explicit = compatibility.get("legacyGapEm")
    if explicit not in (None, ""):
        return _finite(explicit, 0.0, -2.0, 4.0)
    base_em = _finite(
        compatibility.get("legacyBaseEmPx"), _MELDEX_LEGACY_BASE_EM_PX, 0.001, 1000.0
    )
    default_cross_size = (
        _MELDEX_LEGACY_VERTICAL_CROSS_SIZE_PX
        if writing_mode == "vertical"
        else _MELDEX_LEGACY_HORIZONTAL_CROSS_SIZE_PX
    )
    cross_size = _finite(
        compatibility.get("legacyCrossSizePx"),
        default_cross_size,
        0.001,
        1000.0,
    )
    offset = _finite(compatibility.get("legacyOffsetPx"), 3.5, -100.0, 100.0)
    return max(-2.0, min(4.0, ((cross_size - base_em) * 0.5 - offset) / base_em))


def _role_categories(document: dict[str, Any]) -> tuple[set[str], set[str]]:
    layout = _choice(document.get("layoutMode"), set(_BREAK_NAMES), "manga")
    break_names: set[str] = set()
    summary_names: set[str] = set()
    characters = document.get("characters") if isinstance(document.get("characters"), list) else []
    for source in characters:
        if not isinstance(source, dict) or source.get("isDefault"):
            continue
        name = _string(source.get("name"))
        if not name:
            continue
        is_break = source.get("isBreak") if "isBreak" in source else (
            source.get("kind") == "break" or name in _BREAK_NAMES[layout]
        )
        is_summary = source.get("isSummary") if "isSummary" in source else (
            source.get("kind") == "summary" or name in _SUMMARY_NAMES
        )
        if is_break:
            break_names.add(name)
        if is_summary:
            summary_names.add(name)
    return break_names, summary_names


def _is_supported_path(path: Path) -> bool:
    return path.name.lower().endswith(SUPPORTED_SUFFIXES)


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _unescape_ruby(value: str) -> str:
    return _RUBY_ESCAPE_RE.sub(r"\1", value)


def _unescape_plain(value: str) -> str:
    return _PLAIN_ESCAPE_RE.sub(r"\1", value)


def _decode_link_label(value: str) -> str:
    return _unescape_ruby(_LINK_LABEL_ESCAPE_RE.sub(r"\1", value))


def _choice(value: Any, choices: set[str], fallback: str) -> str:
    normalized = _string(value).strip()
    return normalized if normalized in choices else fallback


def _ruby_style(value: Any, fallback: str = "group") -> str:
    return _choice(value, {"group", "mono", "jukugo"}, fallback)


def _finite(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(fallback)
    if not math.isfinite(number):
        number = float(fallback)
    return max(float(minimum), min(float(maximum), number))
