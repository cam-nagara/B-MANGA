"""ページの見開き結合・解除を損失なく行う Operator。

ページ一覧のメタデータだけでなく、左右それぞれの ``page.blend`` を子 Blender
で統合する。完成した一時ページの再読込検証が通るまで原本ディレクトリには
触れず、確定中の失敗時はディレクトリ・JSON・メモリ状態を全て復元する。
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_work
from ..io import schema, spread_page_content
from ..utils import (
    log,
    page_detail,
    page_file_scene,
    page_grid,
    paths,
    spread_metadata,
)

_logger = log.get_logger(__name__)


def _json_payloads(work_data: dict, pages_data: dict) -> tuple[dict, dict]:
    timestamp = datetime.now(timezone.utc).isoformat()
    work_json = deepcopy(work_data)
    pages_json = deepcopy(pages_data)
    work_json["lastSaved"] = timestamp
    pages_json["lastModified"] = timestamp
    return work_json, pages_json


def _merged_pages_payload(
    pages_data: dict,
    index: int,
    *,
    spread_id: str,
    first_page_id: str,
    second_page_id: str,
    coma_count: int,
    tombo_aligned: bool,
    tombo_gap_mm: float,
) -> dict:
    result = deepcopy(pages_data)
    items = result.get("pages", [])
    summary = deepcopy(items[index])
    summary.update({
        "id": spread_id,
        "title": "",
        "dir": f"{spread_id}/",
        "spread": True,
        "originalPages": [first_page_id, second_page_id],
        "tombo": {"aligned": bool(tombo_aligned), "gapMm": round(tombo_gap_mm, 3)},
        "comaCount": int(coma_count),
    })
    summary.pop("thumbnail", None)
    items[index] = summary
    del items[index + 1]
    result["totalPages"] = len(items)
    result["activePageIndex"] = index
    return result


def _split_pages_payload(
    pages_data: dict,
    index: int,
    page_ids: tuple[str, str],
    page_data: dict[str, dict],
    manifest: dict,
) -> dict:
    result = deepcopy(pages_data)
    items = result.get("pages", [])
    del items[index]
    originals = manifest.get("originalPageSummaries", {})
    for offset, page_id in enumerate(page_ids):
        summary = deepcopy(originals.get(page_id, {}))
        detail = page_data[page_id]
        summary.update({
            "id": page_id,
            "title": str(detail.get("title", "") or ""),
            "dir": f"{page_id}/",
            "spread": False,
            "comaCount": len(detail.get("comas", [])),
        })
        summary.pop("originalPages", None)
        summary.pop("tombo", None)
        items.insert(index + offset, summary)
    result["totalPages"] = len(items)
    result["activePageIndex"] = index
    return result


@contextmanager
def _suspend_data_side_effects():
    from ..core.work_info import suppress_page_number_range_update

    with suppress_page_number_range_update(), schema._suspend_load_property_side_effects():
        yield


def _apply_work_data(work, data: dict) -> None:
    work_dir = str(work.work_dir)
    loaded = bool(work.loaded)
    schema.work_from_dict(work, data)
    work.work_dir = work_dir
    work.loaded = loaded


def _configure_merged_summary(
    work,
    index: int,
    merged_data: dict,
    *,
    spread_id: str,
    first_page_id: str,
    second_page_id: str,
    tombo_aligned: bool,
    tombo_gap_mm: float,
):
    schema.page_from_dict(work.pages[index], merged_data)
    work.pages.remove(index + 1)
    merged = work.pages[index]
    merged.id = spread_id
    merged.title = ""
    merged.dir_rel = f"{spread_id}/"
    # 元ページの一覧画像を見開きの画像として誤表示しない。保存内容から
    # ``page_preview.png`` が再生成された時点で一覧側が更新される。
    merged.thumbnail_rel = ""
    merged.spread = True
    merged.tombo_aligned = bool(tombo_aligned)
    merged.tombo_gap_mm = float(tombo_gap_mm)
    merged.original_pages.clear()
    for page_id in (first_page_id, second_page_id):
        ref = merged.original_pages.add()
        ref.page_id = page_id
    merged.coma_count = len(merged.comas)
    work.active_page_index = index
    return merged


def _reset_split_page_identity(entry, page_id: str) -> None:
    entry.id = page_id
    entry.title = ""
    entry.dir_rel = f"{page_id}/"
    entry.spread = False
    entry.original_pages.clear()


def _configure_split_summaries(
    work,
    index: int,
    page_ids: tuple[str, str],
    page_data: dict[str, dict],
    manifest: dict,
) -> dict[str, object]:
    original_summaries = manifest.get("originalPageSummaries", {})
    work.pages.remove(index)
    for offset, page_id in enumerate(page_ids):
        entry = work.pages.add()
        summary = original_summaries.get(page_id, {})
        if isinstance(summary, dict) and summary:
            schema.page_entry_from_dict(entry, summary)
        else:
            _reset_split_page_identity(entry, page_id)
        entry.id = page_id
        entry.dir_rel = f"{page_id}/"
        entry.spread = False
        entry.original_pages.clear()
        schema.page_from_dict(entry, page_data[page_id])
        entry.coma_count = len(entry.comas)
        work.pages.move(len(work.pages) - 1, index + offset)
    work.active_page_index = index
    return {
        page_id: work.pages[index + offset]
        for offset, page_id in enumerate(page_ids)
    }


def _refresh_overview(context, work, operation: str) -> None:
    try:
        page_grid.apply_page_collection_transforms(context, work)
    except Exception:  # noqa: BLE001
        _logger.exception("spread: overview refresh failed after committed %s", operation)


class BMANGA_OT_pages_merge_spread(Operator):
    """連続2ページを、実ページ内容を保持した見開きへ結合する。"""

    bl_idname = "bmanga.pages_merge_spread"
    bl_label = "見開きに変更"
    bl_options = {"REGISTER", "UNDO"}

    left_index: IntProperty(name="左ページ index", default=-1, min=-1)  # type: ignore[valid-type]
    tombo_aligned: BoolProperty(name="トンボを合わせる", default=True)  # type: ignore[valid-type]
    tombo_gap_mm: FloatProperty(  # type: ignore[valid-type]
        name="間隔 (mm)",
        description="仕上がり枠間のギャップ。負値はノド側を重ねる方向",
        default=-9.60,
    )
    fail_phase: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(
            work
            and work.loaded
            and len(work.pages) >= 2
            and page_file_scene.is_work_list_scene(context.scene)
        )

    def invoke(self, context, event):
        work = get_work(context)
        if self.left_index < 0:
            self.left_index = work.active_page_index
        if 0 <= self.left_index < len(work.pages) - 1:
            page_detail.ensure_page_detail(work, work.pages[self.left_index])
            page_detail.ensure_page_detail(work, work.pages[self.left_index + 1])
        return context.window_manager.invoke_props_dialog(self, width=450)

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        left = self.left_index
        if not (0 <= left < len(work.pages) - 1):
            layout.label(text="左ページの選択が不正です", icon="ERROR")
            return
        first = work.pages[left]
        second = work.pages[left + 1]
        column = layout.column()
        column.label(text=f"{first.title} と {second.title} を見開きに統合します")
        column.label(
            text=(
                f"コマ: {len(first.comas) + len(second.comas)} / "
                f"フキダシ: {len(first.balloons) + len(second.balloons)} / "
                f"テキスト: {len(first.texts) + len(second.texts)} を保持"
            ),
            icon="INFO",
        )
        column.separator()
        column.label(
            text="右ページの内容はトンボ合わせの間隔を反映して配置されます",
            icon="ARROW_LEFTRIGHT",
        )
        column.separator()
        column.prop(self, "tombo_aligned")
        sub = column.column()
        sub.enabled = self.tombo_aligned
        sub.prop(self, "tombo_gap_mm")

    def execute(self, context):
        work = get_work(context)
        if work is None or not work.loaded or not page_file_scene.is_work_list_scene(context.scene):
            self.report({"ERROR"}, "ページ一覧で実行してください")
            return {"CANCELLED"}
        index = self.left_index
        if not (0 <= index < len(work.pages) - 1):
            self.report({"ERROR"}, "左ページの選択が不正です")
            return {"CANCELLED"}
        first = work.pages[index]
        second = work.pages[index + 1]
        if first.spread or second.spread:
            self.report({"ERROR"}, "既に見開きのページは結合できません")
            return {"CANCELLED"}
        page_detail.ensure_page_detail(work, first)
        page_detail.ensure_page_detail(work, second)
        first_id = str(first.id)
        second_id = str(second.id)
        try:
            first_number = int(first_id.split("-", 1)[0].lstrip("p"))
            second_number = int(second_id.split("-", 1)[0].lstrip("p"))
            spread_id = paths.format_spread_id(first_number, second_number)
        except ValueError:
            self.report({"ERROR"}, "ページ ID が不正です")
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        right_offset = page_grid.spread_right_page_offset_mm_for_values(
            float(work.paper.canvas_width_mm),
            bool(self.tombo_aligned),
            float(self.tombo_gap_mm),
            finish_width_mm=float(work.paper.finish_width_mm),
        )
        pages_snapshot = schema.pages_to_dict(work)
        original_summaries = {
            first_id: schema.page_entry_to_dict(first),
            second_id: schema.page_entry_to_dict(second),
        }
        try:
            merged_data, id_maps, manifest_parts = spread_metadata.merge_pages(
                schema.page_to_dict(first),
                schema.page_to_dict(second),
                first_page_id=first_id,
                second_page_id=second_id,
                spread_id=spread_id,
                right_offset_mm=right_offset,
            )
            coma_maps = {
                page_id: maps["coma"]
                for page_id, maps in id_maps.items()
            }
            merged_work_data, global_sources = spread_metadata.merge_work_data(
                schema.work_to_dict(work),
                first_page_id=first_id,
                second_page_id=second_id,
                spread_id=spread_id,
                coma_maps=coma_maps,
                right_offset_mm=right_offset,
            )
            merged_pages_data = _merged_pages_payload(
                pages_snapshot,
                index,
                spread_id=spread_id,
                first_page_id=first_id,
                second_page_id=second_id,
                coma_count=len(merged_data.get("comas", [])),
                tombo_aligned=bool(self.tombo_aligned),
                tombo_gap_mm=float(self.tombo_gap_mm),
            )
            work_json, pages_json = _json_payloads(merged_work_data, merged_pages_data)
            manifest = {
                "version": 1,
                "spreadId": spread_id,
                "sourcePages": [first_id, second_id],
                "rightOffsetMm": right_offset,
                "idMaps": id_maps,
                "globalSources": global_sources,
                "originalPageSummaries": original_summaries,
                **manifest_parts,
            }
            request = {
                "first_page_id": first_id,
                "second_page_id": second_id,
                "target_page_id": spread_id,
                "right_page_offset_mm": right_offset,
                "id_maps": id_maps,
                "entity_sources": manifest_parts["entitySources"],
                "work_dir": str(work_dir),
                "work_data": work_json,
                "pages_data": pages_json,
                "page_data": merged_data,
            }
            outcome = spread_page_content.merge_page_content(
                work_dir,
                first_id,
                second_id,
                spread_id,
                request=request,
                coma_maps=coma_maps,
                manifest=manifest,
                work_json=work_json,
                pages_json=pages_json,
                page_json=merged_data,
                fail_phase=str(self.fail_phase or ""),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("pages_merge_spread failed")
            self.report({"ERROR"}, f"見開き統合失敗: {exc}")
            return {"CANCELLED"}
        try:
            with _suspend_data_side_effects():
                _apply_work_data(work, merged_work_data)
                merged = _configure_merged_summary(
                    work,
                    index,
                    outcome["pageData"],
                    spread_id=spread_id,
                    first_page_id=first_id,
                    second_page_id=second_id,
                    tombo_aligned=bool(self.tombo_aligned),
                    tombo_gap_mm=float(self.tombo_gap_mm),
                )
        except Exception:  # noqa: BLE001
            _logger.exception("spread: committed page metadata could not refresh in memory")
            self.report({"WARNING"}, "見開きは保存済みです。作品を開き直してください")
            return {"FINISHED"}
        _refresh_overview(context, work, "merge")
        self.report(
            {"INFO"},
            f"見開き統合: {spread_id} "
            f"(コマ {len(merged.comas)} / フキダシ {len(merged.balloons)} / テキスト {len(merged.texts)})",
        )
        return {"FINISHED"}


class BMANGA_OT_pages_split_spread(Operator):
    """結合時の所属印を使い、見開きを元の2ページへ解除する。"""

    bl_idname = "bmanga.pages_split_spread"
    bl_label = "見開きを解除"
    bl_options = {"REGISTER", "UNDO"}

    spread_index: IntProperty(default=-1, min=-1)  # type: ignore[valid-type]
    fail_phase: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        if not (
            work
            and work.loaded
            and page_file_scene.is_work_list_scene(context.scene)
        ):
            return False
        index = work.active_page_index
        return 0 <= index < len(work.pages) and work.pages[index].spread

    def invoke(self, context, event):
        work = get_work(context)
        if self.spread_index < 0:
            self.spread_index = work.active_page_index
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        work = get_work(context)
        if work is None or not work.loaded or not page_file_scene.is_work_list_scene(context.scene):
            self.report({"ERROR"}, "ページ一覧で実行してください")
            return {"CANCELLED"}
        index = self.spread_index
        if not (0 <= index < len(work.pages)):
            return {"CANCELLED"}
        spread = work.pages[index]
        if not spread.spread:
            self.report({"ERROR"}, "見開きページではありません")
            return {"CANCELLED"}
        if len(spread.original_pages) < 2:
            self.report({"ERROR"}, "結合元ページ情報が失われているため解除できません")
            return {"CANCELLED"}
        page_detail.ensure_page_detail(work, spread)
        work_dir = Path(work.work_dir)
        spread_id = str(spread.id)
        first_id = str(spread.original_pages[0].page_id)
        second_id = str(spread.original_pages[1].page_id)
        page_ids = (first_id, second_id)
        pages_snapshot = schema.pages_to_dict(work)
        try:
            manifest = spread_page_content.read_manifest(work_dir, spread_id)
            if list(manifest.get("sourcePages", [])) != list(page_ids):
                raise spread_page_content.SpreadContentError(
                    "結合元ページ情報と保存済み解除情報が一致しません"
                )
            right_offset = float(manifest.get("rightOffsetMm", 0.0) or 0.0)
            split_data = spread_metadata.split_page(
                schema.page_to_dict(spread),
                manifest,
                first_page_id=first_id,
                second_page_id=second_id,
                spread_id=spread_id,
                right_offset_mm=right_offset,
            )
            split_work_data = spread_metadata.split_work_data(
                schema.work_to_dict(work),
                manifest,
                first_page_id=first_id,
                second_page_id=second_id,
                spread_id=spread_id,
                right_offset_mm=right_offset,
            )
            split_pages_data = _split_pages_payload(
                pages_snapshot, index, page_ids, split_data, manifest
            )
            work_json, pages_json = _json_payloads(split_work_data, split_pages_data)
            memberships = spread_metadata.source_memberships(manifest)
            requests = {
                page_id: {
                    "spread_id": spread_id,
                    "source_memberships": memberships,
                    "reverse_id_maps": spread_metadata.reverse_maps_for_source(
                        manifest, page_id
                    ),
                    "reverse_link_group_map": (
                        spread_metadata.reverse_link_groups_for_source(manifest, page_id)
                    ),
                    "work_dir": str(work_dir),
                    "work_data": work_json,
                    "pages_data": pages_json,
                    "page_data": split_data[page_id],
                }
                for page_id in page_ids
            }
            coma_maps = {
                page_id: spread_metadata.coma_storage_map_for_source(manifest, page_id)
                for page_id in page_ids
            }
            spread_page_content.split_page_content(
                work_dir,
                spread_id,
                page_ids,
                requests=requests,
                coma_maps=coma_maps,
                work_json=work_json,
                pages_json=pages_json,
                page_jsons=split_data,
                fail_phase=str(self.fail_phase or ""),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("pages_split_spread failed")
            self.report({"ERROR"}, f"見開き解除失敗: {exc}")
            return {"CANCELLED"}
        try:
            with _suspend_data_side_effects():
                _apply_work_data(work, split_work_data)
                _configure_split_summaries(
                    work, index, page_ids, split_data, manifest
                )
        except Exception:  # noqa: BLE001
            _logger.exception("spread: committed split metadata could not refresh in memory")
            self.report({"WARNING"}, "見開き解除は保存済みです。作品を開き直してください")
            return {"FINISHED"}
        _refresh_overview(context, work, "split")
        right_coma_count = len(split_data[first_id].get("comas", []))
        left_coma_count = len(split_data[second_id].get("comas", []))
        self.report(
            {"INFO"},
            f"見開き解除: {first_id} / {second_id} "
            f"(右: コマ {right_coma_count} / 左: コマ {left_coma_count})",
        )
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_pages_merge_spread,
    BMANGA_OT_pages_split_spread,
)


def register() -> None:
    import bpy

    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    import bpy

    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
