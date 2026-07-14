"""Blender実機用: コマ詳細UI整理とフチ位置設定の確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "test"))

from detail_dialog_public_test_support import draw_actual_detail  # noqa: E402


class _RecordingLayout:
    def __init__(self, records: list[tuple[str, str, str]]) -> None:
        self.records = records
        self.enabled = True
        self.active = True
        self.scale_y = 1.0
        self.ui_units_x = 0.0
        self.operator_context = "INVOKE_DEFAULT"

    def row(self, **_kwargs):
        return self

    def column(self, **_kwargs):
        return self

    def box(self):
        return self

    def split(self, **_kwargs):
        return self

    def grid_flow(self, **_kwargs):
        return self

    def label(self, text: str = "", **_kwargs) -> None:
        self.records.append(("label", "", text))

    def prop(self, data, prop_name: str, text: str = "", **_kwargs) -> None:
        self.records.append(("prop", prop_name, text))

    def operator(self, _op_id: str, text: str = "", **_kwargs):
        self.records.append(("operator", "", text))
        return type("_Op", (), {})()

    def operator_menu_enum(self, _op_id: str, _prop: str, text: str = "", **_kwargs):
        self.records.append(("operator", "", text))
        return type("_Op", (), {})()

    def separator(self) -> None:
        self.records.append(("separator", "", ""))

    def template_curve_mapping(self, *_args, **_kwargs) -> None:
        self.records.append(("curve", "", ""))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_coma_detail_margin",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_coma_detail_margin"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _reset_comas(page) -> None:
    while len(page.comas):
        page.comas.remove(len(page.comas) - 1)


def _make_coma(page):
    _reset_comas(page)
    coma = page.comas.add()
    coma.id = "c01"
    coma.coma_id = "c01"
    coma.title = "ui_margin_probe"
    coma.shape_type = "rect"
    coma.rect_x_mm = 0.0
    coma.rect_y_mm = 0.0
    coma.rect_width_mm = 100.0
    coma.rect_height_mm = 80.0
    coma.border.visible = True
    coma.border.style = "solid"
    coma.border.width_mm = 4.0
    coma.white_margin.enabled = True
    coma.white_margin.width_mm = 6.0
    return coma


def _assert_detail_ui(context, coma) -> None:
    records: list[tuple[str, str, str]] = []
    layout = _RecordingLayout(records)
    session = draw_actual_detail(
        "bmanga_dev_coma_detail_margin",
        layout,
        context,
        coma,
        "coma",
    )
    assert session.target.stable_id == coma.id
    assert session.layout.max_columns == 2

    texts = {text for _kind, _prop, text in records if text}
    props = {prop for kind, prop, _text in records if kind == "prop"}

    removed_exact = {"白フチ"}
    removed_fragments = {
        "空のときは作品/プリファレンスの設定が使われる",
        "形状:",
        "頂点数:",
        "(Enter=確定 / ESC=キャンセル / 緑線=スナップ)",
        "(負値は作品共通ルールを継承)",
        "上下 (個別)",
        "左右 (個別)",
        "自動くり抜き",
    }
    joined = "\n".join(texts)
    for text in removed_exact:
        assert text not in texts, f"削除対象の文言が残っています: {text}"
    for text in removed_fragments:
        assert text not in joined, f"削除対象の文言が残っています: {text}"
    assert "title" in props, "共通ヘッダーの表示名がありません"
    if hasattr(coma, "visible"):
        assert ("prop", "visible", "表示") in records, "共通ヘッダーの表示がありません"
    assert "coma_gap_vertical_mm" not in props
    assert "coma_gap_horizontal_mm" not in props
    assert "overlap_clipping" not in props
    assert "paper_visible" in props
    assert "background_color" in props
    assert "placement" in props
    assert "背景" in texts
    assert "背景色" in texts
    assert "フチ" in texts
    assert coma.bl_rna.properties["paper_visible"].name == "背景"
    assert "用紙" not in coma.bl_rna.properties["paper_visible"].description
    assert "用紙" not in coma.bl_rna.properties["background_color"].description
    assert coma.bl_rna.properties["overlap_clipping"].name != "自動くり抜き"
    assert "個別" not in coma.bl_rna.properties["coma_gap_vertical_mm"].name
    assert "個別" not in coma.bl_rna.properties["coma_gap_horizontal_mm"].name
    assert "負値" not in coma.bl_rna.properties["coma_gap_vertical_mm"].description
    assert "負値" not in coma.bl_rna.properties["coma_gap_horizontal_mm"].description


def _bounds_mm(obj):
    verts = [v.co for v in obj.data.vertices]
    xs = [co.x * 1000.0 for co in verts]
    ys = [co.y * 1000.0 for co in verts]
    return min(xs), min(ys), max(xs), max(ys), len(obj.data.polygons)


def _assert_margin_geometry(context, work, page, coma) -> None:
    from bmanga_dev_coma_detail_margin.utils import coma_border_object

    expected = {
        "outside": (-8.0, -8.0, 108.0, 88.0),
        "inside": (2.0, 2.0, 98.0, 78.0),
        "both": (-8.0, -8.0, 108.0, 88.0),
    }
    face_counts = {}
    for placement, bounds_expected in expected.items():
        coma.white_margin.placement = placement
        coma_border_object.ensure_coma_border_object(context.scene, work, page, coma)
        obj = bpy.data.objects.get(f"{coma_border_object.COMA_WHITE_MARGIN_NAME_PREFIX}{page.id}_{coma.id}")
        assert obj is not None, "フチの実体が作られていません"
        bounds = _bounds_mm(obj)
        face_counts[placement] = bounds[4]
        for actual, expected_value in zip(bounds[:4], bounds_expected, strict=True):
            assert abs(actual - expected_value) < 0.05, (
                f"{placement} のフチ範囲が違います: actual={bounds[:4]}, expected={bounds_expected}"
            )
    assert face_counts["both"] == face_counts["outside"] + face_counts["inside"]


def _assert_schema_and_export(coma) -> None:
    from bmanga_dev_coma_detail_margin.io import export_pipeline, schema

    coma.white_margin.placement = "inside"
    data = schema.coma_white_margin_to_dict(coma.white_margin)
    assert data["placement"] == "inside"
    schema.coma_white_margin_from_dict(coma.white_margin, {"enabled": True, "placement": "bad", "widthMm": 6.0})
    assert coma.white_margin.placement == "outside"
    coma.white_margin.placement = "inside"
    inside = export_pipeline._draw_coma_white_margin_layer(coma, 1200, 100)
    coma.white_margin.placement = "outside"
    outside = export_pipeline._draw_coma_white_margin_layer(coma, 1200, 100)
    assert inside is not None and outside is not None
    assert inside.image.size[0] < outside.image.size[0]
    assert inside.image.size[1] < outside.image.size[1]


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_detail_margin_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ComaDetailMargin.bmanga"))
        assert "FINISHED" in result, result

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        coma = _make_coma(page)
        _assert_detail_ui(context, coma)
        _assert_margin_geometry(context, work, page, coma)
        _assert_schema_and_export(coma)
        print("BMANGA_COMA_DETAIL_UI_MARGIN_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
