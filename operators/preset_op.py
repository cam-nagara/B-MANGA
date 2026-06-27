"""用紙プリセット適用・保存・削除 Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..io import border_presets, image_path_presets, page_io, presets, work_io
from ..io import coma_io
from ..utils import log
from . import coma_modal_state

_logger = log.get_logger(__name__)


# Blender の EnumProperty callback は返した文字列への参照を保持しないため
# GC でクラッシュすることがある (公式既知の不具合)。モジュールレベルで
# キャッシュを保持して回避する。
_PRESET_ENUM_CACHE: list[tuple[str, str, str]] = []
_SUPPRESS_SELECTOR_UPDATE = False
_SUPPRESS_TOOL_PRESET_REMEMBER = False


def _remember_tool_preset(context, attr: str, value: str) -> None:
    if _SUPPRESS_TOOL_PRESET_REMEMBER:
        return
    try:
        from .. import preferences as addon_preferences

        prefs = addon_preferences.get_preferences(context)
        if prefs is None or not hasattr(prefs, attr):
            return
        setattr(prefs, attr, str(value or ""))
        addon_preferences.request_user_preferences_save()
    except Exception:  # noqa: BLE001
        _logger.debug("tool preset selection remember failed", exc_info=True)


def _restore_selector_if_valid(context, prop_name: str, value: str, items_callback) -> bool:
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, prop_name):
        return False
    value = str(value or "")
    if not value:
        return False
    try:
        valid = {str(item[0]) for item in items_callback(None, context)}
    except Exception:  # noqa: BLE001
        _logger.debug("tool preset selector items failed: %s", prop_name, exc_info=True)
        return False
    if value not in valid:
        return False
    if str(getattr(wm, prop_name, "") or "") == value:
        return True
    setattr(wm, prop_name, value)
    return True


def _preset_enum_items(_self, context):
    global _PRESET_ENUM_CACHE
    work = get_work(context)
    work_dir = Path(work.work_dir) if (work and work.loaded and work.work_dir) else None
    cache: list[tuple[str, str, str]] = []
    for p in presets.list_all_presets(work_dir):
        label = p.name if p.source == "global" else f"{p.name} (共通)"
        cache.append((p.name, label, p.description))
    if not cache:
        cache.append(("", "(プリセットなし)", ""))
    _PRESET_ENUM_CACHE = cache
    return _PRESET_ENUM_CACHE


def _on_paper_preset_selector_change(self, context):
    """WindowManager.bmanga_paper_preset_selector の変更時に用紙プリセットを即時適用."""
    global _SUPPRESS_SELECTOR_UPDATE
    if _SUPPRESS_SELECTOR_UPDATE:
        return
    name = getattr(self, "bmanga_paper_preset_selector", "")
    if not name:
        return
    work = get_work(context)
    if not (work and work.loaded):
        return
    work_dir = Path(work.work_dir) if work.work_dir else None
    preset = presets.load_preset_by_name(name, work_dir)
    if preset is None:
        return
    presets.apply_preset_to_work(preset, work)
    _logger.info("paper preset applied via selector: %s", preset.name)


def sync_paper_preset_selector(context) -> None:
    """現在の ``work.paper.preset_name`` に selector を合わせる."""
    global _SUPPRESS_SELECTOR_UPDATE

    work = get_work(context)
    if not (work and work.loaded):
        return
    name = (getattr(work.paper, "preset_name", "") or "").strip()
    if not name:
        return
    work_dir = Path(work.work_dir) if work.work_dir else None
    preset = presets.load_preset_by_name(name, work_dir)
    if preset is None:
        return
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_paper_preset_selector"):
        return
    cur = getattr(wm, "bmanga_paper_preset_selector", "")
    if cur == name:
        return
    _preset_enum_items(None, context)
    _SUPPRESS_SELECTOR_UPDATE = True
    try:
        wm.bmanga_paper_preset_selector = name
    finally:
        _SUPPRESS_SELECTOR_UPDATE = False


class BMANGA_OT_paper_preset_apply(Operator):
    """選択した用紙プリセットを現在の作品に適用."""

    bl_idname = "bmanga.paper_preset_apply"
    bl_label = "用紙プリセットを適用"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: EnumProperty(  # type: ignore[valid-type]
        name="プリセット",
        items=_preset_enum_items,
    )

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work = get_work(context)
        if not (work and work.loaded):
            return {"CANCELLED"}
        work_dir = Path(work.work_dir) if work.work_dir else None
        preset = presets.load_preset_by_name(self.preset_name, work_dir)
        if preset is None:
            self.report({"ERROR"}, f"プリセットが見つかりません: {self.preset_name}")
            return {"CANCELLED"}
        presets.apply_preset_to_work(preset, work)
        sync_paper_preset_selector(context)
        self.report({"INFO"}, f"プリセット適用: {preset.name}")
        return {"FINISHED"}


class BMANGA_OT_paper_preset_save_local(Operator):
    """現在の用紙設定を共通プリセットとして保存."""

    bl_idname = "bmanga.paper_preset_save_local"
    bl_label = "用紙プリセットとして保存"
    bl_options = {"REGISTER"}

    preset_name: StringProperty(  # type: ignore[valid-type]
        name="プリセット名",
        default="",
    )
    description: StringProperty(  # type: ignore[valid-type]
        name="説明",
        default="",
    )

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and w.work_dir)

    def invoke(self, context, event):
        work = get_work(context)
        self.preset_name = work.paper.preset_name or "新規プリセット"
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work = get_work(context)
        if not self.preset_name.strip():
            self.report({"ERROR"}, "プリセット名が空です")
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        try:
            out = presets.save_local_preset(
                work_dir, work, self.preset_name, self.description
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("preset_save_local failed")
            self.report({"ERROR"}, f"保存失敗: {exc}")
            return {"CANCELLED"}
        work.paper.preset_name = self.preset_name
        try:
            sync_paper_preset_selector(context)
            work_io.save_work_json(work_dir, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("preset_save_local post-save sync failed")
            self.report({"WARNING"}, f"プリセット保存後の同期に失敗: {exc}")
        self.report({"INFO"}, f"共通プリセット保存: {out.name}")
        return {"FINISHED"}


# ---------- 枠線プリセット (枠線 + フチ) ----------

_BORDER_PRESET_ENUM_CACHE: list[tuple[str, str, str]] = []
_SUPPRESS_BORDER_SELECTOR_UPDATE = False


def _border_preset_enum_items(_self, context):
    global _BORDER_PRESET_ENUM_CACHE
    work = get_work(context)
    work_dir = Path(work.work_dir) if (work and work.loaded and work.work_dir) else None
    preset_list = list(border_presets.list_all_presets(work_dir))
    cache: list[tuple[str, str, str]] = []
    for p in preset_list:
        label = p.name if p.source == "global" else f"{p.name} (共通)"
        cache.append((p.name, label, p.description))
    if not cache:
        cache.append(("", "(プリセットなし)", ""))
    _BORDER_PRESET_ENUM_CACHE = cache
    return _BORDER_PRESET_ENUM_CACHE


def _border_preset_work_dir(context) -> Path | None:
    work = get_work(context)
    if work is None or not work.loaded or not work.work_dir:
        return None
    return Path(work.work_dir)


def _selected_border_preset_name(context) -> str:
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_border_preset_selector"):
        return ""
    return str(getattr(wm, "bmanga_border_preset_selector", "") or "")


def _set_border_preset_selector(context, name: str, *, apply: bool) -> None:
    global _SUPPRESS_BORDER_SELECTOR_UPDATE
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_border_preset_selector") or not name:
        return
    valid = {item[0] for item in _border_preset_enum_items(None, context)}
    if name not in valid:
        return
    _SUPPRESS_BORDER_SELECTOR_UPDATE = not apply
    try:
        wm.bmanga_border_preset_selector = name
    finally:
        _SUPPRESS_BORDER_SELECTOR_UPDATE = False


_BALLOON_TOOL_ENUM_CACHE: dict[str, list] = {}
_TEXT_PRESET_ENUM_CACHE: list[tuple[str, str, str]] = []
BALLOON_TOOL_NURBS_PRESET = "mode:nurbs"


def _balloon_tool_preset_enum_items(_self, context):
    """フキダシツール用: 基本形状 + カスタム形状プリセットの選択肢."""
    items = [
        ("DEFAULT", "標準", "既定の形状で作成する"),
        (BALLOON_TOOL_NURBS_PRESET, "なめらか自由形状", "クリックした点を通るなめらかなフキダシを作成する"),
    ]
    try:
        from ..core.balloon import _SHAPE_ITEMS

        for shape_id, label, desc in _SHAPE_ITEMS:
            if shape_id in {"custom", "none"}:
                continue
            items.append((f"shape:{shape_id}", label, desc or f"{label}で作成する"))
    except Exception:  # noqa: BLE001
        _logger.exception("balloon tool shape items build failed")
    try:
        from ..io import balloon_presets

        work = get_work(context)
        work_dir = Path(str(getattr(work, "work_dir", "") or "")) if work is not None else None
        for preset in balloon_presets.list_all_presets(work_dir if work_dir and str(work_dir) else None):
            items.append((f"custom:{preset.name}", f"{preset.name} (カスタム)", preset.description or ""))
    except Exception:  # noqa: BLE001
        _logger.exception("balloon tool custom preset items build failed")
    _BALLOON_TOOL_ENUM_CACHE["items"] = items
    return items


def _on_balloon_tool_preset_selector_change(self, context):
    value = str(getattr(self, "bmanga_balloon_tool_preset_selector", "") or "")
    _remember_tool_preset(context, "last_balloon_tool_preset", value)


def selected_balloon_tool_shape(context) -> tuple[str, str]:
    """フキダシツールのプリセット選択を (shape, custom_preset_name) で返す."""
    wm = getattr(context, "window_manager", None)
    value = str(getattr(wm, "bmanga_balloon_tool_preset_selector", "") or "") if wm is not None else ""
    if value == BALLOON_TOOL_NURBS_PRESET:
        return "", ""
    if value.startswith("shape:"):
        return value.split(":", 1)[1], ""
    if value.startswith("custom:"):
        return "custom", value.split(":", 1)[1]
    return "", ""


def selected_balloon_tool_creation_mode(context) -> str:
    """フキダシツールの作成方式を返す。通常はドラッグ作成。"""
    wm = getattr(context, "window_manager", None)
    value = str(getattr(wm, "bmanga_balloon_tool_preset_selector", "") or "") if wm is not None else ""
    return "nurbs" if value == BALLOON_TOOL_NURBS_PRESET else "drag"


def _text_preset_enum_items(_self, context):
    """テキストツール用: テキストスタイルプリセットの選択肢."""
    global _TEXT_PRESET_ENUM_CACHE
    items: list[tuple[str, str, str]] = []
    try:
        from ..io import text_presets

        work = get_work(context)
        work_dir = Path(str(getattr(work, "work_dir", "") or "")) if work is not None else None
        for preset in text_presets.list_all_presets(work_dir if work_dir and str(work_dir) else None):
            items.append((preset.name, preset.name, preset.description or ""))
    except Exception:  # noqa: BLE001
        _logger.exception("text preset items build failed")
    if not items:
        items.append(("NONE", "—", ""))
    _TEXT_PRESET_ENUM_CACHE = items
    return items


def _on_text_preset_selector_change(self, context):
    """テキストプリセット変更時: カーソル形状を縦書き/横書きに合わせて切替."""
    value = str(getattr(self, "bmanga_text_tool_preset_selector", "") or "")
    _remember_tool_preset(context, "last_text_tool_preset", value)
    if coma_modal_state.is_active("text_tool"):
        cursor_type = text_tool_cursor_type(context)
        op = coma_modal_state.get_active("text_tool")
        if op is not None and hasattr(op, "_setup_vertical_cursor"):
            op._setup_vertical_cursor(context, cursor_type == "vertical")
        coma_modal_state.set_modal_cursor(
            context, "NONE" if cursor_type == "vertical" else cursor_type
        )


def text_tool_cursor_type(context) -> str:
    """選択中のテキストプリセットの縦横に応じたカーソル種別を返す.

    縦書き時は ``"vertical"`` を返す (呼び出し側でカスタム描画に切り替える)。
    """
    name = selected_text_preset_name(context)
    if not name:
        return "TEXT"
    try:
        from ..io import text_presets

        work = get_work(context)
        work_dir = Path(str(getattr(work, "work_dir", "") or "")) if work is not None else None
        for preset in text_presets.list_all_presets(work_dir if work_dir and str(work_dir) else None):
            if preset.name == name:
                if preset.data.get("writing_mode") == "vertical":
                    return "vertical"
                return "TEXT"
    except Exception:  # noqa: BLE001
        pass
    return "TEXT"


def selected_text_preset_name(context) -> str:
    """テキストツールで選択中のプリセット名を返す."""
    wm = getattr(context, "window_manager", None)
    value = str(getattr(wm, "bmanga_text_tool_preset_selector", "") or "") if wm is not None else ""
    if value == "NONE" or not value:
        return ""
    return value


def apply_text_preset_to_entry(context, entry) -> bool:
    """選択中のテキストプリセットを TextEntry に適用."""
    name = selected_text_preset_name(context)
    if not name:
        return False
    try:
        from ..io import text_presets

        work = get_work(context)
        work_dir = Path(str(getattr(work, "work_dir", "") or "")) if work is not None else None
        all_presets = text_presets.list_all_presets(work_dir if work_dir and str(work_dir) else None)
        for preset in all_presets:
            if preset.name == name:
                text_presets.apply_to_entry(entry, preset.data)
                return True
    except Exception:  # noqa: BLE001
        _logger.exception("text preset apply failed")
    return False


def sync_border_preset_selector(context) -> None:
    """アクティブコマの ``border.preset_name`` に枠線セレクタを合わせる.

    枠線セレクタ (``bmanga_border_preset_selector``) は WindowManager 上の一時
    プロパティで、 ファイル/ウィンドウを開き直すたび先頭の「標準」へ戻る。
    コマ自身が保持する適用プリセット名へ追従させ、 コマ編集から戻った直後に
    実際の見た目と表示がズレない (= プリセットがリセットされたように見えない)
    ようにする。 実データ (border.* / white_margin.*) はここでは一切変更しない。
    """
    global _SUPPRESS_BORDER_SELECTOR_UPDATE

    resolved = _resolve_selected_coma(context)
    if resolved is None:
        return
    _work, _page, _pi, coma = resolved
    name = (getattr(getattr(coma, "border", None), "preset_name", "") or "").strip()
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_border_preset_selector"):
        return
    if not name:
        return
    # セレクタの enum に存在しない名前 (削除済みプリセット等) は無視する。
    items = _border_preset_enum_items(None, context)
    if name not in {item[0] for item in items}:
        return
    if getattr(wm, "bmanga_border_preset_selector", "") == name:
        return
    _SUPPRESS_BORDER_SELECTOR_UPDATE = True
    try:
        wm.bmanga_border_preset_selector = name
    finally:
        _SUPPRESS_BORDER_SELECTOR_UPDATE = False


def _resolve_selected_coma(context):
    """枠線プリセットの対象コマを解決.

    枠線/辺の選択 (``bmanga_edge_select_*``) を優先し、無ければアクティブ
    ページのアクティブコマ。戻り値: (work, page, page_index, coma) or None。
    """
    work = get_work(context)
    if work is None or not work.loaded:
        return None
    wm = context.window_manager
    if getattr(wm, "bmanga_edge_select_kind", "none") != "none":
        pi = int(getattr(wm, "bmanga_edge_select_page", -1))
        ci = int(getattr(wm, "bmanga_edge_select_coma", -1))
        if 0 <= pi < len(work.pages):
            page = work.pages[pi]
            if 0 <= ci < len(page.comas):
                return work, page, pi, page.comas[ci]
    page = get_active_page(context)
    if page is None:
        return None
    ci = int(getattr(page, "active_coma_index", -1))
    if not (0 <= ci < len(page.comas)):
        return None
    pi = int(getattr(work, "active_page_index", -1))
    return work, page, pi, page.comas[ci]


def _persist_and_refresh_coma_border(context, work, page, coma) -> None:
    work_dir = Path(work.work_dir)
    try:
        coma_io.save_coma_meta(work_dir, page.id, coma)
        page_io.save_page_json(work_dir, page)
    except Exception:  # noqa: BLE001
        _logger.exception("border preset: save coma meta failed")
    try:
        from ..utils import coma_border_object as _cbo
        from ..utils import page_file_scene

        scene = context.scene
        if scene is not None and page_file_scene.is_current_page_edit_scene(scene, getattr(page, "id", "")):
            _cbo.ensure_coma_border_object(scene, work, page, coma)
    except Exception:  # noqa: BLE001
        _logger.exception("border preset: refresh border object failed")


def _apply_border_preset_to_resolved(context, resolved, preset_name: str) -> bool:
    if resolved is None or not preset_name:
        return False
    work, page, _pi, coma = resolved
    work_dir = Path(work.work_dir) if work.work_dir else None
    preset = border_presets.load_preset_by_name(preset_name, work_dir)
    if preset is None:
        return False
    border_presets.apply_preset_to_coma(preset, coma)
    _persist_and_refresh_coma_border(context, work, page, coma)
    _prepare_border_detail_curve(coma)
    return True


def _prepare_border_detail_curve(coma) -> None:
    border = getattr(coma, "border", None)
    if str(getattr(border, "style", "solid") or "solid") != "brush":
        return
    try:
        from ..utils import coma_blur_curve

        coma_blur_curve.ensure_ui_curve_node(border)
    except Exception:  # noqa: BLE001
        _logger.exception("border preset: prepare blur curve UI failed")


def _on_border_preset_selector_change(self, context):
    global _SUPPRESS_BORDER_SELECTOR_UPDATE
    if _SUPPRESS_BORDER_SELECTOR_UPDATE:
        return
    name = getattr(self, "bmanga_border_preset_selector", "")
    if not name:
        return
    resolved = _resolve_selected_coma(context)
    if resolved is None:
        return
    work, page, _pi, coma = resolved
    work_dir = Path(work.work_dir) if work.work_dir else None
    preset = border_presets.load_preset_by_name(name, work_dir)
    if preset is None:
        return
    border_presets.apply_preset_to_coma(preset, coma)
    _persist_and_refresh_coma_border(context, work, page, coma)
    _prepare_border_detail_curve(coma)
    _logger.info("border preset applied via selector: %s", preset.name)


class BMANGA_OT_border_preset_apply(Operator):
    """選択した枠線プリセットを選択中のコマへ適用 (枠線 + フチ)."""

    bl_idname = "bmanga.border_preset_apply"
    bl_label = "枠線プリセットを適用"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: EnumProperty(  # type: ignore[valid-type]
        name="プリセット",
        items=_border_preset_enum_items,
    )

    @classmethod
    def poll(cls, context):
        return _resolve_selected_coma(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        resolved = _resolve_selected_coma(context)
        if resolved is None:
            self.report({"ERROR"}, "対象のコマが選択されていません")
            return {"CANCELLED"}
        work, page, _pi, coma = resolved
        work_dir = Path(work.work_dir) if work.work_dir else None
        preset = border_presets.load_preset_by_name(self.preset_name, work_dir)
        if preset is None:
            self.report({"ERROR"}, f"プリセットが見つかりません: {self.preset_name}")
            return {"CANCELLED"}
        border_presets.apply_preset_to_coma(preset, coma)
        _persist_and_refresh_coma_border(context, work, page, coma)
        _prepare_border_detail_curve(coma)
        self.report({"INFO"}, f"枠線プリセット適用: {preset.name}")
        return {"FINISHED"}


class BMANGA_OT_border_preset_save_local(Operator):
    """選択中コマの枠線・フチ設定を共通プリセットとして保存.

    詳細設定ダイアログ (``invoke_props_dialog``) の内側から起動される場合、
    Blender は入れ子の ``invoke_props_dialog`` を許さず ``invoke`` を素通り
    して直接 ``execute`` を呼ぶ。 そのため ``preset_name`` の既定値を
    StringProperty 宣言時に非空にしておき、 invoke が呼ばれた場合だけ
    既存プリセット名と重複しない一意な名前へ更新する。
    """

    bl_idname = "bmanga.border_preset_save_local"
    bl_label = "枠線プリセットとして保存"
    bl_options = {"REGISTER"}

    preset_name: StringProperty(  # type: ignore[valid-type]
        name="プリセット名", default="新規枠線プリセット"
    )
    description: StringProperty(name="説明", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and w.work_dir) and _resolve_selected_coma(context) is not None

    def invoke(self, context, event):
        self.preset_name = _unique_border_preset_name(context, "新規枠線プリセット")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        name = self.preset_name.strip() or "新規枠線プリセット"
        # invoke がスキップされる経路 (詳細設定ダイアログ内など) では
        # 既存と重複しない名前を自動採番する。
        name = _unique_border_preset_name(context, name)
        resolved = _resolve_selected_coma(context)
        if resolved is None:
            self.report({"ERROR"}, "対象のコマが選択されていません")
            return {"CANCELLED"}
        work, _page, _pi, coma = resolved
        try:
            out = border_presets.save_local_preset(
                Path(work.work_dir), coma, name, self.description
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("border_preset_save_local failed")
            self.report({"ERROR"}, f"保存失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"枠線プリセット保存: {out.name}")
        return {"FINISHED"}


class BMANGA_OT_border_preset_add_local(Operator):
    """現在のコマ枠を新しい共通プリセットとして追加する."""

    bl_idname = "bmanga.border_preset_add_local"
    bl_label = "枠線プリセットを追加"
    bl_description = "現在のコマ枠設定を、新しい共通プリセットとして追加します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="新規枠線プリセット")  # type: ignore[valid-type]
    description: StringProperty(name="説明", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _border_preset_work_dir(context) is not None and _resolve_selected_coma(context) is not None

    def invoke(self, context, event):
        work_dir = _border_preset_work_dir(context)
        if work_dir is not None:
            self.preset_name = border_presets.unique_preset_name(work_dir, "新規枠線プリセット")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work_dir = _border_preset_work_dir(context)
        resolved = _resolve_selected_coma(context)
        if work_dir is None or resolved is None:
            self.report({"ERROR"}, "対象のコマが選択されていません")
            return {"CANCELLED"}
        work, page, _pi, coma = resolved
        name = border_presets.unique_preset_name(work_dir, self.preset_name.strip() or "新規枠線プリセット")
        try:
            border_presets.save_local_preset(
                work_dir,
                coma,
                name,
                self.description,
                insert_after=_selected_border_preset_name(context),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("border_preset_add_local failed")
            self.report({"ERROR"}, f"追加失敗: {exc}")
            return {"CANCELLED"}
        coma.border.preset_name = name
        _persist_and_refresh_coma_border(context, work, page, coma)
        _set_border_preset_selector(context, name, apply=True)
        self.report({"INFO"}, f"枠線プリセット追加: {name}")
        return {"FINISHED"}


class BMANGA_OT_border_preset_rename(Operator):
    """選択中の枠線プリセットを改名する."""

    bl_idname = "bmanga.border_preset_rename"
    bl_label = "枠線プリセットを改名"
    bl_description = "選択中の枠線プリセットを改名します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="現在の名前", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="新しい名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _border_preset_work_dir(context) is not None and bool(_selected_border_preset_name(context))

    def invoke(self, context, event):
        selected = _selected_border_preset_name(context)
        self.preset_name = selected
        self.new_name = selected
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work_dir = _border_preset_work_dir(context)
        old_name = self.preset_name.strip() or _selected_border_preset_name(context)
        new_name = self.new_name.strip()
        if work_dir is None:
            self.report({"ERROR"}, "対象のコマを選択してください")
            return {"CANCELLED"}
        selected_before = _selected_border_preset_name(context)
        names_before = [preset.name for preset in border_presets.list_all_presets(work_dir)]
        fallback = ""
        if old_name in names_before and len(names_before) > 1:
            index = names_before.index(old_name)
            fallback = names_before[index + 1] if index + 1 < len(names_before) else names_before[index - 1]
        if selected_before == old_name and fallback:
            _set_border_preset_selector(context, fallback, apply=False)
        try:
            preset = border_presets.rename_preset(work_dir, old_name, new_name)
        except Exception as exc:  # noqa: BLE001
            if selected_before == old_name:
                _set_border_preset_selector(context, old_name, apply=False)
            self.report({"ERROR"}, f"改名失敗: {exc}")
            return {"CANCELLED"}
        resolved = _resolve_selected_coma(context)
        if resolved is not None:
            work, page, _pi, coma = resolved
            if getattr(coma.border, "preset_name", "") == old_name:
                coma.border.preset_name = preset.name
                _persist_and_refresh_coma_border(context, work, page, coma)
        _set_border_preset_selector(context, preset.name, apply=True)
        self.report({"INFO"}, f"枠線プリセット改名: {preset.name}")
        return {"FINISHED"}


class BMANGA_OT_border_preset_duplicate(Operator):
    """選択中の枠線プリセットを複製する."""

    bl_idname = "bmanga.border_preset_duplicate"
    bl_label = "枠線プリセットを複製"
    bl_description = "選択中の枠線プリセットを共通プリセットとして複製します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="複製元", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="複製後の名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _border_preset_work_dir(context) is not None and bool(_selected_border_preset_name(context))

    def invoke(self, context, event):
        work_dir = _border_preset_work_dir(context)
        selected = _selected_border_preset_name(context)
        self.preset_name = selected
        self.new_name = (
            border_presets.unique_preset_name(work_dir, f"{selected} コピー")
            if work_dir is not None
            else f"{selected} コピー"
        )
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work_dir = _border_preset_work_dir(context)
        source_name = self.preset_name.strip() or _selected_border_preset_name(context)
        new_name = self.new_name.strip()
        if work_dir is None:
            self.report({"ERROR"}, "対象のコマを選択してください")
            return {"CANCELLED"}
        try:
            preset = border_presets.duplicate_preset(work_dir, source_name, new_name)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"複製失敗: {exc}")
            return {"CANCELLED"}
        _set_border_preset_selector(context, preset.name, apply=True)
        self.report({"INFO"}, f"枠線プリセット複製: {preset.name}")
        return {"FINISHED"}


class BMANGA_OT_border_preset_delete(Operator):
    """選択中の枠線プリセットを削除する."""

    bl_idname = "bmanga.border_preset_delete"
    bl_label = "枠線プリセットを削除"
    bl_description = "選択中の枠線プリセットを共通一覧から削除します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _border_preset_work_dir(context) is not None and bool(_selected_border_preset_name(context))

    def invoke(self, context, event):
        self.preset_name = self.preset_name or _selected_border_preset_name(context)
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        work_dir = _border_preset_work_dir(context)
        name = self.preset_name.strip() or _selected_border_preset_name(context)
        if work_dir is None:
            self.report({"ERROR"}, "対象のコマを選択してください")
            return {"CANCELLED"}
        selected_before = _selected_border_preset_name(context)
        names_before = [preset.name for preset in border_presets.list_all_presets(work_dir)]
        fallback = ""
        if name in names_before and len(names_before) > 1:
            index = names_before.index(name)
            fallback = names_before[index + 1] if index + 1 < len(names_before) else names_before[index - 1]
        if fallback and selected_before == name:
            _set_border_preset_selector(context, fallback, apply=False)
        try:
            border_presets.delete_preset(work_dir, name)
        except Exception as exc:  # noqa: BLE001
            if name in {preset.name for preset in border_presets.list_all_presets(work_dir)}:
                _set_border_preset_selector(context, name, apply=False)
            self.report({"ERROR"}, f"削除失敗: {exc}")
            return {"CANCELLED"}
        presets_after = border_presets.list_all_presets(work_dir)
        after_names = {preset.name for preset in presets_after}
        target = fallback if fallback in after_names else (presets_after[0].name if presets_after else "")
        resolved = _resolve_selected_coma(context)
        if resolved is not None:
            work, page, _pi, coma = resolved
            should_replace_current = (
                selected_before == name or getattr(coma.border, "preset_name", "") == name
            )
            if should_replace_current and target:
                _apply_border_preset_to_resolved(context, resolved, target)
            elif should_replace_current:
                coma.border.preset_name = ""
                _persist_and_refresh_coma_border(context, work, page, coma)
        if selected_before == name and target:
            _set_border_preset_selector(context, target, apply=False)
        self.report({"INFO"}, f"枠線プリセット削除: {name}")
        return {"FINISHED"}


class BMANGA_OT_border_preset_move(Operator):
    """選択中の枠線プリセットを並べ替える."""

    bl_idname = "bmanga.border_preset_move"
    bl_label = "枠線プリセットを並べ替え"
    bl_description = "選択中の枠線プリセットを上下に移動します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]
    direction: StringProperty(name="方向", default="UP")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _border_preset_work_dir(context) is not None and bool(_selected_border_preset_name(context))

    def execute(self, context):
        work_dir = _border_preset_work_dir(context)
        name = self.preset_name.strip() or _selected_border_preset_name(context)
        if work_dir is None:
            self.report({"ERROR"}, "対象のコマを選択してください")
            return {"CANCELLED"}
        try:
            border_presets.move_preset(work_dir, name, self.direction)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"並べ替え失敗: {exc}")
            return {"CANCELLED"}
        _set_border_preset_selector(context, name, apply=False)
        self.report({"INFO"}, f"枠線プリセット並べ替え: {name}")
        return {"FINISHED"}


def _unique_border_preset_name(context, base: str) -> str:
    """共通一覧内で既存プリセット名と被らない名前を返す."""
    work = get_work(context)
    if work is None or not getattr(work, "work_dir", ""):
        return base
    return border_presets.unique_preset_name(Path(work.work_dir), base)


# ---------- 囲い塗り / グラデーション プリセット ----------

_FILL_TOOL_ENUM_CACHE: list[tuple[str, str, str]] = []
_GRADIENT_TOOL_ENUM_CACHE: list[tuple[str, str, str]] = []
_IMAGE_PATH_TOOL_ENUM_CACHE: list[tuple[str, str, str]] = []

_FILL_PRESETS = [
    {"id": "black", "label": "ベタ塗り (黒)", "color": (0, 0, 0, 1), "opacity": 100},
    {"id": "white", "label": "ベタ塗り (白)", "color": (1, 1, 1, 1), "opacity": 100},
    {"id": "gray50", "label": "ベタ塗り (50%)", "color": (0.214, 0.214, 0.214, 1), "opacity": 100},
    {"id": "black50", "label": "ベタ塗り (黒 半透明)", "color": (0, 0, 0, 1), "opacity": 50},
]

_GRADIENT_PRESETS = [
    {"id": "bw_linear", "label": "黒→白", "gradient_type": "linear", "color": (0, 0, 0, 1), "color2": (1, 1, 1, 1), "opacity": 100},
    {"id": "wb_linear", "label": "白→黒", "gradient_type": "linear", "color": (1, 1, 1, 1), "color2": (0, 0, 0, 1), "opacity": 100},
    {"id": "bw_radial", "label": "黒→白 (円形)", "gradient_type": "radial", "color": (0, 0, 0, 1), "color2": (1, 1, 1, 1), "opacity": 100},
    {"id": "bw50", "label": "黒→白 (半透明)", "gradient_type": "linear", "color": (0, 0, 0, 1), "color2": (1, 1, 1, 1), "opacity": 50},
]


def _fill_tool_preset_enum_items(_self, _context):
    global _FILL_TOOL_ENUM_CACHE
    _FILL_TOOL_ENUM_CACHE = [(p["id"], p["label"], "") for p in _FILL_PRESETS]
    return _FILL_TOOL_ENUM_CACHE


def _gradient_tool_preset_enum_items(_self, _context):
    global _GRADIENT_TOOL_ENUM_CACHE
    _GRADIENT_TOOL_ENUM_CACHE = [(p["id"], p["label"], "") for p in _GRADIENT_PRESETS]
    return _GRADIENT_TOOL_ENUM_CACHE


def _image_path_tool_preset_enum_items(_self, context):
    global _IMAGE_PATH_TOOL_ENUM_CACHE
    work = get_work(context)
    work_dir = Path(work.work_dir) if (work and work.loaded and work.work_dir) else None
    preset_list = image_path_presets.list_all_presets(work_dir)
    cache = [(p.name, p.name if p.source == "global" else f"{p.name} (共通)", p.description) for p in preset_list]
    if not cache:
        cache.append(("", "(プリセットなし)", ""))
    _IMAGE_PATH_TOOL_ENUM_CACHE = cache
    return _IMAGE_PATH_TOOL_ENUM_CACHE


def _on_fill_tool_preset_selector_change(self, context):
    value = str(getattr(self, "bmanga_fill_tool_preset_selector", "") or "")
    _remember_tool_preset(context, "last_fill_tool_preset", value)


def _on_gradient_tool_preset_selector_change(self, context):
    value = str(getattr(self, "bmanga_gradient_tool_preset_selector", "") or "")
    _remember_tool_preset(context, "last_gradient_tool_preset", value)


def _on_image_path_tool_preset_selector_change(self, context):
    value = str(getattr(self, "bmanga_image_path_tool_preset_selector", "") or "")
    _remember_tool_preset(context, "last_image_path_tool_preset", value)


def _find_fill_preset(preset_id: str) -> dict | None:
    for p in _FILL_PRESETS:
        if p["id"] == preset_id:
            return p
    return None


def _find_gradient_preset(preset_id: str) -> dict | None:
    for p in _GRADIENT_PRESETS:
        if p["id"] == preset_id:
            return p
    return None


def _image_path_preset_work_dir(context) -> Path | None:
    work = get_work(context)
    if work is None or not work.loaded or not work.work_dir:
        return None
    return Path(work.work_dir)


def _selected_image_path_preset_name(context) -> str:
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_image_path_tool_preset_selector"):
        return ""
    return str(getattr(wm, "bmanga_image_path_tool_preset_selector", "") or "")


def _set_image_path_preset_selector(context, name: str) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_image_path_tool_preset_selector") or not name:
        return
    valid = {item[0] for item in _image_path_tool_preset_enum_items(None, context)}
    if name not in valid:
        return
    setattr(wm, "bmanga_image_path_tool_preset_selector", name)


def _active_image_path_entry(context):
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bmanga_image_path_layers", None) if scene is not None else None
    idx = int(getattr(scene, "bmanga_active_image_path_layer_index", -1)) if scene is not None else -1
    if coll is not None and 0 <= idx < len(coll):
        return coll[idx]
    return None


def apply_fill_preset_to_entry(context, entry) -> bool:
    """選択中の囲い塗りプリセットをフィルエントリに適用."""
    wm = getattr(context, "window_manager", None)
    pid = str(getattr(wm, "bmanga_fill_tool_preset_selector", "") or "") if wm else ""
    preset = _find_fill_preset(pid) if pid else None
    if preset is None and _FILL_PRESETS:
        preset = _FILL_PRESETS[0]
    if preset is None:
        return False
    entry.color = preset["color"]
    entry.opacity = preset["opacity"]
    return True


def apply_image_path_preset_to_entry(context, entry) -> bool:
    """選択中の画像パスプリセットを画像パスエントリに適用."""
    work_dir = _image_path_preset_work_dir(context)
    name = _selected_image_path_preset_name(context)
    preset = image_path_presets.load_preset_by_name(name, work_dir) if name else None
    if preset is None:
        preset_list = image_path_presets.list_all_presets(work_dir)
        preset = preset_list[0] if preset_list else None
    if preset is None:
        return False
    image_path_presets.apply_preset_to_entry(preset, entry)
    return True


def apply_gradient_preset_to_entry(context, entry) -> bool:
    """選択中のグラデーションプリセットをフィルエントリに適用."""
    wm = getattr(context, "window_manager", None)
    pid = str(getattr(wm, "bmanga_gradient_tool_preset_selector", "") or "") if wm else ""
    preset = _find_gradient_preset(pid) if pid else None
    if preset is None and _GRADIENT_PRESETS:
        preset = _GRADIENT_PRESETS[0]
    if preset is None:
        return False
    entry.color = preset["color"]
    entry.color2 = preset["color2"]
    entry.gradient_type = preset["gradient_type"]
    entry.opacity = preset["opacity"]
    return True


class BMANGA_OT_image_path_preset_add_local(Operator):
    """現在の画像パス設定を新しい共通プリセットとして追加する."""

    bl_idname = "bmanga.image_path_preset_add_local"
    bl_label = "画像パスプリセットを追加"
    bl_description = "現在の画像パス設定を、新しい共通プリセットとして追加します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="新規画像パスプリセット")  # type: ignore[valid-type]
    description: StringProperty(name="説明", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _active_image_path_entry(context) is not None

    def invoke(self, context, event):
        work_dir = _image_path_preset_work_dir(context)
        self.preset_name = image_path_presets.unique_preset_name(work_dir, "新規画像パスプリセット")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        entry = _active_image_path_entry(context)
        if entry is None:
            self.report({"ERROR"}, "画像パスが選択されていません")
            return {"CANCELLED"}
        work_dir = _image_path_preset_work_dir(context)
        name = image_path_presets.unique_preset_name(
            work_dir, self.preset_name.strip() or "新規画像パスプリセット"
        )
        try:
            image_path_presets.save_local_preset(
                work_dir,
                entry,
                name,
                self.description,
                insert_after=_selected_image_path_preset_name(context),
            )
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"追加失敗: {exc}")
            return {"CANCELLED"}
        _set_image_path_preset_selector(context, name)
        self.report({"INFO"}, f"画像パスプリセット追加: {name}")
        return {"FINISHED"}


class BMANGA_OT_image_path_preset_rename(Operator):
    """選択中の画像パスプリセットを改名する."""

    bl_idname = "bmanga.image_path_preset_rename"
    bl_label = "画像パスプリセットを改名"
    bl_description = "選択中の画像パスプリセットを改名します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="現在の名前", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="新しい名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_image_path_preset_name(context))

    def invoke(self, context, event):
        selected = _selected_image_path_preset_name(context)
        self.preset_name = selected
        self.new_name = selected
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work_dir = _image_path_preset_work_dir(context)
        old_name = self.preset_name.strip() or _selected_image_path_preset_name(context)
        new_name = self.new_name.strip()
        try:
            preset = image_path_presets.rename_preset(work_dir, old_name, new_name)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"改名失敗: {exc}")
            return {"CANCELLED"}
        _set_image_path_preset_selector(context, preset.name)
        self.report({"INFO"}, f"画像パスプリセット改名: {preset.name}")
        return {"FINISHED"}


class BMANGA_OT_image_path_preset_duplicate(Operator):
    """選択中の画像パスプリセットを複製する."""

    bl_idname = "bmanga.image_path_preset_duplicate"
    bl_label = "画像パスプリセットを複製"
    bl_description = "選択中の画像パスプリセットを共通プリセットとして複製します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="複製元", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="複製後の名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_image_path_preset_name(context))

    def invoke(self, context, event):
        work_dir = _image_path_preset_work_dir(context)
        selected = _selected_image_path_preset_name(context)
        self.preset_name = selected
        self.new_name = image_path_presets.unique_preset_name(work_dir, f"{selected} コピー")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work_dir = _image_path_preset_work_dir(context)
        source_name = self.preset_name.strip() or _selected_image_path_preset_name(context)
        new_name = self.new_name.strip()
        try:
            preset = image_path_presets.duplicate_preset(work_dir, source_name, new_name)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"複製失敗: {exc}")
            return {"CANCELLED"}
        _set_image_path_preset_selector(context, preset.name)
        self.report({"INFO"}, f"画像パスプリセット複製: {preset.name}")
        return {"FINISHED"}


class BMANGA_OT_image_path_preset_delete(Operator):
    """選択中の画像パスプリセットを削除する."""

    bl_idname = "bmanga.image_path_preset_delete"
    bl_label = "画像パスプリセットを削除"
    bl_description = "選択中の画像パスプリセットを共通一覧から削除します"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_image_path_preset_name(context))

    def invoke(self, context, event):
        self.preset_name = self.preset_name or _selected_image_path_preset_name(context)
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        work_dir = _image_path_preset_work_dir(context)
        name = self.preset_name.strip() or _selected_image_path_preset_name(context)
        names_before = [preset.name for preset in image_path_presets.list_all_presets(work_dir)]
        fallback = ""
        if name in names_before and len(names_before) > 1:
            index = names_before.index(name)
            fallback = names_before[index + 1] if index + 1 < len(names_before) else names_before[index - 1]
        try:
            image_path_presets.delete_preset(work_dir, name)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"削除失敗: {exc}")
            return {"CANCELLED"}
        presets_after = image_path_presets.list_all_presets(work_dir)
        after_names = {preset.name for preset in presets_after}
        target = fallback if fallback in after_names else (presets_after[0].name if presets_after else "")
        if target:
            _set_image_path_preset_selector(context, target)
        self.report({"INFO"}, f"画像パスプリセット削除: {name}")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_paper_preset_apply,
    BMANGA_OT_paper_preset_save_local,
    BMANGA_OT_border_preset_apply,
    BMANGA_OT_border_preset_save_local,
    BMANGA_OT_border_preset_add_local,
    BMANGA_OT_border_preset_rename,
    BMANGA_OT_border_preset_duplicate,
    BMANGA_OT_border_preset_delete,
    BMANGA_OT_border_preset_move,
    BMANGA_OT_image_path_preset_add_local,
    BMANGA_OT_image_path_preset_rename,
    BMANGA_OT_image_path_preset_duplicate,
    BMANGA_OT_image_path_preset_delete,
)


def restore_tool_preset_selectors(context) -> None:
    """前回選んだツールプリセットを WindowManager の選択欄へ戻す."""
    global _SUPPRESS_TOOL_PRESET_REMEMBER
    try:
        from .. import preferences as addon_preferences

        prefs = addon_preferences.get_preferences(context)
    except Exception:  # noqa: BLE001
        prefs = None
    if prefs is None:
        return

    _SUPPRESS_TOOL_PRESET_REMEMBER = True
    try:
        _restore_selector_if_valid(
            context,
            "bmanga_balloon_tool_preset_selector",
            getattr(prefs, "last_balloon_tool_preset", ""),
            _balloon_tool_preset_enum_items,
        )
        _restore_selector_if_valid(
            context,
            "bmanga_text_tool_preset_selector",
            getattr(prefs, "last_text_tool_preset", ""),
            _text_preset_enum_items,
        )
        _restore_selector_if_valid(
            context,
            "bmanga_fill_tool_preset_selector",
            getattr(prefs, "last_fill_tool_preset", ""),
            _fill_tool_preset_enum_items,
        )
        _restore_selector_if_valid(
            context,
            "bmanga_gradient_tool_preset_selector",
            getattr(prefs, "last_gradient_tool_preset", ""),
            _gradient_tool_preset_enum_items,
        )
        _restore_selector_if_valid(
            context,
            "bmanga_image_path_tool_preset_selector",
            getattr(prefs, "last_image_path_tool_preset", ""),
            _image_path_tool_preset_enum_items,
        )
    finally:
        _SUPPRESS_TOOL_PRESET_REMEMBER = False


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bmanga_paper_preset_selector = EnumProperty(
        name="プリセット",
        description="用紙プリセットを選択して即時適用",
        items=_preset_enum_items,
        update=_on_paper_preset_selector_change,
    )
    bpy.types.WindowManager.bmanga_border_preset_selector = EnumProperty(
        name="枠線プリセット",
        description="枠線プリセットを選択して選択中のコマへ即時適用",
        items=_border_preset_enum_items,
        update=_on_border_preset_selector_change,
    )
    bpy.types.WindowManager.bmanga_balloon_tool_preset_selector = EnumProperty(
        name="フキダシ形状",
        description="フキダシツールで新しく作るフキダシの形状プリセット",
        items=_balloon_tool_preset_enum_items,
        update=_on_balloon_tool_preset_selector_change,
    )
    bpy.types.WindowManager.bmanga_text_tool_preset_selector = EnumProperty(
        name="テキストプリセット",
        description="テキストツールで新しく作るテキストのスタイルプリセット",
        items=_text_preset_enum_items,
        update=_on_text_preset_selector_change,
    )
    bpy.types.WindowManager.bmanga_fill_tool_preset_selector = EnumProperty(
        name="囲い塗りプリセット",
        description="囲い塗りツールで新しく作るベタ塗りの色・不透明度",
        items=_fill_tool_preset_enum_items,
        update=_on_fill_tool_preset_selector_change,
    )
    bpy.types.WindowManager.bmanga_gradient_tool_preset_selector = EnumProperty(
        name="グラデーションプリセット",
        description="グラデーションツールで新しく作るグラデーションの設定",
        items=_gradient_tool_preset_enum_items,
        update=_on_gradient_tool_preset_selector_change,
    )
    bpy.types.WindowManager.bmanga_image_path_tool_preset_selector = EnumProperty(
        name="画像パスプリセット",
        description="画像パスツールで新しく作る画像パスの設定",
        items=_image_path_tool_preset_enum_items,
        update=_on_image_path_tool_preset_selector_change,
    )
    try:
        restore_tool_preset_selectors(bpy.context)
    except Exception:  # noqa: BLE001
        _logger.exception("tool preset selector restore failed")


def unregister() -> None:
    try:
        del bpy.types.WindowManager.bmanga_paper_preset_selector
    except AttributeError:
        pass
    try:
        del bpy.types.WindowManager.bmanga_border_preset_selector
    except AttributeError:
        pass
    try:
        del bpy.types.WindowManager.bmanga_balloon_tool_preset_selector
    except AttributeError:
        pass
    try:
        del bpy.types.WindowManager.bmanga_text_tool_preset_selector
    except AttributeError:
        pass
    try:
        del bpy.types.WindowManager.bmanga_fill_tool_preset_selector
    except AttributeError:
        pass
    try:
        del bpy.types.WindowManager.bmanga_gradient_tool_preset_selector
    except AttributeError:
        pass
    try:
        del bpy.types.WindowManager.bmanga_image_path_tool_preset_selector
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
