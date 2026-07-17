"""Blender実機用: コマ⇔テキスト/フキダシのレイヤー並べ替えとMeldex取込順.

2026-07-18 ユーザー指示:
  1. レイヤーリストで、コマ行をテキスト/フキダシ行よりも背面 (下) へ、また
     その逆へ並べ替えできること (従来は同種の行しか跨げなかった)。
  2. Meldexシナリオ読込後は、テキスト/フキダシ行がコマ行より前面 (上) に
     並ぶこと。旧データ等でコマより背面にある状態からの再取込でも前面へ戻す。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_cross_kind_move"


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
        "source": {"documentId": "scenario-cross-kind"},
        "pages": [
            {"rows": [
                {"rowId": "r1", "type": "会話", "body": "せりふ一", "rubies": []},
                {"rowId": "r2", "type": "会話", "body": "せりふ二", "rubies": []},
            ]},
        ],
    }


def _rows(stack) -> list[tuple[str, str]]:
    return [(item.kind, item.key) for item in stack]


def _index_of(stack, kind: str, key: str) -> int:
    for i, item in enumerate(stack):
        if item.kind == kind and item.key == key:
            return i
    raise AssertionError(f"stack row not found: {kind}:{key} in {_rows(stack)}")


def _assert_text_balloons_in_front_of_comas(stack, label: str) -> None:
    first_coma = min(
        i for i, item in enumerate(stack) if item.kind == "coma"
    )
    behind = [
        (item.kind, item.key)
        for i, item in enumerate(stack)
        if i > first_coma and item.kind in {"text", "balloon"}
    ]
    assert not behind, f"{label}: コマより背面のテキスト/フキダシ行: {behind}"


def main() -> None:
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_cross_kind_move_"))
    work = bpy.context.scene.bmanga_work
    work.loaded = True
    work.work_dir = str(temp_root)
    from bmanga_dev_cross_kind_move import preferences
    from bmanga_dev_cross_kind_move.io import (
        balloon_presets,
        meldex_scenario_import,
        page_io,
        text_presets,
    )
    from bmanga_dev_cross_kind_move.utils import layer_stack as layer_stack_utils

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
    for cid in ("c01", "c02"):
        coma = page.comas.add()
        coma.id = cid
        coma.coma_id = cid
        coma.shape_type = "rect"
        coma.rect_width_mm = 80.0
        coma.rect_height_mm = 80.0
    page.coma_count = len(page.comas)
    work.active_page_index = 0

    context = bpy.context
    scene = context.scene
    result = meldex_scenario_import.import_payload(context, work, _payload())
    assert result["created"] == 2, result

    stack = layer_stack_utils.sync_layer_stack(context)

    # --- 2. 取込直後: テキスト/フキダシがコマより前面 -------------------------
    _assert_text_balloons_in_front_of_comas(stack, "取込直後")

    page_key = "p0001"
    c01_key = f"{page_key}:c01"
    c02_key = f"{page_key}:c02"

    # --- 1a. コマを1段前面へ: 直前のフキダシ行を跨げること --------------------
    idx = _index_of(stack, "coma", c01_key)
    # PropertyGroup参照は位置ベースなので、移動前に文字列で控える
    above_kind = str(stack[idx - 1].kind)
    above_key = str(stack[idx - 1].key)
    assert above_kind in {"text", "balloon"}, _rows(stack)
    scene.bmanga_active_layer_stack_index = idx
    assert layer_stack_utils.move_stack_item(context, idx, direction="UP"), (
        "コマ行をテキスト/フキダシ行より前面へ移動できません"
    )
    stack = layer_stack_utils.sync_layer_stack(context)
    idx = _index_of(stack, "coma", c01_key)
    crossed = _index_of(stack, above_kind, above_key)
    assert idx < crossed, f"UP後もコマが背面のまま: {_rows(stack)}"

    # --- 1b. コマを最前面へ: 全テキスト/フキダシより上になること --------------
    scene.bmanga_active_layer_stack_index = idx
    assert layer_stack_utils.move_stack_item(context, idx, direction="FRONT")
    stack = layer_stack_utils.sync_layer_stack(context)
    idx = _index_of(stack, "coma", c01_key)
    text_balloon_indices = [
        i for i, item in enumerate(stack) if item.kind in {"text", "balloon"}
    ]
    assert idx < min(text_balloon_indices), f"FRONT後の並び: {_rows(stack)}"
    # 前面コマの z_order が最大になっていること
    c01 = next(c for c in page.comas if str(c.coma_id) == "c01")
    c02 = next(c for c in page.comas if str(c.coma_id) == "c02")
    assert int(c01.z_order) > int(c02.z_order), (
        f"z_order不整合: c01={c01.z_order} c02={c02.z_order}"
    )

    # --- 1b-2. align同期 (コマ追加/ナイフカット等が呼ぶ) が手動並びを崩さないこと
    layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
    stack = layer_stack_utils.sync_layer_stack(context)
    idx = _index_of(stack, "coma", c01_key)
    text_balloon_indices = [
        i for i, item in enumerate(stack) if item.kind in {"text", "balloon"}
    ]
    assert idx < min(text_balloon_indices), (
        f"align同期で最前面コマがテキスト背面へ落ちた: {_rows(stack)}"
    )

    # --- 1c. コマを最背面へ戻す: テキスト/フキダシより背面になること ----------
    scene.bmanga_active_layer_stack_index = idx
    assert layer_stack_utils.move_stack_item(context, idx, direction="BACK")
    stack = layer_stack_utils.sync_layer_stack(context)
    idx = _index_of(stack, "coma", c01_key)
    idx_c02 = _index_of(stack, "coma", c02_key)
    assert idx > max(text_balloon_indices := [
        i for i, item in enumerate(stack) if item.kind in {"text", "balloon"}
    ]), f"BACK後の並び: {_rows(stack)}"
    assert idx > idx_c02, f"BACK後にc02より前面のまま: {_rows(stack)}"
    # page.comas も並べ替えで再構成されるため参照を取り直す
    c01 = next(c for c in page.comas if str(c.coma_id) == "c01")
    c02 = next(c for c in page.comas if str(c.coma_id) == "c02")
    assert int(c01.z_order) < int(c02.z_order), (
        f"BACK後のz_order不整合: c01={c01.z_order} c02={c02.z_order}"
    )
    # コマ子行 (コマプレビュー) がコマ行の直後に追従していること
    assert stack[idx + 1].kind == "coma_preview", _rows(stack)
    assert stack[idx + 1].parent_key == c01_key, _rows(stack)

    # --- 1d. テキストを最背面へ: コマより背面へ動かせること --------------------
    text_key = f"{page_key}:text_0001"
    idx = _index_of(stack, "text", text_key)
    scene.bmanga_active_layer_stack_index = idx
    assert layer_stack_utils.move_stack_item(context, idx, direction="BACK"), (
        "テキスト行をコマ行より背面へ移動できません"
    )
    stack = layer_stack_utils.sync_layer_stack(context)
    idx = _index_of(stack, "text", text_key)
    coma_indices = [i for i, item in enumerate(stack) if item.kind == "coma"]
    assert idx > max(coma_indices), f"テキストBACK後の並び: {_rows(stack)}"

    # --- 2b. 旧データ相当 (テキスト/フキダシがコマより背面) からの再取込 ------
    # 残りのテキスト/フキダシ行もすべて最背面へ落とし、旧作品の状態を作る
    for kind, key in [
        ("balloon", f"{page_key}:balloon_0001"),
        ("text", f"{page_key}:text_0002"),
        ("balloon", f"{page_key}:balloon_0002"),
    ]:
        stack = layer_stack_utils.sync_layer_stack(context)
        idx = _index_of(stack, kind, key)
        scene.bmanga_active_layer_stack_index = idx
        assert layer_stack_utils.move_stack_item(context, idx, direction="BACK")
    stack = layer_stack_utils.sync_layer_stack(context)
    first_coma = min(i for i, item in enumerate(stack) if item.kind == "coma")
    behind = [
        (item.kind, item.key)
        for i, item in enumerate(stack)
        if i > first_coma and item.kind in {"text", "balloon"}
    ]
    assert len(behind) == 4, f"前提作りに失敗 (背面行={behind}): {_rows(stack)}"

    result = meldex_scenario_import.import_payload(context, work, _payload())
    assert result["updated"] == 2, result
    stack = layer_stack_utils.sync_layer_stack(context)
    _assert_text_balloons_in_front_of_comas(stack, "再取込後")

    # align同期 (コマ操作相当) 後も前面のまま維持されること
    layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
    stack = layer_stack_utils.sync_layer_stack(context)
    _assert_text_balloons_in_front_of_comas(stack, "再取込+align同期後")
    # ペア隣接 (テキスト直後にフキダシ) が維持されていること
    for text_id, balloon_id in (("text_0001", "balloon_0001"), ("text_0002", "balloon_0002")):
        t_idx = _index_of(stack, "text", f"{page_key}:{text_id}")
        b_idx = _index_of(stack, "balloon", f"{page_key}:{balloon_id}")
        assert b_idx == t_idx + 1, f"ペア隣接が崩れた: {_rows(stack)}"

    print("CROSS_KIND_MOVE_CHECK_PASS")


main()
