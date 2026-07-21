"""グリースペンシルツールプリセットの設定モデル.

他ツールのプリセットが「レイヤーの設定」を保存するのに対し、グリース
ペンシルツールプリセットは「Blenderのドローモード各ツールの設定」を保存
する。1プリセット = 1機能 (ブラシ / フィル / トリム / 消しゴム / グラブ)
+ その機能の詳細設定。実レイヤーへは何も適用せず、適用先は Blender の
ツール・アクティブブラシである (operators/gp_tool_preset_op.py)。
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import PropertyGroup

# 機能 → (Blenderツール idname, 使用モード)。トリムはブラシを持たない。
GP_TOOL_ITEMS = (
    ("brush", "ブラシ", "ドローモードのブラシで線を描きます"),
    ("fill", "フィル", "囲まれた領域を塗りつぶします"),
    ("trim", "トリム", "ストロークを切り取ります"),
    ("erase", "消しゴム", "ストロークを消します"),
    ("grab", "グラブ", "スカルプトモードのグラブブラシでストロークを動かします"),
)

# ブラシ機能で使える Blender 同梱 (Essentials) のドローブラシ。
# 識別子はブラシアセットのデータ名そのまま (画面のブラシ一覧と同じ表記)。
GP_DRAW_BRUSH_ITEMS = (
    ("Pencil", "Pencil", "鉛筆風の標準ブラシ"),
    ("Pencil Soft", "Pencil Soft", "柔らかい鉛筆風ブラシ"),
    ("Pen", "Pen", "均一な太さのペン"),
    ("Ink Pen", "Ink Pen", "筆圧で太さが変わるインクペン"),
    ("Ink Pen Rough", "Ink Pen Rough", "かすれのあるインクペン"),
    ("Marker Bold", "Marker Bold", "太いマーカー"),
    ("Marker Chisel", "Marker Chisel", "平筆風のマーカー"),
    ("Airbrush", "Airbrush", "エアブラシ"),
)

GP_ERASER_MODE_ITEMS = (
    ("HARD", "ポイント", "触れた点を削除して消します"),
    ("SOFT", "ディゾルブ", "不透明度を下げながら消します"),
    ("STROKE", "ストローク", "触れたストローク全体を消します"),
)

GP_STROKE_TYPE_ITEMS = (
    ("STROKE", "ストローク", "線だけを描きます"),
    ("FILL", "フィル", "塗りだけを描きます"),
    ("BOTH", "両方", "線と塗りを両方描きます"),
)

GP_CAPS_TYPE_ITEMS = (
    ("ROUND", "丸い", "線の端を丸くします"),
    ("FLAT", "フラット", "線の端を平らにします"),
)

GP_FILL_DIRECTION_ITEMS = (
    ("NORMAL", "通常", "クリックした領域を塗りつぶします"),
    ("INVERT", "反転", "クリックした領域の外側を塗りつぶします"),
)

GP_FILL_SOLVER_ITEMS = (
    ("DELAUNAY", "ドロネー", "形状ベースの標準的な塗りつぶし計算"),
    ("PIXEL", "ピクセル", "画面ピクセルベースの塗りつぶし計算 (精度・拡張が使えます)"),
)

GP_FILL_EXTEND_MODE_ITEMS = (
    ("EXTEND", "延長", "線を延長してすき間を閉じます"),
    ("RADIUS", "半径", "線の端点同士を円で閉じます"),
)

GP_SIZE_MODE_ITEMS = (
    (
        "SCENE",
        "ページ基準 (mm)",
        "ズームに関係なく、ページ上で常に同じ太さで描きます (Blender同梱ブラシの既定)",
    ),
    (
        "VIEW",
        "画面基準 (px)",
        "画面上のピクセル数で太さを決めます (ズームすると描かれる太さが変わります)",
    ),
)


class BMangaGpToolSettings(PropertyGroup):
    """グリースペンシルツールプリセット1件分の編集用設定."""

    tool: EnumProperty(  # type: ignore[valid-type]
        name="機能",
        description="このプリセットで使うグリースペンシルの機能",
        items=GP_TOOL_ITEMS,
        default="brush",
    )
    brush_asset: EnumProperty(  # type: ignore[valid-type]
        name="使用ブラシ",
        description="ブラシ機能で使う Blender 同梱ブラシ",
        items=GP_DRAW_BRUSH_ITEMS,
        default="Pencil",
    )
    size_mode: EnumProperty(  # type: ignore[valid-type]
        name="サイズの基準",
        description="ブラシ・フィルの太さをページ基準 (mm) と画面基準 (px) のどちらで決めるか",
        items=GP_SIZE_MODE_ITEMS,
        default="SCENE",
    )
    size_mm: FloatProperty(  # type: ignore[valid-type]
        name="サイズ (mm)",
        description="ページ上での太さ (ミリメートル)",
        default=1.0,
        min=0.01,
        soft_max=50.0,
        max=1000.0,
        precision=2,
    )
    size: IntProperty(  # type: ignore[valid-type]
        name="サイズ (px)",
        description="ブラシの太さ (画面ピクセル)",
        default=14,
        min=1,
        soft_max=1000,
        max=10000,
        subtype="PIXEL",
    )
    use_size_pressure: BoolProperty(  # type: ignore[valid-type]
        name="筆圧サイズ",
        description="ペンの筆圧でサイズを変化させます",
        default=True,
    )
    strength: FloatProperty(  # type: ignore[valid-type]
        name="強さ",
        description="ストロークの不透明度・効果の強さ",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    use_strength_pressure: BoolProperty(  # type: ignore[valid-type]
        name="筆圧強さ",
        description="ペンの筆圧で強さを変化させます",
        default=False,
    )
    stroke_type: EnumProperty(  # type: ignore[valid-type]
        name="ストロークタイプ",
        description="描いた線を「線」「塗り」のどちらとして扱うか",
        items=GP_STROKE_TYPE_ITEMS,
        default="STROKE",
    )
    caps_type: EnumProperty(  # type: ignore[valid-type]
        name="キャップ",
        description="線の端の形",
        items=GP_CAPS_TYPE_ITEMS,
        default="ROUND",
    )
    hardness: FloatProperty(  # type: ignore[valid-type]
        name="硬さ",
        description="ブラシの縁のぼかし具合 (1で縁までくっきり)",
        default=1.0,
        min=0.001,
        max=1.0,
        subtype="FACTOR",
    )
    use_smooth_stroke: BoolProperty(  # type: ignore[valid-type]
        name="手ブレ補正",
        description="カーソルを遅らせて線を滑らかにします (安定化ストローク)",
        default=False,
    )
    smooth_stroke_factor: FloatProperty(  # type: ignore[valid-type]
        name="補正の強さ",
        description="手ブレ補正の強さ",
        default=0.75,
        min=0.5,
        max=0.99,
        subtype="FACTOR",
    )
    fill_direction: EnumProperty(  # type: ignore[valid-type]
        name="方向",
        description="塗りつぶす領域の向き",
        items=GP_FILL_DIRECTION_ITEMS,
        default="NORMAL",
    )
    fill_solver: EnumProperty(  # type: ignore[valid-type]
        name="計算方式",
        description="塗りつぶしの計算方式",
        items=GP_FILL_SOLVER_ITEMS,
        default="DELAUNAY",
    )
    fill_factor: FloatProperty(  # type: ignore[valid-type]
        name="精度",
        description="ピクセル方式の塗りつぶし精度 (大きいほど精密)",
        default=1.0,
        min=0.05,
        max=8.0,
    )
    fill_dilate: IntProperty(  # type: ignore[valid-type]
        name="拡張",
        description="塗りつぶし領域を広げる/狭めるピクセル数",
        default=1,
        min=-40,
        max=40,
        subtype="PIXEL",
    )
    fill_extend_mode: EnumProperty(  # type: ignore[valid-type]
        name="すき間閉じモード",
        description="閉じていない線のすき間をどう閉じて塗りつぶすか",
        items=GP_FILL_EXTEND_MODE_ITEMS,
        default="EXTEND",
    )
    fill_extend_factor: FloatProperty(  # type: ignore[valid-type]
        name="すき間閉じサイズ",
        description="すき間を自動で閉じる長さ (0で無効)",
        default=0.0,
        min=0.0,
        soft_max=10.0,
    )
    eraser_mode: EnumProperty(  # type: ignore[valid-type]
        name="消しゴムモード",
        description="消しゴムの消し方",
        items=GP_ERASER_MODE_ITEMS,
        default="HARD",
    )
    use_active_layer_only: BoolProperty(  # type: ignore[valid-type]
        name="アクティブレイヤーのみ",
        description="選択中のレイヤーのストロークだけを対象にします",
        default=False,
    )
    use_keep_caps: BoolProperty(  # type: ignore[valid-type]
        name="キャップを保持",
        description="切断・消去した端の形を保ちます",
        default=False,
    )


_CLASSES = (BMangaGpToolSettings,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


__all__ = [
    "BMangaGpToolSettings",
    "GP_CAPS_TYPE_ITEMS",
    "GP_DRAW_BRUSH_ITEMS",
    "GP_ERASER_MODE_ITEMS",
    "GP_FILL_DIRECTION_ITEMS",
    "GP_FILL_EXTEND_MODE_ITEMS",
    "GP_FILL_SOLVER_ITEMS",
    "GP_SIZE_MODE_ITEMS",
    "GP_STROKE_TYPE_ITEMS",
    "GP_TOOL_ITEMS",
    "register",
    "unregister",
]
