"""角半径入力の描画ヘルパ."""

from __future__ import annotations


def draw_corner_radius(layout, owner, *, prefix: str = "rounded_corner", text: str = "角半径") -> None:
    row = layout.row(align=True)
    unit_attr = f"{prefix}_radius_unit"
    mm_attr = f"{prefix}_radius_mm"
    percent_attr = f"{prefix}_radius_percent"
    if str(getattr(owner, unit_attr, "mm") or "mm") == "percent":
        row.prop(owner, percent_attr, text=text)
    else:
        row.prop(owner, mm_attr, text=text)
    row.prop(owner, unit_attr, text="")
