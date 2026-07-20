from __future__ import annotations

import ast
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_bmanga_detail_drawer_test"


def _load_drawer_api():
    root_package = ModuleType(PACKAGE)
    root_package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = root_package

    utils = ModuleType(f"{PACKAGE}.utils")
    utils.__path__ = [str(ROOT / "utils")]
    sys.modules[utils.__name__] = utils

    detail_name = f"{PACKAGE}.utils.detail_dialog"
    detail_spec = importlib.util.spec_from_file_location(
        detail_name,
        ROOT / "utils" / "detail_dialog.py",
    )
    detail = importlib.util.module_from_spec(detail_spec)
    assert detail_spec and detail_spec.loader
    sys.modules[detail_name] = detail
    detail_spec.loader.exec_module(detail)

    panels = ModuleType(f"{PACKAGE}.panels")
    panels.__path__ = [str(ROOT / "panels")]
    sys.modules[panels.__name__] = panels

    _install_link_stubs(utils)
    _install_effect_line_panel_stub(panels)
    drawer_name = f"{PACKAGE}.panels.detail_drawers"
    drawer_spec = importlib.util.spec_from_file_location(
        drawer_name,
        ROOT / "panels" / "detail_drawers" / "__init__.py",
        submodule_search_locations=[str(ROOT / "panels" / "detail_drawers")],
    )
    drawer = importlib.util.module_from_spec(drawer_spec)
    assert drawer_spec and drawer_spec.loader
    sys.modules[drawer_name] = drawer
    drawer_spec.loader.exec_module(drawer)
    return detail, drawer


def _install_link_stubs(utils_package) -> None:
    links = ModuleType(f"{PACKAGE}.utils.layer_links")
    links.linked_uids_for_uid = lambda _context, uid: {uid, "text:linked"}
    links.related_uids_for_target = lambda *_args: set()
    display = ModuleType(f"{PACKAGE}.utils.layer_display")

    def draw_linked(layout, _context, _uids):
        layout.box().label(text="リンク中のレイヤー", icon="LINKED")

    display.draw_linked_layers_box = draw_linked
    sys.modules[links.__name__] = links
    sys.modules[display.__name__] = display
    utils_package.layer_links = links
    utils_package.layer_display = display


def _install_effect_line_panel_stub(panels_package) -> None:
    """panels.effect_line_panel は ``import bpy`` を伴う実パネルモジュールの

    ため、bpy非依存のこのハーネスでは軽量スタブに差し替える。
    balloon.py の ``_draw_path`` (パス線box) が
    ``from .. import effect_line_panel`` で無条件に参照するため必要。
    """

    module = ModuleType(f"{PACKAGE}.panels.effect_line_panel")

    def draw_effect_path_settings(
        layout,
        params,
        *,
        preset_mode: bool = False,
        allow_path_edit: bool = True,
        show_base_path: bool = True,
    ) -> None:
        if show_base_path:
            path_box = layout.box()
            path_box.label(text="パス")
            path_box.prop(params, "base_path_enabled", text="基準パス")
        image_box = layout.box()
        image_box.label(text="パス線")
        image_box.prop(params, "line_image_source", text="内容")

    module.draw_effect_path_settings = draw_effect_path_settings
    sys.modules[module.__name__] = module
    panels_package.effect_line_panel = module


DETAIL, DRAWERS = _load_drawer_api()
BASIC = sys.modules[f"{PACKAGE}.panels.detail_drawers.basic"]
PRESET_ADAPTERS = sys.modules[f"{PACKAGE}.panels.detail_drawers.preset_adapters"]


class FixedSlotGrid:
    def __init__(self):
        self.slots = []

    def column(self, *, align=False):
        slot = SimpleNamespace(index=len(self.slots), align=align, blanks=[])
        slot.label = lambda *, text="", item=slot: item.blanks.append(text)
        self.slots.append(slot)
        return slot


class FixedSlotLayout:
    def __init__(self):
        self.grid_kwargs = None
        self.grid = FixedSlotGrid()

    def grid_flow(self, **kwargs):
        self.grid_kwargs = kwargs
        return self.grid

    def column(self, *, align=False):
        return SimpleNamespace(index=0, align=align)


@pytest.mark.parametrize("visible", (1, 2, 3))
def test_fixed_max_slots_keep_three_column_width_when_visible_columns_change(visible):
    layout = FixedSlotLayout()
    columns = BASIC.equal_columns(layout, visible, 3)

    assert layout.grid_kwargs == {
        "row_major": True,
        "columns": 3,
        "even_columns": True,
        "even_rows": False,
        "align": True,
    }
    assert len(layout.grid.slots) == 3
    assert tuple(slot.index for slot in columns) == tuple(range(visible))
    assert all(not slot.blanks for slot in layout.grid.slots[:visible])
    assert all(slot.blanks == [""] for slot in layout.grid.slots[visible:])


@pytest.mark.parametrize("visible", (1, 2))
def test_fixed_max_slots_keep_two_column_width_when_visible_columns_change(visible):
    layout = FixedSlotLayout()
    columns = BASIC.equal_columns(layout, visible, 2)

    assert layout.grid_kwargs["columns"] == 2
    assert layout.grid_kwargs["even_columns"] is True
    assert len(layout.grid.slots) == 2
    assert tuple(slot.index for slot in columns) == tuple(range(visible))
    assert all(not slot.blanks for slot in layout.grid.slots[:visible])
    assert all(slot.blanks == [""] for slot in layout.grid.slots[visible:])


def _function_nodes(filename: str) -> dict[str, ast.AST]:
    drawer_root = ROOT / "panels" / "detail_drawers"
    tree = ast.parse((drawer_root / filename).read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _called_names(node: ast.AST) -> set[str]:
    """呼び出された関数名の集合を返す。``foo()`` と ``module.foo()`` の両方を拾う。"""

    names: set[str] = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        if isinstance(call.func, ast.Name):
            names.add(call.func.id)
        elif isinstance(call.func, ast.Attribute):
            names.add(call.func.attr)
    return names


def test_every_non_preset_body_drawer_enters_the_fixed_slot_layout():
    """プリセットを持たない種別のdrawerは、今も自分で body_columns() を呼ぶ。"""

    expected = {
        "basic.py": {"draw_page_body", "draw_layer_folder_body"},
        "gp.py": {"draw_gp_body"},
        "image.py": {"draw_image_body"},
        "raster_fill.py": {"draw_raster_body"},
        "balloon.py": {"draw_tail_body"},
    }
    for filename, function_names in expected.items():
        functions = _function_nodes(filename)
        for function_name in function_names:
            calls = _called_names(functions[function_name])
            assert "body_columns" in calls, f"{filename}:{function_name}"


def test_every_preset_body_drawer_relies_on_dispatcher_provided_columns():
    """プリセットを持つ種別のdrawerは、dispatcherが渡すsidebar/body_colsを使うだけで、
    自分で equal_columns()/body_columns() を呼び直さない (固定スロットの生成元は
    dispatcher.draw_detail_dialog に一本化されている)。
    """

    expected = {
        "basic.py": {"draw_coma_body"},
        "image.py": {"draw_image_path_body"},
        "raster_fill.py": {"draw_fill_body"},
        "balloon.py": {"draw_balloon_body"},
        "text.py": {"draw_text_body"},
        "effect.py": {"draw_effect_body"},
    }
    for filename, function_names in expected.items():
        functions = _function_nodes(filename)
        for function_name in function_names:
            calls = _called_names(functions[function_name])
            assert "body_columns" not in calls, f"{filename}:{function_name}"
            assert "equal_columns" not in calls, f"{filename}:{function_name}"


def test_dispatcher_creates_fixed_slot_columns_for_preset_targets():
    """dispatcher側は max_columns 固定で equal_columns() を呼び、
    プリセットを持つ種別のsidebar/body列を一括生成する。"""

    dispatcher_source = (ROOT / "panels" / "detail_drawers" / "dispatcher.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(dispatcher_source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    calls = _called_names(functions["_draw_preset_target_dialog"])
    assert "equal_columns" in calls


def test_fill_type_change_clears_the_other_preset_category_selection():
    target = SimpleNamespace(
        kind="fill",
        stable_id="fill-1",
        stack_uid=None,
        namespace=None,
        data=Data(fill_type="gradient"),
    )
    session = SimpleNamespace(
        target=target,
        token="fill-session",
        preset_type="fill",
        preset_selection="別カテゴリの同名プリセット",
    )

    def set_context(preset_type, preset_name):
        session.preset_type = preset_type
        session.preset_selection = preset_name

    session.set_preset_context = set_context
    layout = RecordingLayout()
    assert PRESET_ADAPTERS.draw_preset_management(
        layout, SimpleNamespace(), session, DETAIL.DetailMode.ACTUAL
    )
    assert session.preset_type == "gradient"
    assert session.preset_selection is None
    operators = [item[1] for item in layout.records if item[0] == "operator"]
    assert "bmanga.detail_preset_rename" not in operators
    assert "bmanga.detail_preset_delete" not in operators


def test_gradient_target_switched_to_solid_uses_the_live_fill_category():
    target = SimpleNamespace(
        kind="fill",
        stable_id="fill-gradient-to-solid",
        stack_uid=None,
        # invoke時はgradientだったという古い分類を意図的に残す。
        namespace="gradient",
        data=Data(fill_type="solid"),
    )
    session = SimpleNamespace(
        target=target,
        token="fill-gradient-to-solid-session",
        preset_type="gradient",
        preset_selection="旧グラデーション",
    )

    def set_context(preset_type, preset_name):
        session.preset_type = preset_type
        session.preset_selection = preset_name

    session.set_preset_context = set_context
    layout = RecordingLayout()
    assert PRESET_ADAPTERS.draw_preset_management(
        layout,
        SimpleNamespace(),
        session,
        DETAIL.DetailMode.ACTUAL,
    )
    assert session.preset_type == "fill"
    assert session.preset_selection is None
    labels = [record[1] for record in layout.records if record[0] == "label"]
    assert "囲い塗りプリセット" in labels
    assert "グラデーションプリセット" not in labels


def test_page_coma_count_is_derived_and_read_only():
    page = Data(
        comas=[Data(), Data(), Data()],
        coma_count=99,
        offset_x_mm=0.0,
        offset_y_mm=0.0,
    )
    target = DETAIL.DetailTarget("page", "page-1", "page:page-1", page)
    layout = RecordingLayout()

    BASIC.draw_page_body(
        layout,
        SimpleNamespace(),
        _session(target, DETAIL.DetailMode.ACTUAL, "page"),
        DETAIL.DetailMode.ACTUAL,
    )

    assert ("label", "コマ数: 3") in layout.records
    assert ("prop", "coma_count") not in layout.records


class Data:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class OperatorProxy:
    def __init__(self, layout, operator_id):
        object.__setattr__(self, "_layout", layout)
        object.__setattr__(self, "operator_id", operator_id)

    def __setattr__(self, name, value):
        self._layout._items.append(("operator_property", self.operator_id, name, value))
        object.__setattr__(self, name, value)


class RecordingLayout:
    """Blenderの実UILayoutと同じ「作成順に配置」を再現する記録用レイアウト。

    ``box()``/``row()``/``column()``/``grid_flow()`` は呼ばれた時点で親の
    タイムライン (``_items``) へ即座に自分の位置を確保する (プレースホルダ)。
    実際に ``.label()``/``.prop()`` を書き込むのが後回しになっても、
    ``records`` を読んだ時点でのフラット化は「確保した位置」の順に並ぶ。
    これは dispatcher._draw_preset_target_dialog の
    「top列→プリセット管理→below列の順で確保し、実描画(drawer呼び出し)は
    後回しにしても正しい位置に収まる」という契約と同じ仕組みで、
    ここで正しく検証できる。
    """

    def __init__(self):
        self._items: list = []
        self.enabled = True
        self.active = True

    def _child(self, *_args, **_kwargs):
        child = RecordingLayout()
        self._items.append(child)
        return child

    box = _child
    row = _child
    column = _child
    grid_flow = _child

    def label(self, text="", **_kwargs):
        self._items.append(("label", text))

    def separator(self, **_kwargs):
        self._items.append(("separator",))

    def prop(self, _owner, name, **_kwargs):
        self._items.append(("prop", name))

    def operator(self, operator_id, **_kwargs):
        self._items.append(("operator", operator_id))
        return OperatorProxy(self, operator_id)

    def operator_menu_enum(self, operator_id, enum_name, **_kwargs):
        self._items.append(("operator_menu_enum", operator_id, enum_name))
        return OperatorProxy(self, operator_id)

    @property
    def records(self):
        flat: list = []

        def _walk(node: "RecordingLayout") -> None:
            for item in node._items:
                if isinstance(item, RecordingLayout):
                    _walk(item)
                else:
                    flat.append(item)

        _walk(self)
        return flat


def _text_data():
    return Data(
        title="本文",
        visible=True,
        locked=False,
        x_mm=1.0,
        y_mm=2.0,
        width_mm=30.0,
        height_mm=40.0,
        rotation_deg=0.0,
        speaker_name="",
        linked_balloon_preset="",
        font="",
        font_size_unit="q",
        font_size_value=14.0,
        font_bold=False,
        font_italic=False,
        color=(0.0, 0.0, 0.0, 1.0),
        writing_mode="vertical-rl",
        line_height=1.0,
        letter_spacing=0.0,
        stroke_enabled=False,
        stroke_width_mm=0.1,
        stroke_color=(1.0, 1.0, 1.0, 1.0),
        ruby_line_height=1.0,
        ruby_gap_mm=0.0,
        ruby_letter_spacing=0.0,
        ruby_size_percent=50.0,
        ruby_align="center",
        ruby_small_kana=True,
        ruby_default_style="group",
        ruby_font="",
        ruby_spans=(),
        font_spans=(),
        style_spans=(),
        tatechuyoko_ranges=(),
    )


def _session(target, mode, token):
    return SimpleNamespace(
        target=target,
        mode=mode,
        layout=DETAIL.resolve_detail_layout(target, mode),
        preset_selection="標準",
        token=token,
    )


def test_actual_draw_is_entry_independent_and_has_canonical_section_order():
    data = _text_data()
    target = DETAIL.DetailTarget("text", "text-1", "text:text-1", data)
    mode = DETAIL.DetailMode.ACTUAL
    before = deepcopy(data.__dict__)
    records = []
    for token in ("stack-entry", "object-entry"):
        layout = RecordingLayout()
        DRAWERS.draw_detail_dialog(layout, SimpleNamespace(), _session(target, mode, token), mode)
        records.append(layout.records)

    visible_records = [
        [
            record
            for record in entry
            if not (
                    len(record) >= 3
                    and record[0] == "operator_property"
                    and record[2] in {"session_token", "parent_session_token"}
            )
        ]
        for entry in records
    ]
    assert visible_records[0] == visible_records[1]
    assert data.__dict__ == before
    labels = [record[1] for record in records[0] if record[0] == "label"]
    # 上段の左列は表示情報→配置、右列はプリセット一覧。RecordingLayout は
    # 列を作成順に記録するため、左列全体が右列より先に現れる。
    assert labels.index("テキスト") < labels.index("配置 (mm)")
    assert labels.index("配置 (mm)") < labels.index("テキストプリセット")
    assert labels.index("テキストプリセット") < labels.index("リンク中のレイヤー")
    assert (
        "operator_menu_enum",
        "bmanga.detail_text_linked_balloon_set",
        "preset_name",
    ) in records[0]
    assert (
        "operator_property",
        "bmanga.detail_text_linked_balloon_set",
        "target_id",
        "text-1",
    ) in records[0]


def test_preset_draw_reuses_body_without_actual_only_sections_or_nested_management():
    data = _text_data()
    target = DETAIL.resolve_preset_detail_target("text", "標準", data)
    mode = DETAIL.DetailMode.PRESET
    layout = RecordingLayout()
    DRAWERS.draw_detail_dialog(layout, SimpleNamespace(), _session(target, mode, "preset"), mode)

    labels = [record[1] for record in layout.records if record[0] == "label"]
    operators = [record[1] for record in layout.records if record[0] == "operator"]
    assert labels[0] == "テキストプリセット"
    assert "配置 (mm)" not in labels
    assert "リンク中のレイヤー" not in labels
    assert "bmanga.preset_detail_edit" not in operators
    assert (
        "operator_menu_enum",
        "bmanga.detail_text_linked_balloon_set",
        "preset_name",
    ) in layout.records
    assert (
        "operator_property",
        "bmanga.detail_text_linked_balloon_set",
        "target_id",
        target.stable_id,
    ) in layout.records
    assert not any(
        record[:2] == ("operator_menu_enum", "bmanga.detail_preset_apply")
        for record in layout.records
    )


@pytest.mark.parametrize(
    ("preset_type", "kind"),
    (
        ("border", "coma"),
        ("text", "text"),
        ("effect_line", "effect"),
        ("fill", "fill"),
        ("gradient", "fill"),
        ("image_path", "image_path"),
        ("balloon", "balloon_shape"),
        ("tail", "balloon_tail"),
    ),
)
def test_preset_description_is_after_common_header_and_before_body(
    preset_type,
    kind,
):
    dispatcher = sys.modules[f"{PACKAGE}.panels.detail_drawers.dispatcher"]
    data = Data(effect_type="focus", fill_type="gradient" if preset_type == "gradient" else "solid")
    target = DETAIL.resolve_preset_detail_target(
        preset_type,
        "順序確認",
        data,
        params=data if kind == "effect" else None,
    )
    assert target.kind == kind
    mode = DETAIL.DetailMode.PRESET
    layout = RecordingLayout()
    description = Data(description_text="説明")
    original = dispatcher._BODY_DRAWERS[kind]
    # プリセットを持つ種別は drawer(sidebar_top, sidebar_below, body_cols,
    # context, session, mode) で呼ばれ、持たない種別 (balloon_tail) は旧来の
    # drawer(layout, context, session, mode) のまま呼ばれる。どちらの呼び出し
    # でも先頭引数が「ヘッダ直後・プリセット一覧より上に描く列」なので
    # *args で受けて args[0] へ描画すれば両方のシグネチャに対応できる。
    dispatcher._BODY_DRAWERS[kind] = (
        lambda *args, **_kwargs: args[0].label(text="本文")
    )
    try:
        DRAWERS.draw_detail_dialog(
            layout,
            SimpleNamespace(),
            _session(target, mode, f"preset-{preset_type}"),
            mode,
            description_owner=description,
        )
    finally:
        dispatcher._BODY_DRAWERS[kind] = original
    header_index = next(
        index
        for index, record in enumerate(layout.records)
        if record[0] == "label" and record[1] != "本文"
    )
    description_index = layout.records.index(("prop", "description_text"))
    body_index = layout.records.index(("label", "本文"))
    assert header_index < description_index < body_index


def test_raster_actions_carry_the_fixed_target_and_hide_unimplemented_dpi_action():
    data = Data(
        title="線画",
        visible=True,
        locked=False,
        opacity=100.0,
        dpi=600,
        line_color=(0.0, 0.0, 0.0, 1.0),
        bit_depth="gray8",
    )
    target = DETAIL.DetailTarget("raster", "raster-fixed", "raster:raster-fixed", data)
    mode = DETAIL.DetailMode.ACTUAL
    layout = RecordingLayout()
    DRAWERS.draw_detail_dialog(layout, SimpleNamespace(), _session(target, mode, "raster"), mode)

    operator_ids = [record[1] for record in layout.records if record[0] == "operator"]
    assert "bmanga.raster_layer_resample" not in operator_ids
    assert "bmanga.raster_layer_set_bit_depth" not in operator_ids
    assert ("prop", "bit_depth") in layout.records
    assert "bmanga.detail_raster_paint_enter" not in operator_ids
    paint_spec = DETAIL.get_detail_action_spec("bmanga.detail_raster_paint_enter")
    assert paint_spec.boundary is DETAIL.DetailActionBoundary.INDEPENDENT_IMMEDIATE
    assert paint_spec.closes_parent_before_run is True
    for operator_id in {"bmanga.detail_raster_save_png"}:
        assert (
            "operator_property",
            operator_id,
            "target_id",
            "raster-fixed",
        ) in layout.records
        assert (
            "operator_property",
            operator_id,
            "session_token",
            "raster",
        ) in layout.records
        spec = DETAIL.get_detail_action_spec(operator_id)
        assert spec.boundary is DETAIL.DetailActionBoundary.INDEPENDENT_IMMEDIATE
        assert spec.closes_parent_before_run is False


# --- 実際のdrawer (スタブ差し替えなし) を使ったサイドバー/本文振分けの検証 ---
#
# 上のいくつかのテストは dispatcher._BODY_DRAWERS を差し替えて構造だけを見る。
# ここでは実際の balloon/coma/fill/image_path の drawer を直接呼び、
# sidebar (非プリセット設定) と body_cols (プリセット保存対象) の振分けが
# 例外なく動き、意図した列へ描画されることを確認する。
#
# effect は effect_line_panel が呼び出し無条件で bpy に依存するため、この
# bpy非依存ハーネスでは対象外 (structural な検証は上の
# test_preset_description_is_after_common_header_and_before_body で行う)。
# image_path はACTUALモードだと _draw_inout が effect_line_panel を無条件
# import するため、ここでは preset_mode=True 側だけを検証する。
# fill は fill_type=="gradient" にすると _draw_gradient 経由で bpy 依存の
# fill_real_object を import するため、fill_type="solid" のみ検証する。


def _balloon_data():
    return Data(
        title="フキダシ1",
        visible=True,
        locked=False,
        x_mm=10.0,
        y_mm=10.0,
        width_mm=40.0,
        height_mm=30.0,
        rotation_deg=0.0,
        flip_h=False,
        flip_v=False,
        linked_text_offset_x_mm=0.0,
        linked_text_offset_y_mm=0.0,
        linked_text_padding_x_mm=0.0,
        linked_text_padding_y_mm=0.0,
        tails=(),
        shape="rect",
        corner_type="square",
        custom_preset_name="",
        line_style="solid",
        line_width_mm=0.3,
        line_color=(0.0, 0.0, 0.0, 1.0),
        fill_color=(1.0, 1.0, 1.0, 1.0),
        fill_opacity=100.0,
        opacity=100.0,
    )


def test_balloon_actual_body_puts_non_preset_settings_in_the_sidebar():
    """v0.6.557以降の3列契約 (2026-07-20 さらに再設計):

    列1(サイドバー)=ヘッダ→配置→(プリセット一覧より上)リンクテキストに
    合わせる→しっぽ→プリセット一覧→(プリセット一覧より下)形状→
    リンクレイヤー。列2=線・塗り、列3(一番右)=パス線。

    RecordingLayout は「作成順に配置」(プレースホルダ) を再現するため、
    列1の一連の項目はその列内で正しい順序になる。列2/列3はそれぞれ別列
    (別のプレースホルダ) なので、列1側の項目との前後関係はここでは検証
    しない (実際の画面でも左右に並ぶ別列であり、前後関係に意味が無い)。
    列2→列3の順序 (dispatcherが body_cols を作成した順) だけ検証する。
    """

    data = _balloon_data()
    target = DETAIL.DetailTarget("balloon", "balloon-1", "balloon:balloon-1", data)
    mode = DETAIL.DetailMode.ACTUAL
    layout = RecordingLayout()
    DRAWERS.draw_detail_dialog(layout, SimpleNamespace(), _session(target, mode, "balloon"), mode)

    labels = [record[1] for record in layout.records if record[0] == "label"]
    assert labels.index("フキダシ") < labels.index("配置 (mm)")
    assert labels.index("配置 (mm)") < labels.index("リンクテキストに合わせる")
    assert labels.index("リンクテキストに合わせる") < labels.index("しっぽ (0)")
    assert labels.index("しっぽ (0)") < labels.index("フキダシプリセット")
    assert labels.index("フキダシプリセット") < labels.index("形状")
    assert labels.index("形状") < labels.index("リンク中のレイヤー")
    assert "線・塗り" in labels, "線・塗りが本文列にありません"
    assert "パス線" in labels, "パス線が一番右の列にありません"
    assert labels.index("線・塗り") < labels.index("パス線"), (
        "線・塗り(列2)がパス線(列3、一番右)より後になっています"
    )


def test_balloon_shape_preset_body_only_uses_sidebar_when_body_columns_is_empty():
    """balloon_shape (フキダシプリセット編集) は max_columns=1 のため
    body_cols が空タプルになるが、preset_mode+namespace=="balloon" の早期returnが
    body_cols へ触れる前に完了するため例外にならない (壊れやすい前提の固定用)。
    """

    data = Data(
        linked_text_offset_x_mm=0.0,
        linked_text_offset_y_mm=0.0,
        linked_text_padding_x_mm=0.0,
        linked_text_padding_y_mm=0.0,
    )
    target = DETAIL.resolve_preset_detail_target("balloon", "形状確認", data)
    assert target.kind == "balloon_shape"
    mode = DETAIL.DetailMode.PRESET
    layout = RecordingLayout()
    DRAWERS.draw_detail_dialog(
        layout, SimpleNamespace(), _session(target, mode, "balloon-shape-preset"), mode
    )

    labels = [record[1] for record in layout.records if record[0] == "label"]
    assert labels[0] == "フキダシ形状プリセット"
    assert "リンクテキストに合わせる" in labels


def _coma_data():
    return Data(
        title="コマ1",
        visible=True,
        locked=False,
        coma_blend_template_path="",
        shape_type="rect",
        rect_x_mm=0.0,
        rect_y_mm=0.0,
        rect_width_mm=80.0,
        rect_height_mm=60.0,
        paper_visible=True,
        background_color=(1.0, 1.0, 1.0, 1.0),
        border=Data(
            visible=True,
            style="normal",
            width_mm=1.0,
            color=(0.0, 0.0, 0.0, 1.0),
            corner_type="square",
            corner_radius_mm=0.0,
        ),
        white_margin=Data(
            enabled=False,
            placement="outside",
            width_mm=1.0,
            outer_color=(1.0, 1.0, 1.0, 1.0),
            inner_color=(1.0, 1.0, 1.0, 1.0),
        ),
    )


def test_coma_actual_body_puts_non_preset_settings_in_the_sidebar():
    """サイドバー列 (col0) 内: コマ→blendファイル→形状→枠線プリセット→

    リンクレイヤー。本文列 (col1、別列) は枠線→フチの順だけを検証する
    (別列どうしの前後関係には意味が無い。RecordingLayout の説明を参照)。
    """

    data = _coma_data()
    target = DETAIL.DetailTarget("coma", "coma-1", "coma:coma-1", data)
    mode = DETAIL.DetailMode.ACTUAL
    layout = RecordingLayout()
    DRAWERS.draw_detail_dialog(layout, SimpleNamespace(), _session(target, mode, "coma"), mode)

    labels = [record[1] for record in layout.records if record[0] == "label"]
    assert labels.index("コマ") < labels.index("コマ用blendファイル (このコマのみ)")
    assert labels.index("コマ用blendファイル (このコマのみ)") < labels.index("形状")
    assert labels.index("形状") < labels.index("枠線プリセット")
    assert labels.index("枠線プリセット") < labels.index("リンク中のレイヤー")
    assert "枠線" in labels
    assert "フチ" in labels
    assert labels.index("枠線") < labels.index("フチ")


def test_coma_preset_body_hides_non_preset_sidebar_content():
    data = _coma_data()
    target = DETAIL.resolve_preset_detail_target("border", "枠線確認", data)
    assert target.kind == "coma"
    mode = DETAIL.DetailMode.PRESET
    layout = RecordingLayout()
    DRAWERS.draw_detail_dialog(layout, SimpleNamespace(), _session(target, mode, "coma-preset"), mode)

    labels = [record[1] for record in layout.records if record[0] == "label"]
    assert labels[0] == "コマプリセット"
    assert "コマ用blendファイル (このコマのみ)" not in labels
    assert "形状" not in labels
    assert "枠線" in labels
    assert "フチ" in labels
    assert labels.index("枠線") < labels.index("フチ")


def _fill_data():
    return Data(
        title="塗り1",
        visible=True,
        locked=False,
        fill_type="solid",
        rotation_deg=0.0,
        use_gradient_endpoints=False,
        use_region=False,
        opacity=100.0,
        color=(0.0, 0.0, 0.0, 1.0),
    )


def test_fill_actual_body_puts_non_preset_settings_in_the_sidebar():
    """サイドバー列 (col0) 内: 囲い塗り→配置→囲い塗りプリセット。本文列

    (col1、別列) の「ベタ塗り」は存在確認だけ行う (別列どうしの前後関係
    には意味が無い)。
    """

    data = _fill_data()
    target = DETAIL.DetailTarget("fill", "fill-1", "fill:fill-1", data)
    mode = DETAIL.DetailMode.ACTUAL
    layout = RecordingLayout()
    DRAWERS.draw_detail_dialog(layout, SimpleNamespace(), _session(target, mode, "fill"), mode)

    labels = [record[1] for record in layout.records if record[0] == "label"]
    assert labels.index("囲い塗り") < labels.index("配置")
    assert labels.index("配置") < labels.index("囲い塗りプリセット")
    assert "ベタ塗り" in labels


def _image_path_data():
    return Data(
        title="パターン1",
        content_source="image",
        filepath="",
        opacity=100.0,
        draw_mode="stamp",
        brush_size_mm=5.0,
        aspect_ratio=1.0,
        image_angle_deg=0.0,
        spacing_percent=100.0,
        color=(0.0, 0.0, 0.0, 1.0),
        stamp_angle_mode="fixed",
        inout_size_enabled=False,
        inout_opacity_enabled=False,
        inout_color_enabled=False,
        in_percent=0.0,
        out_percent=0.0,
        inout_start_color=(0.0, 0.0, 0.0, 1.0),
        inout_end_color=(0.0, 0.0, 0.0, 1.0),
    )


def test_image_path_preset_body_stays_on_the_right_column():
    data = _image_path_data()
    target = DETAIL.resolve_preset_detail_target("image_path", "パターン確認", data)
    mode = DETAIL.DetailMode.PRESET
    layout = RecordingLayout()
    DRAWERS.draw_detail_dialog(
        layout, SimpleNamespace(), _session(target, mode, "image-path-preset"), mode
    )

    labels = [record[1] for record in layout.records if record[0] == "label"]
    assert labels[0] == "パターンカーブプリセット"
    assert labels.index("パターンカーブプリセット") < labels.index("内容")
    assert labels.index("内容") < labels.index("描画")
    assert "入り抜き" in labels
