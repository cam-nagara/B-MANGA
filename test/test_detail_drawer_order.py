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


def test_every_supported_body_drawer_enters_the_fixed_slot_layout():
    expected = {
        "basic.py": {"draw_page_body", "draw_coma_body", "draw_layer_folder_body"},
        "gp.py": {"draw_gp_body"},
        "image.py": {"draw_image_body", "draw_image_path_body"},
        "raster_fill.py": {"draw_raster_body", "draw_fill_body"},
        "balloon.py": {"draw_balloon_body", "draw_tail_body"},
        "text.py": {"draw_text_body"},
        "effect.py": {"draw_effect_body"},
    }
    drawer_root = ROOT / "panels" / "detail_drawers"
    for filename, function_names in expected.items():
        tree = ast.parse((drawer_root / filename).read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for function_name in function_names:
            calls = {
                node.func.id
                for node in ast.walk(functions[function_name])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            assert "body_columns" in calls, f"{filename}:{function_name}"


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
    def __init__(self, records, operator_id):
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "operator_id", operator_id)

    def __setattr__(self, name, value):
        self.records.append(("operator_property", self.operator_id, name, value))
        object.__setattr__(self, name, value)


class RecordingLayout:
    def __init__(self, records=None):
        self.records = records if records is not None else []
        self.enabled = True
        self.active = True

    def _child(self, *_args, **_kwargs):
        return RecordingLayout(self.records)

    box = _child
    row = _child
    column = _child
    grid_flow = _child

    def label(self, text="", **_kwargs):
        self.records.append(("label", text))

    def separator(self, **_kwargs):
        self.records.append(("separator",))

    def prop(self, _owner, name, **_kwargs):
        self.records.append(("prop", name))

    def operator(self, operator_id, **_kwargs):
        self.records.append(("operator", operator_id))
        return OperatorProxy(self.records, operator_id)

    def operator_menu_enum(self, operator_id, enum_name, **_kwargs):
        self.records.append(("operator_menu_enum", operator_id, enum_name))
        return OperatorProxy(self.records, operator_id)


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
    dispatcher._BODY_DRAWERS[kind] = (
        lambda body_layout, _context, _session, _mode, **_kwargs: body_layout.label(
            text="本文"
        )
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
