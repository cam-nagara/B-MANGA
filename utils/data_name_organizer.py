"""ページ/コマの実データ名を現在の並びへ揃える."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import bpy

from ..io import coma_io, page_io, work_io
from . import log, paths

_logger = log.get_logger(__name__)
_TEMP_PAGE_PREFIX = "__bmanga_page_tmp__"
_TEMP_COMA_PREFIX = "__bmanga_coma_tmp__"


@dataclass(slots=True)
class DataNameOrganizeResult:
    changed: bool = False
    page_renames: int = 0
    coma_renames: int = 0
    coma_reorders: int = 0

    @property
    def summary(self) -> str:
        if not self.changed:
            return "実データ名は整理済みです"
        parts = []
        if self.page_renames:
            parts.append(f"ページ {self.page_renames} 件")
        if self.coma_renames:
            parts.append(f"コマ {self.coma_renames} 件")
        if self.coma_reorders:
            parts.append(f"並び順 {self.coma_reorders} 件")
        return "実データ名を整理しました: " + " / ".join(parts)


@dataclass(slots=True)
class _PageRename:
    page: object
    old_id: str
    new_id: str


@dataclass(slots=True)
class _ComaRename:
    page: object
    page_id: str
    old_id: str
    new_id: str


def _format_coma_id(index: int) -> str:
    return paths.format_coma_id(index)


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
        return [int(item["index"]) for item in items]
    heights = sorted(float(item["height"]) for item in items)
    row_tolerance = max(2.0, heights[len(heights) // 2] * 0.35)
    rows: list[dict[str, object]] = []
    for item in sorted(items, key=lambda value: (-float(value["top"]), float(value["left"]))):
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


def _existing_data_dir_names(parent: Path) -> set[str]:
    if not parent.is_dir():
        return set()
    return {child.name for child in parent.iterdir() if child.is_dir()}


def _rename_directories(parent: Path, pairs: list[tuple[str, str]]) -> list[tuple[Path, str, str]]:
    unique_pairs = [(old, new) for old, new in pairs if old and new and old != new]
    if not unique_pairs:
        return []
    parent.mkdir(parents=True, exist_ok=True)
    moving_sources = {old for old, _new in unique_pairs}
    for old, new in unique_pairs:
        src = parent / old
        dst = parent / new
        if not src.exists():
            continue
        if dst.exists() and new not in moving_sources:
            raise FileExistsError(f"destination already exists: {dst}")

    token = uuid4().hex
    temp_pairs: list[tuple[Path, Path, str, str]] = []
    for index, (old, new) in enumerate(unique_pairs):
        src = parent / old
        if not src.exists():
            continue
        temp = parent / f".bmanga_rename_{token}_{index}"
        src.rename(temp)
        temp_pairs.append((temp, parent / new, old, new))

    renamed: list[tuple[Path, str, str]] = []
    for temp, dst, old, new in temp_pairs:
        if dst.exists():
            raise FileExistsError(f"destination already exists: {dst}")
        temp.rename(dst)
        renamed.append((dst, old, new))
    return renamed


def _rename_coma_artifacts(coma_dir: Path, old_id: str, new_id: str) -> None:
    if not coma_dir.is_dir() or old_id == new_id:
        return
    for suffix in (".blend", ".json", "_thumb.png", "_preview.png"):
        src = coma_dir / f"{old_id}{suffix}"
        if not src.exists():
            continue
        dst = coma_dir / f"{new_id}{suffix}"
        if dst.exists():
            raise FileExistsError(f"destination already exists: {dst}")
        src.rename(dst)


def _replace_entry_parent_key(entry, old_key: str, new_key: str, *, prefix: bool) -> None:
    key = str(getattr(entry, "parent_key", "") or "")
    if key == old_key:
        replacement = new_key
    elif prefix and key.startswith(f"{old_key}:"):
        replacement = f"{new_key}:{key.split(':', 1)[1]}"
    else:
        return
    if hasattr(entry, "parent_kind") and replacement:
        entry.parent_kind = "coma" if ":" in replacement else "page"
    if hasattr(entry, "scope"):
        entry.scope = "page"
    entry.parent_key = replacement


def _replace_id_key(value: str, old_key: str, new_key: str, *, prefix: bool) -> str:
    if value == old_key:
        return new_key
    if prefix and value.startswith(f"{old_key}:"):
        return f"{new_key}:{value.split(':', 1)[1]}"
    return value


def _retarget_property_entries(scene, work, old_key: str, new_key: str, prefix: bool) -> None:
    for page in getattr(work, "pages", []) or []:
        for collection_name in ("balloons", "texts"):
            for entry in getattr(page, collection_name, []) or []:
                _replace_entry_parent_key(entry, old_key, new_key, prefix=prefix)
        for ref in getattr(page, "original_pages", []) or []:
            page_id = str(getattr(ref, "page_id", "") or "")
            if page_id == old_key:
                ref.page_id = new_key
    for folder in getattr(work, "layer_folders", []) or []:
        _replace_entry_parent_key(folder, old_key, new_key, prefix=prefix)
    for collection_name in (
        "bmanga_raster_layers",
        "bmanga_image_layers",
        "bmanga_image_path_layers",
        "bmanga_fill_layers",
    ):
        for entry in getattr(scene, collection_name, []) or []:
            _replace_entry_parent_key(entry, old_key, new_key, prefix=prefix)


def _collect_existing_parent_keys(scene, old_key: str, prefix: bool, on) -> set[str]:
    keys = {old_key}
    if not prefix:
        return keys
    keys.update(
        str(getattr(item, "parent_key", "") or "")
        for item in getattr(scene, "bmanga_layer_stack", []) or []
        if str(getattr(item, "parent_key", "") or "").startswith(f"{old_key}:")
    )
    for datablocks in (bpy.data.objects, bpy.data.collections):
        for item in datablocks:
            key = str(item.get(on.PROP_PARENT_KEY, "") or "")
            if key.startswith(f"{old_key}:"):
                keys.add(key)
    return keys


def _retarget_drawing_layers(context, keys: set[str], old_key: str, new_key: str, prefix: bool) -> None:
    from . import gp_layer_parenting as gp_parent
    from . import layer_stack as layer_stack_utils

    for old_parent_key in keys:
        new_parent_key = _replace_id_key(old_parent_key, old_key, new_key, prefix=prefix)
        for layer in layer_stack_utils.gp_layers_for_parent_keys(context, {old_parent_key}):
            gp_parent.set_parent_key(layer, new_parent_key)
        for layer in layer_stack_utils.effect_layers_for_parent_keys(context, {old_parent_key}):
            gp_parent.set_parent_key(layer, new_parent_key)


def _retarget_datablock_keys(datablock, old_key: str, new_key: str, prefix: bool, on) -> None:
    parent_key = str(datablock.get(on.PROP_PARENT_KEY, "") or "")
    new_parent = _replace_id_key(parent_key, old_key, new_key, prefix=prefix)
    if new_parent != parent_key:
        datablock[on.PROP_PARENT_KEY] = new_parent
    bmanga_id = str(datablock.get(on.PROP_ID, "") or "")
    new_id = _replace_id_key(bmanga_id, old_key, new_key, prefix=prefix)
    if new_id != bmanga_id:
        datablock[on.PROP_ID] = new_id


def _retarget_scene_current(scene, old_key: str, new_key: str, prefix: bool) -> None:
    if scene is None:
        return
    current_page = str(getattr(scene, "bmanga_current_coma_page_id", "") or "")
    if current_page == old_key:
        scene.bmanga_current_coma_page_id = new_key
    current_coma = str(getattr(scene, "bmanga_current_coma_id", "") or "")
    if not prefix and current_coma and old_key.endswith(f":{current_coma}"):
        scene.bmanga_current_coma_id = new_key.split(":", 1)[1]


def _retarget_keys(context, phases: list[tuple[str, str, bool]]) -> None:
    if not phases:
        return
    from contextlib import ExitStack

    from . import balloon_curve_object
    from . import fill_real_object
    from . import image_path_object
    from . import image_real_object
    from . import layer_object_sync as los
    from . import object_naming as on
    from . import coma_runtime_retarget
    from . import text_real_object

    scene = getattr(context, "scene", None)
    work = getattr(scene, "bmanga_work", None)
    with ExitStack() as stack:
        stack.enter_context(los.suppress_sync())
        stack.enter_context(balloon_curve_object.suspend_auto_sync())
        stack.enter_context(text_real_object.suspend_auto_sync())
        stack.enter_context(image_real_object.suspend_auto_sync())
        stack.enter_context(image_path_object.suspend_auto_sync())
        stack.enter_context(fill_real_object.suspend_auto_sync())
        for old_key, new_key, prefix in phases:
            keys = _collect_existing_parent_keys(scene, old_key, prefix, on)
            for datablocks in (bpy.data.objects, bpy.data.collections):
                for datablock in datablocks:
                    _retarget_datablock_keys(datablock, old_key, new_key, prefix, on)
            _retarget_property_entries(scene, work, old_key, new_key, prefix)
            _retarget_drawing_layers(context, keys, old_key, new_key, prefix)
            coma_runtime_retarget.retarget_coma_runtime_ids(old_key, new_key, prefix=prefix)
            _retarget_scene_current(scene, old_key, new_key, prefix)


def _page_retarget_phases(remaps: list[_PageRename]) -> list[tuple[str, str, bool]]:
    phases: list[tuple[str, str, bool]] = []
    for index, remap in enumerate(remaps):
        phases.append((remap.old_id, f"{_TEMP_PAGE_PREFIX}{index}", True))
    for index, remap in enumerate(remaps):
        phases.append((f"{_TEMP_PAGE_PREFIX}{index}", remap.new_id, True))
    return phases


def _coma_retarget_phases(remaps: list[_ComaRename]) -> list[tuple[str, str, bool]]:
    phases: list[tuple[str, str, bool]] = []
    for index, remap in enumerate(remaps):
        phases.append((f"{remap.page_id}:{remap.old_id}", f"{remap.page_id}:{_TEMP_COMA_PREFIX}{index}", False))
    for index, remap in enumerate(remaps):
        phases.append((f"{remap.page_id}:{_TEMP_COMA_PREFIX}{index}", f"{remap.page_id}:{remap.new_id}", False))
    return phases


def _collect_page_remaps(work) -> list[_PageRename]:
    remaps: list[_PageRename] = []
    page_number = 1
    for page in getattr(work, "pages", []) or []:
        old_id = str(getattr(page, "id", "") or "")
        is_spread = bool(getattr(page, "spread", False))
        if is_spread:
            new_id = paths.format_spread_id(page_number, page_number + 1)
            page_number += 2
        else:
            new_id = paths.format_page_id(page_number)
            page_number += 1
        if old_id != new_id:
            remaps.append(_PageRename(page=page, old_id=old_id, new_id=new_id))
    return remaps


def _set_spread_original_pages(page, page_id: str) -> None:
    if not bool(getattr(page, "spread", False)) or "-" not in page_id:
        return
    head, tail = page_id[1:].split("-", 1)
    values = (f"p{head}", f"p{tail}")
    refs = getattr(page, "original_pages", None)
    if refs is None:
        return
    while len(refs) < 2:
        refs.add()
    refs[0].page_id = values[0]
    refs[1].page_id = values[1]
    while len(refs) > 2:
        refs.remove(len(refs) - 1)


def _apply_page_ids(remaps: list[_PageRename]) -> None:
    for remap in remaps:
        remap.page.id = remap.new_id
        if hasattr(remap.page, "dir_rel"):
            remap.page.dir_rel = f"{remap.new_id}/"
        _set_spread_original_pages(remap.page, remap.new_id)


def _collect_and_apply_coma_remaps(
    context,
    work,
    read_direction: str,
    *,
    page_ids: set[str] | None = None,
) -> tuple[list[_ComaRename], int]:
    all_remaps: list[_ComaRename] = []
    reorder_count = 0
    scene = getattr(context, "scene", None)
    for page in getattr(work, "pages", []) or []:
        comas = getattr(page, "comas", None)
        if comas is None or not comas:
            continue
        page_id = str(getattr(page, "id", "") or "")
        if page_ids is not None and page_id not in page_ids:
            continue
        ordered_indices = _reading_order_indices(page, read_direction)
        if ordered_indices != list(range(len(comas))):
            reorder_count += 1
        active_original = int(getattr(page, "active_coma_index", -1) or -1)
        old_stems = [
            str(getattr(coma, "coma_id", "") or getattr(coma, "id", "") or "")
            for coma in comas
        ]
        remaps_for_page: list[tuple[int, str, str]] = []
        for target_index, original_index in enumerate(ordered_indices):
            old_id = old_stems[original_index]
            new_id = _format_coma_id(target_index + 1)
            if old_id and old_id != new_id:
                remaps_for_page.append((original_index, old_id, new_id))

        _move_comas_to_order(page, ordered_indices)
        if active_original in ordered_indices:
            page.active_coma_index = ordered_indices.index(active_original)
        if hasattr(page, "coma_count"):
            page.coma_count = len(comas)

        current_coma_id = str(getattr(scene, "bmanga_current_coma_id", "") or "")
        for target_index, original_index in enumerate(ordered_indices):
            coma = comas[target_index]
            new_id = _format_coma_id(target_index + 1)
            old_id = old_stems[original_index]
            coma.id = new_id
            coma.coma_id = new_id
            if current_coma_id and current_coma_id == old_id:
                scene.bmanga_current_coma_id = new_id
        for _original_index, old_id, new_id in remaps_for_page:
            all_remaps.append(_ComaRename(page=page, page_id=page_id, old_id=old_id, new_id=new_id))
    return all_remaps, reorder_count


def _rename_page_dirs(work_dir: Path, remaps: list[_PageRename]) -> int:
    renamed = _rename_directories(work_dir, [(remap.old_id, remap.new_id) for remap in remaps])
    return len(renamed)


def _rename_coma_dirs(work_dir: Path, remaps: list[_ComaRename]) -> int:
    by_page: dict[str, list[_ComaRename]] = {}
    for remap in remaps:
        by_page.setdefault(remap.page_id, []).append(remap)
    renamed_count = 0
    for page_id, page_remaps in by_page.items():
        page_dir = paths.page_dir(work_dir, page_id)
        renamed = _rename_directories(
            page_dir,
            [(remap.old_id, remap.new_id) for remap in page_remaps],
        )
        renamed_count += len(renamed)
        for dst, old_id, new_id in renamed:
            _rename_coma_artifacts(dst, old_id, new_id)
        existing = _existing_data_dir_names(page_dir)
        for remap in page_remaps:
            if remap.new_id in existing:
                _rename_coma_artifacts(page_dir / remap.new_id, remap.old_id, remap.new_id)
    return renamed_count


def _save_all_metadata(work_dir: Path, work) -> None:
    work_io.save_work_json(work_dir, work)
    page_io.save_pages_json(work_dir, work)
    for page in getattr(work, "pages", []) or []:
        page_io.save_page_json(work_dir, page)
        page_id = str(getattr(page, "id", "") or "")
        for coma in getattr(page, "comas", []) or []:
            if str(getattr(coma, "coma_id", "") or ""):
                coma_io.save_coma_meta(work_dir, page_id, coma)


def _sync_blender_state(context, work) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    try:
        from . import layer_object_sync as los

        with los.suppress_sync():
            los.mirror_work_to_outliner(scene, work)
    except Exception:  # noqa: BLE001
        _logger.exception("data name organize: outliner sync failed")
    try:
        from . import page_grid

        page_grid.apply_page_collection_transforms(context, work)
    except Exception:  # noqa: BLE001
        _logger.exception("data name organize: page grid sync failed")
    try:
        from . import layer_stack

        layer_stack.sync_layer_stack_after_data_change(
            context,
            align_page_order=True,
            align_coma_order=True,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("data name organize: layer list sync failed")


def organize_data_names(context) -> DataNameOrganizeResult:
    """現在のページ/コマ順に合わせてフォルダ名とファイル名を整理する."""
    from ..core.work import get_work

    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return DataNameOrganizeResult()
    work_dir_raw = str(getattr(work, "work_dir", "") or "")
    if not work_dir_raw:
        return DataNameOrganizeResult()
    work_dir = Path(work_dir_raw)
    read_direction = str(getattr(getattr(work, "paper", None), "read_direction", "left") or "left")

    page_remaps = _collect_page_remaps(work)
    if page_remaps:
        _rename_page_dirs(work_dir, page_remaps)
        _retarget_keys(context, _page_retarget_phases(page_remaps))
        _apply_page_ids(page_remaps)

    coma_remaps, reorder_count = _collect_and_apply_coma_remaps(context, work, read_direction)
    if coma_remaps:
        _rename_coma_dirs(work_dir, coma_remaps)
        _retarget_keys(context, _coma_retarget_phases(coma_remaps))

    result = DataNameOrganizeResult(
        page_renames=len(page_remaps),
        coma_renames=len(coma_remaps),
        coma_reorders=reorder_count,
    )
    result.changed = bool(result.page_renames or result.coma_renames or result.coma_reorders)
    if result.changed:
        _save_all_metadata(work_dir, work)
        _sync_blender_state(context, work)
    return result


def organize_page_coma_names(context, page) -> DataNameOrganizeResult:
    """指定ページのコマIDとコマ用ファイル名だけを現在の読み順へ揃える。"""
    from ..core.work import get_work

    work = get_work(context)
    if work is None or not getattr(work, "loaded", False) or page is None:
        return DataNameOrganizeResult()
    page_id = str(getattr(page, "id", "") or "")
    work_dir_raw = str(getattr(work, "work_dir", "") or "")
    if not page_id or not work_dir_raw:
        return DataNameOrganizeResult()
    work_dir = Path(work_dir_raw)
    read_direction = str(getattr(getattr(work, "paper", None), "read_direction", "left") or "left")
    coma_remaps, reorder_count = _collect_and_apply_coma_remaps(
        context,
        work,
        read_direction,
        page_ids={page_id},
    )
    if coma_remaps:
        _rename_coma_dirs(work_dir, coma_remaps)
        _retarget_keys(context, _coma_retarget_phases(coma_remaps))
    result = DataNameOrganizeResult(
        coma_renames=len(coma_remaps),
        coma_reorders=reorder_count,
    )
    result.changed = bool(result.coma_renames or result.coma_reorders)
    if result.changed:
        _save_all_metadata(work_dir, work)
        _sync_blender_state(context, work)
    return result
