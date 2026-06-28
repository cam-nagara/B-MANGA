"""用紙ガイド線が不透明かつ画面上 1px 相当の太さになることを確認."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_paper_guide_visibility",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_paper_guide_visibility"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _guide_objects(paper_guide_object, page):
    page_id = str(getattr(page, "id", "") or "")
    return [
        obj
        for obj in bpy.data.objects
        if str(obj.get(paper_guide_object.PROP_GUIDE_OWNER_ID, "") or "") == page_id
        and str(obj.get(paper_guide_object.PROP_GUIDE_KIND, "") or "") == paper_guide_object.GUIDE_KIND_LINES
        and obj.type == "CURVE"
    ]


def _curve_spline_count(obj) -> int:
    return len(getattr(getattr(obj, "data", None), "splines", []) or [])


def _assert_guide_materials_are_opaque(guide_objects) -> None:
    for guide_obj in guide_objects:
        materials = list(getattr(guide_obj.data, "materials", []) or [])
        if not materials:
            raise AssertionError(f"用紙ガイド線の素材がありません: {guide_obj.name}")
        for mat in materials:
            alpha = float(mat.diffuse_color[3])
            if abs(alpha - 1.0) > 1.0e-6:
                raise AssertionError(f"用紙ガイド線の素材が不透明ではありません: {mat.name} alpha={alpha}")
            if str(getattr(mat, "blend_method", "")) == "BLEND":
                raise AssertionError(f"用紙ガイド線の素材が半透明表示方式です: {mat.name}")
            if str(getattr(mat, "surface_render_method", "")) == "BLENDED":
                raise AssertionError(f"用紙ガイド線の素材が半透明表示方式です: {mat.name}")


def _assert_stable_viewport_order(guide_objects, safe_fill, bleed_outer_fill) -> None:
    if bool(getattr(safe_fill, "show_in_front", False)):
        raise AssertionError(f"セーフライン外塗りが最前面ワイヤ表示に依存しています: {safe_fill.name}")
    if bool(getattr(bleed_outer_fill, "show_in_front", False)):
        raise AssertionError(f"裁ち落とし枠外塗りが最前面ワイヤ表示に依存しています: {bleed_outer_fill.name}")
    if not (float(bleed_outer_fill.location.z) > float(safe_fill.location.z)):
        raise AssertionError("裁ち落とし枠外塗りがセーフライン外塗りより奥にあります")
    for obj in guide_objects:
        if bool(getattr(obj, "show_in_front", False)):
            raise AssertionError(f"用紙ガイド線が最前面ワイヤ表示に依存しています: {obj.name}")
        if bool(getattr(obj, "show_transparent", False)):
            raise AssertionError(f"用紙ガイド線が透明表示になっています: {obj.name}")
        if float(obj.location.z) <= max(float(safe_fill.location.z), float(bleed_outer_fill.location.z)):
            raise AssertionError(f"用紙ガイド線が塗りより奥にあります: {obj.name}")


def _assert_page_preview_is_behind_guides(
    page_preview_object,
    work_info_text_object,
    page,
    guide_objects,
    safe_fill,
    bleed_outer_fill,
) -> None:
    preview = bpy.data.objects.get(f"{page_preview_object.PREVIEW_OBJECT_PREFIX}{page.id}")
    if preview is None:
        raise AssertionError("ページ一覧のプレビュー画像が作られていません")
    preview_z = float(preview.location.z)
    if preview_z <= 0.0:
        raise AssertionError(f"ページ一覧のプレビュー画像が用紙背景より奥にあります: {preview_z}")
    front_fill_z = min(float(safe_fill.location.z), float(bleed_outer_fill.location.z))
    if not (preview_z < front_fill_z):
        raise AssertionError(
            f"ページ一覧のプレビュー画像が塗りより手前にあります: preview={preview_z}, fill={front_fill_z}"
        )
    guide_z = min(float(obj.location.z) for obj in guide_objects)
    if not (preview_z < guide_z):
        raise AssertionError(
            f"ページ一覧のプレビュー画像が用紙ガイド線より手前にあります: preview={preview_z}, guide={guide_z}"
        )
    info_objects = [
        obj
        for obj in bpy.data.objects
        if obj.get(work_info_text_object.PROP_WORK_INFO_KIND) == "work_info_text"
    ]
    if not info_objects:
        raise AssertionError("作品情報テキストが作られていません")
    for obj in info_objects:
        if not (preview_z < float(obj.location.z)):
            raise AssertionError(
                f"ページ一覧のプレビュー画像が作品情報より手前にあります: {obj.name} preview={preview_z}, info={obj.location.z}"
            )


def _mix_shader_alpha(obj) -> float:
    mat = next((mat for mat in getattr(obj.data, "materials", []) or [] if mat is not None), None)
    if mat is None:
        raise AssertionError(f"塗り素材がありません: {obj.name}")
    nt = getattr(mat, "node_tree", None)
    if nt is not None:
        for node in nt.nodes:
            if getattr(node, "bl_idname", "") == "ShaderNodeMixShader":
                return float(node.inputs["Fac"].default_value)
            if getattr(node, "bl_idname", "") == "ShaderNodeBsdfPrincipled" and "Alpha" in node.inputs:
                return float(node.inputs["Alpha"].default_value)
    return float(mat.diffuse_color[3])


def _mesh_bounds_mm(obj) -> tuple[float, float, float, float]:
    xs = [float(vertex.co.x) * 1000.0 for vertex in obj.data.vertices]
    ys = [float(vertex.co.y) * 1000.0 for vertex in obj.data.vertices]
    if not xs or not ys:
        raise AssertionError(f"塗りメッシュに頂点がありません: {obj.name}")
    return min(xs), max(xs), min(ys), max(ys)


def _assert_close(actual: float, expected: float, label: str, eps: float = 0.01) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: actual={actual:.4f} expected={expected:.4f}")


def _assert_fill_meshes_do_not_overlap(work, safe_fill, bleed_outer_fill) -> None:
    from bmanga_dev_paper_guide_visibility.ui import overlay_shared

    rects = overlay_shared.compute_paper_rects(work.paper, is_left_half=False)
    sx1, sx2, sy1, sy2 = _mesh_bounds_mm(safe_fill)
    bx1, bx2, by1, by2 = _mesh_bounds_mm(bleed_outer_fill)
    _assert_close(sx1, rects.bleed.x, "セーフライン外塗りの外側左端")
    _assert_close(sx2, rects.bleed.x2, "セーフライン外塗りの外側右端")
    _assert_close(sy1, rects.bleed.y, "セーフライン外塗りの外側下端")
    _assert_close(sy2, rects.bleed.y2, "セーフライン外塗りの外側上端")
    _assert_close(bx1, rects.canvas.x, "裁ち落とし枠外塗りの外側左端")
    _assert_close(bx2, rects.canvas.x2, "裁ち落とし枠外塗りの外側右端")
    _assert_close(by1, rects.canvas.y, "裁ち落とし枠外塗りの外側下端")
    _assert_close(by2, rects.canvas.y2, "裁ち落とし枠外塗りの外側上端")


def _assert_fill_settings_update_immediately(paper_guide_object, work, page) -> None:
    overlay = work.safe_area_overlay
    overlay.enabled = True
    overlay.bleed_outer_enabled = True
    overlay.opacity = 42.0
    overlay.bleed_outer_opacity = 73.0
    safe_fill = bpy.data.objects.get(f"{paper_guide_object.PAPER_SAFE_FILL_PREFIX}{page.id}")
    bleed_outer_fill = bpy.data.objects.get(f"{paper_guide_object.PAPER_BLEED_OUTER_FILL_PREFIX}{page.id}")
    if safe_fill is None or bleed_outer_fill is None:
        raise AssertionError("塗り設定変更後の実体がありません")
    if abs(float(safe_fill.color[3]) - 0.42) > 1.0e-6:
        raise AssertionError(f"セーフライン外塗りの不透明度が即時反映されていません: {safe_fill.color[3]}")
    if abs(_mix_shader_alpha(safe_fill) - 0.42) > 1.0e-6:
        raise AssertionError("セーフライン外塗りの素材が即時更新されていません")
    if abs(float(bleed_outer_fill.color[3]) - 0.73) > 1.0e-6:
        raise AssertionError(f"裁ち落とし枠外塗りの不透明度が即時反映されていません: {bleed_outer_fill.color[3]}")
    if abs(_mix_shader_alpha(bleed_outer_fill) - 0.73) > 1.0e-6:
        raise AssertionError("裁ち落とし枠外塗りの素材が即時更新されていません")
    _assert_fill_meshes_do_not_overlap(work, safe_fill, bleed_outer_fill)

    overlay.opacity = 0.0
    safe_fill = bpy.data.objects.get(f"{paper_guide_object.PAPER_SAFE_FILL_PREFIX}{page.id}")
    if safe_fill is None or not bool(safe_fill.hide_viewport):
        raise AssertionError("不透明度0への変更直後にセーフライン外塗りが非表示になっていません")
    overlay.opacity = 31.0
    safe_fill = bpy.data.objects.get(f"{paper_guide_object.PAPER_SAFE_FILL_PREFIX}{page.id}")
    if safe_fill is None or bool(safe_fill.hide_viewport):
        raise AssertionError("不透明度を戻した直後にセーフライン外塗りが再表示されていません")


def _assert_guides_above_coma_planes(guide_objects, safe_fill, bleed_outer_fill, page, coma_z_order) -> None:
    if len(getattr(page, "comas", []) or []) == 0:
        return
    # コマ面だけでなく、最も手前に来るコマ要素 (コマ枠線・白フチ) より確実に前面であることを確認する。
    # ここをコマ面 (plane_z) だけで判定すると、重なり順の深いコマで枠線・白フチがガイド線と
    # 同一深度になり点滅・隠れが起きる不具合を検出できない。
    coplanar_eps = 5.0e-4  # 0.5mm 未満は同一深度 (Z 競合) とみなす
    highest_coma_z = max(
        max(
            float(coma_z_order.plane_z(coma)),
            float(coma_z_order.white_margin_z(coma)),
            float(coma_z_order.border_z(coma)),
        )
        for coma in page.comas
    )
    if not (float(safe_fill.location.z) > highest_coma_z + coplanar_eps):
        raise AssertionError(
            f"セーフライン外塗りが最も手前のコマ要素と同一深度かそれより奥にあります: "
            f"fill={safe_fill.location.z}, coma_top={highest_coma_z}"
        )
    if not (float(bleed_outer_fill.location.z) > highest_coma_z + coplanar_eps):
        raise AssertionError(
            f"裁ち落とし枠外塗りが最も手前のコマ要素と同一深度かそれより奥にあります: "
            f"fill={bleed_outer_fill.location.z}, coma_top={highest_coma_z}"
        )
    for obj in guide_objects:
        if not (float(obj.location.z) > highest_coma_z + coplanar_eps):
            raise AssertionError(
                f"用紙ガイド線が最も手前のコマ要素と同一深度かそれより奥にあります "
                f"(枠線と重なって点滅/非表示になる): guide={obj.location.z}, coma_top={highest_coma_z}"
            )


def _assert_constant_thickness(paper_guide_object, guide_objects) -> None:
    if abs(float(paper_guide_object.GUIDE_SCREEN_PX) - 1.0) > 1.0e-6:
        raise AssertionError(f"用紙ガイド線の画面太さが1pxではありません: {paper_guide_object.GUIDE_SCREEN_PX}")

    original_region = paper_guide_object._active_view3d_region
    original_mpp = paper_guide_object._meters_per_pixel
    try:
        mpp = 0.002
        paper_guide_object._last_mpp = -1.0
        paper_guide_object._active_view3d_region = lambda: (object(), object())
        paper_guide_object._meters_per_pixel = lambda _region, _rv3d: mpp
        paper_guide_object.apply_view_constant_thickness()
        expected_radius = paper_guide_object._guide_half_width_m(
            mpp,
            paper_guide_object._GUIDE_CURVE_RADIUS_SCALE,
        )
        for obj in guide_objects:
            radius = float(getattr(obj.data, "bevel_depth", 0.0) or 0.0)
            if abs(radius - expected_radius) > 1.0e-9:
                raise AssertionError(f"用紙ガイド線の太さ上限が反映されていません: {radius} != {expected_radius}")
            max_radius = paper_guide_object.mm_to_m(paper_guide_object.GUIDE_MAX_WIDTH_MM) * 0.5
            if radius > max_radius + 1.0e-9:
                raise AssertionError(f"用紙ガイド線が太くなりすぎています: {radius} > {max_radius}")
    finally:
        paper_guide_object._active_view3d_region = original_region
        paper_guide_object._meters_per_pixel = original_mpp
        paper_guide_object._last_mpp = -1.0


def _assert_timer_does_not_touch_closed_panel(paper_guide_object, guide_objects) -> None:
    sample = next((obj for obj in guide_objects if _curve_spline_count(obj) > 0), None)
    if sample is None:
        raise AssertionError("用紙ガイド線の太さ監視確認対象がありません")
    original_allowed = paper_guide_object._live_guide_updates_allowed
    original_depth = float(sample.data.bevel_depth)
    manual_depth = original_depth * 3.0 + 0.001
    try:
        sample.data.bevel_depth = manual_depth
        paper_guide_object._live_guide_updates_allowed = lambda: False
        paper_guide_object._last_mpp = -1.0
        paper_guide_object._thickness_timer()
        if abs(float(sample.data.bevel_depth) - manual_depth) > 1.0e-9:
            raise AssertionError("B-MANGAタブ非表示扱いでも用紙ガイド線の太さが戻されています")
    finally:
        sample.data.bevel_depth = original_depth
        paper_guide_object._live_guide_updates_allowed = original_allowed
        paper_guide_object._last_mpp = -1.0


def _assert_timer_idles_when_view_is_stable(paper_guide_object) -> None:
    original_allowed = paper_guide_object._live_guide_updates_allowed
    original_region = paper_guide_object._active_view3d_region
    original_mpp = paper_guide_object._meters_per_pixel
    original_repair = paper_guide_object.repair_loaded_work_paper_guides
    try:
        paper_guide_object._live_guide_updates_allowed = lambda: True
        paper_guide_object._active_view3d_region = lambda: (object(), object())
        paper_guide_object._meters_per_pixel = lambda _region, _rv3d: 0.00001
        paper_guide_object.repair_loaded_work_paper_guides = lambda *args, **kwargs: False
        paper_guide_object._last_mpp = -1.0
        paper_guide_object._last_repair_time = time.monotonic()
        first_interval = paper_guide_object._thickness_timer()
        second_interval = paper_guide_object._thickness_timer()
        if abs(float(first_interval) - float(paper_guide_object._GUIDE_THICKNESS_INTERVAL)) > 1.0e-9:
            raise AssertionError("用紙ガイド線の太さ変更直後に短い確認間隔へ入りません")
        if abs(float(second_interval) - float(paper_guide_object._GUIDE_IDLE_INTERVAL)) > 1.0e-9:
            raise AssertionError("用紙ガイド線の太さが安定していても短い間隔で監視し続けています")
    finally:
        paper_guide_object._live_guide_updates_allowed = original_allowed
        paper_guide_object._active_view3d_region = original_region
        paper_guide_object._meters_per_pixel = original_mpp
        paper_guide_object.repair_loaded_work_paper_guides = original_repair
        paper_guide_object._last_mpp = -1.0


def _assert_repair_check_does_not_create_materials(paper_guide_object, scene, work) -> None:
    original_materials = paper_guide_object._paper_guide_materials
    try:
        def _fail_materials():
            raise AssertionError("修復が必要か見るだけの処理で用紙ガイド線の素材を作っています")

        paper_guide_object._paper_guide_materials = _fail_materials
        if paper_guide_object.repair_loaded_work_paper_guides(scene, work):
            raise AssertionError("用紙ガイド線の修復が不要な状態で再実行されています")
    finally:
        paper_guide_object._paper_guide_materials = original_materials


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_paper_guide_visibility_"))
    mod = None
    try:
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PaperGuideVisibility.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        if "FINISHED" not in result:
            raise AssertionError(f"ページファイルを開けません: {result}")

        from bmanga_dev_paper_guide_visibility.core.work import get_work
        from bmanga_dev_paper_guide_visibility.utils import (
            coma_z_order,
            page_preview_object,
            paper_guide_object,
            work_info_text_object,
        )

        scene = bpy.context.scene
        work = get_work(bpy.context)
        if work is None or not work.loaded:
            raise AssertionError("作品データが読み込まれていません")
        page = work.pages[0]
        work.paper.coma_border_width_mm = 0.73
        if not getattr(page, "comas", None):
            raise AssertionError("コマ枠線幅の反映確認用コマがありません")
        for coma in page.comas:
            if abs(float(coma.border.width_mm) - 0.73) > 1.0e-6:
                raise AssertionError(f"用紙セクションのコマ枠線幅がコマへ反映されていません: {coma.border.width_mm}")
        guide_objects = _guide_objects(paper_guide_object, page)
        if not guide_objects:
            raise AssertionError("用紙ガイド線の実体がありません")
        if len(guide_objects) != 1:
            raise AssertionError(f"用紙ガイド線はページごとに1オブジェクトである必要があります: {guide_objects}")
        if not any(_curve_spline_count(obj) > 0 for obj in guide_objects):
            raise AssertionError("用紙ガイド線が作られていません")
        safe_fill = bpy.data.objects.get(f"{paper_guide_object.PAPER_SAFE_FILL_PREFIX}{page.id}")
        if safe_fill is None:
            raise AssertionError("セーフライン外塗りが作られていません")
        bleed_outer_fill = bpy.data.objects.get(f"{paper_guide_object.PAPER_BLEED_OUTER_FILL_PREFIX}{page.id}")
        if bleed_outer_fill is None:
            raise AssertionError("裁ち落とし枠外塗りが作られていません")
        page_preview_object.sync_page_previews(bpy.context, work, force=True)
        work_info_text_object.regenerate_all_work_info_texts(scene, work)

        _assert_guide_materials_are_opaque(guide_objects)
        _assert_stable_viewport_order(guide_objects, safe_fill, bleed_outer_fill)
        _assert_page_preview_is_behind_guides(
            page_preview_object,
            work_info_text_object,
            page,
            guide_objects,
            safe_fill,
            bleed_outer_fill,
        )
        _assert_fill_settings_update_immediately(paper_guide_object, work, page)
        _assert_guides_above_coma_planes(guide_objects, safe_fill, bleed_outer_fill, page, coma_z_order)
        _assert_constant_thickness(paper_guide_object, guide_objects)
        _assert_timer_does_not_touch_closed_panel(paper_guide_object, guide_objects)
        _assert_timer_idles_when_view_is_stable(paper_guide_object)
        _assert_repair_check_does_not_create_materials(paper_guide_object, scene, work)
        if paper_guide_object.repair_loaded_work_paper_guides(scene, work):
            raise AssertionError("用紙ガイド線の修復が不要な状態で再実行されています")

        # 重なり順の深いコマ (z_order=3) では旧実装で白フチがガイド線と同一深度 (23mm) になり、
        # 点滅・非表示が起きていた。ガイド線 z がコマの重なり順へ追従することを確認する。
        page.comas[0].z_order = 3
        paper_guide_object.regenerate_all_paper_guides(scene, work)
        guide_objects = _guide_objects(paper_guide_object, page)
        safe_fill = bpy.data.objects.get(f"{paper_guide_object.PAPER_SAFE_FILL_PREFIX}{page.id}")
        bleed_outer_fill = bpy.data.objects.get(f"{paper_guide_object.PAPER_BLEED_OUTER_FILL_PREFIX}{page.id}")
        if not guide_objects or safe_fill is None or bleed_outer_fill is None:
            raise AssertionError("重なり順変更後の用紙ガイド線/塗りがありません")
        _assert_guides_above_coma_planes(guide_objects, safe_fill, bleed_outer_fill, page, coma_z_order)

        print("BMANGA_PAPER_GUIDE_VISIBILITY_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
        os._exit(0)
    except Exception:
        import traceback

        traceback.print_exc()
        os._exit(1)
