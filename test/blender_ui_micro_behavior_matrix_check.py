"""Blender実機用: B-Name UI項目の微細挙動マトリクス監査."""

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
    os.environ.get("BNAME_UI_MICRO_OUT", "")
    or tempfile.mkdtemp(prefix="bname_ui_micro_")
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_ui_micro",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_ui_micro"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _mark(label: str) -> None:
    if os.environ.get("BNAME_UI_MICRO_VERBOSE"):
        print(f"BNAME_UI_MICRO_STEP {label}", flush=True)
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with (OUT_DIR / "steps.txt").open("a", encoding="utf-8") as fh:
            fh.write(f"{label}\n")
    except Exception:
        pass


class _DummyOp:
    def __init__(self, idname: str):
        object.__setattr__(self, "idname", idname)
        object.__setattr__(self, "attrs", {})

    def __setattr__(self, name: str, value: Any) -> None:
        self.attrs[name] = value


class _RecordingLayout:
    def __init__(self, records: list[dict[str, Any]], group: str, depth: int = 0, enabled: bool = True):
        object.__setattr__(self, "_records", records)
        object.__setattr__(self, "_group", group)
        object.__setattr__(self, "_depth", depth)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "operator_context", "")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"enabled", "operator_context", "scale_y"}:
            object.__setattr__(self, name, value)

    def _child(self):
        return _RecordingLayout(
            self._records,
            self._group,
            self._depth + 1,
            bool(getattr(self, "enabled", True)),
        )

    def box(self, **_kwargs):
        return self._child()

    def row(self, **_kwargs):
        return self._child()

    def column(self, **_kwargs):
        return self._child()

    def split(self, **_kwargs):
        return self._child()

    def grid_flow(self, **_kwargs):
        return self._child()

    def label(self, **_kwargs) -> None:
        return

    def separator(self, **_kwargs) -> None:
        return

    def prop(self, data, prop_name: str, text: str | None = None, **_kwargs) -> None:
        ok, value = _read_prop(data, prop_name)
        if not ok or not isinstance(value, bool):
            return
        self._records.append(
            {
                "group": self._group,
                "label": _prop_label(data, prop_name, text),
                "target": data,
                "prop": prop_name,
                "enabled": bool(getattr(self, "enabled", True)),
                "depth": self._depth,
            }
        )

    def prop_enum(self, *_args, **_kwargs) -> None:
        return

    def operator(self, idname: str, **_kwargs):
        return _DummyOp(idname)

    def operator_menu_enum(self, idname: str, **_kwargs):
        return _DummyOp(idname)

    def menu(self, *_args, **_kwargs) -> None:
        return

    def template_list(self, *_args, **_kwargs) -> None:
        return

    def template_ID(self, *_args, **_kwargs) -> None:
        return

    def __getattr__(self, _name: str):
        def _fallback(*_args, **_kwargs):
            return self._child()

        return _fallback


def _custom_prop_name(prop_name: str) -> str:
    if prop_name.startswith('["') and prop_name.endswith('"]'):
        return prop_name[2:-2]
    return ""


def _read_prop(target, prop_name: str) -> tuple[bool, Any]:
    custom = _custom_prop_name(prop_name)
    try:
        if custom:
            return True, target[custom]
        return True, getattr(target, prop_name)
    except Exception:  # noqa: BLE001
        return False, None


def _write_prop(target, prop_name: str, value) -> bool:
    custom = _custom_prop_name(prop_name)
    try:
        if custom:
            target[custom] = value
        else:
            setattr(target, prop_name, value)
        return True
    except Exception:  # noqa: BLE001
        return False


def _rna_prop_name(data, prop_name: str) -> str:
    custom = _custom_prop_name(prop_name)
    if custom:
        return custom
    bl_rna = getattr(data, "bl_rna", None)
    props = getattr(bl_rna, "properties", None)
    if props is None:
        return prop_name
    try:
        prop = props[prop_name]
    except Exception:  # noqa: BLE001
        return prop_name
    return str(getattr(prop, "name", "") or prop_name)


def _prop_label(data, prop_name: str, text: str | None) -> str:
    if text is not None and text != "":
        return text
    return _rna_prop_name(data, prop_name)


def _set_active_object(obj) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _raster_obj_for(entry):
    from bname_dev_ui_micro.operators import raster_layer_op
    from bname_dev_ui_micro.utils import object_naming as on

    return on.find_object_by_bname_id(entry.id, kind="raster") or bpy.data.objects.get(
        raster_layer_op.raster_plane_name(entry.id)
    )


def _create_scene(context):
    from bname_dev_ui_micro.operators import balloon_op, effect_line_op, text_op
    from bname_dev_ui_micro.utils import gp_layer_parenting
    from bname_dev_ui_micro.utils import gpencil as gp_utils
    from bname_dev_ui_micro.utils import layer_hierarchy, layer_stack
    from bname_dev_ui_micro.utils import object_naming as on
    from bname_dev_ui_micro.utils.geom import mm_to_m

    work = context.scene.bname_work
    page = work.pages[0]
    if len(page.comas) < 1:
        result = bpy.ops.bname.coma_add()
        assert result == {"FINISHED"}, result
    coma = page.comas[0]
    page_key = layer_hierarchy.page_stack_key(page)
    coma_key = layer_hierarchy.coma_stack_key(page, coma)
    x = float(coma.rect_x_mm) + float(coma.rect_width_mm) * 0.5
    y = float(coma.rect_y_mm) + float(coma.rect_height_mm) * 0.5

    folder = work.layer_folders.add()
    folder.id = "micro_folder"
    folder.title = "監査フォルダ"
    folder.parent_key = page_key

    result = bpy.ops.bname.raster_layer_add(
        "EXEC_DEFAULT",
        dpi_preset="custom",
        dpi=72,
        bit_depth="gray8",
        enter_paint=False,
    )
    assert result == {"FINISHED"}, result
    raster = context.scene.bname_raster_layers[int(context.scene.bname_active_raster_layer_index)]
    raster.title = "監査ラスター"
    raster.parent_kind = "coma"
    raster.parent_key = coma_key
    raster_obj = _raster_obj_for(raster)
    assert raster_obj is not None

    image = context.scene.bname_image_layers.add()
    image.id = "micro_image"
    image.title = "監査画像"
    image.parent_kind = "coma"
    image.parent_key = coma_key
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

    balloon_target = balloon_op._create_balloon_entry(
        context,
        page,
        shape="rect",
        x=x + 55.0,
        y=y,
        w=35.0,
        h=22.0,
        parent_kind="coma",
        parent_key=coma_key,
    )
    balloon_target.id = "micro_tail_target"

    text, missing = text_op._create_text_entry(
        context,
        page,
        body="詳細設定チェック",
        speaker_type="normal",
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
    gp_layer = gp_obj.data.layers.new("監査GP")
    gp_layer_parenting.set_parent_key(gp_layer, coma_key)
    frame = gp_utils.ensure_active_frame(gp_layer)
    assert frame is not None and frame.drawing is not None
    gp_utils.add_stroke_to_drawing(
        frame.drawing,
        [(mm_to_m(8), mm_to_m(8), 0.0), (mm_to_m(24), mm_to_m(16), 0.0)],
    )
    gp_mat = gp_utils.ensure_layer_material(gp_obj, gp_layer, activate=True, assign_existing=True)

    effect_obj, effect_layer = effect_line_op._create_effect_layer(
        context,
        (x, y, 38.0, 30.0),
        parent_key=coma_key,
    )
    _set_active_object(effect_obj)
    context.scene.bname_active_layer_kind = "effect"
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
        "balloon_target": balloon_target,
        "text": text,
        "gp_obj": gp_obj,
        "gp_layer": gp_layer,
        "gp_mat": gp_mat,
        "effect_obj": effect_obj,
        "effect_layer": effect_layer,
    }


def _stack_index_for_kind(kind: str, *, label_contains: str = "") -> int:
    from bname_dev_ui_micro.utils import layer_stack

    stack = layer_stack.sync_layer_stack(bpy.context)
    assert stack is not None
    for index, item in enumerate(stack):
        if str(getattr(item, "kind", "") or "") != kind:
            continue
        if label_contains and label_contains not in str(getattr(item, "label", "") or ""):
            continue
        return index
    raise AssertionError(f"レイヤー一覧に対象がありません: {kind} {label_contains}")


def _select_kind(kind: str, *, label_contains: str = ""):
    from bname_dev_ui_micro.utils import layer_stack

    index = _stack_index_for_kind(kind, label_contains=label_contains)
    stack = layer_stack.sync_layer_stack(bpy.context, preserve_active_index=True)
    assert stack is not None
    layer_stack.clear_all_selection(bpy.context)
    layer_stack.set_item_selected(bpy.context, stack[index], True)
    assert layer_stack.select_stack_index(bpy.context, index)
    return layer_stack.active_stack_item(bpy.context)


def _select_balloon_id(balloon_id: str):
    from bname_dev_ui_micro.utils import layer_stack

    stack = layer_stack.sync_layer_stack(bpy.context)
    assert stack is not None
    for index, item in enumerate(stack):
        if str(getattr(item, "kind", "") or "") != "balloon":
            continue
        if str(balloon_id) in str(getattr(item, "key", "") or ""):
            layer_stack.clear_all_selection(bpy.context)
            layer_stack.set_item_selected(bpy.context, item, True)
            assert layer_stack.select_stack_index(bpy.context, index)
            return item
    raise AssertionError(f"フキダシが見つかりません: {balloon_id}")


def _menu_state() -> dict[str, bool]:
    from bname_dev_ui_micro.ui import context_menu

    items = context_menu.selection_command_items(bpy.context)
    for item in items:
        op_id = str(item.get("operator", "") or "")
        namespace, name = op_id.split(".", 1)
        assert getattr(getattr(bpy.ops, namespace), name, None) is not None, op_id
    return {str(item.get("label", "")): bool(item.get("enabled", False)) for item in items}


def _stack_count(kind: str) -> int:
    from bname_dev_ui_micro.utils import layer_stack

    stack = layer_stack.sync_layer_stack(bpy.context)
    return sum(1 for item in stack if str(getattr(item, "kind", "") or "") == kind)


def _select_effect_object(targets) -> None:
    _set_active_object(targets["effect_obj"])
    targets["effect_obj"].data.layers.active = targets["effect_layer"]
    targets["effect_layer"].select = True
    bpy.context.scene.bname_active_layer_kind = "effect"


def _check_right_click_matrix(targets) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    expected_labels = {
        "page": {"詳細設定": True, "コピー": False, "貼り付け": False, "複製": True, "リンク複製": False, "選択レイヤーをリンク": False, "削除": True},
        "coma": {"詳細設定": True, "コピー": False, "貼り付け": False, "複製": True, "リンク複製": False, "選択レイヤーをリンク": False, "削除": True},
        "gp": {"詳細設定": True, "コピー": True, "貼り付け": False, "複製": True, "リンク複製": False, "選択レイヤーをリンク": False, "削除": True},
        "effect": {"詳細設定": True, "コピー": True, "貼り付け": False, "複製": True, "リンク複製": True, "選択レイヤーをリンク": False, "削除": True},
        "raster": {"詳細設定": True, "コピー": True, "貼り付け": False, "複製": True, "リンク複製": False, "選択レイヤーをリンク": False, "削除": True},
        "image": {"詳細設定": True, "コピー": False, "貼り付け": False, "複製": True, "リンク複製": False, "選択レイヤーをリンク": False, "削除": True},
        "balloon": {
            "詳細設定": True,
            "コピー": True,
            "貼り付け": False,
            "複製": True,
            "リンク複製": False,
            "選択レイヤーをリンク": False,
            "しっぽをコピー": False,
            "しっぽを貼り付け": False,
            "削除": True,
        },
        "text": {"詳細設定": True, "コピー": True, "貼り付け": False, "複製": True, "リンク複製": False, "選択レイヤーをリンク": False, "削除": True},
    }
    for kind, expected in expected_labels.items():
        _mark(f"right_click_state_{kind}")
        _select_kind(kind)
        if kind == "effect":
            _select_effect_object(targets)
        state = _menu_state()
        ok = all(state.get(label) is enabled for label, enabled in expected.items())
        results.append({"group": "右クリック", "label": f"{kind} 初期状態", "ok": ok, "state": state})

    for kind in ("gp", "effect", "raster", "balloon", "text"):
        _mark(f"right_click_copy_{kind}")
        _select_kind(kind)
        if kind == "effect":
            _select_effect_object(targets)
        result = bpy.ops.bname.layer_clipboard_copy("EXEC_DEFAULT")
        state = _menu_state()
        ok = result == {"FINISHED"} and state.get("貼り付け") is True
        results.append({"group": "右クリック", "label": f"{kind} コピー後に貼り付け有効", "ok": ok, "state": state})

    _mark("right_click_paste_text")
    _select_kind("text")
    before = _stack_count("text")
    result = bpy.ops.bname.layer_clipboard_paste("EXEC_DEFAULT")
    after = _stack_count("text")
    results.append(
        {
            "group": "右クリック",
            "label": "テキスト 貼り付けで複製",
            "ok": result == {"FINISHED"} and after == before + 1,
            "before": before,
            "after": after,
        }
    )

    _mark("right_click_duplicate_image")
    _select_kind("image")
    before = _stack_count("image")
    result = bpy.ops.bname.layer_stack_duplicate("EXEC_DEFAULT")
    after = _stack_count("image")
    results.append(
        {
            "group": "右クリック",
            "label": "画像 複製",
            "ok": result == {"FINISHED"} and after == before + 1,
            "before": before,
            "after": after,
        }
    )
    _mark("right_click_delete_image")
    result = bpy.ops.bname.layer_stack_delete("EXEC_DEFAULT")
    deleted = _stack_count("image")
    results.append(
        {
            "group": "右クリック",
            "label": "画像 削除",
            "ok": result == {"FINISHED"} and deleted == before,
            "before": after,
            "after": deleted,
        }
    )

    _mark("right_click_tail")
    source = targets["balloon"]
    source.tails.clear()
    source.tails.add()
    _mark("right_click_tail_select_source")
    _select_balloon_id(str(source.id))
    state = _menu_state()
    _mark(f"right_click_tail_poll_{bpy.ops.bname.balloon_tail_clipboard_copy.poll()}")
    _mark("right_click_tail_copy")
    result = bpy.ops.bname.balloon_tail_clipboard_copy("EXEC_DEFAULT")
    _mark("right_click_tail_select_target")
    _select_balloon_id(str(targets["balloon_target"].id))
    paste_state = _menu_state()
    before = len(targets["balloon_target"].tails)
    _mark("right_click_tail_paste")
    paste_result = bpy.ops.bname.balloon_tail_clipboard_paste("EXEC_DEFAULT")
    _mark("right_click_tail_done")
    after = len(targets["balloon_target"].tails)
    results.append(
        {
            "group": "右クリック",
            "label": "フキダシしっぽ コピー/貼り付け",
            "ok": (
                state.get("しっぽをコピー") is True
                and result == {"FINISHED"}
                and paste_state.get("しっぽを貼り付け") is True
                and paste_result == {"FINISHED"}
                and after == before + 1
            ),
            "state": paste_state,
            "before": before,
            "after": after,
        }
    )

    _mark("right_click_link_effect")
    _select_kind("effect")
    _select_effect_object(targets)
    before = _stack_count("effect")
    result = bpy.ops.bname.effect_line_create_linked("EXEC_DEFAULT")
    after = _stack_count("effect")
    results.append(
        {
            "group": "右クリック",
            "label": "効果線 リンク複製",
            "ok": result == {"FINISHED"} and after == before + 1,
            "before": before,
            "after": after,
        }
    )
    return results


def _collect_draw_props(records: list[dict[str, Any]], group: str, draw_fn, *args) -> None:
    layout = _RecordingLayout(records, group)
    draw_fn(layout, *args)


def _stack_item(kind: str, label: str = ""):
    return SimpleNamespace(kind=kind, label=label or kind)


def _collect_detail_props(records: list[dict[str, Any]], context, targets) -> None:
    from bname_dev_ui_micro.operators import layer_detail_op
    from bname_dev_ui_micro.panels import gpencil_panel

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
        _collect_draw_props(
            records,
            f"レイヤー詳細 / {label}",
            gpencil_panel.draw_stack_item_detail,
            context,
            _stack_item(kind, label),
            {"target": target, "object": obj},
        )

    balloon = targets["balloon"]
    for shape in ("rect", "cloud", "custom"):
        balloon.shape = shape
        if shape == "rect":
            balloon.rounded_corner_enabled = True
        if shape == "custom":
            balloon.custom_preset_name = "監査カスタム"
        _collect_draw_props(
            records,
            f"レイヤー詳細 / フキダシ / {shape}",
            gpencil_panel.draw_stack_item_detail,
            context,
            _stack_item("balloon", f"フキダシ {shape}"),
            {"target": balloon, "object": None},
        )

    params = context.scene.bname_effect_line_params
    for effect_type in ("focus", "speed", "beta_flash", "white_outline"):
        params.effect_type = effect_type
        params.start_to_coma_frame = False
        params.start_shape = "rect"
        params.end_shape = "rect"
        params.start_rounded_corner_enabled = True
        params.end_rounded_corner_enabled = True
        params.brush_jitter_enabled = True
        params.length_jitter_enabled = True
        params.spacing_mode = "distance"
        params.spacing_jitter_enabled = True
        params.bundle_enabled = True
        params.fill_base_shape = True
        params.white_outline_width_jitter_enabled = True
        params.white_outline_length_jitter_enabled = True
        _collect_draw_props(
            records,
            f"レイヤー詳細 / 効果線 / {effect_type}",
            gpencil_panel.draw_stack_item_detail,
            context,
            _stack_item("effect", f"効果線 {effect_type}"),
            {"target": targets["effect_layer"], "object": targets["effect_obj"]},
        )
        if effect_type == "focus":
            params.start_to_coma_frame = True
            params.start_frame_density_basis = "rounded_frame"
            _collect_draw_props(
                records,
                "レイヤー詳細 / 効果線 / focus / コマ枠始点",
                gpencil_panel.draw_stack_item_detail,
                context,
                _stack_item("effect", "効果線 focus"),
                {"target": targets["effect_layer"], "object": targets["effect_obj"]},
            )

    targets["balloon"].shape = "cloud"
    for group, fn, target in (
        ("右クリック詳細 / 画像", layer_detail_op._draw_image_detail, targets["image"]),
        ("右クリック詳細 / ラスター", layer_detail_op._draw_raster_detail, targets["raster"]),
        ("右クリック詳細 / フキダシ", layer_detail_op._draw_balloon_detail, targets["balloon"]),
        ("右クリック詳細 / テキスト", layer_detail_op._draw_text_detail, targets["text"]),
        ("右クリック詳細 / GP", layer_detail_op._draw_gp_detail, targets["gp_obj"]),
    ):
        _collect_draw_props(records, group, fn, target)

    for effect_type in ("focus", "speed", "beta_flash", "white_outline"):
        params.effect_type = effect_type
        _collect_draw_props(
            records,
            f"右クリック詳細 / 効果線 / {effect_type}",
            layer_detail_op._draw_effect_detail,
            context,
            targets["effect_obj"],
        )


def _make_background(context, name: str, *, kind: str, page_id: str = ""):
    cam = context.scene.camera
    assert cam is not None
    image = bpy.data.images.new(name, width=16, height=16)
    image["bname_coma_camera_ref"] = True
    image["bname_kind"] = kind
    image["bname_page_id"] = page_id
    if kind == "name":
        image["bname_full_page_mask"] = True
    bg = cam.data.background_images.new()
    bg.image = image
    bg.show_background_image = True
    return bg


def _force_coma_file_mode(context) -> None:
    from bname_dev_ui_micro.core.mode import MODE_COMA, set_mode

    work = context.scene.bname_work
    page = work.pages[0]
    coma = page.comas[0]
    work_dir = Path(work.work_dir)
    coma_dir = work_dir / page.id / coma.coma_id
    coma_dir.mkdir(parents=True, exist_ok=True)
    coma_path = coma_dir / f"{coma.coma_id}.blend"
    if not bpy.data.filepath or Path(bpy.data.filepath).resolve() != coma_path.resolve():
        result = bpy.ops.wm.save_as_mainfile(filepath=str(coma_path))
        assert "FINISHED" in result, result
    set_mode(MODE_COMA, context)
    context.scene.bname_current_coma_page_id = page.id
    context.scene.bname_current_coma_id = coma.coma_id


def _collect_coma_panel_props(records: list[dict[str, Any]], context) -> None:
    from bname_dev_ui_micro.panels import coma_camera_panel, work_panel
    from bname_dev_ui_micro.utils import coma_camera

    _force_coma_file_mode(context)
    work = context.scene.bname_work
    page = work.pages[0]
    coma = page.comas[0]
    coma_camera.ensure_coma_camera_scene(context, generate_references=False)
    _make_background(context, "ページ画像_現在.png", kind="name", page_id=page.id)
    _make_background(context, "ページ画像_別ページ.png", kind="name", page_id="p9999")
    _make_background(context, "下絵_コマ.png", kind="koma")
    _make_background(context, "ハッチング間隔.png", kind="koma")

    for cls, group in (
        (work_panel.BNAME_PT_coma_return, "コマ編集B-Nameパネル / ページ一覧に戻る"),
        (coma_camera_panel.BNAME_PT_coma_camera, "コマ編集B-Nameパネル / カメラ"),
    ):
        layout = _RecordingLayout(records, group)
        dummy = SimpleNamespace(layout=layout)
        cls.draw(dummy, context)


def _unique_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[int, str, str]] = set()
    out: list[dict[str, Any]] = []
    for record in records:
        target = record["target"]
        try:
            pointer = int(target.as_pointer())
        except Exception:  # noqa: BLE001
            pointer = id(target)
        key = (pointer, str(record["prop"]), str(record["group"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out


def _toggle_record(record: dict[str, Any]) -> dict[str, Any]:
    ok, before = _read_prop(record["target"], record["prop"])
    if not ok or not isinstance(before, bool):
        return {**_record_summary(record), "ok": False, "error": "値を読めません"}
    if not _write_prop(record["target"], record["prop"], not before):
        return {**_record_summary(record), "ok": False, "error": "値を書けません"}
    ok, after = _read_prop(record["target"], record["prop"])
    restored = _write_prop(record["target"], record["prop"], before)
    return {
        **_record_summary(record),
        "ok": ok and after is (not before) and restored,
        "before": before,
        "after": after,
        "restored": restored,
    }


def _record_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "group": str(record["group"]),
        "label": str(record["label"]),
        "prop": str(record["prop"]),
        "enabled": bool(record.get("enabled", True)),
    }


def _check_bool_controls(context, targets) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    _collect_detail_props(records, context, targets)
    _collect_coma_panel_props(records, context)
    results = [_toggle_record(record) for record in _unique_records(records)]
    labels = {f"{item['group']} / {item['label']}" for item in results}
    required_fragments = (
        "画像 / 左右反転",
        "画像 / 上下反転",
        "画像 / 表示",
        "画像 / ロック",
        "画像 / 2値化",
        "ラスター / 表示",
        "ラスター / ロック",
        "フキダシ / 角丸",
        "フキダシ / 水平反転",
        "フキダシ / 垂直反転",
        "テキスト / 太字",
        "テキスト / 斜体",
        "テキスト / 白フチ",
        "ページ / 表示",
        "コマ / 表示",
        "コマ / 自動くり抜き",
        "レイヤー詳細 / コマ / 枠線を表示",
        "コマ編集B-Nameパネル / ページ一覧に戻る / フィット",
        "コマ編集B-Nameパネル / カメラ / 背景を透過",
        "コマ編集B-Nameパネル / カメラ / 魚眼モード",
        "コマ編集B-Nameパネル / カメラ / 縮小モード",
        "コマ編集B-Nameパネル / カメラ / ハッチング間隔を表示",
    )
    missing = [fragment for fragment in required_fragments if not any(fragment in label for label in labels)]
    results.append({"group": "必須項目", "label": "主要チェックボックス検出", "ok": not missing, "missing": missing})
    return results


def _page_backgrounds(context):
    return [
        bg
        for bg in context.scene.camera.data.background_images
        if getattr(bg, "image", None) is not None and str(bg.image.get("bname_kind", "")) == "name"
    ]


def _koma_backgrounds(context):
    return [
        bg
        for bg in context.scene.camera.data.background_images
        if getattr(bg, "image", None) is not None
        and str(bg.image.get("bname_kind", "")) == "koma"
        and "コマ" in getattr(bg.image, "name", "")
    ]


def _check_coma_camera_side_effects(context) -> list[dict[str, Any]]:
    from bname_dev_ui_micro.core.mode import get_mode
    from bname_dev_ui_micro.utils import coma_camera

    _mark("coma_camera_start")
    scene = context.scene
    _mark(f"coma_camera_state_mode_{get_mode(context)}_camera_{getattr(getattr(scene, 'camera', None), 'type', '')}")
    settings = scene.bname_coma_camera_settings
    results: list[dict[str, Any]] = []

    _mark("coma_camera_white")
    settings.white_background = False
    results.append({
        "group": "コマ編集B-Nameパネル",
        "label": "背景を透過 OFF",
        "ok": scene.render.film_transparent is False,
    })
    settings.white_background = True
    results.append({
        "group": "コマ編集B-Nameパネル",
        "label": "背景を透過 ON",
        "ok": scene.render.film_transparent is True,
    })

    _mark("coma_camera_opacity")
    settings.name_bg_images_opacity = 0.32
    page_alphas = [round(float(bg.alpha), 2) for bg in _page_backgrounds(context)]
    koma_alphas_before = [round(float(bg.alpha), 2) for bg in _koma_backgrounds(context)]
    settings.koma_bg_images_opacity = 0.74
    koma_alphas_after = [round(float(bg.alpha), 2) for bg in _koma_backgrounds(context)]
    results.append({
        "group": "コマ編集B-Nameパネル",
        "label": "ページ画像/下絵_コマ 不透明度が分離",
        "ok": all(alpha == 0.32 for alpha in page_alphas)
        and all(alpha != 0.32 for alpha in koma_alphas_before)
        and all(alpha == 0.74 for alpha in koma_alphas_after),
        "page": page_alphas,
        "koma": koma_alphas_after,
    })

    _mark("coma_camera_scale")
    settings.bg_images_scale = 1.35
    results.append({
        "group": "コマ編集B-Nameパネル",
        "label": "ページ画像のスケール",
        "ok": all(abs(float(bg.scale) - 1.35) < 0.01 for bg in _page_backgrounds(context)),
    })

    _mark("coma_camera_show_all")
    settings.name_visible = True
    settings.name_show_all_pages = False
    coma_camera.set_page_reference_visibility(context, show_all=False)
    page_vis_current = [bool(bg.show_background_image) for bg in _page_backgrounds(context)]
    settings.name_show_all_pages = True
    page_vis_all = [bool(bg.show_background_image) for bg in _page_backgrounds(context)]
    results.append({
        "group": "コマ編集B-Nameパネル",
        "label": "全ページも表示",
        "ok": page_vis_current.count(True) == 1 and all(page_vis_all),
        "current": page_vis_current,
        "all": page_vis_all,
    })

    _mark("coma_camera_toggle_name")
    _mark(f"coma_camera_toggle_name_poll_{bpy.ops.bname.coma_camera_toggle_name_backgrounds.poll()}")
    bpy.ops.bname.coma_camera_toggle_name_backgrounds("EXEC_DEFAULT")
    hidden = [bool(bg.show_background_image) for bg in _page_backgrounds(context)]
    bpy.ops.bname.coma_camera_toggle_name_backgrounds("EXEC_DEFAULT")
    shown = [bool(bg.show_background_image) for bg in _page_backgrounds(context)]
    results.append({
        "group": "コマ編集B-Nameパネル",
        "label": "ページ画像 表示/非表示ボタン",
        "ok": not any(hidden) and all(shown),
    })

    _mark("coma_camera_toggle_koma")
    bpy.ops.bname.coma_camera_toggle_koma_backgrounds("EXEC_DEFAULT")
    koma_hidden = [bool(bg.show_background_image) for bg in _koma_backgrounds(context)]
    bpy.ops.bname.coma_camera_toggle_koma_backgrounds("EXEC_DEFAULT")
    koma_shown = [bool(bg.show_background_image) for bg in _koma_backgrounds(context)]
    results.append({
        "group": "コマ編集B-Nameパネル",
        "label": "下絵_コマ 表示/非表示ボタン",
        "ok": not any(koma_hidden) and all(koma_shown),
    })

    _mark("coma_camera_depth")
    settings.koma_depth = True
    depths_back = [str(bg.display_depth) for bg in _koma_backgrounds(context)]
    settings.koma_depth = False
    depths_front = [str(bg.display_depth) for bg in _koma_backgrounds(context)]
    results.append({
        "group": "コマ編集B-Nameパネル",
        "label": "コマを後ろにする",
        "ok": all(depth == "BACK" for depth in depths_back) and all(depth == "FRONT" for depth in depths_front),
    })

    _mark("coma_camera_reduction")
    original = (int(scene.render.resolution_x), int(scene.render.resolution_y))
    scene.bname_coma_camera_reduction_mode = True
    scene.bname_coma_camera_preview_scale_percentage = 25.0
    reduced = (int(scene.render.resolution_x), int(scene.render.resolution_y))
    scene.bname_coma_camera_reduction_mode = False
    restored = (int(scene.render.resolution_x), int(scene.render.resolution_y))
    results.append({
        "group": "コマ編集B-Nameパネル",
        "label": "縮小モード",
        "ok": reduced[0] < original[0] and reduced[1] < original[1] and restored == original,
        "original": original,
        "reduced": reduced,
        "restored": restored,
    })

    _mark("coma_camera_fisheye")
    scene.bname_coma_camera_fisheye_layout_mode = True
    fisheye = (
        str(getattr(scene.camera.data, "type", "")),
        int(scene.render.resolution_x),
        int(scene.render.resolution_y),
    )
    scene.bname_coma_camera_fisheye_layout_mode = False
    normal = str(getattr(scene.camera.data, "type", ""))
    results.append({
        "group": "コマ編集B-Nameパネル",
        "label": "魚眼モード",
        "ok": fisheye[0] == "PANO" and fisheye[1] == fisheye[2] and normal == "PERSP",
        "fisheye": fisheye,
        "normal": normal,
    })
    _mark("coma_camera_done")
    return results


def _write_results(results: list[dict[str, Any]]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "bname_ui_micro_behavior_matrix.json"
    serializable = []
    for item in results:
        serializable.append({k: v for k, v in item.items() if k != "target"})
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_ui_micro_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "UiMicro.bname"))
        assert result == {"FINISHED"}, result
        context = bpy.context
        _mark("scene")
        targets = _create_scene(context)
        _mark("right_click")
        results: list[dict[str, Any]] = []
        results.extend(_check_right_click_matrix(targets))
        _mark("bool_controls")
        results.extend(_check_bool_controls(context, targets))
        _mark("coma_camera")
        results.extend(_check_coma_camera_side_effects(context))
        _mark("write")
        _write_results(results)
        failures = [item for item in results if not bool(item.get("ok", False))]
        _mark("before_final_print")
        print(f"BNAME_UI_MICRO_BEHAVIOR_MATRIX_OK items={len(results)} failures={len(failures)} out={OUT_DIR}")
        _mark("after_final_print")
        assert not failures, json.dumps(failures[:20], ensure_ascii=False, indent=2)
    finally:
        if mod is not None:
            try:
                _mark("cleanup_unregister")
                mod.unregister()
                _mark("cleanup_unregistered")
            except Exception:
                pass
        _mark("cleanup_factory_settings")
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _mark("cleanup_temp")
        shutil.rmtree(temp_root, ignore_errors=True)
        _mark("cleanup_done")


if __name__ == "__main__":
    exit_code = 0
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        exit_code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
