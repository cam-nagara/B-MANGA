"""Blender実機用: ツール挙動/右クリック/ショートカット/選択編集のAI目視監査."""

from __future__ import annotations

import importlib.util
import html
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Quaternion


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(
    os.environ.get("BMANGA_TOOL_VISUAL_OUT", "")
    or tempfile.mkdtemp(prefix="bmanga_tool_visual_audit_")
).resolve()


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_tool_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_tool_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _view3d_context():
    wm = bpy.context.window_manager
    for window in wm.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type == "WINDOW":
                    return window, screen, area, region, area.spaces.active.region_3d
    raise RuntimeError("VIEW_3D が見つかりません")


def _view3d_override():
    window, screen, area, region, _rv3d = _view3d_context()
    return bpy.context.temp_override(window=window, screen=screen, area=area, region=region)


def _screen_event_for_world(x_mm: float, y_mm: float, *, event_type: str = "LEFTMOUSE", value: str = "PRESS"):
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from bmanga_dev_tool_visual.operators import coma_picker
    from bmanga_dev_tool_visual.utils.geom import mm_to_m

    _window, _screen, _area, region, rv3d = _view3d_context()
    point = location_3d_to_region_2d(region, rv3d, (mm_to_m(x_mm), mm_to_m(y_mm), 0.0))
    if point is None:
        raise AssertionError(f"画面座標に変換できません: {x_mm}, {y_mm}")

    def make_event(px: float, py: float, *, final: bool = False):
        mouse_x = region.x + (round(float(px)) if final else float(px))
        mouse_y = region.y + (round(float(py)) if final else float(py))
        return SimpleNamespace(
            type=event_type,
            value=value,
            mouse_x=int(mouse_x) if final else mouse_x,
            mouse_y=int(mouse_y) if final else mouse_y,
            ctrl=False,
            shift=False,
            alt=False,
        )

    def world_at(px: float, py: float) -> tuple[float, float] | None:
        return coma_picker._event_world_mm(bpy.context, make_event(px, py))

    px = float(point.x)
    py = float(point.y)
    for _ in range(10):
        world = world_at(px, py)
        if world is None:
            break
        err_x = float(x_mm) - float(world[0])
        err_y = float(y_mm) - float(world[1])
        if abs(err_x) + abs(err_y) <= 0.05:
            break
        world_x = world_at(px + 1.0, py)
        world_y = world_at(px, py + 1.0)
        if world_x is None or world_y is None:
            break
        j00 = float(world_x[0]) - float(world[0])
        j10 = float(world_x[1]) - float(world[1])
        j01 = float(world_y[0]) - float(world[0])
        j11 = float(world_y[1]) - float(world[1])
        det = j00 * j11 - j01 * j10
        if abs(det) < 1.0e-9:
            break
        step_x = (err_x * j11 - j01 * err_y) / det
        step_y = (j00 * err_y - err_x * j10) / det
        step_x = max(-250.0, min(250.0, step_x))
        step_y = max(-250.0, min(250.0, step_y))
        px += step_x
        py += step_y

    best = None
    center_x = int(round(px))
    center_y = int(round(py))
    for ix in range(center_x - 3, center_x + 4):
        for iy in range(center_y - 3, center_y + 4):
            world = world_at(float(ix), float(iy))
            if world is None:
                continue
            score = abs(float(x_mm) - float(world[0])) + abs(float(y_mm) - float(world[1]))
            if best is None or score < best[0]:
                best = (score, ix, iy)
    if best is not None:
        _score, px, py = best
    return SimpleNamespace(
        type=event_type,
        value=value,
        mouse_x=int(region.x + round(float(px))),
        mouse_y=int(region.y + round(float(py))),
        ctrl=False,
        shift=False,
        alt=False,
    )


def _screenshot(name: str) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=4)
    except Exception:
        pass
    scene = bpy.context.scene
    old_filepath = scene.render.filepath
    scene.render.filepath = str(path)
    try:
        with _view3d_override():
            result = bpy.ops.render.opengl("EXEC_DEFAULT", view_context=True, write_still=True)
    finally:
        scene.render.filepath = old_filepath
    if "FINISHED" not in result:
        raise RuntimeError(f"viewport capture failed: {result}")
    return str(path)


def _dismiss_startup_splash() -> None:
    try:
        bpy.context.preferences.view.show_splash = False
    except Exception:
        pass
    window = getattr(bpy.context, "window", None)
    simulate = getattr(window, "event_simulate", None)
    if simulate is not None:
        try:
            simulate(type="ESC", value="PRESS")
            simulate(type="ESC", value="RELEASE")
            _window, _screen, _area, region, _rv3d = _view3d_context()
            click_x = int(region.x) + 24
            click_y = int(region.y) + 24
            simulate(type="LEFTMOUSE", value="PRESS", x=click_x, y=click_y)
            simulate(type="LEFTMOUSE", value="RELEASE", x=click_x, y=click_y)
        except Exception:
            pass
    if os.name == "nt":
        try:
            import ctypes

            vk_escape = 0x1B
            keyeventf_keyup = 0x0002
            ctypes.windll.user32.keybd_event(vk_escape, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk_escape, 0, keyeventf_keyup, 0)
        except Exception:
            pass
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=4)
    except Exception:
        pass


def _create_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = 180
    height = 120
    pixels: list[float] = []
    for y in range(height):
        for x in range(width):
            rgba = (0.92, 0.92, 0.92, 1.0)
            if x < 5 or x >= width - 5 or y < 5 or y >= height - 5:
                rgba = (0.08, 0.08, 0.08, 1.0)
            elif 18 <= x <= 78 and 20 <= y <= 62:
                rgba = (0.70, 0.86, 1.0, 1.0)
            elif 100 <= x <= 160 and 20 <= y <= 62:
                rgba = (1.0, 0.82, 0.58, 1.0)
            elif 28 <= x <= 150 and 80 <= y <= 92:
                rgba = (0.12, 0.12, 0.12, 1.0)
            pixels.extend(rgba)
    image = bpy.data.images.new("B-MANGA監査画像", width=width, height=height, alpha=True)
    image.pixels.foreach_set(pixels)
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)


def _make_contact_sheet(items: list[dict], summary: dict) -> str:
    width = 700
    row_h = 430
    header_h = 190
    height = header_h + row_h * len(items)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]

    def text(x: int, y: int, value: str, size: int = 14, weight: str = "400", fill: str = "#222") -> None:
        escaped = html.escape(str(value), quote=False)
        lines.append(
            f'<text x="{x}" y="{y}" font-family="Yu Gothic, Meiryo, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="{fill}">{escaped}</text>'
        )

    text(22, 38, "B-MANGA ツール挙動 / 右クリック / ショートカット AI目視監査", 22, "700", "#000")
    text(22, 68, f"ショートカットOK: {summary['shortcut_ok']} / 右クリックOK: {summary['menu_ok']} / 選択編集OK: {summary['selection_edit_ok']}", 16, "500", "#000")
    y = 88
    for line in summary["shortcut_lines"][:5]:
        text(22, y, line, 14, "400", "#333")
        y += 20
    y = header_h
    for item in items:
        lines.append(f'<rect x="12" y="{y - 28}" width="{width - 24}" height="{row_h - 10}" fill="#f7faf7" stroke="#c9d2c9"/>')
        text(22, y, f"{item['label']}  result={item['result']}", 16, "500", "#000")
        text(22, y + 22, item["note"], 14, "400", "#333")
        href = html.escape(Path(item["screenshot"]).name, quote=True)
        lines.append(f'<image x="22" y="{y + 52}" width="620" height="360" href="{href}" preserveAspectRatio="xMidYMid meet"/>')
        y += row_h
    lines.append("</svg>")
    path = OUT_DIR / "tool_behavior_visual_contact_sheet.svg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _add_gp_layer(context, parent_key: str):
    from bmanga_dev_tool_visual.utils import gp_object_layer, layer_object_model
    from bmanga_dev_tool_visual.utils import gpencil as gp_utils
    from bmanga_dev_tool_visual.utils.geom import mm_to_m

    obj = gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bmanga_id=layer_object_model.make_stable_id("gp"),
        title="visual_gp",
        z_index=210,
        parent_kind="coma" if ":" in parent_key else "page",
        parent_key=parent_key,
    )
    layer = layer_object_model.content_layer(obj)
    assert layer is not None
    frame = gp_utils.ensure_active_frame(layer)
    assert frame is not None and getattr(frame, "drawing", None) is not None
    assert gp_utils.add_stroke_to_drawing(
        frame.drawing,
        [
            (mm_to_m(42.0), mm_to_m(70.0), 0.0),
            (mm_to_m(62.0), mm_to_m(84.0), 0.0),
            (mm_to_m(78.0), mm_to_m(66.0), 0.0),
        ],
    )
    return layer


def _setup_scene(temp_root: Path):
    mod = _load_addon()
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ToolVisualAudit.bmanga"))
    assert "FINISHED" in result, result
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
    assert "FINISHED" in result, result

    from bmanga_dev_tool_visual.operators import effect_line_op, raster_layer_op
    from bmanga_dev_tool_visual.utils import layer_hierarchy, layer_stack as layer_stack_utils
    from bmanga_dev_tool_visual.utils.geom import mm_to_px

    context = bpy.context
    work = context.scene.bmanga_work
    work.paper.canvas_width_mm = 210.0
    work.paper.canvas_height_mm = 297.0
    page = work.pages[0]
    panel = page.comas[0]
    panel.shape_type = "rect"
    panel.rect_x_mm = 22.0
    panel.rect_y_mm = 44.0
    panel.rect_width_mm = 168.0
    panel.rect_height_mm = 206.0
    parent_key = layer_hierarchy.coma_stack_key(page, panel)

    gp_layer = _add_gp_layer(context, parent_key)
    effect_obj, effect_layer = effect_line_op._create_effect_layer(
        context,
        (96.0, 64.0, 36.0, 26.0),
        parent_key=parent_key,
    )
    _ = effect_obj
    raster_result = bpy.ops.bmanga.raster_layer_add("EXEC_DEFAULT", dpi=30, bit_depth="gray8", enter_paint=False)
    assert "FINISHED" in raster_result, raster_result
    raster = context.scene.bmanga_raster_layers[context.scene.bmanga_active_raster_layer_index]
    raster.parent_kind = "coma"
    raster.parent_key = parent_key
    raster_image = raster_layer_op.ensure_raster_image(context, raster, create_missing=True)
    assert raster_image is not None
    rx = int(round(mm_to_px(36.0, int(raster.dpi))))
    ry = int(round(mm_to_px(232.0, int(raster.dpi))))
    width, height = int(raster_image.size[0]), int(raster_image.size[1])
    pixels = list(raster_image.pixels[:])
    # The UI test converts through integer screen pixels; at 300 dpi one
    # viewport pixel can move the sampled raster position by more than 10 px.
    for yy in range(max(0, ry - 24), min(height, ry + 25)):
        for xx in range(max(0, rx - 24), min(width, rx + 25)):
            offset = (yy * width + xx) * 4
            pixels[offset:offset + 4] = [0.0, 0.0, 0.0, 1.0]
    raster_image.pixels[:] = pixels
    raster_image.update()

    image_path = temp_root / "visual_image.png"
    _create_png(image_path)
    image = context.scene.bmanga_image_layers.add()
    image.id = "visual_image"
    image.title = "画像"
    image.filepath = str(image_path)
    image.x_mm = 138.0
    image.y_mm = 60.0
    image.width_mm = 34.0
    image.height_mm = 22.0
    image.parent_kind = "coma"
    image.parent_key = parent_key

    balloon = page.balloons.add()
    balloon.id = "visual_balloon"
    balloon.shape = "ellipse"
    balloon.x_mm = 54.0
    balloon.y_mm = 130.0
    balloon.width_mm = 44.0
    balloon.height_mm = 28.0
    balloon.parent_kind = "coma"
    balloon.parent_key = parent_key

    text = page.texts.add()
    text.id = "visual_text"
    text.body = "右クリック"
    text.x_mm = 122.0
    text.y_mm = 132.0
    text.width_mm = 40.0
    text.height_mm = 28.0
    text.parent_kind = "coma"
    text.parent_key = parent_key

    layer_stack_utils.sync_layer_stack_after_data_change(context)
    return mod, {
        "work": work,
        "page": page,
        "panel": panel,
        "gp": gp_layer,
        "effect": effect_layer,
        "raster": raster,
        "image": image,
        "balloon": balloon,
        "text": text,
    }


def _assert_shortcuts() -> tuple[bool, list[str]]:
    from bmanga_dev_tool_visual.keymap import keymap as keymap_mod
    from bmanga_dev_tool_visual.utils import shortcut_visibility

    state = keymap_mod.get_state()
    assert state is not None
    # The page-file transition may leave stale SpaceView3D wrappers for one UI
    # tick.  Touching show_region_ui here crashes Blender 5.1, while shortcut
    # visibility only requires the panel-drawn marker.
    shortcut_visibility.mark_bmanga_panel_drawn(bpy.context)
    try:
        keymap_mod._apply_visibility_state(state, True)
    except Exception:
        pass
    actual = [
        (
            str(getattr(kmi, "idname", "")),
            str(getattr(kmi, "type", "")),
            bool(getattr(kmi, "shift", False)),
            bool(getattr(kmi, "ctrl", False)),
            bool(getattr(kmi, "alt", False)),
            bool(getattr(kmi, "active", False)),
        )
        for kmi in getattr(state, "bmanga_items", []) or []
    ]
    window_actual = []
    for km in getattr(state, "bmanga_keymaps", []) or []:
        if str(getattr(km, "name", "") or "") != "Window":
            continue
        for kmi in getattr(km, "keymap_items", []) or []:
            window_actual.append(
                (
                    str(getattr(kmi, "idname", "")),
                    str(getattr(kmi, "type", "")),
                    bool(getattr(kmi, "shift", False)),
                    bool(getattr(kmi, "ctrl", False)),
                    bool(getattr(kmi, "alt", False)),
                    bool(getattr(kmi, "active", False)),
                )
            )
    expected = [
        ("オブジェクトツール", "bmanga.set_mode_object", "O", False, False, False),
        ("描画ツール", "bmanga.set_mode_draw", "P", False, False, False),
        ("枠線カットツール", "bmanga.coma_knife_cut", "F", False, False, False),
        ("レイヤー移動ツール", "bmanga.layer_move_tool", "K", False, False, False),
        ("テキストツール", "bmanga.text_tool", "T", False, False, False),
        ("ナビゲート", "bmanga.view_navigate", "SPACE", False, False, False),
        ("ブラシサイズ", "bmanga.brush_size_drag", "LEFTMOUSE", False, True, True),
        ("レイヤー選択", "bmanga.page_pick_viewport", "LEFTMOUSE", True, True, False),
        ("ページ並べ替え", "bmanga.page_reorder_drag", "LEFTMOUSE", False, False, True),
        ("Alt移動", "bmanga.alt_reparent_drag", "LEFTMOUSE", False, False, True),
        ("Alt+Shift移動", "bmanga.alt_reparent_out", "LEFTMOUSE", True, False, True),
        ("次のページ", "bmanga.page_next", "COMMA", False, False, False),
        ("前のページ", "bmanga.page_prev", "PERIOD", False, False, False),
    ]
    lines = []
    for label, op_id, key, shift, ctrl, alt in expected:
        ok = any(
            item[0] == op_id
            and item[1] == key
            and item[2] == shift
            and item[3] == ctrl
            and item[4] == alt
            and item[5]
            for item in actual
        )
        if not ok:
            raise AssertionError(f"ショートカット不足: {label} {key}")
        mods = "+".join(part for part, enabled in (("Shift", shift), ("Ctrl", ctrl), ("Alt", alt)) if enabled)
        lines.append(f"{label}: {mods + '+' if mods else ''}{key}")
    if not any(
        item[0] == "bmanga.set_mode_object"
        and item[1] == "O"
        and not item[2]
        and not item[3]
        and not item[4]
        and item[5]
        for item in window_actual
    ):
        raise AssertionError("B-MANGAパネル上で使うオブジェクトツールショートカットがWindow側にありません")
    return True, lines


def _assert_menu_items(context) -> bool:
    from bmanga_dev_tool_visual.ui import context_menu
    from bmanga_dev_tool_visual.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context)
    assert stack is not None
    required_kinds = {"page", "coma", "gp", "effect", "raster", "image", "balloon", "text"}
    found = set()
    for index, item in enumerate(stack):
        kind = str(getattr(item, "kind", "") or "")
        if kind not in required_kinds:
            continue
        assert layer_stack_utils.select_stack_index(context, index)
        items = context_menu.selection_command_items(context)
        labels = [str(item.get("label", "")) for item in items]
        expected = ["詳細設定", "コピー", "貼り付け", "複製", "リンク複製"]
        if kind in {"balloon", "effect"}:
            expected.append("中心点を中心へ戻す")
            expected.append("自由変形")
            expected.append("自由変形をリセット")
            if kind == "balloon":
                expected.append("拡大・縮小・回転")
                expected.append("拡大・縮小・回転をリセット")
        elif kind == "text":
            expected.append("自由変形をリセット")
        expected.extend(["選択レイヤーをリンク", "リンクを解除"])
        if kind == "page":
            expected.extend(["見開きに変更", "見開きを解除"])
        if kind == "balloon":
            expected.extend(["フキダシを結合", "しっぽをコピー", "しっぽを貼り付け"])
        expected.append("削除")
        assert labels == expected, (kind, labels)
        for menu_item in items:
            op_id = str(menu_item.get("operator", "") or "")
            namespace, name = op_id.split(".", 1)
            assert getattr(getattr(bpy.ops, namespace), name, None) is not None, (kind, op_id)
        found.add(kind)
    missing = required_kinds - found
    if missing:
        raise AssertionError(f"右クリックメニュー対象不足: {sorted(missing)}")
    return True


def _assert_click_and_edit(context, data) -> bool:
    from bmanga_dev_tool_visual.operators import object_tool_op, object_tool_selection, view_op
    from bmanga_dev_tool_visual.operators import coma_edge_drag_session
    from bmanga_dev_tool_visual.utils import object_selection, page_browser, page_grid
    from bmanga_dev_tool_visual.utils.geom import Rect

    work = data["work"]
    page = data["page"]
    panel = data["panel"]
    _window, _screen, area, _region, _rv3d = _view3d_context()
    if page_browser.is_marked_area(area) or page_browser.page_browser_area(context) == area:
        ox, oy = page_browser.page_offset_mm(work, context.scene, area, 0)
    else:
        ox, oy = page_grid.page_total_offset_mm(work, context.scene, 0)
    keys = {
        "page": object_selection.page_key(page),
        "coma": object_selection.coma_key(page, panel),
        "gp": object_selection.gp_key(data["gp"]),
        "effect": object_selection.effect_key(data["effect"]),
        "raster": object_selection.raster_key(data["raster"]),
        "image": object_selection.image_key(data["image"]),
        "balloon": object_selection.balloon_key(page, data["balloon"]),
        "text": object_selection.text_key(page, data["text"]),
    }
    hit_aliases = {"coma": {"coma", "coma_edge", "coma_vertex"}}

    def expected_hit_kinds(expected_kind: str) -> set[str]:
        return set(hit_aliases.get(expected_kind, {expected_kind}))

    def find_click_point(expected_kind: str, candidates: list[tuple[float, float]]) -> tuple[float, float]:
        expected = expected_hit_kinds(expected_kind)
        seen = []
        for local_x, local_y in candidates:
            point = (ox + local_x, oy + local_y)
            event = _screen_event_for_world(point[0], point[1])
            hit = object_tool_op.hit_object_at_event(context, event)
            event_world = object_tool_op.coma_picker._event_world_mm(context, event)
            seen.append((
                local_x,
                local_y,
                None if event_world is None else round(event_world[0] - ox, 2),
                None if event_world is None else round(event_world[1] - oy, 2),
                None if hit is None else str(hit.get("kind", "")),
            ))
            if hit is not None and str(hit.get("kind", "")) in expected:
                return point
        raise AssertionError(f"クリック選択できる地点が見つかりません: {sorted(expected)}; seen={seen}")

    hit_points = {
        "page": find_click_point(
            "page",
            [
                (4.0, 12.0), (8.0, 32.0), (8.0, 148.0), (8.0, 276.0),
                (105.0, 12.0), (105.0, 280.0), (204.0, 24.0), (204.0, 276.0),
                (14.0, 92.0), (196.0, 92.0), (14.0, 210.0), (196.0, 210.0),
            ],
        ),
        "coma": find_click_point(
            "coma",
            [
                (22.0, 150.0), (190.0, 150.0), (106.0, 44.0), (106.0, 250.0),
                (22.0, 44.0), (190.0, 44.0), (190.0, 250.0), (22.0, 250.0),
                (176.0, 230.0), (178.0, 198.0), (160.0, 210.0), (34.0, 232.0),
                (34.0, 194.0), (120.0, 220.0), (170.0, 118.0), (100.0, 188.0),
            ],
        ),
        "gp": find_click_point(
            "gp",
            [(58.0, 74.0), (62.0, 80.0), (68.0, 74.0), (52.0, 72.0)],
        ),
        "effect": find_click_point(
            "effect",
            [(114.0, 77.0), (104.0, 70.0), (126.0, 82.0), (96.0, 64.0)],
        ),
        "raster": find_click_point(
            "raster",
            [(36.0, 232.0), (44.0, 224.0), (32.0, 216.0), (50.0, 238.0)],
        ),
        "image": find_click_point(
            "image",
            [(154.0, 72.0), (148.0, 66.0), (164.0, 76.0), (142.0, 62.0)],
        ),
        "balloon": find_click_point(
            "balloon",
            [(76.0, 144.0), (64.0, 138.0), (88.0, 150.0), (76.0, 132.0)],
        ),
        "text": find_click_point(
            "text",
            [(142.0, 146.0), (132.0, 140.0), (154.0, 152.0), (126.0, 154.0)],
        ),
    }
    for kind, point in hit_points.items():
        event = _screen_event_for_world(point[0], point[1])
        hit = object_tool_op.hit_object_at_event(context, event)
        if hit is None or str(hit.get("kind", "")) not in expected_hit_kinds(kind):
            view = object_tool_op.view_event_region.view3d_window_under_event(context, event)
            edge_hit = None
            page_hit = None
            if view is not None:
                _area, region, rv3d, mx, my = view
                edge_hit = object_tool_op.coma_edge_move_op._pick_edge_or_vertex(work, region, rv3d, int(mx), int(my))
                page_hit = object_tool_op.coma_picker.find_page_at_event(context, event)
            raise AssertionError(
                "クリック選択対象が不一致: "
                f"{kind} -> {hit}; point={point}; mouse=({event.mouse_x},{event.mouse_y}); "
                f"edge={edge_hit}; page={page_hit}"
            )
        object_tool_op.activate_hit(context, hit, mode="single")
        if keys[kind] not in object_selection.get_keys(context):
            raise AssertionError(f"クリック選択キーが入っていません: {kind}")

    while len(work.pages) < 3:
        page_extra = work.pages.add()
        index = len(work.pages)
        page_extra.id = f"p{index:04d}"
        page_extra.title = f"{index}ページ"
        page_extra.in_page_range = True
        coma_extra = page_extra.comas.add()
        coma_extra.id = "c01"
        coma_extra.coma_id = "c01"
        coma_extra.title = "コマ1"
        coma_extra.shape_type = "rect"
        coma_extra.rect_x_mm = 24.0
        coma_extra.rect_y_mm = 48.0
        coma_extra.rect_width_mm = 120.0
        coma_extra.rect_height_mm = 160.0
    context.scene.bmanga_overview_cols = 1
    context.scene.bmanga_page_browser_fit = True
    page_browser.mark_workspace(context.workspace, "LEFT")
    browser_bbox = page_browser.layout_bbox_mm(work, context.scene, area)
    if browser_bbox is None:
        raise AssertionError("ページ一覧の表示範囲を計算できません")
    with _view3d_override():
        _window, _screen, fit_area, fit_region, _fit_rv3d = _view3d_context()
        view_op._fit_view_to_rect_mm(bpy.context, fit_area, fit_region, *browser_bbox)
        try:
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        except Exception:
            pass
    browser_page_index = 2
    browser_page = work.pages[browser_page_index]
    browser_coma = browser_page.comas[0]
    box, boy = page_browser.page_offset_mm(work, context.scene, area, browser_page_index)
    vertex_world = (box + browser_coma.rect_x_mm, boy + browser_coma.rect_y_mm)
    vertex_event = _screen_event_for_world(vertex_world[0], vertex_world[1])
    vertex_hit = object_tool_op.hit_object_at_event(context, vertex_event)
    if vertex_hit is None or str(vertex_hit.get("kind", "")) != "coma_vertex":
        view = object_tool_op.view_event_region.view3d_window_under_event(context, vertex_event)
        direct_edge = None
        event_world = object_tool_op.coma_picker._event_world_mm(context, vertex_event)
        if view is not None:
            dbg_area, dbg_region, dbg_rv3d, dbg_mx, dbg_my = view
            direct_edge = object_tool_op.coma_edge_move_op._pick_edge_or_vertex(
                work,
                dbg_region,
                dbg_rv3d,
                int(dbg_mx),
                int(dbg_my),
                context=context,
                area=dbg_area,
            )
        raise AssertionError(
            "ページ一覧の頂点をドラッグ対象として拾えません: "
            f"hit={vertex_hit} direct={direct_edge} event=({vertex_event.mouse_x},{vertex_event.mouse_y}) "
            f"world={None if event_world is None else (round(event_world[0], 2), round(event_world[1], 2))} "
            f"target={(round(vertex_world[0], 2), round(vertex_world[1], 2))} "
            f"bbox={tuple(round(float(v), 2) for v in browser_bbox)} "
            f"area={None if view is None else (dbg_region.width, dbg_region.height, round(dbg_mx, 2), round(dbg_my, 2))}"
        )
    view = object_tool_op.view_event_region.view3d_window_under_event(context, vertex_event)
    if view is None:
        raise AssertionError("ページ一覧のドラッグ開始位置が3Dビュー内にありません")
    drag_area, drag_region, drag_rv3d, _mx, _my = view
    selection = {
        "type": "vertex",
        "page": int(vertex_hit["page"]),
        "coma": int(vertex_hit["coma"]),
        "vertex": int(vertex_hit["vertex"]),
    }
    session = coma_edge_drag_session.ComaEdgeDragSession(
        context,
        work,
        drag_area,
        drag_region,
        drag_rv3d,
        selection,
        vertex_world,
    )
    move_event = _screen_event_for_world(vertex_world[0] + 6.0, vertex_world[1] + 4.0, event_type="MOUSEMOVE")
    session.apply(move_event)
    session.finish("B-MANGA: 枠線移動")
    if str(getattr(browser_coma, "shape_type", "")) != "polygon" or len(browser_coma.vertices) < 1:
        raise AssertionError("ページ一覧の頂点ドラッグがコマ形状に反映されません")
    moved_v = browser_coma.vertices[int(selection["vertex"])]
    if abs(float(moved_v.x_mm) - (24.0 + 6.0)) > 0.5 or abs(float(moved_v.y_mm) - (48.0 + 4.0)) > 0.5:
        raise AssertionError(
            f"ページ一覧の頂点ドラッグ量が不正です: {(float(moved_v.x_mm), float(moved_v.y_mm))}"
        )

    selected = set(
        object_tool_selection.select_keys_in_world_rect(
            context,
            Rect(ox + 20.0, oy + 40.0, 175.0, 215.0),
            mode="single",
        )
    )
    for kind in ("coma", "gp", "effect", "raster", "image", "balloon", "text"):
        if keys[kind] not in selected:
            raise AssertionError(f"矩形ドラッグ選択から漏れました: {kind}")

    for kind in ("gp", "effect", "raster", "image", "balloon", "text"):
        key = keys[kind]
        fake_op = SimpleNamespace(_drag_action="move")
        snapshots = object_tool_op.BMANGA_OT_object_tool._make_snapshots(
            fake_op,
            context,
            [key],
            primary_key=key,
            action="move",
        )
        if not snapshots:
            raise AssertionError(f"ドラッグ編集の準備ができません: {kind}")
        before = object_tool_selection.selection_bounds_for_key(context, key)
        fake_op._snapshots = snapshots
        object_tool_op.BMANGA_OT_object_tool._apply_snapshots(fake_op, context, 3.0, 2.0)
        if kind == "raster":
            if not bool(data["raster"].get("bmanga_raster_dirty", False)):
                raise AssertionError("ラスターのドラッグ編集が反映されていません")
            continue
        after = object_tool_selection.selection_bounds_for_key(context, key)
        if before is None or after is None or abs(float(after.x) - float(before.x)) < 0.5:
            raise AssertionError(f"ドラッグ編集で位置が変わりません: {kind}")
    return True


def _invoke_tool(label: str, op_id: str, operator_context: str, props: dict | None = None) -> dict:
    props = props or {}
    namespace, name = op_id.split(".", 1)
    op = getattr(getattr(bpy.ops, namespace), name)
    with _view3d_override():
        try:
            from bmanga_dev_tool_visual.utils import shortcut_visibility

            # Use only the validated override area.  Iterating every cached
            # SpaceView3D immediately after page.blend replacement can touch a
            # stale RNA wrapper in Blender 5.1.
            space = getattr(bpy.context, "space_data", None)
            if space is not None and not bool(getattr(space, "show_region_ui", False)):
                space.show_region_ui = True
            shortcut_visibility.mark_bmanga_panel_drawn(bpy.context)
        except Exception:
            pass
        try:
            result = op(operator_context, **props)
        except TypeError:
            result = op(**props)
    ok = "FINISHED" in result or "RUNNING_MODAL" in result
    if not ok:
        raise AssertionError(f"{label} の起動に失敗: {result}")
    return {"label": label, "result": sorted(result), "ok": True}


def _select_stack_for_key(context, key: str) -> bool:
    from bmanga_dev_tool_visual.utils import layer_hierarchy, layer_stack as layer_stack_utils
    from bmanga_dev_tool_visual.utils import object_selection

    kind, page_id, item_id = object_selection.parse_key(key)
    if kind == "page":
        stack_key = item_id
    elif kind in {"coma", "balloon", "text"}:
        stack_key = f"{page_id}:{item_id}"
    elif page_id == layer_hierarchy.OUTSIDE_STACK_KEY:
        stack_key = layer_hierarchy.outside_child_key(item_id)
    else:
        stack_key = item_id
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        return False
    for stack_index, item in enumerate(stack):
        if str(getattr(item, "kind", "")) == kind and str(getattr(item, "key", "")) == stack_key:
            return layer_stack_utils.select_stack_index(context, stack_index)
    return False


def _run_tool_visuals(context, data) -> list[dict]:
    from bmanga_dev_tool_visual.utils import layer_stack as layer_stack_utils
    from bmanga_dev_tool_visual.utils import object_selection

    tool_specs = [
        ("オブジェクトツール", "bmanga.object_tool", "INVOKE_DEFAULT", {}, object_selection.balloon_key(data["page"], data["balloon"])),
        ("GP描画", "bmanga.gpencil_master_mode_set", "EXEC_DEFAULT", {"mode": "PAINT_GREASE_PENCIL"}, object_selection.gp_key(data["gp"])),
        ("ラスター描画", "bmanga.raster_layer_mode_set", "EXEC_DEFAULT", {"mode": "TEXTURE_PAINT"}, object_selection.raster_key(data["raster"])),
        ("枠線カットツール", "bmanga.coma_knife_cut", "INVOKE_DEFAULT", {}, object_selection.coma_key(data["page"], data["panel"])),
        ("レイヤー移動ツール", "bmanga.layer_move_tool", "INVOKE_DEFAULT", {}, object_selection.image_key(data["image"])),
        ("フキダシツール", "bmanga.balloon_tool", "INVOKE_DEFAULT", {}, object_selection.balloon_key(data["page"], data["balloon"])),
        ("テキストツール", "bmanga.text_tool", "INVOKE_DEFAULT", {}, object_selection.text_key(data["page"], data["text"])),
        ("効果線ツール", "bmanga.effect_line_tool", "INVOKE_DEFAULT", {}, object_selection.effect_key(data["effect"])),
    ]
    items = []
    for index, (label, op_id, op_context, props, select_key) in enumerate(tool_specs):
        print(f"BMANGA_TOOL_VISUAL_PHASE tool={index}:{label}", flush=True)
        if index >= 3:
            # Match the toolbar's object-mode transition before launching a
            # modal creation/editing tool from raster paint mode.
            with _view3d_override():
                mode_result = bpy.ops.bmanga.raster_layer_mode_set("EXEC_DEFAULT", mode="OBJECT")
            if "FINISHED" not in mode_result:
                raise AssertionError(f"{label} の前にオブジェクトモードへ戻せません: {mode_result}")
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        if not _select_stack_for_key(context, select_key):
            raise AssertionError(f"{label} の対象をレイヤー一覧で選択できません")
        object_selection.set_keys(context, [select_key])
        select_kind, _page_id, _item_id = object_selection.parse_key(select_key)
        if select_kind == "raster":
            context.scene.bmanga_active_layer_kind = "raster"
        elif select_kind == "gp":
            context.scene.bmanga_active_layer_kind = "gp"
        result = _invoke_tool(label, op_id, op_context, props)
        shot = _screenshot(f"tool_{index:02d}.png")
        items.append({
            "label": label,
            "result": ",".join(result["result"]),
            "note": "選択ハンドルとツール切替後の画面を確認",
            "screenshot": shot,
        })
    return items


def _run_visual_audit() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        bpy.context.preferences.view.show_splash = False
    except Exception:
        pass
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_tool_visual_audit_"))
    mod = None
    try:
        print("BMANGA_TOOL_VISUAL_PHASE setup", flush=True)
        mod, data = _setup_scene(temp_root)
        context = bpy.context
        with _view3d_override():
            bpy.ops.view3d.view_axis(type="TOP", align_active=False)
            rv3d = getattr(bpy.context.space_data, "region_3d", None)
            if rv3d is not None:
                rv3d.view_perspective = "ORTHO"
            bpy.ops.bmanga.view_fit_page("EXEC_DEFAULT")
            rv3d = getattr(bpy.context.space_data, "region_3d", None)
            if rv3d is not None:
                rv3d.view_perspective = "ORTHO"
                rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
        _dismiss_startup_splash()
        print("BMANGA_TOOL_VISUAL_PHASE shortcuts", flush=True)
        shortcut_ok, shortcut_lines = _assert_shortcuts()
        print("BMANGA_TOOL_VISUAL_PHASE menus", flush=True)
        menu_ok = _assert_menu_items(context)
        print("BMANGA_TOOL_VISUAL_PHASE selection", flush=True)
        with _view3d_override():
            selection_edit_ok = _assert_click_and_edit(bpy.context, data)
        print("BMANGA_TOOL_VISUAL_PHASE tools", flush=True)
        screenshots = _run_tool_visuals(context, data)
        summary = {
            "shortcut_ok": shortcut_ok,
            "menu_ok": menu_ok,
            "selection_edit_ok": selection_edit_ok,
            "shortcut_lines": shortcut_lines,
        }
        contact = _make_contact_sheet(screenshots, summary)
        result_path = OUT_DIR / "tool_behavior_visual_audit.json"
        result_path.write_text(
            json.dumps(
                {
                    "contact_sheet": contact,
                    "summary": summary,
                    "screenshots": screenshots,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"BMANGA_TOOL_BEHAVIOR_VISUAL_OK visual={contact}", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


def _has_view3d_context() -> bool:
    try:
        _view3d_context()
        return True
    except Exception:
        return False


def _visual_audit_tick():
    if not _has_view3d_context():
        return 0.25
    try:
        _run_visual_audit()
        sys.stdout.flush()
        os._exit(0)
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    return None


def main() -> None:
    bpy.app.timers.register(_visual_audit_tick, first_interval=0.25)


if __name__ == "__main__":
    main()
