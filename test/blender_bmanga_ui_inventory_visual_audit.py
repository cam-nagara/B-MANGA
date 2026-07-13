"""Blender実機用: B-MANGA UI項目の実描画棚卸しをAI目視用画像にする."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(
    os.environ.get("BMANGA_UI_INVENTORY_OUT", "")
    or tempfile.mkdtemp(prefix="bmanga_ui_inventory_")
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_ui_inventory",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_ui_inventory"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _font(ImageFont, *, size: int):
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


class _DummyOp:
    def __init__(self, idname: str):
        object.__setattr__(self, "idname", idname)
        object.__setattr__(self, "attrs", {})

    def __setattr__(self, name: str, value: Any) -> None:
        self.attrs[name] = value


class _FakeLayout:
    """UILayoutの最小記録版.

    draw() 内で呼ばれる label / prop / operator などをUI項目として収集する。
    row/column/box/split は同じ記録先を共有した子レイアウトを返す。
    """

    def __init__(self, records: list[dict[str, Any]], group: str, depth: int = 0):
        object.__setattr__(self, "_records", records)
        object.__setattr__(self, "_group", group)
        object.__setattr__(self, "_depth", depth)

    def __setattr__(self, _name: str, _value: Any) -> None:
        return

    def _child(self, extra: int = 1):
        return _FakeLayout(self._records, self._group, self._depth + extra)

    def _add(self, kind: str, label: str, detail: str = "", *, icon: str = "") -> None:
        text = str(label or "").strip()
        if not text and not detail:
            return
        self._records.append(
            {
                "group": self._group,
                "depth": self._depth,
                "kind": kind,
                "label": text,
                "detail": str(detail or ""),
                "icon": str(icon or ""),
            }
        )

    def box(self, **_kwargs):
        return self._child(1)

    def row(self, **_kwargs):
        return self._child(1)

    def column(self, **_kwargs):
        return self._child(1)

    def split(self, **_kwargs):
        return self._child(1)

    def grid_flow(self, **_kwargs):
        return self._child(1)

    def label(self, text: str = "", icon: str = "", **_kwargs) -> None:
        self._add("ラベル", text or icon, icon=icon)

    def prop(self, data, prop_name: str, text: str | None = None, **_kwargs) -> None:
        label = _prop_label(data, prop_name, text)
        self._add("項目", label, prop_name)

    def prop_enum(self, data, prop_name: str, value: str, text: str = "", **_kwargs) -> None:
        label = text or f"{_prop_label(data, prop_name, None)}: {value}"
        self._add("選択肢", label, f"{prop_name}={value}")

    def operator(self, idname: str, text: str = "", icon: str = "", **_kwargs):
        label = text or _operator_label(idname)
        self._add("ボタン", label, idname, icon=icon)
        return _DummyOp(idname)

    def operator_menu_enum(self, idname: str, prop: str, text: str = "", icon: str = "", **_kwargs):
        label = text or _operator_label(idname)
        self._add("メニュー", label, f"{idname}.{prop}", icon=icon)
        return _DummyOp(idname)

    def menu(self, menu_id: str, text: str = "", icon: str = "", **_kwargs) -> None:
        label = text or _menu_label(menu_id)
        self._add("メニュー", label, menu_id, icon=icon)

    def template_list(self, listtype_name: str, list_id: str, data, propname: str, *_args, **_kwargs):
        label = _prop_label(data, propname, None)
        detail = f"{listtype_name} {list_id}".strip()
        self._add("一覧", label, detail)

    def template_ID(self, data, propname: str, **_kwargs):
        label = _prop_label(data, propname, None)
        self._add("ID選択", label, propname)

    def separator(self, **_kwargs) -> None:
        return

    def __getattr__(self, name: str):
        def _fallback(*_args, **_kwargs):
            self._add("未分類", name)
            return self._child(1)

        return _fallback


def _rna_prop_name(data, prop_name: str) -> str:
    if not prop_name or prop_name.startswith("["):
        return prop_name
    bl_rna = getattr(data, "bl_rna", None)
    props = getattr(bl_rna, "properties", None)
    if props is None:
        return prop_name
    try:
        prop = props[prop_name]
    except Exception:
        return prop_name
    return str(getattr(prop, "name", "") or prop_name)


def _prop_label(data, prop_name: str, text: str | None) -> str:
    if text is not None and text != "":
        return text
    return _rna_prop_name(data, prop_name)


def _operator_label(idname: str) -> str:
    try:
        namespace, name = idname.split(".", 1)
        op = getattr(getattr(bpy.ops, namespace), name)
        rna = op.get_rna_type()
        return str(getattr(rna, "name", "") or idname)
    except Exception:
        return idname


def _menu_label(menu_id: str) -> str:
    try:
        cls = getattr(bpy.types, menu_id)
        return str(getattr(cls, "bl_label", "") or menu_id)
    except Exception:
        return menu_id


def _set_active_object(obj) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _raster_obj_for(entry):
    from bmanga_dev_ui_inventory.operators import raster_layer_op
    from bmanga_dev_ui_inventory.utils import object_naming as on

    return on.find_object_by_bmanga_id(entry.id, kind="raster") or bpy.data.objects.get(
        raster_layer_op.raster_plane_name(entry.id)
    )


def _build_scene(context):
    from bmanga_dev_ui_inventory.operators import balloon_op, effect_line_op, text_op
    from bmanga_dev_ui_inventory.utils import gp_layer_parenting
    from bmanga_dev_ui_inventory.utils import gpencil as gp_utils
    from bmanga_dev_ui_inventory.utils import layer_hierarchy, layer_stack
    from bmanga_dev_ui_inventory.utils import object_naming as on
    from bmanga_dev_ui_inventory.utils.geom import mm_to_m

    work = context.scene.bmanga_work
    page = work.pages[0]
    coma = page.comas[0]
    page_key = layer_hierarchy.page_stack_key(page)
    coma_key = layer_hierarchy.coma_stack_key(page, coma)
    x = float(coma.rect_x_mm) + float(coma.rect_width_mm) * 0.5
    y = float(coma.rect_y_mm) + float(coma.rect_height_mm) * 0.5

    folder = work.layer_folders.add()
    folder.id = "audit_folder"
    folder.title = "監査フォルダ"
    folder.parent_key = page_key

    result = bpy.ops.bmanga.raster_layer_add(
        "EXEC_DEFAULT",
        dpi_preset="custom",
        dpi=72,
        bit_depth="gray8",
        enter_paint=False,
    )
    assert result == {"FINISHED"}, result
    raster = context.scene.bmanga_raster_layers[int(context.scene.bmanga_active_raster_layer_index)]
    raster.title = "ラスター詳細チェック"
    raster_obj = _raster_obj_for(raster)
    assert raster_obj is not None

    image = context.scene.bmanga_image_layers.add()
    image.id = "image_detail_audit"
    image.title = "画像詳細チェック"
    image.parent_kind = "page"
    image.parent_key = page_key
    image.binarize_enabled = True

    balloon = balloon_op._create_balloon_entry(
        context,
        page,
        shape="rect",
        x=x,
        y=y,
        w=45.0,
        h=28.0,
        parent_kind="coma",
        parent_key=coma_key,
    )
    balloon.rounded_corner_enabled = True
    balloon.tails.add()

    text, missing = text_op._create_text_entry(
        context,
        page,
        body="詳細設定チェック",
        x_mm=x,
        y_mm=y,
        width_mm=42.0,
        height_mm=18.0,
        parent_kind="coma",
        parent_key=coma_key,
    )
    assert not missing
    text.stroke_enabled = True
    text.parent_balloon_id = balloon.id

    gp_obj = gp_utils.ensure_master_gpencil(context.scene)
    gp_layer = gp_obj.data.layers.new("詳細GP")
    gp_layer_parenting.set_parent_key(gp_layer, page_key)
    frame = gp_utils.ensure_active_frame(gp_layer)
    assert frame is not None and frame.drawing is not None
    gp_utils.add_stroke_to_drawing(
        frame.drawing,
        [(mm_to_m(8), mm_to_m(8), 0.0), (mm_to_m(24), mm_to_m(16), 0.0)],
    )
    gp_utils.ensure_layer_material(gp_obj, gp_layer, activate=True, assign_existing=True)

    effect_obj, effect_layer = effect_line_op._create_effect_layer(
        context,
        (x, y, 38.0, 30.0),
        parent_key=coma_key,
    )
    _set_active_object(effect_obj)
    context.scene.bmanga_active_layer_kind = "effect"
    effect_layer.select = True
    effect_obj.data.layers.active = effect_layer
    effect_obj[on.PROP_KIND] = "effect"

    layer_stack.sync_layer_stack_after_data_change(context)

    return {
        "work": work,
        "page": page,
        "coma": coma,
        "folder": folder,
        "image": image,
        "raster": raster,
        "raster_obj": raster_obj,
        "balloon": balloon,
        "text": text,
        "gp_obj": gp_obj,
        "gp_layer": gp_layer,
        "effect_obj": effect_obj,
        "effect_layer": effect_layer,
    }


def _collect_draw(records: list[dict[str, Any]], group: str, draw_fn, *args) -> None:
    layout = _FakeLayout(records, group)
    try:
        draw_fn(layout, *args)
    except Exception as exc:  # noqa: BLE001
        records.append(
            {
                "group": group,
                "depth": 0,
                "kind": "描画エラー",
                "label": str(exc),
                "detail": type(exc).__name__,
                "icon": "ERROR",
            }
        )


def _collect_panel(records: list[dict[str, Any]], context, cls, group_prefix: str, *, force: bool = False) -> None:
    label = str(getattr(cls, "bl_label", cls.__name__))
    group = f"{group_prefix} / {label}"
    poll_ok = True
    if not force and hasattr(cls, "poll"):
        try:
            poll_ok = bool(cls.poll(context))
        except Exception as exc:  # noqa: BLE001
            poll_ok = False
            records.append(
                {
                    "group": group,
                    "depth": 0,
                    "kind": "pollエラー",
                    "label": str(exc),
                    "detail": type(exc).__name__,
                    "icon": "ERROR",
                }
            )
    if not poll_ok:
        records.append(
            {
                "group": group,
                "depth": 0,
                "kind": "非表示",
                "label": "この状態では表示されません",
                "detail": getattr(cls, "bl_idname", cls.__name__),
                "icon": "HIDE_ON",
            }
        )
        return
    layout = _FakeLayout(records, group)
    dummy_self = SimpleNamespace(layout=layout)
    try:
        cls.draw(dummy_self, context)
    except Exception as exc:  # noqa: BLE001
        records.append(
            {
                "group": group,
                "depth": 0,
                "kind": "描画エラー",
                "label": str(exc),
                "detail": type(exc).__name__,
                "icon": "ERROR",
            }
        )


def _registered_panel_classes():
    from bmanga_dev_ui_inventory.panels import (
        coma_camera_panel,
        export_panel,
        gpencil_panel,
        outliner_layer_panel,
        paper_panel,
        tool_panel,
        view_panel,
        work_panel,
    )

    return (
        work_panel.BMANGA_PT_work,
        work_panel.BMANGA_PT_coma_return,
        paper_panel.BMANGA_PT_paper,
        tool_panel.BMANGA_PT_tools,
        view_panel.BMANGA_PT_view,
        gpencil_panel.BMANGA_PT_layer_stack,
        outliner_layer_panel.BMANGA_PT_outliner_layers,
        coma_camera_panel.BMANGA_PT_coma_camera,
        export_panel.BMANGA_PT_export,
    )


def _collect_panels(records: list[dict[str, Any]], context) -> None:
    from bmanga_dev_ui_inventory.core.mode import MODE_COMA, MODE_PAGE, set_mode
    from bmanga_dev_ui_inventory.utils import coma_camera
    from bmanga_dev_ui_inventory.utils import page_file_scene
    from bmanga_dev_ui_inventory.panels import coma_camera_panel, gpencil_panel, work_panel

    set_mode(MODE_PAGE, context)
    original_get_prefs = gpencil_panel._get_prefs
    gpencil_panel._get_prefs = lambda: SimpleNamespace(gpencil_follow_cursor=True)
    try:
        for cls in _registered_panel_classes():
            _collect_panel(records, context, cls, "B-MANGAパネル / ページ一覧")

        work = context.scene.bmanga_work
        page = work.pages[0] if len(work.pages) else None
        original_current_role = page_file_scene.current_role
        original_page_id = str(getattr(context.scene, "bmanga_current_page_id", "") or "")
        original_overview = bool(getattr(context.scene, "bmanga_overview_mode", True))
        if page is not None:
            page_id = str(getattr(page, "id", "") or "")
            context.scene.bmanga_current_page_id = page_id
            context.scene.bmanga_overview_mode = False
            page_file_scene.current_role = lambda _context=None: (page_file_scene.ROLE_PAGE, page_id, "")
            try:
                for cls in _registered_panel_classes():
                    _collect_panel(records, context, cls, "B-MANGAパネル / ページ編集")
            finally:
                page_file_scene.current_role = original_current_role
                context.scene.bmanga_current_page_id = original_page_id
                context.scene.bmanga_overview_mode = original_overview

        set_mode(MODE_COMA, context)
        work = context.scene.bmanga_work
        page = work.pages[0]
        coma = page.comas[0]
        context.scene.bmanga_current_coma_page_id = page.id
        context.scene.bmanga_current_coma_id = coma.coma_id
        coma_camera.ensure_coma_camera_scene(context, generate_references=False)
        _collect_panel(records, context, work_panel.BMANGA_PT_coma_return, "B-MANGAパネル / コマ編集", force=True)
        _collect_panel(records, context, coma_camera_panel.BMANGA_PT_coma_camera, "B-MANGAパネル / コマ編集", force=True)
    finally:
        gpencil_panel._get_prefs = original_get_prefs

    set_mode(MODE_PAGE, context)


def _stack_item(kind: str, label: str = ""):
    return SimpleNamespace(kind=kind, label=label or kind)


def _collect_layer_stack_details(records: list[dict[str, Any]], context, targets) -> None:
    from bmanga_dev_ui_inventory.panels import gpencil_panel

    pairs = (
        ("ページ", "page", targets["page"], None),
        ("コマ", "coma", targets["coma"], None),
        ("汎用フォルダ", "layer_folder", targets["folder"], None),
        ("GP", "gp", targets["gp_layer"], targets["gp_obj"]),
        ("画像", "image", targets["image"], None),
        ("ラスター", "raster", targets["raster"], None),
        ("テキスト", "text", targets["text"], None),
    )
    for label, kind, target, obj in pairs:
        item = _stack_item(kind, label)
        resolved = {"target": target, "object": obj}
        _collect_draw(
            records,
            f"レイヤー詳細 / {label}",
            gpencil_panel.draw_stack_item_detail,
            context,
            item,
            resolved,
        )

    balloon = targets["balloon"]
    for shape in ("rect", "cloud", "custom"):
        balloon.shape = shape
        if shape == "custom":
            balloon.custom_preset_name = "監査カスタム"
        item = _stack_item("balloon", f"フキダシ {shape}")
        resolved = {"target": balloon, "object": None}
        _collect_draw(
            records,
            f"レイヤー詳細 / フキダシ / {shape}",
            gpencil_panel.draw_stack_item_detail,
            context,
            item,
            resolved,
        )

    params = context.scene.bmanga_effect_line_params
    for effect_type in ("focus", "speed", "beta_flash", "white_outline"):
        params.effect_type = effect_type
        params.start_to_coma_frame = True
        params.spacing_mode = "distance"
        params.start_shape = "cloud"
        params.end_shape = "cloud"
        params.start_rounded_corner_enabled = True
        params.end_rounded_corner_enabled = True
        params.brush_jitter_enabled = True
        params.spacing_jitter_enabled = True
        params.bundle_enabled = True
        params.fill_base_shape = True
        params.white_outline_width_jitter_enabled = True
        params.white_outline_length_jitter_enabled = True
        item = _stack_item("effect", f"効果線 {effect_type}")
        resolved = {"target": targets["effect_layer"], "object": targets["effect_obj"]}
        _collect_draw(
            records,
            f"レイヤー詳細 / 効果線 / {effect_type}",
            gpencil_panel.draw_stack_item_detail,
            context,
            item,
            resolved,
        )


def _collect_right_click_details(records: list[dict[str, Any]], context, targets) -> None:
    from bmanga_dev_ui_inventory.operators import layer_detail_op
    from bmanga_dev_ui_inventory.utils import gpencil as gp_utils
    from bmanga_dev_ui_inventory.utils import object_naming as on

    def _audit_gp_object(name: str, kind: str):
        data = gp_utils.ensure_gpencil(f"{name}_data")
        obj = bpy.data.objects.new(name, data)
        context.scene.collection.objects.link(obj)
        layer = data.layers.new("詳細")
        gp_utils.ensure_active_frame(layer)
        data.layers.active = layer
        obj[on.PROP_KIND] = kind
        obj[on.PROP_ID] = name
        obj[on.PROP_TITLE] = name
        obj[on.PROP_Z_INDEX] = 1000
        return obj

    gp_obj = _audit_gp_object("ui_inventory_gp_detail", "gp")
    effect_obj = _audit_gp_object("ui_inventory_effect_detail", "effect")

    groups = (
        ("右クリック詳細設定 / 画像", layer_detail_op._draw_image_detail, targets["image"]),
        ("右クリック詳細設定 / ラスター", layer_detail_op._draw_raster_detail, targets["raster"]),
        ("右クリック詳細設定 / フキダシ", layer_detail_op._draw_balloon_detail, targets["balloon"]),
        ("右クリック詳細設定 / テキスト", layer_detail_op._draw_text_detail, targets["text"]),
        ("右クリック詳細設定 / GP", layer_detail_op._draw_gp_detail, gp_obj),
    )
    targets["balloon"].shape = "cloud"
    for group, fn, target in groups:
        _collect_draw(records, group, fn, target)

    params = context.scene.bmanga_effect_line_params
    for effect_type in ("focus", "speed", "beta_flash", "white_outline"):
        params.effect_type = effect_type
        params.start_to_coma_frame = True
        params.spacing_mode = "distance"
        _collect_draw(
            records,
            f"右クリック詳細設定 / 効果線 / {effect_type}",
            layer_detail_op._draw_effect_detail,
            context,
            effect_obj,
        )


def _collect_legacy_status(records: list[dict[str, Any]]) -> None:
    legacy = (
        ("旧独立パネル / フキダシ", "統合先: レイヤー詳細 / 右クリック詳細設定"),
        ("旧独立パネル / テキスト", "統合先: レイヤー詳細 / 右クリック詳細設定"),
        ("旧独立パネル / 効果線", "統合先: レイヤー詳細 / 右クリック詳細設定"),
        ("旧独立パネル / 画像レイヤー", "統合先: レイヤー"),
        ("旧独立パネル / ページ一覧", "統合先: レイヤー上部のページ選択 + ビューの選択ページ"),
        ("旧独立パネル / コマ一覧", "統合先: レイヤー"),
        ("旧独立パネル / 枠線ツール", "統合先: ツール + コマ詳細"),
    )
    for group, detail in legacy:
        records.append(
            {
                "group": "統合済みパネル",
                "depth": 0,
                "kind": "未登録",
                "label": group,
                "detail": detail,
                "icon": "INFO",
            }
        )


def _record_texts(records: list[dict[str, Any]]) -> set[str]:
    texts: set[str] = set()
    for item in records:
        texts.add(str(item.get("label", "")))
        texts.add(str(item.get("detail", "")))
    return texts


def _required_labels_missing(records: list[dict[str, Any]]) -> list[str]:
    texts = _record_texts(records)
    required = (
        "作品情報",
        "プリセット",
        "レイヤー",
        "B-MANGA 階層を修復",
        "カメラプリセット",
        "ページ一覧",
        "配置 (mm)",
        "線・塗り",
        "白フチ",
        "外端形状",
        "内端形状",
        "白抜き線",
        "流線",
        "選択中: 監査フォルダ (汎用フォルダ)",
    )
    return [label for label in required if label not in texts]


def _write_json(records: list[dict[str, Any]], missing: list[str]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "bmanga_ui_inventory_visual_audit.json"
    payload = {
        "records": records,
        "missing_required_labels": missing,
        "group_counts": _group_counts(records),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _group_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in records:
        group = str(item.get("group", ""))
        counts[group] = counts.get(group, 0) + 1
    return dict(sorted(counts.items()))


def _write_image(records: list[dict[str, Any]], missing: list[str]) -> Path | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None

    width = 1900
    row_h = 24
    group_h = 30
    header_h = 110
    group_count = len(_group_counts(records))
    height = header_h + group_h * group_count + row_h * (len(records) + len(missing) + 4)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(ImageFont, size=20)
    font = _font(ImageFont, size=12)
    small = _font(ImageFont, size=11)

    draw.text((24, 18), "B-MANGA UI項目 実描画棚卸し AI目視シート", fill=(0, 0, 0), font=title_font)
    draw.text(
        (24, 52),
        f"対象: B-MANGAパネル / レイヤー詳細 / 右クリック詳細設定 / 統合済み旧パネル  項目数: {len(records)}",
        fill=(0, 0, 0),
        font=font,
    )
    status = "必須ラベル欠落なし" if not missing else f"必須ラベル欠落: {', '.join(missing)}"
    draw.text((24, 76), status, fill=(0, 120, 0) if not missing else (180, 0, 0), font=font)

    y = header_h
    current_group = None
    for item in records:
        group = str(item["group"])
        if group != current_group:
            current_group = group
            draw.rectangle((18, y, width - 18, y + group_h), fill=(226, 232, 244), outline=(150, 158, 178))
            draw.text((28, y + 8), group, fill=(0, 0, 0), font=font)
            y += group_h
        kind = str(item["kind"])
        is_error = "エラー" in kind or kind == "描画エラー"
        fill = (255, 235, 235) if is_error else (248, 250, 253)
        draw.rectangle((18, y, width - 18, y + row_h), fill=fill, outline=(220, 225, 232))
        indent = int(item.get("depth", 0)) * 18
        draw.text((30, y + 6), kind, fill=(160, 0, 0) if is_error else (52, 64, 84), font=small)
        draw.text((150 + indent, y + 6), str(item.get("label", "")), fill=(0, 0, 0), font=small)
        draw.text((760, y + 6), str(item.get("detail", ""))[:150], fill=(75, 75, 75), font=small)
        y += row_h

    if missing:
        draw.rectangle((18, y, width - 18, y + group_h), fill=(255, 225, 225), outline=(180, 0, 0))
        draw.text((28, y + 8), "必須ラベル欠落", fill=(160, 0, 0), font=font)
        y += group_h
        for label in missing:
            draw.text((40, y + 6), label, fill=(160, 0, 0), font=small)
            y += row_h

    path = OUT_DIR / "bmanga_ui_inventory_visual_audit.png"
    image.save(path)
    return path


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_ui_inventory_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "UiInventory.bmanga"))
        assert result == {"FINISHED"}, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert result == {"FINISHED"}, result
        context = bpy.context
        targets = _build_scene(context)
        records: list[dict[str, Any]] = []
        _collect_panels(records, context)
        _collect_layer_stack_details(records, context, targets)
        _collect_right_click_details(records, context, targets)
        _collect_legacy_status(records)
        missing = _required_labels_missing(records)
        json_path = _write_json(records, missing)
        image_path = _write_image(records, missing)
        print(
            "BMANGA_UI_INVENTORY_VISUAL_AUDIT_OK "
            f"json={json_path} image={image_path} items={len(records)} missing={len(missing)}"
        )
        assert not any("エラー" in str(item["kind"]) for item in records), records
        assert not missing, missing
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
        os._exit(0)
    except Exception:
        import traceback

        traceback.print_exc()
        os._exit(1)
