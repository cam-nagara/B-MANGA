"""overview 配置の grid transform 計算.

overlay 描画・coma_picker・ページ Collection transform がすべて同じ式で
page_index → (ox_mm, oy_mm) を導出する必要があるため、この 1 ファイルに
集約する。日本漫画は右→左読みのため、ページ 0001 が x=0 で以降は負の X
方向に展開される。

- 計算式は ``ui/overlay.py`` の overview 配置ロジックと一致させる
- 単位は mm (Blender unit 変換は 0.001 を掛ける)
"""

from __future__ import annotations

import bpy

from . import gpencil as gp_utils
from . import log
from .geom import mm_to_m

_logger = log.get_logger(__name__)

DEFAULT_SPREAD_TOMBO_ALIGNED = True
DEFAULT_SPREAD_TOMBO_GAP_MM = -9.6


def page_spread_tombo_aligned(page) -> bool:
    value = getattr(page, "tombo_aligned", DEFAULT_SPREAD_TOMBO_ALIGNED)
    if value is None:
        return DEFAULT_SPREAD_TOMBO_ALIGNED
    return bool(value)


def page_spread_tombo_gap_mm(page) -> float:
    value = getattr(page, "tombo_gap_mm", DEFAULT_SPREAD_TOMBO_GAP_MM)
    if value is None or value == "":
        return DEFAULT_SPREAD_TOMBO_GAP_MM
    try:
        return float(value)
    except (TypeError, ValueError):
        return DEFAULT_SPREAD_TOMBO_GAP_MM


def _logical_slot_index(
    page_index: int,
    start_side: str = "right",
    read_direction: str = "left",
) -> int:
    """見開きスロット index (= 「1 ページ目の逆側の空白」分の補正込み).

    1 ページ目だけは ``start_side`` と ``read_direction`` の組み合わせに応じて、
    物理的な左/右位置が期待どおりになるスロットへ置く。反対側は空白扱いにし、
    2 ページ目以降は常に ``page_index + 1`` へ進める。
    """
    if read_direction == "down":
        return max(0, int(page_index))
    if page_index <= 0:
        first_page_is_slot0 = (
            (start_side == "right" and read_direction == "left")
            or (start_side == "left" and read_direction == "right")
        )
        return 0 if first_page_is_slot0 else 1
    return page_index + 1


def _work_spread_flags(work) -> list[bool]:
    return [bool(getattr(p, "spread", False)) for p in getattr(work, "pages", []) or []]


def spread_right_page_offset_mm_for_values(
    canvas_width_mm: float,
    tombo_aligned: bool,
    tombo_gap_mm: float,
    finish_width_mm: float | None = None,
) -> float:
    """見開き内で右半分ページが始まる X 位置を返す.

    「トンボを合わせる」がオンのときは CLIP STUDIO PAINT と同じく
    仕上がり枠を基準に合わせる。間隔は仕上がり枠間のギャップ。
    負値の間隔はノド側の重なり、正値はノド側の空きとして扱う。
    """
    width = max(0.0, float(canvas_width_mm))
    if not bool(tombo_aligned):
        return width
    try:
        gap = float(tombo_gap_mm)
    except (TypeError, ValueError):
        gap = 0.0
    fw = float(finish_width_mm) if finish_width_mm is not None else width
    return max(0.0, fw + gap)


def spread_right_page_offset_mm(
    page,
    canvas_width_mm: float,
    finish_width_mm: float | None = None,
) -> float:
    return spread_right_page_offset_mm_for_values(
        canvas_width_mm,
        page_spread_tombo_aligned(page),
        page_spread_tombo_gap_mm(page),
        finish_width_mm=finish_width_mm,
    )


def spread_content_width_mm(
    page,
    canvas_width_mm: float,
    finish_width_mm: float | None = None,
) -> float:
    width = max(0.0, float(canvas_width_mm))
    if not bool(getattr(page, "spread", False)):
        return width
    return max(width, spread_right_page_offset_mm(page, width, finish_width_mm) + width)


def slot_for_page_in_work(
    work,
    page_index: int,
    start_side: str = "right",
    read_direction: str = "left",
) -> int:
    """見開きを 2 スロット占有として数えた論理スロットを返す.

    - 見開きでないページだけの作品では `_logical_slot_index` と完全一致する
      (1 ページ目の逆側を空白にし、2 ページ目以降は 1 スロットずつ進む)。
    - 見開きページは 2 スロットを占有する。ペア (偶スロット+奇スロット) に
      跨がるよう、奇スロット開始になる場合は 1 スロット空けて偶スロットへ揃える。
    """
    if read_direction == "down":
        return max(0, int(page_index))
    flags = _work_spread_flags(work)
    if not (0 <= int(page_index) < len(flags)):
        return _logical_slot_index(page_index, start_side, read_direction)
    first_page_is_slot0 = (
        (start_side == "right" and read_direction == "left")
        or (start_side == "left" and read_direction == "right")
    )
    slot = 0
    cursor = 0
    for i in range(int(page_index) + 1):
        is_spread = flags[i]
        if i == 0:
            if is_spread:
                slot = 0
                cursor = 2
            else:
                slot = 0 if first_page_is_slot0 else 1
                cursor = 2
            continue
        if is_spread and cursor % 2 == 1:
            cursor += 1
        slot = cursor
        cursor += 2 if is_spread else 1
    return slot


def is_left_half_page(page_index: int, start_side: str = "right",
                      read_direction: str = "left", *, work=None) -> bool:
    """そのページが見開きペアの「物理左半分」かを返す.

    ``page_grid_offset_mm`` で計算される X 軸位置に基づき、ペア (col=偶, col=奇)
    の中で物理的に X が小さい側 (= 画面左) のページに True を返す。

    ペア内の物理左右は ``read_direction`` で決まる:
      - "right" (西洋本): col 増加 = 画面右へ進む → c=0 が物理左、c=1 が物理右
      - "left"  (日本マンガ): col 増加 = 画面左へ進む → c=0 が物理右、c=1 が物理左

    例: 日本マンガ (start_side="right", read_direction="left") の場合
      - page 1 (slot 0, c=0): 物理右 = 単独右ページ
      - page 2 (slot 2, c=0): 物理右 = 次の見開きの右ページ
      - page 3 (slot 3, c=1): 物理左 = 次の見開きの左ページ
    """
    if read_direction == "down":
        return False
    if work is not None:
        slot = slot_for_page_in_work(work, page_index, start_side, read_direction)
    else:
        slot = _logical_slot_index(page_index, start_side, read_direction)
    c_in_pair = slot % 2
    if read_direction == "right":
        return c_in_pair == 0
    return c_in_pair == 1


def page_grid_offset_mm(
    page_index: int,
    cols: int,
    gap_mm: float,
    canvas_width_mm: float,
    canvas_height_mm: float,
    start_side: str = "right",
    read_direction: str = "left",
    *,
    work=None,
    gap_y_mm: float | None = None,
) -> tuple[float, float]:
    """``page_index`` の grid offset (mm) を返す.

    見開きペアロジック:
      - 「論理スロット」を start_side で補正 (1 ページ目の単独ページの逆側に
        空白スロットを 1 つ置く)
      - 偶スロット (左半分) と次の奇スロット (右半分) で見開きペア
      - ペア内: 隙間 0 (密着)
      - ペア間: gap_mm 隙間あり

    read_direction:
      - "left":  X が負方向に進む (col が増えるほど左へ) — 日本マンガ既定
      - "right": X が正方向に進む — 西洋本
      - "down":  すべて X=0 で Y のみ進む (縦スクロール)。cols は無視。

    ``work`` を渡すと見開きページを 2 スロット占有として配置する
    (見開き自身はペア 2 枠ぶんの幅で表示し、後続ページはその分ずれる)。
    返す offset はページ内容のローカル原点 (= 内容の左端) に対応する。
    """
    if work is not None:
        slot = slot_for_page_in_work(work, page_index, start_side, read_direction)
        ox, oy = slot_grid_offset_mm(
            slot, cols, gap_mm, canvas_width_mm, canvas_height_mm, read_direction,
            gap_y_mm=gap_y_mm,
        )
        pages = getattr(work, "pages", []) or []
        is_spread = (
            bool(getattr(pages[page_index], "spread", False))
            if 0 <= int(page_index) < len(pages)
            else False
        )
        if is_spread and read_direction == "left":
            fw = float(getattr(getattr(work, "paper", None), "finish_width_mm", 0) or 0) or None
            R = spread_right_page_offset_mm(pages[page_index], canvas_width_mm, fw)
            ox -= (R + canvas_width_mm) / 2.0
        return ox, oy
    slot = _logical_slot_index(page_index, start_side, read_direction)
    return slot_grid_offset_mm(
        slot,
        cols,
        gap_mm,
        canvas_width_mm,
        canvas_height_mm,
        read_direction,
        gap_y_mm=gap_y_mm,
    )


def page_content_width_mm(
    work, page_index: int, canvas_width_mm: float,
    finish_width_mm: float | None = None,
) -> float:
    """ページ内容の横幅 (mm)。見開きは 2 ページ分."""
    pages = getattr(work, "pages", []) or []
    if finish_width_mm is None:
        paper = getattr(work, "paper", None)
        if paper is not None:
            fw = float(getattr(paper, "finish_width_mm", 0) or 0)
            if fw > 0:
                finish_width_mm = fw
    if 0 <= int(page_index) < len(pages):
        return spread_content_width_mm(pages[page_index], canvas_width_mm, finish_width_mm)
    return float(canvas_width_mm)


def slot_grid_offset_mm(
    slot: int,
    cols: int,
    gap_mm: float,
    canvas_width_mm: float,
    canvas_height_mm: float,
    read_direction: str = "left",
    *,
    gap_y_mm: float | None = None,
) -> tuple[float, float]:
    """見開き補正後の論理 slot から grid offset (mm) を返す."""
    cols = max(1, int(cols))
    gy = float(gap_y_mm) if gap_y_mm is not None else float(gap_mm)
    if read_direction == "down":
        return (0.0, -int(slot) * (canvas_height_mm + gy))

    col = slot % cols
    row = slot // cols
    x_total = 0.0
    for c in range(col):
        x_total += canvas_width_mm
        if c % 2 == 1:
            x_total += gap_mm
    sign = -1.0 if read_direction == "left" else 1.0
    ox = sign * x_total
    oy = -row * (canvas_height_mm + gy)
    return (ox, oy)


def page_manual_offset_for_scene_mm(scene, page_entry) -> tuple[float, float]:
    """シーンの役割を考慮した手動移動量 (mm)。

    「表示X/Y」は全ページ一覧での見た目上の移動のため、ページ編集シーンでは
    内容の配置に加えない (紙・コマ・フキダシ・効果線などが一覧上の移動量で
    ばらばらに動かないようにする)。
    """
    try:
        from . import page_file_scene

        if page_file_scene.is_page_edit_scene(scene):
            return 0.0, 0.0
    except Exception:  # noqa: BLE001
        pass
    return page_manual_offset_mm(page_entry)


def page_manual_offset_mm(page_entry) -> tuple[float, float]:
    """ページエントリに保存された手動移動量 (mm) を返す."""
    if page_entry is None:
        return 0.0, 0.0
    try:
        return (
            float(getattr(page_entry, "offset_x_mm", 0.0)),
            float(getattr(page_entry, "offset_y_mm", 0.0)),
        )
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def original_page_index(work, page_index: int) -> int:
    """軽量化されたページ一覧でも、元の作品内ページ番号を返す."""
    try:
        index = int(page_index)
    except (TypeError, ValueError):
        return -1
    resolver = getattr(work, "original_page_index", None)
    if callable(resolver):
        try:
            resolved = int(resolver(index))
            if resolved >= 0:
                return resolved
        except Exception:  # noqa: BLE001
            pass
    return index


def page_total_offset_mm(
    work,
    scene,
    page_index: int,
) -> tuple[float, float]:
    """grid 配置とページ手動移動量を合成した offset (mm) を返す."""
    if work is None or scene is None or not (0 <= page_index < len(work.pages)):
        return 0.0, 0.0
    cols, gap_x, gap_y, cw, ch = _resolve_overview_params(scene, work)
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    grid_page_index = original_page_index(work, page_index)
    ox_mm, oy_mm = page_grid_offset_mm(
        grid_page_index, cols, gap_x, cw, ch, start_side, read_direction,
        work=work, gap_y_mm=gap_y,
    )
    add_x, add_y = page_manual_offset_for_scene_mm(scene, work.pages[page_index])
    return ox_mm + add_x, oy_mm + add_y


def resolve_gap_mm(scene) -> tuple[float, float]:
    """シーンからページ間隔 (横, 縦) mm を取得する."""
    fallback = float(getattr(scene, "bmanga_overview_gap_mm", 30.0))
    gap_x = float(getattr(scene, "bmanga_overview_gap_x_mm", fallback))
    gap_y = float(getattr(scene, "bmanga_overview_gap_y_mm", fallback))
    return gap_x, gap_y


def _resolve_overview_params(scene, work) -> tuple[int, float, float, float, float]:
    cols = max(1, int(getattr(scene, "bmanga_overview_cols", 4)))
    gap_x, gap_y = resolve_gap_mm(scene)
    cw = float(work.paper.canvas_width_mm)
    ch = float(work.paper.canvas_height_mm)
    return cols, gap_x, gap_y, cw, ch


# 下書き (マスター GP) のストローク座標はキャンバス絶対値のため、ページの
# 並べ替え・列数変更などで grid 配置が変わると、その時点で閉じている
# ページ用 blend のストロークには平行移動が届かない。ページ/コマ用 blend の
# 保存時に「自ページの grid オフセット」を scene へ記録し、次の読込時に
# 現在のオフセットとの差分だけストロークを動かして追従させる。
PROP_GP_SAVED_PAGE_OFFSET = "bmanga_gp_saved_page_offset"


def _own_detail_page_id(context) -> str:
    """ページ/コマ用 blend が属するページ ID (作品一覧シーンでは空)."""
    try:
        from . import page_file_scene

        role, page_id, _coma_id = page_file_scene.current_role(context)
        if role in {page_file_scene.ROLE_PAGE, page_file_scene.ROLE_COMA}:
            return page_id
        if role != page_file_scene.ROLE_UNKNOWN:
            return ""
        # 新規作成中 (filepath 未確定) はシーン上の編集状態から判定する
        scene = getattr(context, "scene", None)
        page_id = page_file_scene.current_page_id(scene)
        if page_id:
            return page_id
        return str(getattr(scene, "bmanga_current_coma_page_id", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def record_gp_page_offset(context, work) -> None:
    """自ページの grid オフセットを scene に記録する (ページ/コマ用 blend のみ)."""
    scene = getattr(context, "scene", None) or bpy.context.scene
    if scene is None or work is None:
        return
    page_id = _own_detail_page_id(context)
    if not page_id:
        return
    try:
        from . import page_file_scene

        index = page_file_scene.find_page_index(work, page_id)
    except Exception:  # noqa: BLE001
        return
    if not (0 <= index < len(getattr(work, "pages", []) or [])):
        return
    ox, oy = page_total_offset_mm(work, scene, index)
    scene[PROP_GP_SAVED_PAGE_OFFSET] = {"page_id": page_id, "ox": float(ox), "oy": float(oy)}


def reconcile_gp_strokes_with_page_offset(context, work) -> None:
    """保存時と現在で自ページの配置が変わっていたら、下書きを差分だけ移動する."""
    scene = getattr(context, "scene", None) or bpy.context.scene
    if scene is None or work is None:
        return
    page_id = _own_detail_page_id(context)
    if not page_id:
        return
    try:
        from . import page_file_scene

        index = page_file_scene.find_page_index(work, page_id)
    except Exception:  # noqa: BLE001
        return
    if not (0 <= index < len(getattr(work, "pages", []) or [])):
        return
    stored = scene.get(PROP_GP_SAVED_PAGE_OFFSET)
    stored_id = ""
    stored_ox = stored_oy = 0.0
    try:
        if stored is not None:
            stored_id = str(stored.get("page_id", "") or "")
            stored_ox = float(stored.get("ox", 0.0))
            stored_oy = float(stored.get("oy", 0.0))
    except Exception:  # noqa: BLE001
        stored_id = ""
    if stored_id == page_id:
        ox, oy = page_total_offset_mm(work, scene, index)
        dx = ox - stored_ox
        dy = oy - stored_oy
        if abs(dx) > 1.0e-6 or abs(dy) > 1.0e-6:
            try:
                from . import layer_stack

                layer_stack.translate_gp_layers_for_parent_keys(
                    context,
                    layer_stack.gp_parent_keys_for_page(work.pages[index]),
                    dx,
                    dy,
                )
            except Exception:  # noqa: BLE001
                _logger.exception("gp stroke reconcile failed: %s", page_id)
    # 旧ファイル (記録なし) や別ページの記録は、現在値の記録だけ行う
    record_gp_page_offset(context, work)


SUBPAGE_OFFSET_X_PROP = "bmanga_subpage_offset_x_mm"
SUBPAGE_OFFSET_Y_PROP = "bmanga_subpage_offset_y_mm"


def _obj_subpage_offset_mm(obj) -> tuple[float, float]:
    """``obj`` の subpage offset (mm) を custom property から取得.

    見開きページでは、左半分と右半分の 2 GP を同じ Collection に置くために
    右 GP に (canvas_width_mm, 0) の subpage offset を乗せる。
    custom property が無ければ (0.0, 0.0)。
    """
    try:
        ox = float(obj.get(SUBPAGE_OFFSET_X_PROP, 0.0))
        oy = float(obj.get(SUBPAGE_OFFSET_Y_PROP, 0.0))
        return ox, oy
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


# Grease Pencil オブジェクトを用紙より手前 (z>0) に配置するためのオフセット (m).
# GP Object のベース Z リフト。 現仕様では assign_per_page_z_ranks が
# page 内 rank ベース (0.1 刻み) で Object.location.z を確定するため、
# ここでは追加リフト不要 (= 0)。 paper_bg z=0 に対しては rank*0.1 で
# 自然に上に乗る。
GP_Z_LIFT_M = 0.0


def apply_page_collection_transforms(context, work) -> int:
    """全ページ Collection の location を grid offset で再計算.

    戻り値: 更新した Collection 数。Collection が未生成のページはスキップ。
    overview モード設定に関わらず常に grid 配置で並べる (scene 内の
    物理座標は overview モードに依存しない)。

    per-object の subpage offset (見開きの右半分用) があれば grid offset に
    加算する。これにより見開きページで 2 GP (左/右) を正しい位置に並置できる。

    バグ #3 対策: ページ Collection の **直下** だけでなく、コマ
    サブコレクション (``c01`` / ``c02`` …) や折りたたみフォルダ配下まで
    再帰的に走査する。 ``start_side`` / ``read_direction`` 変更時に raster
    / GP / effect レイヤーが旧位置 (例: 単独ページのつもりで slot 0 = x=0)
    に取り残されて「余分な空白 paper_bg」のように見える問題を解消する。

    Object 種別ごとに位置決めを切り替える:
      - ``raster`` / ``gp`` / ``effect``: 全キャンバス座標を持つので world
        位置 = ページ grid オフセット
      - ``balloon`` / ``image`` / ``text``: entry の ``x_mm`` / ``y_mm`` を
        ページローカル座標として保持しているため、world = ページ offset +
        entry オフセット
      - その他 (``master_sketch`` 等): 旧仕様どおり page Collection 直下に
        いる場合のみ page offset で揃える。サブコレクション配下に紛れた
        managed 外 Object (例: 基本枠コマ Mesh) は触らない

    Z 軸は ``assign_per_page_z_ranks`` が per-page rank で決めるため、
    ここでは現在値を保持する (= 上書きしない)。
    """
    try:
        from . import layer_object_sync as _los
    except Exception:  # noqa: BLE001
        _los = None

    if _los is not None and not _los.is_sync_in_progress():
        with _los.suppress_sync():
            return _apply_page_collection_transforms_impl(context, work)
    return _apply_page_collection_transforms_impl(context, work)


def _apply_page_collection_transforms_impl(context, work) -> int:
    scene = context.scene if context else bpy.context.scene
    if scene is None or work is None:
        return 0
    cols, gap_x, gap_y, cw, ch = _resolve_overview_params(scene, work)
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    page_ids = {str(getattr(page, "id", "") or "") for page in getattr(work, "pages", []) or []}
    try:
        from . import page_file_scene

        is_work_list_scene = page_file_scene.is_work_list_scene(scene)
        current_page_id = page_file_scene.current_page_id(scene)
        is_page_edit_scene = bool(current_page_id and page_file_scene.is_page_edit_scene(scene))
        real_work = page_file_scene.work_for_pages(work, {current_page_id}) if is_page_edit_scene else work
    except Exception:  # noqa: BLE001
        is_work_list_scene = False
        current_page_id = ""
        is_page_edit_scene = False
        real_work = work

    # entry-positioned kinds 用に scene 全体の image_layers を 1 度だけ index 化
    image_entries: dict[str, object] = {}
    for entry in getattr(scene, "bmanga_image_layers", []) or []:
        eid = str(getattr(entry, "id", "") or "")
        if eid:
            image_entries[eid] = entry

    full_canvas_kinds = {"raster", "gp", "effect"}
    entry_relative_kinds = {"balloon", "image", "text"}
    text_objects_by_id: dict[str, object] = {}
    for obj in bpy.data.objects:
        try:
            if str(obj.get("bmanga_kind", "") or "") != "text":
                continue
            if not bool(obj.get("bmanga_managed", False)):
                continue
            bid = str(obj.get("bmanga_id", "") or "")
            if bid and bid not in text_objects_by_id:
                text_objects_by_id[bid] = obj
        except Exception:  # noqa: BLE001
            continue

    def _owner_page_id(parent_key: str) -> str:
        key = str(parent_key or "")
        if not key:
            return ""
        if ":" in key:
            return key.split(":", 1)[0]
        if key in page_ids:
            return key
        try:
            from . import layer_folder
            from .layer_hierarchy import OUTSIDE_STACK_KEY

            semantic = layer_folder.semantic_parent_key_for_folder(work, key)
            if semantic and semantic != OUTSIDE_STACK_KEY:
                return semantic.split(":", 1)[0]
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _set_xy(obj, x_m: float, y_m: float) -> None:
        try:
            loc = obj.location
            if (
                abs(float(loc.x) - float(x_m)) <= 1.0e-9
                and abs(float(loc.y) - float(y_m)) <= 1.0e-9
            ):
                return
            obj.location = (x_m, y_m, loc.z)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "apply_page_collection_transforms: location set failed on %s",
                obj.name,
            )

    def _set_page_text_objects(page_entry, page_id: str, ox_mm: float, oy_mm: float) -> None:
        for entry in getattr(page_entry, "texts", []) or []:
            text_id = str(getattr(entry, "id", "") or "")
            if not text_id:
                continue
            obj = text_objects_by_id.get(f"{page_id}:{text_id}") or text_objects_by_id.get(text_id)
            if obj is None:
                continue
            _set_xy(
                obj,
                mm_to_m(ox_mm + float(getattr(entry, "x_mm", 0.0) or 0.0)),
                mm_to_m(oy_mm + float(getattr(entry, "y_mm", 0.0) or 0.0)),
            )

    updated = 0
    for i, page_entry in enumerate(work.pages):
        coll = gp_utils.get_page_collection(page_entry.id)
        if coll is None:
            continue
        page_id_str = str(getattr(page_entry, "id", "") or "")
        ox_mm, oy_mm = page_grid_offset_mm(
            i, cols, gap_x, cw, ch, start_side, read_direction,
            work=work, gap_y_mm=gap_y,
        )
        # ページ編集シーンでは「一覧上の手動移動量」を内容に加えない
        # (一覧専用の見た目オフセット。紙やコマと内容がずれる原因になる)
        if not is_page_edit_scene:
            add_x, add_y = page_manual_offset_mm(page_entry)
            ox_mm += add_x
            oy_mm += add_y

        _set_page_text_objects(page_entry, page_id_str, ox_mm, oy_mm)

        # ページ内の entry ルックアップを 1 度だけ作る
        balloon_entries: dict[str, object] = {}
        text_entries: dict[str, object] = {}
        for entry in getattr(page_entry, "balloons", []) or []:
            eid = str(getattr(entry, "id", "") or "")
            if eid:
                balloon_entries[eid] = entry
        for entry in getattr(page_entry, "texts", []) or []:
            eid = str(getattr(entry, "id", "") or "")
            if eid:
                text_entries[eid] = entry
                text_entries[f"{page_id_str}:{eid}"] = entry

        direct_child_set = {id(o) for o in coll.objects}
        # サブコレクション (c01, c02, ... ) も含めた全 Object を走査。
        # Object が他ページの Collection にも link されているケースがあるため、
        # ``bmanga_parent_key`` で「自分はこのページに属している」と明示している
        # Object のみ位置更新する (二重処理防止)。
        for obj in coll.all_objects:
            sub_x, sub_y = _obj_subpage_offset_mm(obj)
            kind = str(obj.get("bmanga_kind", "") or "")
            managed = bool(obj.get("bmanga_managed", False))
            parent_key = str(obj.get("bmanga_parent_key", "") or "")
            owner_page_id = _owner_page_id(parent_key)
            # parent_key がページ ID を持つのに現在処理中のページと違うなら、
            # 別ページの管轄なのでここでは触らない (そのページの iteration で更新される)
            if owner_page_id and owner_page_id != page_id_str:
                continue

            if kind in full_canvas_kinds and managed:
                _set_xy(
                    obj,
                    mm_to_m(ox_mm + sub_x),
                    mm_to_m(oy_mm + sub_y),
                )
                continue

            if kind in entry_relative_kinds and managed:
                bmanga_id = str(obj.get("bmanga_id", "") or "")
                entry = None
                if kind == "balloon":
                    entry = balloon_entries.get(bmanga_id)
                elif kind == "text":
                    entry = text_entries.get(bmanga_id)
                elif kind == "image":
                    entry = image_entries.get(bmanga_id)
                if entry is None:
                    continue
                ex_mm = float(getattr(entry, "x_mm", 0.0) or 0.0)
                ey_mm = float(getattr(entry, "y_mm", 0.0) or 0.0)
                if kind in {"balloon", "image"}:
                    ex_mm += float(getattr(entry, "width_mm", 0.0) or 0.0) * 0.5
                    ey_mm += float(getattr(entry, "height_mm", 0.0) or 0.0) * 0.5
                _set_xy(
                    obj,
                    mm_to_m(ox_mm + ex_mm + sub_x),
                    mm_to_m(oy_mm + ey_mm + sub_y),
                )
                continue

            # 旧仕様維持: bmanga_kind が無い page Collection 直下の Object
            # (master_sketch 等) は page offset に揃える。コマサブ配下の
            # 未識別 Object (= 基本枠コマ Mesh など) には触らない。
            if id(obj) in direct_child_set:
                _set_xy(
                    obj,
                    mm_to_m(ox_mm + sub_x),
                    mm_to_m(oy_mm + sub_y),
                )
        updated += 1

    # コマ平面 Mesh (utils/coma_plane.py) は ``__masks__`` 撤廃後の新方式で、
    # コマ Collection 直下 + ``bmanga_managed=False`` で識別フラグも持たないため
    # 上記の bmanga_kind 判定では拾われない。 page offset 変更時に追従させるため、
    # 末尾で明示的に locations を再計算する。
    try:
        from . import coma_plane as _cp

        if not is_work_list_scene:
            _cp.update_coma_plane_locations(scene, work)
    except Exception:  # noqa: BLE001
        _logger.exception("apply_page_collection_transforms: coma_plane location update failed")
    try:
        from . import coma_border_object as _cbo

        if not is_work_list_scene:
            _cbo.update_coma_border_locations(scene, work)
    except Exception:  # noqa: BLE001
        _logger.exception("apply_page_collection_transforms: coma_border location update failed")
    try:
        from . import overview_camera as _overview_camera

        _overview_camera.ensure_overview_camera(scene, work)
    except Exception:  # noqa: BLE001
        _logger.exception("apply_page_collection_transforms: overview camera update failed")
    try:
        from . import page_content_visibility as _page_content_visibility

        _page_content_visibility.apply_page_content_visibility(context, work)
    except Exception:  # noqa: BLE001
        _logger.exception("apply_page_collection_transforms: page content visibility update failed")
    try:
        from . import fill_real_object as _fro

        if not is_work_list_scene:
            _fro.sync_all_fill_real_objects(scene, real_work)
    except Exception:  # noqa: BLE001
        _logger.exception("apply_page_collection_transforms: fill object location update failed")
    if is_work_list_scene:
        try:
            from . import page_preview_object

            page_preview_object.sync_page_previews(context, work)
        except Exception:  # noqa: BLE001
            _logger.exception("apply_page_collection_transforms: page preview sync failed")
    return updated


def page_index_at_world_mm(
    work, scene, x_mm: float, y_mm: float
) -> int | None:
    """world 座標 (mm) からページ index を逆引き (キャンバス矩形内のみ).

    overview 的 grid 配置を前提に、各ページのキャンバス矩形 [ox, ox+cw] x
    [oy, oy+ch] 内に座標が入っているかを確認する。入っていない場合は None。
    境界近傍のデッドゾーン処理は呼び出し側で行う。
    """
    if work is None or scene is None:
        return None
    _, _, _, cw, ch = _resolve_overview_params(scene, work)
    from . import page_range

    for i, page in enumerate(work.pages):
        if not page_range.page_in_range(page):
            continue
        ox_mm, oy_mm = page_total_offset_mm(work, scene, i)
        pw = page_content_width_mm(work, i, cw)
        if ox_mm <= x_mm <= ox_mm + pw and oy_mm <= y_mm <= oy_mm + ch:
            return i
    return None
