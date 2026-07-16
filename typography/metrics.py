"""フォントメトリクス計算 (fontTools 共用層).

計画書 3.1.5 の「グリフ選択・メトリクス」層。ビューポート (blf) と書き出し
(Pillow) の両方で同じ結果を得られるよう、純粋な計算のみ提供する。

fontTools は Phase 3 後半で wheels に同梱する想定。現段階ではフォント
ファイルのパスから最低限の情報を取り出せるように、標準ライブラリのみで
動くフォールバック実装を置く。実際の OpenType ``vert`` フィーチャによる
縦書きグリフ切替は fontTools 同梱後に拡張する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# fontTools が未同梱でも import エラーにならないよう lazy import。
try:
    from fontTools.ttLib import TTFont  # type: ignore
    _HAS_FONTTOOLS = True
except ImportError:  # pragma: no cover - fontTools is bundled only after wheels step
    TTFont = None  # type: ignore
    _HAS_FONTTOOLS = False


@dataclass(frozen=True)
class GlyphMetrics:
    advance: float  # 文字送り (em 単位、1.0 = フォントサイズそのまま)
    ascent: float
    descent: float
    use_vertical_variant: bool  # OpenType vert 適用済みか


def has_fonttools() -> bool:
    return _HAS_FONTTOOLS


def approximate_em_width(ch: str) -> float:
    """OpenType なしで概算の 1 文字幅を返す.

    日本語の全角文字は 1.0 em、半角英数字は 0.5 em、絵文字等は 1.0 em を
    デフォルト値とする。fontTools が使える環境では ``glyph_width`` を使う。
    """
    if not ch:
        return 0.0
    code = ord(ch)
    # ASCII 基本範囲
    if 0x0020 <= code <= 0x007E:
        return 0.5
    # 半角カナ
    if 0xFF61 <= code <= 0xFF9F:
        return 0.5
    return 1.0


def is_kinsoku_start(ch: str) -> bool:
    """行頭禁則文字か (、。 」 等を簡易判定)."""
    if not ch:
        return False
    return ch in "、。，．」』）】〉》〕］！？・ー…ゝゞ々"


def is_kinsoku_end(ch: str) -> bool:
    """行末禁則文字か (「 等)."""
    if not ch:
        return False
    return ch in "「『（【〈《〔［"


_VERTICAL_ROTATE_CHARS = frozenset(
    "…‥～〜ー―–—─－"  # 三点リーダ・波ダッシュ・長音・ダッシュ類
    "「」『』（）〈〉《》【】〔〕〖〗〘〙〚〛［］｛｝"  # 括弧類 (回転で縦組み用字形相当になる)
)


def needs_vertical_rotation(ch: str) -> bool:
    """縦書きで 90° 回転が必要な文字か."""
    return ch in _VERTICAL_ROTATE_CHARS


# 句読点は横書き字形では全角ボディの左下に字面があるが、縦書きでは右上に
# 置く (JIS X 4051 / JLREQ の縦組み配置)。字面が左下 1/4 領域に収まるため、
# 右へ 0.5em・上へ 0.5em ずらすとちょうど右上 1/4 領域へ移る。
_VERTICAL_UPPER_RIGHT_PUNCTUATION = frozenset("、。，．")
_PUNCTUATION_OFFSET_EM = (0.5, 0.5)

# 小書き仮名 (拗音・促音等) は縦書き用字形ではボディの右上寄りに置かれる
# (docs/縦書きの小文字…徹底調査.txt 参照)。OpenType vert 切替導入までの
# 近似として右上へ 0.1em ずらす。
_VERTICAL_SMALL_KANA = frozenset("ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ")
_SMALL_KANA_OFFSET_EM = (0.1, 0.1)


def vertical_draw_offset_em(ch: str) -> tuple[float, float]:
    """縦書きで字面を描画時にずらす量 (em 単位、右+ / 上+)。

    セル (文字送り) 位置は変えず、字面の描画位置だけをずらすための値。
    """
    if not ch:
        return (0.0, 0.0)
    if ch in _VERTICAL_UPPER_RIGHT_PUNCTUATION:
        return _PUNCTUATION_OFFSET_EM
    if ch in _VERTICAL_SMALL_KANA:
        return _SMALL_KANA_OFFSET_EM
    return (0.0, 0.0)


def is_tatechuyoko_candidate(chars: str) -> bool:
    """連続した半角英数字の塊が縦中横の候補か (2〜4 文字の半角数字)."""
    if not chars or len(chars) > 4:
        return False
    return all(ch.isascii() and ch.isalnum() for ch in chars)


def _is_tatechuyoko_char(ch: str) -> bool:
    """自動縦中横の対象文字か (半角英数字 + ! ?)."""
    if not ch:
        return False
    if ch.isascii() and ch.isalnum():
        return True
    return ch in "!?"


def auto_tatechuyoko_ranges(text: str) -> list[tuple[int, int]]:
    """本文から自動縦中横の対象範囲 (start, length) を検出する.

    半角英数字 + '!' '?' の連続ランのうち、2〜4 文字のものだけを縦中横の
    候補として返す。1 文字のラン (単独文字はそのまま正立) と 5 文字以上の
    ラン (縦積みのまま) は対象外。ランはそのまま (途中で区切らず) 判定する
    ため、"no1234567" のような 5 文字以上のランは丸ごと対象外になる。
    """
    ranges: list[tuple[int, int]] = []
    text = text or ""
    n = len(text)
    i = 0
    while i < n:
        if not _is_tatechuyoko_char(text[i]):
            i += 1
            continue
        start = i
        while i < n and _is_tatechuyoko_char(text[i]):
            i += 1
        length = i - start
        if 2 <= length <= 4:
            ranges.append((start, length))
        # length == 1 または length >= 5 は対象外 (縦積みのまま)
    return ranges


def load_font(font_path: str) -> Optional[object]:
    """fontTools で TTFont を開く。失敗時は None."""
    if not _HAS_FONTTOOLS:
        return None
    try:
        return TTFont(font_path)
    except Exception:
        return None
