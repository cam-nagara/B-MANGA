"""Blender実機用: 順序ボタンによるレイヤーのコマ出し入れ (全階層1行送り).

2026-07-21 ユーザー指示:
  レイヤーリストの「前面へ / 背面へ」ボタンは、同階層内に限定せず、レイヤー
  リスト上の順番で全階層を1行ずつ移動する。上下の着地位置に応じて通常レイヤーを
  コマの中へ入れたり、コマの外 (ページ直下) へ出したりできる。

  一方で「最前面 / 最背面」は従来どおり、そのレイヤーが今いる階層の端まで移動する
  (コマ背面へ送る操作を残し、レイヤーが意図せずアウトサイドへ抜けるのも防ぐ)。
  またコマ/ページ行はコンテナのため全階層移動の対象外 (同階層内で並べ替え)。

確認内容:
  1. テキスト行を「背面へ」で押し下げるとコマ配下へ入る (parent_key=コマ)
  2. コマ配下のテキスト行を「前面へ」で押し上げるとページ直下へ戻る
  3. 「最背面」ではコマへ入らずページ直下のまま (同階層維持)
  4. 「最前面」でもアウトサイドへ抜けずページ直下のまま (同階層維持)
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_flat_move_into_coma"


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
        "source": {"documentId": "scenario-flat-move-into-coma"},
        "pages": [
            {"rows": [
                {"rowId": "r1", "type": "会話", "body": "せりふ一", "rubies": []},
            ]},
        ],
    }


def _rows(stack) -> list[tuple[int, str, str, str, int]]:
    return [
        (i, item.kind, item.key, item.parent_key, int(item.depth))
        for i, item in enumerate(stack)
    ]


def _index_of(stack, kind: str, key: str) -> int:
    for i, item in enumerate(stack):
        if item.kind == kind and item.key == key:
            return i
    raise AssertionError(f"stack row not found: {kind}:{key} in {_rows(stack)}")


def _is_page_child(entry) -> bool:
    return str(entry.parent_kind) == "page" and ":" not in str(entry.parent_key)


def main() -> None:
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_flat_move_into_coma_"))
    work = bpy.context.scene.bmanga_work
    work.loaded = True
    work.work_dir = str(temp_root)

    from bmanga_dev_flat_move_into_coma import preferences
    from bmanga_dev_flat_move_into_coma.io import (
        balloon_presets,
        meldex_scenario_import,
        page_io,
        text_presets,
    )
    from bmanga_dev_flat_move_into_coma.utils import layer_stack as layer_stack_utils

    preferences.get_preferences = lambda _context=None: SimpleNamespace(
        meldex_apply_text_presentation=False
    )
    balloon_presets.list_all_presets = lambda _path: []
    text_presets.list_all_presets = lambda _path: [
        SimpleNamespace(
            name="会話",
            data={
                "font": r"C:\Windows\Fonts\msgothic.ttc",
                "writing_mode": "vertical",
                "line_height": 1.5,
                "linked_balloon_preset": "",
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
    coma.rect_width_mm = 100.0
    coma.rect_height_mm = 100.0
    page.coma_count = 1
    work.active_page_index = 0

    context = bpy.context
    scene = context.scene
    result = meldex_scenario_import.import_payload(context, work, _payload())
    assert result["created"] == 1, result

    text_key = "p0001:text_0001"
    coma_key = "p0001:c01"

    def _text():
        return next(t for t in page.texts if str(t.id) == "text_0001")

    stack = layer_stack_utils.sync_layer_stack(context)
    assert _is_page_child(_text()), _text().parent_key

    # --- 1. 「背面へ」で1行ずつ押し下げるとコマ配下へ入る --------------------
    entered = False
    for _ in range(8):
        stack = layer_stack_utils.sync_layer_stack(context)
        ti = _index_of(stack, "text", text_key)
        scene.bmanga_active_layer_stack_index = ti
        moved = layer_stack_utils.move_stack_item(context, ti, direction="DOWN")
        if str(_text().parent_key) == coma_key:
            entered = True
            break
        if not moved:
            break
    assert entered, f"「背面へ」でコマ配下へ入れませんでした: {_rows(layer_stack_utils.sync_layer_stack(context))}"

    # --- 2. 「前面へ」で押し上げるとコマ外 (ページ直下) へ戻る ----------------
    exited = False
    for _ in range(8):
        stack = layer_stack_utils.sync_layer_stack(context)
        ti = _index_of(stack, "text", text_key)
        scene.bmanga_active_layer_stack_index = ti
        moved = layer_stack_utils.move_stack_item(context, ti, direction="UP")
        if _is_page_child(_text()):
            exited = True
            break
        if not moved:
            break
    assert exited, f"「前面へ」でコマ外へ戻せませんでした: {_rows(layer_stack_utils.sync_layer_stack(context))}"

    # --- 3. 「最背面」は同階層維持 (コマへ入らない) --------------------------
    stack = layer_stack_utils.sync_layer_stack(context)
    ti = _index_of(stack, "text", text_key)
    scene.bmanga_active_layer_stack_index = ti
    layer_stack_utils.move_stack_item(context, ti, direction="BACK")
    assert _is_page_child(_text()), (
        f"「最背面」でテキストがコマへ入った: {_rows(layer_stack_utils.sync_layer_stack(context))}"
    )

    # --- 4. 「最前面」も同階層維持 (アウトサイドへ抜けない) ------------------
    stack = layer_stack_utils.sync_layer_stack(context)
    ti = _index_of(stack, "text", text_key)
    scene.bmanga_active_layer_stack_index = ti
    layer_stack_utils.move_stack_item(context, ti, direction="FRONT")
    assert _is_page_child(_text()), (
        f"「最前面」でテキストがアウトサイドへ抜けた: {_rows(layer_stack_utils.sync_layer_stack(context))}"
    )

    print("FLAT_MOVE_INTO_COMA_CHECK_PASS")


main()
