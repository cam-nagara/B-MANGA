"""Blender 実機用: 枠線プリセットのファイル往復後表示をAI目視用に出力."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Euler


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(
    os.environ.get("BMANGA_BORDER_ROUNDTRIP_VISUAL_OUT", "")
    or (ROOT / ".codex" / "visual" / "border_preset_file_roundtrip")
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_border_roundtrip_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_border_roundtrip_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _font(ImageFont, size: int):
    for path in (
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    ):
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", value).strip("_") or "preset"


def _current_page(work):
    page_id = str(getattr(bpy.context.scene, "bmanga_current_page_id", "") or "")
    for page in work.pages:
        if str(getattr(page, "id", "") or "") == page_id:
            return page
    return work.pages[int(getattr(work, "active_page_index", 0))]


def _write_preview_image(path: Path, seed: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = 240
    height = 160
    image = bpy.data.images.new(f"BManga_RoundtripPreview_{seed}", width=width, height=height, alpha=True)
    pixels = [0.0] * (width * height * 4)
    for y in range(height):
        for x in range(width):
            u = x / max(1, width - 1)
            v = y / max(1, height - 1)
            stripe = 0.10 if (x // 24 + seed) % 2 == 0 else 0.0
            off = (y * width + x) * 4
            pixels[off] = 0.92 - 0.12 * v
            pixels[off + 1] = 0.95 - 0.10 * u - stripe
            pixels[off + 2] = 0.98 - 0.12 * (seed % 3) / 3.0
            pixels[off + 3] = 1.0
    image.pixels.foreach_set(pixels)
    image.update()
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()


def _make_stale_standard_border(coma_border_object, page_id: str, coma_id: str) -> str:
    curve_name = f"{coma_border_object.COMA_BORDER_CURVE_PREFIX}{page_id}_{coma_id}"
    obj_name = f"{coma_border_object.COMA_BORDER_NAME_PREFIX}{page_id}_{coma_id}"
    curve = bpy.data.curves.new(curve_name, type="CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new(type="POLY")
    spline.points.add(4)
    for point, co in zip(
        spline.points,
        (
            (0.03, 0.05, 0.0, 1.0),
            (0.18, 0.05, 0.0, 1.0),
            (0.18, 0.24, 0.0, 1.0),
            (0.03, 0.24, 0.0, 1.0),
            (0.03, 0.05, 0.0, 1.0),
        ),
    ):
        point.co = co
    curve.bevel_depth = 0.0015
    obj = bpy.data.objects.new(obj_name, curve)
    obj[coma_border_object.PROP_COMA_BORDER_KIND] = "coma_border"
    obj[coma_border_object.PROP_COMA_BORDER_OWNER_ID] = f"{page_id}:{coma_id}"
    bpy.context.scene.collection.objects.link(obj)
    return obj.name


def _add_page_content(temp_root: Path, page, coma, index: int) -> None:
    from bmanga_dev_border_roundtrip_visual.operators import balloon_op, text_op
    from bmanga_dev_border_roundtrip_visual.utils import layer_hierarchy, paths

    page_key = layer_hierarchy.page_stack_key(page)
    coma_key = layer_hierarchy.coma_stack_key(page, coma)
    _write_preview_image(paths.coma_thumb_path(Path(bpy.context.scene.bmanga_work.work_dir), page.id, coma.coma_id), index)

    balloon = balloon_op._create_balloon_entry(
        bpy.context,
        page,
        shape="ellipse",
        x=72.0,
        y=100.0,
        w=48.0,
        h=26.0,
        parent_kind="coma",
        parent_key=coma_key,
    )
    balloon.body = ""
    text, missing = text_op._create_text_entry(
        bpy.context,
        page,
        body=f"{index + 1}",
        x_mm=130.0,
        y_mm=100.0,
        width_mm=18.0,
        height_mm=14.0,
        parent_kind="coma",
        parent_key=coma_key,
    )
    assert not missing and text is not None

    image_path = temp_root / f"visual_image_{index}.png"
    _write_preview_image(image_path, index + 10)
    image = bpy.context.scene.bmanga_image_layers.add()
    image.id = f"visual_image_{index}"
    image.title = "画像"
    image.filepath = str(image_path)
    image.x_mm = 132.0
    image.y_mm = 140.0
    image.width_mm = 30.0
    image.height_mm = 20.0
    image.parent_kind = "page"
    image.parent_key = page_key


def _prepare_camera() -> None:
    from bmanga_dev_border_roundtrip_visual.utils.geom import mm_to_m

    scene = bpy.context.scene
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            scene.render.engine = engine
            break
        except Exception:
            pass
    scene.render.resolution_x = 420
    scene.render.resolution_y = 594
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    if scene.world is not None:
        try:
            scene.world.color = (1.0, 1.0, 1.0)
        except Exception:
            pass
    camera = bpy.data.objects.get("BManga_RoundtripVisual_Camera")
    if camera is None:
        camera_data = bpy.data.cameras.new("BManga_RoundtripVisual_Camera")
        camera = bpy.data.objects.new("BManga_RoundtripVisual_Camera", camera_data)
        scene.collection.objects.link(camera)
    camera.location = (mm_to_m(105.0), mm_to_m(148.5), 1.0)
    camera.rotation_euler = Euler((0.0, 0.0, 0.0), "XYZ")
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = mm_to_m(320.0)
    scene.camera = camera
    light = bpy.data.objects.get("BManga_RoundtripVisual_Light")
    if light is None:
        light_data = bpy.data.lights.new("BManga_RoundtripVisual_Light", type="AREA")
        light = bpy.data.objects.new("BManga_RoundtripVisual_Light", light_data)
        scene.collection.objects.link(light)
    light.location = (mm_to_m(105.0), mm_to_m(148.5), 1.2)
    light.rotation_euler = Euler((0.0, 0.0, 0.0), "XYZ")
    light.data.energy = 800.0
    light.data.size = 1.2


def _make_edit_objects_renderable() -> list[tuple[bpy.types.Object, bool]]:
    from bmanga_dev_border_roundtrip_visual.utils import coma_plane, paper_bg_object, paper_guide_object

    changed: list[tuple[bpy.types.Object, bool]] = []
    for obj in bpy.data.objects:
        kind = str(obj.get(paper_guide_object.PROP_GUIDE_KIND, "") or "")
        should_render = None
        if kind in {"safe_fill", "bleed_outer_fill"}:
            should_render = False
        elif kind == paper_guide_object.GUIDE_KIND_LINES:
            should_render = False
        elif str(obj.get(paper_bg_object.PROP_BG_OWNER_ID, "") or ""):
            should_render = True
        elif str(obj.get(coma_plane.PROP_COMA_PLANE_OWNER_ID, "") or ""):
            should_render = True
        if should_render is None:
            continue
        before = bool(getattr(obj, "hide_render", False))
        changed.append((obj, before))
        obj.hide_render = not should_render
    return changed


def _view3d_context():
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type == "WINDOW":
                    space = area.spaces.active
                    rv3d = getattr(space, "region_3d", None)
                    if rv3d is not None:
                        return window, screen, area, region, space, rv3d
    raise RuntimeError("3Dビューが見つかりません")


def _has_view3d_context() -> bool:
    if bool(getattr(bpy.app, "background", False)):
        return False
    try:
        _view3d_context()
        return True
    except Exception:
        return False


def _render_viewport_page(preset_name: str, index: int) -> str:
    window, screen, area, region, space, rv3d = _view3d_context()
    path = OUT_DIR / f"{index + 1:02d}_{_safe_name(preset_name)}.png"
    scene = bpy.context.scene
    old_path = str(scene.render.filepath)
    old_res = (int(scene.render.resolution_x), int(scene.render.resolution_y))
    override = {
        "window": window,
        "screen": screen,
        "area": area,
        "region": region,
        "space_data": space,
        "region_data": rv3d,
        "scene": scene,
    }
    try:
        scene.render.filepath = str(path)
        scene.render.resolution_x = 1440
        scene.render.resolution_y = 1200
        with bpy.context.temp_override(**override):
            bpy.ops.view3d.view_axis(type="TOP", align_active=False)
            fit = bpy.ops.bmanga.view_fit_page("EXEC_DEFAULT")
            if "FINISHED" not in fit:
                raise AssertionError(f"{preset_name}: ページ表示に合わせられません: {fit}")
            if space.shading.type not in {"MATERIAL", "RENDERED"}:
                space.shading.type = "MATERIAL"
            rv3d.view_perspective = "ORTHO"
            rv3d.view_distance = max(0.01, float(rv3d.view_distance) * 0.86)
            result = bpy.ops.render.opengl("EXEC_DEFAULT", write_still=True, view_context=True)
    finally:
        scene.render.filepath = old_path
        scene.render.resolution_x, scene.render.resolution_y = old_res
    if "FINISHED" not in result:
        raise AssertionError(f"{preset_name}: 3Dビュー画像の保存に失敗しました: {result}")
    return str(path)


def _render_scene_page(preset_name: str, index: int) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _prepare_camera()
    path = OUT_DIR / f"{index + 1:02d}_{_safe_name(preset_name)}.png"
    scene = bpy.context.scene
    old_path = str(scene.render.filepath)
    changed = _make_edit_objects_renderable()
    try:
        scene.render.filepath = str(path)
        result = bpy.ops.render.render("EXEC_DEFAULT", write_still=True)
    finally:
        scene.render.filepath = old_path
        for obj, before in changed:
            if obj.name in bpy.data.objects:
                obj.hide_render = before
    if "FINISHED" not in result:
        raise AssertionError(f"{preset_name}: レンダーに失敗しました: {result}")
    return str(path)


def _render_page(preset_name: str, index: int) -> tuple[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if _has_view3d_context():
        return _render_viewport_page(preset_name, index), "3Dビュー"
    return _render_scene_page(preset_name, index), "背景レンダー"


def _assert_runtime(page, preset_name: str) -> dict[str, object]:
    from bmanga_dev_border_roundtrip_visual.utils import coma_border_object, coma_plane

    coma = page.comas[0]
    owner = f"{page.id}:{coma.coma_id}"
    border = coma.border
    border_objects = [
        obj for obj in bpy.data.objects
        if str(obj.get(coma_border_object.PROP_COMA_BORDER_OWNER_ID, "") or "") == owner
    ]
    style = str(getattr(border, "style", "solid") or "solid")
    visible = bool(getattr(border, "visible", True))
    soft_mask = False
    if style == "brush":
        if border_objects:
            raise AssertionError(f"{preset_name}: 輪郭ぼかしに標準枠線が残っています")
        plane = coma_plane.find_coma_plane_object(page.id, coma.coma_id)
        soft_mask = plane is not None and plane.data.attributes.get(coma_plane.COMA_PLANE_SOFT_MASK_ATTR) is not None
        if not soft_mask:
            raise AssertionError(f"{preset_name}: 輪郭ぼかし濃度がありません")
    elif visible and not border_objects:
        raise AssertionError(f"{preset_name}: 枠線が表示されていません")
    return {
        "preset": preset_name,
        "style": style,
        "visible": visible,
        "border_objects": len(border_objects),
        "soft_mask": soft_mask,
    }


def _make_contact_sheet(rendered: list[dict[str, object]]) -> str:
    from PIL import Image, ImageDraw, ImageFont

    font = _font(ImageFont, 22)
    small = _font(ImageFont, 16)
    cell_w = 460
    cell_h = 720
    cols = 2
    rows = (len(rendered) + cols - 1) // cols
    sheet = Image.new("RGB", (cell_w * cols, 86 + cell_h * rows), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((22, 18), "B-MANGA 枠線プリセット ファイル往復後の表示", fill=(0, 0, 0), font=font)
    draw.text((22, 48), "各画像は 作品一覧→ページ→コマ→ページ→作品一覧→ページ の後に生成", fill=(40, 40, 40), font=small)
    for i, item in enumerate(rendered):
        x = (i % cols) * cell_w
        y = 86 + (i // cols) * cell_h
        image = Image.open(str(item["render"])).convert("RGB")
        image.thumbnail((390, 560))
        draw.text((x + 18, y + 12), str(item["preset"]), fill=(0, 0, 0), font=font)
        draw.text(
            (x + 18, y + 42),
            f"style={item['style']} visible={item['visible']} soft={item['soft_mask']} / {item['visual_mode']}",
            fill=(60, 60, 60),
            font=small,
        )
        sheet.paste(image, (x + 35, y + 80))
    out = OUT_DIR / "border_preset_file_roundtrip_contact.png"
    sheet.save(out)
    return str(out)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_border_roundtrip_visual_"))
    mod = None
    rendered: list[dict[str, object]] = []
    success = False
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "BorderRoundtripVisual.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")

        from bmanga_dev_border_roundtrip_visual.io import border_presets
        from bmanga_dev_border_roundtrip_visual.utils import coma_border_object, layer_object_sync

        work = bpy.context.scene.bmanga_work
        presets = border_presets.list_global_presets()
        while len(work.pages) < len(presets):
            assert "FINISHED" in bpy.ops.bmanga.page_add("EXEC_DEFAULT")
        for index, preset in enumerate(presets):
            page = work.pages[index]
            coma = page.comas[0]
            coma.title = preset.name
            coma.shape_type = "rect"
            coma.rect_x_mm = 30.0
            coma.rect_y_mm = 55.0
            coma.rect_width_mm = 150.0
            coma.rect_height_mm = 185.0
            border_presets.apply_preset_to_coma(preset, coma)
            if bool(getattr(coma.border, "visible", True)):
                # AI目視用に枠線色だけを強調する。スタイル/太さ/ぼかし/表示有無は
                # プリセット値のままなので、標準枠線への巻き戻りは検出できる。
                coma.border.color = (0.0, 1.0, 1.0, 1.0)
            _add_page_content(temp_root, page, coma, index)
        assert "FINISHED" in bpy.ops.bmanga.work_save("EXEC_DEFAULT")

        for index, preset in enumerate(presets):
            result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=index)
            if "FINISHED" not in result:
                raise AssertionError(f"{preset.name}: ページを開けません: {result}")
            work = bpy.context.scene.bmanga_work
            page = _current_page(work)
            coma = page.comas[0]
            if str(getattr(coma.border, "style", "") or "") == "brush":
                stale_name = _make_stale_standard_border(coma_border_object, page.id, coma.coma_id)
                assert bpy.data.objects.get(stale_name) is not None
                assert "FINISHED" in bpy.ops.wm.save_as_mainfile(filepath=str(bpy.data.filepath))
                assert "FINISHED" in bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
                assert "FINISHED" in bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=index)
                if bpy.data.objects.get(stale_name) is not None:
                    raise AssertionError(f"{preset.name}: 古い標準枠線が再表示されています")
                work = bpy.context.scene.bmanga_work
                page = _current_page(work)
            work.active_page_index = index
            page.active_coma_index = 0
            assert "FINISHED" in bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT")
            assert "FINISHED" in bpy.ops.bmanga.exit_coma_mode("EXEC_DEFAULT")
            work = bpy.context.scene.bmanga_work
            page = _current_page(work)
            layer_object_sync.mirror_work_to_outliner(bpy.context.scene, work)
            state = _assert_runtime(page, preset.name)
            render_path, visual_mode = _render_page(preset.name, index)
            state["render"] = render_path
            state["visual_mode"] = visual_mode
            rendered.append(state)
            assert "FINISHED" in bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        contact = _make_contact_sheet(rendered)
        report_path = OUT_DIR / "border_preset_file_roundtrip_visual.json"
        report_path.write_text(json.dumps({"items": rendered, "contact_sheet": contact}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"BMANGA_BORDER_PRESET_FILE_ROUNDTRIP_VISUAL_OK visual={contact}", flush=True)
        success = True
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)
        if success and not bool(getattr(bpy.app, "background", False)):
            bpy.app.timers.register(lambda: (bpy.ops.wm.quit_blender(), None)[1], first_interval=0.5)


if __name__ == "__main__":
    main()
