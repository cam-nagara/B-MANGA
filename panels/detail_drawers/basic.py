"""共通詳細ダイアログの基本種別と小さなUI補助。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...utils.detail_dialog import (
    DetailActionBoundary,
    DetailContractError,
    get_detail_action_spec,
)


class ClassifiedDetailLayout:
    """共通詳細内の全Operatorを分類表へ強制的に通すLayout代理。"""

    __slots__ = ("_layout",)

    def __init__(self, layout) -> None:
        object.__setattr__(self, "_layout", layout)

    def __getattr__(self, name: str):
        return getattr(self._layout, name)

    def __setattr__(self, name: str, field_value: Any) -> None:
        setattr(self._layout, name, field_value)

    def _child(self, method_name: str, *args: Any, **kwargs: Any):
        child = getattr(self._layout, method_name)(*args, **kwargs)
        return ClassifiedDetailLayout(child)

    def box(self, *args: Any, **kwargs: Any):
        return self._child("box", *args, **kwargs)

    def row(self, *args: Any, **kwargs: Any):
        return self._child("row", *args, **kwargs)

    def column(self, *args: Any, **kwargs: Any):
        return self._child("column", *args, **kwargs)

    def split(self, *args: Any, **kwargs: Any):
        return self._child("split", *args, **kwargs)

    def grid_flow(self, *args: Any, **kwargs: Any):
        return self._child("grid_flow", *args, **kwargs)

    def column_flow(self, *args: Any, **kwargs: Any):
        return self._child("column_flow", *args, **kwargs)

    def operator(self, operator_id: str, *args: Any, **kwargs: Any):
        _require_drawable_action(operator_id)
        return self._layout.operator(operator_id, *args, **kwargs)

    def operator_menu_enum(
        self,
        operator_id: str,
        enum_property: str,
        *args: Any,
        **kwargs: Any,
    ):
        _require_drawable_action(operator_id)
        return self._layout.operator_menu_enum(
            operator_id,
            enum_property,
            *args,
            **kwargs,
        )


def _require_drawable_action(operator_id: str) -> None:
    spec = get_detail_action_spec(operator_id)
    if spec.boundary is DetailActionBoundary.EXCLUDED:
        raise DetailContractError(f"excluded detail operator: {operator_id}")


def classified_layout(layout):
    if isinstance(layout, ClassifiedDetailLayout):
        return layout
    return ClassifiedDetailLayout(layout)


def detail_operator(layout, operator_id: str, *args: Any, **kwargs: Any):
    return classified_layout(layout).operator(operator_id, *args, **kwargs)


def detail_operator_menu_enum(
    layout,
    operator_id: str,
    enum_property: str,
    *args: Any,
    **kwargs: Any,
):
    return classified_layout(layout).operator_menu_enum(
        operator_id,
        enum_property,
        *args,
        **kwargs,
    )


def value(source: Any, name: str, default: Any = None) -> Any:
    """PropertyGroupと辞書を同じ読み取り規則で扱う。"""

    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def has_field(source: Any, name: str) -> bool:
    if source is None:
        return False
    if isinstance(source, Mapping):
        return name in source
    return hasattr(source, name)


def prop_if(layout, owner: Any, name: str, **kwargs: Any) -> bool:
    """存在するRNAプロパティだけを描画する。値は変更しない。"""

    if not has_field(owner, name):
        return False
    layout.prop(owner, name, **kwargs)
    return True


def prop_pair(layout, owner: Any, first: str, second: str, **kwargs: Any) -> None:
    row = layout.row(align=True)
    prop_if(row, owner, first, **kwargs.get(first, {}))
    prop_if(row, owner, second, **kwargs.get(second, {}))


def equal_columns(layout, count: int, max_count: int | None = None):
    """最大列数ぶんの固定等幅slotを作り、現在表示する先頭列だけを返す。"""

    column_count = max(1, int(count))
    maximum = max(column_count, int(max_count or column_count))
    if maximum == 1:
        return (layout.column(align=True),)
    grid = layout.grid_flow(
        row_major=True,
        columns=maximum,
        even_columns=True,
        even_rows=False,
        align=True,
    )
    slots = tuple(grid.column(align=True) for _index in range(maximum))
    for slot in slots[column_count:]:
        slot.label(text="")
    return slots[:column_count]


def body_columns(layout, session):
    spec = session.layout
    return equal_columns(layout, spec.column_count, spec.max_columns)


def set_operator_fields(operator: Any, **fields: Any) -> None:
    """宣言済みのオペレータープロパティだけへ固定対象を渡す。"""

    for name, field_value in fields.items():
        if field_value is None:
            continue
        try:
            setattr(operator, name, field_value)
        except (AttributeError, TypeError):
            continue


def draw_page_body(layout, _context, session, _mode) -> None:
    entry = session.target.data
    column = body_columns(layout, session)[0]
    box = column.box()
    box.label(text="ページ設定", icon="FILE_BLANK")
    try:
        coma_count = len(getattr(entry, "comas", ()) or ())
    except (ReferenceError, TypeError):
        coma_count = 0
    box.label(text=f"コマ数: {coma_count}")
    prop_pair(
        box,
        entry,
        "offset_x_mm",
        "offset_y_mm",
        offset_x_mm={"text": "表示X"},
        offset_y_mm={"text": "表示Y"},
    )


def draw_coma_body(layout, context, session, mode) -> None:
    """コマ設定。選択依存ボタンを避け、固定済みentryだけを描画する。"""

    from .. import coma_detail_panel

    entry = session.target.data
    columns = body_columns(layout, session)
    primary = columns[0]
    secondary = columns[min(1, len(columns) - 1)]
    preset_mode = str(getattr(mode, "value", mode)) == "preset"
    if not preset_mode:
        blend_box = primary.box()
        blend_box.label(text="コマ用blendファイル (このコマのみ)", icon="FILE_BLEND")
        prop_if(blend_box, entry, "coma_blend_template_path", text="")

        shape_box = primary.box()
        shape_box.label(text="形状")
        _draw_coma_shape(shape_box, entry)

    border_box = secondary.box()
    border_box.label(text="枠線")
    coma_detail_panel.draw_coma_border_settings(
        border_box,
        context,
        entry,
        # プリセット一覧は共通外枠の本文より前へ一度だけ置く。
        preset_mode=True,
    )

    white_box = secondary.box()
    white_box.label(text="フチ")
    coma_detail_panel.draw_coma_white_margin_settings(white_box, entry)


def _draw_coma_shape(layout, entry) -> None:
    if str(value(entry, "shape_type", "rect") or "rect") == "rect":
        prop_pair(layout, entry, "rect_x_mm", "rect_y_mm")
        prop_pair(layout, entry, "rect_width_mm", "rect_height_mm")
    row = layout.row(align=True)
    prop_if(row, entry, "paper_visible", text="背景")
    prop_if(row, entry, "background_color", text="背景色")


def draw_layer_folder_body(layout, _context, session, _mode) -> None:
    entry = session.target.data
    box = body_columns(layout, session)[0].box()
    box.label(text="フォルダー設定", icon="FILE_FOLDER")
    prop_if(box, entry, "expanded", text="展開")


__all__ = [
    "ClassifiedDetailLayout",
    "body_columns",
    "classified_layout",
    "detail_operator",
    "detail_operator_menu_enum",
    "draw_coma_body",
    "draw_layer_folder_body",
    "draw_page_body",
    "equal_columns",
    "has_field",
    "prop_if",
    "prop_pair",
    "set_operator_fields",
    "value",
]
