"""コマエントリ (ComaEntry) PropertyGroup.

page.json のコマリストに対応。cNN.blend の実体本体は Blender API
側で管理し、ここではメタデータ (形状/Z順序/枠線/フチ/リンク参照等) を
保持する。

計画書 3.2.5 / 4.7 参照。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..utils import log
from .coma_border import (
    BMangaComaBorder,
    BMangaComaWhiteMargin,
)

_logger = log.get_logger(__name__)


_SHAPE_TYPE_ITEMS = (
    ("rect", "矩形", ""),
    ("polygon", "多角形", ""),
    ("bezier", "曲線", ""),
    ("freeform", "フリーフォーム", ""),
)


def _coma_number_get(self) -> int:
    display_number = int(getattr(self, "display_number", 0) or 0)
    if display_number > 0:
        return display_number
    try:
        from ..utils import coma_id_edit

        return coma_id_edit.coma_number_from_id(
            str(getattr(self, "coma_id", "") or getattr(self, "id", "") or "")
        )
    except Exception:  # noqa: BLE001
        return 1


def _coma_number_set(self, value: int) -> None:
    try:
        from ..utils import coma_id_edit

        coma_id_edit.set_coma_display_number(bpy.context, self, int(value))
    except Exception:  # noqa: BLE001
        _logger.exception("coma number update failed")


def _tag_view3d_redraw(context) -> None:
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def _on_coma_background_color_changed(self, context) -> None:
    try:
        from ..core.mode import MODE_COMA, get_mode
        from ..utils import coma_camera

        scene = getattr(context, "scene", None)
        if scene is not None and get_mode(context) == MODE_COMA:
            stem = str(getattr(scene, "bmanga_current_coma_id", "") or "")
            if stem == str(getattr(self, "coma_id", "") or ""):
                coma_camera.sync_world_background_color(context, panel=self)
    except Exception:  # noqa: BLE001
        pass
    # コマ Collection 直下の coma_plane Mesh の Material 色を即時反映
    try:
        from ..utils import coma_plane as _cp

        _cp.on_coma_background_color_changed(self)
    except Exception:  # noqa: BLE001
        pass
    _tag_view3d_redraw(context)


def _on_coma_visible_changed(_self, context) -> None:
    try:
        from ..utils import coma_plane as _cp

        _cp.on_coma_geometry_changed(_self)
    except Exception:  # noqa: BLE001
        pass
    _tag_view3d_redraw(context)


def _on_coma_paper_visible_changed(_self, context) -> None:
    try:
        from ..utils import coma_plane as _cp

        _cp.on_coma_paper_visible_changed(_self)
    except Exception:  # noqa: BLE001
        pass
    _tag_view3d_redraw(context)


def _on_coma_blend_template_path_changed(self, _context) -> None:
    try:
        self.coma_blend_template_needs_apply = True
    except Exception:  # noqa: BLE001
        pass


def _on_coma_geometry_changed(self, context) -> None:
    """``rect_*_mm`` 変更で coma_plane Mesh を即時更新.

    update callback で呼ぶことで、 枠線辺ドラッグ / 三角ハンドル拡張 /
    レイヤー移動ツール等、 個別 operator に同期コールを散らす必要を無くす
    (rect_*_mm を書き換えれば常に Mesh が追従する仕組み)。
    """
    try:
        from ..utils import coma_plane as _cp

        _cp.on_coma_geometry_changed(self)
    except Exception:  # noqa: BLE001
        pass
    _tag_view3d_redraw(context)


def _on_coma_vertex_changed(self, context) -> None:
    """``BMangaComaVertex.x_mm`` / ``y_mm`` 変更で coma_plane Mesh を即時更新."""
    try:
        from ..utils import coma_plane as _cp

        _cp.on_vertex_changed(self)
    except Exception:  # noqa: BLE001
        pass
    _tag_view3d_redraw(context)


class BMangaComaVertex(bpy.types.PropertyGroup):
    """コマ枠の頂点 (mm)."""

    x_mm: FloatProperty(  # type: ignore[valid-type]
        name="X", default=0.0, update=_on_coma_vertex_changed
    )
    y_mm: FloatProperty(  # type: ignore[valid-type]
        name="Y", default=0.0, update=_on_coma_vertex_changed
    )


class BMangaLayerRef(bpy.types.PropertyGroup):
    """作画レイヤー ID 参照 (Grease Pencil / 画像レイヤー / フキダシ)."""

    layer_id: StringProperty(name="Layer ID", default="")  # type: ignore[valid-type]


class BMangaComaEntry(bpy.types.PropertyGroup):
    """コマ 1 件分のメタデータ (cNN.json 相当)."""

    # --- 識別子 ---
    id: StringProperty(  # type: ignore[valid-type]
        name="コマ ID",
        description="cNN 形式のコマID (2 桁ゼロパディング)",
        default="",
    )
    title: StringProperty(  # type: ignore[valid-type]
        name="表示名",
        default="",
    )
    coma_id: StringProperty(  # type: ignore[valid-type]
        name="ファイル stem",
        description="cNN (ファイル名のベース)",
        default="",
    )
    display_number: IntProperty(  # type: ignore[valid-type]
        name="表示番号",
        default=0,
        min=0,
        options={"HIDDEN"},
    )
    coma_number: IntProperty(  # type: ignore[valid-type]
        name="コマ番号",
        description="レイヤー一覧に表示するコマ番号。並び順やファイル名は変えません",
        min=1,
        soft_max=999,
        get=_coma_number_get,
        set=_coma_number_set,
    )
    coma_blend_template_path: StringProperty(  # type: ignore[valid-type]
        name="コマ用blendファイル",
        description=(
            "このコマに使う .blend。変更後は次に開く時にこのコマへコピーする。"
            "空なら未作成コマで作品またはプリファレンスの設定を使う"
        ),
        default="",
        subtype="FILE_PATH",
        update=_on_coma_blend_template_path_changed,
    )
    coma_blend_template_needs_apply: BoolProperty(  # type: ignore[valid-type]
        name="コマ用blendファイル変更未適用",
        default=False,
        options={"HIDDEN"},
    )

    # --- 形状 ---
    shape_type: EnumProperty(  # type: ignore[valid-type]
        name="形状",
        items=_SHAPE_TYPE_ITEMS,
        default="rect",
    )
    vertices: CollectionProperty(type=BMangaComaVertex)  # type: ignore[valid-type]

    # 矩形ショートカット (shape_type='rect' のときに使用)
    rect_x_mm: FloatProperty(  # type: ignore[valid-type]
        name="X", default=0.0, update=_on_coma_geometry_changed
    )
    rect_y_mm: FloatProperty(  # type: ignore[valid-type]
        name="Y", default=0.0, update=_on_coma_geometry_changed
    )
    rect_width_mm: FloatProperty(  # type: ignore[valid-type]
        name="幅", default=50.0, min=0.1, update=_on_coma_geometry_changed
    )
    rect_height_mm: FloatProperty(  # type: ignore[valid-type]
        name="高さ", default=50.0, min=0.1, update=_on_coma_geometry_changed
    )

    # --- Z順序・重なりくり抜き ---
    z_order: IntProperty(  # type: ignore[valid-type]
        name="Z順序",
        description="同ページ内のコマ重なり順 (大きいほど手前)",
        default=0,
    )
    overlap_clipping: BoolProperty(  # type: ignore[valid-type]
        name="重なり処理",
        description="手前のコマが重なる範囲を処理する",
        default=True,
    )
    visible: BoolProperty(  # type: ignore[valid-type]
        name="表示",
        description="このコマ枠とプレビューを表示する",
        default=True,
        update=_on_coma_visible_changed,
    )
    paper_visible: BoolProperty(  # type: ignore[valid-type]
        name="背景",
        description="このコマの背景面を表示する",
        default=True,
        update=_on_coma_paper_visible_changed,
    )
    selected: BoolProperty(  # type: ignore[valid-type]
        name="マルチ選択",
        default=False,
        options={"SKIP_SAVE"},
    )
    background_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="背景色",
        description="コマ内側に敷く背景色",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_coma_background_color_changed,
    )

    # --- 枠線・フチ ---
    border: PointerProperty(type=BMangaComaBorder)  # type: ignore[valid-type]
    white_margin: PointerProperty(type=BMangaComaWhiteMargin)  # type: ignore[valid-type]

    # --- 紐づけ ---
    layer_refs: CollectionProperty(type=BMangaLayerRef)  # type: ignore[valid-type]
    coma_gap_vertical_mm: FloatProperty(  # type: ignore[valid-type]
        name="上下スキマ",
        default=-1.0,
        description="未設定時は作品共通ルールを使う",
    )
    coma_gap_horizontal_mm: FloatProperty(  # type: ignore[valid-type]
        name="左右スキマ",
        default=-1.0,
        description="未設定時は作品共通ルールを使う",
    )


_CLASSES = (
    BMangaComaVertex,
    BMangaLayerRef,
    BMangaComaEntry,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("panel registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
