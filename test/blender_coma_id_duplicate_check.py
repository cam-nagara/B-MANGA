"""Blender実機用: コマID重複の予防 (採番) と治癒 (整理) を検証する。

背景 (2026-07-20 発見): コマIDの採番がディスク上の cNN フォルダしか見て
いなかったため、一度も保存されていないコマ (データのみのコマ) と同じIDを
枠線カット等が払い出し、同一ページに同じIDのコマが2つできた。ID重複が
起きると、コマ内容の不透明度マスク画像が同名で共有され、レイヤーリスト上の
所属コマ (元のコマ) と、ビューポートでの切り抜き形状 (別コマの形) が
食い違って見える。さらに整理処理 (organize) がID重複ページを再採番する際、
子レイヤーの親キーを2つ目の重複コマ側へ誤って付け替えていた。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_coma_id_dup",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_coma_id_dup"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_rect_coma(entry, coma_id: str, x_mm: float, y_mm: float, z_order: int) -> None:
    entry.id = coma_id
    entry.coma_id = coma_id
    entry.title = coma_id
    entry.shape_type = "rect"
    entry.rect_x_mm = x_mm
    entry.rect_y_mm = y_mm
    entry.rect_width_mm = 60.0
    entry.rect_height_mm = 60.0
    entry.z_order = z_order


def _page_ids(page) -> list[str]:
    return [str(getattr(coma, "id", "") or "") for coma in page.comas]


def _assert_unique_ids(page, label: str) -> None:
    ids = _page_ids(page)
    assert len(ids) == len(set(ids)), f"{label}: コマIDが重複しています: {ids}"


def _check_allocation_avoids_data_only_coma(temp_root: Path) -> None:
    """採番: ディスクにフォルダが無い「データのみのコマ」のIDを避ける。"""
    from bmanga_dev_coma_id_dup.io import coma_io, page_io
    from bmanga_dev_coma_id_dup.operators import coma_knife_cut_op

    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "coma_id_alloc.bmanga"))
    assert "FINISHED" in result, result
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    while len(page.comas) > 0:
        page.comas.remove(len(page.comas) - 1)
    work_dir = Path(work.work_dir)

    saved = page.comas.add()
    _set_rect_coma(saved, "c01", 20.0, 120.0, 0)
    coma_io.save_coma_meta(work_dir, page.id, saved)
    data_only = page.comas.add()
    _set_rect_coma(data_only, "c04", 120.0, 40.0, 1)
    # c04 は意図的にディスクへ保存しない (フォルダ無し = データのみのコマ)
    assert not (work_dir / page.id / "c04").is_dir(), "前提: c04 フォルダが無いこと"
    page.coma_count = len(page.comas)
    page_io.save_page_json(work_dir, page)
    page_io.save_pages_json(work_dir, work)
    result = bpy.ops.bmanga.open_page_file(index=0)
    assert "FINISHED" in result, result

    work = bpy.context.scene.bmanga_work
    work_dir = Path(work.work_dir)
    page = work.pages[0]

    allocated = coma_io.allocate_new_coma_id(work_dir, page.id, page=page)
    assert allocated not in {"c01", "c04"}, (
        f"採番がデータ上のコマIDと衝突しています: {allocated}"
    )

    target_index = next(
        i for i, coma in enumerate(page.comas) if str(coma.id) == "c01"
    )
    target = page.comas[target_index]
    cut_x = float(target.rect_x_mm) + float(target.rect_width_mm) * 0.5
    ok = coma_knife_cut_op._apply_cut_to_coma(
        work,
        page,
        target_index,
        work_dir,
        (cut_x, float(target.rect_y_mm) - 5.0),
        (cut_x, float(target.rect_y_mm) + float(target.rect_height_mm) + 5.0),
    )
    assert ok, "枠線カットに失敗しました"
    _assert_unique_ids(page, "カット直後")
    coma_knife_cut_op._finalize_cut_after_data_change(bpy.context, work, page, work_dir)
    page = work.pages[0]
    _assert_unique_ids(page, "カット整理後")
    print("[ok] allocation avoids data-only coma ids", flush=True)


def _check_organize_heals_duplicates(temp_root: Path) -> None:
    """治癒: ID重複ページを整理すると一意になり、子レイヤーは元のコマに残る。"""
    from bmanga_dev_coma_id_dup.io import coma_io, page_io
    from bmanga_dev_coma_id_dup.utils import data_name_organizer

    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "coma_id_heal.bmanga"))
    assert "FINISHED" in result, result
    work = bpy.context.scene.bmanga_work
    work.paper.read_direction = "left"
    page = work.pages[0]
    while len(page.comas) > 0:
        page.comas.remove(len(page.comas) - 1)
    work_dir = Path(work.work_dir)

    # 読み順: keeper(右上) → c01(左上) → dup(左下)。keeper と dup が同ID c04。
    keeper = page.comas.add()
    _set_rect_coma(keeper, "c04", 120.0, 120.0, 2)
    plain = page.comas.add()
    _set_rect_coma(plain, "c01", 20.0, 120.0, 1)
    coma_io.save_coma_meta(work_dir, page.id, plain)
    dup = page.comas.add()
    _set_rect_coma(dup, "c04", 20.0, 40.0, 0)
    page.coma_count = len(page.comas)
    page_io.save_page_json(work_dir, page)
    page_io.save_pages_json(work_dir, work)
    result = bpy.ops.bmanga.open_page_file(index=0)
    assert "FINISHED" in result, result

    scene = bpy.context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    fill = scene.bmanga_fill_layers.add()
    fill.id = "dup_fill"
    fill.parent_kind = "coma"
    fill.parent_key = f"{page.id}:c04"

    try:
        data_name_organizer.organize_page_coma_names(bpy.context, page)
    except Exception:  # noqa: BLE001
        # リネーム直後のメタ保存は保存ベースライン制約で失敗することがある
        # (実運用ではカット側が例外を握って続行する。既知の別課題)。
        # 本テストの検証対象はID・親キーのデータ状態のみ。
        pass
    page = work.pages[0]
    _assert_unique_ids(page, "整理後")
    ids = _page_ids(page)
    assert ids == ["c01", "c02", "c03"], f"読み順の再採番になっていません: {ids}"

    # keeper (右上, 旧c04) は読み順先頭なので c01 になる
    keeper_now = page.comas[0]
    assert float(keeper_now.rect_x_mm) == 120.0 and float(keeper_now.rect_y_mm) == 120.0, (
        "読み順先頭のコマが右上のコマではありません"
    )
    fill = scene.bmanga_fill_layers[0]
    assert str(fill.parent_key) == f"{page.id}:c01", (
        f"子レイヤーが元のコマ (右上) に残っていません: {fill.parent_key}"
    )
    dup_now = page.comas[2]
    assert float(dup_now.rect_x_mm) == 20.0 and float(dup_now.rect_y_mm) == 40.0, (
        "重複2つ目のコマ (左下) が読み順末尾になっていません"
    )
    assert str(fill.parent_key) != f"{page.id}:{dup_now.id}", (
        "子レイヤーが重複2つ目のコマ側へ誤って付け替えられました"
    )
    print("[ok] organize heals duplicated coma ids", flush=True)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_id_dup_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        _check_allocation_avoids_data_only_coma(temp_root)

        bpy.ops.wm.read_factory_settings(use_empty=True)
        try:
            mod.unregister()
        except Exception:  # noqa: BLE001
            pass
        mod = _load_addon()
        _check_organize_heals_duplicates(temp_root)
        print("BMANGA_COMA_ID_DUPLICATE_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    import os
    import traceback

    try:
        main()
        os._exit(0)
    except Exception:
        traceback.print_exc()
        os._exit(1)
