"""グリースペンシルツールプリセットの適用と CRUD オペレーター.

プリセットはレイヤーではなく「Blenderのツール状態」へ適用する:
モード切替 (描画 / スカルプト) → ツール切替 → ブラシアセット切替 →
アクティブブラシへ設定値を書き込み、の順で行う。
"""

from __future__ import annotations

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..core.gp_tool import GP_DRAW_BRUSH_ITEMS
from ..io import gp_tool_presets
from ..utils import detail_popup, log

_logger = log.get_logger(__name__)

_GP_PAINT_MODE = "PAINT_GREASE_PENCIL"
_GP_SCULPT_MODE = "SCULPT_GREASE_PENCIL"

# 機能 → (必要モード, ツールid)。ブラシ系ツールはアセット切替も行う。
_TOOL_IDNAME = {
    "brush": (_GP_PAINT_MODE, "builtin.brush"),
    "fill": (_GP_PAINT_MODE, "builtin_brush.Fill"),
    "trim": (_GP_PAINT_MODE, "builtin.trim"),
    "erase": (_GP_PAINT_MODE, "builtin_brush.Erase"),
    "grab": (_GP_SCULPT_MODE, "builtin.brush"),
}

_DRAW_BRUSH_LIB = "brushes/essentials_brushes-gp_draw.blend/Brush/"
_SCULPT_BRUSH_LIB = "brushes/essentials_brushes-gp_sculpt.blend/Brush/"
_ERASER_BRUSH_BY_MODE = {
    "HARD": "Eraser Hard",
    "SOFT": "Eraser Soft",
    "STROKE": "Eraser Stroke",
}
_KNOWN_DRAW_BRUSHES = {item[0] for item in GP_DRAW_BRUSH_ITEMS}


def _selected_gp_tool_preset_name(context) -> str:
    wm = getattr(context, "window_manager", None)
    return str(getattr(wm, "bmanga_gp_tool_preset_selector", "") or "") if wm else ""


def _set_gp_tool_preset_selector(context, name: str) -> None:
    """リネーム・削除等の後始末用のセレクタ再設定 (ツール適用はしない)."""
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_gp_tool_preset_selector"):
        return
    from . import preset_op

    try:
        with preset_op.suppress_selector_apply():
            wm.bmanga_gp_tool_preset_selector = name
    except TypeError:
        pass


def _view3d_override(context) -> dict | None:
    """ツール切替に必要な VIEW_3D コンテキストを返す (現在地が VIEW_3D なら None)."""
    space = getattr(context, "space_data", None)
    if space is not None and getattr(space, "type", "") == "VIEW_3D":
        return None
    window = getattr(context, "window", None)
    if window is not None:
        windows = (window,)
    else:
        manager = getattr(context, "window_manager", None)
        windows = tuple(getattr(manager, "windows", ()) or ())
    for candidate in windows:
        screen = getattr(candidate, "screen", None)
        for area in getattr(screen, "areas", ()) or ():
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is None:
                continue
            return {"window": candidate, "area": area, "region": region}
    return None


def _run_in_view3d(context, callback):
    override = _view3d_override(context)
    if override is None:
        return callback()
    with context.temp_override(**override):
        return callback()


def _paint_container(context, required_mode: str):
    tool_settings = getattr(context, "tool_settings", None)
    if tool_settings is None:
        tool_settings = getattr(getattr(context, "scene", None), "tool_settings", None)
    if tool_settings is None:
        return None
    attr = "gpencil_sculpt_paint" if required_mode == _GP_SCULPT_MODE else "gpencil_paint"
    return getattr(tool_settings, attr, None)


def _active_object(context):
    objects = getattr(getattr(context, "view_layer", None), "objects", None)
    return getattr(objects, "active", None) if objects is not None else None


def _ensure_gp_mode(context, required_mode: str) -> bool:
    """GPレイヤーを選択して必要なモードへ入る (既存のツール終了処理を通す)."""
    obj = _active_object(context)
    if (
        obj is not None
        and getattr(obj, "type", "") == "GREASEPENCIL"
        and getattr(obj, "mode", "") == required_mode
    ):
        return True
    try:
        result = bpy.ops.bmanga.gpencil_master_mode_set("EXEC_DEFAULT", mode=required_mode)
    except Exception:  # noqa: BLE001
        _logger.exception("gp tool preset: mode switch failed (%s)", required_mode)
        result = {"CANCELLED"}
    obj = _active_object(context)
    if "FINISHED" in result and getattr(obj, "mode", "") == required_mode:
        return True
    # B-MANGAのレイヤー一覧管理外でも、アクティブがGPオブジェクトなら直接切替する
    if obj is None or getattr(obj, "type", "") != "GREASEPENCIL":
        return False
    try:
        bpy.ops.object.mode_set(mode=required_mode)
    except Exception:  # noqa: BLE001
        _logger.exception("gp tool preset: direct mode switch failed (%s)", required_mode)
        return False
    return getattr(obj, "mode", "") == required_mode


def _set_active_tool(context, tool_idname: str) -> bool:
    def _call():
        return bpy.ops.wm.tool_set_by_id(name=tool_idname)

    try:
        result = _run_in_view3d(context, _call)
    except Exception:  # noqa: BLE001
        # ヘッドレス実行等ではトリムツールのカーソル設定が失敗し得る。
        # ブラシ設定の適用は続行する。
        _logger.exception("gp tool preset: tool switch failed (%s)", tool_idname)
        return False
    return "FINISHED" in result


def _activate_brush_asset(context, relative_identifier: str) -> bool:
    def _call():
        return bpy.ops.brush.asset_activate(
            asset_library_type="ESSENTIALS",
            asset_library_identifier="",
            relative_asset_identifier=relative_identifier,
        )

    try:
        result = _run_in_view3d(context, _call)
    except Exception:  # noqa: BLE001
        _logger.exception("gp tool preset: brush activate failed (%s)", relative_identifier)
        return False
    return "FINISHED" in result


def _brush_asset_identifier(data: dict) -> str | None:
    tool = gp_tool_presets.tool_id(data)
    if tool == "brush":
        name = str(data.get("brushAsset") or "Pencil")
        if name not in _KNOWN_DRAW_BRUSHES:
            name = "Pencil"
        return _DRAW_BRUSH_LIB + name
    if tool == "fill":
        return _DRAW_BRUSH_LIB + "Fill"
    if tool == "erase":
        mode = str(data.get("eraserMode") or "HARD")
        return _DRAW_BRUSH_LIB + _ERASER_BRUSH_BY_MODE.get(mode, "Eraser Hard")
    if tool == "grab":
        return _SCULPT_BRUSH_LIB + "Grab"
    return None  # trim はブラシを切り替えない


def _unified_settings(paint):
    return getattr(paint, "unified_paint_settings", None) if paint is not None else None


def _apply_brush_size(brush, data: dict, *, honor_size_mode: bool, ups=None) -> None:
    """サイズを適用する。

    Blender 5.2 の同梱GPブラシは既定で「シーン基準サイズ」(unprojected_size)
    のため、px の size だけを書いても画面の太さは変わらない。ブラシ/フィルは
    プリセットの「サイズの基準」に従って基準ごと適用し、消しゴム/グラブは
    画面px基準へ固定して px を確実に効かせる。スカルプトモードは既定で
    「統一サイズ設定」を使うため、有効な場合は統一設定側へも同じ値を書く。
    """
    size_mode = gp_tool_presets.enum_value("sizeMode", data.get("sizeMode"), "SCENE")
    if not honor_size_mode:
        size_mode = "VIEW"
    use_unified = bool(getattr(ups, "use_unified_size", False)) if ups is not None else False
    if size_mode == "SCENE" and "sizeMm" in data:
        size_mm = max(0.01, float(data.get("sizeMm") or 1.0))
        if hasattr(brush, "use_locked_size"):
            brush.use_locked_size = "SCENE"
        if hasattr(brush, "unprojected_size"):
            # 注意: この後に px の size を書くと Blender が連動値を再計算して
            # mm 指定が上書きされるため、ページ基準では unprojected_size のみ書く。
            brush.unprojected_size = size_mm * 0.001
        if use_unified:
            if hasattr(ups, "use_locked_size"):
                ups.use_locked_size = "SCENE"
            if hasattr(ups, "unprojected_size"):
                ups.unprojected_size = size_mm * 0.001
        return
    size_px = max(1, int(data.get("size") or 1)) if "size" in data else None
    if hasattr(brush, "use_locked_size"):
        brush.use_locked_size = "VIEW"
    if size_px is not None and hasattr(brush, "size"):
        brush.size = size_px
    if use_unified:
        if hasattr(ups, "use_locked_size"):
            ups.use_locked_size = "VIEW"
        if size_px is not None and hasattr(ups, "size"):
            ups.size = size_px


def _apply_common_size_strength(
    brush, data: dict, *, honor_size_mode: bool = False, ups=None
) -> None:
    _apply_brush_size(brush, data, honor_size_mode=honor_size_mode, ups=ups)
    if "useSizePressure" in data and hasattr(brush, "use_pressure_size"):
        brush.use_pressure_size = bool(data.get("useSizePressure"))
    if "strength" in data and hasattr(brush, "strength"):
        strength = max(0.0, min(10.0, float(data.get("strength") or 0.0)))
        brush.strength = strength
        if ups is not None and bool(getattr(ups, "use_unified_strength", False)) and hasattr(ups, "strength"):
            ups.strength = strength
    if "useStrengthPressure" in data and hasattr(brush, "use_pressure_strength"):
        brush.use_pressure_strength = bool(data.get("useStrengthPressure"))


def _apply_settings_to_brush(brush, data: dict, *, paint=None) -> None:
    """アクティブブラシへプリセットの詳細設定を書き込む."""
    if brush is None:
        return
    tool = gp_tool_presets.tool_id(data)
    settings = getattr(brush, "gpencil_settings", None)
    if tool != "trim":
        _apply_common_size_strength(
            brush,
            data,
            honor_size_mode=tool in {"brush", "fill"},
            ups=_unified_settings(paint),
        )
    if tool == "brush":
        if "useSmoothStroke" in data and hasattr(brush, "use_smooth_stroke"):
            brush.use_smooth_stroke = bool(data.get("useSmoothStroke"))
        if "smoothStrokeFactor" in data and hasattr(brush, "smooth_stroke_factor"):
            brush.smooth_stroke_factor = max(0.5, min(0.99, float(data.get("smoothStrokeFactor") or 0.75)))
        if settings is not None:
            if "strokeType" in data:
                settings.stroke_type = gp_tool_presets.enum_value(
                    "strokeType", data.get("strokeType"), "STROKE"
                )
            if "capsType" in data:
                settings.caps_type = gp_tool_presets.enum_value(
                    "capsType", data.get("capsType"), "ROUND"
                )
            if "hardness" in data:
                settings.hardness = max(0.001, min(1.0, float(data.get("hardness") or 1.0)))
    elif tool == "fill" and settings is not None:
        if "fillDirection" in data:
            settings.fill_direction = gp_tool_presets.enum_value(
                "fillDirection", data.get("fillDirection"), "NORMAL"
            )
        if "fillSolver" in data:
            settings.fill_solver = gp_tool_presets.enum_value(
                "fillSolver", data.get("fillSolver"), "DELAUNAY"
            )
        if "fillFactor" in data:
            settings.fill_factor = max(0.05, min(8.0, float(data.get("fillFactor") or 1.0)))
        if "fillDilate" in data:
            settings.dilate = max(-40, min(40, int(data.get("fillDilate") or 0)))
        if "fillExtendMode" in data:
            settings.fill_extend_mode = gp_tool_presets.enum_value(
                "fillExtendMode", data.get("fillExtendMode"), "EXTEND"
            )
        if "fillExtendFactor" in data:
            settings.extend_stroke_factor = max(0.0, float(data.get("fillExtendFactor") or 0.0))
    elif tool == "erase" and settings is not None:
        if "eraserMode" in data:
            settings.eraser_mode = gp_tool_presets.enum_value(
                "eraserMode", data.get("eraserMode"), "HARD"
            )
        if "activeLayerOnly" in data:
            settings.use_active_layer_only = bool(data.get("activeLayerOnly"))
        if "keepCaps" in data:
            settings.use_keep_caps_eraser = bool(data.get("keepCaps"))
    elif tool == "trim" and settings is not None:
        # トリムはアクティブブラシの設定 (対象レイヤー / キャップ保持) を参照する
        if "activeLayerOnly" in data:
            settings.use_active_layer_only = bool(data.get("activeLayerOnly"))
        if "keepCaps" in data:
            settings.use_keep_caps_eraser = bool(data.get("keepCaps"))


def apply_gp_tool_preset(context, name: str, *, report=None) -> bool:
    """プリセット名を指定して Blender のツール状態へ適用する."""
    preset = gp_tool_presets.load_preset_by_name(name)
    if preset is None:
        if report is not None:
            report({"WARNING"}, f"プリセットが見つかりません: {name}")
        return False
    data = dict(preset.data)
    tool = gp_tool_presets.tool_id(data)
    required_mode, tool_idname = _TOOL_IDNAME[tool]
    if not _ensure_gp_mode(context, required_mode):
        if report is not None:
            report({"WARNING"}, "手描きレイヤーを選択してから適用してください")
        return False
    _set_active_tool(context, tool_idname)
    identifier = _brush_asset_identifier(data)
    if identifier is not None:
        _activate_brush_asset(context, identifier)
    paint = _paint_container(context, required_mode)
    brush = getattr(paint, "brush", None) if paint is not None else None
    _apply_settings_to_brush(brush, data, paint=paint)
    _logger.info("gp tool preset applied: %s (%s)", name, tool)
    return True


def snapshot_current_tool_settings(context) -> dict:
    """現在の Blender ツール状態からプリセット保存用データを作る."""
    data: dict = {"tool": "brush"}
    obj = getattr(getattr(context, "view_layer", None), "objects", None)
    obj = getattr(obj, "active", None) if obj is not None else None
    mode = getattr(obj, "mode", "") if obj is not None else ""

    if mode == _GP_SCULPT_MODE:
        paint = _paint_container(context, _GP_SCULPT_MODE)
        brush = getattr(paint, "brush", None) if paint is not None else None
        data["tool"] = "grab"
        # スカルプトは統一サイズ設定が既定のため、実際に効いている値を拾う
        _snapshot_size_strength(brush, data, ups=_unified_settings(paint))
        return data

    paint = _paint_container(context, _GP_PAINT_MODE)
    brush = getattr(paint, "brush", None) if paint is not None else None
    settings = getattr(brush, "gpencil_settings", None) if brush is not None else None
    tool_idname = ""
    if mode == _GP_PAINT_MODE:
        try:
            workspace_tool = context.workspace.tools.from_space_view3d_mode(
                _GP_PAINT_MODE, create=False
            )
            tool_idname = str(getattr(workspace_tool, "idname", "") or "")
        except Exception:  # noqa: BLE001
            tool_idname = ""

    if tool_idname == "builtin.trim":
        data["tool"] = "trim"
        if settings is not None:
            data["activeLayerOnly"] = bool(settings.use_active_layer_only)
            data["keepCaps"] = bool(settings.use_keep_caps_eraser)
        return data

    brush_type = str(getattr(brush, "gpencil_brush_type", "") or "") if brush is not None else ""
    if brush_type == "FILL" and settings is not None:
        data["tool"] = "fill"
        _snapshot_size_strength(brush, data, capture_size_mode=True)
        data["fillDirection"] = str(settings.fill_direction)
        data["fillSolver"] = str(settings.fill_solver)
        data["fillFactor"] = round(float(settings.fill_factor), 4)
        data["fillDilate"] = int(settings.dilate)
        data["fillExtendMode"] = str(settings.fill_extend_mode)
        data["fillExtendFactor"] = round(float(settings.extend_stroke_factor), 4)
        return data
    if brush_type == "ERASE" and settings is not None:
        data["tool"] = "erase"
        _snapshot_size_strength(brush, data)
        data["eraserMode"] = str(settings.eraser_mode)
        data["activeLayerOnly"] = bool(settings.use_active_layer_only)
        data["keepCaps"] = bool(settings.use_keep_caps_eraser)
        return data

    data["tool"] = "brush"
    if brush is not None:
        name = str(getattr(brush, "name", "") or "")
        data["brushAsset"] = name if name in _KNOWN_DRAW_BRUSHES else "Pencil"
        _snapshot_size_strength(brush, data, capture_size_mode=True)
        if hasattr(brush, "use_smooth_stroke"):
            data["useSmoothStroke"] = bool(brush.use_smooth_stroke)
        if hasattr(brush, "smooth_stroke_factor"):
            data["smoothStrokeFactor"] = round(float(brush.smooth_stroke_factor), 4)
        if settings is not None:
            data["strokeType"] = str(settings.stroke_type)
            data["capsType"] = str(settings.caps_type)
            data["hardness"] = round(float(settings.hardness), 4)
    return data


def _snapshot_size_strength(
    brush, data: dict, *, capture_size_mode: bool = False, ups=None
) -> None:
    if brush is None:
        return
    if hasattr(brush, "size"):
        data["size"] = int(brush.size)
    if capture_size_mode:
        locked = str(getattr(brush, "use_locked_size", "") or "")
        data["sizeMode"] = "SCENE" if locked == "SCENE" else "VIEW"
        if hasattr(brush, "unprojected_size"):
            data["sizeMm"] = round(float(brush.unprojected_size) * 1000.0, 3)
    if hasattr(brush, "use_pressure_size"):
        data["useSizePressure"] = bool(brush.use_pressure_size)
    if hasattr(brush, "strength"):
        data["strength"] = round(float(brush.strength), 4)
    if hasattr(brush, "use_pressure_strength"):
        data["useStrengthPressure"] = bool(brush.use_pressure_strength)
    if ups is not None and bool(getattr(ups, "use_unified_size", False)) and hasattr(ups, "size"):
        data["size"] = int(ups.size)
    if (
        ups is not None
        and bool(getattr(ups, "use_unified_strength", False))
        and hasattr(ups, "strength")
    ):
        data["strength"] = round(float(ups.strength), 4)


class BMANGA_OT_gp_tool_preset_add_local(Operator):
    """現在のツール設定を新しいグリースペンシルツールプリセットとして追加する."""

    bl_idname = "bmanga.gp_tool_preset_add_local"
    bl_label = "グリースペンシルツールプリセットを追加"
    bl_description = "現在のツール設定を、新しいプリセットとして追加します"
    bl_options = {"REGISTER"}

    preset_name: StringProperty(  # type: ignore[valid-type]
        name="プリセット名", default="新規グリースペンシルツールプリセット"
    )
    description: StringProperty(name="説明", default="")  # type: ignore[valid-type]

    def invoke(self, context, event):
        self.preset_name = gp_tool_presets.unique_preset_name(
            self.preset_name or "新規グリースペンシルツールプリセット"
        )
        return detail_popup.invoke_props_dialog(context, event, self)

    def execute(self, context):
        name = gp_tool_presets.unique_preset_name(
            self.preset_name.strip() or "新規グリースペンシルツールプリセット"
        )
        entry_data = snapshot_current_tool_settings(context)
        try:
            gp_tool_presets.save_local_preset(name, self.description, entry_data)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("gp_tool_preset_add_local failed")
            self.report({"ERROR"}, f"追加失敗: {exc}")
            return {"CANCELLED"}
        _set_gp_tool_preset_selector(context, name)
        self.report({"INFO"}, f"グリースペンシルツールプリセット追加: {name}")
        return {"FINISHED"}


class BMANGA_OT_gp_tool_preset_save(Operator):
    """現在のツール設定で選択中のプリセットを上書き保存する."""

    bl_idname = "bmanga.gp_tool_preset_save"
    bl_label = "グリースペンシルツールプリセットを上書き保存"
    bl_description = "現在のツール設定で選択中のプリセットを上書き保存します"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(_selected_gp_tool_preset_name(context))

    def execute(self, context):
        name = _selected_gp_tool_preset_name(context)
        if not name:
            self.report({"ERROR"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        preset = gp_tool_presets.load_preset_by_name(name)
        description = preset.description if preset is not None else ""
        entry_data = snapshot_current_tool_settings(context)
        try:
            gp_tool_presets.save_local_preset(name, description, entry_data)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("gp_tool_preset_save failed")
            self.report({"ERROR"}, f"上書き保存失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"グリースペンシルツールプリセット上書き保存: {name}")
        return {"FINISHED"}


class BMANGA_OT_gp_tool_preset_rename(Operator):
    """選択中のグリースペンシルツールプリセットを改名する."""

    bl_idname = "bmanga.gp_tool_preset_rename"
    bl_label = "グリースペンシルツールプリセットを改名"
    bl_options = {"REGISTER"}

    preset_name: StringProperty(name="現在の名前", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="新しい名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_gp_tool_preset_name(context))

    def invoke(self, context, event):
        selected = _selected_gp_tool_preset_name(context)
        self.preset_name = self.preset_name or selected
        self.new_name = self.new_name or self.preset_name
        return detail_popup.invoke_props_dialog(context, event, self)

    def execute(self, context):
        old_name = self.preset_name.strip() or _selected_gp_tool_preset_name(context)
        new_name = self.new_name.strip()
        if not new_name:
            self.report({"ERROR"}, "新しい名前を入力してください")
            return {"CANCELLED"}
        try:
            gp_tool_presets.rename_preset(old_name, new_name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("gp_tool_preset_rename failed")
            self.report({"ERROR"}, f"改名失敗: {exc}")
            return {"CANCELLED"}
        _set_gp_tool_preset_selector(context, new_name)
        self.report({"INFO"}, f"グリースペンシルツールプリセット改名: {old_name} → {new_name}")
        return {"FINISHED"}


class BMANGA_OT_gp_tool_preset_duplicate(Operator):
    """選択中のグリースペンシルツールプリセットを複製する."""

    bl_idname = "bmanga.gp_tool_preset_duplicate"
    bl_label = "グリースペンシルツールプリセットを複製"
    bl_options = {"REGISTER"}

    preset_name: StringProperty(name="複製元", default="")  # type: ignore[valid-type]
    new_name: StringProperty(name="複製後の名前", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_gp_tool_preset_name(context))

    def invoke(self, context, event):
        selected = _selected_gp_tool_preset_name(context)
        self.preset_name = self.preset_name or selected
        self.new_name = gp_tool_presets.unique_preset_name(
            self.new_name or f"{self.preset_name} コピー"
        )
        return detail_popup.invoke_props_dialog(context, event, self)

    def execute(self, context):
        source_name = self.preset_name.strip() or _selected_gp_tool_preset_name(context)
        new_name = self.new_name.strip()
        if not new_name:
            self.report({"ERROR"}, "新しい名前を入力してください")
            return {"CANCELLED"}
        try:
            gp_tool_presets.duplicate_preset(source_name, new_name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("gp_tool_preset_duplicate failed")
            self.report({"ERROR"}, f"複製失敗: {exc}")
            return {"CANCELLED"}
        _set_gp_tool_preset_selector(context, new_name)
        self.report({"INFO"}, f"グリースペンシルツールプリセット複製: {new_name}")
        return {"FINISHED"}


class BMANGA_OT_gp_tool_preset_delete(Operator):
    """選択中のグリースペンシルツールプリセットを削除する."""

    bl_idname = "bmanga.gp_tool_preset_delete"
    bl_label = "グリースペンシルツールプリセットを削除"
    bl_options = {"REGISTER"}

    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_gp_tool_preset_name(context))

    def invoke(self, context, event):
        self.preset_name = self.preset_name or _selected_gp_tool_preset_name(context)
        return detail_popup.invoke_confirm(context, event, self)

    def execute(self, context):
        name = self.preset_name.strip() or _selected_gp_tool_preset_name(context)
        if not name:
            self.report({"ERROR"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        all_presets = gp_tool_presets.list_all_presets()
        names = [preset.name for preset in all_presets]
        fallback = ""
        if name in names and len(names) > 1:
            idx = names.index(name)
            fallback = names[idx + 1] if idx + 1 < len(names) else names[idx - 1]
        try:
            gp_tool_presets.delete_preset(name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("gp_tool_preset_delete failed")
            self.report({"ERROR"}, f"削除失敗: {exc}")
            return {"CANCELLED"}
        if fallback:
            _set_gp_tool_preset_selector(context, fallback)
        self.report({"INFO"}, f"グリースペンシルツールプリセット削除: {name}")
        return {"FINISHED"}


class BMANGA_OT_gp_tool_preset_move(Operator):
    """選択中のグリースペンシルツールプリセットを並べ替える."""

    bl_idname = "bmanga.gp_tool_preset_move"
    bl_label = "グリースペンシルツールプリセットを並べ替え"
    bl_options = {"REGISTER"}

    preset_name: StringProperty(name="プリセット名", default="")  # type: ignore[valid-type]
    direction: StringProperty(name="方向", default="UP")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return bool(_selected_gp_tool_preset_name(context))

    def execute(self, context):
        name = self.preset_name.strip() or _selected_gp_tool_preset_name(context)
        if not name:
            self.report({"ERROR"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        try:
            gp_tool_presets.move_preset(name, self.direction)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"並べ替え失敗: {exc}")
            return {"CANCELLED"}
        _set_gp_tool_preset_selector(context, name)
        self.report({"INFO"}, f"グリースペンシルツールプリセット並べ替え: {name}")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_gp_tool_preset_add_local,
    BMANGA_OT_gp_tool_preset_save,
    BMANGA_OT_gp_tool_preset_rename,
    BMANGA_OT_gp_tool_preset_duplicate,
    BMANGA_OT_gp_tool_preset_delete,
    BMANGA_OT_gp_tool_preset_move,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
