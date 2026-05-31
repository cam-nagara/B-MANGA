"""3D アセット連携 Operator (Phase 4).

計画書 3.4.4 / 3.4.5 / 8.13 参照。アセットブラウザからのリンク追加は
Blender 標準 UI を使うため、ここでは:
- リンク元 .blend を subprocess で開く
- 現在選択中のオブジェクトのリンク情報を cNN.json に記録
のみを提供する。
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.types import Operator

from ..core.mode import MODE_COMA, get_mode
from ..core.work import find_page_by_id, get_work
from ..io import coma_io
from ..utils import asset_bundle, bpy_link, log, paths

_logger = log.get_logger(__name__)


class BNAME_OT_open_link_source(Operator):
    """選択中オブジェクトのリンク元 .blend を新しい Blender で開く."""

    bl_idname = "bname.open_link_source"
    bl_label = "リンク元ファイルを開く"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None:
            return False
        return bool(
            (getattr(obj, "library", None) and obj.library.filepath)
            or (getattr(obj, "data", None) and getattr(obj.data, "library", None) and obj.data.library.filepath)
        )

    def execute(self, context):
        obj = context.active_object
        candidates = list(bpy_link.find_linked_filepaths(obj))
        if not candidates:
            self.report({"ERROR"}, "リンク元ファイルが見つかりません")
            return {"CANCELLED"}
        target = candidates[0]
        proc = bpy_link.open_in_new_blender(target)
        if proc is None:
            self.report({"ERROR"}, f"Blender 起動に失敗: {target}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"別 Blender で開きました: {target.name}")
        return {"FINISHED"}


class BNAME_OT_record_asset_link(Operator):
    """コマ編集モード中、選択中オブジェクトのリンク参照を cNN.json に記録."""

    bl_idname = "bname.record_asset_link"
    bl_label = "このリンクを記録"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        if get_mode(context) != MODE_COMA:
            return False
        obj = context.active_object
        return obj is not None

    def execute(self, context):
        work = get_work(context)
        stem = getattr(context.scene, "bname_current_coma_id", "")
        page_id = getattr(context.scene, "bname_current_coma_page_id", "")
        page = find_page_by_id(work, page_id)
        if work is None or page is None or not stem:
            self.report({"ERROR"}, "コマ編集モード + アクティブコマが必要です")
            return {"CANCELLED"}
        if not paths.is_valid_coma_id(stem):
            self.report({"ERROR"}, f"不正なコマ stem: {stem}")
            return {"CANCELLED"}
        entry = _find_coma_by_stem(page, stem)
        if entry is None:
            self.report({"ERROR"}, f"コマエントリが見つかりません: {stem}")
            return {"CANCELLED"}
        obj = context.active_object
        link_id = _make_link_id(obj)
        if not link_id:
            self.report({"ERROR"}, "リンク情報が取得できません")
            return {"CANCELLED"}
        # 既存の layer_refs に同じ ID が無ければ追加
        for existing in entry.layer_refs:
            if existing.layer_id == link_id:
                self.report({"INFO"}, "既に記録済みです")
                return {"CANCELLED"}
        ref = entry.layer_refs.add()
        ref.layer_id = link_id
        try:
            coma_io.save_coma_meta(Path(work.work_dir), page.id, entry)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("record_asset_link failed")
            self.report({"ERROR"}, f"保存失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"リンク記録: {link_id}")
        return {"FINISHED"}


class BNAME_OT_asset_register_layers(Operator):
    """選択中のB-Nameレイヤーをアセット登録する。"""

    bl_idname = "bname.asset_register_layers"
    bl_label = "アセットに登録"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1, options={"HIDDEN"})  # type: ignore[valid-type]
    name: bpy.props.StringProperty(name="名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and getattr(work, "loaded", False))

    def execute(self, context):
        try:
            coll = asset_bundle.register_selected_layers_as_asset(
                context,
                index=int(self.index),
                name=str(self.name or ""),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("register B-Name layer asset failed")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"アセットに登録: {coll.name}")
        return {"FINISHED"}

    def invoke(self, context, event):
        try:
            coll = asset_bundle.register_selected_layers_as_asset(
                context,
                index=int(self.index),
                name=str(self.name or ""),
                event=event,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("register B-Name layer asset failed")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"アセットに登録: {coll.name}")
        return {"FINISHED"}


class BNAME_OT_asset_register_selected_objects(Operator):
    """選択中のBlenderオブジェクトをアセット登録する。"""

    bl_idname = "bname.asset_register_selected_objects"
    bl_label = "選択オブジェクトをアセットに登録"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty(name="名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(getattr(context, "selected_objects", None) or getattr(context, "active_object", None))

    def execute(self, context):
        try:
            coll = asset_bundle.register_selected_objects_as_asset(
                context,
                name=str(self.name or ""),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("register Blender object asset failed")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"アセットに登録: {coll.name}")
        return {"FINISHED"}

    def invoke(self, context, event):
        try:
            coll = asset_bundle.register_selected_objects_as_asset(
                context,
                name=str(self.name or ""),
                event=event,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("register Blender object asset failed")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"アセットに登録: {coll.name}")
        return {"FINISHED"}


class BNAME_OT_asset_import_dropped(Operator):
    """配置済みのB-NameアセットをB-Nameレイヤーに変換する。"""

    bl_idname = "bname.asset_import_dropped"
    bl_label = "配置したB-Nameアセットを取り込む"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        count = asset_bundle.process_pending_dropped_assets(context)
        if count <= 0:
            self.report({"INFO"}, "取り込むB-Nameアセットはありません")
            return {"CANCELLED"}
        self.report({"INFO"}, f"B-Nameアセットを取り込み: {count}")
        return {"FINISHED"}


def _find_coma_by_stem(page, stem: str):
    for entry in page.comas:
        if entry.coma_id == stem:
            return entry
    return None


def _make_link_id(obj: bpy.types.Object) -> str:
    """Object 名 + ライブラリパスから識別用 ID 文字列を合成."""
    lib = getattr(obj, "library", None)
    lib_path = lib.filepath if lib else ""
    return f"link:{obj.name}|{lib_path}"


_CLASSES = (
    BNAME_OT_open_link_source,
    BNAME_OT_record_asset_link,
    BNAME_OT_asset_register_layers,
    BNAME_OT_asset_register_selected_objects,
    BNAME_OT_asset_import_dropped,
)


def _draw_outliner_asset_menu(self, context) -> None:
    layout = self.layout
    layout.separator()
    layout.operator(
        BNAME_OT_asset_register_selected_objects.bl_idname,
        text="アセットに登録",
        icon="ASSET_MANAGER",
    )


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    for menu_name in ("OUTLINER_MT_object", "OUTLINER_MT_context_menu"):
        menu = getattr(bpy.types, menu_name, None)
        if menu is not None:
            try:
                menu.append(_draw_outliner_asset_menu)
            except Exception:  # noqa: BLE001
                pass


def unregister() -> None:
    for menu_name in ("OUTLINER_MT_object", "OUTLINER_MT_context_menu"):
        menu = getattr(bpy.types, menu_name, None)
        if menu is not None:
            try:
                menu.remove(_draw_outliner_asset_menu)
            except Exception:  # noqa: BLE001
                pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
