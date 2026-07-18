"""Blender 実機用: テキスト案内枠 (青枠線) が選択中/編集中のときだけ表示されることを確認する.

対象修正:
- ui/overlay_text.py の draw_text_guides() に entry_selected 述語 (省略時 None =
  従来通りの常時表示) を追加し、青枠線は
  「編集中」または「entry_selected が真を返すエントリ」だけに描画するよう変更した。
- ui/overlay.py の draw_text_guides 呼び出し2箇所 (アクティブページの texts /
  _draw_shared_layers の共有テキスト) で object_selection の選択キー集合から
  entry_selected 述語を渡すよう変更した。

このテストは draw_rect_outline を記録用の偽関数に差し替えて draw_text_guides を
直接呼び、以下 6 ケースを検証する (silent skip 禁止・失敗時は例外で exit 非0):
  (a) 非選択・非編集のページテキスト -> 青枠なし
  (b) object_selection でページテキストを選択 -> 選択したものだけ青枠あり
  (c) entry_selected=None (呼び出し側が選択状態を渡さない旧来互換) -> 常時表示
  (d) 非選択の共有テキスト (ui/overlay.py の _draw_shared_layers 相当) -> 青枠なし
  (e) 共有テキストを選択 -> 青枠あり
  (f) 別のテキストを選択している間、対象のテキストには青枠が出ない
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_text_guide_selection",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_text_guide_selection"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _add_text(collection, *, entry_id: str, x_mm: float):
    entry = collection.add()
    entry.id = entry_id
    entry.body = "テスト"
    entry.x_mm = x_mm
    entry.y_mm = 0.0
    entry.width_mm = 10.0
    entry.height_mm = 10.0
    entry.writing_mode = "horizontal"
    entry.font_size_q = 20.0
    return entry


class _SharedProxy:
    """ui/overlay.py の _SharedLayerProxy 相当 (ページ外の共有テキスト)."""

    def __init__(self, work) -> None:
        self.id = "__outside__"
        self.texts = work.shared_texts


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_text_guide_selection_"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()

    from bmanga_dev_text_guide_selection.ui import overlay_text
    from bmanga_dev_text_guide_selection.utils import object_selection

    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "TextGuideSelection.bmanga"))
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]

    entry_a = _add_text(page.texts, entry_id="page_text_a", x_mm=0.0)
    entry_b = _add_text(page.texts, entry_id="page_text_b", x_mm=20.0)
    shared_a = _add_text(work.shared_texts, entry_id="shared_text_a", x_mm=0.0)
    shared_b = _add_text(work.shared_texts, entry_id="shared_text_b", x_mm=20.0)

    shared_proxy = _SharedProxy(work)

    def _keys() -> set[str]:
        return set(object_selection.get_keys(bpy.context))

    def _run(page_like, *, entry_selected):
        outlines: list[tuple[float, float]] = []
        overlay_text.draw_text_guides(
            page_like,
            context=None,
            entry_visible=lambda _entry: True,
            draw_rect_fill=lambda *_a, **_k: None,
            draw_rect_outline=lambda rect, color, **_k: outlines.append(
                (round(rect.x, 3), round(rect.y, 3), tuple(color))
            ),
            entry_selected=entry_selected,
        )
        return outlines

    def _pt(entry) -> tuple[float, float, tuple]:
        return (round(entry.x_mm, 3), round(entry.y_mm, 3), overlay_text._TEXT_GUIDE_COLOR)

    # (a) 非選択・非編集のページテキスト -> 青枠なし
    object_selection.set_keys(bpy.context, [])
    outlines_a = _run(
        page,
        entry_selected=lambda entry: object_selection.text_key(page, entry) in _keys(),
    )
    assert outlines_a == [], f"(a) 非選択なのに青枠が描画された: {outlines_a}"

    # (b) ページテキストを選択 -> 選択したものだけ青枠あり
    object_selection.set_keys(bpy.context, [object_selection.text_key(page, entry_a)])
    outlines_b = _run(
        page,
        entry_selected=lambda entry: object_selection.text_key(page, entry) in _keys(),
    )
    assert outlines_b == [_pt(entry_a)], f"(b) 選択中テキストの青枠が想定と異なる: {outlines_b}"

    # (c) entry_selected=None (呼び出し側が選択状態を渡さない旧来互換) -> 常時表示
    object_selection.set_keys(bpy.context, [])
    outlines_c = _run(page, entry_selected=None)
    assert outlines_c == [_pt(entry_a), _pt(entry_b)], f"(c) 後方互換の常時表示が崩れている: {outlines_c}"

    # (d) 非選択の共有テキスト (_draw_shared_layers 相当) -> 青枠なし
    object_selection.set_keys(bpy.context, [])
    outlines_d = _run(
        shared_proxy,
        entry_selected=lambda entry: object_selection.text_key(None, entry) in _keys(),
    )
    assert outlines_d == [], f"(d) 非選択の共有テキストに青枠が描画された: {outlines_d}"

    # (e) 共有テキストを選択 -> 青枠あり
    object_selection.set_keys(bpy.context, [object_selection.text_key(None, shared_a)])
    outlines_e = _run(
        shared_proxy,
        entry_selected=lambda entry: object_selection.text_key(None, entry) in _keys(),
    )
    assert outlines_e == [_pt(shared_a)], f"(e) 選択中の共有テキストの青枠が想定と異なる: {outlines_e}"

    # (f) 別のテキスト (shared_b) を選択している間、対象 (shared_a) には青枠が出ない
    object_selection.set_keys(bpy.context, [object_selection.text_key(None, shared_b)])
    outlines_f = _run(
        shared_proxy,
        entry_selected=lambda entry: object_selection.text_key(None, entry) in _keys(),
    )
    assert outlines_f == [_pt(shared_b)], f"(f) 別テキスト選択時の青枠が想定と異なる: {outlines_f}"
    assert _pt(shared_a) not in outlines_f, f"(f) 非選択の対象テキストに青枠が出た: {outlines_f}"

    print("PASS: blender_text_guide_selection_check (6/6)")


if __name__ == "__main__":
    main()
