"""Apply validated Meldex scenario documents to an open B-MANGA work."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

from ..core import balloon as balloon_core
from ..core.work_info import suppress_page_number_range_update
from ..utils import (
    balloon_curve_object,
    json_io,
    layer_stack,
    log,
    page_detail,
    page_file_scene,
    page_grid,
    page_range,
    paths,
    text_real_object,
    text_style,
)
from ..utils.layer_hierarchy import page_stack_key
from . import (
    balloon_presets,
    meldex_scenario_import_transaction,
    meldex_text_presentation,
    page_io,
    text_presets,
    work_io,
)
from .meldex_contract import ScenarioDocument, ScenarioRow, validate_payload

_logger = log.get_logger(__name__)


def import_payload(context, work, payload: dict) -> dict[str, int]:
    document = validate_payload(payload)
    if meldex_text_presentation.is_enabled(context):
        document = meldex_text_presentation.enrich_from_source_file(document)
    return _import_document(context, work, document, payload_to_save=payload)


def import_document(context, work, document: ScenarioDocument) -> dict[str, int]:
    return _import_document(context, work, document, payload_to_save=None)


def _import_document(
    context,
    work,
    document: ScenarioDocument,
    *,
    payload_to_save: dict | None,
) -> dict[str, int]:
    work_dir = Path(str(work.work_dir))
    original_active = int(getattr(work, "active_page_index", -1))
    with meldex_scenario_import_transaction.scenario_import_transaction(
        work,
        len(document.pages),
        save_payload_copy=payload_to_save is not None,
    ) as plan:
        result, new_pairs = _apply_document(
            context,
            work,
            document,
            plan,
            original_active,
        )
        if payload_to_save is not None:
            json_io.write_json(paths.scenario_file(work_dir), payload_to_save)
    _post_import_sync(context, work, result["pagesAdded"], new_pairs)
    return result


def _apply_document(context, work, document, plan, original_active):
    work_dir = plan.work_dir
    added_pages = _ensure_page_count(work, work_dir, plan.new_page_ids)
    balloon_by_name = {p.name: p for p in balloon_presets.list_all_presets(work_dir)}
    ordered_text_presets = text_presets.list_all_presets(work_dir)
    text_by_name = {p.name: p for p in ordered_text_presets}
    first_text_preset = ordered_text_presets[0] if ordered_text_presets else None
    apply_meldex_presentation = (
        document.version >= 2 and meldex_text_presentation.is_enabled(context)
    )
    result = {"pagesAdded": added_pages, "created": 0, "updated": 0, "ignored": 0}
    # 今回の取込で両方を新設したペアだけ、後段で隣接順へ揃える。
    # 既存要素の手動並び順は再取込でも維持する。
    new_pairs: list[tuple[str, str, str]] = []
    with ExitStack() as stack:
        stack.enter_context(balloon_curve_object.defer_auto_sync())
        stack.enter_context(text_real_object.suspend_auto_sync())
        for page_index, source_page in enumerate(document.pages):
            page = work.pages[page_index]
            if not bool(page.detail_loaded):
                raise meldex_scenario_import_transaction.ScenarioImportTransactionError(
                    f"{page.id} のページ情報を読み込めません"
                )
            page_key = page_stack_key(page)
            for row_index, row in enumerate(source_page.rows):
                if not row.body:
                    result["ignored"] += 1
                    continue
                exact_text_preset = text_by_name.get(row.type_name)
                created, pair_new, balloon_id, text_id = _upsert_row(
                    work,
                    page,
                    document.document_id,
                    row,
                    row_index,
                    balloon_by_name,
                    exact_text_preset or first_text_preset,
                    exact_text_preset is not None,
                    meldex_text_presentation.merge_presentations(
                        document.presentation, row.presentation
                    ) if apply_meldex_presentation else None,
                )
                result["created" if created else "updated"] += 1
                if pair_new:
                    new_pairs.append((page_key, balloon_id, text_id))
            page.coma_count = len(page.comas)
            page_io.save_page_json(work_dir, page)
            if str(page.id) not in plan.originally_loaded_page_ids:
                page_detail.clear_page_detail(page)
    work.active_page_index = original_active if -1 <= original_active < len(work.pages) else -1
    if added_pages:
        with suppress_page_number_range_update():
            page_range.sync_end_number_to_page_count(work)
    page_io.save_pages_json(work_dir, work)
    work_io.save_work_json(work_dir, work)
    return result, new_pairs


def _post_import_sync(context, work, added_pages: int, new_pairs) -> None:
    if added_pages:
        try:
            page_grid.apply_page_collection_transforms(context, work)
        except Exception:  # noqa: BLE001 - 保存済み取込をUI同期失敗だけで巻き戻さない
            _logger.exception("meldex import: page grid sync failed")
    try:
        layer_stack.sync_layer_stack_after_data_change(context)
    except Exception:  # noqa: BLE001 - 次回読込で再構築できる表示同期はベストエフォート
        _logger.exception("meldex import: layer stack sync failed")
    try:
        layer_stack.normalize_paired_layer_order(context, new_pairs)
    except Exception:  # noqa: BLE001 - 並び順の最終保証はベストエフォート
        _logger.exception("meldex import: paired layer order normalize failed")
    try:
        _sync_current_page(context, work)
    except Exception:  # noqa: BLE001 - JSON取込成功後の実体同期失敗は次回読込で回復可能
        _logger.exception("meldex import: current page object sync failed")


def _ensure_page_count(
    work,
    work_dir: Path,
    expected_page_ids: tuple[str, ...],
) -> int:
    """事前検査で確保したIDどおりに、不足ページを末尾へ追加する。

    2026-07-12 ユーザー指示により、通常のページ追加 (BMANGA_OT_page_add) と
    同様に基本枠サイズの矩形コマを1個自動生成する。既存ページには一切触れない
    対象は ``expected_page_ids`` に含まれる今回の新規ページだけに限る。
    """
    from ..operators.coma_op import create_basic_frame_coma

    for expected_page_id in expected_page_ids:
        page = page_io.register_new_page(work)
        if str(page.id) != expected_page_id:
            raise meldex_scenario_import_transaction.ScenarioImportTransactionError(
                "ページ採番が取込開始時の検査結果と一致しません"
            )
        page_io.ensure_page_dir(work_dir, page.id)
        coma = create_basic_frame_coma(work, page, work_dir)
        if str(getattr(coma, "coma_id", "") or "") != "c01":
            raise meldex_scenario_import_transaction.ScenarioImportTransactionError(
                f"{page.id} の基本枠コマ採番が不正です"
            )
    return len(expected_page_ids)


def _upsert_row(
    work, page, document_id: str, row: ScenarioRow, ordinal: int, balloon_by_name: dict, text_preset,
    text_preset_exact_match: bool, incoming_presentation: dict | None,
) -> tuple[bool, bool, str, str]:
    balloon = _find_source(page.balloons, document_id, row.row_id)
    text = _find_source(page.texts, document_id, row.row_id)
    balloon_existed = balloon is not None
    text_new = text is None
    # previous_type は _stamp_source (直後に meldex_type を row.type_name で
    # 上書きする) より前に読み取る。空タイプも正規の値なので、既存行の
    # 空→名前付き変更を見落とさない。
    previous_type = str(getattr(text, "meldex_type", "") or "") if not text_new else ""
    if text_new:
        text = page.texts.add()
        text.id = _allocate_id(page.texts, "text")
    _stamp_source(text, document_id, row)
    type_changed = not text_new and previous_type != row.type_name
    if text_new or type_changed:
        text_presets.reset_entry_to_defaults(text)
        if text_preset is not None:
            text_presets.apply_to_entry(text, text_preset.data)
    # 既定はB-MANGAプリセットを正本とする。明示的にオンの場合だけ、その上へ
    # Meldexの本文・ルビ共通設定を重ねる（B-MANGA固有項目はプリセットを維持）。
    if incoming_presentation is not None:
        meldex_text_presentation.apply_to_entry(text, incoming_presentation)
    text.speaker_name = row.type_name
    text.body = row.body
    text.ruby_spans.clear()
    for source in _rubies_by_priority(row.rubies):
        ruby = text.ruby_spans.add()
        ruby.start = int(source["start"])
        ruby.length = int(source["length"])
        ruby.ruby_text = str(source["rubyText"])
        ruby.style = str(source["style"])
        if hasattr(ruby, "origin"):
            ruby.origin = str(source.get("origin", "manual") or "manual")
        if hasattr(ruby, "priority"):
            ruby.priority = int(source.get("priority", 0) or 0)
        for source_segment in source.get("segments", ()):
            segment = ruby.segments.add()
            segment.start = int(source_segment["start"])
            segment.length = int(source_segment["length"])
            segment.ruby_text = str(source_segment["rubyText"])
    text_style.normalize_ruby_spans(text)

    # フキダシ作成判定:
    #   - テキストプリセットが一致し、かつ linked_balloon_preset が空 →
    #     フキダシなしのテキスト単体行として扱う (テキストプリセット側が
    #     フキダシとの連動を望んでいないため)。ただし既存フキダシが
    #     あれば黙って削除はせず、従来通り更新対象にする。
    #   - linked_balloon_preset にプリセット名があれば、そのプリセットで
    #     フキダシを作成/更新する。
    #   - テキストプリセットが一致しない行は、従来通り row.type_name で
    #     フキダシプリセットを独立にマッチングする。
    linked = str(getattr(text, "linked_balloon_preset", "") or "")
    skip_balloon = text_preset_exact_match and not linked

    if skip_balloon and not balloon_existed:
        text.parent_balloon_id = ""
        text.parent_kind = "page"
        text.parent_key = page_stack_key(page)
        if text_new:
            _set_initial_center(work, text, ordinal)
        _fit_text_only(text)
        page.active_text_index = len(page.texts) - 1
        return text_new, False, "", str(text.id)

    balloon_new = balloon is None
    if balloon_new:
        balloon = page.balloons.add()
        from ..operators.balloon_op import _allocate_balloon_id

        balloon.id = _allocate_balloon_id(page, work)
        balloon_core.apply_balloon_shape_defaults(balloon, force=True)
    _stamp_source(balloon, document_id, row)
    pair_new = balloon_new and text_new
    if balloon_new and text_new:
        _set_initial_center(work, text, ordinal)
    if balloon_new or type_changed:
        if linked:
            _apply_balloon_preset(balloon, balloon_by_name.get(_linked_custom_name(linked)), linked)
        else:
            _apply_balloon_preset(balloon, balloon_by_name.get(row.type_name))
    text.parent_balloon_id = balloon.id
    text.parent_kind = "page"
    text.parent_key = page_stack_key(page)
    balloon.parent_kind = "page"
    balloon.parent_key = page_stack_key(page)
    balloon.text_id = text.id
    _fit_pair(text, balloon, balloon_new=balloon_new)
    if balloon_new and text_new:
        _place_initial_pair(work, page, text, balloon)
    page.active_balloon_index = len(page.balloons) - 1
    page.active_text_index = len(page.texts) - 1
    created = balloon_new or text_new
    return created, pair_new, str(balloon.id), str(text.id)


def _find_source(collection, document_id: str, row_id: str):
    for entry in collection:
        if entry.meldex_source_document_id == document_id and entry.meldex_source_row_id == row_id:
            return entry
    return None


def _rubies_by_priority(rubies) -> tuple[dict, ...]:
    """Resolve overlaps by priority, then longer parent range, then source order."""
    selected: list[tuple[int, dict]] = []
    occupied: set[int] = set()
    ranked = sorted(
        enumerate(rubies),
        key=lambda pair: (
            -int(pair[1].get("priority", 0)),
            -int(pair[1].get("length", 0)),
            pair[0],
        ),
    )
    for source_index, ruby in ranked:
        covered = set(range(int(ruby["start"]), int(ruby["start"]) + int(ruby["length"])))
        if occupied.intersection(covered):
            continue
        occupied.update(covered)
        selected.append((source_index, ruby))
    selected.sort(key=lambda pair: (int(pair[1]["start"]), pair[0]))
    return tuple(ruby for _index, ruby in selected)


def _stamp_source(entry, document_id: str, row: ScenarioRow) -> None:
    entry.meldex_source_document_id = document_id
    entry.meldex_source_row_id = row.row_id
    entry.meldex_type = row.type_name


def _allocate_id(collection, prefix: str) -> str:
    used = {str(getattr(item, "id", "") or "") for item in collection}
    index = 1
    while f"{prefix}_{index:04d}" in used:
        index += 1
    return f"{prefix}_{index:04d}"


def _linked_custom_name(reference: str) -> str:
    value = str(reference or "")
    if value.startswith("custom:"):
        return value.split(":", 1)[1]
    return "" if value.startswith("shape:") else value


def _apply_balloon_preset(entry, preset, reference: str = "") -> None:
    _reset_balloon_to_defaults(entry)
    if reference:
        from ..utils import text_balloon_link

        text_balloon_link.apply_balloon_preset_reference(entry, reference, preset=preset)
        return
    if preset is None:
        entry.shape = "ellipse"
        entry.custom_preset_name = ""
        return
    balloon_presets.apply_linked_text_settings(entry, preset.data)
    entry.shape = "custom"
    entry.custom_preset_name = preset.name


def _reset_balloon_to_defaults(entry) -> None:
    keep = {
        "rna_type", "id", "title", "meldex_source_document_id", "meldex_source_row_id", "meldex_type",
        "x_mm", "y_mm", "width_mm", "height_mm", "parent_kind", "parent_key", "folder_key", "text_id",
    }
    properties = getattr(getattr(entry, "bl_rna", None), "properties", ())
    for prop in properties:
        key = str(getattr(prop, "identifier", "") or "")
        if key in keep or not key or bool(getattr(prop, "is_readonly", False)):
            continue
        if str(getattr(prop, "type", "") or "") in {"COLLECTION", "POINTER"}:
            continue
        default = getattr(prop, "default_array", None) if bool(getattr(prop, "is_array", False)) else getattr(prop, "default", None)
        try:
            setattr(entry, key, default)
        except (AttributeError, TypeError, ValueError):
            continue
    if hasattr(entry, "tails"):
        entry.tails.clear()
    shape_params = getattr(entry, "shape_params", None)
    for prop in getattr(getattr(shape_params, "bl_rna", None), "properties", ()):
        key = str(getattr(prop, "identifier", "") or "")
        if not key or key == "rna_type" or bool(getattr(prop, "is_readonly", False)):
            continue
        default = getattr(prop, "default_array", None) if bool(getattr(prop, "is_array", False)) else getattr(prop, "default", None)
        try:
            setattr(shape_params, key, default)
        except (AttributeError, TypeError, ValueError):
            continue
    entry.shape = "ellipse"
    balloon_core.apply_balloon_shape_defaults(entry, force=True)


def _set_initial_center(work, text, ordinal: int) -> None:
    paper = getattr(work, "paper", None)
    width = float(getattr(paper, "canvas_width_mm", 182.0) or 182.0)
    height = float(getattr(paper, "canvas_height_mm", 257.0) or 257.0)
    column, row = divmod(ordinal, 5)
    text.x_mm = max(12.0, width - 30.0 - column * 35.0)
    text.y_mm = max(12.0, height - 30.0 - row * max(25.0, (height - 50.0) / 5.0))


def _place_initial_pair(work, page, text, balloon) -> None:
    """新規取込フキダシを既存要素と重ならない紙面内の位置へ移す。"""
    paper = getattr(work, "paper", None)
    canvas_w = float(getattr(paper, "canvas_width_mm", 182.0) or 182.0)
    canvas_h = float(getattr(paper, "canvas_height_mm", 257.0) or 257.0)
    width = float(balloon.width_mm)
    height = float(balloon.height_mm)
    margin = 12.0
    gap = 8.0
    occupied = [item for item in page.balloons if item != balloon]
    column_step = max(48.0, width + gap)
    for column in range(8):
        left = canvas_w - margin - width - column * column_step
        if left < margin:
            break
        top = canvas_h - margin
        while top - height >= margin:
            bottom = top - height
            colliders = [
                item for item in occupied
                if left < float(item.x_mm + item.width_mm) + gap
                and left + width + gap > float(item.x_mm)
                and bottom < float(item.y_mm + item.height_mm) + gap
                and top + gap > float(item.y_mm)
            ]
            if not colliders:
                dx = left - float(balloon.x_mm)
                dy = bottom - float(balloon.y_mm)
                balloon.x_mm += dx
                balloon.y_mm += dy
                text.x_mm += dx
                text.y_mm += dy
                return
            top = min(float(item.y_mm) for item in colliders) - gap


def _fit_pair(text, balloon, *, balloon_new: bool) -> None:
    from ..operators import text_edit_runtime
    from ..utils import text_balloon_link

    if balloon_new:
        text_center_x = float(text.x_mm)
        text_center_y = float(text.y_mm)
    else:
        balloon_center_x = float(balloon.x_mm) + float(balloon.width_mm) * 0.5
        balloon_center_y = float(balloon.y_mm) + float(balloon.height_mm) * 0.5
        text_center_x = balloon_center_x - float(getattr(balloon, "linked_text_offset_x_mm", 0.0) or 0.0)
        text_center_y = balloon_center_y - float(getattr(balloon, "linked_text_offset_y_mm", 0.0) or 0.0)
    width, height = text_edit_runtime.natural_text_outer_size(text)
    text.width_mm = max(2.0, width)
    text.height_mm = max(2.0, height)
    text.x_mm = text_center_x - float(text.width_mm) * 0.5
    text.y_mm = text_center_y - float(text.height_mm) * 0.5
    text_balloon_link.fit_linked_balloon_to_text(text, balloon)


def _fit_text_only(text) -> None:
    from ..operators import text_edit_runtime

    width, height = text_edit_runtime.natural_text_outer_size(text)
    text.width_mm = max(2.0, width)
    text.height_mm = max(2.0, height)


def _sync_current_page(context, work) -> None:
    page_id = page_file_scene.current_page_id(getattr(context, "scene", None))
    if not page_id:
        return
    page = next((item for item in work.pages if item.id == page_id), None)
    if page is None or not page.detail_loaded:
        return
    for balloon in page.balloons:
        if balloon.meldex_source_document_id:
            balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=balloon, page=page)
    for text in page.texts:
        if text.meldex_source_document_id:
            text_real_object.ensure_text_real_object(scene=context.scene, entry=text, page=page)
