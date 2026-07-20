"""Blender 実機用: 全レイヤー種のロック機能の確認.

対象:
1. 全種別 (フキダシ/テキスト/コマ/画像/パターンカーブ/ラスター/塗り/
   フォルダ/GP/効果線) の locked 状態の存在
2. スキーマ (JSON) 往復での locked 維持 (旧作品互換: キー無しは False)
3. 一括ロックオペレーター (bmanga.layer_stack_lock_selected) の混在選択トグル
4. ロック中のフキダシ/テキストがツールの当たり判定から除外されること
5. レイヤー一覧カードのロックアイコンがリンクアイコン直後に描画されること
   (静的順序検証 + スタブレイアウトでの描画内容検証)
"""

from __future__ import annotations

import ast
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_layer_lock",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_layer_lock"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _add_balloon(page, balloon_id: str, parent_key: str, x_mm: float, y_mm: float):
    entry = page.balloons.add()
    entry.id = balloon_id
    entry.shape = "rect"
    entry.x_mm = x_mm
    entry.y_mm = y_mm
    entry.width_mm = 20.0
    entry.height_mm = 14.0
    entry.parent_kind = "page"
    entry.parent_key = parent_key
    return entry


def _add_text(page, text_id: str, parent_key: str, x_mm: float, y_mm: float):
    entry = page.texts.add()
    entry.id = text_id
    entry.body = text_id
    entry.x_mm = x_mm
    entry.y_mm = y_mm
    entry.width_mm = 16.0
    entry.height_mm = 10.0
    entry.parent_kind = "page"
    entry.parent_key = parent_key
    return entry


def _add_image(context, image_id: str, parent_key: str):
    entry = context.scene.bmanga_image_layers.add()
    entry.id = image_id
    entry.title = image_id
    entry.parent_kind = "page"
    entry.parent_key = parent_key
    return entry


def _add_image_path(context, image_path_id: str, parent_key: str):
    entry = context.scene.bmanga_image_path_layers.add()
    entry.id = image_path_id
    entry.title = image_path_id
    entry.parent_kind = "page"
    entry.parent_key = parent_key
    return entry


def _add_raster(context, raster_id: str, parent_key: str):
    entry = context.scene.bmanga_raster_layers.add()
    entry.id = raster_id
    entry.title = raster_id
    entry.scope = "page"
    entry.parent_kind = "page"
    entry.parent_key = parent_key
    return entry


def _add_fill(context, fill_id: str, parent_key: str):
    entry = context.scene.bmanga_fill_layers.add()
    entry.id = fill_id
    entry.title = fill_id
    entry.parent_kind = "page"
    entry.parent_key = parent_key
    return entry


def _add_folder(work, folder_id: str, parent_key: str):
    entry = work.layer_folders.add()
    entry.id = folder_id
    entry.title = folder_id
    entry.parent_key = parent_key
    return entry


def _add_gp_layer(context, parent_key: str):
    from bmanga_dev_layer_lock.utils import gp_object_layer
    from bmanga_dev_layer_lock.utils import layer_object_model

    obj = gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bmanga_id=layer_object_model.make_stable_id("gp"),
        title="lock_gp",
        z_index=310,
        parent_kind="page",
        parent_key=parent_key,
    )
    assert obj is not None
    return obj


def _stack(context):
    from bmanga_dev_layer_lock.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    assert stack is not None
    layer_stack_utils.remember_layer_stack_signature(context)
    return stack


def _find_stack_item(context, uid: str):
    from bmanga_dev_layer_lock.utils import layer_stack as layer_stack_utils

    for index, item in enumerate(_stack(context)):
        if layer_stack_utils.stack_item_uid(item) == uid:
            return index, item
    raise AssertionError(f"stack item not found: {uid}")


def _check_draw_order_static() -> None:
    """draw_item 内で _draw_lock_slot が _draw_link_state_icon の直後に呼ばれることを
    ソース上で検証する (静的順序検証)。

    draw_item のトップレベル文だけを source 順に走査する (ast.walk は BFS で
    子ノードへ潜る順序があいまいなため、あえて使わない)。
    """

    source = (ROOT / "panels" / "gpencil_panel.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    ul_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BMANGA_UL_layer_stack"
    )
    draw_item = next(
        node for node in ul_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "draw_item"
    )
    call_names: list[str] = []
    for stmt in draw_item.body:
        call = stmt.value if isinstance(stmt, ast.Expr) else None
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
            call_names.append(call.func.id)
    link_idx = call_names.index("_draw_link_state_icon")
    lock_idx = call_names.index("_draw_lock_slot")
    if lock_idx != link_idx + 1:
        raise AssertionError(
            f"_draw_lock_slot がリンクアイコンの直後に呼ばれていません: {call_names}"
        )


class _StubCell:
    """gpencil_panel の描画ヘルパーが要求する最小 UILayout 相当のスタブ."""

    def __init__(self):
        self.ui_units_x = None
        self.calls: list[tuple] = []

    def row(self, align=True):  # noqa: ARG002
        return self

    def operator(self, opname, text="", icon="NONE", emboss=True):  # noqa: ARG002
        self.calls.append(("operator", opname, icon))
        return SimpleNamespace(index=-1)

    def label(self, text="", icon="NONE"):  # noqa: ARG002
        self.calls.append(("label", icon))


def _check_draw_lock_slot_stub(gpencil_panel) -> None:
    # ロック可能種別 (raster): locked=True/False どちらもロックアイコン操作子を描く
    locked_target = SimpleNamespace(locked=True)
    cell = _StubCell()
    gpencil_panel._draw_lock_slot(cell, SimpleNamespace(kind="raster"), {"target": locked_target}, 3)
    assert cell.calls, "ロック可能種別でロックスロットが描画されていません"
    assert cell.calls[-1][:2] == ("operator", "bmanga.layer_stack_toggle_lock")
    assert cell.calls[-1][2] == "LOCKED", cell.calls

    unlocked_target = SimpleNamespace(locked=False)
    cell = _StubCell()
    gpencil_panel._draw_lock_slot(cell, SimpleNamespace(kind="raster"), {"target": unlocked_target}, 3)
    assert cell.calls[-1][2] == "UNLOCKED", cell.calls

    # ロック非対応種別 (page): 同幅の placeholder ラベルのみ
    cell = _StubCell()
    gpencil_panel._draw_lock_slot(cell, SimpleNamespace(kind="page"), {"target": SimpleNamespace()}, 0)
    assert cell.calls and cell.calls[-1][0] == "label", cell.calls
    assert not any(call[0] == "operator" for call in cell.calls), cell.calls


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_layer_lock_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "LayerLock.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file(index=0)
        assert "FINISHED" in result, result

        from bmanga_dev_layer_lock.io import schema
        from bmanga_dev_layer_lock.operators import balloon_op, text_op
        from bmanga_dev_layer_lock.panels import gpencil_panel
        from bmanga_dev_layer_lock.utils import layer_lock
        from bmanga_dev_layer_lock.utils import layer_object_model
        from bmanga_dev_layer_lock.utils import layer_stack as layer_stack_utils
        from bmanga_dev_layer_lock.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        page_key = page_stack_key(page)
        assert len(page.comas) >= 1, "デフォルトコマがありません"
        # コマヒットテストの決定性のため、参照を持つ前に単一コマへ整理する
        # (CollectionProperty.remove() は他の既存参照を無効化し得るため)
        while len(page.comas) > 1:
            page.comas.remove(len(page.comas) - 1)
        coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = 0.0
        coma.rect_y_mm = 0.0
        coma.rect_width_mm = 30.0
        coma.rect_height_mm = 30.0

        # ---------- ① 全種別の locked プロパティ存在 (既定 False) ----------
        balloon = _add_balloon(page, "lock_balloon", page_key, 10.0, 10.0)
        text = _add_text(page, "lock_text", page_key, 40.0, 40.0)
        image = _add_image(context, "lock_image", page_key)
        image_path = _add_image_path(context, "lock_image_path", page_key)
        raster = _add_raster(context, "lock_raster", page_key)
        fill = _add_fill(context, "lock_fill", page_key)
        folder = _add_folder(work, "lock_folder", page_key)
        gp_obj = _add_gp_layer(context, page_key)

        from bmanga_dev_layer_lock.operators import effect_line_op

        eff_obj, _eff_layer = effect_line_op._create_effect_layer(
            context, (60.0, 60.0, 20.0, 16.0), parent_key=page_key,
        )

        for label, entry in (
            ("balloon", balloon),
            ("text", text),
            ("coma", coma),
            ("image", image),
            ("image_path", image_path),
            ("raster", raster),
            ("fill", fill),
            ("layer_folder", folder),
        ):
            assert hasattr(entry, "locked"), f"{label}: locked プロパティがありません"
            assert bool(getattr(entry, "locked")) is False, f"{label}: 既定値が False ではありません"

        assert layer_object_model.user_locked(gp_obj) is False, "gp: 既定で未ロックではありません"
        assert layer_object_model.user_locked(eff_obj) is False, "effect: 既定で未ロックではありません"

        # utils/layer_lock.py の種別横断アクセサを直接検証
        fake_raster_item = SimpleNamespace(kind="raster")
        fake_raster_resolved = {"target": SimpleNamespace(locked=False)}
        assert layer_lock.is_lockable(fake_raster_item, fake_raster_resolved) is True
        assert layer_lock.get_locked(fake_raster_item, fake_raster_resolved) is False
        assert layer_lock.set_locked(fake_raster_item, fake_raster_resolved, True) is True
        assert layer_lock.get_locked(fake_raster_item, fake_raster_resolved) is True

        fake_page_item = SimpleNamespace(kind="page")
        assert layer_lock.is_lockable(fake_page_item, {"target": SimpleNamespace()}) is False
        assert layer_lock.get_locked(fake_page_item, {"target": SimpleNamespace()}) is False

        gp_item = SimpleNamespace(kind="gp")
        gp_resolved = {"object": gp_obj}
        assert layer_lock.is_lockable(gp_item, gp_resolved) is True
        assert layer_lock.get_locked(gp_item, gp_resolved) is False
        assert layer_lock.set_locked(gp_item, gp_resolved, True) is True
        assert layer_object_model.user_locked(gp_obj) is True
        assert layer_lock.set_locked(gp_item, gp_resolved, False) is True
        assert layer_object_model.user_locked(gp_obj) is False

        # ---------- ② スキーマ (JSON) 往復での locked 維持 ----------
        balloon.locked = True
        text.locked = True
        coma.locked = True
        image.locked = True
        image_path.locked = True
        raster.locked = True
        fill.locked = True
        folder.locked = True

        page_data = schema.page_to_dict(page)
        work_data = schema.work_to_dict(work)

        page.balloons.clear()
        page.texts.clear()
        page.comas.clear()
        schema.page_from_dict(page, page_data)
        # Blender の CollectionProperty は clear()/remove() で既存の Python 参照が
        # 無効になるため、以後使う変数はロード後の実体へ張り直す (add() のみは
        # 既存参照を壊さない — 本ファイル内の他の .add() 呼び出しは張り直し不要)。
        balloon = next(e for e in page.balloons if e.id == "lock_balloon")
        text = next(e for e in page.texts if e.id == "lock_text")
        coma = page.comas[0]
        assert balloon.locked is True, "フキダシの locked がスキーマ往復で失われました"
        assert text.locked is True, "テキストの locked がスキーマ往復で失われました"
        assert bool(getattr(coma, "locked", False)) is True, "コマの locked がスキーマ往復で失われました"

        context.scene.bmanga_image_layers.clear()
        context.scene.bmanga_image_path_layers.clear()
        context.scene.bmanga_raster_layers.clear()
        context.scene.bmanga_fill_layers.clear()
        work.layer_folders.clear()
        schema.work_from_dict(work, work_data)
        image = next(e for e in context.scene.bmanga_image_layers if e.id == "lock_image")
        image_path = next(e for e in context.scene.bmanga_image_path_layers if e.id == "lock_image_path")
        raster = next(e for e in context.scene.bmanga_raster_layers if e.id == "lock_raster")
        fill = next(e for e in context.scene.bmanga_fill_layers if e.id == "lock_fill")
        folder = next(e for e in work.layer_folders if e.id == "lock_folder")
        assert image.locked is True, "画像の locked がスキーマ往復で失われました"
        assert image_path.locked is True, "パターンカーブの locked がスキーマ往復で失われました"
        assert raster.locked is True, "ラスターの locked がスキーマ往復で失われました"
        assert fill.locked is True, "塗りの locked がスキーマ往復で失われました"
        assert folder.locked is True, "フォルダの locked がスキーマ往復で失われました"

        # 旧作品互換: JSON に "locked" キーが無ければ False を維持する。
        # page.balloons の既存参照 (balloon) を壊さないよう、別コレクション
        # (work.shared_balloons) を使い捨てで使う。
        legacy_dict = dict(schema.balloon_entry_to_dict(balloon))
        del legacy_dict["locked"]
        legacy_entry = work.shared_balloons.add()
        schema.balloon_entry_from_dict(legacy_entry, legacy_dict)
        assert legacy_entry.locked is False, "旧データ (locked キー無し) が False にフォールバックしません"
        work.shared_balloons.clear()

        # ここまでの往復確認用に True にした locked を、後続のヒットテスト
        # 検証 (④) のため未ロックへ戻しておく。
        balloon.locked = False
        text.locked = False
        coma.locked = False

        # ---------- ③ 一括ロックオペレーターの混在選択トグル ----------
        mix_balloon = _add_balloon(page, "mix_balloon", page_key, 70.0, 10.0)
        mix_text = _add_text(page, "mix_text", page_key, 90.0, 10.0)
        mix_balloon.locked = False
        mix_text.locked = True  # 混在選択 (未ロック1件+ロック済み1件)

        _stack(context)
        mix_balloon_uid = layer_stack_utils.target_uid("balloon", f"{page_key}:{mix_balloon.id}")
        mix_text_uid = layer_stack_utils.target_uid("text", f"{page_key}:{mix_text.id}")
        _idx, _item = _find_stack_item(context, mix_balloon_uid)
        _idx, _item = _find_stack_item(context, mix_text_uid)
        # 「選択中のレイヤーのロックを切替」が本当に選択集合だけを対象にする
        # ことを検証するため、既存の選択/アクティブ状態を先にクリアする
        # (GP/効果線オブジェクトは作成直後に Blender ネイティブ選択が残るため
        # 明示的に全解除する)。
        layer_stack_utils.clear_all_selection(context)
        context.scene.bmanga_active_layer_stack_index = -1
        for obj in bpy.data.objects:
            try:
                obj.select_set(False)
            except Exception:  # noqa: BLE001
                pass
        mix_balloon.selected = True
        mix_text.selected = True

        result = bpy.ops.bmanga.layer_stack_lock_selected("EXEC_DEFAULT")
        assert "FINISHED" in result, result
        assert mix_balloon.locked is True, "混在選択の一括ロックで未ロック側がロックされません"
        assert mix_text.locked is True, "混在選択の一括ロックで既ロック側の状態が壊れました"

        # 全ロック済みの状態でもう一度実行 -> 全解除
        result = bpy.ops.bmanga.layer_stack_lock_selected("EXEC_DEFAULT")
        assert "FINISHED" in result, result
        assert mix_balloon.locked is False, "全ロック済み選択の再実行で解除されません"
        assert mix_text.locked is False, "全ロック済み選択の再実行で解除されません"

        mix_balloon.selected = False
        mix_text.selected = False

        # ---------- 単一行ロック切替オペレーター (カードのロックアイコン相当) ----------
        _stack(context)
        raster_uid = layer_stack_utils.target_uid("raster", raster.id)
        idx, _item = _find_stack_item(context, raster_uid)
        assert raster.locked is True
        result = bpy.ops.bmanga.layer_stack_toggle_lock(index=idx)
        assert "FINISHED" in result, result
        assert raster.locked is False, "単一行ロック切替が効きません"
        result = bpy.ops.bmanga.layer_stack_toggle_lock(index=idx)
        assert "FINISHED" in result, result
        assert raster.locked is True

        _stack(context)
        gp_uid = layer_stack_utils.target_uid("gp", layer_object_model.stable_id(gp_obj))
        idx, _item = _find_stack_item(context, gp_uid)
        assert layer_object_model.user_locked(gp_obj) is False
        result = bpy.ops.bmanga.layer_stack_toggle_lock(index=idx)
        assert "FINISHED" in result, result
        assert layer_object_model.user_locked(gp_obj) is True, "GPレイヤーの単一行ロック切替が効きません"
        assert bool(gp_obj.hide_select) is True, "GPロックがオブジェクト選択除外に連動していません"

        # ---------- ④ ロック中のフキダシ/テキストがツールの当たり判定から除外される ----------
        # 矩形の中心を狙う (境界値ではなく確実に内側のヒットにするため)
        balloon_center_x = float(balloon.x_mm) + float(balloon.width_mm) * 0.5
        balloon_center_y = float(balloon.y_mm) + float(balloon.height_mm) * 0.5
        text_center_x = float(text.x_mm) + float(text.width_mm) * 0.5
        text_center_y = float(text.y_mm) + float(text.height_mm) * 0.5

        hit_idx, hit_entry, _part = balloon_op._hit_balloon_entry(page, balloon_center_x, balloon_center_y)
        assert hit_entry is not None and hit_entry.id == "lock_balloon", "ロック前のフキダシがヒットしません"
        balloon.locked = True
        hit_idx, hit_entry, _part = balloon_op._hit_balloon_entry(page, balloon_center_x, balloon_center_y)
        assert hit_entry is None, "ロック中のフキダシがヒットテストから除外されていません"
        balloon.locked = False
        hit_idx, hit_entry, _part = balloon_op._hit_balloon_entry(page, balloon_center_x, balloon_center_y)
        assert hit_entry is not None, "ロック解除後にフキダシが再びヒットしません"

        hit_idx, hit_entry, _part = text_op._hit_text_entry(page, text_center_x, text_center_y)
        assert hit_entry is not None and hit_entry.id == "lock_text", "ロック前のテキストがヒットしません"
        text.locked = True
        hit_idx, hit_entry, _part = text_op._hit_text_entry(page, text_center_x, text_center_y)
        assert hit_entry is None, "ロック中のテキストがヒットテストから除外されていません"
        text.locked = False
        hit_idx, hit_entry, _part = text_op._hit_text_entry(page, text_center_x, text_center_y)
        assert hit_entry is not None, "ロック解除後にテキストが再びヒットしません"

        # コマ: coma_picker のヒットテスト (ページローカル座標、grid offset非依存)
        # から除外される
        from bmanga_dev_layer_lock.operators import coma_picker

        coma_hit = coma_picker._hit_test_page(page, 5.0, 5.0)
        assert coma_hit == 0, f"ロック前のコマがヒットしません: {coma_hit}"
        coma.locked = True
        coma_hit_locked = coma_picker._hit_test_page(page, 5.0, 5.0)
        assert coma_hit_locked != 0, "ロック中のコマが coma_picker のヒットテストから除外されていません"
        coma.locked = False
        coma_hit_after = coma_picker._hit_test_page(page, 5.0, 5.0)
        assert coma_hit_after == 0, "ロック解除後にコマが再びヒットしません"

        # ---------- ⑤ カード描画: ロックアイコンがリンクアイコン直後 ----------
        _check_draw_order_static()
        _check_draw_lock_slot_stub(gpencil_panel)

        print("BMANGA_LAYER_LOCK_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
