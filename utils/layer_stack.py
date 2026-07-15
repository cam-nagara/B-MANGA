"""統合レイヤースタックの同期・選択・並び替えヘルパ."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from array import array

import bpy

from ..core.work import get_active_page, get_work
from . import gp_layer_parenting as gp_parent
from . import edge_selection
from . import gpencil as gp_utils
from . import layer_folder as layer_folder_utils
from . import log
from . import object_selection
from . import object_naming as on
from .layer_hierarchy import (
    PAGE_KIND,
    COMA_KIND,
    OUTSIDE_KIND,
    OUTSIDE_STACK_KEY,
    entry_center,
    page_stack_key,
    coma_containing_point,
    coma_stack_key,
    outside_child_key,
    split_child_key,
)

_logger = log.get_logger(__name__)

PAGE_COMA_CHILD_KINDS = {"gp", "effect", "raster", "image", "image_path", "fill", "balloon", "text"}
COMA_PREVIEW_KIND = "coma_preview"
COMA_REORDER_KINDS = PAGE_COMA_CHILD_KINDS | {COMA_PREVIEW_KIND}
LAYER_FOLDER_KIND = layer_folder_utils.LAYER_FOLDER_KIND
_sync_scheduled = False
_sync_should_apply_order = False
_sync_order_moved_uid = ""
_draw_stack_signatures: dict[int, tuple[str, ...]] = {}

@dataclass(frozen=True)
class LayerTarget:
    kind: str
    key: str
    label: str
    parent_key: str = ""
    depth: int = 0
    # 2026-07-12: フキダシ⇔子テキストの紐付けペア用。相手 target の uid
    # (kind:key 形式) を保持する。紐付けの無い target では空文字のまま。
    # スタック上の実アイテムには保存しない (収集のたびに再計算する一時情報)。
    pair_key: str = ""

    @property
    def uid(self) -> str:
        return target_uid(self.kind, self.key)


def _target_has_stack_row(target: LayerTarget) -> bool:
    kind = str(target.kind or "").strip()
    if not kind:
        return False
    if kind in {OUTSIDE_KIND, PAGE_KIND, COMA_KIND}:
        return True
    return bool(str(target.label or "").strip())


def _stack_has_placeholder_rows(stack) -> bool:
    """保存済みの古いレイヤー一覧に残った空行を検出する。"""
    seen: set[str] = set()
    for item in stack or []:
        kind = str(getattr(item, "kind", "") or "").strip()
        key = str(getattr(item, "key", "") or "").strip()
        label = str(
            getattr(item, "label", "")
            or getattr(item, "name", "")
            or ""
        ).strip()
        if not kind or not key or not label:
            return True
        uid = stack_item_uid(item)
        if uid in seen:
            return True
        seen.add(uid)
    return False


def target_uid(kind: str, key: str) -> str:
    if str(kind or "") in {"gp", "effect", LAYER_FOLDER_KIND}:
        from . import layer_uid

        return layer_uid.make_managed_uid(str(kind), str(key))
    return f"{kind}:{key}"


def coma_preview_key(coma_key: str) -> str:
    return f"{coma_key}:__preview__"


def stack_item_uid(item) -> str:
    return target_uid(getattr(item, "kind", ""), getattr(item, "key", ""))


def find_stack_index_for_item(stack, item) -> int:
    from . import layer_stack_visible

    return layer_stack_visible.find_stack_index_for_item(stack, item)


def visible_layer_stack_entries(context, stack=None) -> list[tuple[int, object]]:
    from . import layer_stack_visible

    return layer_stack_visible.visible_layer_stack_entries(context, stack)


def set_active_visible_stack_index_silently(context, index: int) -> None:
    from . import layer_stack_visible

    layer_stack_visible.set_active_visible_stack_index_silently(context, index)


def sync_visible_layer_stack(context, *, stack=None) -> bool:
    from . import layer_stack_visible

    return layer_stack_visible.sync_visible_layer_stack(context, stack=stack)


def set_active_stack_index_silently(context, index: int) -> None:
    """実データ選択を再実行せず、UIList のアクティブ行だけを合わせる。"""
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "bmanga_active_layer_stack_index"):
        return
    # 同値なら代入しない。無条件代入は Scene を更新済み扱いにして depsgraph_update を
    # 発火させ、描画コールバック経由の sync_layer_stack 呼び出しで無限再描画ループに
    # なる (細線のちらつきの原因)。
    if int(getattr(scene, "bmanga_active_layer_stack_index", -1)) == int(index):
        return
    core_layer_stack = None
    try:
        from ..core import layer_stack as core_layer_stack

        core_layer_stack._active_index_update_depth += 1
    except Exception:  # noqa: BLE001
        core_layer_stack = None
    try:
        scene.bmanga_active_layer_stack_index = int(index)
    finally:
        if core_layer_stack is not None:
            core_layer_stack._active_index_update_depth = max(
                0,
                core_layer_stack._active_index_update_depth - 1,
            )


def _iter_effect_objects() -> list[bpy.types.Object]:
    """新設計の効果線 Object を返す。旧集約 Object は含めない。"""
    objects: list[bpy.types.Object] = []
    for obj in bpy.data.objects:
        if getattr(obj, "type", "") != "GREASEPENCIL":
            continue
        if str(obj.get(on.PROP_KIND, "") or "") != "effect":
            continue
        objects.append(obj)
    objects.sort(key=lambda obj: int(obj.get(on.PROP_Z_INDEX, 0) or 0), reverse=True)
    return objects


def _effect_parent_key(obj, layer) -> str:
    parent_key = gp_parent.parent_key(layer)
    if parent_key:
        return parent_key
    return str(obj.get(on.PROP_PARENT_KEY, "") or "")


def _ensure_unique_id(entry, used: set[str], prefix: str) -> str:
    key = str(getattr(entry, "id", "") or "").strip()
    if key and key not in used:
        used.add(key)
        return key
    i = 1
    while True:
        candidate = f"{prefix}_{i:04d}"
        if candidate not in used:
            try:
                entry.id = candidate
            except Exception:  # noqa: BLE001
                pass
            used.add(candidate)
            return candidate
        i += 1


def _coma_parent_key_matches(entry, page, coma_key: str, panel) -> bool:
    parent = str(getattr(entry, "parent_key", "") or "")
    if parent == coma_key:
        return True
    if parent == getattr(panel, "id", ""):
        return True
    if parent == getattr(panel, "coma_id", ""):
        return True
    return parent == f"{getattr(page, 'id', '')}:{getattr(panel, 'coma_id', '')}"


def _explicit_entry_parent(entry, page, panels_by_key: dict[str, object]) -> tuple[str, int] | None:
    """エントリ (balloon/text) の永続化された親キーを解決する.

    coma 親は ``panels_by_key`` ではなく ``page.comas`` 全件で解決する。これにより、
    per-panel 呼び出し (``panels_by_key`` が部分集合) でも、別コマがオーソリティ
    親であるエントリを「該当無し」と誤判定して空間フォールバックで重複生成する
    バグを避ける。呼び出し側で ``parent not in panels_by_key`` のときに skip する
    こと。
    """
    _ = panels_by_key  # 互換のため引数は残すが、解決は page.comas で行う
    parent = str(getattr(entry, "parent_key", "") or "")
    if not parent:
        return None
    parent_kind = str(getattr(entry, "parent_kind", "") or "")
    page_key = page_stack_key(page)
    if parent_kind == "page" or (not parent_kind and ":" not in parent):
        if parent in {getattr(page, "id", ""), page_key}:
            return page_key, 1
        return None
    if parent_kind == "coma" or ":" in parent:
        for panel in getattr(page, "comas", []):
            coma_key = coma_stack_key(page, panel)
            if _coma_parent_key_matches(entry, page, coma_key, panel):
                return coma_key, 2
    return None


_PAGE_NUM_RE = re.compile(r"p0*(\d+)$")
_COMA_NUM_RE = re.compile(r"(?:coma[_-]?)0*(\d+)$")


def _page_label(page_key: str, title: str) -> str:
    if title:
        return title
    m = _PAGE_NUM_RE.match(str(page_key or ""))
    if m:
        return f"ページ{int(m.group(1)):03d}"
    return str(page_key or "")


def _coma_display_label(panel, fallback: str) -> str:
    raw_title = str(getattr(panel, "title", "") or "")
    label = raw_title.replace("基本枠", "").strip(" -_　")
    if label:
        return label
    coma_id = str(getattr(panel, "coma_id", "") or fallback or "")
    m = _COMA_NUM_RE.match(coma_id)
    if m:
        return f"コマ{int(m.group(1)):02d}"
    return coma_id or str(fallback or "")


_LAYER_KIND_JP = {
    "balloon": "フキダシ",
    "text": "テキスト",
    "image": "画像",
    "image_path": "パターンカーブ",
    "raster": "ラスター",
    "fill": "塗り",
    "effect": "効果線",
    "gp": "下書き",
}

_LAYER_ID_NUMBER_RE = re.compile(
    r"^(?:shared_)?(?:balloon|text|image|image_path|raster|fill|effect|gp)[_-]?0*(\d+)$"
)


def _jp_layer_label(kind: str, raw_id: str) -> str:
    """既定 ID (balloon_0001 等) のままのレイヤー名を日本語表記で表示する.

    ユーザーが付けた表示名はそのまま使い、ここへは表示名が空のときの
    フォールバック (= 内部 ID) だけが来る。
    """
    base = _LAYER_KIND_JP.get(str(kind or ""), "")
    raw = str(raw_id or "")
    if not base:
        return raw
    if not raw:
        return base
    match = _LAYER_ID_NUMBER_RE.match(raw)
    if match:
        return f"{base} {int(match.group(1))}"
    lowered = raw.lower()
    if lowered in {kind, f"shared_{kind}"}:
        return base
    return raw


def _collect_raster_targets_for_page(page, panels_by_key: dict[str, object]):
    scene = getattr(bpy.context, "scene", None)
    coll = getattr(scene, "bmanga_raster_layers", None) if scene is not None else None
    if coll is None:
        return [], {}
    page_key = page_stack_key(page)
    page_children: list[LayerTarget] = []
    panel_children: dict[str, list[LayerTarget]] = {}
    for entry in reversed(list(coll)):
        if str(getattr(entry, "scope", "") or "page") != "page":
            continue
        parent_kind = str(getattr(entry, "parent_kind", "") or "page")
        parent_key = str(getattr(entry, "parent_key", "") or "")
        label = getattr(entry, "title", "") or _jp_layer_label("raster", getattr(entry, "id", ""))
        if parent_kind == "page" and parent_key in {getattr(page, "id", ""), page_key}:
            page_children.append(LayerTarget("raster", entry.id, label, page_key, 1))
            continue
        if parent_kind == "coma":
            for coma_key, panel in panels_by_key.items():
                if _coma_parent_key_matches(entry, page, coma_key, panel):
                    panel_children.setdefault(coma_key, []).append(
                        LayerTarget("raster", entry.id, label, coma_key, 2)
                    )
                    break
    return page_children, panel_children


def _collect_image_targets_for_page(page, panels_by_key: dict[str, object]):
    scene = getattr(bpy.context, "scene", None)
    coll = getattr(scene, "bmanga_image_layers", None) if scene is not None else None
    if coll is None:
        return [], {}
    page_key = page_stack_key(page)
    page_children: list[LayerTarget] = []
    panel_children: dict[str, list[LayerTarget]] = {}
    for entry in reversed(list(coll)):
        parent_kind = str(getattr(entry, "parent_kind", "") or "none")
        parent_key = str(getattr(entry, "parent_key", "") or "")
        label = getattr(entry, "title", "") or _jp_layer_label("image", getattr(entry, "id", ""))
        if parent_kind == "page" and parent_key in {getattr(page, "id", ""), page_key}:
            page_children.append(LayerTarget("image", entry.id, label, page_key, 1))
            continue
        if parent_kind == "coma":
            for coma_key, panel in panels_by_key.items():
                if _coma_parent_key_matches(entry, page, coma_key, panel):
                    panel_children.setdefault(coma_key, []).append(
                        LayerTarget("image", entry.id, label, coma_key, 2)
                    )
                    break
    return page_children, panel_children


def _collect_fill_targets_for_page(page, panels_by_key: dict[str, object]):
    scene = getattr(bpy.context, "scene", None)
    coll = getattr(scene, "bmanga_fill_layers", None) if scene is not None else None
    if coll is None:
        return [], {}
    page_key = page_stack_key(page)
    page_children: list[LayerTarget] = []
    panel_children: dict[str, list[LayerTarget]] = {}
    for entry in reversed(list(coll)):
        parent_kind = str(getattr(entry, "parent_kind", "") or "page")
        parent_key = str(getattr(entry, "parent_key", "") or "")
        label = getattr(entry, "title", "") or _jp_layer_label("fill", getattr(entry, "id", ""))
        if parent_kind == "page" and parent_key in {getattr(page, "id", ""), page_key}:
            page_children.append(LayerTarget("fill", entry.id, label, page_key, 1))
            continue
        if parent_kind == "coma":
            for coma_key, panel in panels_by_key.items():
                if _coma_parent_key_matches(entry, page, coma_key, panel):
                    panel_children.setdefault(coma_key, []).append(
                        LayerTarget("fill", entry.id, label, coma_key, 2)
                    )
                    break
    return page_children, panel_children


def _collect_image_path_targets_for_page(page, panels_by_key: dict[str, object]):
    scene = getattr(bpy.context, "scene", None)
    coll = getattr(scene, "bmanga_image_path_layers", None) if scene is not None else None
    if coll is None:
        return [], {}
    page_key = page_stack_key(page)
    page_children: list[LayerTarget] = []
    panel_children: dict[str, list[LayerTarget]] = {}
    for entry in reversed(list(coll)):
        parent_kind = str(getattr(entry, "parent_kind", "") or "page")
        parent_key = str(getattr(entry, "parent_key", "") or "")
        label = getattr(entry, "title", "") or _jp_layer_label("image_path", getattr(entry, "id", ""))
        if parent_kind == "page" and parent_key in {getattr(page, "id", ""), page_key}:
            page_children.append(LayerTarget("image_path", entry.id, label, page_key, 1))
            continue
        if parent_kind == "coma":
            for coma_key, panel in panels_by_key.items():
                if _coma_parent_key_matches(entry, page, coma_key, panel):
                    panel_children.setdefault(coma_key, []).append(
                        LayerTarget("image_path", entry.id, label, coma_key, 2)
                    )
                    break
    return page_children, panel_children


def _retarget_root_subtree_to_outside(targets: list[LayerTarget]) -> list[LayerTarget]:
    """ページに属さない個別レイヤーを「ページ外」配下へ載せ替える."""
    return [
        LayerTarget(
            target.kind,
            target.key,
            target.label,
            OUTSIDE_STACK_KEY,
            1,
        )
        for target in targets
    ]




def _collect_outside_layer_targets(
    work,
    scene,
    gp_root_targets: list[LayerTarget],
    effect_root_targets: list[LayerTarget],
) -> list[LayerTarget]:
    targets = [LayerTarget(OUTSIDE_KIND, OUTSIDE_STACK_KEY, "(ページ外)")]
    if work is None:
        return targets

    for panel in sorted(
        list(getattr(work, "shared_comas", [])),
        key=lambda entry: int(getattr(entry, "z_order", 0)),
        reverse=True,
    ):
        stem = str(getattr(panel, "coma_id", "") or getattr(panel, "id", "") or "")
        if not stem:
            continue
        label = _coma_display_label(panel, stem)
        targets.append(LayerTarget(COMA_KIND, outside_child_key(stem), label, OUTSIDE_STACK_KEY, 1))

    raster_layers = getattr(scene, "bmanga_raster_layers", None) if scene is not None else None
    if raster_layers is not None:
        used_raster: set[str] = set()
        for entry in reversed(list(raster_layers)):
            scope = str(getattr(entry, "scope", "") or "")
            parent_kind = str(getattr(entry, "parent_kind", "") or "")
            parent_key = str(getattr(entry, "parent_key", "") or "")
            if scope != "master" and parent_kind != "none" and parent_key:
                continue
            key = _ensure_unique_id(entry, used_raster, "raster")
            label = getattr(entry, "title", "") or _jp_layer_label("raster", key)
            targets.append(LayerTarget("raster", key, label, OUTSIDE_STACK_KEY, 1))

    image_layers = getattr(scene, "bmanga_image_layers", None) if scene is not None else None
    if image_layers is not None:
        used_image: set[str] = set()
        for entry in reversed(list(image_layers)):
            parent_kind = str(getattr(entry, "parent_kind", "") or "none")
            parent_key = str(getattr(entry, "parent_key", "") or "")
            if parent_kind != "none" and parent_key:
                continue
            key = _ensure_unique_id(entry, used_image, "image")
            label = getattr(entry, "title", "") or _jp_layer_label("image", key)
            targets.append(LayerTarget("image", key, label, OUTSIDE_STACK_KEY, 1))

    image_path_layers = getattr(scene, "bmanga_image_path_layers", None) if scene is not None else None
    if image_path_layers is not None:
        used_image_path: set[str] = set()
        for entry in reversed(list(image_path_layers)):
            parent_kind = str(getattr(entry, "parent_kind", "") or "none")
            parent_key = str(getattr(entry, "parent_key", "") or "")
            if parent_kind != "none" and parent_key:
                continue
            key = _ensure_unique_id(entry, used_image_path, "image_path")
            label = getattr(entry, "title", "") or _jp_layer_label("image_path", key)
            targets.append(LayerTarget("image_path", key, label, OUTSIDE_STACK_KEY, 1))

    used_balloon: set[str] = set()
    for entry in reversed(list(getattr(work, "shared_balloons", []))):
        bid = _ensure_unique_id(entry, used_balloon, "shared_balloon")
        label = getattr(entry, "title", "") or _jp_layer_label("balloon", getattr(entry, "id", "") or bid)
        targets.append(LayerTarget("balloon", outside_child_key(bid), label, OUTSIDE_STACK_KEY, 1))

    used_text: set[str] = set()
    for entry in reversed(list(getattr(work, "shared_texts", []))):
        tid = _ensure_unique_id(entry, used_text, "shared_text")
        label = getattr(entry, "title", "") or getattr(entry, "body", "") or _jp_layer_label("text", tid)
        targets.append(LayerTarget("text", outside_child_key(tid), label, OUTSIDE_STACK_KEY, 1))

    targets.extend(_retarget_root_subtree_to_outside(effect_root_targets))
    targets.extend(_retarget_root_subtree_to_outside(gp_root_targets))
    return targets


def _collect_page_layer_targets(
    page,
    panels_by_key: dict[str, object],
    *,
    include_page_children: bool = True,
) -> list[LayerTarget]:
    targets: list[LayerTarget] = []
    used_text: set[str] = set()
    used_balloon: set[str] = set()
    page_key = page_stack_key(page)
    panel_children: dict[str, list[LayerTarget]] = {}
    page_children: list[LayerTarget] = []
    balloon_groups: dict[str, LayerTarget] = {}
    balloon_group_children: dict[str, list[LayerTarget]] = {}
    balloon_group_parents: dict[str, set[str]] = {}
    raster_page_children, raster_panel_children = _collect_raster_targets_for_page(
        page,
        panels_by_key,
    )
    page_children.extend(raster_page_children)
    for coma_key, children in raster_panel_children.items():
        panel_children.setdefault(coma_key, []).extend(children)
    image_page_children, image_panel_children = _collect_image_targets_for_page(
        page,
        panels_by_key,
    )
    page_children.extend(image_page_children)
    for coma_key, children in image_panel_children.items():
        panel_children.setdefault(coma_key, []).extend(children)
    image_path_page_children, image_path_panel_children = _collect_image_path_targets_for_page(
        page,
        panels_by_key,
    )
    page_children.extend(image_path_page_children)
    for coma_key, children in image_path_panel_children.items():
        panel_children.setdefault(coma_key, []).extend(children)
    fill_page_children, fill_panel_children = _collect_fill_targets_for_page(
        page,
        panels_by_key,
    )
    page_children.extend(fill_page_children)
    for coma_key, children in fill_panel_children.items():
        panel_children.setdefault(coma_key, []).extend(children)

    # 紐付けペア (2026-07-12): テキストとフキダシが balloon.text_id /
    # text.parent_balloon_id で相互に一致する場合だけ「紐付け」とみなし、
    # テキストの直後にフキダシを並べ替える。balloon.text_id は単一値のため、
    # 複数テキストが同じフキダシへ parent_balloon_id を向けていても、相互一致
    # するテキストは高々1件 (=「最後のテキストの直後に1回だけ」を自然に満たす)。
    # グループ化フキダシ (merge_group_id 有り) はこの並べ替えの対象外。
    balloon_text_id_by_bid: dict[str, str] = {}
    balloon_container_by_bid: dict[str, list] = {}
    text_pair_anchor: dict[str, str] = {}  # bid -> tid (紐付け確定したペア)

    for entry in reversed(list(getattr(page, "balloons", []))):
        bid = _ensure_unique_id(entry, used_balloon, "balloon")
        explicit_parent = _explicit_entry_parent(entry, page, panels_by_key)
        if explicit_parent is not None:
            parent, depth = explicit_parent
            # オーソリティ親が今回の panels_by_key 範囲外なら skip。
            # (all-panels 呼び出しでは全コマが含まれるため skip されない)
            if depth == 2 and parent not in panels_by_key:
                continue
        else:
            panel = coma_containing_point(page, *entry_center(entry))
            if panel is not None:
                parent = coma_stack_key(page, panel)
                depth = 2
                if parent not in panels_by_key:
                    continue
            else:
                parent = page_key
                depth = 1
        group_id = str(getattr(entry, "merge_group_id", "") or "")
        if group_id:
            group_key = f"{page_key}:{group_id}"
            balloon_group_parents.setdefault(group_key, set()).add(parent)
            if group_key not in balloon_groups:
                label = group_id.replace("balloon_group_", "フキダシ結合 ")
                balloon_groups[group_key] = LayerTarget(
                    "balloon_group", group_key, label, parent, depth
                )
            label = getattr(entry, "title", "") or _jp_layer_label("balloon", bid)
            target = LayerTarget("balloon", f"{page_key}:{bid}", label, group_key, depth + 1)
            balloon_group_children.setdefault(group_key, []).append(target)
            continue
        label = getattr(entry, "title", "") or _jp_layer_label("balloon", bid)
        target = LayerTarget("balloon", f"{page_key}:{bid}", label, parent, depth)
        container = panel_children.setdefault(parent, []) if depth == 2 else page_children
        container.append(target)
        balloon_text_id_by_bid[bid] = str(getattr(entry, "text_id", "") or "")
        balloon_container_by_bid[bid] = container

    for group_key, group_target in balloon_groups.items():
        if len(balloon_group_parents.get(group_key, set())) > 1:
            group_target = LayerTarget(
                "balloon_group",
                group_key,
                group_target.label,
                page_key,
                1,
            )
            children = [
                LayerTarget(child.kind, child.key, child.label, group_key, 2)
                for child in balloon_group_children.get(group_key, [])
            ]
        else:
            children = balloon_group_children.get(group_key, [])
        if group_target.depth == 2:
            panel_children.setdefault(group_target.parent_key, []).append(group_target)
            panel_children[group_target.parent_key].extend(children)
        else:
            page_children.append(group_target)
            page_children.extend(children)

    for entry in reversed(list(getattr(page, "texts", []))):
        tid = _ensure_unique_id(entry, used_text, "text")
        label = getattr(entry, "title", "") or getattr(entry, "body", "") or _jp_layer_label("text", tid)
        explicit_parent = _explicit_entry_parent(entry, page, panels_by_key)
        if explicit_parent is not None:
            parent, depth = explicit_parent
            if depth == 2 and parent not in panels_by_key:
                continue
        else:
            center = entry_center(entry)
            panel = coma_containing_point(page, *center)
            if panel is not None:
                parent = coma_stack_key(page, panel)
                depth = 2
                if parent not in panels_by_key:
                    continue
            else:
                parent = page_key
                depth = 1
        target = LayerTarget("text", f"{page_key}:{tid}", label, parent, depth)
        container = panel_children.setdefault(parent, []) if depth == 2 else page_children
        container.append(target)
        # 紐付けペア (2026-07-12): balloon.text_id がこのテキストを指し、かつ
        # 同じコンテナ (同じ親) に属する場合だけ、紐付け対象として扱う。
        pbid = str(getattr(entry, "parent_balloon_id", "") or "")
        if (
            pbid
            and balloon_text_id_by_bid.get(pbid) == tid
            and balloon_container_by_bid.get(pbid) is container
        ):
            balloon_uid = target_uid("balloon", f"{page_key}:{pbid}")
            text_uid = target_uid("text", f"{page_key}:{tid}")
            b_idx = next((i for i, t in enumerate(container) if t.uid == balloon_uid), -1)
            if b_idx >= 0:
                container[b_idx] = replace(container[b_idx], pair_key=text_uid)
                container[-1] = replace(container[-1], pair_key=balloon_uid)
                text_pair_anchor[pbid] = tid

    # 紐付け確定ペアを、コンテナ内でテキストの直後にフキダシが来るよう並べ替える。
    # 紐付けの無いフキダシは触らない (従来の相対位置を維持)。
    for bid, tid in text_pair_anchor.items():
        container = balloon_container_by_bid.get(bid)
        if container is None:
            continue
        balloon_uid = target_uid("balloon", f"{page_key}:{bid}")
        text_uid = target_uid("text", f"{page_key}:{tid}")
        b_idx = next((i for i, t in enumerate(container) if t.uid == balloon_uid), -1)
        if b_idx < 0:
            continue
        balloon_target = container.pop(b_idx)
        t_idx = next((i for i, t in enumerate(container) if t.uid == text_uid), len(container))
        container.insert(t_idx + 1, balloon_target)

    for coma_key in panels_by_key:
        targets.extend(panel_children.get(coma_key, []))
    if include_page_children:
        targets.extend(page_children)
    return targets


def _collect_layer_folder_targets(work) -> list[LayerTarget]:
    if work is None:
        return []
    depths = layer_folder_utils.folder_depths(work)
    targets: list[LayerTarget] = []
    for folder in reversed(list(getattr(work, "layer_folders", []))):
        key = layer_folder_utils.folder_key(folder)
        if not key or key not in depths:
            continue
        if layer_folder_utils.folder_has_collapsed_ancestor(work, key):
            continue
        parent_key = layer_folder_utils.folder_parent_key(folder)
        if not _layer_folder_parent_visible(work, parent_key):
            continue
        label = str(getattr(folder, "title", "") or key)
        targets.append(LayerTarget(LAYER_FOLDER_KIND, key, label, parent_key, depths[key]))
    return targets


def _layer_folder_parent_visible(work, parent_key: str) -> bool:
    return _layer_folder_parent_visible_impl(work, parent_key, set())


def _layer_folder_parent_visible_impl(work, parent_key: str, seen: set[str]) -> bool:
    parent_key = str(parent_key or "") or OUTSIDE_STACK_KEY
    if parent_key == OUTSIDE_STACK_KEY:
        return True
    if parent_key in seen:
        return False
    seen.add(parent_key)
    parent_folder = layer_folder_utils.find_folder(work, parent_key)
    if parent_folder is not None:
        return (
            bool(getattr(parent_folder, "expanded", True))
            and not layer_folder_utils.folder_has_collapsed_ancestor(work, parent_key)
            and _layer_folder_parent_visible_impl(work, layer_folder_utils.folder_parent_key(parent_folder), seen)
        )
    page_key, _child = split_child_key(parent_key)
    for page in getattr(work, "pages", []):
        if page_stack_key(page) == page_key:
            return bool(getattr(page, "stack_expanded", True))
    return False


def _retarget_targets_to_layer_folders(context, targets: list[LayerTarget]) -> list[LayerTarget]:
    work = get_work(context)
    if work is None:
        return targets
    depths = layer_folder_utils.folder_depths(work)
    if not depths:
        return targets
    out: list[LayerTarget] = []
    for target in targets:
        if target.kind not in layer_folder_utils.FOLDER_CHILD_KINDS:
            out.append(target)
            continue
        folder_key = layer_folder_utils.target_folder_key(context, target.kind, target.key)
        if folder_key not in depths:
            out.append(target)
            continue
        if not layer_folder_utils.folder_children_visible(work, folder_key):
            continue
        out.append(
            LayerTarget(
                target.kind,
                target.key,
                target.label,
                folder_key,
                depths[folder_key] + 1,
            )
        )
    return out


def _partition_effect_object_targets(work) -> tuple[list[LayerTarget], dict[str, list[LayerTarget]]]:
    """個別管理オブジェクトの効果線を安定IDで一覧化する。"""
    root_targets: list[LayerTarget] = []
    targets_by_parent: dict[str, list[LayerTarget]] = {}
    from . import layer_object_model

    for obj in layer_object_model.iter_layer_objects("effect"):
        key = layer_object_model.stable_id(obj)
        if not key:
            continue
        parent_key = layer_object_model.parent_key(obj)
        label = layer_object_model.display_title(obj)
        target = LayerTarget(
            "effect",
            key,
            label,
            parent_key if parent_key else "",
            gp_parent.parent_depth(parent_key) if parent_key else 0,
        )
        if parent_key and gp_parent.parent_key_exists(work, parent_key):
            targets_by_parent.setdefault(parent_key, []).append(target)
        else:
            root_targets.append(target)
    return root_targets, targets_by_parent


def _partition_gp_object_targets(work) -> tuple[list[LayerTarget], dict[str, list[LayerTarget]]]:
    """個別管理オブジェクトの手描きを安定IDで一覧化する。"""
    root_targets: list[LayerTarget] = []
    targets_by_parent: dict[str, list[LayerTarget]] = {}
    from . import layer_object_model

    for obj in layer_object_model.iter_layer_objects("gp"):
        key = layer_object_model.stable_id(obj)
        if not key:
            continue
        parent_key = layer_object_model.parent_key(obj)
        target = LayerTarget(
            "gp",
            key,
            layer_object_model.display_title(obj),
            parent_key if parent_key else "",
            gp_parent.parent_depth(parent_key) if parent_key else 0,
        )
        if parent_key and gp_parent.parent_key_exists(work, parent_key):
            targets_by_parent.setdefault(parent_key, []).append(target)
        else:
            root_targets.append(target)
    return root_targets, targets_by_parent


def collect_targets(context) -> list[LayerTarget]:
    """現在の作品/シーンから、前面→背面の統合レイヤー候補を返す."""
    scene = context.scene
    work = get_work(context)
    targets: list[LayerTarget] = []
    gp_root_targets, gp_targets_by_parent = _partition_gp_object_targets(work)
    effect_root_targets, effect_targets_by_parent = _partition_effect_object_targets(work)

    if work is not None and getattr(work, "loaded", False):
        from . import page_range

        targets.extend(
            _collect_outside_layer_targets(
                work,
                scene,
                gp_root_targets,
                effect_root_targets,
            )
        )
        for page in work.pages:
            if not page_range.page_in_range(page):
                continue
            page_key = page_stack_key(page)
            label = _page_label(page_key, getattr(page, "title", ""))
            targets.append(LayerTarget(PAGE_KIND, page_key, label))
            if not bool(getattr(page, "stack_expanded", True)):
                continue
            panels = sorted(
                list(getattr(page, "comas", [])),
                key=lambda p: int(getattr(p, "z_order", 0)),
                reverse=True,
            )
            panels_by_key: dict[str, object] = {}
            for panel in panels:
                key = coma_stack_key(page, panel)
                panels_by_key[key] = panel

            # コマ外のページ直下レイヤーは、ページ内のどのコマよりも前面に置く。
            all_page_layers = _collect_page_layer_targets(page, panels_by_key)
            visible_parent_keys = {page_key}
            for target in gp_targets_by_parent.get(page_key, []):
                targets.append(target)
                visible_parent_keys.add(target.key)
            for target in effect_targets_by_parent.get(page_key, []):
                targets.append(target)
                visible_parent_keys.add(target.key)
            for target in all_page_layers:
                if target.parent_key in visible_parent_keys:
                    targets.append(target)
                    visible_parent_keys.add(target.key)

            for panel in panels:
                key = coma_stack_key(page, panel)
                panel_label = _coma_display_label(panel, getattr(panel, "coma_id", "") or key)
                targets.append(LayerTarget(COMA_KIND, key, panel_label, page_key, 1))
                targets.extend(gp_targets_by_parent.get(key, []))
                targets.extend(effect_targets_by_parent.get(key, []))
                targets.extend(
                    _collect_page_layer_targets(
                        page, {key: panel}, include_page_children=False
                    )
                )
                targets.append(
                    LayerTarget(
                        COMA_PREVIEW_KIND,
                        coma_preview_key(key),
                        "コマプレビュー",
                        key,
                        2,
                    )
                )

    elif gp_root_targets or effect_root_targets:
        targets.extend(effect_root_targets)
        targets.extend(gp_root_targets)

    if work is not None and getattr(work, "loaded", False):
        targets.extend(_collect_layer_folder_targets(work))
        targets = _retarget_targets_to_layer_folders(context, targets)

    # 防御: 万一 UID 重複が混入してもスタックには 1 行しか出さない。
    # (per-panel 呼び出しと spatial fallback の組合せで重複が紛れ込むケースの保険)
    seen: set[str] = set()
    deduped: list[LayerTarget] = []
    for t in targets:
        if not _target_has_stack_row(t):
            continue
        if t.uid in seen:
            continue
        seen.add(t.uid)
        deduped.append(t)
    return deduped


def _set_item_from_target(item, target: LayerTarget) -> None:
    # 既存値と同じなら代入しない。同値でも代入すると Scene が更新済み扱いになり、
    # depsgraph_update が発火する。sync_layer_stack はビューポート描画コールバック
    # からも毎フレーム呼ばれるため、無条件代入だと「描画→更新→再描画」の無限ループに
    # なり、用紙ガイド線などの細線がちらつく。変化がある項目だけ書き込む。
    if item.kind != target.kind:
        item.kind = target.kind
    if item.name != target.label:
        item.name = target.label
    if item.key != target.key:
        item.key = target.key
    if item.label != target.label:
        item.label = target.label
    if item.parent_key != target.parent_key:
        item.parent_key = target.parent_key
    if item.depth != target.depth:
        item.depth = target.depth


def _find_insert_index_for_target(stack, target: LayerTarget) -> int:
    if target.kind == OUTSIDE_KIND:
        return 0
    # 紐付けペア (2026-07-12): 増分同期で紐付き相手が既にスタックへ載っている
    # 場合、汎用の「親の直後」規則より優先してペア隣接位置へ挿入する。
    # (同一バッチでテキスト→フキダシの順に処理される場合、テキストは先に
    # スタックへ入っているため、フキダシ側のこの分岐で正しく直後へ入る)
    if target.pair_key:
        if target.kind == "balloon":
            for i, item in enumerate(stack):
                if stack_item_uid(item) == target.pair_key:
                    return i + 1
        elif target.kind == "text":
            for i, item in enumerate(stack):
                if stack_item_uid(item) == target.pair_key:
                    return i
    if target.parent_key:
        parent_idx = -1
        last_child_idx = -1
        for i, item in enumerate(stack):
            if item.key == target.parent_key and item.kind in {OUTSIDE_KIND, PAGE_KIND, COMA_KIND, "balloon_group", LAYER_FOLDER_KIND}:
                parent_idx = i
                last_child_idx = max(last_child_idx, i)
            elif getattr(item, "parent_key", "") == target.parent_key:
                last_child_idx = i
        if parent_idx >= 0 and target.kind != COMA_PREVIEW_KIND and target.kind in (PAGE_COMA_CHILD_KINDS | {LAYER_FOLDER_KIND}):
            return parent_idx + 1
        if last_child_idx >= 0:
            return last_child_idx + 1
        if parent_idx >= 0:
            return parent_idx + 1
    if target.kind == PAGE_KIND:
        last_page = -1
        for i, item in enumerate(stack):
            if item.kind == PAGE_KIND:
                last_page = i
        return last_page + 1
    return len(stack)


def _add_target_to_stack(stack, target: LayerTarget) -> None:
    item = stack.add()
    _set_item_from_target(item, target)
    from_index = len(stack) - 1
    to_index = max(0, min(_find_insert_index_for_target(stack, target), from_index))
    if to_index != from_index:
        stack.move(from_index, to_index)


def _ordered_items_by_uid(items, uid_order: list[str] | None):
    if uid_order is None:
        return items
    ordered = []
    used_uids: set[str] = set()
    for uid in uid_order:
        for item in items:
            if stack_item_uid(item) == uid and uid not in used_uids:
                ordered.append(item)
                used_uids.add(uid)
                break
    ordered.extend(item for item in items if stack_item_uid(item) not in used_uids)
    return ordered


def _normalize_tree_order(
    stack,
    page_key_order: list[str] | None = None,
    coma_key_order_by_page: dict[str, list[str]] | None = None,
) -> None:
    """ページ/コマを常にツリー構造へ戻す。

    UIList のD&Dは親子制約を知らないため、ページが子階層へ落ちたり
    コマが別コマ配下へ落ちたように見える並びを、次の描画で正規化する。
    """
    current = [stack_item_uid(item) for item in stack]
    desired: list[str] = []
    used: set[str] = set()

    def _append_uid(item) -> None:
        uid = stack_item_uid(item)
        if uid in used:
            return
        desired.append(uid)
        used.add(uid)

    page_items = [item for item in stack if item.kind == PAGE_KIND]
    if page_key_order is not None:
        page_uid_order = [target_uid(PAGE_KIND, key) for key in page_key_order]
        page_items = _ordered_items_by_uid(page_items, page_uid_order)

    def _append_subtree_in_stack_order(parent_key: str) -> None:
        """``parent_key`` 直下の子をスタック順で append し、コンテナなら再帰."""
        for child in stack:
            uid = stack_item_uid(child)
            if uid in used:
                continue
            if str(getattr(child, "parent_key", "") or "") != parent_key:
                continue
            _append_uid(child)
            kind = getattr(child, "kind", "")
            if kind in {OUTSIDE_KIND, COMA_KIND, "balloon_group", LAYER_FOLDER_KIND}:
                _append_subtree_in_stack_order(getattr(child, "key", ""))

    def _append_page_subtree(page_item) -> None:
        _append_uid(page_item)
        page_key = page_item.key
        if coma_key_order_by_page is not None:
            # align モード: コマを実データ順に並べ、その後にページ直下子を出す.
            panel_items = [
                item
                for item in stack
                if item.kind == COMA_KIND and split_child_key(item.key)[0] == page_key
            ]
            panel_uid_order = [
                target_uid(COMA_KIND, key)
                for key in coma_key_order_by_page.get(page_key, [])
            ]
            panel_items = _ordered_items_by_uid(panel_items, panel_uid_order)
            for panel_item in panel_items:
                _append_uid(panel_item)
                _append_subtree_in_stack_order(panel_item.key)
            for child in stack:
                if (
                    str(getattr(child, "parent_key", "") or "") == page_key
                    and child.kind != COMA_KIND
                ):
                    _append_uid(child)
                    kind = getattr(child, "kind", "")
                    if kind in {"balloon_group", LAYER_FOLDER_KIND}:
                        _append_subtree_in_stack_order(child.key)
        else:
            # デフォルト: スタック順を尊重。ページ直下の子(ページ直下 GP, コマ等)
            # を出現順に append。これにより「ページとその第1コマの間に GP を
            # 入れる」など、ユーザーが選んだ任意位置を保てる.
            _append_subtree_in_stack_order(page_key)

    outside_item = _find_stack_item(stack, OUTSIDE_KIND, OUTSIDE_STACK_KEY)
    if outside_item is not None:
        _append_uid(outside_item)
        _append_subtree_in_stack_order(OUTSIDE_STACK_KEY)

    if page_key_order is not None:
        for page_item in page_items:
            _append_page_subtree(page_item)

    for item in stack:
        if stack_item_uid(item) in used:
            continue
        if item.kind == OUTSIDE_KIND:
            _append_uid(item)
            _append_subtree_in_stack_order(item.key)
        elif item.kind == PAGE_KIND:
            _append_page_subtree(item)
        elif not getattr(item, "parent_key", ""):
            _append_uid(item)

    desired.extend(uid for uid in current if uid not in used)
    for target_index, uid in enumerate(desired):
        current_index = next((i for i, item in enumerate(stack) if stack_item_uid(item) == uid), -1)
        if current_index >= 0 and current_index != target_index:
            stack.move(current_index, target_index)


def sync_layer_stack(
    context,
    *,
    preserve_active_index: bool = False,
    align_page_order: bool = False,
    align_coma_order: bool = False,
):
    """統合レイヤーリストを実データに同期する。

    既存行の並びは維持し、消えた実体だけを削除、新規実体だけを前面側へ
    追加する。これにより UIList 側のD&D並び替えを上書きしない。
    """
    scene = context.scene
    stack = getattr(scene, "bmanga_layer_stack", None)
    if stack is None:
        return None
    old_active_index = int(getattr(scene, "bmanga_active_layer_stack_index", -1))
    old_active_uid = ""
    if 0 <= old_active_index < len(stack):
        old_active_uid = stack_item_uid(stack[old_active_index])

    targets = collect_targets(context)
    target_by_uid = {target.uid: target for target in targets}

    for i in range(len(stack) - 1, -1, -1):
        if stack_item_uid(stack[i]) not in target_by_uid:
            stack.remove(i)

    existing = {stack_item_uid(item) for item in stack}
    for item in stack:
        target = target_by_uid.get(stack_item_uid(item))
        if target is not None:
            _set_item_from_target(item, target)

    missing = [target for target in targets if target.uid not in existing]
    for target in missing:
        _add_target_to_stack(stack, target)
    page_key_order = None
    if align_page_order:
        work = get_work(context)
        if work is not None and getattr(work, "loaded", False):
            page_key_order = [page_stack_key(page) for page in work.pages]
    coma_key_order_by_page = None
    if align_coma_order:
        coma_key_order_by_page = {}
        for target in targets:
            if target.kind != COMA_KIND:
                continue
            page_key, _stem = split_child_key(target.key)
            coma_key_order_by_page.setdefault(page_key, []).append(target.key)
    _normalize_tree_order(stack, page_key_order, coma_key_order_by_page)

    if preserve_active_index and old_active_uid:
        for i, item in enumerate(stack):
            if stack_item_uid(item) == old_active_uid:
                set_active_stack_index_silently(context, i)
                break
        else:
            _sync_active_stack_index(context)
    elif preserve_active_index and 0 <= old_active_index < len(stack):
        set_active_stack_index_silently(context, old_active_index)
    else:
        _sync_active_stack_index(context)
    sync_visible_layer_stack(context, stack=stack)
    return stack


def _stack_signature(scene) -> tuple[str, ...]:
    stack = getattr(scene, "bmanga_layer_stack", None)
    if stack is None:
        return ()
    return tuple(stack_item_uid(item) for item in stack)


def _remember_stack_signature(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    try:
        scene_key = int(scene.as_pointer())
    except Exception:  # noqa: BLE001
        scene_key = id(scene)
    _draw_stack_signatures[scene_key] = _stack_signature(scene)


def remember_layer_stack_signature(context) -> None:
    """現在の UIList 並びを既知状態として記録する。Operator からの同期後に使う."""
    _remember_stack_signature(context)


def _find_stack_index_by_uid(stack, uid: str) -> int:
    if stack is None or not uid:
        return -1
    for i, item in enumerate(stack):
        if stack_item_uid(item) == uid:
            return i
    return -1


def _find_stack_item(stack, kind: str, key: str):
    for item in stack or []:
        if getattr(item, "kind", "") == kind and getattr(item, "key", "") == key:
            return item
    return None


def _same_move_scope(a, b) -> bool:
    """右側の順序ボタンで同じ移動単位として扱える行かを返す."""
    a_kind = getattr(a, "kind", "")
    b_kind = getattr(b, "kind", "")
    a_parent = str(getattr(a, "parent_key", "") or "")
    b_parent = str(getattr(b, "parent_key", "") or "")
    a_coma_child = a_kind in COMA_REORDER_KINDS and ":" in a_parent
    b_coma_child = b_kind in COMA_REORDER_KINDS and ":" in b_parent
    if a_coma_child or b_coma_child:
        return a_coma_child and b_coma_child and a_parent == b_parent
    if a_kind == PAGE_KIND or b_kind == PAGE_KIND:
        return a_kind == b_kind == PAGE_KIND
    if a_kind == COMA_KIND or b_kind == COMA_KIND:
        return (
            a_kind == b_kind == COMA_KIND
            and split_child_key(getattr(a, "key", ""))[0]
            == split_child_key(getattr(b, "key", ""))[0]
        )
    if a_kind == LAYER_FOLDER_KIND or b_kind == LAYER_FOLDER_KIND:
        return (
            a_kind == b_kind == LAYER_FOLDER_KIND
            and str(getattr(a, "parent_key", "") or "")
            == str(getattr(b, "parent_key", "") or "")
        )
    return (
        a_kind == b_kind
        and a_parent == b_parent
    )


def _move_scope_indices(stack, item) -> list[int]:
    return [i for i, candidate in enumerate(stack) if _same_move_scope(item, candidate)]


def _direction_from_target_index(from_index: int, to_index: int, stack_len: int) -> str:
    if to_index <= 0:
        return "FRONT"
    if to_index >= stack_len - 1:
        return "BACK"
    return "UP" if to_index < from_index else "DOWN"


def _target_index_for_stack_move(
    stack,
    from_index: int,
    to_index: int | None = None,
    direction: str = "",
) -> int:
    if stack is None or not (0 <= from_index < len(stack)):
        return -1
    if not direction:
        if to_index is None:
            return -1
        direction = _direction_from_target_index(from_index, int(to_index), len(stack))
    direction = str(direction or "").upper()
    siblings = _move_scope_indices(stack, stack[from_index])
    if from_index not in siblings:
        return -1
    pos = siblings.index(from_index)
    if direction == "FRONT":
        target_pos = 0
    elif direction == "BACK":
        target_pos = len(siblings) - 1
    elif direction == "UP":
        target_pos = pos - 1
    elif direction == "DOWN":
        target_pos = pos + 1
    else:
        return -1
    if not (0 <= target_pos < len(siblings)):
        return -1
    sibling_index = siblings[target_pos]
    if direction in {"BACK", "DOWN"} and sibling_index > from_index:
        return _stack_subtree_end_index(stack, sibling_index)
    return sibling_index


def _stack_subtree_end_index(stack, index: int) -> int:
    if stack is None or not (0 <= index < len(stack)):
        return index
    try:
        base_depth = int(getattr(stack[index], "depth", 0))
    except Exception:  # noqa: BLE001
        base_depth = 0
    end = index
    for i in range(index + 1, len(stack)):
        try:
            depth = int(getattr(stack[i], "depth", 0))
        except Exception:  # noqa: BLE001
            depth = 0
        if depth <= base_depth:
            break
        end = i
    return end


def _parent_item_allows_child(parent, child_kind: str) -> bool:
    parent_kind = getattr(parent, "kind", "")
    if parent_kind == OUTSIDE_KIND:
        return child_kind in PAGE_COMA_CHILD_KINDS or child_kind in {COMA_KIND, LAYER_FOLDER_KIND}
    if parent_kind == PAGE_KIND:
        return child_kind in PAGE_COMA_CHILD_KINDS or child_kind in {COMA_KIND, LAYER_FOLDER_KIND}
    if parent_kind == COMA_KIND:
        return child_kind in PAGE_COMA_CHILD_KINDS or child_kind == LAYER_FOLDER_KIND
    if parent_kind == LAYER_FOLDER_KIND:
        return child_kind in layer_folder_utils.FOLDER_CONTAINER_CHILD_KINDS
    return False


def _parent_key_exists_for_child(context, child_kind: str, parent_key: str) -> bool:
    parent_key = str(parent_key or "")
    if not parent_key:
        return True
    if parent_key == OUTSIDE_STACK_KEY:
        return child_kind in PAGE_COMA_CHILD_KINDS or child_kind in {COMA_KIND, LAYER_FOLDER_KIND}
    if layer_folder_utils.is_folder_key(context, parent_key):
        return child_kind in layer_folder_utils.FOLDER_CONTAINER_CHILD_KINDS
    if child_kind == COMA_KIND:
        return ":" not in parent_key and gp_parent.parent_key_exists(get_work(context), parent_key)
    work = get_work(context)
    if child_kind == LAYER_FOLDER_KIND and gp_parent.parent_key_exists(work, parent_key):
        return True
    if child_kind in PAGE_COMA_CHILD_KINDS and gp_parent.parent_key_exists(work, parent_key):
        return True
    return False


def _parent_key_from_flat_drop(stack, moved_index: int, child_kind: str) -> str:
    """UIList の平坦D&D位置から、移動した行の親キーを推定する."""
    if stack is None or moved_index <= 0:
        return ""
    previous = stack[moved_index - 1]
    if _parent_item_allows_child(previous, child_kind):
        return str(getattr(previous, "key", "") or "")
    if child_kind in PAGE_COMA_CHILD_KINDS and getattr(previous, "kind", "") in PAGE_COMA_CHILD_KINDS:
        return str(getattr(previous, "parent_key", "") or "")
    previous_parent_key = str(getattr(previous, "parent_key", "") or "")
    if previous_parent_key:
        return previous_parent_key
    return ""


def _parent_key_one_level_up(stack, parent_key: str) -> str:
    parent = (
        _find_stack_item(stack, OUTSIDE_KIND, parent_key)
        or _find_stack_item(stack, LAYER_FOLDER_KIND, parent_key)
        or _find_stack_item(stack, COMA_KIND, parent_key)
        or _find_stack_item(stack, PAGE_KIND, parent_key)
    )
    if parent is None:
        page_key, _child_key = split_child_key(parent_key)
        return page_key if page_key != parent_key else ""
    if getattr(parent, "kind", "") == COMA_KIND:
        page_key, _child_key = split_child_key(parent_key)
        return page_key
    return str(getattr(parent, "parent_key", "") or "")


def _drop_parent_from_nesting_delta(stack, item, moved_index: int, nesting_delta: int) -> str:
    old_parent_key = str(getattr(item, "parent_key", "") or "")
    if nesting_delta < 0:
        return _parent_key_one_level_up(stack, old_parent_key)
    if nesting_delta > 0:
        return _parent_key_from_flat_drop(stack, moved_index, getattr(item, "kind", ""))
    return _parent_key_from_flat_drop(stack, moved_index, getattr(item, "kind", ""))


def _stack_item_page_key(item, context=None) -> str:
    """スタック行が属するページキーを返す。ページ非依存なら "" を返す.

    - coma / balloon / text / balloon_group: 行 key が ``page_id:child_id`` 形式
    - raster / image / image_path: 永続化された ``parent_key`` のページプレフィックス
    - gp / effect: ``parent_key`` のページプレフィックス
    """
    kind = getattr(item, "kind", "")
    key = str(getattr(item, "key", "") or "")
    parent_key = str(getattr(item, "parent_key", "") or "")
    if key == OUTSIDE_STACK_KEY or parent_key == OUTSIDE_STACK_KEY:
        return ""
    if kind in {COMA_KIND, COMA_PREVIEW_KIND, "balloon", "balloon_group", "text"}:
        page_key, _ = split_child_key(key)
        if page_key == OUTSIDE_STACK_KEY:
            return ""
        return page_key
    if kind in {"raster", "image", "image_path", "gp", "effect"}:
        page_key, _ = split_child_key(parent_key)
        if page_key == OUTSIDE_STACK_KEY:
            return ""
        return page_key
    if kind == LAYER_FOLDER_KIND:
        work = get_work(context or bpy.context)
        semantic_parent = layer_folder_utils.semantic_parent_key_for_folder(work, key)
        if semantic_parent == OUTSIDE_STACK_KEY:
            return ""
        page_key, _ = split_child_key(semantic_parent)
        return page_key
    return ""


def _parent_key_page(parent_key: str) -> str:
    if not parent_key:
        return ""
    if parent_key == OUTSIDE_STACK_KEY:
        return ""
    page_key, _ = split_child_key(parent_key)
    if page_key == OUTSIDE_STACK_KEY:
        return ""
    return page_key


def _apply_stack_drop_hint(context, moved_uid: str, *, nesting_delta: int = 0) -> bool:
    """UIList D&Dの位置/横方向ヒントを、保存可能な親変更へ変換する."""
    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
    moved_index = _find_stack_index_by_uid(stack, moved_uid)
    if moved_index < 0:
        return False
    item = stack[moved_index]
    kind = getattr(item, "kind", "")
    if kind == COMA_PREVIEW_KIND:
        return False
    if kind not in {COMA_KIND, "gp", "effect", "raster", "image", "image_path", "balloon", "text", LAYER_FOLDER_KIND}:
        return False
    parent_key = _drop_parent_from_nesting_delta(stack, item, moved_index, nesting_delta)
    old_parent_key = str(getattr(item, "parent_key", "") or "")
    if parent_key and not _parent_key_exists_for_child(context, kind, parent_key):
        return False
    if layer_folder_utils.is_folder_key(context, parent_key):
        if kind == LAYER_FOLDER_KIND:
            changed = layer_folder_utils.set_folder_parent(context, str(getattr(item, "key", "") or ""), parent_key)
            if changed:
                item.parent_key = parent_key
                parent = _find_stack_item(stack, LAYER_FOLDER_KIND, parent_key)
                item.depth = int(getattr(parent, "depth", -1)) + 1 if parent is not None else int(getattr(item, "depth", 0))
            return changed
        if layer_folder_utils.is_folder_child_kind(kind):
            return layer_folder_utils.assign_item_to_folder(context, item, parent_key)
        return False
    if kind == LAYER_FOLDER_KIND:
        changed = layer_folder_utils.set_folder_parent(
            context,
            str(getattr(item, "key", "") or ""),
            parent_key or OUTSIDE_STACK_KEY,
        )
        if changed:
            item.parent_key = parent_key or OUTSIDE_STACK_KEY
            parent = (
                _find_stack_item(stack, OUTSIDE_KIND, item.parent_key)
                or _find_stack_item(stack, PAGE_KIND, item.parent_key)
                or _find_stack_item(stack, COMA_KIND, item.parent_key)
            )
            item.depth = int(getattr(parent, "depth", -1)) + 1 if parent is not None else 0
        return changed
    try:
        from . import layer_stack_dnd

        if (
            layer_stack_dnd.child_can_use_semantic_parent(kind)
            and layer_stack_dnd.is_semantic_parent_key(context, parent_key)
        ):
            old_folder_key = old_parent_key if layer_folder_utils.is_folder_key(context, old_parent_key) else ""
            folder_changed = False
            if old_folder_key and layer_folder_utils.is_folder_child_kind(kind):
                folder_changed = layer_folder_utils.set_item_folder_key(context, item, "")
            changed = layer_stack_dnd.apply_semantic_parent_drop(context, item, parent_key)
            if old_folder_key and not changed and not folder_changed:
                layer_folder_utils.set_item_folder_key(context, item, old_folder_key)
            return bool(changed or folder_changed)
    except Exception:  # noqa: BLE001
        _logger.exception("semantic layer stack D&D parent drop failed")
        return False
    # page/coma/outside への意味的な D&D は上で layer_reparent に委譲済み。
    # フォールバック経路ではページをまたぐ単純 parent_key 書き換えを拒否する。
    entry_page = _stack_item_page_key(item, context)
    target_page = _parent_key_page(parent_key)
    if entry_page and target_page and entry_page != target_page:
        return False
    # 旧バージョンでは Y-only ドラッグでの「深く入れる」(depth 増加) を抑止していたが、
    # CSP / Photoshop のレイヤーパネルでは Y-drag だけでフォルダ/コマに直接入れられるのが
    # 標準。ここで block すると D&D が「入れたいのに入らない」状態になるため撤廃。
    if parent_key == old_parent_key:
        return False
    item.parent_key = parent_key
    parent = None
    if parent_key:
        parent = (
            _find_stack_item(stack, LAYER_FOLDER_KIND, parent_key)
            or _find_stack_item(stack, OUTSIDE_KIND, parent_key)
            or _find_stack_item(stack, PAGE_KIND, parent_key)
            or _find_stack_item(stack, COMA_KIND, parent_key)
        )
    item.depth = int(getattr(parent, "depth", -1)) + 1 if parent is not None else 0
    return True


def apply_stack_drop_hint(context, moved_uid: str, *, nesting_delta: int = 0) -> bool:
    """D&D中の同一行ドロップや横方向ドラッグを親変更として適用する。"""
    changed = _apply_stack_drop_hint(context, moved_uid, nesting_delta=nesting_delta)
    if changed:
        apply_stack_order(context)
        _remember_stack_signature(context)
    return changed


def _infer_moved_uid(previous: tuple[str, ...], current: tuple[str, ...]) -> str:
    if len(previous) != len(current) or previous == current:
        return ""
    first = -1
    last = -1
    for i, (old_uid, new_uid) in enumerate(zip(previous, current)):
        if old_uid != new_uid:
            if first < 0:
                first = i
            last = i
    if first < 0 or last < 0:
        return ""
    if previous[first] == current[last]:
        return previous[first]
    if previous[last] == current[first]:
        return previous[last]
    return ""


def _active_uid_from_signature(scene, signature: tuple[str, ...]) -> str:
    idx = int(getattr(scene, "bmanga_active_layer_stack_index", -1))
    if 0 <= idx < len(signature):
        return signature[idx]
    return ""


def apply_stack_order_if_ui_changed(context, *, moved_uid: str = "") -> bool:
    """UIList の D&D で変わった Collection 順を、同期で戻る前に実データへ適用する."""
    scene = getattr(context, "scene", None)
    if scene is None or getattr(scene, "bmanga_layer_stack", None) is None:
        return False
    try:
        scene_key = int(scene.as_pointer())
    except Exception:  # noqa: BLE001
        scene_key = id(scene)
    signature = _stack_signature(scene)
    previous = _draw_stack_signatures.get(scene_key)
    if previous is None:
        _remember_stack_signature(context)
        return False
    if previous == signature:
        return False
    if set(previous) != set(signature):
        _remember_stack_signature(context)
        return False
    if not moved_uid:
        moved_uid = _active_uid_from_signature(scene, signature)
    if not moved_uid:
        moved_uid = _infer_moved_uid(previous, signature)
    _apply_stack_drop_hint(context, moved_uid)
    apply_stack_order(context)
    _remember_stack_signature(context)
    return True


def _sync_real_objects_after_stack_order(context) -> None:
    """レイヤー一覧 D&D 後の実体オブジェクトを最新化する."""
    scene = getattr(context, "scene", None)
    work = get_work(context)
    if scene is None or work is None:
        return
    try:
        from . import layer_object_sync

        layer_object_sync.mirror_work_to_outliner(scene, work)
        with layer_object_sync.suppress_sync():
            layer_object_sync.assign_per_page_z_ranks(scene, work)
    except Exception:  # noqa: BLE001
        _logger.exception("layer stack real object sync failed")


def sync_layer_stack_after_data_change(
    context,
    *,
    align_page_order: bool = False,
    align_coma_order: bool = False,
) -> None:
    """Operator で実データを更新した直後に、UIList と既知シグネチャを揃える."""
    try:
        sync_layer_stack(
            context,
            align_page_order=align_page_order,
            align_coma_order=align_coma_order,
        )
        _remember_stack_signature(context)
        _sync_real_objects_after_stack_order(context)
        tag_view3d_redraw(context)
    except Exception:  # noqa: BLE001
        _logger.exception("layer stack sync after data change failed")


def normalize_paired_layer_order(context, pairs) -> None:
    """指定した (page_key, balloon_id, text_id) の組について、レイヤー
    スタック上でテキストの直後にフキダシが来るよう強制する (2026-07-12)。

    Meldex取込などで新規作成した紐付けペアの最終保証として呼び出す想定。
    既存の (今回新規作成していない) ペアは呼び出し側で ``pairs`` に含めない
    こと — このヘルパー自体は渡された組を無条件に並べ替える。
    """
    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
    if stack is None or not pairs:
        return
    changed = False
    for page_key, balloon_id, text_id in pairs:
        balloon_id = str(balloon_id or "")
        text_id = str(text_id or "")
        if not balloon_id or not text_id:
            continue
        balloon_uid = target_uid("balloon", f"{page_key}:{balloon_id}")
        text_uid = target_uid("text", f"{page_key}:{text_id}")
        balloon_idx = _find_stack_index_by_uid(stack, balloon_uid)
        text_idx = _find_stack_index_by_uid(stack, text_uid)
        if balloon_idx < 0 or text_idx < 0:
            continue
        # 親コンテナ (ページ直下 / コマ直下 / フォルダ) が異なるペアは移動しない。
        # フラットな stack.move はコンテナ境界を跨げるため、片方だけ別の親へ
        # 移動済みの場合に階層表示とインデックス位置が矛盾するのを防ぐ。
        balloon_parent = str(getattr(stack[balloon_idx], "parent_key", "") or "")
        text_parent = str(getattr(stack[text_idx], "parent_key", "") or "")
        if balloon_parent != text_parent:
            continue
        if text_idx == balloon_idx - 1:
            continue
        if text_idx > balloon_idx:
            stack.move(text_idx, balloon_idx)
        else:
            stack.move(balloon_idx, text_idx + 1)
        changed = True
    if changed:
        _remember_stack_signature(context)


def schedule_layer_stack_draw_maintenance(context) -> bool:
    """Panel.draw 中に Scene を書き換えず、必要な同期だけをタイマー予約する."""
    scene = getattr(context, "scene", None)
    if scene is None:
        return False
    stack = getattr(scene, "bmanga_layer_stack", None)
    if stack is None:
        return False
    try:
        scene_key = int(scene.as_pointer())
    except Exception:  # noqa: BLE001
        scene_key = id(scene)
    signature = _stack_signature(scene)
    try:
        from . import layer_stack_visible

        visible_current = layer_stack_visible.visible_layer_stack_is_current(
            context,
            stack,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("visible layer stack freshness check failed")
        visible_current = True
    if _stack_has_placeholder_rows(stack):
        # 同じ placeholder 状態を毎 draw で再 sync すると、sync タイマーが
        # tag_view3d_redraw を繰り返すためパネルを開いている間ずっとビューポートが
        # 再描画され続ける (約16回/秒の点滅)。signature が前回スケジュール時から
        # 変わっていない = 直前の sync で解消できなかった状態なら、再スケジュール
        # しない (解消可能なら signature が変わり、その時に再度 sync される)。
        if _draw_stack_signatures.get(scene_key) == signature:
            return False
        _draw_stack_signatures[scene_key] = signature
        schedule_layer_stack_sync()
        return True
    if not visible_current:
        _draw_stack_signatures[scene_key] = signature
        schedule_layer_stack_sync()
        return True
    previous = _draw_stack_signatures.get(scene_key)
    if previous is None:
        _draw_stack_signatures[scene_key] = signature
        if not signature:
            schedule_layer_stack_sync()
            return True
        return False
    if previous != signature:
        _draw_stack_signatures[scene_key] = signature
        apply_order = set(previous) == set(signature)
        moved_uid = ""
        if apply_order:
            moved_uid = _active_uid_from_signature(scene, signature)
            if not moved_uid:
                moved_uid = _infer_moved_uid(previous, signature)
        schedule_layer_stack_sync(apply_order=apply_order, moved_uid=moved_uid)
        return True
    elif not signature:
        schedule_layer_stack_sync()
        return True
    return False


def _active_key_from_scene(context) -> tuple[str, str] | None:
    scene = context.scene
    kind = getattr(scene, "bmanga_active_layer_kind", "gp")
    work = get_work(context)
    page = get_active_page(context)

    if kind == PAGE_KIND and work is not None:
        idx = int(getattr(work, "active_page_index", -1))
        if 0 <= idx < len(work.pages):
            return PAGE_KIND, page_stack_key(work.pages[idx])
    if kind == COMA_KIND and page is not None:
        idx = int(getattr(page, "active_coma_index", -1))
        if 0 <= idx < len(page.comas):
            return COMA_KIND, coma_stack_key(page, page.comas[idx])
    if kind == LAYER_FOLDER_KIND:
        key = str(getattr(scene, "bmanga_active_layer_folder_key", "") or "")
        if layer_folder_utils.is_folder_key(context, key):
            return LAYER_FOLDER_KIND, key
    if kind == "image":
        coll = getattr(scene, "bmanga_image_layers", None)
        idx = int(getattr(scene, "bmanga_active_image_layer_index", -1))
        if coll is not None and 0 <= idx < len(coll):
            return "image", getattr(coll[idx], "id", "")
    if kind == "image_path":
        coll = getattr(scene, "bmanga_image_path_layers", None)
        idx = int(getattr(scene, "bmanga_active_image_path_layer_index", -1))
        if coll is not None and 0 <= idx < len(coll):
            return "image_path", getattr(coll[idx], "id", "")
    if kind == "raster":
        coll = getattr(scene, "bmanga_raster_layers", None)
        idx = int(getattr(scene, "bmanga_active_raster_layer_index", -1))
        if coll is not None and 0 <= idx < len(coll):
            return "raster", getattr(coll[idx], "id", "")
    if kind == "fill":
        coll = getattr(scene, "bmanga_fill_layers", None)
        idx = int(getattr(scene, "bmanga_active_fill_layer_index", -1))
        if coll is not None and 0 <= idx < len(coll):
            return "fill", getattr(coll[idx], "id", "")
    if kind == "balloon" and page is not None:
        idx = int(getattr(page, "active_balloon_index", -1))
        if 0 <= idx < len(page.balloons):
            return "balloon", f"{page_stack_key(page)}:{getattr(page.balloons[idx], 'id', '')}"
    if kind == "text" and page is not None:
        idx = int(getattr(page, "active_text_index", -1))
        if 0 <= idx < len(page.texts):
            return "text", f"{page_stack_key(page)}:{getattr(page.texts[idx], 'id', '')}"
    if kind == "effect":
        key = str(getattr(scene, "bmanga_active_effect_layer_name", "") or "")
        obj, layer = _find_effect_layer_by_key(key)
        if obj is not None and layer is not None:
            from . import layer_object_model

            return "effect", layer_object_model.stable_id(obj)

    from . import layer_object_model

    view_layer = getattr(context, "view_layer", None)
    active_obj = getattr(getattr(view_layer, "objects", None), "active", None)
    if layer_object_model.is_layer_object(active_obj, "gp"):
        return "gp", layer_object_model.stable_id(active_obj)
    return None


def _sync_active_stack_index(context) -> None:
    scene = context.scene
    stack = getattr(scene, "bmanga_layer_stack", None)
    if stack is None:
        return
    active_key = _active_key_from_scene(context)
    if active_key is not None:
        uid = target_uid(*active_key)
        for i, item in enumerate(stack):
            if stack_item_uid(item) == uid:
                set_active_stack_index_silently(context, i)
                return
    idx = int(getattr(scene, "bmanga_active_layer_stack_index", -1))
    if idx >= len(stack):
        set_active_stack_index_silently(context, len(stack) - 1)
    elif idx < -1:
        set_active_stack_index_silently(context, -1)


def _find_by_id(coll, key: str):
    for i, entry in enumerate(coll):
        if getattr(entry, "id", "") == key:
            return i, entry
    return -1, None


def _find_effect_layer_by_key(key: str):
    """効果線の永続IDから (制御Object, content Layer) を返す。"""
    key = str(key or "")
    if not key:
        return None, None
    from . import layer_object_model

    obj = layer_object_model.find_layer_object("effect", key)
    return obj, layer_object_model.content_layer(obj)


def gp_parent_keys_for_page(page) -> set[str]:
    return gp_parent.parent_keys_for_page(page)


def gp_parent_key_for_coma(page, panel) -> str:
    return gp_parent.parent_key_for_coma(page, panel)


def gp_layers_for_parent_keys(context, parent_keys: set[str]) -> list[object]:
    _ = context
    keys = {str(key or "") for key in parent_keys if str(key or "")}
    if not keys:
        return []
    from . import layer_object_model

    return [
        layer
        for obj in layer_object_model.iter_layer_objects("gp")
        if layer_object_model.parent_key(obj) in keys
        for layer in (layer_object_model.content_layer(obj),)
        if layer is not None
    ]


def effect_layers_for_parent_keys(context, parent_keys: set[str]) -> list[object]:
    _ = context
    keys = {str(key or "") for key in parent_keys if str(key or "")}
    if not keys:
        return []
    from . import layer_object_model

    return [
        layer
        for obj in layer_object_model.iter_layer_objects("effect")
        if layer_object_model.parent_key(obj) in keys
        for layer in (layer_object_model.content_layer(obj),)
        if layer is not None
    ]


def _effect_layer_pairs_for_parent_keys(parent_keys: set[str]) -> list[tuple[object, object]]:
    keys = {str(key or "") for key in parent_keys if str(key or "")}
    if not keys:
        return []
    from . import layer_object_model

    return [
        (obj, layer)
        for obj in layer_object_model.iter_layer_objects("effect")
        if layer_object_model.parent_key(obj) in keys
        for layer in (layer_object_model.content_layer(obj),)
        if layer is not None
    ]


def _stamp_layer_object_parent(context, obj, parent_key: str) -> None:
    kind = str(obj.get(on.PROP_KIND, "") or "") if obj is not None else ""
    if kind not in {"gp", "effect"}:
        return
    parent_key = "" if parent_key == OUTSIDE_STACK_KEY else str(parent_key or "")
    parent_kind = "coma" if ":" in parent_key else ("page" if parent_key else "outside")
    try:
        from . import layer_object_sync as los

        los.stamp_layer_object(
            obj,
            kind=kind,
            bmanga_id=str(obj.get(on.PROP_ID, "") or obj.name),
            title=str(obj.get(on.PROP_TITLE, "") or obj.name),
            z_index=int(obj.get(on.PROP_Z_INDEX, 0) or 0),
            parent_kind=parent_kind,
            parent_key=parent_key,
            folder_id=str(obj.get(on.PROP_FOLDER_ID, "") or ""),
            scene=getattr(context, "scene", None),
        )
    except Exception:  # noqa: BLE001
        _logger.exception("layer object parent stamp failed: %s", getattr(obj, "name", ""))


def delete_gp_layers_for_parent_keys(context, parent_keys: set[str]) -> int:
    keys = {str(key or "") for key in parent_keys if str(key or "")}
    if not keys:
        return 0
    from . import layer_object_model

    removed = 0
    for obj in list(layer_object_model.iter_layer_objects("gp")):
        if layer_object_model.parent_key(obj) not in keys:
            continue
        try:
            if layer_object_model.remove_layer_object(obj):
                removed += 1
        except Exception:  # noqa: BLE001
            _logger.exception("delete gp object for parent failed: %s", getattr(obj, "name", ""))
    if removed:
        tag_view3d_redraw(context)
    return removed


def delete_effect_layers_for_parent_keys(context, parent_keys: set[str]) -> int:
    if not parent_keys:
        return 0
    removed = 0
    for obj, layer in list(_effect_layer_pairs_for_parent_keys(parent_keys)):
        try:
            from ..operators import effect_line_op

            effect_line_op._delete_effect_layer(context, obj, layer)
            removed += 1
        except Exception:  # noqa: BLE001
            _logger.exception("delete effect layer for parent failed: %s", getattr(layer, "name", ""))
    if removed:
        tag_view3d_redraw(context)
    return removed


def _reparent_layer_objects(
    context,
    kind: str,
    old_parent_key: str,
    new_parent_key: str,
) -> int:
    work = get_work(context)
    if not old_parent_key or not gp_parent.parent_key_exists(work, new_parent_key):
        return 0
    from . import layer_object_model

    source_page_id, _source_child_id = split_child_key(old_parent_key)
    target_page_id, _target_child_id = split_child_key(new_parent_key)
    cross_page = bool(
        source_page_id and target_page_id and source_page_id != target_page_id
    )
    changed = 0
    for obj in list(layer_object_model.iter_layer_objects(kind)):
        if layer_object_model.parent_key(obj) != old_parent_key:
            continue
        if cross_page:
            from . import cross_page_transfer

            if cross_page_transfer.transfer_layer_object_to_parent(
                context,
                work,
                obj,
                new_parent_key,
                folder_id="",
            ):
                changed += 1
            continue
        _stamp_layer_object_parent(context, obj, new_parent_key)
        changed += 1
    if changed:
        tag_view3d_redraw(context)
    return changed


def reparent_gp_layers(context, old_parent_key: str, new_parent_key: str) -> int:
    return _reparent_layer_objects(context, "gp", old_parent_key, new_parent_key)


def reparent_effect_layers(context, old_parent_key: str, new_parent_key: str) -> int:
    return _reparent_layer_objects(context, "effect", old_parent_key, new_parent_key)


def translate_gp_layers_for_parent_keys(context, parent_keys: set[str], dx_mm: float, dy_mm: float) -> int:
    moved = 0
    for layer in gp_layers_for_parent_keys(context, parent_keys):
        gp_parent.translate_layer(layer, dx_mm, dy_mm)
        moved += 1
    if moved:
        tag_view3d_redraw(context)
    return moved


def translate_effect_layers_for_parent_keys(context, parent_keys: set[str], dx_mm: float, dy_mm: float) -> int:
    keys = {str(key or "") for key in parent_keys if str(key or "")}
    # 新しい効果線 Object はページ/コマ Collection にリンクされるため、
    # ページ移動では Collection transform だけでワールド位置が追従する。
    # コマ単体移動時は Collection transform が走らないため stroke 座標を動かす。
    is_page_move = any(key != OUTSIDE_STACK_KEY and ":" not in key for key in keys)
    moved = 0
    for obj, layer in _effect_layer_pairs_for_parent_keys(keys):
        if is_page_move and str(obj.get(on.PROP_KIND, "") or "") == "effect":
            continue
        try:
            from ..operators import effect_line_op

            bounds = effect_line_op.effect_layer_bounds(obj, layer)
            center = effect_line_op.effect_layer_center(obj, layer, bounds)
            if bounds is not None:
                x, y, w, h = bounds
                new_center = None if center is None else (center[0] + dx_mm, center[1] + dy_mm)
                effect_line_op._write_effect_strokes(
                    context,
                    obj,
                    layer,
                    (x + dx_mm, y + dy_mm, w, h),
                    center_xy_mm=new_center,
                )
            else:
                gp_parent.translate_layer(layer, dx_mm, dy_mm)
        except Exception:  # noqa: BLE001
            gp_parent.translate_layer(layer, dx_mm, dy_mm)
        moved += 1
    if moved:
        tag_view3d_redraw(context)
    return moved


def capture_gp_layers_for_parent_keys(context, parent_keys: set[str]):
    return gp_parent.capture_layers(gp_layers_for_parent_keys(context, parent_keys))


def capture_effect_layers_for_parent_keys(context, parent_keys: set[str]):
    keys = {str(key or "") for key in parent_keys if str(key or "")}
    pairs = _effect_layer_pairs_for_parent_keys(keys)
    points = gp_parent.capture_layers([layer for _obj, layer in pairs])
    meta = []
    try:
        from ..operators import effect_line_op

        for obj, layer in pairs:
            key = effect_line_op._layer_meta_key(layer)
            data = effect_line_op._effect_meta(obj).get(key)
            meta.append((obj, key, dict(data) if isinstance(data, dict) else None))
    except Exception:  # noqa: BLE001
        meta = []
    return {"points": points, "effect_meta": meta}


def restore_gp_layer_snapshots(snapshot) -> None:
    if isinstance(snapshot, dict):
        gp_parent.restore_layers(snapshot.get("points", []))
        meta_snapshot = snapshot.get("effect_meta", [])
        if meta_snapshot:
            try:
                from ..operators import effect_line_op

                for obj, key, entry in meta_snapshot:
                    meta = effect_line_op._effect_meta(obj)
                    if entry is None:
                        meta.pop(str(key), None)
                    else:
                        meta[str(key)] = dict(entry)
                    effect_line_op._write_effect_meta(obj, meta)
            except Exception:  # noqa: BLE001
                pass
        return
    gp_parent.restore_layers(snapshot)


def raster_entries_for_parent_keys(context, parent_keys: set[str]) -> list[object]:
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bmanga_raster_layers", None) if scene is not None else None
    if coll is None or not parent_keys:
        return []
    keys = {str(key or "") for key in parent_keys if str(key or "")}
    return [
        entry for entry in coll
        if str(getattr(entry, "parent_key", "") or "") in keys
    ]


def translate_raster_layers_for_parent_keys(context, parent_keys: set[str], dx_mm: float, dy_mm: float) -> int:
    try:
        from ..operators import raster_layer_op
    except Exception:  # noqa: BLE001
        return 0
    moved = 0
    for entry in raster_entries_for_parent_keys(context, parent_keys):
        try:
            if raster_layer_op.translate_raster_layer_pixels(context, entry, dx_mm, dy_mm):
                moved += 1
        except Exception:  # noqa: BLE001
            _logger.exception("translate raster pixels failed: %s", getattr(entry, "id", ""))
    if moved:
        tag_view3d_redraw(context)
    return moved


def capture_raster_layers_for_parent_keys(context, parent_keys: set[str]):
    try:
        from ..operators import raster_layer_op
    except Exception:  # noqa: BLE001
        return []
    snapshots = []
    for entry in raster_entries_for_parent_keys(context, parent_keys):
        image = raster_layer_op.ensure_raster_image(context, entry, create_missing=False)
        if image is None:
            continue
        try:
            total = int(image.size[0]) * int(image.size[1]) * 4
            data = array("f", image.pixels[:])
            if len(data) != total:
                continue
            snapshots.append((str(getattr(entry, "id", "") or ""), str(image.name), data))
        except Exception:  # noqa: BLE001
            _logger.exception("capture raster pixels failed: %s", getattr(entry, "id", ""))
    return snapshots


def restore_raster_layer_snapshots(context, snapshot) -> None:
    try:
        from ..operators import raster_layer_op
    except Exception:  # noqa: BLE001
        raster_layer_op = None
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bmanga_raster_layers", None) if scene is not None else None
    entry_by_id = {
        str(getattr(entry, "id", "") or ""): entry
        for entry in (coll or [])
    }
    for entry_id, image_name, data in snapshot or []:
        image = bpy.data.images.get(str(image_name))
        if image is None:
            continue
        try:
            image.pixels.foreach_set(data)
            image.update()
            entry = entry_by_id.get(str(entry_id))
            if entry is not None and raster_layer_op is not None:
                raster_layer_op.mark_raster_dirty(entry)
        except Exception:  # noqa: BLE001
            _logger.exception("restore raster pixels failed: %s", image_name)


def resolve_stack_item(context, item):
    """スタック行が参照する実体を辞書で返す。見つからなければ None."""
    if item is None:
        return None
    kind = getattr(item, "kind", "")
    key = getattr(item, "key", "")
    scene = context.scene
    work = get_work(context)
    page = get_active_page(context)

    if kind == OUTSIDE_KIND:
        if work is None:
            return None
        return {"kind": kind, "target": work, "object": None, "index": -1}
    if kind == PAGE_KIND:
        if work is None:
            return None
        idx, entry = _find_by_id(work.pages, key)
        return {"kind": kind, "target": entry, "object": None, "index": idx}
    if kind == COMA_KIND:
        if work is None:
            return None
        page_id, stem = split_child_key(key)
        if page_id == OUTSIDE_STACK_KEY:
            for coma_idx, panel in enumerate(getattr(work, "shared_comas", [])):
                if getattr(panel, "coma_id", "") == stem or getattr(panel, "id", "") == stem:
                    return {
                        "kind": kind,
                        "target": panel,
                        "object": None,
                        "index": coma_idx,
                        "page": None,
                        "page_index": -1,
                    }
            return {
                "kind": kind,
                "target": None,
                "object": None,
                "index": -1,
                "page": None,
                "page_index": -1,
            }
        page_idx, target_page = _find_by_id(work.pages, page_id)
        if target_page is None:
            return None
        for coma_idx, panel in enumerate(target_page.comas):
            if getattr(panel, "coma_id", "") == stem or getattr(panel, "id", "") == stem:
                return {
                    "kind": kind,
                    "target": panel,
                    "object": None,
                    "index": coma_idx,
                    "page": target_page,
                    "page_index": page_idx,
                }
        return {"kind": kind, "target": None, "object": None, "index": -1,
                "page": target_page, "page_index": page_idx}
    if kind == COMA_PREVIEW_KIND:
        if work is None:
            return None
        coma_key = str(key or "")
        marker = ":__preview__"
        if coma_key.endswith(marker):
            coma_key = coma_key[: -len(marker)]
        page_id, stem = split_child_key(coma_key)
        page_idx, target_page = _find_by_id(work.pages, page_id)
        if target_page is None:
            return None
        for coma_idx, panel in enumerate(target_page.comas):
            if getattr(panel, "coma_id", "") == stem or getattr(panel, "id", "") == stem:
                obj = None
                try:
                    from . import coma_plane

                    obj = coma_plane.find_coma_plane_object(page_id, stem)
                except Exception:  # noqa: BLE001
                    obj = None
                return {
                    "kind": kind,
                    "target": panel,
                    "object": obj,
                    "index": coma_idx,
                    "page": target_page,
                    "page_index": page_idx,
                }
        return {"kind": kind, "target": None, "object": None, "index": -1,
                "page": target_page, "page_index": page_idx}
    if kind == "gp":
        from . import layer_object_model

        obj = layer_object_model.find_layer_object("gp", key)
        target = layer_object_model.content_layer(obj)
        return {
            "kind": kind,
            "target": target,
            "object": obj,
            "stable_id": key,
            "index": -1,
        }
    if kind == LAYER_FOLDER_KIND:
        if work is None:
            return None
        for idx, folder in enumerate(getattr(work, "layer_folders", [])):
            if layer_folder_utils.folder_key(folder) == key:
                return {"kind": kind, "target": folder, "object": None, "index": idx}
        return {"kind": kind, "target": None, "object": None, "index": -1}
    if kind == "image":
        coll = getattr(scene, "bmanga_image_layers", None)
        if coll is None:
            return None
        idx, entry = _find_by_id(coll, key)
        return {"kind": kind, "target": entry, "object": None, "index": idx}
    if kind == "image_path":
        coll = getattr(scene, "bmanga_image_path_layers", None)
        if coll is None:
            return None
        idx, entry = _find_by_id(coll, key)
        return {"kind": kind, "target": entry, "object": None, "index": idx}
    if kind == "raster":
        coll = getattr(scene, "bmanga_raster_layers", None)
        if coll is None:
            return None
        idx, entry = _find_by_id(coll, key)
        target_page = None
        page_idx = -1
        if work is not None and entry is not None:
            parent_key = str(getattr(entry, "parent_key", "") or "")
            for i, candidate in enumerate(getattr(work, "pages", [])):
                if parent_key in {
                    getattr(candidate, "id", ""),
                    page_stack_key(candidate),
                }:
                    target_page = candidate
                    page_idx = i
                    break
        return {
            "kind": kind,
            "target": entry,
            "object": None,
            "index": idx,
            "page": target_page,
            "page_index": page_idx,
        }
    if kind == "fill":
        coll = getattr(scene, "bmanga_fill_layers", None)
        if coll is None:
            return None
        idx, entry = _find_by_id(coll, key)
        return {"kind": kind, "target": entry, "object": None, "index": idx}
    if kind == "balloon":
        page_id, child_id = split_child_key(key)
        if page_id == OUTSIDE_STACK_KEY and work is not None:
            idx, entry = _find_by_id(getattr(work, "shared_balloons", []), child_id or key)
            return {
                "kind": kind,
                "target": entry,
                "object": None,
                "index": idx,
                "page": None,
                "page_index": -1,
            }
        target_page = page
        page_idx = int(getattr(work, "active_page_index", -1)) if work is not None else -1
        if page_id and work is not None:
            page_idx, target_page = _find_by_id(work.pages, page_id)
        if target_page is None:
            return None
        idx, entry = _find_by_id(target_page.balloons, child_id or key)
        return {
            "kind": kind,
            "target": entry,
            "object": None,
            "index": idx,
            "page": target_page,
            "page_index": page_idx,
        }
    if kind == "balloon_group":
        page_id, group_id = split_child_key(key)
        target_page = page
        page_idx = int(getattr(work, "active_page_index", -1)) if work is not None else -1
        if page_id and work is not None:
            page_idx, target_page = _find_by_id(work.pages, page_id)
        if target_page is None:
            return None
        return {
            "kind": kind,
            "target": target_page,
            "object": None,
            "index": -1,
            "page": target_page,
            "page_index": page_idx,
            "group_id": group_id,
        }
    if kind == "text":
        page_id, child_id = split_child_key(key)
        if page_id == OUTSIDE_STACK_KEY and work is not None:
            idx, entry = _find_by_id(getattr(work, "shared_texts", []), child_id or key)
            return {
                "kind": kind,
                "target": entry,
                "object": None,
                "index": idx,
                "page": None,
                "page_index": -1,
            }
        target_page = page
        page_idx = int(getattr(work, "active_page_index", -1)) if work is not None else -1
        if page_id and work is not None:
            page_idx, target_page = _find_by_id(work.pages, page_id)
        if target_page is None:
            return None
        idx, entry = _find_by_id(target_page.texts, child_id or key)
        return {
            "kind": kind,
            "target": entry,
            "object": None,
            "index": idx,
            "page": target_page,
            "page_index": page_idx,
        }
    if kind == "effect":
        obj, target = _find_effect_layer_by_key(key)
        return {
            "kind": kind,
            "target": target,
            "object": obj,
            "stable_id": key,
            "index": -1,
        }
    return None


def _selection_attr_name(target) -> str:
    """エントリの選択フラグ属性名を返す。GP layer は native ``select`` を使う."""
    if target is None:
        return ""
    if hasattr(target, "selected"):
        return "selected"
    if hasattr(target, "select"):
        return "select"
    return ""


def is_item_selected(context, item) -> bool:
    """``item`` がマルチセレクト集合に含まれるかを返す。

    アクティブ行 (``bmanga_active_layer_stack_index``) も「選択中」として扱う。
    """
    scene = getattr(context, "scene", None)
    if scene is None or item is None:
        return False
    stack = getattr(scene, "bmanga_layer_stack", None)
    if stack is not None:
        idx = int(getattr(scene, "bmanga_active_layer_stack_index", -1))
        if 0 <= idx < len(stack):
            if stack_item_uid(stack[idx]) == stack_item_uid(item):
                return True
    resolved = resolve_stack_item(context, item)
    if str(getattr(item, "kind", "") or "") in {"gp", "effect"}:
        obj = _layer_item_selection_object(item, resolved)
        try:
            return bool(obj and obj.select_get())
        except Exception:  # noqa: BLE001
            return False
    target = resolved.get("target") if resolved is not None else None
    attr = _selection_attr_name(target)
    if not attr:
        return False
    try:
        return bool(getattr(target, attr))
    except Exception:  # noqa: BLE001
        return False


def _layer_item_selection_object(item, resolved):
    obj = resolved.get("object") if resolved is not None else None
    if obj is None or str(getattr(item, "kind", "") or "") != "effect":
        return obj
    try:
        from . import effect_line_object

        return effect_line_object.find_effect_display_object(obj) or obj
    except Exception:  # noqa: BLE001
        return obj


def set_item_selected(context, item, value: bool) -> bool:
    """``item`` 配下の実エントリにマルチセレクトフラグを書き込む.

    GP layer は native ``select`` を使い、その他のエントリは独自 ``selected``
    プロパティを使う。balloon_group のような仮想行は対象外で False を返す.
    """
    if item is None:
        return False
    resolved = resolve_stack_item(context, item)
    if str(getattr(item, "kind", "") or "") in {"gp", "effect"}:
        obj = _layer_item_selection_object(item, resolved)
        if obj is None:
            return False
        try:
            obj.select_set(bool(value))
            return True
        except Exception:  # noqa: BLE001
            return False
    target = resolved.get("target") if resolved is not None else None
    attr = _selection_attr_name(target)
    if not attr:
        return False
    try:
        setattr(target, attr, bool(value))
        return True
    except Exception:  # noqa: BLE001
        return False


def clear_all_selection(context) -> int:
    """スタック全行のマルチセレクトフラグをクリアする (アクティブ行は影響なし)."""
    scene = getattr(context, "scene", None)
    if scene is None:
        return 0
    stack = getattr(scene, "bmanga_layer_stack", None)
    if stack is None:
        return 0
    cleared = 0
    for item in stack:
        if set_item_selected(context, item, False):
            cleared += 1
    return cleared


def active_stack_item(context):
    # 「アクティブ項目を読む」だけの関数。以前はここで毎回 sync_layer_stack を
    # 呼んでいたが、それはレイヤー一覧を作り直して Scene に書き込む副作用がある。
    # この関数はパネルの draw やツール/ハンドラから高頻度で呼ばれるため、毎回
    # 書き込むと depsgraph 更新 → ビューポート再描画 → また呼ばれる、の連鎖で
    # 「B-MANGA パネルを開いている間ずっと細線が点滅する」再描画ループ(実測
    # 約15回/秒)になっていた。読み取りでは書き込まず、既存の一覧をそのまま見る。
    # 一覧はオペレータの変更後同期・パネルの draw 維持処理で最新化される。
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    stack = getattr(scene, "bmanga_layer_stack", None)
    if stack is None:
        return None
    idx = int(getattr(scene, "bmanga_active_layer_stack_index", -1))
    if 0 <= idx < len(stack):
        return stack[idx]
    return None


def _set_active_object(context, obj) -> None:
    if obj is None or context.view_layer is None:
        return
    try:
        context.view_layer.objects.active = obj
        obj.select_set(True)
    except Exception:  # noqa: BLE001
        pass


def _leave_grease_pencil_draw_modes(context) -> None:
    view_layer = getattr(context, "view_layer", None)
    obj = getattr(view_layer, "objects", None)
    active = getattr(obj, "active", None) if obj is not None else None
    if active is None or getattr(active, "type", "") != "GREASEPENCIL":
        return
    if getattr(active, "mode", "") not in {"PAINT_GREASE_PENCIL", "EDIT"}:
        return
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:  # noqa: BLE001
        pass


def _object_selection_key_for_stack_item(item, resolved) -> str:
    if item is None or resolved is None:
        return ""
    target = resolved.get("target")
    if target is None:
        return ""
    kind = str(getattr(item, "kind", "") or "")
    if kind == PAGE_KIND:
        return object_selection.page_key(target)
    if kind == COMA_KIND:
        return object_selection.coma_key(resolved.get("page"), target)
    if kind == "balloon":
        return object_selection.balloon_key(resolved.get("page"), target)
    if kind == "text":
        return object_selection.text_key(resolved.get("page"), target)
    if kind == "image":
        return object_selection.image_key(target)
    if kind == "image_path":
        return object_selection.image_path_key(target)
    if kind == "raster":
        return object_selection.raster_key(target)
    if kind == "fill":
        return object_selection.fill_key(target)
    if kind == "gp":
        return object_selection.gp_key(resolved.get("object"))
    if kind == "effect":
        return object_selection.effect_key(resolved.get("object"))
    return ""


def _sync_object_selection_for_stack_item(context, item, resolved) -> None:
    key = _object_selection_key_for_stack_item(item, resolved)
    if key:
        object_selection.select_key(context, key, mode="single")
    else:
        object_selection.clear(context)
    _sync_native_selection(context)


def sync_object_selection_from_stack_selection(context, stack=None) -> None:
    if stack is None:
        stack = sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        object_selection.clear(context)
        _sync_native_selection(context)
        return
    keys: list[str] = []
    for item in stack:
        if not is_item_selected(context, item):
            continue
        resolved = resolve_stack_item(context, item)
        key = _object_selection_key_for_stack_item(item, resolved)
        if key and key not in keys:
            keys.append(key)
    object_selection.set_keys(context, keys)
    _sync_native_selection(context)


def _select_stack_item_after_move(context, moved_uid: str) -> bool:
    stack = sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        object_selection.clear(context)
        _sync_native_selection(context)
        return False
    for index, item in enumerate(stack):
        if stack_item_uid(item) != moved_uid:
            continue
        clear_all_selection(context)
        if select_stack_index(context, index, sync_object_selection=True):
            return True
        set_active_stack_index_silently(context, index)
        object_selection.clear(context)
        _sync_native_selection(context)
        sync_visible_layer_stack(context, stack=stack)
        tag_view3d_redraw(context)
        return False
    object_selection.clear(context)
    _sync_native_selection(context)
    return False


def _sync_native_selection(context) -> None:
    try:
        from ..operators import object_tool_selection

        object_tool_selection.sync_outliner_selection_for_keys(
            context, object_selection.get_keys(context)
        )
    except Exception:  # noqa: BLE001
        pass


def select_stack_index(context, index: int, *, sync_object_selection: bool = True) -> bool:
    stack = sync_layer_stack(context, preserve_active_index=True)
    if stack is None or not (0 <= index < len(stack)):
        return False
    context.scene.bmanga_active_layer_stack_index = index
    item = stack[index]
    resolved = resolve_stack_item(context, item)
    if resolved is None or resolved.get("target") is None:
        return False

    kind = item.kind
    scene = context.scene
    page = get_active_page(context)
    if kind != "gp":
        _leave_grease_pencil_draw_modes(context)
    if kind == PAGE_KIND:
        work = get_work(context)
        idx = int(resolved.get("index", -1))
        if work is None or not (0 <= idx < len(work.pages)):
            return False
        work.active_page_index = idx
        try:
            from ..core.mode import MODE_PAGE, set_mode

            set_mode(MODE_PAGE, context)
            scene.bmanga_overview_mode = True
            scene.bmanga_current_coma_id = ""
            scene.bmanga_current_coma_page_id = ""
        except Exception:  # noqa: BLE001
            pass
        scene.bmanga_active_layer_kind = PAGE_KIND
        edge_selection.clear_selection(context)
    elif kind == COMA_KIND:
        work = get_work(context)
        page_idx = int(resolved.get("page_index", -1))
        coma_idx = int(resolved.get("index", -1))
        target_page = resolved.get("page")
        if target_page is None:
            scene.bmanga_active_layer_kind = COMA_KIND
            edge_selection.clear_selection(context)
            target = resolved.get("target")
            if target is not None and hasattr(target, "selected"):
                try:
                    target.selected = True
                except Exception:  # noqa: BLE001
                    pass
            if sync_object_selection:
                _sync_object_selection_for_stack_item(context, item, resolved)
            sync_visible_layer_stack(context, stack=stack)
            tag_view3d_redraw(context)
            return True
        if (
            work is None
            or target_page is None
            or not (0 <= page_idx < len(work.pages))
            or not (0 <= coma_idx < len(target_page.comas))
        ):
            return False
        work.active_page_index = page_idx
        target_page.active_coma_index = coma_idx
        try:
            from ..core.mode import MODE_PAGE, set_mode

            set_mode(MODE_PAGE, context)
            scene.bmanga_overview_mode = True
        except Exception:  # noqa: BLE001
            pass
        scene.bmanga_active_layer_kind = COMA_KIND
        edge_selection.set_selection(
            context,
            "border",
            page_index=page_idx,
            coma_index=coma_idx,
        )
    elif kind == COMA_PREVIEW_KIND:
        work = get_work(context)
        page_idx = int(resolved.get("page_index", -1))
        coma_idx = int(resolved.get("index", -1))
        target_page = resolved.get("page")
        if work is not None and target_page is not None and 0 <= page_idx < len(work.pages):
            work.active_page_index = page_idx
            if 0 <= coma_idx < len(target_page.comas):
                target_page.active_coma_index = coma_idx
        _set_active_object(context, resolved.get("object"))
        scene.bmanga_active_layer_kind = COMA_KIND
        edge_selection.clear_selection(context)
    elif kind == "gp":
        obj = resolved.get("object")
        layer = resolved.get("target")
        _set_active_object(context, obj)
        try:
            obj.data.layers.active = layer
            gp_utils.ensure_active_frame(layer)
            gp_utils.ensure_layer_material(obj, layer, activate=True, assign_existing=True)
        except Exception:  # noqa: BLE001
            _logger.exception("select gp layer failed")
        scene.bmanga_active_layer_kind = "gp"
        edge_selection.clear_selection(context)
    elif kind == LAYER_FOLDER_KIND:
        folder_key = str(getattr(item, "key", "") or "")
        if hasattr(scene, "bmanga_active_layer_folder_key"):
            scene.bmanga_active_layer_folder_key = folder_key
        scene.bmanga_active_layer_kind = LAYER_FOLDER_KIND
        work = get_work(context)
        semantic_parent = layer_folder_utils.semantic_parent_key_for_folder(work, folder_key)
        if work is not None and semantic_parent and semantic_parent != OUTSIDE_STACK_KEY:
            page_key, _child = split_child_key(semantic_parent)
            idx, _page = _find_by_id(getattr(work, "pages", []), page_key)
            if 0 <= idx < len(work.pages):
                work.active_page_index = idx
        edge_selection.clear_selection(context)
    elif kind == "image":
        scene.bmanga_active_image_layer_index = int(resolved.get("index", -1))
        scene.bmanga_active_layer_kind = "image"
        edge_selection.clear_selection(context)
    elif kind == "image_path":
        scene.bmanga_active_image_path_layer_index = int(resolved.get("index", -1))
        scene.bmanga_active_layer_kind = "image_path"
        edge_selection.clear_selection(context)
    elif kind == "raster":
        page_idx = int(resolved.get("page_index", -1))
        work = get_work(context)
        if work is not None and 0 <= page_idx < len(work.pages):
            work.active_page_index = page_idx
        scene.bmanga_active_raster_layer_index = int(resolved.get("index", -1))
        scene.bmanga_active_layer_kind = "raster"
        edge_selection.clear_selection(context)
    elif kind == "fill":
        scene.bmanga_active_fill_layer_index = int(resolved.get("index", -1))
        scene.bmanga_active_layer_kind = "fill"
        edge_selection.clear_selection(context)
    elif kind == "balloon_group":
        target_page = resolved.get("page") or page
        if target_page is None:
            return False
        work = get_work(context)
        page_idx = int(resolved.get("page_index", -1))
        if work is not None and 0 <= page_idx < len(work.pages):
            work.active_page_index = page_idx
        group_id = str(resolved.get("group_id", "") or "")
        first_selected = -1
        for i, balloon in enumerate(getattr(target_page, "balloons", [])):
            selected = str(getattr(balloon, "merge_group_id", "") or "") == group_id
            try:
                balloon.selected = selected
            except Exception:  # noqa: BLE001
                pass
            if selected and first_selected < 0:
                first_selected = i
        if first_selected >= 0:
            target_page.active_balloon_index = first_selected
        scene.bmanga_active_layer_kind = "balloon"
        edge_selection.clear_selection(context)
    elif kind == "balloon":
        target_page = resolved.get("page") or page
        if target_page is None:
            target = resolved.get("target")
            if target is None:
                return False
            try:
                target.selected = True
            except Exception:  # noqa: BLE001
                pass
            scene.bmanga_active_layer_kind = "balloon"
            edge_selection.clear_selection(context)
            if sync_object_selection:
                _sync_object_selection_for_stack_item(context, item, resolved)
            sync_visible_layer_stack(context, stack=stack)
            tag_view3d_redraw(context)
            return True
        work = get_work(context)
        page_idx = int(resolved.get("page_index", -1))
        if work is not None and 0 <= page_idx < len(work.pages):
            work.active_page_index = page_idx
        target_page.active_balloon_index = int(resolved.get("index", -1))
        for i, balloon in enumerate(getattr(target_page, "balloons", [])):
            try:
                balloon.selected = i == target_page.active_balloon_index
            except Exception:  # noqa: BLE001
                pass
        scene.bmanga_active_layer_kind = "balloon"
        edge_selection.clear_selection(context)
    elif kind == "text":
        target_page = resolved.get("page") or page
        if target_page is None:
            target = resolved.get("target")
            if target is None:
                return False
            try:
                target.selected = True
            except Exception:  # noqa: BLE001
                pass
            scene.bmanga_active_layer_kind = "text"
            edge_selection.clear_selection(context)
            if sync_object_selection:
                _sync_object_selection_for_stack_item(context, item, resolved)
            sync_visible_layer_stack(context, stack=stack)
            tag_view3d_redraw(context)
            return True
        work = get_work(context)
        page_idx = int(resolved.get("page_index", -1))
        if work is not None and 0 <= page_idx < len(work.pages):
            work.active_page_index = page_idx
        target_page.active_text_index = int(resolved.get("index", -1))
        scene.bmanga_active_layer_kind = "text"
        edge_selection.clear_selection(context)
    elif kind == "effect":
        obj = resolved.get("object")
        layer = resolved.get("target")
        try:
            from ..operators import effect_line_op

            if not effect_line_op._set_active_effect_layer(context, obj, layer):
                return False
        except Exception:  # noqa: BLE001
            _logger.exception("effect layer params restore failed")
            return False
        active_obj = obj
        try:
            from . import effect_line_object as _elo

            display = _elo.find_effect_display_object(obj)
            if display is not None:
                active_obj = display
        except Exception:  # noqa: BLE001
            active_obj = obj
        _set_active_object(context, active_obj)
        edge_selection.clear_selection(context)
    try:
        from . import layer_links

        layer_links.expand_linked_selection(context, stack=stack, base_item=item)
    except Exception:  # noqa: BLE001
        _logger.exception("linked layer selection expansion failed")
    if sync_object_selection:
        try:
            _sync_object_selection_for_stack_item(context, item, resolved)
        except Exception:  # noqa: BLE001
            _logger.exception("object selection sync from layer stack failed")
    sync_visible_layer_stack(context, stack=stack)
    tag_view3d_redraw(context)
    return True


def move_stack_item(
    context,
    from_index: int,
    to_index: int | None = None,
    *,
    direction: str = "",
) -> bool:
    stack = sync_layer_stack(context, preserve_active_index=True)
    if stack is None or len(stack) == 0:
        return False
    if not (0 <= from_index < len(stack)):
        return False
    target_index = _target_index_for_stack_move(stack, from_index, to_index, direction)
    if target_index < 0 or from_index == target_index:
        return False
    moved_uid = stack_item_uid(stack[from_index])
    stack.move(from_index, target_index)
    moved_index = _find_stack_index_by_uid(stack, moved_uid)
    if moved_index >= 0:
        set_active_stack_index_silently(context, moved_index)
    apply_stack_order(context)
    _select_stack_item_after_move(context, moved_uid)
    remember_layer_stack_signature(context)
    return True


def _reorder_collection(coll, desired_back_to_front: list[str], key_fn) -> None:
    actual = [key_fn(entry) for entry in coll]
    desired = [key for key in desired_back_to_front if key in actual]
    desired.extend(key for key in actual if key not in desired)
    for target_index, key in enumerate(desired):
        current_index = next(
            (i for i, entry in enumerate(coll) if key_fn(entry) == key),
            -1,
        )
        if current_index >= 0 and current_index != target_index:
            coll.move(current_index, target_index)


def _restore_active_collection_index(owner, prop_name: str, coll, active_key: str) -> None:
    for i, entry in enumerate(coll):
        if getattr(entry, "id", "") == active_key:
            setattr(owner, prop_name, i)
            return
    setattr(owner, prop_name, 0 if len(coll) > 0 else -1)


def _restore_active_page_coma(work, active_page_key: str, active_coma_key: str) -> None:
    if work is None:
        return
    work.active_page_index = -1
    for i, page in enumerate(work.pages):
        if page_stack_key(page) == active_page_key:
            work.active_page_index = i
            break
    if work.active_page_index < 0 and len(work.pages) > 0:
        work.active_page_index = 0
    for page in work.pages:
        if int(getattr(page, "active_coma_index", -1)) >= len(page.comas):
            page.active_coma_index = len(page.comas) - 1 if len(page.comas) else -1
        if not active_coma_key:
            continue
        for j, panel in enumerate(page.comas):
            if coma_stack_key(page, panel) == active_coma_key:
                page.active_coma_index = j
                break


def _apply_page_coma_orders(context, stack) -> None:
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return
    try:
        from . import page_grid
    except Exception:  # noqa: BLE001
        page_grid = None
    active_page_key = ""
    active_coma_key = ""
    active_idx = int(getattr(work, "active_page_index", -1))
    if 0 <= active_idx < len(work.pages):
        active_page = work.pages[active_idx]
        active_page_key = page_stack_key(active_page)
        coma_idx = int(getattr(active_page, "active_coma_index", -1))
        if 0 <= coma_idx < len(active_page.comas):
            active_coma_key = coma_stack_key(active_page, active_page.comas[coma_idx])

    page_keys = [item.key for item in stack if item.kind == PAGE_KIND]
    _reorder_collection(work.pages, page_keys, page_stack_key)
    try:
        from . import page_range

        page_range.update_page_range_visibility(work)
    except Exception:  # noqa: BLE001
        _logger.exception("page range update after stack order failed")
    for page in work.pages:
        page_key = page_stack_key(page)
        coma_keys = [
            item.key
            for item in stack
            if item.kind == COMA_KIND and split_child_key(item.key)[0] == page_key
        ]
        _reorder_collection(page.comas, coma_keys, lambda panel: coma_stack_key(page, panel))
        count = len(page.comas)
        for i, panel in enumerate(page.comas):
            panel.z_order = count - i - 1
        page.coma_count = count

    _restore_active_page_coma(work, active_page_key, active_coma_key)
    try:
        if page_grid is None:
            return
        page_grid.apply_page_collection_transforms(context, work)
    except Exception:  # noqa: BLE001
        _logger.exception("apply page collection transforms after stack order failed")


def _apply_simple_collection_orders(context, stack) -> None:
    scene = context.scene
    image_layers = getattr(scene, "bmanga_image_layers", None)
    if image_layers is not None:
        active_key = ""
        idx = int(getattr(scene, "bmanga_active_image_layer_index", -1))
        if 0 <= idx < len(image_layers):
            active_key = getattr(image_layers[idx], "id", "")
        front = [item.key for item in stack if item.kind == "image"]
        _reorder_collection(image_layers, list(reversed(front)), lambda entry: entry.id)
        if active_key:
            _restore_active_collection_index(
                scene, "bmanga_active_image_layer_index", image_layers, active_key
            )

    image_path_layers = getattr(scene, "bmanga_image_path_layers", None)
    if image_path_layers is not None:
        active_key = ""
        idx = int(getattr(scene, "bmanga_active_image_path_layer_index", -1))
        if 0 <= idx < len(image_path_layers):
            active_key = getattr(image_path_layers[idx], "id", "")
        front = [item.key for item in stack if item.kind == "image_path"]
        _reorder_collection(image_path_layers, list(reversed(front)), lambda entry: entry.id)
        if active_key:
            _restore_active_collection_index(
                scene, "bmanga_active_image_path_layer_index", image_path_layers, active_key
            )

    raster_layers = getattr(scene, "bmanga_raster_layers", None)
    if raster_layers is not None:
        active_key = ""
        idx = int(getattr(scene, "bmanga_active_raster_layer_index", -1))
        if 0 <= idx < len(raster_layers):
            active_key = getattr(raster_layers[idx], "id", "")
        front = [item.key for item in stack if item.kind == "raster"]
        _reorder_collection(raster_layers, list(reversed(front)), lambda entry: entry.id)
        if active_key:
            _restore_active_collection_index(
                scene, "bmanga_active_raster_layer_index", raster_layers, active_key
            )

    fill_layers = getattr(scene, "bmanga_fill_layers", None)
    if fill_layers is not None:
        active_key = ""
        idx = int(getattr(scene, "bmanga_active_fill_layer_index", -1))
        if 0 <= idx < len(fill_layers):
            active_key = getattr(fill_layers[idx], "id", "")
        front = [item.key for item in stack if item.kind == "fill"]
        _reorder_collection(fill_layers, list(reversed(front)), lambda entry: entry.id)
        if active_key:
            _restore_active_collection_index(
                scene, "bmanga_active_fill_layer_index", fill_layers, active_key
            )

    work = get_work(context)
    if work is None:
        return

    layer_folders = getattr(work, "layer_folders", None)
    if layer_folders is not None:
        front = [item.key for item in stack if item.kind == LAYER_FOLDER_KIND]
        _reorder_collection(layer_folders, list(reversed(front)), lambda entry: entry.id)

    shared_balloons = getattr(work, "shared_balloons", None)
    if shared_balloons is not None:
        front = [
            split_child_key(item.key)[1]
            for item in stack
            if item.kind == "balloon"
            and split_child_key(item.key)[0] == OUTSIDE_STACK_KEY
        ]
        _reorder_collection(shared_balloons, list(reversed(front)), lambda entry: entry.id)

    shared_texts = getattr(work, "shared_texts", None)
    if shared_texts is not None:
        front = [
            split_child_key(item.key)[1]
            for item in stack
            if item.kind == "text"
            and split_child_key(item.key)[0] == OUTSIDE_STACK_KEY
        ]
        _reorder_collection(shared_texts, list(reversed(front)), lambda entry: entry.id)

    shared_comas = getattr(work, "shared_comas", None)
    if shared_comas is not None:
        front = [
            split_child_key(item.key)[1]
            for item in stack
            if item.kind == COMA_KIND
            and split_child_key(item.key)[0] == OUTSIDE_STACK_KEY
        ]
        _reorder_collection(
            shared_comas,
            list(reversed(front)),
            lambda entry: str(getattr(entry, "coma_id", "") or getattr(entry, "id", "")),
        )
        count = len(shared_comas)
        for i, panel in enumerate(shared_comas):
            panel.z_order = count - i - 1

    for page in work.pages:
        page_key = page_stack_key(page)
        active_balloon = ""
        if 0 <= page.active_balloon_index < len(page.balloons):
            active_balloon = page.balloons[page.active_balloon_index].id
        front = [
            split_child_key(item.key)[1]
            for item in stack
            if item.kind == "balloon" and split_child_key(item.key)[0] == page_key
        ]
        _reorder_collection(page.balloons, list(reversed(front)), lambda entry: entry.id)
        if active_balloon:
            _restore_active_collection_index(
                page, "active_balloon_index", page.balloons, active_balloon
            )

        active_text = ""
        if 0 <= page.active_text_index < len(page.texts):
            active_text = page.texts[page.active_text_index].id
        front = [
            split_child_key(item.key)[1]
            for item in stack
            if item.kind == "text" and split_child_key(item.key)[0] == page_key
        ]
        _reorder_collection(page.texts, list(reversed(front)), lambda entry: entry.id)
        if active_text:
            _restore_active_collection_index(page, "active_text_index", page.texts, active_text)


def _apply_layer_object_parenting(context, stack, work, kind: str) -> None:
    from . import layer_object_model

    for item in stack:
        if getattr(item, "kind", "") != kind:
            continue
        obj = layer_object_model.find_layer_object(
            kind,
            str(getattr(item, "key", "") or ""),
        )
        if obj is None:
            continue
        desired_parent_key = str(getattr(item, "parent_key", "") or "")
        ui_parent_key = desired_parent_key
        folder_key = ""
        if layer_folder_utils.is_folder_key(context, desired_parent_key):
            folder_key = desired_parent_key
            desired_parent_key = layer_folder_utils.semantic_parent_key_for_folder(
                work,
                folder_key,
            )
        if desired_parent_key == OUTSIDE_STACK_KEY:
            desired_parent_key = ""
        if desired_parent_key and not gp_parent.parent_key_exists(work, desired_parent_key):
            desired_parent_key = ""
            if ui_parent_key != OUTSIDE_STACK_KEY:
                ui_parent_key = ""
        layer_object_model.set_folder_id(obj, folder_key)
        _stamp_layer_object_parent(context, obj, desired_parent_key)
        if folder_key:
            item.parent_key = folder_key
        else:
            item.parent_key = ui_parent_key if ui_parent_key == OUTSIDE_STACK_KEY else desired_parent_key


def _apply_image_parenting(context, stack) -> None:
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bmanga_image_layers", None) if scene is not None else None
    if coll is None:
        return
    work = get_work(context)
    by_key = {
        str(getattr(item, "key", "") or ""): str(getattr(item, "parent_key", "") or "")
        for item in stack
        if getattr(item, "kind", "") == "image"
    }
    for entry in coll:
        key = str(getattr(entry, "id", "") or "")
        if key not in by_key:
            continue
        parent_key = by_key[key]
        try:
            if parent_key == OUTSIDE_STACK_KEY or not parent_key:
                entry.parent_kind = "none"
                entry.parent_key = ""
            elif gp_parent.parent_key_exists(work, parent_key):
                entry.parent_kind = "coma" if ":" in parent_key else "page"
                entry.parent_key = parent_key
        except Exception:  # noqa: BLE001
            pass


def _apply_image_path_parenting(context, stack) -> None:
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bmanga_image_path_layers", None) if scene is not None else None
    if coll is None:
        return
    work = get_work(context)
    by_key = {
        str(getattr(item, "key", "") or ""): str(getattr(item, "parent_key", "") or "")
        for item in stack
        if getattr(item, "kind", "") == "image_path"
    }
    for entry in coll:
        key = str(getattr(entry, "id", "") or "")
        if key not in by_key:
            continue
        parent_key = by_key[key]
        try:
            if parent_key == OUTSIDE_STACK_KEY or not parent_key:
                entry.parent_kind = "none"
                entry.parent_key = ""
            elif gp_parent.parent_key_exists(work, parent_key):
                entry.parent_kind = "coma" if ":" in parent_key else "page"
                entry.parent_key = parent_key
        except Exception:  # noqa: BLE001
            pass


def _apply_raster_parenting(context, stack) -> None:
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bmanga_raster_layers", None) if scene is not None else None
    if coll is None:
        return
    work = get_work(context)
    by_key = {
        str(getattr(item, "key", "") or ""): str(getattr(item, "parent_key", "") or "")
        for item in stack
        if getattr(item, "kind", "") == "raster"
    }
    for entry in coll:
        key = str(getattr(entry, "id", "") or "")
        if key not in by_key:
            continue
        parent_key = by_key[key]
        try:
            if parent_key == OUTSIDE_STACK_KEY or not parent_key:
                entry.scope = "master"
                entry.parent_kind = "none"
                entry.parent_key = ""
            elif gp_parent.parent_key_exists(work, parent_key):
                entry.scope = "page"
                entry.parent_kind = "coma" if ":" in parent_key else "page"
                entry.parent_key = parent_key
        except Exception:  # noqa: BLE001
            pass


def _apply_balloon_parenting(context, stack) -> None:
    work = get_work(context)
    if work is None:
        return
    for page in getattr(work, "pages", []):
        page_key = page_stack_key(page)
        by_key = {
            split_child_key(str(getattr(item, "key", "") or ""))[1]: str(getattr(item, "parent_key", "") or "")
            for item in stack
            if getattr(item, "kind", "") == "balloon"
            and split_child_key(str(getattr(item, "key", "") or ""))[0] == page_key
        }
        for entry in getattr(page, "balloons", []):
            key = str(getattr(entry, "id", "") or "")
            if key not in by_key:
                continue
            parent_key = by_key[key]
            existing_parent = str(getattr(entry, "parent_key", "") or "")
            fallback_panel = coma_containing_point(page, *entry_center(entry))
            fallback_parent = coma_stack_key(page, fallback_panel) if fallback_panel is not None else page_key
            if not existing_parent and parent_key == fallback_parent:
                continue
            if not parent_key or not gp_parent.parent_key_exists(work, parent_key):
                continue
            entry.parent_kind = "coma" if ":" in parent_key else "page"
            entry.parent_key = parent_key
            try:
                from . import balloon_curve_object

                balloon_curve_object.ensure_balloon_curve_object(
                    scene=context.scene,
                    entry=entry,
                    page=page,
                )
            except Exception:  # noqa: BLE001
                _logger.exception("apply balloon parenting real object sync failed")


def _apply_text_parenting(context, stack) -> None:
    work = get_work(context)
    if work is None:
        return
    for page in getattr(work, "pages", []):
        page_key = page_stack_key(page)
        by_key = {
            split_child_key(str(getattr(item, "key", "") or ""))[1]: str(getattr(item, "parent_key", "") or "")
            for item in stack
            if getattr(item, "kind", "") == "text"
            and split_child_key(str(getattr(item, "key", "") or ""))[0] == page_key
        }
        for entry in getattr(page, "texts", []):
            key = str(getattr(entry, "id", "") or "")
            if key not in by_key:
                continue
            parent_key = by_key[key]
            existing_parent = str(getattr(entry, "parent_key", "") or "")
            fallback_panel = coma_containing_point(page, *entry_center(entry))
            fallback_parent = coma_stack_key(page, fallback_panel) if fallback_panel is not None else page_key
            if not existing_parent and parent_key == fallback_parent:
                continue
            if not parent_key or not gp_parent.parent_key_exists(work, parent_key):
                continue
            entry.parent_kind = "coma" if ":" in parent_key else "page"
            entry.parent_key = parent_key


def apply_stack_order(context) -> None:
    stack = getattr(context.scene, "bmanga_layer_stack", None)
    if stack is None:
        return
    _apply_page_coma_orders(context, stack)
    _apply_simple_collection_orders(context, stack)
    _apply_layer_object_parenting(context, stack, get_work(context), "gp")
    _apply_layer_object_parenting(context, stack, get_work(context), "effect")
    _apply_image_parenting(context, stack)
    _apply_image_path_parenting(context, stack)
    _apply_raster_parenting(context, stack)
    _apply_balloon_parenting(context, stack)
    _apply_text_parenting(context, stack)
    _sync_real_objects_after_stack_order(context)
    tag_view3d_redraw(context)


def delete_stack_index(context, index: int) -> bool:
    stack = sync_layer_stack(context, preserve_active_index=True)
    if stack is None or not (0 <= index < len(stack)):
        return False
    item = stack[index]
    resolved = resolve_stack_item(context, item)
    if resolved is None or resolved.get("target") is None:
        stack.remove(index)
        return True
    kind = item.kind
    scene = context.scene
    page = get_active_page(context)

    if kind == OUTSIDE_KIND:
        return False
    if kind == PAGE_KIND:
        if not select_stack_index(context, index):
            return False
        try:
            return "FINISHED" in bpy.ops.bmanga.page_remove("EXEC_DEFAULT")
        except Exception:  # noqa: BLE001
            _logger.exception("delete page from layer stack failed")
            return False
    if kind == COMA_KIND:
        if resolved.get("page") is None:
            work = get_work(context)
            coll = getattr(work, "shared_comas", None) if work is not None else None
            idx = int(resolved.get("index", -1))
            if coll is None or not (0 <= idx < len(coll)):
                return False
            coll.remove(idx)
            sync_layer_stack(context)
            tag_view3d_redraw(context)
            return True
        if not select_stack_index(context, index):
            return False
        try:
            return "FINISHED" in bpy.ops.bmanga.coma_remove("EXEC_DEFAULT")
        except Exception:  # noqa: BLE001
            _logger.exception("delete panel from layer stack failed")
            return False
    if kind == "gp":
        obj = resolved.get("object")
        from . import layer_object_model

        if not layer_object_model.remove_layer_object(obj):
            return False
    elif kind == LAYER_FOLDER_KIND:
        work = get_work(context)
        if work is None or not layer_folder_utils.remove_folder_preserve_children(work, item.key):
            return False
        if hasattr(scene, "bmanga_active_layer_folder_key"):
            scene.bmanga_active_layer_folder_key = ""
    elif kind == "image":
        coll = getattr(scene, "bmanga_image_layers", None)
        idx = int(resolved.get("index", -1))
        if coll is None or not (0 <= idx < len(coll)):
            return False
        image_id = str(getattr(coll[idx], "id", "") or "")
        coll.remove(idx)
        try:
            from . import image_real_object

            image_real_object.remove_image_real_object(image_id)
        except Exception:  # noqa: BLE001
            _logger.exception("delete image real object from layer stack failed")
        scene.bmanga_active_image_layer_index = min(idx, len(coll) - 1) if len(coll) else -1
    elif kind == "image_path":
        coll = getattr(scene, "bmanga_image_path_layers", None)
        idx = int(resolved.get("index", -1))
        if coll is None or not (0 <= idx < len(coll)):
            return False
        image_path_id = str(getattr(coll[idx], "id", "") or "")
        coll.remove(idx)
        try:
            from . import image_path_object

            image_path_object.remove_image_path_object(image_path_id)
        except Exception:  # noqa: BLE001
            _logger.exception("delete image path object from layer stack failed")
        scene.bmanga_active_image_path_layer_index = min(idx, len(coll) - 1) if len(coll) else -1
    elif kind == "raster":
        idx = int(resolved.get("index", -1))
        try:
            from ..operators import raster_layer_op

            if not raster_layer_op.remove_raster_by_index(context, idx):
                return False
        except Exception:  # noqa: BLE001
            _logger.exception("delete raster from layer stack failed")
            return False
    elif kind == "fill":
        coll = getattr(scene, "bmanga_fill_layers", None)
        idx = int(resolved.get("index", -1))
        if coll is None or not (0 <= idx < len(coll)):
            return False
        fill_id = str(getattr(coll[idx], "id", "") or "")
        coll.remove(idx)
        try:
            from . import fill_real_object

            fill_real_object.remove_fill_real_object(fill_id)
        except Exception:  # noqa: BLE001
            _logger.exception("delete fill real object from layer stack failed")
        scene.bmanga_active_fill_layer_index = min(idx, len(coll) - 1) if len(coll) else -1
    elif kind == "balloon":
        idx = int(resolved.get("index", -1))
        target_page = resolved.get("page") or page
        if target_page is None:
            work = get_work(context)
            coll = getattr(work, "shared_balloons", None) if work is not None else None
            if coll is None or not (0 <= idx < len(coll)):
                return False
            bid = coll[idx].id
            for text in getattr(work, "shared_texts", []):
                if text.parent_balloon_id == bid:
                    text.parent_balloon_id = ""
            try:
                from . import balloon_curve_object

                balloon_curve_object.remove_balloon_objects_by_id(bid)
            except Exception:  # noqa: BLE001
                _logger.exception("delete shared balloon object from layer stack failed")
            coll.remove(idx)
        else:
            if not (0 <= idx < len(target_page.balloons)):
                return False
            bid = target_page.balloons[idx].id
            for text in target_page.texts:
                if text.parent_balloon_id == bid:
                    text.parent_balloon_id = ""
            try:
                from . import balloon_curve_object

                balloon_curve_object.remove_balloon_objects_by_id(bid)
            except Exception:  # noqa: BLE001
                _logger.exception("delete balloon object from layer stack failed")
            target_page.balloons.remove(idx)
            target_page.active_balloon_index = min(idx, len(target_page.balloons) - 1) if len(target_page.balloons) else -1
    elif kind == "text":
        idx = int(resolved.get("index", -1))
        target_page = resolved.get("page") or page
        if target_page is None:
            work = get_work(context)
            coll = getattr(work, "shared_texts", None) if work is not None else None
            if coll is None or not (0 <= idx < len(coll)):
                return False
            coll.remove(idx)
        else:
            if not (0 <= idx < len(target_page.texts)):
                return False
            text_id = str(getattr(target_page.texts[idx], "id", "") or "")
            page_id = str(getattr(target_page, "id", "") or "")
            target_page.texts.remove(idx)
            try:
                from . import text_real_object

                text_real_object.remove_text_real_object(page_id, text_id)
            except Exception:  # noqa: BLE001
                _logger.exception("delete text real object from layer stack failed")
            target_page.active_text_index = min(idx, len(target_page.texts) - 1) if len(target_page.texts) else -1
    elif kind == "effect":
        obj = resolved.get("object")
        target = resolved["target"]
        try:
            from ..operators import effect_line_op

            effect_line_op._delete_effect_layer(context, obj, target)
        except Exception:  # noqa: BLE001
            _logger.exception("delete effect from layer stack failed")
            return False
        scene.bmanga_active_effect_layer_name = ""
    else:
        return False

    sync_layer_stack(context)
    idx = min(index, len(stack) - 1) if len(stack) else -1
    scene.bmanga_active_layer_stack_index = idx
    if idx >= 0:
        select_stack_index(context, idx)
    elif hasattr(scene, "bmanga_active_layer_kind"):
        scene.bmanga_active_layer_kind = "gp"
    tag_view3d_redraw(context)
    return True


def tag_view3d_redraw(context) -> None:
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def schedule_layer_stack_sync(
    *,
    retries: int = 6,
    interval: float = 0.1,
    apply_order: bool = False,
    moved_uid: str = "",
) -> None:
    """ファイルロード直後の UI 再構築をまたいでレイヤースタックを同期する."""
    global _sync_order_moved_uid, _sync_scheduled, _sync_should_apply_order

    _sync_should_apply_order = _sync_should_apply_order or bool(apply_order)
    if moved_uid:
        _sync_order_moved_uid = moved_uid
    if _sync_scheduled:
        return
    _sync_scheduled = True
    state = {"left": max(1, int(retries))}

    def _tick():
        global _sync_order_moved_uid, _sync_scheduled, _sync_should_apply_order

        converged = False
        try:
            if _sync_should_apply_order:
                apply_stack_order(bpy.context)
            scene = getattr(bpy.context, "scene", None)
            before_sig = _stack_signature(scene) if scene is not None else ()
            sync_layer_stack(bpy.context)
            after_sig = _stack_signature(scene) if scene is not None else None
            _remember_stack_signature(bpy.context)
            if _sync_should_apply_order or after_sig != before_sig:
                _sync_real_objects_after_stack_order(bpy.context)
            # 一覧が前回 tick から変化しなければ収束とみなし、残りの tick と
            # 再描画を打ち切る (読込直後の連続再構築・連続再描画を抑える)
            converged = (
                not _sync_should_apply_order
                and after_sig is not None
                and after_sig == before_sig
                and state["left"] < max(1, int(retries))
            )
            if not converged:
                tag_view3d_redraw(bpy.context)
        except Exception:  # noqa: BLE001
            _logger.exception("scheduled layer stack sync failed")
        state["left"] -= 1
        if state["left"] > 0 and not converged:
            return interval
        _sync_scheduled = False
        _sync_should_apply_order = False
        _sync_order_moved_uid = ""
        return None

    try:
        bpy.app.timers.register(_tick, first_interval=interval)
    except Exception:  # noqa: BLE001
        _logger.exception("schedule layer stack sync failed")
