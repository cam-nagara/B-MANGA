"""Blender 実機チェック: ファイル役割ごとのデータ整理 (v0.6.279).

- 作品ファイル (work.blend) はページ一覧のみを扱い、各ページの詳細
  (コマ・フキダシ・テキスト) をメモリに持たない
- 詳細未読込ページの page.json は保存時に上書きされない (データ保護)
- プレビュー再生成は詳細をその場で読み込み、使用後に破棄する
- ページ出力は詳細をその場で読み込んで正しく描ける
- 見開き結合は作品ファイルからでも詳細を読み込んで動く
- ページ用 blend では従来どおり全ページの詳細を持つ
- 一覧の列数の初期値が 8
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bname_dev_work_slim"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    return importlib.import_module(f"{MOD_NAME}.{path}")


def main() -> None:
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bname_work_slim_"))
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "SlimWork.bname"))
    assert result == {"FINISHED"}, result
    work_dir = temp_root / "SlimWork.bname"

    page_file_scene = _sub("utils.page_file_scene")
    page_detail = _sub("utils.page_detail")
    handlers = _sub("utils.handlers")

    # --- 列数の初期値 ---
    assert int(bpy.context.scene.bname_overview_cols) == 8, bpy.context.scene.bname_overview_cols
    work = bpy.context.scene.bname_work
    assert int(work.view_overview_cols) == 8, work.view_overview_cols
    print("OVERVIEW_COLS_DEFAULT_OK", flush=True)

    # --- ページを増やし、ページ側でコマ/フキダシ入りの page.json を作る ---
    assert "FINISHED" in bpy.ops.bname.page_add("EXEC_DEFAULT")
    result = bpy.ops.bname.open_page_file("EXEC_DEFAULT", index=0)
    assert result == {"FINISHED"}, result
    assert page_file_scene.is_page_edit_scene(bpy.context.scene)
    work = bpy.context.scene.bname_work
    page = work.pages[0]
    # ページ用 blend では全ページの詳細が読み込まれている
    assert all(bool(p.detail_loaded) for p in work.pages), [p.detail_loaded for p in work.pages]
    balloon_op = _sub("operators.balloon_op")
    entry = balloon_op._create_balloon_entry(
        bpy.context, page, shape="ellipse", x=40.0, y=120.0, w=50.0, h=40.0,
        parent_kind="page", parent_key=str(page.id),
    )
    assert entry is not None
    assert handlers.save_scene_work_to_disk(bpy.context, reason="test"), "保存に失敗"
    page_json = work_dir / "p0001" / "page.json"
    saved = json.loads(page_json.read_text(encoding="utf-8"))
    assert len(saved.get("comas", [])) >= 1, "コマが page.json にありません"
    assert len(saved.get("balloons", [])) == 1, "フキダシが page.json にありません"
    print("PAGE_SCENE_DETAIL_OK", flush=True)

    # --- 作品ファイルへ戻る: 詳細を持たない ---
    result = bpy.ops.bname.exit_page_file("EXEC_DEFAULT")
    assert "FINISHED" in result, result
    assert page_file_scene.is_work_list_scene(bpy.context.scene), "作品ファイルに戻れていません"
    work = bpy.context.scene.bname_work
    page = work.pages[0]
    assert not bool(page.detail_loaded), "作品ファイルで詳細読込フラグが立っています"
    assert len(page.comas) == 0, f"作品ファイルがコマを保持: {len(page.comas)}"
    assert len(page.balloons) == 0, f"作品ファイルがフキダシを保持: {len(page.balloons)}"
    assert len(page.texts) == 0, "作品ファイルがテキストを保持"
    print("WORK_SCENE_SLIM_OK", flush=True)

    # --- 保存ガード: 作品ファイルから保存しても page.json は上書きされない ---
    before = page_json.read_text(encoding="utf-8")
    assert handlers.save_scene_work_to_disk(bpy.context, reason="test-slim")
    after = page_json.read_text(encoding="utf-8")
    saved = json.loads(after)
    assert len(saved.get("comas", [])) >= 1 and len(saved.get("balloons", [])) == 1, (
        "作品ファイルからの保存で page.json が空データに上書きされました"
    )
    del before
    print("SAVE_GUARD_OK", flush=True)

    # --- プレビュー再生成: 詳細をその場で読み込み、使用後に破棄 ---
    page_preview_object = _sub("utils.page_preview_object")
    png = page_preview_object.ensure_preview_png(
        work, page, 0, current=False, scene=bpy.context.scene, force=True
    )
    assert png is not None and Path(png).is_file(), "プレビュー再生成に失敗"
    assert not bool(page.detail_loaded), "プレビュー後に詳細が残っています"
    assert len(page.comas) == 0 and len(page.balloons) == 0
    # 中身が白紙でない (コマ枠線などが描かれている) ことを確認
    from PIL import Image

    img = Image.open(png).convert("L")
    extrema = img.getextrema()
    assert extrema[0] < 200, f"プレビューが白紙に見えます: extrema={extrema}"
    print("PREVIEW_ON_DEMAND_OK", flush=True)

    # --- ページ出力: 作品ファイルからでも詳細をその場で読み込んで描ける ---
    export_pipeline = _sub("io.export_pipeline")
    options = export_pipeline.ExportOptions(area="canvas", dpi_override=72)
    img = export_pipeline.render_page(work, page, options)
    assert img is not None, "作品ファイルからのページ出力に失敗"
    assert bool(page.detail_loaded), "出力時のオンデマンド読込が行われていません"
    assert len(page.balloons) == 1
    print("EXPORT_ON_DEMAND_OK", flush=True)

    # --- 見開き結合: 作品ファイルから実行しても内容が保持される ---
    page_detail.clear_page_detail(page)  # スリム状態へ戻す
    coma_count_p1 = len(json.loads((work_dir / "p0001" / "page.json").read_text(encoding="utf-8")).get("comas", []))
    coma_count_p2 = len(json.loads((work_dir / "p0002" / "page.json").read_text(encoding="utf-8")).get("comas", []))
    result = bpy.ops.bname.pages_merge_spread("EXEC_DEFAULT", left_index=0)
    assert "FINISHED" in result, result
    merged = work.pages[0]
    assert merged.spread
    assert len(merged.comas) == coma_count_p1 + coma_count_p2, (
        len(merged.comas), coma_count_p1, coma_count_p2
    )
    assert len(merged.balloons) == 1, "見開き結合でフキダシが失われました"
    merged_json = json.loads((work_dir / merged.dir_rel.strip("/") / "page.json").read_text(encoding="utf-8"))
    assert len(merged_json.get("balloons", [])) == 1
    print("SPREAD_MERGE_ON_DEMAND_OK", flush=True)

    # --- ページ一覧上ではコマを選択できない (ピッカーのゲート) ---
    coma_picker = _sub("operators.coma_picker")
    assert page_file_scene.is_work_list_scene(bpy.context.scene)
    hit = coma_picker.find_coma_at_event(bpy.context, type("E", (), {"mouse_x": 0, "mouse_y": 0})())
    assert hit is None, "作品ファイルでコマがヒットしています"
    print("WORK_PICK_GUARD_OK", flush=True)

    print("BNAME_WORK_FILE_SLIM_CHECK_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        os._exit(1)
    os._exit(0)
