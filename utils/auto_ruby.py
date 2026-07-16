"""自動ルビ — IME辞書から読みを自動設定."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import log, text_style

_logger = log.get_logger(__name__)

_CACHE: dict[str, list[tuple[str, str]]] = {}
_CACHE_MTIME: dict[str, float] = {}


def _is_ruby_target(surface: str) -> bool:
    """ルビ対象か判定。漢字を含む語のみ対象。"""
    return bool(re.search(r"[一-鿿㐀-䶿]", surface))


def parse_ime_dictionary(path: Path) -> list[tuple[str, str]]:
    """IME辞書ファイルを読み込み、(表記, 読み) のリストを返す.

    対応形式:
    - Google日本語入力 / ATOK: 読み<TAB>表記<TAB>品詞[<TAB>コメント]
    - MS-IME テキスト形式: 読み<TAB>表記<TAB>品詞
    いずれもTSV。1列目=読み、2列目=表記。
    """
    entries: list[tuple[str, str]] = []
    try:
        for encoding in ("utf-8-sig", "utf-8", "utf-16", "shift_jis", "euc-jp"):
            try:
                text = path.read_text(encoding=encoding)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        else:
            _logger.warning("auto_ruby: cannot decode %s", path)
            return []

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            reading = parts[0].strip()
            surface = parts[1].strip()
            if not reading or not surface:
                continue
            if not _is_ruby_target(surface):
                continue
            entries.append((surface, reading))
    except OSError as exc:
        _logger.warning("auto_ruby: failed to read %s: %s", path, exc)
    return entries


def load_dictionaries(paths: list[Path]) -> list[tuple[str, str]]:
    """複数辞書を読み込み、表記の長い順にソートして返す."""
    all_entries: dict[str, str] = {}
    for p in paths:
        p = Path(p)
        if not p.is_file():
            continue
        key = str(p)
        mtime = p.stat().st_mtime
        if key in _CACHE and _CACHE_MTIME.get(key) == mtime:
            for surface, reading in _CACHE[key]:
                all_entries[surface] = reading
            continue
        entries = parse_ime_dictionary(p)
        _CACHE[key] = entries
        _CACHE_MTIME[key] = mtime
        for surface, reading in entries:
            all_entries[surface] = reading

    result = [(s, r) for s, r in all_entries.items()]
    result.sort(key=lambda x: -len(x[0]))
    return result


def apply_auto_ruby(entry, dictionary: list[tuple[str, str]]) -> int:
    """TextEntry に辞書ベースのルビを一括適用. 適用数を返す."""
    body = str(getattr(entry, "body", "") or "")
    if not body:
        return 0

    existing = set()
    for seg in text_style.ruby_spans_snapshot(entry):
        for i in range(seg[0], seg[1]):
            existing.add(i)

    applied = 0
    covered = set(existing)

    for surface, reading in dictionary:
        start = 0
        while True:
            pos = body.find(surface, start)
            if pos < 0:
                break
            end = pos + len(surface)
            indices = set(range(pos, end))
            if indices & covered:
                start = pos + 1
                continue
            if text_style.apply_ruby_span(
                entry, pos, end, reading,
                str(getattr(entry, "ruby_default_style", "group") or "group"),
                origin="local-auto-dictionary",
            ):
                covered |= indices
                applied += 1
            start = end

    return applied
