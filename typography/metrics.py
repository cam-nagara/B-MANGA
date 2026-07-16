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


# 自動縦中横を無効にした時の半角 ASCII は、縦組みの既定どおり横倒しにする。
# 自動縦中横が有効なら下の ``_TATECHUYOKO_ASCII_CHARS`` が先に範囲化する
# ため、通常は Meldex と同じく横組みの 1 セルとして正立する。
_ASCII_VERTICAL_ROTATE_CHARS = "".join(chr(code) for code in range(0x21, 0x7F))

_VERTICAL_ROTATE_CHARS = frozenset(
    _ASCII_VERTICAL_ROTATE_CHARS
    + "…‥～〜ー―–—─－"  # 三点リーダ・波ダッシュ・長音・ダッシュ類
    + "「」『』（）〈〉《》【】〔〕〖〗〘〙〚〛［］｛｝"  # 括弧類
    # 全角 ASCII 約物のうち、横書き字形を 90° 回して縦組み相当にするもの。
    # ！・？・＋など正立または回転対称の記号は含めない。
    + "＂＇／：；＜＝＞＼＾＿｀｜"
)


def needs_vertical_rotation(ch: str) -> bool:
    """縦書きで 90° 回転が必要な文字か."""
    return ch in _VERTICAL_ROTATE_CHARS


def needs_vertical_ruby_rotation(ch: str) -> bool:
    """縦書きルビで縦組み字形相当の90°回転が必要か.

    Meldexのルビは ``text-orientation: upright`` なのでASCIIは正立させる。
    一方、和文括弧・ダッシュ等はブラウザの縦組み字形に合わせて回転する。
    """
    return ch in _VERTICAL_ROTATE_CHARS and ch not in _ASCII_VERTICAL_ROTATE_CHARS


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


# Meldex の自動縦中横は、半角英数字に加えて句読点・括弧・演算子等を含む
# 連続 ASCII 記号を 1 つの横組みセルにする。B-MANGA でも同じ入力を別の
# 見た目にしないため、空白を除く printable ASCII 全体を対象にする。
_TATECHUYOKO_ASCII_CHARS = frozenset(chr(code) for code in range(0x21, 0x7F))


def is_tatechuyoko_candidate(chars: str) -> bool:
    """連続する空白以外の printable ASCII が縦中横の候補か."""
    return bool(chars) and all(ch in _TATECHUYOKO_ASCII_CHARS for ch in chars)


def _is_tatechuyoko_char(ch: str) -> bool:
    """自動縦中横の対象文字か (空白以外の printable ASCII)."""
    return ch in _TATECHUYOKO_ASCII_CHARS


def auto_tatechuyoko_ranges(text: str) -> list[tuple[int, int]]:
    """本文から自動縦中横の対象範囲 (start, length) を検出する.

    空白以外の printable ASCII の連続ランを、文字数にかかわらず縦中横の
    候補として返す。Meldex と同様に句読点・引用符・括弧・演算子等も含め、
    途中で分割しない。空白・改行・全角文字でランを区切る。
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
        ranges.append((start, i - start))
    return ranges


def load_font(font_path: str) -> Optional[object]:
    """fontTools で TTFont を開く。失敗時は None."""
    if not _HAS_FONTTOOLS:
        return None
    try:
        return TTFont(font_path)
    except Exception:
        return None
