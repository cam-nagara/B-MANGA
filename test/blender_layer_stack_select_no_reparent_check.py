"""Blender実機用: 行選択がUIList D&Dと誤検知されて親変更されないことの回帰確認.

2026-07-18 報告の不具合:
  Meldex取込作品でオブジェクトツールのクリック (行選択) が、同期・Undo など
  内部要因のスタック並び替えを UIList D&D と誤検知し、選択しただけのテキスト/
  フキダシ行を隣のコマ行の配下へ勝手に親変更していた。結果としてコマ行が
  最前面扱いになり、コマの並べ替えもできなくなっていた。

確認内容:
  1. 記憶シグネチャと実並びがズレた状態で行選択しても親変更されない
  2. コマ行を最背面へ並べ替えできる (テキストが誤ってコマ配下に入らない)
  3. UIList の実 D&D 契約 (1行移動 + apply_stack_order_if_ui_changed) では
     従来どおり親変更ヒントが適用される
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_select_no_reparent"


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


def _payload():
    return {
        "contract": "meldex-bmanga-scenario",
        "version": 1,
        "source": {"documentId": "scenario-select-no-reparent"},
        "pages": [
            {"rows": [
                {"rowId": "r1", "type": "会話", "body": "せりふ一", "rubies": []},
                {"rowId": "r2", "type": "会話", "body": "せりふ二", "rubies": []},
            ]},
        ],
    }


def _rows(stack) -> list[tuple[str, str, str]]:
    return [(item.kind, item.key, item.parent_key) for item in stack]


def _index_of(stack, kind: str, key: str) -> int:
    for i, item in enumerate(stack):
        if item.kind == kind and item.key == key:
            return i
    raise AssertionError(f"stack row not found: {kind}:{key} in {_rows(stack)}")


def main() -> None:
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_select_no_reparent_"))
    work = bpy.context.scene.bmanga_work
    work.loaded = True
    work.work_dir = str(temp_root)
    from bmanga_dev_select_no_reparent import preferences
    from bmanga_dev_select_no_reparent.io import (
        balloon_presets,
        meldex_scenario_import,
        page_io,
        text_presets,
    )
    from bmanga_dev_select_no_reparent.utils import layer_stack as layer_stack_utils

    preferences.get_preferences = lambda _context=None: SimpleNamespace(
        meldex_apply_text_presentation=False
    )
    balloon_presets.list_all_presets = lambda _path: [SimpleNamespace(name="会話", data={})]
    text_presets.list_all_presets = lambda _path: [
        SimpleNamespace(
            name="会話",
            data={
                "font": r"C:\\Windows\\Fonts\\msgothic.ttc",
                "writing_mode": "vertical",
                "line_height": 1.5,
                "linked_balloon_preset": "会話",
            },
        ),
    ]

    page = page_io.register_new_page(work)
    page_io.ensure_page_dir(temp_root, page.id)
    page.detail_loaded = True
    coma = page.comas.add()
    coma.id = "c01"
    coma.coma_id = "c01"
    coma.shape_type = "rect"
    coma.rect_width_mm = 182.0
    coma.rect_height_mm = 257.0
    page.coma_count = 1
    work.active_page_index = 0

    context = bpy.context
    scene = context.scene
    result = meldex_scenario_import.import_payload(context, work, _payload())
    assert result["created"] == 2, result

    stack = scene.bmanga_layer_stack
    page_i = _index_of(stack, "page", "p0001")

    # --- 1. 記憶シグネチャと実並びがズレた状態を作る (内部要因の並び替え相当) ---
    coma_i = _index_of(stack, "coma", "p0001:c01")
    stack.move(coma_i, page_i + 1)
    prev_i = _index_of(stack, "coma_preview", "p0001:c01:__preview__")
    stack.move(prev_i, page_i + 2)

    text_entry = next(t for t in page.texts if str(t.id) == "text_0001")
    assert str(text_entry.parent_key) == "p0001", text_entry.parent_key

    ti = _index_of(stack, "text", "p0001:text_0001")
    assert layer_stack_utils.select_stack_index(context, ti)

    stack = scene.bmanga_layer_stack
    text_entry = next(t for t in page.texts if str(t.id) == "text_0001")
    assert str(text_entry.parent_key) == "p0001", (
        f"行選択だけでテキストが親変更された: parent_key={text_entry.parent_key!r} "
        f"stack={_rows(stack)}"
    )
    row_i = _index_of(stack, "text", "p0001:text_0001")
    assert str(stack[row_i].parent_key) == "p0001", _rows(stack)

    # --- 2. コマ行を最背面へ並べ替えできる ---
    ci = _index_of(stack, "coma", "p0001:c01")
    scene.bmanga_active_layer_stack_index = ci
    assert layer_stack_utils.move_stack_item(context, ci, direction="BACK"), (
        "コマ行を最背面へ移動できません"
    )
    stack = scene.bmanga_layer_stack
    ci = _index_of(stack, "coma", "p0001:c01")
    text_balloon_indices = [
        i for i, item in enumerate(stack) if item.kind in {"text", "balloon"}
    ]
    assert ci > max(text_balloon_indices), f"BACK後の並び: {_rows(stack)}"

    # --- 3. 実 D&D 契約 (1行移動) では従来どおり親変更される ---
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    layer_stack_utils.remember_layer_stack_signature(context)
    ci = _index_of(stack, "coma", "p0001:c01")
    t2 = _index_of(stack, "text", "p0001:text_0002")
    target = ci + 1
    if t2 < target:
        target -= 1
    stack.move(t2, target)
    t2_uid = layer_stack_utils.target_uid("text", "p0001:text_0002")
    assert layer_stack_utils.apply_stack_order_if_ui_changed(context, moved_uid=t2_uid)
    text2 = next(t for t in page.texts if str(t.id) == "text_0002")
    assert str(text2.parent_key) == "p0001:c01", (
        f"実D&Dの親変更が適用されない: parent_key={text2.parent_key!r}"
    )

    print("SELECT_NO_REPARENT_CHECK_PASS")


main()
