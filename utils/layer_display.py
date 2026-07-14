"""レイヤー一覧・詳細設定で使う表示名・アイコンの共通ヘルパー.

- 種別 (kind) → アイコン名の対応表 (旧 ``panels.gpencil_panel._kind_icon``)
- テキストレイヤーの表示名の切り詰め (レイヤー一覧の行が長文で崩れないように)
- 詳細設定ダイアログの「リンク中のレイヤー」box 描画 (uid 集合 → 行一覧)

データ (title/body 等) 自体は一切変更しない。あくまで表示用の文字列を作るだけ。
"""

from __future__ import annotations

TEXT_LABEL_TRUNCATE_LIMIT = 7

_KIND_ICONS = {
    "page": "FILE_BLANK",
    "outside_group": "FILE_FOLDER",
    "coma": "MOD_WIREFRAME",
    "coma_preview": "IMAGE_DATA",
    "gp": "OUTLINER_OB_GREASEPENCIL",
    "layer_folder": "FILE_FOLDER",
    "image": "IMAGE_DATA",
    "raster": "BRUSH_DATA",
    "fill": "NODE_TEXTURE",
    "balloon_group": "FILE_FOLDER",
    "balloon": "MOD_FLUID",
    "text": "FONT_DATA",
    "effect": "STROKE",
}


def kind_icon(kind: str) -> str:
    """種別文字列に対応するアイコン名を返す (未知の種別は RENDERLAYERS)."""
    return _KIND_ICONS.get(str(kind or ""), "RENDERLAYERS")


def truncate_label(text: str, limit: int = TEXT_LABEL_TRUNCATE_LIMIT) -> str:
    """先頭 ``limit`` 文字までに切り詰め、超過分は「…」1文字に置き換える.

    ちょうど ``limit`` 文字なら省略記号は付けない (仕様: 8文字以上で切り詰め)。
    文字数は Python の str 単位 (コードポイント) で数える。
    """
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def text_entry_raw_name(entry, fallback: str = "") -> str:
    """テキストエントリの表示名 (切り詰め前)。title 優先、無ければ本文、無ければ fallback."""
    if entry is None:
        return str(fallback or "")
    title = getattr(entry, "title", "") or ""
    if title:
        return str(title)
    body = getattr(entry, "body", "") or ""
    if body:
        return str(body)
    return str(fallback or "")


def text_entry_display_name(
    entry, fallback: str = "", *, limit: int = TEXT_LABEL_TRUNCATE_LIMIT
) -> str:
    """レイヤー一覧・詳細設定で使うテキストの表示名 (先頭 limit 文字に切り詰め済み)."""
    return truncate_label(text_entry_raw_name(entry, fallback), limit)


def stack_row_display_name(item, target=None) -> str:
    """レイヤー一覧の行と同じ規則で表示名を返す (テキストのみ7文字切り詰め).

    ``item.label`` はスタック同期時に title/body/既定名から解決済みの表示名
    なので、テキスト以外はそのまま使い、テキストだけ切り詰めを追加で適用する。
    """
    kind = str(getattr(item, "kind", "") or "")
    label = str(
        getattr(item, "label", "")
        or getattr(item, "name", "")
        or getattr(item, "key", "")
        or ""
    )
    if kind == "text":
        return text_entry_display_name(target, label)
    return label or "レイヤー"


def linked_layer_rows(context, partner_uids) -> list[tuple[str, str]]:
    """uid 集合を (kind, 表示名) の一覧へ解決する.

    詳細設定ダイアログの「リンク中のレイヤー」表示用。まず現在の
    ``scene.bmanga_layer_stack`` から一致する行を探し、見つからない uid は
    ``page.balloons`` / ``page.texts`` を直接探すフォールバックで解決する
    (フィルタ等で一覧に出ていない行も名前だけは表示できるようにするため)。
    """
    uids = set(partner_uids or ())
    if not uids:
        return []
    from . import layer_stack as layer_stack_utils

    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
    remaining = set(uids)
    rows: list[tuple[str, str]] = []
    if stack is not None:
        for row_item in stack:
            uid = layer_stack_utils.stack_item_uid(row_item)
            if uid not in remaining:
                continue
            resolved = layer_stack_utils.resolve_stack_item(context, row_item)
            row_target = resolved.get("target") if resolved is not None else None
            if row_target is None:
                continue
            kind = str(getattr(row_item, "kind", "") or "")
            rows.append((kind, stack_row_display_name(row_item, row_target)))
            remaining.discard(uid)
    if remaining:
        rows.extend(_fallback_rows_from_pages(context, remaining))
    return rows


def _fallback_rows_from_pages(context, uids: set[str]) -> list[tuple[str, str]]:
    """スタックに見つからなかった balloon/text uid を page から直接引く."""
    from ..core.work import get_work
    from . import layer_hierarchy

    work = get_work(context)
    if work is None:
        return []
    rows: list[tuple[str, str]] = []
    for uid in uids:
        kind, sep, key = str(uid or "").partition(":")
        if not sep or kind not in {"balloon", "text"}:
            continue
        page_key, _sep2, entry_id = key.partition(":")
        page = None
        for candidate in getattr(work, "pages", []) or []:
            if layer_hierarchy.page_stack_key(candidate) == page_key:
                page = candidate
                break
        if page is None:
            continue
        collection = getattr(page, "balloons" if kind == "balloon" else "texts", []) or []
        for entry in collection:
            if str(getattr(entry, "id", "") or "") != entry_id:
                continue
            if kind == "text":
                name = text_entry_display_name(entry, entry_id)
            else:
                name = str(getattr(entry, "title", "") or "") or entry_id
            rows.append((kind, name or "レイヤー"))
            break
    return rows


def draw_linked_layers_box(layout, context, partner_uids) -> None:
    """「リンク中のレイヤー」box を描画する (相手が無ければ何も描画しない)."""
    rows = linked_layer_rows(context, partner_uids)
    if not rows:
        return
    box = layout.box()
    box.label(text="リンク中のレイヤー", icon="LINKED")
    for kind, name in rows:
        box.label(text=name, icon=kind_icon(kind))
