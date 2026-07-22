"""Blender実機用: パターンカーブ (image_path) レイヤーがPNG書き出しに出力されることを確認.

v0.6.569 以前は書き出しパイプラインに image_path のレンダラーが存在せず、
PNG/PSD 書き出しでパターンカーブだけが常に欠落していた (既存未実装ギャップ)。
本テストは io/export_image_path.py の追加を回帰保護する:
  1. shape/stamp のパターンカーブがパス上に出力され、パス外には出力されないこと
  2. image/ribbon も出力されること
  3. コマ配下ではコマ外へはみ出した部分がコママスクで切られること
  4. visible=False では出力されないこと
  5. レイヤーリスト順 (塗りとの前後) が出力へ反映されること
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_image_path_export",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_image_path_export"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _options(export_pipeline):
    return export_pipeline.ExportOptions(
        area="canvas",
        dpi_override=120,
        include_paper_color=True,
        include_coma_backgrounds=True,
        include_coma_previews=False,
        include_nombre=False,
        include_work_info=False,
        include_tombo=False,
    )


def _px_at(img, x_mm: float, y_mm: float, dpi: int):
    from bmanga_dev_image_path_export.utils.geom import mm_to_px

    x = int(round(mm_to_px(x_mm, dpi)))
    y = img.height - int(round(mm_to_px(y_mm, dpi)))
    x = max(0, min(img.width - 1, x))
    y = max(0, min(img.height - 1, y))
    return img.convert("RGBA").getpixel((x, y))


def _add_path_entry(scene, entry_id: str, points, **kwargs):
    # setattr のたびに update コールバック→ビューポート自動同期が発火し、
    # 親キー未設定の途中状態で path_points_json が座標書き戻しで壊れるため、
    # 実運用ツールと同じく suspend_auto_sync で構築し、最後に1回だけ同期する。
    from bmanga_dev_image_path_export.utils import image_path_object

    with image_path_object.suspend_auto_sync():
        entry = scene.bmanga_image_path_layers.add()
        entry.id = entry_id
        entry.title = entry_id
        for key, value in kwargs.items():
            setattr(entry, key, value)
        entry.path_points_json = json.dumps([[float(x), float(y)] for x, y in points])
    image_path_object.on_image_path_entry_changed(entry)
    return entry


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_image_path_export_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PathExport.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        from bmanga_dev_image_path_export.core.work import get_work
        from bmanga_dev_image_path_export.io import export_pipeline
        from bmanga_dev_image_path_export.utils import layer_hierarchy
        from bmanga_dev_image_path_export.utils import layer_stack as layer_stack_utils

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        page = work.pages[0]
        options = _options(export_pipeline)
        dpi = 120

        # work_new の初期ページはページほぼ全面を覆うコマを1つ持つ。ページ直下
        # レイヤーはコマ背景の背面に合成される既存仕様のため、そのままだと
        # ページ直下の検証点がコマ背景に覆われてしまう。既存コマを右上の
        # 小さな矩形に作り直し、コママスク検証 (パート3) でも使う。
        if len(page.comas) == 0:
            coma = page.comas.add()
            coma.id = "c01"
            coma.coma_id = "c01"
            coma.title = "c01"
        else:
            coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = 30.0
        coma.rect_y_mm = 260.0
        coma.rect_width_mm = 60.0
        coma.rect_height_mm = 60.0
        coma.z_order = 0
        coma_key = layer_hierarchy.coma_stack_key(page, coma)

        # --- 1. shape/stamp (ページ直下・水平パス・赤い円) ---
        shape_entry = _add_path_entry(
            scene,
            "path_shape01",
            [(40.0, 200.0), (140.0, 200.0)],
            content_source="shape",
            shape_kind="circle",
            draw_mode="stamp",
            brush_size_mm=14.0,
            spacing_percent=100.0,
            color=(1.0, 0.0, 0.0, 1.0),
            parent_kind="page",
            parent_key=str(page.id),
        )
        img = export_pipeline.render_page(work, page, options)
        assert img is not None
        on_path = _px_at(img, 40.0, 200.0, dpi)
        off_path = _px_at(img, 90.0, 240.0, dpi)
        assert on_path[0] > 180 and on_path[1] < 100 and on_path[2] < 100, (
            f"パス上のスタンプ (始点) が赤くありません: {on_path}"
        )
        assert not (off_path[0] > 180 and off_path[1] < 100), (
            f"パス外にスタンプ色が漏れています: {off_path}"
        )

        # --- 4. visible=False で消えること ---
        shape_entry.visible = False
        img_hidden = export_pipeline.render_page(work, page, options)
        hidden_px = _px_at(img_hidden, 40.0, 200.0, dpi)
        assert not (hidden_px[0] > 180 and hidden_px[1] < 100), (
            f"非表示のパターンカーブが出力されています: {hidden_px}"
        )
        shape_entry.visible = True

        # --- 2. image/ribbon (小さな緑PNGをテスト内生成) ---
        from PIL import Image as PILImage

        src_path = temp_root / "ribbon_src.png"
        PILImage.new("RGBA", (16, 16), (0, 200, 0, 255)).save(src_path)
        _add_path_entry(
            scene,
            "path_ribbon01",
            [(40.0, 100.0), (140.0, 100.0)],
            content_source="image",
            draw_mode="ribbon",
            filepath=str(src_path),
            brush_size_mm=10.0,
            parent_kind="page",
            parent_key=str(page.id),
        )
        img2 = export_pipeline.render_page(work, page, options)
        ribbon_px = _px_at(img2, 90.0, 100.0, dpi)
        assert ribbon_px[1] > 120 and ribbon_px[0] < 120, (
            f"リボンの画像色 (緑) がパス上に出力されていません: {ribbon_px}"
        )

        # --- 3. コマ配下のコママスク ---
        # コマ内から右外へはみ出す水平パス
        _add_path_entry(
            scene,
            "path_masked01",
            [(40.0, 290.0), (140.0, 290.0)],
            content_source="shape",
            shape_kind="circle",
            draw_mode="stamp",
            brush_size_mm=12.0,
            color=(0.0, 0.0, 1.0, 1.0),
            parent_kind="coma",
            parent_key=coma_key,
        )
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        img3 = export_pipeline.render_page(work, page, options)
        inside_px = _px_at(img3, 40.0, 290.0, dpi)
        outside_px = _px_at(img3, 140.0, 290.0, dpi)
        assert inside_px[2] > 150 and inside_px[0] < 120, (
            f"コマ内のパターンカーブが出力されていません: {inside_px}"
        )
        assert not (outside_px[2] > 150 and outside_px[0] < 120), (
            f"コマ外へはみ出した部分がマスクで切られていません: {outside_px}"
        )

        # --- 5. レイヤーリスト順の反映 ---
        # 書き出しのリスト順反映 (apply_coma_preview_order) はコマ配下のレイヤー
        # だけが対象 (ページ直下は固定カテゴリ順が既存契約) のため、コマ内の
        # 青スタンプ (path_masked01) と不透明な塗りの前後で検証する。
        fill_entry = scene.bmanga_fill_layers.add()
        fill_entry.id = "order_fill01"
        fill_entry.title = "順序確認塗り"
        fill_entry.fill_type = "solid"
        fill_entry.color = (1.0, 1.0, 0.0, 1.0)
        fill_entry.opacity = 100.0
        fill_entry.parent_kind = "coma"
        fill_entry.parent_key = coma_key
        fill_entry.use_region = True
        fill_entry.region_x_mm = 30.0
        fill_entry.region_y_mm = 280.0
        fill_entry.region_width_mm = 60.0
        fill_entry.region_height_mm = 20.0

        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        assert stack is not None
        path_uid = layer_stack_utils.target_uid("image_path", "path_masked01")
        fill_uid = layer_stack_utils.target_uid("fill", "order_fill01")
        stack_uids = [layer_stack_utils.stack_item_uid(item) for item in stack]
        assert path_uid in stack_uids and fill_uid in stack_uids, (
            f"コマ配下のレイヤーが一覧にありません: {stack_uids}"
        )

        def _move_before(uid: str, anchor_uid: str) -> None:
            from_idx = next(
                i for i, item in enumerate(stack)
                if layer_stack_utils.stack_item_uid(item) == uid
            )
            anchor_idx = next(
                i for i, item in enumerate(stack)
                if layer_stack_utils.stack_item_uid(item) == anchor_uid
            )
            if from_idx < anchor_idx:
                anchor_idx -= 1
            stack.move(from_idx, anchor_idx)

        # 書き出しの順序反映 (apply_coma_preview_order) は「コマプレビュー行が
        # 書き出しレイヤー内に存在する」時だけ発火する。未編集コマはプレビュー
        # PNGを持たないため、実運用でプレビュー行が担う役割を透明1pxのスタブで
        # 代用して順序機構を発火させる (既存のコマ内容z-orderテストと同じ発想)。
        from bmanga_dev_image_path_export.io import export_stack_order

        preview_uid = layer_stack_utils.target_uid(
            layer_stack_utils.COMA_PREVIEW_KIND,
            layer_stack_utils.coma_preview_key(coma_key),
        )

        def _render_with_preview_boundary():
            build_layers = export_pipeline.build_page_layers(work, page, options)
            build_layers.append(
                export_pipeline.ExportLayer(
                    "preview_stub",
                    export_pipeline.Image.new("RGBA", (1, 1), (0, 0, 0, 0)),
                    0,
                    0,
                    stack_uid=preview_uid,
                    stack_parent_key=coma_key,
                )
            )
            ordered = export_stack_order.apply_coma_preview_order(work, page, build_layers)
            size = export_pipeline._page_canvas_size_px(work, page, options)
            return export_pipeline._flatten_layers(ordered, size)

        # 塗りを前面 (リスト上でパターンカーブより上) にする → 青スタンプは隠れる
        _move_before(fill_uid, path_uid)
        layer_stack_utils.apply_stack_order(context)
        img_fill_front = _render_with_preview_boundary()
        covered_px = _px_at(img_fill_front, 40.0, 290.0, dpi)
        assert covered_px[0] > 180 and covered_px[1] > 180 and covered_px[2] < 120, (
            f"前面の塗りがコマ内パターンカーブを覆っていません: {covered_px}"
        )

        # パターンカーブを前面へ戻す → 青スタンプが塗りの上に見える
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        _move_before(path_uid, fill_uid)
        layer_stack_utils.apply_stack_order(context)
        img_path_front = _render_with_preview_boundary()
        top_px = _px_at(img_path_front, 40.0, 290.0, dpi)
        assert top_px[2] > 150 and top_px[0] < 120, (
            f"前面へ戻したパターンカーブが塗りの上に出力されていません: {top_px}"
        )

        print("BMANGA_IMAGE_PATH_EXPORT_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    main()
