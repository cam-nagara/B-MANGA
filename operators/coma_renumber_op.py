"""コマ ID を順番通り (c01, c02, ...) に振り直す operator.

枠線カット等で coma_id に飛び番が出たとき、ユーザー操作で順番通りに
リネームする。``BMangaComaEntry.id`` / ``BMangaComaEntry.coma_id`` の両方を
更新し、Outliner Collection 名 (mirror 経由) も追従する。

注意: 物理ファイル名 (cNN.blend / cNN フォルダ) はリネームしない。
ファイル整合は別途ユーザー操作で行う想定。
"""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..utils import log

_logger = log.get_logger(__name__)
_TEMP_KEY_PREFIX = "__coma_renumber_tmp__"


def _format_coma_id(index: int) -> str:
    """1 → "c01" のように 2 桁ゼロパディング (3 桁以上は素直にそのまま)."""
    if index < 100:
        return f"c{index:02d}"
    return f"c{index:d}"


def _coma_bounds(panel) -> tuple[float, float, float, float]:
    if str(getattr(panel, "shape_type", "") or "") == "rect":
        x = float(getattr(panel, "rect_x_mm", 0.0) or 0.0)
        y = float(getattr(panel, "rect_y_mm", 0.0) or 0.0)
        w = float(getattr(panel, "rect_width_mm", 0.0) or 0.0)
        h = float(getattr(panel, "rect_height_mm", 0.0) or 0.0)
        return x, y, x + w, y + h
    points = [
        (float(getattr(v, "x_mm", 0.0) or 0.0), float(getattr(v, "y_mm", 0.0) or 0.0))
        for v in getattr(panel, "vertices", []) or []
    ]
    if not points:
        return 0.0, 0.0, 0.0, 0.0
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _reading_order_indices(page, read_direction: str) -> list[int]:
    comas = getattr(page, "comas", None)
    if comas is None:
        return []
    items = []
    for index, coma in enumerate(comas):
        left, bottom, right, top = _coma_bounds(coma)
        width = max(0.0, right - left)
        height = max(0.1, top - bottom)
        items.append(
            {
                "index": index,
                "left": left,
                "center_x": left + width * 0.5,
                "top": top,
                "height": height,
            }
        )
    if len(items) <= 1:
        return [item["index"] for item in items]
    heights = sorted(item["height"] for item in items)
    row_tolerance = max(2.0, heights[len(heights) // 2] * 0.35)
    rows: list[dict[str, object]] = []
    for item in sorted(items, key=lambda value: (-value["top"], value["left"])):
        for row in rows:
            if abs(float(item["top"]) - float(row["top"])) <= row_tolerance:
                row["items"].append(item)  # type: ignore[index, union-attr]
                row["top"] = max(float(row["top"]), float(item["top"]))
                break
        else:
            rows.append({"top": float(item["top"]), "items": [item]})
    right_to_left = str(read_direction or "left") == "left"
    ordered: list[int] = []
    for row in sorted(rows, key=lambda value: -float(value["top"])):
        row_items = list(row["items"])  # type: ignore[arg-type]
        row_items.sort(
            key=lambda value: float(value["center_x"]),
            reverse=right_to_left,
        )
        ordered.extend(int(item["index"]) for item in row_items)
    return ordered


def _move_comas_to_order(page, ordered_indices: list[int]) -> None:
    current = list(range(len(getattr(page, "comas", []) or [])))
    for target_index, original_index in enumerate(ordered_indices):
        current_index = current.index(original_index)
        if current_index != target_index:
            page.comas.move(current_index, target_index)
            item = current.pop(current_index)
            current.insert(target_index, item)


def _replace_parent_key_on_entry(entry, old_key: str, new_key: str) -> None:
    if str(getattr(entry, "parent_key", "") or "") != old_key:
        return
    if hasattr(entry, "parent_kind"):
        entry.parent_kind = "coma"
    if hasattr(entry, "scope"):
        entry.scope = "page"
    entry.parent_key = new_key


def _retarget_parent_keys(context, page, remaps: list[tuple[str, str]]) -> None:
    from ..utils import gp_layer_parenting as gp_parent
    from ..utils import layer_stack as layer_stack_utils
    from ..utils import object_naming as on

    scene = getattr(context, "scene", None)
    phases = [
        (old, f"{getattr(page, 'id', '')}:{_TEMP_KEY_PREFIX}{index}")
        for index, (old, _new) in enumerate(remaps)
    ]
    phases.extend(
        (f"{getattr(page, 'id', '')}:{_TEMP_KEY_PREFIX}{index}", new)
        for index, (_old, new) in enumerate(remaps)
    )
    for old_key, new_key in phases:
        for collection_name in ("balloons", "texts"):
            for entry in getattr(page, collection_name, []) or []:
                _replace_parent_key_on_entry(entry, old_key, new_key)
        for folder in getattr(getattr(scene, "bmanga_work", None), "layer_folders", []) or []:
            _replace_parent_key_on_entry(folder, old_key, new_key)
        for collection_name in ("bmanga_raster_layers", "bmanga_image_layers", "bmanga_image_path_layers"):
            for entry in getattr(scene, collection_name, []) or []:
                _replace_parent_key_on_entry(entry, old_key, new_key)
        for layer in layer_stack_utils.gp_layers_for_parent_keys(context, {old_key}):
            gp_parent.set_parent_key(layer, new_key)
        for layer in layer_stack_utils.effect_layers_for_parent_keys(context, {old_key}):
            gp_parent.set_parent_key(layer, new_key)
        for obj in bpy.data.objects:
            if str(obj.get(on.PROP_PARENT_KEY, "") or "") == old_key:
                obj[on.PROP_PARENT_KEY] = new_key
        for coll in bpy.data.collections:
            if str(coll.get(on.PROP_PARENT_KEY, "") or "") == old_key:
                coll[on.PROP_PARENT_KEY] = new_key


def _retarget_coma_collections(page_id: str, remaps: list[tuple[str, str]]) -> None:
    from ..utils import object_naming as on

    pairs = []
    for index, (old_key, _new_key) in enumerate(remaps):
        coll = on.find_collection_by_bmanga_id(old_key, kind="coma")
        if coll is not None:
            temp_key = f"{page_id}:{_TEMP_KEY_PREFIX}{index}"
            coll[on.PROP_ID] = temp_key
            pairs.append((coll, temp_key))
    for coll, temp_key in pairs:
        index = int(temp_key.rsplit(_TEMP_KEY_PREFIX, 1)[1])
        new_id = remaps[index][1].split(":", 1)[1]
        coll[on.PROP_ID] = f"{page_id}:{new_id}"


def _renumber_page_comas(context, page, read_direction: str) -> int:
    """page.comas を読み順に並べ、id / coma_id を 1 から振り直す."""
    comas = getattr(page, "comas", None)
    if comas is None:
        return 0
    old_stems = [
        str(getattr(coma, "coma_id", "") or getattr(coma, "id", "") or "")
        for coma in comas
    ]
    active_original = int(getattr(page, "active_coma_index", -1) or -1)
    ordered_indices = _reading_order_indices(page, read_direction)
    _move_comas_to_order(page, ordered_indices)
    if active_original in ordered_indices:
        page.active_coma_index = ordered_indices.index(active_original)
    page_id = str(getattr(page, "id", "") or "")
    scene = getattr(context, "scene", None)
    current_coma_id = str(getattr(scene, "bmanga_current_coma_id", "") or "")
    remaps: list[tuple[str, str]] = []
    changed = 0
    for i, original_index in enumerate(ordered_indices):
        coma = comas[i]
        new_id = _format_coma_id(i + 1)
        old_id = str(getattr(coma, "id", "") or "")
        if old_id != new_id:
            try:
                coma.id = new_id
            except Exception:  # noqa: BLE001
                _logger.exception("coma renumber: id set failed")
                continue
            changed += 1
        old_stem = str(getattr(coma, "coma_id", "") or "")
        if old_stem != new_id:
            try:
                coma.coma_id = new_id
            except Exception:  # noqa: BLE001
                pass
        if page_id and old_stems[original_index] and old_stems[original_index] != new_id:
            remaps.append((f"{page_id}:{old_stems[original_index]}", f"{page_id}:{new_id}"))
        if scene is not None and current_coma_id and current_coma_id == old_stems[original_index]:
            scene.bmanga_current_coma_id = new_id
    if remaps:
        _retarget_parent_keys(context, page, remaps)
        _retarget_coma_collections(page_id, remaps)
    return changed


class BMANGA_OT_coma_renumber_active_page(Operator):
    """アクティブページのコマ ID を順番通りに振り直す."""

    bl_idname = "bmanga.coma_renumber_active_page"
    bl_label = "コマ ID を順番通り再採番"
    bl_description = (
        "現在のページのコマ番号を読む順番に振り直します。"
        "コマ用blendファイル名は変更されません。"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work
        from ..utils import page_file_scene

        work = get_work(context)
        if not (work and getattr(work, "loaded", False)):
            return False
        scene = getattr(context, "scene", None)
        if not page_file_scene.is_page_edit_scene(scene):
            return False
        page_id = page_file_scene.current_page_id(scene)
        page_index = page_file_scene.find_page_index(work, page_id)
        if page_index < 0:
            return False
        return bool(len(getattr(work.pages[page_index], "comas", []) or []))

    def execute(self, context):
        from ..core.work import get_work
        from ..utils import layer_object_sync as los
        from ..utils import page_file_scene

        scene = context.scene
        work = get_work(context)
        page_id = page_file_scene.current_page_id(scene)
        idx = page_file_scene.find_page_index(work, page_id)
        if idx < 0:
            self.report({"WARNING"}, "ページ用blendファイルで実行してください")
            return {"CANCELLED"}
        page = work.pages[idx]
        page_id = str(getattr(page, "id", "") or "")
        if not page_id:
            self.report({"WARNING"}, "アクティブページの ID が空です")
            return {"CANCELLED"}

        read_direction = str(getattr(getattr(work, "paper", None), "read_direction", "left") or "left")
        changed = _renumber_page_comas(context, page, read_direction)
        with los.suppress_sync():
            los.mirror_work_to_outliner(scene, work)

        self.report(
            {"INFO"},
            f"選択ページ: {changed} 件のコマ ID を再採番しました",
        )
        return {"FINISHED"}


_CLASSES = (BMANGA_OT_coma_renumber_active_page,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
