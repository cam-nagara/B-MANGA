"""Helpers for per-range text styling."""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

StyleTuple = tuple[str, float, tuple[float, float, float, float], bool, bool]
StyleSegment = tuple[int, int, StyleTuple]
RubySegment = tuple[int, int, str, str]

_FONT_DROPDOWN_ITEMS: list[tuple[str, str, str]] | None = None
_FONT_DROPDOWN_PATHS: dict[str, str] = {}
_DEFAULT_FONT_CHOICE = "__DEFAULT__"


def _abspath_maybe(path: str) -> str:
    path = str(path or "").strip()
    if not path:
        return ""
    try:
        import bpy

        return bpy.path.abspath(path)
    except Exception:  # noqa: BLE001
        return path


def font_candidates() -> list[str]:
    if os.name == "nt":
        return [
            r"C:\Windows\Fonts\YuGothM.ttc",
            r"C:\Windows\Fonts\YuGothR.ttc",
            r"C:\Windows\Fonts\meiryo.ttc",
            r"C:\Windows\Fonts\msgothic.ttc",
        ]
    return [
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    ]


def _font_search_dirs() -> list[Path]:
    if os.name == "nt":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        return [Path(windir) / "Fonts"]
    return [
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
    ]


def _dedup_key(path: Path) -> str:
    return str(path).lower() if os.name == "nt" else str(path)


def available_font_paths() -> list[str]:
    """Return stable font paths for UI dropdowns."""
    paths: dict[str, str] = {}
    for candidate in font_candidates():
        path = _abspath_maybe(candidate)
        if path and Path(path).is_file():
            paths[_dedup_key(Path(path))] = str(Path(path))
    for directory in _font_search_dirs():
        if not directory.exists():
            continue
        try:
            files = list(directory.rglob("*")) if os.name != "nt" else list(directory.glob("*"))
        except OSError:
            continue
        for path in files:
            if path.suffix.lower() not in {".ttf", ".ttc", ".otf"}:
                continue
            paths[_dedup_key(path)] = str(path)
            if len(paths) >= 1000:
                break
        if len(paths) >= 1000:
            break
    return sorted(paths.values(), key=lambda item: (Path(item).stem.lower(), item.lower()))


def _font_choice_key(path: str) -> str:
    digest = hashlib.sha1(str(path).encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"FONT_{digest}"


def _parse_font_family_name(path: str) -> str:
    """TrueType/OpenType ファイルからフォントファミリー名を取得する."""
    try:
        with open(path, "rb") as f:
            header = f.read(16)
            if len(header) < 12:
                return ""
            font_offset = 0
            if header[:4] == b"ttcf":
                if len(header) < 16:
                    return ""
                font_offset = struct.unpack_from(">I", header, 12)[0]
            f.seek(font_offset)
            font_header = f.read(12)
            if len(font_header) < 12:
                return ""
            num_tables = struct.unpack_from(">H", font_header, 4)[0]
            table_data = f.read(num_tables * 16)
            if len(table_data) < num_tables * 16:
                return ""
            name_offset = 0
            name_length = 0
            for i in range(num_tables):
                off = i * 16
                if table_data[off:off + 4] == b"name":
                    name_offset = struct.unpack_from(">I", table_data, off + 8)[0]
                    name_length = struct.unpack_from(">I", table_data, off + 12)[0]
                    break
            if name_offset == 0:
                return ""
            f.seek(name_offset)
            name_table = f.read(min(name_length, 65536))
            if len(name_table) < 6:
                return ""
            count = struct.unpack_from(">H", name_table, 2)[0]
            string_base = struct.unpack_from(">H", name_table, 4)[0]
            ja_name = ""
            en_name = ""
            fallback = ""
            for i in range(count):
                rec = 6 + i * 12
                if rec + 12 > len(name_table):
                    break
                pid, eid, lid, nid, slen, soff = struct.unpack_from(">HHHHHH", name_table, rec)
                if nid != 1:
                    continue
                abs_off = string_base + soff
                if abs_off + slen > len(name_table):
                    continue
                raw = name_table[abs_off:abs_off + slen]
                if pid == 3 and eid == 1:
                    try:
                        decoded = raw.decode("utf-16-be")
                    except Exception:  # noqa: BLE001
                        continue
                    if lid == 0x0411 and not ja_name:
                        ja_name = decoded
                    elif lid == 0x0409 and not en_name:
                        en_name = decoded
                elif pid == 1 and not fallback:
                    try:
                        fallback = raw.decode("mac-roman")
                    except Exception:  # noqa: BLE001
                        pass
            return ja_name or en_name or fallback
    except Exception:  # noqa: BLE001
        return ""


def font_dropdown_items(_self=None, _context=None) -> list[tuple[str, str, str]]:
    """EnumProperty items for selecting installed fonts from a dropdown."""
    global _FONT_DROPDOWN_ITEMS
    if _FONT_DROPDOWN_ITEMS is not None:
        return _FONT_DROPDOWN_ITEMS
    items = [(_DEFAULT_FONT_CHOICE, "基本フォント", "テキストレイヤーの基本フォントを使う")]
    _FONT_DROPDOWN_PATHS.clear()
    seen_labels: dict[str, int] = {}
    raw_items: list[tuple[str, str, str]] = []
    for path in available_font_paths():
        key = _font_choice_key(path)
        _FONT_DROPDOWN_PATHS[key] = path
        label = _parse_font_family_name(path) or Path(path).stem
        seen_labels[label] = seen_labels.get(label, 0) + 1
        raw_items.append((key, label, path))
    for key, label, path in raw_items:
        if seen_labels.get(label, 0) > 1:
            label = f"{label} ({Path(path).stem})"
        items.append((key, label, path))
    _FONT_DROPDOWN_ITEMS = items
    return items


def reset_font_dropdown_cache() -> None:
    """フォントキャッシュをリセットする (アドオン再読み込み時用)."""
    global _FONT_DROPDOWN_ITEMS
    _FONT_DROPDOWN_ITEMS = None
    _FONT_DROPDOWN_PATHS.clear()


def font_path_from_dropdown_choice(choice: str) -> str:
    if choice == _DEFAULT_FONT_CHOICE:
        return ""
    if _FONT_DROPDOWN_ITEMS is None:
        font_dropdown_items()
    return _FONT_DROPDOWN_PATHS.get(str(choice or ""), "")


def dropdown_choice_for_font_path(path: str) -> str:
    path = _abspath_maybe(path)
    if not path:
        return _DEFAULT_FONT_CHOICE
    if _FONT_DROPDOWN_ITEMS is None:
        font_dropdown_items()
    normalized = str(Path(path))
    for key, item_path in _FONT_DROPDOWN_PATHS.items():
        try:
            if Path(item_path).resolve() == Path(normalized).resolve():
                return key
        except OSError:
            if item_path == normalized:
                return key
    return _DEFAULT_FONT_CHOICE


# ビューポートはグリフ単位で resolve_font_path を毎フレーム呼ぶため、
# プリファレンスの RNA 参照を毎回行わないようモジュール内にキャッシュする。
# 値の変更時は preferences 側の update コールバックが reset する。
_PREFERRED_BASE_FONT_CACHE: str | None = None


def reset_preferred_base_font_cache() -> None:
    """標準フォントプリファレンスのキャッシュを破棄する (設定変更時に呼ぶ)."""
    global _PREFERRED_BASE_FONT_CACHE
    _PREFERRED_BASE_FONT_CACHE = None


def preferred_base_font_path() -> str:
    """アドオンプリファレンスで設定された標準フォントのパスを返す.

    プリファレンス未登録・headless実行など取得できない状況では空文字を返す
    (例外を伝播させない。取得失敗時はキャッシュせず次回再試行する)。
    """
    global _PREFERRED_BASE_FONT_CACHE
    if _PREFERRED_BASE_FONT_CACHE is not None:
        return _PREFERRED_BASE_FONT_CACHE
    try:
        from ..preferences import get_preferences

        prefs = get_preferences()
        if prefs is None:
            return ""
        value = _abspath_maybe(str(getattr(prefs, "default_base_font_path", "") or ""))
    except Exception:  # noqa: BLE001
        return ""
    _PREFERRED_BASE_FONT_CACHE = value
    return value


def resolve_font_path(preferred: str = "") -> str:
    preferred = _abspath_maybe(preferred)
    if preferred and Path(preferred).is_file():
        return preferred
    base_font = preferred_base_font_path()
    if base_font and Path(base_font).is_file():
        return base_font
    for candidate in font_candidates():
        if Path(candidate).is_file():
            return candidate
    return preferred


def _body_len(entry) -> int:
    return len(str(getattr(entry, "body", "") or ""))


def _default_style(entry) -> StyleTuple:
    color = getattr(entry, "color", (0.0, 0.0, 0.0, 1.0))
    return (
        str(getattr(entry, "font", "") or ""),
        float(getattr(entry, "font_size_q", 20.0)),
        (
            float(color[0]),
            float(color[1]),
            float(color[2]),
            float(color[3]),
        ),
        bool(getattr(entry, "font_bold", False)),
        bool(getattr(entry, "font_italic", False)),
    )


def _normalized_segments(entry) -> list[tuple[int, int, str]]:
    spans = getattr(entry, "font_spans", None)
    if spans is None:
        return []
    body_len = _body_len(entry)
    segments: list[tuple[int, int, str]] = []
    for span in spans:
        font = str(getattr(span, "font", "") or "").strip()
        if not font:
            continue
        start = max(0, min(body_len, int(getattr(span, "start", 0))))
        end = max(start, min(body_len, start + int(getattr(span, "length", 0))))
        if start >= end:
            continue
        segments = _exclude_range(segments, start, end)
        segments.append((start, end, font))
    return _merge_segments(sorted(segments, key=lambda item: (item[0], item[1], item[2])))


def _exclude_range(
    segments: list[tuple[int, int, str]],
    start: int,
    end: int,
) -> list[tuple[int, int, str]]:
    kept: list[tuple[int, int, str]] = []
    for seg_start, seg_end, font in segments:
        if seg_end <= start or seg_start >= end:
            kept.append((seg_start, seg_end, font))
            continue
        if seg_start < start:
            kept.append((seg_start, start, font))
        if end < seg_end:
            kept.append((end, seg_end, font))
    return kept


def _merge_segments(segments: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    merged: list[tuple[int, int, str]] = []
    for start, end, font in segments:
        if start >= end:
            continue
        if merged and merged[-1][1] == start and merged[-1][2] == font:
            merged[-1] = (merged[-1][0], end, font)
        else:
            merged.append((start, end, font))
    return merged


def _write_segments(entry, segments: list[tuple[int, int, str]], *, body_len_override: int | None = None) -> None:
    spans = getattr(entry, "font_spans", None)
    if spans is None:
        return
    spans.clear()
    body_len = _body_len(entry) if body_len_override is None else max(0, int(body_len_override))
    for start, end, font in _merge_segments(sorted(segments, key=lambda item: item[0])):
        start = max(0, min(body_len, int(start)))
        end = max(start, min(body_len, int(end)))
        if start >= end or not font:
            continue
        span = spans.add()
        span.start = int(start)
        span.length = int(end - start)
        span.font = font


def _style_from_span(span) -> StyleTuple:
    color = getattr(span, "color", (0.0, 0.0, 0.0, 1.0))
    return (
        str(getattr(span, "font", "") or ""),
        float(getattr(span, "font_size_q", 20.0)),
        (
            float(color[0]),
            float(color[1]),
            float(color[2]),
            float(color[3]),
        ),
        bool(getattr(span, "font_bold", False)),
        bool(getattr(span, "font_italic", False)),
    )


def _normalized_style_segments(entry) -> list[StyleSegment]:
    spans = getattr(entry, "style_spans", None)
    if spans is None:
        return []
    body_len = _body_len(entry)
    segments: list[StyleSegment] = []
    for span in spans:
        start = max(0, min(body_len, int(getattr(span, "start", 0))))
        end = max(start, min(body_len, start + int(getattr(span, "length", 0))))
        if start >= end:
            continue
        segments = _exclude_style_range(segments, start, end)
        segments.append((start, end, _style_from_span(span)))
    return _merge_style_segments(sorted(segments, key=lambda item: (item[0], item[1], item[2])))


def _exclude_style_range(segments: list[StyleSegment], start: int, end: int) -> list[StyleSegment]:
    kept: list[StyleSegment] = []
    for seg_start, seg_end, style in segments:
        if seg_end <= start or seg_start >= end:
            kept.append((seg_start, seg_end, style))
            continue
        if seg_start < start:
            kept.append((seg_start, start, style))
        if end < seg_end:
            kept.append((end, seg_end, style))
    return kept


def _merge_style_segments(segments: list[StyleSegment]) -> list[StyleSegment]:
    merged: list[StyleSegment] = []
    for start, end, style in segments:
        if start >= end:
            continue
        if merged and merged[-1][1] == start and merged[-1][2] == style:
            merged[-1] = (merged[-1][0], end, style)
        else:
            merged.append((start, end, style))
    return merged


def _write_style_segments(entry, segments: list[StyleSegment], *, body_len_override: int | None = None) -> None:
    spans = getattr(entry, "style_spans", None)
    if spans is None:
        return
    spans.clear()
    body_len = _body_len(entry) if body_len_override is None else max(0, int(body_len_override))
    for start, end, style in _merge_style_segments(sorted(segments, key=lambda item: item[0])):
        start = max(0, min(body_len, int(start)))
        end = max(start, min(body_len, int(end)))
        if start >= end:
            continue
        font, font_size_q, color, bold, italic = style
        span = spans.add()
        span.start = int(start)
        span.length = int(end - start)
        span.font = font
        span.font_size_q = float(font_size_q)
        span.color = color
        span.font_bold = bool(bold)
        span.font_italic = bool(italic)


def normalize_font_spans(entry) -> None:
    _write_segments(entry, _normalized_segments(entry))


def normalize_style_spans(entry) -> None:
    _write_style_segments(entry, _normalized_style_segments(entry))


def font_spans_snapshot(entry) -> tuple[tuple[int, int, str], ...]:
    return tuple(_normalized_segments(entry))


def style_spans_snapshot(entry) -> tuple[StyleSegment, ...]:
    return tuple(_normalized_style_segments(entry))


def _normalized_ruby_segments(entry, collection_name: str = "ruby_spans") -> list[RubySegment]:
    spans = getattr(entry, collection_name, None)
    if spans is None:
        return []
    body_len = _body_len(entry)
    segments: list[RubySegment] = []
    for span in spans:
        start = max(0, min(body_len, int(getattr(span, "start", 0))))
        end = max(start, min(body_len, start + int(getattr(span, "length", 0))))
        text = str(getattr(span, "ruby_text", "") or "")
        style = str(getattr(span, "style", "group") or "group")
        if start < end and (text or collection_name != "ruby_spans"):
            segments.append((start, end, text, style))
    return sorted(segments, key=lambda item: (item[0], item[1], item[2], item[3]))


def _write_ruby_segments(
    entry,
    segments: list[RubySegment],
    collection_name: str = "ruby_spans",
    *,
    body_len_override: int | None = None,
) -> None:
    spans = getattr(entry, collection_name, None)
    if spans is None:
        return
    spans.clear()
    body_len = _body_len(entry) if body_len_override is None else max(0, int(body_len_override))
    for start, end, text, style in sorted(segments, key=lambda item: (item[0], item[1])):
        start = max(0, min(body_len, int(start)))
        end = max(start, min(body_len, int(end)))
        if start >= end:
            continue
        if collection_name == "ruby_spans" and not str(text or ""):
            continue
        span = spans.add()
        span.start = int(start)
        span.length = int(end - start)
        span.ruby_text = str(text or "")
        span.style = str(style or "group")


def ruby_spans_snapshot(entry) -> tuple[RubySegment, ...]:
    return tuple(_normalized_ruby_segments(entry, "ruby_spans"))


def ruby_records_snapshot(entry) -> tuple[tuple, ...]:
    """Return the complete ruby state used by history and render invalidation."""
    return tuple(
        (
            record["start"],
            record["end"],
            record["text"],
            record["style"],
            record["origin"],
            record["priority"],
            record["segments"],
        )
        for record in _normalized_ruby_records(entry)
    )


def tatechuyoko_ranges_snapshot(entry) -> tuple[RubySegment, ...]:
    return tuple(_normalized_ruby_segments(entry, "tatechuyoko_ranges"))


def normalize_ruby_spans(entry) -> None:
    records = _normalized_ruby_records(entry)
    _write_ruby_records(entry, records)


def _normalized_ruby_records(entry) -> list[dict]:
    body_len = _body_len(entry)
    records: list[dict] = []
    for span in getattr(entry, "ruby_spans", ()):
        start = max(0, min(body_len, int(getattr(span, "start", 0))))
        end = max(start, min(body_len, start + int(getattr(span, "length", 0))))
        text = str(getattr(span, "ruby_text", "") or "")
        if start >= end or not text:
            continue
        records.append({
            "start": start,
            "end": end,
            "text": text,
            "style": str(getattr(span, "style", "group") or "group"),
            "origin": str(getattr(span, "origin", "manual") or "manual"),
            "priority": int(getattr(span, "priority", 0) or 0),
            "segments": tuple(
                (
                    int(getattr(segment, "start", 0)),
                    max(1, int(getattr(segment, "length", 1))),
                    str(getattr(segment, "ruby_text", "") or ""),
                )
                for segment in getattr(span, "segments", ())
                if str(getattr(segment, "ruby_text", "") or "")
            ),
        })
    return sorted(records, key=lambda item: (item["start"], item["end"], item["text"], item["style"]))


def _write_ruby_records(entry, records: list[dict], *, body_len_override: int | None = None) -> None:
    spans = getattr(entry, "ruby_spans", None)
    if spans is None:
        return
    spans.clear()
    body_len = _body_len(entry) if body_len_override is None else max(0, int(body_len_override))
    for record in records:
        start = max(0, min(body_len, int(record["start"])))
        end = max(start, min(body_len, int(record["end"])))
        if start >= end or not str(record["text"] or ""):
            continue
        span = spans.add()
        span.start = start
        span.length = end - start
        span.ruby_text = str(record["text"])
        span.style = str(record["style"] or "group")
        if hasattr(span, "origin"):
            span.origin = str(record.get("origin", "manual") or "manual")
        if hasattr(span, "priority"):
            span.priority = int(record.get("priority", 0) or 0)
        for seg_start, seg_length, seg_text in record.get("segments", ()):
            if seg_start < 0 or seg_start + seg_length > span.length or not seg_text:
                continue
            segment = span.segments.add()
            segment.start = seg_start
            segment.length = seg_length
            segment.ruby_text = seg_text


def normalize_tatechuyoko_ranges(entry) -> None:
    _write_ruby_segments(
        entry,
        _normalized_ruby_segments(entry, "tatechuyoko_ranges"),
        "tatechuyoko_ranges",
    )


def all_spans_snapshot(entry):
    return (
        font_spans_snapshot(entry),
        style_spans_snapshot(entry),
        ruby_spans_snapshot(entry),
        tatechuyoko_ranges_snapshot(entry),
        ruby_records_snapshot(entry),
    )


def restore_font_spans(entry, snapshot) -> None:
    segments = []
    body_len = _body_len(entry)
    for start, end, font in snapshot or ():
        start = max(0, min(body_len, int(start)))
        end = max(start, min(body_len, int(end)))
        if start < end and font:
            segments.append((start, end, str(font)))
    _write_segments(entry, segments)


def restore_style_spans(entry, snapshot) -> None:
    body_len = _body_len(entry)
    segments: list[StyleSegment] = []
    for item in snapshot or ():
        start, end, style = item
        start = max(0, min(body_len, int(start)))
        end = max(start, min(body_len, int(end)))
        if start < end:
            segments.append((start, end, style))
    _write_style_segments(entry, segments)


def restore_ruby_spans(entry, snapshot) -> None:
    segments: list[RubySegment] = []
    body_len = _body_len(entry)
    for item in snapshot or ():
        start, end, text, style = item
        start = max(0, min(body_len, int(start)))
        end = max(start, min(body_len, int(end)))
        if start < end and str(text or ""):
            segments.append((start, end, str(text or ""), str(style or "group")))
    _write_ruby_segments(entry, segments, "ruby_spans")


def restore_ruby_records(entry, snapshot) -> None:
    records = []
    for item in snapshot or ():
        if len(item) < 7:
            continue
        start, end, text, style, origin, priority, segments = item[:7]
        records.append({
            "start": start,
            "end": end,
            "text": text,
            "style": style,
            "origin": origin,
            "priority": priority,
            "segments": tuple(segments or ()),
        })
    _write_ruby_records(entry, records)


def restore_tatechuyoko_ranges(entry, snapshot) -> None:
    segments: list[RubySegment] = []
    body_len = _body_len(entry)
    for item in snapshot or ():
        start, end, text, style = item
        start = max(0, min(body_len, int(start)))
        end = max(start, min(body_len, int(end)))
        if start < end:
            segments.append((start, end, str(text or ""), str(style or "group")))
    _write_ruby_segments(entry, segments, "tatechuyoko_ranges")


def restore_all_spans(entry, snapshot) -> None:
    parts = tuple(snapshot or ())
    font_snapshot = parts[0] if len(parts) >= 1 else ()
    style_snapshot = parts[1] if len(parts) >= 2 else ()
    ruby_snapshot = parts[2] if len(parts) >= 3 else ()
    tatechuyoko_snapshot = parts[3] if len(parts) >= 4 else ()
    ruby_records = parts[4] if len(parts) >= 5 else ()
    restore_font_spans(entry, font_snapshot)
    restore_style_spans(entry, style_snapshot)
    if ruby_records:
        restore_ruby_records(entry, ruby_records)
    else:
        restore_ruby_spans(entry, ruby_snapshot)
    restore_tatechuyoko_ranges(entry, tatechuyoko_snapshot)


def apply_font_span(entry, start: int, end: int, font: str) -> bool:
    body_len = _body_len(entry)
    start = max(0, min(body_len, int(start)))
    end = max(start, min(body_len, int(end)))
    if start >= end:
        return False
    segments = _exclude_range(_normalized_segments(entry), start, end)
    font = str(font or "").strip()
    if font:
        segments.append((start, end, font))
    _write_segments(entry, segments)
    return True


def apply_style_span(
    entry,
    start: int,
    end: int,
    *,
    font: str,
    font_size_q: float,
    color,
    bold: bool,
    italic: bool,
) -> bool:
    body_len = _body_len(entry)
    start = max(0, min(body_len, int(start)))
    end = max(start, min(body_len, int(end)))
    if start >= end:
        return False
    color_tuple = (
        float(color[0]),
        float(color[1]),
        float(color[2]),
        float(color[3]),
    )
    style: StyleTuple = (
        str(font or "").strip(),
        max(1.0, float(font_size_q)),
        color_tuple,
        bool(bold),
        bool(italic),
    )
    segments = _exclude_style_range(_normalized_style_segments(entry), start, end)
    segments.append((start, end, style))
    _write_style_segments(entry, segments)
    return True


def apply_ruby_span(
    entry, start: int, end: int, ruby_text: str, style: str = "group", *,
    origin: str = "manual", priority: int = 0, segments=(),
) -> bool:
    body_len = _body_len(entry)
    start = max(0, min(body_len, int(start)))
    end = max(start, min(body_len, int(end)))
    ruby_text = str(ruby_text or "").strip()
    if start >= end or not ruby_text:
        return False
    records = [
        record for record in _normalized_ruby_records(entry)
        if record["end"] <= start or record["start"] >= end
    ]
    records.append({
        "start": start, "end": end, "text": ruby_text, "style": str(style or "group"),
        "origin": str(origin or "manual"), "priority": int(priority), "segments": tuple(segments or ()),
    })
    _write_ruby_records(entry, records)
    return True


def clear_ruby_span_segments(segments: list[RubySegment], start: int, end: int) -> list[RubySegment]:
    kept: list[RubySegment] = []
    for seg_start, seg_end, text, style in segments:
        if seg_end <= start or seg_start >= end:
            kept.append((seg_start, seg_end, text, style))
    return kept


def clear_ruby_spans(entry, start: int | None = None, end: int | None = None) -> bool:
    spans = getattr(entry, "ruby_spans", None)
    if spans is None:
        return False
    if start is None or end is None:
        changed = len(spans) > 0
        spans.clear()
        return changed
    body_len = _body_len(entry)
    start = max(0, min(body_len, int(start)))
    end = max(start, min(body_len, int(end)))
    before = _normalized_ruby_records(entry)
    kept = [record for record in before if record["end"] <= start or record["start"] >= end]
    _write_ruby_records(entry, kept)
    return len(before) != len(kept)


def _adjust_font_spans_for_replace(entry, start: int, end: int, new_length: int) -> None:
    body_len = _body_len(entry)
    start = max(0, min(body_len, int(start)))
    end = max(start, min(body_len, int(end)))
    new_length = max(0, int(new_length))
    delta = new_length - (end - start)
    old_segments = _normalized_segments(entry)
    inherited_font = ""
    if start < end and new_length > 0:
        for seg_start, seg_end, font in old_segments:
            if seg_start <= start < seg_end:
                inherited_font = font
                break
    adjusted: list[tuple[int, int, str]] = []
    for seg_start, seg_end, font in old_segments:
        if start == end:
            if seg_end <= start:
                adjusted.append((seg_start, seg_end, font))
            elif seg_start >= start:
                adjusted.append((seg_start + delta, seg_end + delta, font))
            else:
                adjusted.append((seg_start, seg_end + delta, font))
            continue
        if seg_end <= start:
            adjusted.append((seg_start, seg_end, font))
        elif seg_start >= end:
            adjusted.append((seg_start + delta, seg_end + delta, font))
        else:
            if seg_start < start:
                adjusted.append((seg_start, start, font))
            if end < seg_end:
                adjusted.append((start + new_length, seg_end + delta, font))
    if inherited_font:
        adjusted = _exclude_range(adjusted, start, start + new_length)
        adjusted.append((start, start + new_length, inherited_font))
    _write_segments(entry, adjusted, body_len_override=body_len + delta)


def _adjust_style_spans_for_replace(entry, start: int, end: int, new_length: int) -> None:
    body_len = _body_len(entry)
    start = max(0, min(body_len, int(start)))
    end = max(start, min(body_len, int(end)))
    new_length = max(0, int(new_length))
    delta = new_length - (end - start)
    old_segments = _normalized_style_segments(entry)
    inherited_style: StyleTuple | None = None
    if start < end and new_length > 0:
        for seg_start, seg_end, style in old_segments:
            if seg_start <= start < seg_end:
                inherited_style = style
                break
    adjusted: list[StyleSegment] = []
    for seg_start, seg_end, style in old_segments:
        if start == end:
            if seg_end <= start:
                adjusted.append((seg_start, seg_end, style))
            elif seg_start >= start:
                adjusted.append((seg_start + delta, seg_end + delta, style))
            else:
                adjusted.append((seg_start, seg_end + delta, style))
            continue
        if seg_end <= start:
            adjusted.append((seg_start, seg_end, style))
        elif seg_start >= end:
            adjusted.append((seg_start + delta, seg_end + delta, style))
        else:
            if seg_start < start:
                adjusted.append((seg_start, start, style))
            if end < seg_end:
                adjusted.append((start + new_length, seg_end + delta, style))
    if inherited_style is not None:
        adjusted = _exclude_style_range(adjusted, start, start + new_length)
        adjusted.append((start, start + new_length, inherited_style))
    _write_style_segments(entry, adjusted, body_len_override=body_len + delta)


def _adjust_ruby_segments_for_replace(
    entry,
    start: int,
    end: int,
    new_length: int,
    collection_name: str,
) -> None:
    body_len = _body_len(entry)
    start = max(0, min(body_len, int(start)))
    end = max(start, min(body_len, int(end)))
    new_length = max(0, int(new_length))
    delta = new_length - (end - start)
    if collection_name == "ruby_spans":
        adjusted_records: list[dict] = []
        for record in _normalized_ruby_records(entry):
            seg_start, seg_end = record["start"], record["end"]
            if start == end:
                if seg_end <= start:
                    pass
                elif seg_start >= start:
                    record["start"] += delta
                    record["end"] += delta
                else:
                    if record.get("segments"):
                        # Segment correspondence is no longer reliable after an
                        # insertion inside the annotated parent text.
                        continue
                    record["end"] += delta
                adjusted_records.append(record)
            elif seg_end <= start:
                adjusted_records.append(record)
            elif seg_start >= end:
                record["start"] += delta
                record["end"] += delta
                adjusted_records.append(record)
            # 親文字へ触れる置換では、そのルビと内訳を一体で無効化する。
        _write_ruby_records(entry, adjusted_records, body_len_override=body_len + delta)
        return
    adjusted: list[RubySegment] = []
    for seg_start, seg_end, text, style in _normalized_ruby_segments(entry, collection_name):
        if start == end:
            if seg_end <= start:
                adjusted.append((seg_start, seg_end, text, style))
            elif seg_start >= start:
                adjusted.append((seg_start + delta, seg_end + delta, text, style))
            else:
                adjusted.append((seg_start, seg_end + delta, text, style))
            continue
        if seg_end <= start:
            adjusted.append((seg_start, seg_end, text, style))
        elif seg_start >= end:
            adjusted.append((seg_start + delta, seg_end + delta, text, style))
        # A replacement touching the annotated parent text invalidates that ruby.
    _write_ruby_segments(entry, adjusted, collection_name, body_len_override=body_len + delta)


def adjust_spans_for_replace(entry, start: int, end: int, new_length: int) -> None:
    _adjust_font_spans_for_replace(entry, start, end, new_length)
    _adjust_style_spans_for_replace(entry, start, end, new_length)
    _adjust_ruby_segments_for_replace(entry, start, end, new_length, "ruby_spans")
    _adjust_ruby_segments_for_replace(entry, start, end, new_length, "tatechuyoko_ranges")


def adjust_font_spans_for_replace(entry, start: int, end: int, new_length: int) -> None:
    adjust_spans_for_replace(entry, start, end, new_length)


def font_for_index(entry, index: int) -> str:
    index = int(index)
    for start, end, style in _normalized_style_segments(entry):
        if start <= index < end:
            font = style[0]
            return font if font else str(getattr(entry, "font", "") or "")
    for start, end, font in _normalized_segments(entry):
        if start <= index < end:
            return font
    return str(getattr(entry, "font", "") or "")


def style_for_index(entry, index: int) -> StyleTuple:
    index = int(index)
    for start, end, style in _normalized_style_segments(entry):
        if start <= index < end:
            font, font_size_q, color, bold, italic = style
            return (
                font if font else str(getattr(entry, "font", "") or ""),
                font_size_q,
                color,
                bold,
                italic,
            )
    font, font_size_q, color, bold, italic = _default_style(entry)
    legacy_font = font_for_index(entry, index)
    return (legacy_font or font, font_size_q, color, bold, italic)


def font_size_q_for_index(entry, index: int) -> float:
    return float(style_for_index(entry, index)[1])


def color_for_index(entry, index: int) -> tuple[float, float, float, float]:
    return style_for_index(entry, index)[2]


def bold_for_index(entry, index: int) -> bool:
    return bool(style_for_index(entry, index)[3])


def italic_for_index(entry, index: int) -> bool:
    return bool(style_for_index(entry, index)[4])
