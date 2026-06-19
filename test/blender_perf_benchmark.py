"""B-MANGA 標準分量 (55ページ×コマ5+フキダシ+テキスト) パフォーマンス計測.

ユーザーの標準分量: 55 ページ / 各ページ コマ 4+1 / 全コマにフキダシor効果線 /
全フキダシにテキスト。これを合成し、主要処理の時間を計測する。

使い方: blender --background --factory-startup --python bmanga_perf_bench.py
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
MOD = "bmanga_perf_benchmark"
PAGES = 55
COMAS_PER_PAGE = 5
RESULTS: list[tuple[str, float]] = []


def _load_addon():
    spec = importlib.util.spec_from_file_location(MOD, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD] = mod
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    return importlib.import_module(f"{MOD}.{path}")


def _timeit(label: str, fn, *, repeat: int = 1):
    best = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    RESULTS.append((label, best))
    print(f"BENCH {label}: {best*1000:.1f} ms", flush=True)
    return best


def _fill_page_data(work, page, page_no: int) -> None:
    """1 ページ分の標準コンテンツ (コマ5・フキダシ5・テキスト5) をデータに作る."""
    schema = _sub("io.schema")
    del schema
    page.comas.clear()
    page.balloons.clear()
    page.texts.clear()
    for ci in range(COMAS_PER_PAGE):
        coma = page.comas.add()
        coma.id = f"c{ci + 1:02d}"
        coma.rect_x_mm = 15.0 + (ci % 2) * 95.0
        coma.rect_y_mm = 200.0 - (ci // 2) * 90.0
        coma.rect_width_mm = 85.0
        coma.rect_height_mm = 80.0
        balloon = page.balloons.add()
        balloon.id = f"balloon_{page_no:04d}_{ci:02d}"
        balloon.shape = "ellipse"
        balloon.x_mm = 20.0 + (ci % 2) * 95.0
        balloon.y_mm = 205.0 - (ci // 2) * 90.0
        balloon.width_mm = 42.0
        balloon.height_mm = 30.0
        tail = balloon.tails.add()
        tail.type = "straight"
        tail.direction_deg = 250.0
        tail.length_mm = 9.0
        tail.root_width_mm = 4.0
        if ci == 0:
            balloon.fill_gradient_enabled = True
            balloon.fill_gradient_angle_deg = 45.0
        text = page.texts.add()
        text.id = f"text_{page_no:04d}_{ci:02d}"
        text.body = f"セリフ {page_no}-{ci} のテキストです。\n二行目もあります。"
        text.x_mm = balloon.x_mm + 8.0
        text.y_mm = balloon.y_mm + 6.0
        text.width_mm = 26.0
        text.height_mm = 18.0
    page.coma_count = len(page.comas)


def main() -> None:
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_perf_"))
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "Perf.bmanga"))
    assert result == {"FINISHED"}, result
    work_dir = temp_root / "Perf.bmanga"

    page_io = _sub("io.page_io")
    handlers = _sub("utils.handlers")
    page_detail = _sub("utils.page_detail")
    page_preview_object = _sub("utils.page_preview_object")
    export_pipeline = _sub("io.export_pipeline")

    work = bpy.context.scene.bmanga_work

    # --- 55 ページ作成 + データ充填 ---
    t0 = time.perf_counter()
    while len(work.pages) < PAGES:
        assert "FINISHED" in bpy.ops.bmanga.page_add("EXEC_DEFAULT")
    print(f"BENCH setup_pages: {(time.perf_counter()-t0)*1000:.1f} ms", flush=True)
    work = bpy.context.scene.bmanga_work
    for i, page in enumerate(work.pages):
        page_detail.ensure_page_detail(work, page)
        _fill_page_data(work, page, i + 1)
        page_io.save_page_json(work_dir, page)
    page_io.save_pages_json(work_dir, work)
    print("SETUP_DONE", flush=True)

    # --- 計測 1: 全ページ詳細読込 (ページ用blendの起動コスト相当) ---
    def _load_all():
        for page in work.pages:
            page_detail.clear_page_detail(page)
        for page in work.pages:
            page_io.load_page_json(work_dir, page)

    _timeit("load_all_page_json_55p", _load_all, repeat=3)

    # --- 計測 2: 保存 (全ページ詳細読込済み状態 = ページ用blend相当) ---
    _timeit("save_all_1st", lambda: handlers.save_scene_work_to_disk(bpy.context, reason="bench1"))
    _timeit("save_all_2nd_unchanged", lambda: handlers.save_scene_work_to_disk(bpy.context, reason="bench2"))

    # --- 計測 3: プレビュー PNG 全 55 ページ生成 (初回) と再確認 (2回目) ---
    def _previews(force: bool):
        for i, page in enumerate(work.pages):
            page_preview_object.ensure_preview_png(work, page, i, current=False, scene=bpy.context.scene, force=force)

    _timeit("preview_png_55p_force", lambda: _previews(True))
    _timeit("preview_png_55p_cached", lambda: _previews(False))

    # --- 計測 4: ページ出力 1 ページ (グラデフキダシ込み, 220dpi) ---
    page0 = work.pages[0]
    page_detail.ensure_page_detail(work, page0)
    options = export_pipeline.ExportOptions(area="canvas", dpi_override=220)
    _timeit("export_render_page_220dpi", lambda: export_pipeline.render_page(work, page0, options), repeat=2)

    # --- 計測 5: ページ用 blend でのフキダシ同期 (スライダー相当) ---
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    bco = _sub("utils.balloon_curve_object")
    balloon_op = _sub("operators.balloon_op")
    entry = balloon_op._create_balloon_entry(
        bpy.context, page, shape="cloud", x=120.0, y=120.0, w=50.0, h=40.0,
        parent_kind="page", parent_key=str(page.id),
    )
    tail = entry.tails.add()
    tail.type = "straight"
    tail.direction_deg = 250.0
    tail.length_mm = 10.0
    tail.root_width_mm = 4.0
    bco.ensure_balloon_curve_object(scene=bpy.context.scene, entry=entry, page=page)

    def _slider_geometry():
        for i in range(10):
            entry.line_width_mm = 0.5 + 0.01 * i  # 形状が変わる
            bco.ensure_balloon_curve_object(scene=bpy.context.scene, entry=entry, page=page)

    def _slider_color():
        for i in range(10):
            entry.line_color = (0.1 + 0.05 * i, 0.0, 0.0, 1.0)  # 形状は変わらない
            bco.ensure_balloon_curve_object(scene=bpy.context.scene, entry=entry, page=page)

    _timeit("balloon_sync_x10_geometry_change", _slider_geometry)
    _timeit("balloon_sync_x10_color_only", _slider_color)

    # --- 計測 6: レイヤー一覧の再構築 (ページ用blend) ---
    layer_stack = _sub("utils.layer_stack")
    _timeit("layer_stack_sync", lambda: layer_stack.sync_layer_stack(bpy.context), repeat=3)

    # --- 計測 7: ミラー同期 (全要素ensure) ---
    layer_object_sync = _sub("utils.layer_object_sync")
    _timeit(
        "mirror_work_to_outliner_x2_2nd",
        lambda: layer_object_sync.mirror_work_to_outliner(bpy.context.scene, work),
        repeat=2,
    )

    print("==== RESULTS ====", flush=True)
    for label, sec in RESULTS:
        print(f"{label}\t{sec*1000:.1f}", flush=True)
    print("BMANGA_PERF_BENCH_DONE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    sys.stdout.flush()
    os._exit(0)
