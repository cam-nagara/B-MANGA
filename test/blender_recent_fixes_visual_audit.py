"""Blender実機用: 直近修正のAI目視用チェックシートを生成する。"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(
    os.environ.get("BMANGA_RECENT_FIXES_VISUAL_OUT", "")
    or ROOT / "_verify" / "recent_fixes_visual_audit"
).resolve()


def _load_package(package_name: str, package_root: Path):
    spec = importlib.util.spec_from_file_location(
        package_name,
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _unregister(mod) -> None:
    if mod is None:
        return
    try:
        mod.unregister()
    except Exception:
        pass


def _sub(package_name: str, path: str):
    __import__(f"{package_name}.{path}")
    return sys.modules[f"{package_name}.{path}"]


def _result(title: str, ok: bool, detail: str, *, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "title": title,
        "ok": bool(ok),
        "detail": detail,
        "evidence": evidence or {},
    }


class _MenuLayout:
    def __init__(self):
        self.operator_context = "EXEC_DEFAULT"
        self.enabled = True
        self.ops: list[tuple[str, dict[str, Any]]] = []
        self.menus: list[tuple[str, dict[str, Any]]] = []

    def row(self, align=False):
        del align
        return self

    def separator(self):
        return None

    def menu(self, menu_idname, **kwargs):
        self.menus.append((menu_idname, kwargs))
        return None

    def operator(self, op_id, **kwargs):
        self.ops.append((op_id, kwargs))
        return type("_OpProps", (), {})()


class _Layout:
    def __init__(
        self,
        props=None,
        labels=None,
        ops=None,
        grid_columns=None,
        props_by_column=None,
        column_name: str = "root",
    ):
        self.props = [] if props is None else props
        self.labels = [] if labels is None else labels
        self.ops = [] if ops is None else ops
        self.grid_columns = [] if grid_columns is None else grid_columns
        self.props_by_column = {} if props_by_column is None else props_by_column
        self.column_name = column_name
        self.enabled = True

    def _child(self, column_name: str | None = None):
        return _Layout(
            self.props,
            self.labels,
            self.ops,
            self.grid_columns,
            self.props_by_column,
            self.column_name if column_name is None else column_name,
        )

    def box(self):
        return self._child()

    def row(self, align: bool = False):
        del align
        return self._child()

    def column(self, align: bool = False):
        del align
        return self._child()

    def split(self, factor: float = 0.5, align: bool = False):
        del factor, align
        return self._child()

    def grid_flow(self, **kwargs):
        self.grid_columns.append(int(kwargs.get("columns", 0) or 0))
        return _GridLayout(
            self.props,
            self.labels,
            self.ops,
            self.grid_columns,
            self.props_by_column,
        )

    def separator(self, **_kwargs):
        return None

    def label(self, text: str = "", **_kwargs):
        self.labels.append(str(text))
        return None

    def prop(self, _owner, attr: str, **_kwargs):
        name = str(attr)
        self.props.append(name)
        self.props_by_column.setdefault(self.column_name, []).append(name)
        return None

    def prop_search(self, _owner, attr: str, *_args, **_kwargs):
        return self.prop(_owner, attr, **_kwargs)

    def operator(self, op_id: str, **_kwargs):
        self.ops.append(str(op_id))
        return type("_OpProps", (), {})()

    def template_curve_mapping(self, *_args, **_kwargs):
        self.labels.append("線幅グラフ")
        return None


class _GridLayout(_Layout):
    def __init__(self, props, labels, ops, grid_columns, props_by_column):
        super().__init__(props, labels, ops, grid_columns, props_by_column, "grid")
        self._next_column_index = 0

    def column(self, align: bool = False):
        del align
        name = f"col{self._next_column_index}"
        self._next_column_index += 1
        return self._child(name)


def _assert_close(actual: float, expected: float, label: str, eps: float = 1.0e-4) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: actual={actual!r}, expected={expected!r}")


def _set_effect_params_silently(scene, effect_line_op, callback) -> None:
    effect_line_op._set_scene_params_syncing(scene, True)
    try:
        callback(scene.bmanga_effect_line_params)
    finally:
        effect_line_op._set_scene_params_syncing(scene, False)


def _check_line_register() -> dict[str, Any]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_package("b_manga_line_recent_visual", ROOT / "addons" / "b_manga_line")
    try:
        from b_manga_line_recent_visual import core

        mod.register()
        mod.unregister()
        mod.register()
        ok = (
            bool(getattr(core.BMangaLineSettings, "is_registered", False))
            and getattr(bpy.types.Object, "bmanga_line_settings", None) is not None
            and getattr(bpy.types.Scene, "bmanga_line_camera", None) is not None
            and getattr(bpy.types, "BMANGA_LINE_PT_main", None) is not None
        )
        return _result(
            "B-MANGA Liner の再有効化",
            ok,
            "register → unregister → register を実機で実行し、重複登録エラーなし。",
        )
    finally:
        _unregister(mod)


def _check_render_tab_context() -> dict[str, Any]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_package("bmanga_render_recent_visual", ROOT / "addons" / "b_manga_render")
    try:
        from bmanga_render_recent_visual import panels

        normal_visible = panels.BMANGA_RENDER_PT_main.poll(bpy.context)
        coma_context = SimpleNamespace(
            scene=SimpleNamespace(
                bmanga_current_coma_page_id="p0001",
                bmanga_current_coma_id="c01",
            )
        )
        coma_visible = panels.BMANGA_RENDER_PT_main.poll(coma_context)
        ok = (
            getattr(bpy.types, "BMANGA_RENDER_PT_main", None) is not None
            and getattr(bpy.types, "BMANGA_RENDER_PT_main").bl_category == "BMRender"
            and normal_visible is False
            and coma_visible is True
        )
        return _result(
            "BMRender タブ表示",
            ok,
            "通常ファイルでは非表示、コマファイルではBMRenderタブとして表示される。",
            evidence={
                "normal_visible": normal_visible,
                "coma_visible": coma_visible,
                "category": getattr(bpy.types, "BMANGA_RENDER_PT_main").bl_category,
            },
        )
    finally:
        _unregister(mod)


def _move_uid(stack, uid: str, target_index: int, layer_stack_utils) -> None:
    current = next(
        (i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == uid),
        -1,
    )
    if current < 0:
        raise AssertionError(f"レイヤーリストに対象がありません: {uid}")
    stack.move(current, target_index)


def _check_main_addon() -> list[dict[str, Any]]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_recent_fixes_visual_"))
    mod = None
    package_name = "bmanga_dev_recent_visual"
    results: list[dict[str, Any]] = []
    try:
        mod = _load_package(package_name, ROOT)
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "RecentFixesVisual.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        context = bpy.context
        scene = context.scene
        work = scene.bmanga_work
        page = work.pages[0]

        results.append(_check_coma_menu(package_name))
        results.append(_check_tool_presets(package_name, context))
        results.append(_check_layer_order(package_name, context, scene, page))
        results.append(_check_effect_detail_graph(package_name, context, scene, page))
        results.append(_check_page_preview_update(context, temp_root, page))
        return results
    finally:
        _unregister(mod)
        shutil.rmtree(temp_root, ignore_errors=True)


def _check_coma_menu(package_name: str) -> dict[str, Any]:
    context_menu = _sub(package_name, "ui.context_menu")
    page_file_scene = _sub(package_name, "utils.page_file_scene")
    shortcut_visibility = _sub(package_name, "utils.shortcut_visibility")
    original_current_role = page_file_scene.current_role
    original_current_blend_is_coma = shortcut_visibility.current_blend_is_coma_blend
    original_panel_visible = shortcut_visibility.bmanga_panel_visible
    try:
        page_file_scene.current_role = lambda _context=None: (page_file_scene.ROLE_COMA, "p0001", "c01")
        shortcut_visibility.current_blend_is_coma_blend = lambda: True
        shortcut_visibility.bmanga_panel_visible = lambda _context=None: True
        layout = _MenuLayout()
        context_menu.BMANGA_MT_object_context.draw(SimpleNamespace(layout=layout), bpy.context)
        labels = [str(kwargs.get("text", "") or "") for _op_id, kwargs in layout.ops]
        ok = labels == ["リンク元ファイルを開く", "このリンクを記録"]
        return _result(
            "コマファイル右クリックメニュー",
            ok,
            "B-MANGA サブメニューはリンク元ファイルを開く / このリンクを記録だけ。",
            evidence={"labels": labels},
        )
    finally:
        page_file_scene.current_role = original_current_role
        shortcut_visibility.current_blend_is_coma_blend = original_current_blend_is_coma
        shortcut_visibility.bmanga_panel_visible = original_panel_visible


def _ids(items) -> list[str]:
    return [str(item[0]) for item in items]


def _check_tool_presets(package_name: str, context) -> dict[str, Any]:
    preset_op = _sub(package_name, "operators.preset_op")
    effect_line_preset_op = _sub(package_name, "operators.effect_line_preset_op")
    balloon_tail_detail_op = _sub(package_name, "operators.balloon_tail_detail_op")
    wm = context.window_manager
    balloon_ids = _ids(preset_op._balloon_tool_preset_enum_items(None, context))
    expected = {
        "DEFAULT",
        "mode:nurbs",
        "shape:rect",
        "shape:ellipse",
        "shape:cloud",
        "shape:fluffy",
        "shape:thorn",
        "shape:thorn-curve",
    }
    for preset_id in balloon_ids:
        wm.bmanga_balloon_tool_preset_selector = preset_id
        preset_op.selected_balloon_tool_creation_mode(context)
        preset_op.selected_balloon_tool_shape(context)
    counts = {
        "フキダシ": len(balloon_ids),
        "囲い塗り": len(_ids(preset_op._fill_tool_preset_enum_items(None, context))),
        "グラデーション": len(_ids(preset_op._gradient_tool_preset_enum_items(None, context))),
        "テキスト": len([name for name in _ids(preset_op._text_preset_enum_items(None, context)) if name]),
        "効果線": len(_ids(effect_line_preset_op._effect_line_tool_preset_enum_items(None, context))),
        "しっぽ": len(_ids(balloon_tail_detail_op._tail_preset_enum_items(None, context))),
        "パターンカーブ": len(_ids(preset_op._image_path_tool_preset_enum_items(None, context))),
        "コマ作成": len(_ids(preset_op._border_preset_enum_items(None, context))),
    }
    ok = expected <= set(balloon_ids) and all(value > 0 for value in counts.values())
    return _result(
        "各ツールのプリセット切替",
        ok,
        "全プリセット一覧を取得し、フキダシは各項目を選択して作成モード解決まで確認。",
        evidence={"counts": counts, "balloon_ids": balloon_ids},
    )


def _check_layer_order(package_name: str, context, scene, page) -> dict[str, Any]:
    balloon_op = _sub(package_name, "operators.balloon_op")
    effect_line_op = _sub(package_name, "operators.effect_line_op")
    fill_real_object = _sub(package_name, "utils.fill_real_object")
    balloon_curve_object = _sub(package_name, "utils.balloon_curve_object")
    effect_line_object = _sub(package_name, "utils.effect_line_object")
    layer_hierarchy = _sub(package_name, "utils.layer_hierarchy")
    layer_stack_utils = _sub(package_name, "utils.layer_stack")

    parent_key = layer_hierarchy.page_stack_key(page)
    fill = scene.bmanga_fill_layers.add()
    fill.id = "visual_layer_order_fill"
    fill.title = "グラデーション"
    fill.fill_type = "gradient"
    fill.parent_kind = "page"
    fill.parent_key = parent_key
    fill_obj = fill_real_object.ensure_fill_real_object(scene=scene, entry=fill, page=page)
    balloon = balloon_op._create_balloon_entry(
        context,
        page,
        shape="ellipse",
        x=76.0,
        y=82.0,
        w=60.0,
        h=34.0,
        parent_kind="page",
        parent_key=parent_key,
    )
    balloon.id = "visual_layer_order_balloon"
    balloon_obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=balloon, page=page)
    effect_obj, effect_layer = effect_line_op._create_effect_layer(
        context,
        (76.0, 82.0, 60.0, 34.0),
        parent_key=parent_key,
    )
    effect_display = effect_line_object.find_effect_display_object(effect_obj) or effect_obj
    assert fill_obj is not None and balloon_obj is not None and effect_obj is not None and effect_layer is not None

    layer_stack_utils.sync_layer_stack_after_data_change(context)
    stack = layer_stack_utils.sync_layer_stack(context)
    assert stack is not None
    parent_index = next((i for i, item in enumerate(stack) if str(getattr(item, "key", "") or "") == parent_key), -1)
    assert parent_index >= 0
    fill_uid = layer_stack_utils.target_uid("fill", fill.id)
    balloon_uid = layer_stack_utils.target_uid("balloon", f"{page.id}:{balloon.id}")
    effect_uid = layer_stack_utils.target_uid("effect", layer_stack_utils._node_stack_key(effect_layer))

    _move_uid(stack, balloon_uid, parent_index + 1, layer_stack_utils)
    _move_uid(stack, fill_uid, parent_index + 1, layer_stack_utils)
    layer_stack_utils.apply_stack_order(context)
    context.view_layer.update()
    fill_front = float(fill_obj.location.z) > float(balloon_obj.location.z)

    _move_uid(stack, effect_uid, parent_index + 1, layer_stack_utils)
    _move_uid(stack, balloon_uid, parent_index + 1, layer_stack_utils)
    layer_stack_utils.apply_stack_order(context)
    context.view_layer.update()
    balloon_front = float(balloon_obj.location.z) > float(effect_display.location.z)

    ok = fill_front and balloon_front
    return _result(
        "レイヤーリスト順の即時反映",
        ok,
        "グラデーション/フキダシ/効果線の代表入れ替えで、ビューポート上の前後関係が即時更新。",
        evidence={
            "gradient_front_z": float(fill_obj.location.z),
            "balloon_z": float(balloon_obj.location.z),
            "effect_z": float(effect_display.location.z),
        },
    )


def _check_effect_detail_graph(package_name: str, context, scene, page) -> dict[str, Any]:
    effect_line_gen = _sub(package_name, "operators.effect_line_gen")
    effect_line_op = _sub(package_name, "operators.effect_line_op")
    layer_detail_op = _sub(package_name, "operators.layer_detail_op")
    effect_inout_curve = _sub(package_name, "utils.effect_inout_curve")
    object_naming = _sub(package_name, "utils.object_naming")
    page_stack_key = _sub(package_name, "utils.layer_hierarchy").page_stack_key

    def initial_values(p):
        p.effect_type = "focus"
        p.spacing_mode = "angle"
        p.spacing_angle_deg = 30.0
        p.max_line_count = 12
        p.brush_size_mm = 2.0
        p.inout_apply_brush_size = True
        p.inout_apply_opacity = True
        p.in_percent = 30.0
        p.out_percent = 20.0
        p.in_start_percent = 40.0
        p.out_start_percent = 25.0

    _set_effect_params_silently(scene, effect_line_op, initial_values)
    obj, layer = effect_line_op._create_effect_layer(
        context,
        (42.0, 55.0, 82.0, 66.0),
        parent_key=page_stack_key(page),
    )
    assert obj is not None and layer is not None
    layout = _Layout()
    layer_detail_op._draw_effect_detail(layout, context, obj, load_from_layer=False)
    node = effect_inout_curve.get_profile_node()
    if node is None:
        raise AssertionError("線幅グラフが作成されていません")
    effect_inout_curve._apply_points_to_node(
        node,
        ((0.0, 0.15), (0.25, 0.7), (0.45, 1.0), (0.75, 1.0), (1.0, 0.35)),
    )
    bmanga_id = object_naming.get_bmanga_id(obj)
    layer_detail_op._sync_detail_profile_curve(context, "effect", bmanga_id)
    params = scene.bmanga_effect_line_params
    _assert_close(params.in_percent, 15.0, "入り")
    _assert_close(params.out_percent, 35.0, "抜き")
    _assert_close(params.in_start_percent, 45.0, "入り始点")
    _assert_close(params.out_start_percent, 25.0, "抜き始点")
    assert layer_detail_op._apply_effect_detail_params_to_layer(context, obj, layer)
    strokes = effect_line_gen.generate_strokes(
        params,
        center_xy_mm=(82.0, 87.0),
        radius_xy_mm=(42.0, 32.0),
        seed=4,
    )
    radii = [float(radius) for stroke in strokes for radius in (getattr(stroke, "radii", None) or ())]
    ok = 5 in layout.grid_columns and radii and min(radii) < max(radii) * 0.8
    return _result(
        "効果線詳細設定と線幅グラフ",
        ok,
        "詳細設定は5列、グラフ編集は入り抜き数値と生成線の太さへ反映。",
        evidence={
            "grid_columns": layout.grid_columns,
            "in_percent": float(params.in_percent),
            "out_percent": float(params.out_percent),
            "in_start_percent": float(params.in_start_percent),
            "out_start_percent": float(params.out_start_percent),
            "radius_min": min(radii) if radii else None,
            "radius_max": max(radii) if radii else None,
        },
    )


def _check_page_preview_update(context, temp_root: Path, page) -> dict[str, Any]:
    preview_path = temp_root / "RecentFixesVisual.bmanga" / str(page.id) / "page_preview.png"
    before = preview_path.stat().st_mtime if preview_path.is_file() else 0.0
    result = bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
    assert "FINISHED" in result, result
    after = preview_path.stat().st_mtime if preview_path.is_file() else 0.0
    ok = preview_path.is_file() and after > before
    return _result(
        "作品ファイルへ戻った時のページ画像更新",
        ok,
        "ページ編集後に保存して戻るタイミングでページ画像が生成・更新される。",
        evidence={"preview_path": str(preview_path), "mtime_before": before, "mtime_after": after},
    )


def _font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    for path in (
        r"C:\Windows\Fonts\YuGothB.ttc" if bold else r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\meiryob.ttc" if bold else r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    ):
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    rows: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if current and width > max_width:
            rows.append(current)
            current = char
        else:
            current = candidate
    if current:
        rows.append(current)
    return rows


def _make_contact_sheet(results: list[dict[str, Any]]) -> str:
    from PIL import Image, ImageDraw

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    width = 1640
    row_h = 152
    header_h = 140
    height = header_h + row_h * len(results) + 36
    image = Image.new("RGB", (width, height), (250, 250, 248))
    draw = ImageDraw.Draw(image)
    title_font = _font(34, bold=True)
    label_font = _font(24, bold=True)
    text_font = _font(20)
    small_font = _font(17)
    ok_count = sum(1 for item in results if item["ok"])
    draw.text((28, 26), "B-MANGA 直近修正 AI目視チェックシート", fill=(0, 0, 0), font=title_font)
    draw.text(
        (28, 76),
        f"確認項目: {len(results)} / OK: {ok_count} / NG: {len(results) - ok_count}",
        fill=(34, 34, 34),
        font=text_font,
    )
    y = header_h
    for index, item in enumerate(results, 1):
        ok = bool(item["ok"])
        bg = (236, 250, 240) if ok else (255, 232, 232)
        badge = (34, 128, 72) if ok else (190, 48, 48)
        draw.rounded_rectangle((20, y, width - 20, y + row_h - 16), radius=10, fill=bg, outline=(200, 210, 204))
        draw.rounded_rectangle((42, y + 30, 138, y + 76), radius=8, fill=badge)
        draw.text((62, y + 38), "OK" if ok else "NG", fill="white", font=label_font)
        draw.text((168, y + 24), f"{index}. {item['title']}", fill=(0, 0, 0), font=label_font)
        line_y = y + 62
        for line in _wrap_text(draw, item["detail"], text_font, 860)[:2]:
            draw.text((168, line_y), line, fill=(38, 38, 38), font=text_font)
            line_y += 26
        evidence = json.dumps(item.get("evidence", {}), ensure_ascii=False, sort_keys=True)
        for line in _wrap_text(draw, evidence, small_font, 480)[:4]:
            draw.text((1110, line_y - 52), line, fill=(70, 70, 70), font=small_font)
            line_y += 22
        y += row_h
    path = OUT_DIR / "recent_fixes_ai_visual_contact.png"
    image.save(path)
    return str(path)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    try:
        results.append(_check_line_register())
        results.append(_check_render_tab_context())
        results.extend(_check_main_addon())
        contact = _make_contact_sheet(results)
        payload = {"contact_sheet": contact, "results": results}
        (OUT_DIR / "recent_fixes_ai_visual_summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if not all(item["ok"] for item in results):
            raise AssertionError(json.dumps(results, ensure_ascii=False))
        print(f"BMANGA_RECENT_FIXES_VISUAL_OK visual={contact}", flush=True)
    except Exception:
        (OUT_DIR / "recent_fixes_ai_visual_error.txt").write_text(
            traceback.format_exc(),
            encoding="utf-8",
        )
        traceback.print_exc()
        os._exit(1)


if __name__ == "__main__":
    main()
