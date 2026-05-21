"""用紙プリセット適用・保存・削除 Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..io import border_presets, page_io, presets, work_io
from ..io import coma_io
from ..utils import log

_logger = log.get_logger(__name__)


# Blender の EnumProperty callback は返した文字列への参照を保持しないため
# GC でクラッシュすることがある (公式既知の不具合)。モジュールレベルで
# キャッシュを保持して回避する。
_PRESET_ENUM_CACHE: list[tuple[str, str, str]] = []
_SUPPRESS_SELECTOR_UPDATE = False


def _preset_enum_items(_self, context):
    global _PRESET_ENUM_CACHE
    work = get_work(context)
    work_dir = Path(work.work_dir) if (work and work.loaded and work.work_dir) else None
    cache: list[tuple[str, str, str]] = []
    for p in presets.list_all_presets(work_dir):
        label = p.name if p.source == "global" else f"{p.name} (作品)"
        cache.append((p.name, label, p.description))
    if not cache:
        cache.append(("", "(プリセットなし)", ""))
    _PRESET_ENUM_CACHE = cache
    return _PRESET_ENUM_CACHE


def _on_paper_preset_selector_change(self, context):
    """WindowManager.bname_paper_preset_selector の変更時に用紙プリセットを即時適用."""
    global _SUPPRESS_SELECTOR_UPDATE
    if _SUPPRESS_SELECTOR_UPDATE:
        return
    name = getattr(self, "bname_paper_preset_selector", "")
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
    if wm is None or not hasattr(wm, "bname_paper_preset_selector"):
        return
    cur = getattr(wm, "bname_paper_preset_selector", "")
    if cur == name:
        return
    _preset_enum_items(None, context)
    _SUPPRESS_SELECTOR_UPDATE = True
    try:
        wm.bname_paper_preset_selector = name
    finally:
        _SUPPRESS_SELECTOR_UPDATE = False


class BNAME_OT_paper_preset_apply(Operator):
    """選択した用紙プリセットを現在の作品に適用."""

    bl_idname = "bname.paper_preset_apply"
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


class BNAME_OT_paper_preset_save_local(Operator):
    """現在の用紙設定を作品ローカルプリセットとして保存."""

    bl_idname = "bname.paper_preset_save_local"
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
        self.report({"INFO"}, f"ローカルプリセット保存: {out.name}")
        return {"FINISHED"}


# ---------- 枠線プリセット (枠線 + 白フチ) ----------

_BORDER_PRESET_ENUM_CACHE: list[tuple[str, str, str]] = []
_SUPPRESS_BORDER_SELECTOR_UPDATE = False


def _border_preset_enum_items(_self, context):
    global _BORDER_PRESET_ENUM_CACHE
    work = get_work(context)
    work_dir = Path(work.work_dir) if (work and work.loaded and work.work_dir) else None
    preset_list = list(border_presets.list_all_presets(work_dir))
    # 「標準」を先頭に固定し、新規ウィンドウでの初期選択を「標準」にする
    # (動的 EnumProperty は items の先頭要素が既定値になるため)
    preset_list.sort(key=lambda p: 0 if p.name == "標準" else 1)
    cache: list[tuple[str, str, str]] = []
    for p in preset_list:
        label = p.name if p.source == "global" else f"{p.name} (作品)"
        cache.append((p.name, label, p.description))
    if not cache:
        cache.append(("", "(プリセットなし)", ""))
    _BORDER_PRESET_ENUM_CACHE = cache
    return _BORDER_PRESET_ENUM_CACHE


def _resolve_selected_coma(context):
    """枠線プリセットの対象コマを解決.

    枠線/辺の選択 (``bname_edge_select_*``) を優先し、無ければアクティブ
    ページのアクティブコマ。戻り値: (work, page, page_index, coma) or None。
    """
    work = get_work(context)
    if work is None or not work.loaded:
        return None
    wm = context.window_manager
    if getattr(wm, "bname_edge_select_kind", "none") != "none":
        pi = int(getattr(wm, "bname_edge_select_page", -1))
        ci = int(getattr(wm, "bname_edge_select_coma", -1))
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

        scene = context.scene
        if scene is not None:
            _cbo.ensure_coma_border_object(scene, work, page, coma)
    except Exception:  # noqa: BLE001
        _logger.exception("border preset: refresh border object failed")


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
    name = getattr(self, "bname_border_preset_selector", "")
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


class BNAME_OT_border_preset_apply(Operator):
    """選択した枠線プリセットを選択中のコマへ適用 (枠線 + 白フチ)."""

    bl_idname = "bname.border_preset_apply"
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


class BNAME_OT_border_preset_save_local(Operator):
    """選択中コマの枠線・白フチ設定を作品ローカルプリセットとして保存.

    詳細設定ダイアログ (``invoke_props_dialog``) の内側から起動される場合、
    Blender は入れ子の ``invoke_props_dialog`` を許さず ``invoke`` を素通り
    して直接 ``execute`` を呼ぶ。 そのため ``preset_name`` の既定値を
    StringProperty 宣言時に非空にしておき、 invoke が呼ばれた場合だけ
    既存プリセット名と重複しない一意な名前へ更新する。
    """

    bl_idname = "bname.border_preset_save_local"
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


def _unique_border_preset_name(context, base: str) -> str:
    """同一作品内で既存プリセット名と被らない名前を返す."""
    work = get_work(context)
    if work is None or not getattr(work, "work_dir", ""):
        return base
    try:
        existing = {
            str(getattr(p, "name", "") or "")
            for p in border_presets.list_all_presets(Path(work.work_dir))
        }
    except Exception:  # noqa: BLE001
        return base
    if base not in existing:
        return base
    for i in range(2, 1000):
        candidate = f"{base} {i:03d}"
        if candidate not in existing:
            return candidate
    return base


_CLASSES = (
    BNAME_OT_paper_preset_apply,
    BNAME_OT_paper_preset_save_local,
    BNAME_OT_border_preset_apply,
    BNAME_OT_border_preset_save_local,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bname_paper_preset_selector = EnumProperty(
        name="プリセット",
        description="用紙プリセットを選択して即時適用",
        items=_preset_enum_items,
        update=_on_paper_preset_selector_change,
    )
    bpy.types.WindowManager.bname_border_preset_selector = EnumProperty(
        name="枠線プリセット",
        description="枠線プリセットを選択して選択中のコマへ即時適用",
        items=_border_preset_enum_items,
        update=_on_border_preset_selector_change,
    )


def unregister() -> None:
    try:
        del bpy.types.WindowManager.bname_paper_preset_selector
    except AttributeError:
        pass
    try:
        del bpy.types.WindowManager.bname_border_preset_selector
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
