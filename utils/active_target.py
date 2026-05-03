"""アクティブな page / coma の解決ヘルパ.

各種レイヤー作成 op で「ユーザーが今選択している階層」を統一して取得する
ためのユーティリティ。優先順位:

    1. ``scene.bname_current_coma_id`` (cNN.blend 編集中)
    2. ``BNamePageEntry.active_coma_index`` (page browser でコマ選択)
    3. それ以外は page 直下

新規レイヤー作成時に Outliner Collection の親を決定する基準として使う。
"""

from __future__ import annotations

from typing import Optional

import bpy


def focus_active_coma(scene, work, page_index: int, coma_index: int) -> None:
    """ビューポート上のコマ選択 (枠線選択ツール / オブジェクトツール等) を
    PropertyGroup 上の active 階層にも反映する.

    新規レイヤー追加 (``resolve_active_target``) は ``page.active_coma_index``
    と ``scene.bname_current_coma_id`` を参照するため、 viewport クリック
    だけで「このコマを active にする」状態にしないとレイヤー追加時に
    ページ直下にしか入らない。 本関数はそのギャップを埋める。

    既に同値ならスキップ (update callback の連鎖を避ける)。
    """
    if work is None:
        return
    pages = list(getattr(work, "pages", []) or [])
    if not (0 <= page_index < len(pages)):
        return
    try:
        if int(getattr(work, "active_page_index", -1)) != page_index:
            work.active_page_index = page_index
    except Exception:  # noqa: BLE001
        pass
    page = pages[page_index]
    comas = list(getattr(page, "comas", []) or [])
    if not (0 <= coma_index < len(comas)):
        return
    try:
        if int(getattr(page, "active_coma_index", -1)) != coma_index:
            page.active_coma_index = coma_index
    except Exception:  # noqa: BLE001
        pass
    coma_id = str(getattr(comas[coma_index], "id", "") or "")
    if scene is None or not coma_id:
        return
    try:
        if str(getattr(scene, "bname_current_coma_id", "") or "") != coma_id:
            scene.bname_current_coma_id = coma_id
    except Exception:  # noqa: BLE001
        pass
    if hasattr(scene, "bname_active_layer_kind"):
        try:
            if str(getattr(scene, "bname_active_layer_kind", "") or "") != "coma":
                scene.bname_active_layer_kind = "coma"
        except Exception:  # noqa: BLE001
            pass
    # Outliner の active_layer_collection も即同期 (depsgraph_update_post を
    # 待たずに viewport クリック直後にハイライトを更新する)。
    try:
        from . import active_collection_sync as _acs

        page_id = str(getattr(page, "id", "") or "")
        if page_id and coma_id:
            _acs.request_active_coma(bpy.context, page_id, coma_id)
    except Exception:  # noqa: BLE001
        pass


def focus_active_page(scene, work, page_index: int) -> None:
    """ページ直下を active 階層にする (コマ未選択状態).

    ``focus_active_coma`` のページ版。 Outliner も page Collection に同期する。
    """
    if work is None:
        return
    pages = list(getattr(work, "pages", []) or [])
    if not (0 <= page_index < len(pages)):
        return
    try:
        if int(getattr(work, "active_page_index", -1)) != page_index:
            work.active_page_index = page_index
    except Exception:  # noqa: BLE001
        pass
    page = pages[page_index]
    try:
        if int(getattr(page, "active_coma_index", -1)) != -1:
            page.active_coma_index = -1
    except Exception:  # noqa: BLE001
        pass
    if scene is not None:
        try:
            if str(getattr(scene, "bname_current_coma_id", "") or "") != "":
                scene.bname_current_coma_id = ""
        except Exception:  # noqa: BLE001
            pass
        if hasattr(scene, "bname_active_layer_kind"):
            try:
                if str(getattr(scene, "bname_active_layer_kind", "") or "") != "page":
                    scene.bname_active_layer_kind = "page"
            except Exception:  # noqa: BLE001
                pass
    try:
        from . import active_collection_sync as _acs

        page_id = str(getattr(page, "id", "") or "")
        if page_id:
            _acs.request_active_coma(bpy.context, page_id, "")
    except Exception:  # noqa: BLE001
        pass


def focus_creation_target(
    context, work, page, parent_kind: str, parent_key: str
) -> None:
    """新規レイヤー作成 op の作成パスから呼ぶ汎用 active 切替.

    parent_kind / parent_key (例: "coma" + "p0001:c02" or "page" + "p0001")
    を解析して、 該当ページ/コマを active 階層にし、 Outliner Collection も
    同期する。 viewport クリック位置に基づいて作成された entry の Object が
    対応する Collection に link されるよう、 既存の active を上書きする。
    """
    if work is None or page is None:
        return
    pages = list(getattr(work, "pages", []) or [])
    page_index = -1
    for i, p in enumerate(pages):
        if p is page:
            page_index = i
            break
    if page_index < 0:
        return
    coma_id = ""
    if str(parent_kind) == "coma" and ":" in str(parent_key or ""):
        coma_id = str(parent_key).split(":", 1)[1]
    if coma_id:
        comas = list(getattr(page, "comas", []) or [])
        coma_index = -1
        for j, c in enumerate(comas):
            if str(getattr(c, "id", "") or "") == coma_id:
                coma_index = j
                break
        if coma_index >= 0:
            scene = getattr(context, "scene", None)
            focus_active_coma(scene, work, page_index, coma_index)
            return
    scene = getattr(context, "scene", None)
    focus_active_page(scene, work, page_index)


def resolve_active_target(
    context, *, prefer_page=None
) -> tuple[str, str, Optional[object]]:
    """ユーザーが現在選択している階層 (page or coma) を解決.

    Returns:
        ``(parent_kind, parent_key, page_entry)``:
            - parent_kind: ``"page"`` or ``"coma"``
            - parent_key: ``"<page_id>"`` または ``"<page_id>:<coma_id>"``
            - page_entry: 解決したページの ``BNamePageEntry`` (取得不可なら None)
    """
    scene = getattr(context, "scene", None)
    if scene is None:
        return ("page", "", None)
    work = getattr(scene, "bname_work", None)
    if work is None or not getattr(work, "loaded", False):
        return ("page", "", None)
    pages = getattr(work, "pages", None)
    if not pages:
        return ("page", "", None)

    # アクティブページを解決
    page = prefer_page
    if page is None:
        idx = int(getattr(work, "active_page_index", 0))
        if 0 <= idx < len(pages):
            page = pages[idx]
    if page is None:
        return ("page", "", None)
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return ("page", "", None)

    comas = getattr(page, "comas", None)
    if not comas:
        return ("page", page_id, page)

    # 1. scene.bname_current_coma_id 最優先 (cNN.blend 編集中)
    current_coma_id = str(getattr(scene, "bname_current_coma_id", "") or "")
    if current_coma_id:
        for coma in comas:
            if str(getattr(coma, "id", "") or "") == current_coma_id:
                return ("coma", f"{page_id}:{current_coma_id}", page)

    # 2. page.active_coma_index
    coma_idx = int(getattr(page, "active_coma_index", -1))
    if 0 <= coma_idx < len(comas):
        coma = comas[coma_idx]
        coma_id = str(getattr(coma, "id", "") or "")
        if coma_id:
            return ("coma", f"{page_id}:{coma_id}", page)

    # 3. ページ直下
    return ("page", page_id, page)
