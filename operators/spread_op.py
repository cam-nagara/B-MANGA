"""ページの見開き変更・解除 Operator (計画書 3.3.4 / Phase 3 データ保持版).

**データ保持**:
- 見開き統合時: 左右両ページの panels / balloons / texts / GP ストローク / cNN.blend を
  すべて見開きページに引き継ぐ
- 見開き解除時: 見開きページの panels / balloons / texts / GP を中心 x で左右
  ページに振り分けて保持

**座標規約**:
- 見開きキャンバスは左ページ幅 + 右ページ開始位置として扱う。
  「トンボを合わせる」が有効な場合は右ページ開始位置に間隔を反映する
- 原点 (0, 0) はキャンバスの左下 = 左ページの原点
- 左ページ (b = 0002) の内容は x ∈ [0, W] に配置
- 右ページ (a = 0001) の内容は x ∈ [R, R+W] に配置
  - メタデータ (panels / balloons / texts) は PropertyGroup 上の x に ``+R`` を加算
  - GP オブジェクトは subpage offset custom property で ``+R`` の位置ずらしを表現
    (strokes 自体は触らず、obj.location のみで移動 → stroke データを破壊しない)

**見開き解除**:
- 各エンティティの中心 x を見て、右ページ開始位置より左なら左ページ (0002)、
  それ以上なら右ページ (0001) に振り分け
- 右ページに振り分けた panels / balloons / texts は右ページ開始位置ぶん x を戻す
- GP は subpage-offset 付きの右サブ GP (``*_R``) を右ページ用として切り出し、
  主 GP を左ページ用として切り出す
"""

from __future__ import annotations

import shutil
from pathlib import Path

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty
from bpy.types import Operator

from ..core.work import get_work
from ..io import page_io, coma_io, schema
from ..utils import gpencil as gp_utils
from ..utils import log, page_detail, page_grid, paths

_logger = log.get_logger(__name__)


# ---------- 共通ヘルパ ----------


def _shift_coma_entry_x(entry, dx_mm: float) -> None:
    """coma_entry の rect / 多角形頂点 x を dx_mm ずらす."""
    entry.rect_x_mm = entry.rect_x_mm + dx_mm
    for v in entry.vertices:
        v.x_mm = v.x_mm + dx_mm


def _shift_balloon_entry_x(entry, dx_mm: float) -> None:
    entry.x_mm = entry.x_mm + dx_mm


def _shift_text_entry_x(entry, dx_mm: float) -> None:
    entry.x_mm = entry.x_mm + dx_mm


def _reset_split_page_identity(entry, page_id: str) -> None:
    entry.id = page_id
    entry.title = ""
    entry.dir_rel = f"{page_id}/"
    entry.spread = False
    entry.original_pages.clear()


def _split_page_entries(work, index: int, reading_first_id: str, reading_second_id: str):
    right_half = work.pages[index]
    left_half = work.pages[index + 1]
    _reset_split_page_identity(right_half, reading_first_id)
    _reset_split_page_identity(left_half, reading_second_id)
    return right_half, left_half


def _copy_coma_entry(src, dst) -> None:
    """ComaEntry の内容を schema 経由で複製. coma_id は呼出側で上書き."""
    data = schema.coma_entry_to_dict(src)
    schema.coma_entry_from_dict(dst, data)


def _copy_balloon_entry(src, dst) -> None:
    data = schema.balloon_entry_to_dict(src)
    schema.balloon_entry_from_dict(dst, data)


def _copy_text_entry(src, dst) -> None:
    data = schema.text_entry_to_dict(src)
    schema.text_entry_from_dict(dst, data)


def _reallocate_balloon_id(used_ids: set[str]) -> str:
    """新しい balloon id を採番。``used_ids`` と衝突しないように."""
    i = 1
    while True:
        candidate = f"balloon_{i:04d}"
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate
        i += 1


def _reallocate_text_id(used_ids: set[str]) -> str:
    i = 1
    while True:
        candidate = f"text_{i:04d}"
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate
        i += 1


def _subpage_gp_name(page_id: str, suffix: str = "") -> str:
    """見開きの右サブ GP 用の Object 名を返す.

    primary (左ページ用) は ``page_{page_id}_sketch``。
    suffix="_R" で ``page_{page_id}_sketch_R`` を返す。
    """
    return f"{gp_utils.page_gp_object_name(page_id)}{suffix}"


def _subpage_gp_data_name(page_id: str, suffix: str = "") -> str:
    return f"{gp_utils.page_gp_data_name(page_id)}{suffix}"


# ---------- 見開き統合 ----------


def _merge_pages_pp_groups(
    merged_entry, b_entry, right_page_offset_mm: float
) -> None:
    """merged_entry (元 a) の panels/balloons/texts を右ページ位置へシフト、b の内容を追加.

    - merged.comas: 既存 a の panels を右ページ位置へ移動。続いて b の panels を append
      (coma_id 衝突は後段でリネーム処理)
    - balloons / texts も同様。b の balloon id / text id は merged 内で衝突する可能性が
      あるため採番し直し。text の parent_balloon_id は新 id に追随させる。
    """
    # a の既存 panels/balloons/texts を右半分へシフト
    for panel in merged_entry.comas:
        _shift_coma_entry_x(panel, right_page_offset_mm)
    for balloon in merged_entry.balloons:
        _shift_balloon_entry_x(balloon, right_page_offset_mm)
    for text in merged_entry.texts:
        _shift_text_entry_x(text, right_page_offset_mm)

    # b の balloon id → merged 内でユニーク化するためのマップ
    merged_balloon_ids = {b.id for b in merged_entry.balloons}
    balloon_id_map: dict[str, str] = {}
    for b_balloon in b_entry.balloons:
        if b_balloon.id in merged_balloon_ids or not b_balloon.id:
            new_id = _reallocate_balloon_id(merged_balloon_ids)
        else:
            new_id = b_balloon.id
            merged_balloon_ids.add(new_id)
        balloon_id_map[b_balloon.id] = new_id

    # b の balloons を merged に追加 (シフトなし: 左半分)
    for b_balloon in b_entry.balloons:
        new_entry = merged_entry.balloons.add()
        _copy_balloon_entry(b_balloon, new_entry)
        new_entry.id = balloon_id_map[b_balloon.id]

    # b の texts を merged に追加. parent_balloon_id を新 id にマップ
    merged_text_ids = {t.id for t in merged_entry.texts}
    for b_text in b_entry.texts:
        new_entry = merged_entry.texts.add()
        _copy_text_entry(b_text, new_entry)
        if new_entry.id in merged_text_ids or not new_entry.id:
            new_entry.id = _reallocate_text_id(merged_text_ids)
        else:
            merged_text_ids.add(new_entry.id)
        if b_text.parent_balloon_id in balloon_id_map:
            new_entry.parent_balloon_id = balloon_id_map[b_text.parent_balloon_id]


def _merge_basic_frame_comas(merged_entry, work, right_offset: float) -> None:
    """見開き統合後、左右の基本枠コマが揃っていたら 1 つのフル幅コマに統合する."""
    from ..utils import geom as _geom

    paper = work.paper
    cw = paper.canvas_width_mm
    eps = 1.5

    left_rect = _geom.inner_frame_rect(paper, is_left_half=True)
    legacy_rect = _geom.inner_frame_rect(paper, is_left_half=False)
    right_rect = _geom.Rect(
        right_offset + (cw - paper.inner_frame_width_mm) / 2.0 + paper.inner_frame_offset_x_mm,
        (paper.canvas_height_mm - paper.inner_frame_height_mm) / 2.0 + paper.inner_frame_offset_y_mm,
        paper.inner_frame_width_mm,
        paper.inner_frame_height_mm,
    )

    def _matches(coma, rect):
        return (
            abs(getattr(coma, "rect_x_mm", -999) - rect.x) < eps
            and abs(getattr(coma, "rect_y_mm", -999) - rect.y) < eps
            and abs(getattr(coma, "rect_width_mm", -999) - rect.width) < eps
            and abs(getattr(coma, "rect_height_mm", -999) - rect.height) < eps
        )

    def _matches_left(coma):
        return _matches(coma, left_rect) or _matches(coma, legacy_rect)

    left_idx = None
    right_idx = None
    for i, coma in enumerate(merged_entry.comas):
        if left_idx is None and _matches_left(coma):
            left_idx = i
        if right_idx is None and _matches(coma, right_rect):
            right_idx = i

    if left_idx is None or right_idx is None:
        return

    actual_left = merged_entry.comas[left_idx]
    actual_left_x = float(getattr(actual_left, "rect_x_mm", left_rect.x))
    span_x = actual_left_x
    span_width = (right_rect.x + right_rect.width) - actual_left_x

    keep_idx = min(left_idx, right_idx)
    remove_idx = max(left_idx, right_idx)
    merged_entry.comas.remove(remove_idx)
    keep = merged_entry.comas[keep_idx]
    keep.shape_type = "rect"
    keep.rect_x_mm = span_x
    keep.rect_y_mm = left_rect.y
    keep.rect_width_mm = span_width
    keep.rect_height_mm = left_rect.height
    keep.vertices.clear()


def _merge_coma_files(
    work_dir: Path,
    merged_entry,
    b_entry,
    a_old_id: str,
    b_old_id: str,
    spread_id: str,
) -> None:
    """a のディレクトリを spread に rename、b の panel_* を spread へコピー/rename.

    処理順:
    1. ``pages/{a_old_id}/`` を ``pages/{spread_id}/`` へ rename (a の panel_* もそのまま)
    2. b の各 panel について空き stem を採番し、``pages/{b_old_id}/panels/`` から
       ``pages/{spread_id}/panels/`` に move
    3. b の panel PropertyGroup を merged.comas に copy し、coma_id を新 stem に差替
    4. ``pages/{b_old_id}/`` ディレクトリ (panels 空のはず) を remove

    merged_entry の panels は既に右ページ位置へシフト済 (呼出側で実施)。
    """
    work_dir = Path(work_dir)
    a_dir = paths.page_dir(work_dir, a_old_id)
    spread_dir = paths.page_dir(work_dir, spread_id)

    # 1) a のディレクトリを spread_id へ rename. (a == spread_id の場合は不要)
    if spread_dir.exists():
        raise FileExistsError(f"spread destination already exists: {spread_dir}")
    if a_dir.exists():
        a_dir.rename(spread_dir)
    else:
        # 念のため空の骨格を用意
        page_io.ensure_page_dir(work_dir, spread_id)

    # 2) b の panel ファイルを spread にコピー (stem 衝突回避で新採番)
    stem_remap: dict[str, str] = {}
    for b_panel in b_entry.comas:
        old_stem = b_panel.coma_id
        if not old_stem or not paths.is_valid_coma_id(old_stem):
            continue
        new_stem = coma_io.allocate_new_coma_id(work_dir, spread_id)
        try:
            coma_io.move_coma_files(work_dir, b_old_id, spread_id, old_stem, new_stem)
        except FileNotFoundError:
            # panel ファイルが存在しない PropertyGroup だけのケース (新規追加直後など)
            pass
        except Exception:  # noqa: BLE001
            _logger.exception(
                "merge: panel files move failed %s/%s -> %s/%s",
                b_old_id, old_stem, spread_id, new_stem,
            )
            continue
        stem_remap[old_stem] = new_stem

    # 3) b の panels を merged.comas に append. coma_id / id を新 stem に差替
    for b_panel in b_entry.comas:
        new_entry = merged_entry.comas.add()
        _copy_coma_entry(b_panel, new_entry)
        old_stem = b_panel.coma_id
        new_stem = stem_remap.get(old_stem, old_stem)
        # 衝突チェック: merged 内で同名 coma_id が既にあるなら更に新採番
        existing = {p.coma_id for p in merged_entry.comas if p is not new_entry}
        if new_stem in existing:
            new_stem = coma_io.allocate_new_coma_id(work_dir, spread_id)
        new_entry.coma_id = new_stem
        new_entry.id = new_stem
        # coma_id を書き換えた場合、.json メタも上書き再保存
        try:
            coma_io.save_coma_meta(work_dir, spread_id, new_entry)
        except Exception:  # noqa: BLE001
            _logger.exception("merge: save_coma_meta failed for %s", new_entry.coma_id)

    # 4) 空になった b ディレクトリを削除
    b_dir = paths.page_dir(work_dir, b_old_id)
    if b_dir.exists():
        try:
            shutil.rmtree(b_dir)
        except Exception:  # noqa: BLE001
            _logger.exception("merge: remove %s failed", b_dir)

    # 5) a の panel_*.json も +W シフト後の座標で上書き保存
    #    (page.json が load 時のソース・オブ・トゥルースだが、panel_*.json
    #    も揃えておく方がデータ不整合を起こしにくい)
    for panel in merged_entry.comas:
        if not panel.coma_id or not paths.is_valid_coma_id(panel.coma_id):
            continue
        # b 由来の panels は step 3 で既に save 済なので、a 由来のみ書き直す
        # 判定: stem_remap の値は b 由来のみ → a 由来を見分けるため
        #       stem_remap.values() に含まれない coma_id を対象とする
        if panel.coma_id in stem_remap.values():
            continue
        try:
            coma_io.save_coma_meta(work_dir, spread_id, panel)
        except Exception:  # noqa: BLE001
            _logger.exception("merge: resave panel meta failed for %s", panel.coma_id)


def _merge_page_gpencil(
    scene,
    a_old_id: str,
    b_old_id: str,
    spread_id: str,
    right_page_offset_mm: float,
) -> None:
    """a / b の GP オブジェクトを見開きページ Collection に再配置.

    - b (左) の GP → 主 GP として ``page_{spread_id}_sketch`` にリネーム。
      obj.location は grid offset のみ (subpage_offset = 0)。
    - a (右) の GP → 副 GP として ``page_{spread_id}_sketch_R`` にリネーム。
      subpage_offset_x_mm = 右ページ開始位置を custom property にセット。
    - 元 Collection ``page_{a_old_id}`` / ``page_{b_old_id}`` は削除。
    - Collection ``page_{spread_id}`` を新設して両 GP を収容。
    """
    # 取得
    a_obj = gp_utils.get_page_gpencil(a_old_id)
    b_obj = gp_utils.get_page_gpencil(b_old_id)
    a_coll = gp_utils.get_page_collection(a_old_id)
    b_coll = gp_utils.get_page_collection(b_old_id)

    # 新 Collection 作成 (他に衝突していれば既存を再利用)
    spread_coll = gp_utils.ensure_page_collection(scene, spread_id)

    # b → 主 GP
    if b_obj is not None:
        # name の衝突を避けるため、先に a を一時名に退避
        if a_obj is not None:
            tmp_obj_name = f"__bmanga_tmp_{a_old_id}_R_obj"
            tmp_data_name = f"__bmanga_tmp_{a_old_id}_R_data"
            gp_utils.rename_gp_object_and_data(a_obj, tmp_obj_name, tmp_data_name)
        # b を spread の主 GP 名にリネーム
        gp_utils.rename_gp_object_and_data(
            b_obj,
            gp_utils.page_gp_object_name(spread_id),
            gp_utils.page_gp_data_name(spread_id),
        )
        # spread Collection のみにリンク
        gp_utils.relink_object_to_page(scene, b_obj, spread_id)
        # 主 GP は subpage offset = 0
        b_obj[page_grid.SUBPAGE_OFFSET_X_PROP] = 0.0
        b_obj[page_grid.SUBPAGE_OFFSET_Y_PROP] = 0.0

    # a → 副 GP (右半分)
    if a_obj is not None:
        gp_utils.rename_gp_object_and_data(
            a_obj,
            _subpage_gp_name(spread_id, "_R"),
            _subpage_gp_data_name(spread_id, "_R"),
        )
        gp_utils.relink_object_to_page(scene, a_obj, spread_id)
        a_obj[page_grid.SUBPAGE_OFFSET_X_PROP] = float(right_page_offset_mm)
        a_obj[page_grid.SUBPAGE_OFFSET_Y_PROP] = 0.0

    # 旧 Collection (a / b) を削除
    for coll in (a_coll, b_coll):
        if coll is None or coll == spread_coll:
            continue
        try:
            bpy.data.collections.remove(coll)
        except Exception:  # noqa: BLE001
            _logger.exception("merge: remove collection %s failed", coll.name)


# ---------- 見開き解除 ----------


def _split_page_assign_entries(
    spread_data: dict,
    left_entry,
    right_entry,
    right_page_offset_mm: float,
) -> dict:
    """spread の panels/balloons/texts を中心 x で左右ページに振り分け.

    戻り値: ``{"right_coma_ids": [...], "balloon_id_map_right": {...}}``
    右ページ用のコマ stem リスト (ファイル操作の入力に使う) 等。
    """
    right_offset = float(right_page_offset_mm)

    # 振り分け: panel
    left_comas: list[dict] = []
    right_comas: list[dict] = []
    right_coma_ids: list[str] = []
    for coma_data in spread_data.get("comas", []) or []:
        data = dict(coma_data)
        shape = data.get("shape", {}) if isinstance(data.get("shape", {}), dict) else {}
        shape_type = str(shape.get("type", "rect") or "rect")
        if shape_type == "rect":
            rect = shape.get("rect", {}) if isinstance(shape.get("rect", {}), dict) else {}
            center_x = float(rect.get("x", 0.0)) + float(rect.get("widthMm", 0.0)) / 2.0
        elif shape_type == "polygon" and len(shape.get("vertices", []) or []) > 0:
            xs = [float(pair[0]) for pair in shape.get("vertices", []) if len(pair) > 0]
            center_x = (min(xs) + max(xs)) / 2.0 if xs else 0.0
        else:
            rect = shape.get("rect", {}) if isinstance(shape.get("rect", {}), dict) else {}
            center_x = float(rect.get("x", 0.0))
        if center_x < right_offset:
            left_comas.append(data)
        else:
            right_comas.append(data)
            right_coma_ids.append(str(data.get("comaId", "") or data.get("id", "") or ""))

    # 左右ページの panels に再構築
    left_entry.comas.clear()
    right_entry.comas.clear()
    for d in left_comas:
        e = left_entry.comas.add()
        schema.coma_entry_from_dict(e, d)
        # 左ページはそのまま
    for d in right_comas:
        e = right_entry.comas.add()
        schema.coma_entry_from_dict(e, d)
        # 右ページは x を右ページ開始位置ぶん戻す
        _shift_coma_entry_x(e, -right_offset)
    left_entry.active_coma_index = 0 if len(left_entry.comas) > 0 else -1
    right_entry.active_coma_index = 0 if len(right_entry.comas) > 0 else -1
    left_entry.coma_count = len(left_entry.comas)
    right_entry.coma_count = len(right_entry.comas)

    # balloon 振り分け
    left_balloon_ids: set[str] = set()
    right_balloon_ids: set[str] = set()
    balloon_to_page: dict[str, str] = {}  # balloon_id -> "L" or "R"
    left_balloons: list[dict] = []
    right_balloons: list[dict] = []
    for balloon_data in spread_data.get("balloons", []) or []:
        data = dict(balloon_data)
        balloon_id = str(data.get("id", "") or "")
        center_x = float(data.get("xMm", 0.0)) + float(data.get("widthMm", 0.0)) / 2.0
        if center_x < right_offset:
            left_balloons.append(data)
            balloon_to_page[balloon_id] = "L"
            left_balloon_ids.add(balloon_id)
        else:
            right_balloons.append(data)
            balloon_to_page[balloon_id] = "R"
            right_balloon_ids.add(balloon_id)

    left_entry.balloons.clear()
    right_entry.balloons.clear()
    for d in left_balloons:
        e = left_entry.balloons.add()
        schema.balloon_entry_from_dict(e, d)
    for d in right_balloons:
        e = right_entry.balloons.add()
        schema.balloon_entry_from_dict(e, d)
        _shift_balloon_entry_x(e, -right_offset)
    left_entry.active_balloon_index = 0 if len(left_entry.balloons) > 0 else -1
    right_entry.active_balloon_index = 0 if len(right_entry.balloons) > 0 else -1

    # text 振り分け. parent_balloon_id がついていれば親の所属ページに従う
    left_texts: list[dict] = []
    right_texts: list[dict] = []
    for text_data in spread_data.get("texts", []) or []:
        data = dict(text_data)
        parent_balloon_id = str(data.get("parentBalloonId", "") or "")
        if parent_balloon_id and parent_balloon_id in balloon_to_page:
            page_side = balloon_to_page[parent_balloon_id]
        else:
            center_x = float(data.get("xMm", 0.0)) + float(data.get("widthMm", 0.0)) / 2.0
            page_side = "L" if center_x < right_offset else "R"
        if page_side == "L":
            left_texts.append(data)
        else:
            right_texts.append(data)

    left_entry.texts.clear()
    right_entry.texts.clear()
    for d in left_texts:
        e = left_entry.texts.add()
        schema.text_entry_from_dict(e, d)
        # parent_balloon_id は左ページに残存する balloon だけ有効
        if e.parent_balloon_id and e.parent_balloon_id not in left_balloon_ids:
            e.parent_balloon_id = ""
    for d in right_texts:
        e = right_entry.texts.add()
        schema.text_entry_from_dict(e, d)
        _shift_text_entry_x(e, -right_offset)
        if e.parent_balloon_id and e.parent_balloon_id not in right_balloon_ids:
            e.parent_balloon_id = ""
    left_entry.active_text_index = 0 if len(left_entry.texts) > 0 else -1
    right_entry.active_text_index = 0 if len(right_entry.texts) > 0 else -1

    return {
        "right_coma_ids": right_coma_ids,
        "left_page": schema.page_to_dict(left_entry),
        "right_page": schema.page_to_dict(right_entry),
    }


def _restore_split_assignment(assignment: dict, left_entry, right_entry) -> None:
    """CollectionProperty の参照取り直し後に左右ページの詳細を戻す."""
    left_data = assignment.get("left_page", {})
    right_data = assignment.get("right_page", {})
    if isinstance(left_data, dict):
        schema.page_from_dict(left_entry, left_data)
    if isinstance(right_data, dict):
        schema.page_from_dict(right_entry, right_data)


def _split_coma_files(
    work_dir: Path,
    spread_id: str,
    left_id: str,
    right_id: str,
    right_coma_ids: list[str],
) -> None:
    """spread/ ディレクトリを 2 ページに分割してファイルを配分.

    実装:
    1. ``pages/{spread_id}/`` ディレクトリを ``pages/{left_id}/`` に rename
      (左ページの panel ファイルはそのまま残る)
    2. 右ページ用に ``pages/{right_id}/`` を新設し、右ページに属する
      panel stem の一式を left_id から right_id へ move
    """
    work_dir = Path(work_dir)
    spread_dir = paths.page_dir(work_dir, spread_id)
    left_dir = paths.page_dir(work_dir, left_id)
    right_dir = paths.page_dir(work_dir, right_id)

    if left_dir.exists() and left_dir != spread_dir:
        raise FileExistsError(f"left destination already exists: {left_dir}")
    if right_dir.exists():
        raise FileExistsError(f"right destination already exists: {right_dir}")

    if spread_dir.exists() and spread_dir != left_dir:
        spread_dir.rename(left_dir)
    else:
        page_io.ensure_page_dir(work_dir, left_id)

    page_io.ensure_page_dir(work_dir, right_id)

    for stem in right_coma_ids:
        if not stem or not paths.is_valid_coma_id(stem):
            continue
        try:
            coma_io.move_coma_files(work_dir, left_id, right_id, stem, stem)
        except FileNotFoundError:
            pass  # panel ファイル未作成のエントリ
        except FileExistsError:
            # 右ディレクトリ側で衝突したら採番し直し
            new_stem = coma_io.allocate_new_coma_id(work_dir, right_id)
            coma_io.move_coma_files(work_dir, left_id, right_id, stem, new_stem)
            # PropertyGroup 側の coma_id は呼出側で再計算するのが本来だが、
            # ここで検出した場合は警告のみ (頻度は低いケース)
            _logger.warning(
                "split: panel stem collision %s -> renamed to %s (PropertyGroup unchanged)",
                stem, new_stem,
            )


def _split_page_gpencil(
    scene,
    spread_id: str,
    left_id: str,
    right_id: str,
) -> None:
    """見開きページの主 GP / _R サブ GP を左右ページ単独の GP に戻す.

    - 主 GP (``page_{spread_id}_sketch``) → 左ページ用にリネーム, subpage offset クリア
    - 副 GP (``page_{spread_id}_sketch_R``) → 右ページ用にリネーム, subpage offset クリア
    - 見開き Collection を削除、左/右 Collection を新設して各 GP を再リンク
    """
    primary_name = gp_utils.page_gp_object_name(spread_id)
    sub_manga = _subpage_gp_name(spread_id, "_R")
    primary = bpy.data.objects.get(primary_name)
    sub = bpy.data.objects.get(sub_manga)
    spread_coll = gp_utils.get_page_collection(spread_id)

    # primary → left_id 用 GP
    if primary is not None:
        gp_utils.rename_gp_object_and_data(
            primary,
            gp_utils.page_gp_object_name(left_id),
            gp_utils.page_gp_data_name(left_id),
        )
        gp_utils.relink_object_to_page(scene, primary, left_id)
        for key in (page_grid.SUBPAGE_OFFSET_X_PROP, page_grid.SUBPAGE_OFFSET_Y_PROP):
            try:
                if key in primary:
                    del primary[key]
            except Exception:  # noqa: BLE001
                pass
    else:
        # 主 GP が無ければ左ページに空 GP を新規生成
        gp_utils.ensure_page_gpencil(scene, left_id)

    # sub → right_id 用 GP
    if sub is not None:
        gp_utils.rename_gp_object_and_data(
            sub,
            gp_utils.page_gp_object_name(right_id),
            gp_utils.page_gp_data_name(right_id),
        )
        gp_utils.relink_object_to_page(scene, sub, right_id)
        for key in (page_grid.SUBPAGE_OFFSET_X_PROP, page_grid.SUBPAGE_OFFSET_Y_PROP):
            try:
                if key in sub:
                    del sub[key]
            except Exception:  # noqa: BLE001
                pass
    else:
        gp_utils.ensure_page_gpencil(scene, right_id)

    # 見開き Collection を削除 (空のはず)
    if spread_coll is not None:
        # relink で spread_coll から抜けているはず。念のため中身を確認。
        try:
            bpy.data.collections.remove(spread_coll)
        except Exception:  # noqa: BLE001
            _logger.exception("split: remove spread collection failed")


# ---------- Operator ----------


class BMANGA_OT_pages_merge_spread(Operator):
    """連続 2 ページを見開きに統合 (データ保持つき)."""

    bl_idname = "bmanga.pages_merge_spread"
    bl_label = "見開きに変更"
    bl_options = {"REGISTER", "UNDO"}

    left_index: IntProperty(  # type: ignore[valid-type]
        name="左ページ index",
        default=-1,
        min=-1,
    )
    tombo_aligned: BoolProperty(  # type: ignore[valid-type]
        name="トンボを合わせる",
        default=True,
    )
    tombo_gap_mm: FloatProperty(  # type: ignore[valid-type]
        name="間隔 (mm)",
        description="仕上がり枠間のギャップ。負値はノド側を重ねる方向",
        default=-9.60,
    )

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and len(w.pages) >= 2)

    def invoke(self, context, event):
        work = get_work(context)
        if self.left_index < 0:
            self.left_index = work.active_page_index
        # 作品ファイルではページ詳細を常駐させないため、結合対象 2 ページの
        # 詳細をここで読み込む (ダイアログの件数表示と結合処理に使う)
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
        a = work.pages[left]
        b = work.pages[left + 1]
        col = layout.column()
        col.label(text=f"{a.title} と {b.title} を見開きに統合します")
        summary = (
            f"コマ: {len(a.comas) + len(b.comas)} / "
            f"フキダシ: {len(a.balloons) + len(b.balloons)} / "
            f"テキスト: {len(a.texts) + len(b.texts)} を保持"
        )
        col.label(text=summary, icon="INFO")
        col.separator()
        col.label(
            text="右ページの内容はトンボ合わせの間隔を反映して配置されます",
            icon="ARROW_LEFTRIGHT",
        )
        col.separator()
        col.prop(self, "tombo_aligned")
        sub = col.column()
        sub.enabled = self.tombo_aligned
        sub.prop(self, "tombo_gap_mm")

    def execute(self, context):
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        left = self.left_index
        if not (0 <= left < len(work.pages) - 1):
            self.report({"ERROR"}, "左ページの選択が不正です")
            return {"CANCELLED"}
        a = work.pages[left]
        b = work.pages[left + 1]
        if a.spread or b.spread:
            self.report({"ERROR"}, "既に見開きのページは結合できません")
            return {"CANCELLED"}
        page_detail.ensure_page_detail(work, a)
        page_detail.ensure_page_detail(work, b)
        work_dir = Path(work.work_dir)

        # 結合 ID (左=a, 右=b を連結した文字列; 読み順準拠)
        try:
            head_a = int(a.id.split("-", 1)[0].lstrip("p"))
            head_b = int(b.id.split("-", 1)[0].lstrip("p"))
        except ValueError:
            self.report({"ERROR"}, "ページ ID が不正です")
            return {"CANCELLED"}
        spread_id = paths.format_spread_id(head_a, head_b)

        a_old_id = a.id
        b_old_id = b.id
        W = float(work.paper.canvas_width_mm)
        FW = float(work.paper.finish_width_mm)
        right_offset = page_grid.spread_right_page_offset_mm_for_values(
            W,
            bool(self.tombo_aligned),
            float(self.tombo_gap_mm),
            finish_width_mm=FW,
        )

        try:
            # 1) メタデータ統合: a の panels/balloons/texts を +R、b を +0 で追加
            _merge_pages_pp_groups(a, b, right_offset)

            # 1.5) 左右の基本枠コマをフル幅 1 コマに統合
            _merge_basic_frame_comas(a, work, right_offset)

            # 2) ファイル操作: a dir を spread_id にリネームし、b の panels をコピー統合
            _merge_coma_files(work_dir, a, b, a_old_id, b_old_id, spread_id)

            # 3) GP: 左/右 GP を spread Collection に再配置、subpage_offset を設定
            _merge_page_gpencil(context.scene, a_old_id, b_old_id, spread_id, right_offset)

            # 4) pages コレクション: b を削除し、a を spread_id にリブランド
            work.pages.remove(left + 1)
            merged = work.pages[left]
            merged.id = spread_id
            merged.title = ""
            merged.dir_rel = f"{spread_id}/"
            merged.spread = True
            merged.tombo_aligned = self.tombo_aligned
            merged.tombo_gap_mm = self.tombo_gap_mm
            merged.original_pages.clear()
            r1 = merged.original_pages.add()
            r1.page_id = paths.format_page_id(head_a)
            r2 = merged.original_pages.add()
            r2.page_id = paths.format_page_id(head_b)
            merged.coma_count = len(merged.comas)
            work.active_page_index = left

            # 5) grid transform を再配置
            page_grid.apply_page_collection_transforms(context, work)

            # 6) JSON 保存
            page_io.save_page_json(work_dir, merged)
            page_io.save_pages_json(work_dir, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("pages_merge_spread failed")
            self.report({"ERROR"}, f"見開き統合失敗: {exc}")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"見開き統合: {spread_id} "
            f"(panels {len(merged.comas)} / balloons {len(merged.balloons)} / texts {len(merged.texts)})",
        )
        return {"FINISHED"}


class BMANGA_OT_pages_split_spread(Operator):
    """見開きを 2 ページに解除 (データ保持つき)."""

    bl_idname = "bmanga.pages_split_spread"
    bl_label = "見開きを解除"
    bl_options = {"REGISTER", "UNDO"}

    spread_index: IntProperty(default=-1, min=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        if not (w and w.loaded):
            return False
        idx = w.active_page_index
        return 0 <= idx < len(w.pages) and w.pages[idx].spread

    def invoke(self, context, event):
        work = get_work(context)
        if self.spread_index < 0:
            self.spread_index = work.active_page_index
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        idx = self.spread_index
        if not (0 <= idx < len(work.pages)):
            return {"CANCELLED"}
        entry = work.pages[idx]
        if not entry.spread:
            self.report({"ERROR"}, "見開きページではありません")
            return {"CANCELLED"}
        if len(entry.original_pages) < 2:
            self.report({"ERROR"}, "結合元ページ情報が失われているため解除できません")
            return {"CANCELLED"}
        page_detail.ensure_page_detail(work, entry)
        work_dir = Path(work.work_dir)
        spread_id_prev = entry.id
        # original_pages[0] は merge 時に head_a (読み順で先) を保存している。
        #   読み順 先 (earlier) = head_a = 見開き内の「右半分」  → reading_first / physical_right
        #   読み順 後 (later)   = head_b = 見開き内の「左半分」  → reading_second / physical_left
        reading_first_id = entry.original_pages[0].page_id   # = "0001" 側 = 物理右半分
        reading_second_id = entry.original_pages[1].page_id  # = "0002" 側 = 物理左半分

        W = float(work.paper.canvas_width_mm)
        FW = float(work.paper.finish_width_mm)
        right_offset = page_grid.spread_right_page_offset_mm(entry, W, FW)

        try:
            # 1) spread entry の内容を dict で snapshot (後で振り分けに使う)
            spread_data = schema.page_to_dict(entry)

            # 2) spread entry を削除し、読み順で 2 ページを追加
            #    読み順 先 = 物理右半分 を idx (前方) に、
            #    読み順 後 = 物理左半分 を idx+1 (後方) に。
            work.pages.remove(idx)

            right_half = work.pages.add()
            _reset_split_page_identity(right_half, reading_first_id)
            work.pages.move(len(work.pages) - 1, idx)

            left_half = work.pages.add()
            _reset_split_page_identity(left_half, reading_second_id)
            work.pages.move(len(work.pages) - 1, idx + 1)

            # CollectionProperty は add/remove/move 後に Python 側の参照が古くなり得る。
            # 振り分け直前に、必ずページ一覧から取り直す。
            right_half, left_half = _split_page_entries(
                work, idx, reading_first_id, reading_second_id
            )

            # 振り分け: 左右ページの境目を、ノド側の重なり/空き込みで扱う
            assignment = _split_page_assign_entries(spread_data, left_half, right_half, right_offset)
            right_coma_ids = assignment["right_coma_ids"]

            right_half, left_half = _split_page_entries(
                work, idx, reading_first_id, reading_second_id
            )
            _restore_split_assignment(assignment, left_half, right_half)

            # 3) ファイル操作: spread/ → 物理左ページ (reading_second_id) dir に rename、
            #    物理右ページ (reading_first_id) 用に panel files を move
            _split_coma_files(
                work_dir, spread_id_prev, reading_second_id, reading_first_id, right_coma_ids
            )

            # 4) GP 分割 (primary → 左半分 = reading_second_id, sub_R → 右半分 = reading_first_id)
            _split_page_gpencil(
                context.scene, spread_id_prev, reading_second_id, reading_first_id
            )

            # 5) pages コレクションの active を読み順 先 (物理右) へ
            work.active_page_index = idx

            # 6) coma_count 再計算
            right_half, left_half = _split_page_entries(
                work, idx, reading_first_id, reading_second_id
            )
            _restore_split_assignment(assignment, left_half, right_half)
            left_half.coma_count = len(left_half.comas)
            right_half.coma_count = len(right_half.comas)

            # 7) grid transform を再配置
            page_grid.apply_page_collection_transforms(context, work)

            # 8) JSON 保存
            #    page.json がロード時のソース・オブ・トゥルース。panel_*.json
            #    も座標不整合を避けるため左右ページ分を個別に書き直す。
            right_half, left_half = _split_page_entries(
                work, idx, reading_first_id, reading_second_id
            )
            _restore_split_assignment(assignment, left_half, right_half)
            left_half.coma_count = len(left_half.comas)
            right_half.coma_count = len(right_half.comas)
            for e in left_half.comas:
                if e.coma_id and paths.is_valid_coma_id(e.coma_id):
                    try:
                        coma_io.save_coma_meta(work_dir, left_half.id, e)
                    except Exception:  # noqa: BLE001
                        _logger.exception("split: resave panel %s/%s failed", left_half.id, e.coma_id)
            for e in right_half.comas:
                if e.coma_id and paths.is_valid_coma_id(e.coma_id):
                    try:
                        coma_io.save_coma_meta(work_dir, right_half.id, e)
                    except Exception:  # noqa: BLE001
                        _logger.exception("split: resave panel %s/%s failed", right_half.id, e.coma_id)
            page_io.save_page_json(work_dir, left_half)
            page_io.save_page_json(work_dir, right_half)
            page_io.save_pages_json(work_dir, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("pages_split_spread failed")
            self.report({"ERROR"}, f"見開き解除失敗: {exc}")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"見開き解除: {reading_first_id} / {reading_second_id} "
            f"(右: panels {len(right_half.comas)} / 左: panels {len(left_half.comas)})",
        )
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_pages_merge_spread,
    BMANGA_OT_pages_split_spread,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
