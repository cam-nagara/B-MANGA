"""用紙設定 PropertyGroup.

work.json の ``paper`` セクションに対応するデータモデル。既定値は計画書
3.2.4「集英社マンガ誌汎用」プリセット (257×364mm / 600dpi) に合わせる。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)

from ..utils import log

_logger = log.get_logger(__name__)

_COLOR_MODE_ITEMS = (
    ("monochrome", "モノクロ", "2 値 (印刷入稿用途)"),
    ("grayscale", "グレースケール", "グレースケール"),
    ("rgb", "RGB", "RGB カラー"),
    ("cmyk", "CMYK", "CMYK カラー"),
)

_UNIT_ITEMS = (
    ("mm", "mm", "ミリメートル"),
    ("px", "px", "ピクセル"),
    ("inch", "inch", "インチ"),
)

_START_SIDE_ITEMS = (
    ("right", "右から", "1 ページ目は右ページ単独 (日本のマンガ・右綴じ)"),
    ("left", "左から", "1 ページ目は左ページ単独 (西洋本・左綴じ)"),
)

_READ_DIRECTION_ITEMS = (
    ("left", "左方向", "ページが左方向に進む (日本のマンガ既定)"),
    ("right", "右方向", "ページが右方向に進む (西洋本)"),
    ("down", "下方向", "ページが下方向に進む (縦スクロール)"),
)


def _tag_view3d_redraw(context) -> None:
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def _on_paper_visual_changed(_self, context) -> None:
    try:
        from ..core.work import get_work
        from ..utils import paper_guide_object

        work = get_work(context)
        scene = getattr(context, "scene", None) if context is not None else None
        if scene is not None and work is not None and work.loaded:
            paper_guide_object.regenerate_all_paper_guides(scene, work)
    except Exception:  # noqa: BLE001
        pass
    _tag_view3d_redraw(context)


def _on_paper_color_changed(self, context) -> None:
    """``paper_color`` 変更時に paper_bg Mesh のマテリアル色を即時反映.

    ``_ensure_paper_material`` は呼び直すたびに material のノード木 (Emission
    の Color) と ``mat.diffuse_color`` (Solid 表示用) を ``paper.paper_color``
    から更新するので、 既存の paper_bg Object の Material slot がそのまま
    新色を表示する (Mesh / Object 自体には触らない)。
    ``mat.update_tag()`` で depsgraph に材質変更を通知し、 Material Preview /
    Rendered だけでなく Solid モードのビューポートも即時に再描画する。
    """
    try:
        from ..utils import paper_bg_object as _pbg

        mat = _pbg._ensure_paper_material(self)
        if mat is not None:
            try:
                mat.update_tag()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        _logger.exception("paper_color update: material refresh failed")
    _tag_view3d_redraw(context)


def _on_paper_layout_changed(_self, context) -> None:
    try:
        from ..core.work import get_work
        from ..utils import page_grid, paper_bg_object, paper_guide_object

        work = get_work(context)
        if work is not None and work.loaded:
            page_grid.apply_page_collection_transforms(context, work)
            scene = getattr(context, "scene", None) if context is not None else None
            if scene is not None:
                paper_bg_object.regenerate_all_paper_bgs(scene, work)
                paper_guide_object.regenerate_all_paper_guides(scene, work)
    except Exception:  # noqa: BLE001
        pass
    _tag_view3d_redraw(context)


def _on_coma_border_width_changed(self, context) -> None:
    try:
        from ..core.work import get_work
        from ..utils import coma_border_object, coma_plane, page_file_scene, paper_guide_object

        work = get_work(context)
        scene = getattr(context, "scene", None) if context is not None else None
        if work is None or not work.loaded:
            return
        target_width = max(0.0, float(getattr(self, "coma_border_width_mm", 0.5) or 0.0))
        for page in getattr(work, "pages", []) or []:
            for coma in getattr(page, "comas", []) or []:
                border = getattr(coma, "border", None)
                if border is None:
                    continue
                if abs(float(getattr(border, "width_mm", 0.0) or 0.0) - target_width) > 1.0e-6:
                    border.width_mm = target_width
                if scene is not None and page_file_scene.is_current_page_edit_scene(scene, page.id):
                    coma_plane.ensure_coma_plane(scene, work, page, coma)
                    coma_border_object.ensure_coma_border_object(scene, work, page, coma)
        if scene is not None:
            paper_guide_object.regenerate_all_paper_guides(scene, work)
    except Exception:  # noqa: BLE001
        _logger.exception("coma border width update failed")
    _tag_view3d_redraw(context)


def _paper_dpi(self) -> int:
    try:
        return max(1, int(getattr(self, "dpi", 600) or 600))
    except Exception:  # noqa: BLE001
        return 600


def _mm_to_display_unit(self, mm: float) -> float:
    unit = str(getattr(self, "unit", "mm") or "mm")
    value = float(mm or 0.0)
    if unit == "px":
        return value / 25.4 * _paper_dpi(self)
    if unit == "inch":
        return value / 25.4
    return value


def _display_unit_to_mm(self, value: float) -> float:
    unit = str(getattr(self, "unit", "mm") or "mm")
    raw = float(value or 0.0)
    if unit == "px":
        return raw / _paper_dpi(self) * 25.4
    if unit == "inch":
        return raw * 25.4
    return raw


def _display_getter(mm_attr: str):
    def _get(self) -> float:
        return _mm_to_display_unit(self, getattr(self, mm_attr, 0.0))

    return _get


def _display_setter(mm_attr: str):
    def _set(self, value: float) -> None:
        setattr(self, mm_attr, _display_unit_to_mm(self, value))

    return _set


class BNamePaperSettings(bpy.types.PropertyGroup):
    """用紙寸法・解像度・基本枠・セーフライン設定."""

    # --- キャンバス全体 ---
    # 単位は B-Name 独自の ``unit`` プロパティで管理するため、Blender の
    # シーン単位に依存する ``unit="LENGTH"`` は使わない (FloatProperty の
    # 既定 ``unit="NONE"`` にする)。
    canvas_width_mm: FloatProperty(  # type: ignore[valid-type]
        name="幅",
        description="原稿用紙の幅 (裁ち落とし込み、mm)",
        default=257.00,
        min=1.0,
        soft_max=1000.0,
        update=_on_paper_layout_changed,
    )
    canvas_height_mm: FloatProperty(  # type: ignore[valid-type]
        name="高さ",
        description="原稿用紙の高さ (裁ち落とし込み、mm)",
        default=364.00,
        min=1.0,
        soft_max=1000.0,
        update=_on_paper_layout_changed,
    )
    dpi: IntProperty(  # type: ignore[valid-type]
        name="解像度 (dpi)",
        description="書き出し基準の解像度",
        default=600,
        min=72,
        soft_max=1200,
        update=_on_paper_layout_changed,
    )
    unit: EnumProperty(  # type: ignore[valid-type]
        name="単位",
        description="UI 表示上の単位",
        items=_UNIT_ITEMS,
        default="mm",
        update=_on_paper_visual_changed,
    )
    canvas_width_value: FloatProperty(  # type: ignore[valid-type]
        name="幅",
        description="現在の単位で表示・入力する原稿用紙の幅",
        default=257.00,
        min=0.0,
        soft_max=10000.0,
        precision=3,
        get=_display_getter("canvas_width_mm"),
        set=_display_setter("canvas_width_mm"),
    )
    canvas_height_value: FloatProperty(  # type: ignore[valid-type]
        name="高さ",
        description="現在の単位で表示・入力する原稿用紙の高さ",
        default=364.00,
        min=0.0,
        soft_max=10000.0,
        precision=3,
        get=_display_getter("canvas_height_mm"),
        set=_display_setter("canvas_height_mm"),
    )

    # --- 仕上がり (製本) ---
    finish_width_mm: FloatProperty(  # type: ignore[valid-type]
        name="幅",
        description="製本後の仕上がり幅 (mm)",
        default=221.81,
        min=1.0,
        soft_max=1000.0,
        update=_on_paper_layout_changed,
    )
    finish_height_mm: FloatProperty(  # type: ignore[valid-type]
        name="高さ",
        description="製本後の仕上がり高さ (mm)",
        default=328.78,
        min=1.0,
        soft_max=1000.0,
        update=_on_paper_layout_changed,
    )
    finish_width_value: FloatProperty(  # type: ignore[valid-type]
        name="幅",
        description="現在の単位で表示・入力する仕上がり幅",
        default=221.81,
        min=0.0,
        soft_max=10000.0,
        precision=3,
        get=_display_getter("finish_width_mm"),
        set=_display_setter("finish_width_mm"),
    )
    finish_height_value: FloatProperty(  # type: ignore[valid-type]
        name="高さ",
        description="現在の単位で表示・入力する仕上がり高さ",
        default=328.78,
        min=0.0,
        soft_max=10000.0,
        precision=3,
        get=_display_getter("finish_height_mm"),
        set=_display_setter("finish_height_mm"),
    )
    bleed_mm: FloatProperty(  # type: ignore[valid-type]
        name="裁ち落とし幅",
        description="仕上がり枠の外側に確保する塗り足し (mm)",
        default=7.00,
        min=0.0,
        soft_max=50.0,
        update=_on_paper_layout_changed,
    )
    bleed_value: FloatProperty(  # type: ignore[valid-type]
        name="裁ち落とし幅",
        description="現在の単位で表示・入力する裁ち落とし幅",
        default=7.00,
        min=0.0,
        soft_max=10000.0,
        precision=3,
        get=_display_getter("bleed_mm"),
        set=_display_setter("bleed_mm"),
    )

    # --- 基本枠 (内枠) ---
    inner_frame_width_mm: FloatProperty(  # type: ignore[valid-type]
        name="幅",
        description="本文領域の幅 (mm)",
        default=180.00,
        min=1.0,
        soft_max=500.0,
        update=_on_paper_layout_changed,
    )
    inner_frame_height_mm: FloatProperty(  # type: ignore[valid-type]
        name="高さ",
        description="本文領域の高さ (mm)",
        default=270.00,
        min=1.0,
        soft_max=500.0,
        update=_on_paper_layout_changed,
    )
    inner_frame_offset_x_mm: FloatProperty(  # type: ignore[valid-type]
        name="横オフセット",
        default=0.00,
        soft_min=-100.0,
        soft_max=100.0,
        update=_on_paper_layout_changed,
    )
    inner_frame_offset_y_mm: FloatProperty(  # type: ignore[valid-type]
        name="縦オフセット",
        default=0.00,
        soft_min=-100.0,
        soft_max=100.0,
        update=_on_paper_layout_changed,
    )
    inner_frame_width_value: FloatProperty(  # type: ignore[valid-type]
        name="幅",
        description="現在の単位で表示・入力する基本枠の幅",
        default=180.00,
        min=0.0,
        soft_max=10000.0,
        precision=3,
        get=_display_getter("inner_frame_width_mm"),
        set=_display_setter("inner_frame_width_mm"),
    )
    inner_frame_height_value: FloatProperty(  # type: ignore[valid-type]
        name="高さ",
        description="現在の単位で表示・入力する基本枠の高さ",
        default=270.00,
        min=0.0,
        soft_max=10000.0,
        precision=3,
        get=_display_getter("inner_frame_height_mm"),
        set=_display_setter("inner_frame_height_mm"),
    )
    inner_frame_offset_x_value: FloatProperty(  # type: ignore[valid-type]
        name="横オフセット",
        description="現在の単位で表示・入力する基本枠の横オフセット",
        default=0.00,
        soft_min=-10000.0,
        soft_max=10000.0,
        precision=3,
        get=_display_getter("inner_frame_offset_x_mm"),
        set=_display_setter("inner_frame_offset_x_mm"),
    )
    inner_frame_offset_y_value: FloatProperty(  # type: ignore[valid-type]
        name="縦オフセット",
        description="現在の単位で表示・入力する基本枠の縦オフセット",
        default=0.00,
        soft_min=-10000.0,
        soft_max=10000.0,
        precision=3,
        get=_display_getter("inner_frame_offset_y_mm"),
        set=_display_setter("inner_frame_offset_y_mm"),
    )
    coma_border_width_mm: FloatProperty(  # type: ignore[valid-type]
        name="コマ枠線幅 (mm)",
        description="新規コマと既存コマに使うコマ枠線の幅",
        default=0.5,
        min=0.0,
        soft_max=10.0,
        precision=3,
        update=_on_coma_border_width_changed,
    )

    # --- セーフライン (天/地/ノド/小口) ---
    safe_top_mm: FloatProperty(  # type: ignore[valid-type]
        name="天",
        default=17.49,
        min=0.0,
        soft_max=100.0,
        update=_on_paper_visual_changed,
    )
    safe_bottom_mm: FloatProperty(  # type: ignore[valid-type]
        name="地",
        default=17.49,
        min=0.0,
        soft_max=100.0,
        update=_on_paper_visual_changed,
    )
    safe_gutter_mm: FloatProperty(  # type: ignore[valid-type]
        name="ノド",
        description="綴じ側のセーフライン (mm)",
        default=20.90,
        min=0.0,
        soft_max=100.0,
        update=_on_paper_visual_changed,
    )
    safe_fore_edge_mm: FloatProperty(  # type: ignore[valid-type]
        name="小口",
        description="綴じと反対側のセーフライン (mm)",
        default=17.23,
        min=0.0,
        soft_max=100.0,
        update=_on_paper_visual_changed,
    )
    safe_top_value: FloatProperty(  # type: ignore[valid-type]
        name="天",
        description="現在の単位で表示・入力する天のセーフライン",
        default=17.49,
        min=0.0,
        soft_max=10000.0,
        precision=3,
        get=_display_getter("safe_top_mm"),
        set=_display_setter("safe_top_mm"),
    )
    safe_bottom_value: FloatProperty(  # type: ignore[valid-type]
        name="地",
        description="現在の単位で表示・入力する地のセーフライン",
        default=17.49,
        min=0.0,
        soft_max=10000.0,
        precision=3,
        get=_display_getter("safe_bottom_mm"),
        set=_display_setter("safe_bottom_mm"),
    )
    safe_gutter_value: FloatProperty(  # type: ignore[valid-type]
        name="ノド",
        description="現在の単位で表示・入力するノドのセーフライン",
        default=20.90,
        min=0.0,
        soft_max=10000.0,
        precision=3,
        get=_display_getter("safe_gutter_mm"),
        set=_display_setter("safe_gutter_mm"),
    )
    safe_fore_edge_value: FloatProperty(  # type: ignore[valid-type]
        name="小口",
        description="現在の単位で表示・入力する小口のセーフライン",
        default=17.23,
        min=0.0,
        soft_max=10000.0,
        precision=3,
        get=_display_getter("safe_fore_edge_mm"),
        set=_display_setter("safe_fore_edge_mm"),
    )

    # --- 色・線数 ---
    color_mode: EnumProperty(  # type: ignore[valid-type]
        name="基本表現色",
        items=_COLOR_MODE_ITEMS,
        default="monochrome",
    )
    default_line_count: FloatProperty(  # type: ignore[valid-type]
        name="基本線数",
        description="モノクロ書き出し時の網点線数",
        default=60.00,
        min=10.0,
        soft_max=200.0,
    )
    paper_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="用紙色",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_paper_color_changed,
    )
    display_alpha: FloatProperty(  # type: ignore[valid-type]
        name="紙面表示アルファ",
        description="ビューポート上の紙面 (キャンバス色) の表示透明度. 0 で非表示、1 で完全不透明",
        default=0.85,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    show_canvas_frame: BoolProperty(  # type: ignore[valid-type]
        name="用紙枠",
        description="キャンバス外周のガイド線を表示",
        default=True,
        update=_on_paper_visual_changed,
    )
    show_guides: BoolProperty(  # type: ignore[valid-type]
        name="用紙ガイド",
        description="用紙のガイド線をまとめて表示",
        default=True,
        update=_on_paper_visual_changed,
    )
    show_bleed_frame: BoolProperty(  # type: ignore[valid-type]
        name="裁ち落とし枠",
        description="裁ち落とし枠のガイド線を表示",
        default=True,
        update=_on_paper_visual_changed,
    )
    show_finish_frame: BoolProperty(  # type: ignore[valid-type]
        name="仕上がり枠",
        description="仕上がり枠のガイド線を表示",
        default=True,
        update=_on_paper_visual_changed,
    )
    show_inner_frame: BoolProperty(  # type: ignore[valid-type]
        name="基本枠",
        description="基本枠のガイド線を表示",
        default=True,
        update=_on_paper_visual_changed,
    )
    show_safe_line: BoolProperty(  # type: ignore[valid-type]
        name="セーフライン",
        description="セーフラインのガイド線を表示",
        default=True,
        update=_on_paper_visual_changed,
    )
    show_trim_marks: BoolProperty(  # type: ignore[valid-type]
        name="トンボ",
        description="トンボを表示",
        default=True,
        update=_on_paper_visual_changed,
    )
    color_profile: StringProperty(  # type: ignore[valid-type]
        name="カラープロファイル",
        description="表示/書き出し用 ICC プロファイル名",
        default="sRGB IEC61966-2.1",
    )

    # --- 綴じ / 読む方向 ---
    start_side: EnumProperty(  # type: ignore[valid-type]
        name="開始ページの位置",
        description="1 ページ目が見開きの左右どちらに来るか",
        items=_START_SIDE_ITEMS,
        default="left",
        update=_on_paper_layout_changed,
    )
    read_direction: EnumProperty(  # type: ignore[valid-type]
        name="読む方向",
        description="overview で次の見開きペアが置かれる方向",
        items=_READ_DIRECTION_ITEMS,
        default="left",
        update=_on_paper_layout_changed,
    )

    # --- プリセット参照 ---
    preset_name: StringProperty(  # type: ignore[valid-type]
        name="使用プリセット名",
        default="集英社マンガ誌汎用",
    )


_CLASSES = (BNamePaperSettings,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("paper registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
