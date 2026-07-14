"""作品データの集約 PropertyGroup.

work.json 全体を Blender 内で保持する root コンテナ。Scene.bmanga_work に
PointerProperty で attach する。

依存順: 参照先 (paper / work_info / safe_area_overlay / page) を先に
register しておくこと。core/__init__.py が順序を保証する。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..utils import layer_uid, log
from .balloon import BMangaBalloonEntry
from .coma import BMangaComaEntry
from .layer_folder import BMangaLayerFolder
from .page import BMangaPageEntry
from .paper import BMangaPaperSettings
from .safe_area_overlay import BMangaSafeAreaOverlay
from .text_entry import BMangaTextEntry
from .work_info import BMangaNombre, BMangaWorkInfo

_logger = log.get_logger(__name__)


def _on_active_page_index_changed(_self, context) -> None:
    try:
        from ..utils import page_content_visibility

        page_content_visibility.schedule_apply(context)
    except Exception:  # noqa: BLE001
        pass


class BMangaComaGap(bpy.types.PropertyGroup):
    """コマ間隔ルール (作品共通、計画書 3.2.5.4).

    既定値: 上下 7.3mm / 左右 2.1mm。値は mm 単位。
    Blender のシーン単位に依存しないよう unit は明示せず、UI 表示でも
    名前に "(mm)" を含めて単位を明示する。
    """

    vertical_mm: FloatProperty(  # type: ignore[valid-type]
        name="上下スキマ (mm)",
        description="コマとコマの上下の間隔（mm）",
        default=7.3,
        min=0.0,
        soft_max=50.0,
    )
    horizontal_mm: FloatProperty(  # type: ignore[valid-type]
        name="左右スキマ (mm)",
        description="コマとコマの左右の間隔（mm）",
        default=2.1,
        min=0.0,
        soft_max=50.0,
    )


class BMangaWorkData(bpy.types.PropertyGroup):
    """作品 1 件分のデータ (.bmanga フォルダ 1 個分)."""

    # --- メタ ---
    loaded: BoolProperty(  # type: ignore[valid-type]
        name="作品ロード済み",
        default=False,
    )
    detail_data_version: IntProperty(  # type: ignore[valid-type]
        name="作品詳細データ形式版",
        description="手描き・効果線・レイヤーリンクを含む作品詳細データの形式版",
        default=layer_uid.CURRENT_DETAIL_DATA_VERSION,
        min=0,
        options={"HIDDEN"},
    )
    work_dir: StringProperty(  # type: ignore[valid-type]
        name="作品ディレクトリ",
        description="MyWork.bmanga/ のフルパス",
        default="",
        subtype="DIR_PATH",
    )
    coma_blend_template_path: StringProperty(  # type: ignore[valid-type]
        name="コマblendテンプレート",
        description=(
            "新規 cNN.blend 作成時に初回コピーする .blend。"
            "空ならB-MANGA標準の空コマシーンを作成"
        ),
        default="",
        subtype="FILE_PATH",
    )
    page_preview_scale_percentage: FloatProperty(  # type: ignore[valid-type]
        name="ページ一覧用コマ画像縮小率",
        description="ページ一覧に表示するコマ画像PNGの縮小率",
        default=12.5,
        min=1.0,
        max=100.0,
        subtype="PERCENTAGE",
    )
    auto_render_coma_thumb_on_return: BoolProperty(  # type: ignore[valid-type]
        name="ページ一覧に戻る時にコマ画像を更新",
        description="コマ用blendファイルからページ一覧に戻る時、表示用のコマ画像を自動レンダリングします",
        default=True,
    )
    view_overlay_enabled: BoolProperty(  # type: ignore[valid-type]
        name="オーバーレイ表示",
        description="ページ番号・作品情報・選択枠・編集ハンドルなどの補助表示を切り替えます",
        default=True,
    )
    view_overview_cols: IntProperty(  # type: ignore[valid-type]
        name="一覧の列数",
        description="全ページ一覧時の横方向のページ数",
        default=8,
        min=2,
    )
    view_overview_gap_mm: FloatProperty(  # type: ignore[valid-type]
        name="一覧のページ間隔",
        description="全ページ一覧時のページ同士の余白（mm）",
        default=30.0,
        min=0.0,
    )
    view_page_preview_enabled: BoolProperty(  # type: ignore[valid-type]
        name="ページ一覧表示",
        description="ページ編集中に、他のページを軽い縮小画像で表示します",
        default=True,
    )
    view_page_preview_page_radius: IntProperty(  # type: ignore[valid-type]
        name="旧ページ一覧半径",
        default=3,
        min=0,
        options={"HIDDEN"},
    )
    view_page_preview_range_mode: StringProperty(  # type: ignore[valid-type]
        name="ページ一覧表示範囲",
        description="ページ一覧を全ページ表示するか、現在ページの前後だけ表示するか (ALL / NEAR)",
        default="ALL",
    )
    view_page_preview_resolution_percentage: FloatProperty(  # type: ignore[valid-type]
        name="画像解像度",
        description="ページプレビュー画像の細かさ。ページ実解像度に対する割合(%)で指定",
        default=25.0,
        min=5.0,
        max=200.0,
        subtype="PERCENTAGE",
    )
    view_page_browser_position: StringProperty(  # type: ignore[valid-type]
        name="ページ一覧の位置",
        description="ページ一覧専用ビューを表示する位置",
        default="LEFT",
    )
    view_page_browser_size: FloatProperty(  # type: ignore[valid-type]
        name="ページ一覧の幅",
        description="ページ一覧専用ビューの分割比率",
        default=0.28,
        min=0.12,
        max=0.5,
    )
    view_page_browser_fit: BoolProperty(  # type: ignore[valid-type]
        name="フィット",
        description="ページ一覧ビューをパネルの縦横比に合わせて表示します",
        default=True,
    )

    # --- 各セクション ---
    work_info: PointerProperty(type=BMangaWorkInfo, description="作品の書誌情報 (作品名・話数・作者名など)")  # type: ignore[valid-type]
    nombre: PointerProperty(type=BMangaNombre, description="ノンブル (ページ番号) の表示設定")  # type: ignore[valid-type]
    paper: PointerProperty(type=BMangaPaperSettings, description="用紙サイズ・仕上がり枠などの設定")  # type: ignore[valid-type]
    safe_area_overlay: PointerProperty(type=BMangaSafeAreaOverlay, description="セーフエリアのオーバーレイ表示設定")  # type: ignore[valid-type]
    coma_gap: PointerProperty(type=BMangaComaGap, description="コマ間隔の既定ルール")  # type: ignore[valid-type]

    # --- ページ一覧 ---
    pages: CollectionProperty(type=BMangaPageEntry, description="作品に含まれるページの一覧")  # type: ignore[valid-type]
    # フキダシ番号の採番カウンター (作品全体で単調増加)。詳細未読込の
    # ページが居ても番号が衝突しないよう、過去に使った最大番号を記憶する。
    balloon_id_counter: IntProperty(default=0, min=0, options={"HIDDEN"})  # type: ignore[valid-type]
    active_page_index: IntProperty(  # type: ignore[valid-type]
        name="アクティブページ",
        description="現在編集中のページの番号",
        default=-1,
        min=-1,
        update=_on_active_page_index_changed,
    )

    # --- ページ外レイヤー ---
    shared_balloons: CollectionProperty(type=BMangaBalloonEntry, description="どのページにも属さない共有フキダシの一覧")  # type: ignore[valid-type]
    shared_texts: CollectionProperty(type=BMangaTextEntry, description="どのページにも属さない共有テキストの一覧")  # type: ignore[valid-type]
    shared_comas: CollectionProperty(type=BMangaComaEntry, description="どのページにも属さない共有コマの一覧")  # type: ignore[valid-type]
    layer_folders: CollectionProperty(type=BMangaLayerFolder, description="レイヤー一覧のフォルダ構成")  # type: ignore[valid-type]


# ----- Scene attach ヘルパ -----


def get_work(context: bpy.types.Context | None = None) -> BMangaWorkData | None:
    """現在のシーンに紐づく BMangaWorkData を返す.

    Scene に PointerProperty が attach されていなければ None。
    """
    ctx = context or bpy.context
    scene = getattr(ctx, "scene", None)
    if scene is None:
        return None
    return getattr(scene, "bmanga_work", None)


def get_active_page(context: bpy.types.Context | None = None) -> BMangaPageEntry | None:
    work = get_work(context)
    if work is None or not work.loaded:
        return None
    idx = work.active_page_index
    if idx < 0 or idx >= len(work.pages):
        return None
    return work.pages[idx]


def find_page_by_id(work: BMangaWorkData | None, page_id: str) -> BMangaPageEntry | None:
    if work is None or not work.loaded or not page_id:
        return None
    for page in work.pages:
        if getattr(page, "id", "") == page_id:
            return page
    return None


_CLASSES = (
    BMangaComaGap,
    BMangaWorkData,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bmanga_work = PointerProperty(type=BMangaWorkData)
    _logger.debug("work registered")


def unregister() -> None:
    try:
        del bpy.types.Scene.bmanga_work
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
