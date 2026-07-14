"""Blender実機用: プリセット詳細編集ダイアログの「各ツール共通詳細ダイアログ化」確認.

operators/preset_detail_op.py の BMANGA_OT_preset_detail_edit を、専用の
簡易ダイアログをやめて各ツールの既存詳細設定描画関数を共用する形へ書き換え
た (border / text / effect_line / fill / gradient / image_path / tail の
7タイプ、balloon は対象外で説明編集のみ)。

各タイプについて以下を確認する:
  (a) スクラッチへ値を設定して保存 (新規プリセット作成)
  (b) プリセット → スクラッチへのロード (説明・代表フィールドの往復確認)
  (c) スクラッチの代表フィールドを変更
  (d) 上書き保存 (同じプリセット名への上書き)
  (e) 再ロードして変更が永続化されたことを確認
  (f) draw 関数を _RecordingLayout スタブで呼び、例外が出ないこと・
      実データ前提の UI (プリセット選択列・削除ボタン等) が出ないこと・
      プリセット保存対象のフィールドは出ることを確認
  (g) 上記一連の操作 (b)〜(f) の前後で、シーンに元からある「無関係な実
      データ」(実コマ・実テキスト・実フキダシしっぽ・実フィル/グラデー
      ションレイヤー・実パターンカーブレイヤー・実効果線ツール設定
      (scene.bmanga_effect_line_params)・オブジェクト総数) が一切変化して
      いないことを確認する。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "test"))

from detail_dialog_public_test_support import draw_preset_detail  # noqa: E402

MOD_NAME = "bmanga_dev_preset_detail_tool_dialog"

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)
    else:
        print(f"OK: {message}", flush=True)


class _RecordingLayout:
    """draw() 系関数をヘッドレスで呼ぶための最小 UILayout スタブ."""

    def __init__(self, records: list[tuple[str, str, str]]) -> None:
        self.records = records
        self.enabled = True
        self.active = True
        self.alignment = "EXPAND"
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

    def prop(self, _data, prop_name: str, text: str = "", **_kwargs) -> None:
        self.records.append(("prop", prop_name, text))

    def prop_search(self, _data, prop_name: str, _search_data, _search_prop, text: str = "", **_kwargs) -> None:
        self.records.append(("prop_search", prop_name, text))

    def operator(self, op_id: str, text: str = "", **_kwargs):
        self.records.append(("operator", op_id, text))
        return type("_Op", (), {})()

    def operator_menu_enum(self, op_id: str, _prop: str, text: str = "", **_kwargs):
        self.records.append(("operator_menu_enum", op_id, text))
        return type("_Op", (), {})()

    def menu(self, menu_id: str, text: str = "", **_kwargs) -> None:
        self.records.append(("menu", menu_id, text))

    def separator(self) -> None:
        self.records.append(("separator", "", ""))

    def template_curve_mapping(self, *_args, **_kwargs) -> None:
        self.records.append(("curve", "", ""))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    return importlib.import_module(f"{MOD_NAME}.{path}")


def _ops(records: list[tuple[str, str, str]]) -> set[str]:
    return {op_id for kind, op_id, _text in records if kind == "operator"}


def _props(records: list[tuple[str, str, str]]) -> set[str]:
    return {name for kind, name, _text in records if kind == "prop"}


# ────────────────────────────────────────────────────────────────
# 「無関係な実データ」フィクスチャ
# ────────────────────────────────────────────────────────────────


def _make_real_fixture(context):
    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]

    coma = page.comas.add()
    coma.id = "c01"
    coma.coma_id = "c01"
    coma.shape_type = "rect"
    coma.rect_width_mm = 80.0
    coma.rect_height_mm = 60.0
    coma.border.visible = True
    coma.border.style = "solid"
    coma.border.width_mm = 0.6
    coma.border.color = (0.1, 0.1, 0.1, 1.0)
    coma.white_margin.enabled = True
    coma.white_margin.width_mm = 0.4

    text = page.texts.add()
    text.id = "t01"
    text.body = "実データ文字列"
    text.font_size_value = 24.0
    text.color = (0.2, 0.2, 0.2, 1.0)
    text.writing_mode = "vertical"

    balloon = page.balloons.add()
    balloon.id = "b01"
    balloon.shape = "ellipse"
    balloon.x_mm = 10.0
    balloon.y_mm = 10.0
    balloon.width_mm = 30.0
    balloon.height_mm = 20.0
    tail = balloon.tails.add()
    tail.type = "straight"
    tail.line_type = "wedge"
    tail.direction_deg = 90.0
    tail.length_mm = 12.0
    tail.root_width_mm = 4.0

    fill = scene.bmanga_fill_layers.add()
    fill.id = "f01"
    fill.fill_type = "solid"
    fill.color = (0.3, 0.3, 0.3, 1.0)
    fill.opacity = 80.0

    grad = scene.bmanga_fill_layers.add()
    grad.id = "g01"
    grad.fill_type = "gradient"
    grad.color = (0.0, 0.0, 0.0, 1.0)
    grad.color2 = (1.0, 1.0, 1.0, 1.0)
    grad.gradient_type = "radial"
    grad.opacity = 90.0

    image_path = scene.bmanga_image_path_layers.add()
    image_path.id = "ip01"
    image_path.content_source = "shape"
    image_path.shape_kind = "circle"
    image_path.brush_size_mm = 6.0

    scene.bmanga_effect_line_params.effect_type = "focus"
    scene.bmanga_effect_line_params.brush_size_mm = 0.4
    scene.bmanga_effect_line_params.max_line_count = 42
    scene.bmanga_active_layer_kind = "effect"

    return {
        "coma": coma,
        "text": text,
        "balloon": balloon,
        "tail": tail,
        "fill": fill,
        "gradient": grad,
        "image_path": image_path,
    }


def _snapshot_real_data(context, fixture, effect_line_core) -> dict:
    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    return {
        "object_count": len(bpy.data.objects),
        "coma_count": len(page.comas),
        "text_count": len(page.texts),
        "balloon_count": len(page.balloons),
        "tail_count": len(fixture["balloon"].tails),
        "fill_layer_count": len(scene.bmanga_fill_layers),
        "image_path_count": len(scene.bmanga_image_path_layers),
        "coma_border": (
            fixture["coma"].border.style,
            round(float(fixture["coma"].border.width_mm), 6),
            tuple(round(float(c), 6) for c in fixture["coma"].border.color),
            bool(fixture["coma"].white_margin.enabled),
            round(float(fixture["coma"].white_margin.width_mm), 6),
        ),
        "text": (
            fixture["text"].body,
            round(float(fixture["text"].font_size_value), 6),
            tuple(round(float(c), 6) for c in fixture["text"].color),
            fixture["text"].writing_mode,
        ),
        "tail": (
            fixture["tail"].type,
            fixture["tail"].line_type,
            round(float(fixture["tail"].direction_deg), 6),
            round(float(fixture["tail"].length_mm), 6),
            round(float(fixture["tail"].root_width_mm), 6),
        ),
        "fill": (
            fixture["fill"].fill_type,
            tuple(round(float(c), 6) for c in fixture["fill"].color),
            round(float(fixture["fill"].opacity), 6),
        ),
        "gradient": (
            fixture["gradient"].fill_type,
            tuple(round(float(c), 6) for c in fixture["gradient"].color),
            tuple(round(float(c), 6) for c in fixture["gradient"].color2),
            fixture["gradient"].gradient_type,
            round(float(fixture["gradient"].opacity), 6),
        ),
        "image_path": (
            fixture["image_path"].content_source,
            fixture["image_path"].shape_kind,
            round(float(fixture["image_path"].brush_size_mm), 6),
        ),
        "effect_line_params": effect_line_core.effect_params_to_dict(scene.bmanga_effect_line_params),
        "active_layer_kind": str(scene.bmanga_active_layer_kind),
    }


# ────────────────────────────────────────────────────────────────
# タイプ別チェック
# ────────────────────────────────────────────────────────────────


def _check_border(context, preset_detail_op) -> None:
    scratch = context.window_manager.bmanga_preset_scratch_border
    preset_detail_op._reset_props(scratch)
    preset_detail_op._reset_props(scratch.border)
    preset_detail_op._reset_props(scratch.white_margin)
    scratch.border.style = "dashed"
    scratch.border.width_mm = 1.2
    scratch.border.color = (0.5, 0.1, 0.1, 1.0)
    scratch.white_margin.enabled = True
    scratch.white_margin.width_mm = 0.8
    preset_detail_op._save_border(context, "TestBorderPreset", "説明A")

    preset_detail_op._reset_props(scratch)
    preset_detail_op._reset_props(scratch.border)
    preset_detail_op._reset_props(scratch.white_margin)
    description = preset_detail_op._load_border(context, "TestBorderPreset")
    _check(description == "説明A", f"border: 説明の読込 (実際: {description!r})")
    _check(scratch.border.style == "dashed", "border: style の読込")
    _check(abs(scratch.border.width_mm - 1.2) < 1.0e-4, "border: width_mm の読込")
    _check(bool(scratch.white_margin.enabled), "border: white_margin.enabled の読込")

    scratch.border.width_mm = 2.4
    scratch.border.style = "solid"
    preset_detail_op._save_border(context, "TestBorderPreset", "説明B")

    preset_detail_op._reset_props(scratch)
    preset_detail_op._reset_props(scratch.border)
    preset_detail_op._reset_props(scratch.white_margin)
    description2 = preset_detail_op._load_border(context, "TestBorderPreset")
    _check(description2 == "説明B", "border: 上書き後の説明の読込")
    _check(scratch.border.style == "solid", "border: 上書き後の style の読込")
    _check(abs(scratch.border.width_mm - 2.4) < 1.0e-4, "border: 上書き後の width_mm の読込")

    # inner_color は white_margin.placement が inside/both のときだけ描画
    # される (draw_coma_white_margin_settings の既存の条件分岐)。draw() の
    # 網羅確認のため both にしておく。
    scratch.white_margin.placement = "both"
    records: list[tuple[str, str, str]] = []
    draw_preset_detail(
        MOD_NAME,
        _RecordingLayout(records),
        context,
        "border",
        scratch,
        preset_name="TestBorderPreset",
    )
    ops = _ops(records)
    for forbidden in (
        "bmanga.border_preset_add_local",
        "bmanga.border_preset_rename",
        "bmanga.border_preset_duplicate",
        "bmanga.border_preset_delete",
        "bmanga.border_preset_move",
    ):
        _check(forbidden not in ops, f"border: draw に実コマ用ボタンが出ない ({forbidden})")
    props = _props(records)
    for expected in ("style", "width_mm", "color", "outer_color", "inner_color"):
        _check(expected in props, f"border: draw に {expected} が含まれる")


def _check_text(context, preset_detail_op) -> None:
    scratch = context.window_manager.bmanga_preset_scratch_text
    preset_detail_op._reset_props(scratch)
    scratch.font_size_unit = "q"
    scratch.font_size_value = 18.0
    scratch.writing_mode = "horizontal"
    scratch.color = (0.1, 0.2, 0.3, 1.0)
    scratch.stroke_enabled = True
    scratch.stroke_width_mm = 0.3
    preset_detail_op._save_text(context, "TestTextPreset", "説明A")

    preset_detail_op._reset_props(scratch)
    description = preset_detail_op._load_text(context, "TestTextPreset")
    _check(description == "説明A", f"text: 説明の読込 (実際: {description!r})")
    _check(scratch.writing_mode == "horizontal", "text: writing_mode の読込")
    _check(abs(scratch.font_size_value - 18.0) < 1.0e-3, "text: font_size_value の読込")
    _check(bool(scratch.stroke_enabled), "text: stroke_enabled の読込")

    scratch.writing_mode = "vertical"
    scratch.font_size_value = 32.0
    preset_detail_op._save_text(context, "TestTextPreset", "説明B")

    preset_detail_op._reset_props(scratch)
    description2 = preset_detail_op._load_text(context, "TestTextPreset")
    _check(description2 == "説明B", "text: 上書き後の説明の読込")
    _check(scratch.writing_mode == "vertical", "text: 上書き後の writing_mode の読込")
    _check(abs(scratch.font_size_value - 32.0) < 1.0e-3, "text: 上書き後の font_size_value の読込")

    records: list[tuple[str, str, str]] = []
    draw_preset_detail(
        MOD_NAME,
        _RecordingLayout(records),
        context,
        "text",
        scratch,
        preset_name="TestTextPreset",
    )
    ops = _ops(records)
    for forbidden in ("bmanga.text_ruby_add_dialog", "bmanga.text_ruby_clear", "bmanga.text_meta_dialog"):
        _check(forbidden not in ops, f"text: draw に実テキスト用ボタンが出ない ({forbidden})")
    # v0.6.501: 保存対象なのでプリセットモードでも、固定session対象を渡す
    # 選択式の専用操作として表示する（旧モジュール大域IDは使わない）。
    _check(
        any(
            kind == "operator_menu_enum"
            and op_id == "bmanga.detail_text_linked_balloon_set"
            for kind, op_id, _text in records
        ),
        "text: draw に固定対象のリンクフキダシ選択が出る",
    )
    props = _props(records)
    for expected in ("font_size_value", "writing_mode", "color", "stroke_enabled", "line_height"):
        _check(expected in props, f"text: draw に {expected} が含まれる")
    for absent in ("x_mm", "speaker_name"):
        _check(absent not in props, f"text: draw に実テキスト専用の {absent} が出ない")


def _check_effect_line(context, preset_detail_op, effect_line_core) -> None:
    scratch = context.window_manager.bmanga_preset_scratch_effect_line
    preset_detail_op._reset_props(scratch)
    scratch.effect_type = "uni_flash"
    scratch.brush_size_mm = 0.5
    scratch.max_line_count = 88
    scratch.opacity = 70.0
    preset_detail_op._save_effect_line(context, "TestEffectLinePreset", "説明A")

    preset_detail_op._reset_props(scratch)
    description = preset_detail_op._load_effect_line(context, "TestEffectLinePreset")
    _check(description == "説明A", f"effect_line: 説明の読込 (実際: {description!r})")
    _check(scratch.effect_type == "uni_flash", "effect_line: effect_type の読込")
    _check(abs(scratch.brush_size_mm - 0.5) < 1.0e-4, "effect_line: brush_size_mm の読込")
    _check(scratch.max_line_count == 88, "effect_line: max_line_count の読込")

    scratch.brush_size_mm = 1.1
    scratch.max_line_count = 12
    preset_detail_op._save_effect_line(context, "TestEffectLinePreset", "説明B")

    preset_detail_op._reset_props(scratch)
    description2 = preset_detail_op._load_effect_line(context, "TestEffectLinePreset")
    _check(description2 == "説明B", "effect_line: 上書き後の説明の読込")
    _check(abs(scratch.brush_size_mm - 1.1) < 1.0e-4, "effect_line: 上書き後の brush_size_mm の読込")
    _check(scratch.max_line_count == 12, "effect_line: 上書き後の max_line_count の読込")

    # スクラッチ編集が実ツール設定 (scene.bmanga_effect_line_params) を
    # 書き換えないこと (core/effect_line.py _on_params_changed のポインタ
    # ガードの直接確認)。scene.bmanga_active_layer_kind == "effect" の状態で
    # 多数のプロパティを変更し、実ツール設定が一切動かないことを見る。
    before = effect_line_core.effect_params_to_dict(context.scene.bmanga_effect_line_params)
    scratch.brush_size_mm = 9.9
    scratch.effect_type = "speed"
    scratch.opacity = 12.0
    scratch.max_line_count = 999
    after = effect_line_core.effect_params_to_dict(context.scene.bmanga_effect_line_params)
    _check(
        before == after,
        "effect_line: スクラッチ編集が実ツール設定 (scene.bmanga_effect_line_params) を変えない",
    )
    # 次のロード確認のため、上の直接編集で汚したスクラッチを既知の状態へ戻す。
    preset_detail_op._reset_props(scratch)
    preset_detail_op._load_effect_line(context, "TestEffectLinePreset")

    records: list[tuple[str, str, str]] = []
    draw_preset_detail(
        MOD_NAME,
        _RecordingLayout(records),
        context,
        "effect_line",
        scratch,
        preset_name="TestEffectLinePreset",
        params=scratch,
    )
    ops = _ops(records)
    for forbidden in ("bmanga.effect_line_generate", "bmanga.effect_line_base_path_edit"):
        _check(forbidden not in ops, f"effect_line: draw に実オブジェクト用ボタンが出ない ({forbidden})")
    props = _props(records)
    for expected in ("effect_type", "brush_size_mm", "max_line_count", "opacity"):
        _check(expected in props, f"effect_line: draw に {expected} が含まれる")


def _check_fill(context, preset_detail_op) -> None:
    scratch = context.window_manager.bmanga_preset_scratch_fill
    preset_detail_op._reset_props(scratch)
    scratch.fill_type = "solid"
    scratch.color = (0.4, 0.1, 0.1, 1.0)
    scratch.opacity = 55.0
    preset_detail_op._save_fill(context, "TestFillPreset", "説明A")

    preset_detail_op._reset_props(scratch)
    description = preset_detail_op._load_fill(context, "TestFillPreset")
    _check(description == "説明A", f"fill: 説明の読込 (実際: {description!r})")
    _check(scratch.fill_type == "solid", "fill: fill_type が solid で読み込まれる")
    _check(abs(scratch.opacity - 55.0) < 1.0e-3, "fill: opacity の読込")

    scratch.color = (0.9, 0.8, 0.7, 1.0)
    scratch.opacity = 33.0
    preset_detail_op._save_fill(context, "TestFillPreset", "説明B")

    preset_detail_op._reset_props(scratch)
    description2 = preset_detail_op._load_fill(context, "TestFillPreset")
    _check(description2 == "説明B", "fill: 上書き後の説明の読込")
    _check(abs(scratch.opacity - 33.0) < 1.0e-3, "fill: 上書き後の opacity の読込")

    records: list[tuple[str, str, str]] = []
    draw_preset_detail(
        MOD_NAME,
        _RecordingLayout(records),
        context,
        "fill",
        scratch,
        preset_name="TestFillPreset",
    )
    ops = _ops(records)
    _check(not ops, f"fill: draw に操作ボタンが出ない (実際: {ops})")
    props = _props(records)
    for expected in ("color", "opacity"):
        _check(expected in props, f"fill: draw に {expected} が含まれる")
    for absent in ("title", "visible", "locked", "rotation_deg", "fill_type"):
        _check(absent not in props, f"fill: draw に実レイヤー専用の {absent} が出ない")


def _check_gradient(context, preset_detail_op) -> None:
    scratch = context.window_manager.bmanga_preset_scratch_gradient
    preset_detail_op._reset_props(scratch)
    scratch.fill_type = "gradient"
    scratch.color = (0.0, 0.0, 0.0, 1.0)
    scratch.color2 = (1.0, 1.0, 1.0, 1.0)
    scratch.gradient_type = "radial"
    scratch.opacity = 66.0
    preset_detail_op._save_gradient(context, "TestGradientPreset", "説明A")

    preset_detail_op._reset_props(scratch)
    description = preset_detail_op._load_gradient(context, "TestGradientPreset")
    _check(description == "説明A", f"gradient: 説明の読込 (実際: {description!r})")
    _check(scratch.fill_type == "gradient", "gradient: fill_type が gradient で読み込まれる")
    _check(scratch.gradient_type == "radial", "gradient: gradient_type の読込")

    scratch.gradient_type = "linear"
    scratch.opacity = 20.0
    preset_detail_op._save_gradient(context, "TestGradientPreset", "説明B")

    preset_detail_op._reset_props(scratch)
    description2 = preset_detail_op._load_gradient(context, "TestGradientPreset")
    _check(description2 == "説明B", "gradient: 上書き後の説明の読込")
    _check(scratch.gradient_type == "linear", "gradient: 上書き後の gradient_type の読込")

    records: list[tuple[str, str, str]] = []
    draw_preset_detail(
        MOD_NAME,
        _RecordingLayout(records),
        context,
        "gradient",
        scratch,
        preset_name="TestGradientPreset",
    )
    props = _props(records)
    for expected in ("color", "color2", "gradient_type", "opacity"):
        _check(expected in props, f"gradient: draw に {expected} が含まれる")


def _check_image_path(context, preset_detail_op) -> None:
    scratch = context.window_manager.bmanga_preset_scratch_image_path
    preset_detail_op._reset_props(scratch)
    scratch.content_source = "shape"
    scratch.shape_kind = "polygon"
    scratch.shape_sides = 5
    scratch.brush_size_mm = 8.0
    scratch.opacity = 60.0
    preset_detail_op._save_image_path(context, "TestImagePathPreset", "説明A")

    preset_detail_op._reset_props(scratch)
    description = preset_detail_op._load_image_path(context, "TestImagePathPreset")
    _check(description == "説明A", f"image_path: 説明の読込 (実際: {description!r})")
    _check(scratch.shape_kind == "polygon", "image_path: shape_kind の読込")
    _check(scratch.shape_sides == 5, "image_path: shape_sides の読込")

    scratch.shape_sides = 8
    scratch.brush_size_mm = 15.0
    preset_detail_op._save_image_path(context, "TestImagePathPreset", "説明B")

    preset_detail_op._reset_props(scratch)
    description2 = preset_detail_op._load_image_path(context, "TestImagePathPreset")
    _check(description2 == "説明B", "image_path: 上書き後の説明の読込")
    _check(scratch.shape_sides == 8, "image_path: 上書き後の shape_sides の読込")

    records: list[tuple[str, str, str]] = []
    draw_preset_detail(
        MOD_NAME,
        _RecordingLayout(records),
        context,
        "image_path",
        scratch,
        preset_name="TestImagePathPreset",
    )
    ops = _ops(records)
    _check(not ops, f"image_path: draw に操作ボタンが出ない (実際: {ops})")
    props = _props(records)
    for expected in ("brush_size_mm", "opacity", "color"):
        _check(expected in props, f"image_path: draw に {expected} が含まれる")
    for absent in ("title", "visible", "locked"):
        _check(absent not in props, f"image_path: draw に実レイヤー専用の {absent} が出ない")


def _check_tail(context, preset_detail_op) -> None:
    scratch = context.window_manager.bmanga_preset_scratch_tail
    preset_detail_op._reset_props(scratch)
    scratch.points.clear()
    scratch.type = "curve"
    scratch.line_type = "wedge"
    scratch.root_width_mm = 5.0
    scratch.tip_width_mm = 1.0
    scratch.length_mm = 14.0
    scratch.curve_bend = 0.2
    preset_detail_op._save_tail(context, "TestTailPreset", "説明A")

    preset_detail_op._reset_props(scratch)
    scratch.points.clear()
    description = preset_detail_op._load_tail(context, "TestTailPreset")
    _check(description == "説明A", f"tail: 説明の読込 (実際: {description!r})")
    _check(scratch.line_type == "wedge", "tail: line_type の読込")
    _check(abs(scratch.root_width_mm - 5.0) < 1.0e-4, "tail: root_width_mm の読込")

    scratch.root_width_mm = 9.0
    scratch.line_type = "ellipse_chain"
    preset_detail_op._save_tail(context, "TestTailPreset", "説明B")

    preset_detail_op._reset_props(scratch)
    scratch.points.clear()
    description2 = preset_detail_op._load_tail(context, "TestTailPreset")
    _check(description2 == "説明B", "tail: 上書き後の説明の読込")
    _check(scratch.line_type == "ellipse_chain", "tail: 上書き後の line_type の読込")
    _check(abs(scratch.root_width_mm - 9.0) < 1.0e-4, "tail: 上書き後の root_width_mm の読込")

    records: list[tuple[str, str, str]] = []
    draw_preset_detail(
        MOD_NAME,
        _RecordingLayout(records),
        context,
        "tail",
        scratch,
        preset_name="TestTailPreset",
    )
    ops = _ops(records)
    for forbidden in (
        "bmanga.balloon_tail_remove",
        "bmanga.balloon_tail_preset_apply",
        "bmanga.balloon_tail_preset_save",
    ):
        _check(forbidden not in ops, f"tail: draw に実しっぽ用ボタンが出ない ({forbidden})")
    props = _props(records)
    for expected in ("root_width_mm", "tip_width_mm", "line_type"):
        _check(expected in props, f"tail: draw に {expected} が含まれる")


def _check_balloon_description_only(context, preset_detail_op, balloon_presets) -> None:
    """balloon タイプは対象外 (頂点座標列プリセットのため、専用ツール
    ダイアログが存在しない)。説明編集のみ現行どおり動作することを確認する。
    """
    balloon_presets.save_local_preset(
        None, "TestBalloonPreset", "説明A", [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    )
    data = preset_detail_op._load_balloon("TestBalloonPreset")
    _check(data is not None, "balloon: プリセットデータの読込")
    if data is None:
        return
    _check(str(data.get("description", "")) == "説明A", "balloon: 説明の読込")

    preset_detail_op._save_balloon("TestBalloonPreset", "説明B", data)
    reloaded = preset_detail_op._load_balloon("TestBalloonPreset")
    _check(reloaded is not None and str(reloaded.get("description", "")) == "説明B", "balloon: 説明の上書き保存")

    records: list[tuple[str, str, str]] = []
    label_texts = []

    class _Op:
        preset_type = "balloon"
        preset_name = "TestBalloonPreset"

        def draw(self, _context):
            layout = _RecordingLayout(records)
            layout.label(text="このプリセットタイプは詳細編集未対応です")
            label_texts.append("このプリセットタイプは詳細編集未対応です")

    _Op().draw(context)
    _check(
        "このプリセットタイプは詳細編集未対応です" in label_texts,
        "balloon: 詳細編集未対応の案内文言 (draw() のフォールバック分岐と同一文言)",
    )


def _check_operator_dispatch_tables(preset_detail_op, detail_drawer_dispatcher) -> None:
    target_kinds = {
        "border": "coma",
        "text": "text",
        "effect_line": "effect",
        "fill": "fill",
        "gradient": "fill",
        "image_path": "image_path",
        "tail": "balloon_tail",
    }
    for key, target_kind in target_kinds.items():
        _check(key in preset_detail_op._LOADERS, f"dispatch: _LOADERS に {key} が登録されている")
        _check(key in preset_detail_op._SAVERS, f"dispatch: _SAVERS に {key} が登録されている")
        _check(
            key in preset_detail_op._PRESET_SCRATCH_ATTRS,
            f"dispatch: 一時設定に {key} が登録されている",
        )
        _check(
            target_kind in detail_drawer_dispatcher._BODY_DRAWERS,
            f"dispatch: 共通本文に {target_kind} が登録されている",
        )
    _check(
        not hasattr(preset_detail_op, "_DRAWERS"),
        "dispatch: プリセット専用描画表を持たず共通描画だけを使う",
    )
    _check("balloon" not in preset_detail_op._LOADERS, "dispatch: balloon は _LOADERS に含まれない (専用フォールバック)")


def _open_text_preset_session(context, name: str):
    detail = _sub("utils.detail_dialog")
    state = _sub("utils.detail_dialog_state")
    adapters = _sub("utils.detail_state_adapters")
    runtime = _sub("operators.detail_dialog_runtime")
    scratch = context.window_manager.bmanga_preset_scratch_text
    target = detail.resolve_preset_detail_target("text", name, scratch)
    session = state.begin_detail_session(
        target,
        detail.DetailMode.PRESET,
        registry=adapters.ACTUAL_DETAIL_STATE_REGISTRY,
        target_validator=lambda identity: identity.stable_id == target.stable_id,
    )
    runtime.register_preset_session(session)
    return session


def _check_preset_session_lock_and_failed_save_cleanup(context, preset_detail_op) -> None:
    runtime = _sub("operators.detail_dialog_runtime")
    state = _sub("utils.detail_dialog_state")
    operator_cls = preset_detail_op.BMANGA_OT_preset_detail_edit
    scratch = context.window_manager.bmanga_preset_scratch_text

    preset_detail_op._reset_props(scratch)
    scratch.font_size_value = 31.0
    lock_session = _open_text_preset_session(context, "TestTextPreset")
    original_loader = preset_detail_op._LOADERS["text"]
    loader_calls = []

    def _contaminating_loader(_context, _name):
        loader_calls.append(True)
        scratch.font_size_value = 999.0
        return ""

    class _InvokeProbe:
        preset_type = "text"
        preset_name = "TestTextPreset"
        parent_session_token = ""
        parent_target_kind = ""
        parent_target_id = ""
        _detail_session = None

        def _parent_session_is_valid(self):
            return True

        def report(self, _level, _message):
            return None

    try:
        preset_detail_op._LOADERS["text"] = _contaminating_loader
        result = operator_cls.invoke(_InvokeProbe(), context, SimpleNamespace())
        _check("CANCELLED" in result, "同種プリセット詳細の2画面目を拒否する")
        _check(not loader_calls, "2画面目の読込前に拒否して編集中scratchを守る")
        _check(abs(float(scratch.font_size_value) - 31.0) < 1.0e-6, "拒否後も1画面目の一時値を保つ")
    finally:
        preset_detail_op._LOADERS["text"] = original_loader
        state.cancel_detail_session(lock_session)
        runtime.unregister_preset_session(lock_session)

    preset_detail_op._reset_props(scratch)
    scratch.font_size_value = 41.0
    failure_session = _open_text_preset_session(context, "TestTextPreset")
    scratch.font_size_value = 42.0
    original_saver = preset_detail_op._SAVERS["text"]

    class _ExecuteProbe:
        preset_type = "text"
        preset_name = "TestTextPreset"
        parent_session_token = ""
        parent_target_kind = ""
        parent_target_id = ""
        description_text = "保存失敗"
        _detail_session = failure_session
        _parent_session_is_valid = operator_cls._parent_session_is_valid
        _release_failed_session = operator_cls._release_failed_session
        _restore_open_session = operator_cls._restore_open_session

        def report(self, _level, _message):
            return None

    def _fail_save(_context, _name, _description):
        raise OSError("intentional preset save failure")

    probe = _ExecuteProbe()
    try:
        preset_detail_op._SAVERS["text"] = _fail_save
        result = operator_cls.execute(probe, context)
    finally:
        preset_detail_op._SAVERS["text"] = original_saver
    _check("CANCELLED" in result, "プリセット保存失敗を中止として返す")
    _check(probe._detail_session is None, "保存失敗後にOperatorのセッション参照を解放する")
    _check(
        failure_session.token not in runtime._OPEN_PRESET_SESSIONS,
        "保存失敗後にプリセットセッション登録を解放する",
    )
    _check(
        abs(float(scratch.font_size_value) - 41.0) < 1.0e-6,
        "保存失敗後に一時設定を開始時へ戻す",
    )


# ────────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────────


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_preset_detail_tool_dialog_"))
    old_config = os.environ.get("BMANGA_USER_CONFIG_DIR")
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PresetDetailDialog.bmanga"))
        assert "FINISHED" in result, result

        context = bpy.context
        preset_detail_op = _sub("operators.preset_detail_op")
        detail_drawer_dispatcher = _sub("panels.detail_drawers.dispatcher")
        balloon_presets = _sub("io.balloon_presets")
        effect_line_core = _sub("core.effect_line")

        _check_operator_dispatch_tables(preset_detail_op, detail_drawer_dispatcher)
        _check_preset_session_lock_and_failed_save_cleanup(context, preset_detail_op)

        fixture = _make_real_fixture(context)
        before = _snapshot_real_data(context, fixture, effect_line_core)

        try:
            _check_border(context, preset_detail_op)
            _check_text(context, preset_detail_op)
            _check_effect_line(context, preset_detail_op, effect_line_core)
            _check_fill(context, preset_detail_op)
            _check_gradient(context, preset_detail_op)
            _check_image_path(context, preset_detail_op)
            _check_tail(context, preset_detail_op)
        except Exception:  # noqa: BLE001
            FAILURES.append("いずれかのタイプ別チェックが例外で中断した")
            traceback.print_exc()

        after = _snapshot_real_data(context, fixture, effect_line_core)
        for key in before:
            _check(
                before[key] == after[key],
                f"実データ不変: {key} が7タイプ分のスクラッチ編集の前後で一致 "
                f"(前: {before[key]!r} / 後: {after[key]!r})",
            )

        _check_balloon_description_only(context, preset_detail_op, balloon_presets)

        print("BMANGA_PRESET_DETAIL_TOOL_DIALOG_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        if old_config is None:
            os.environ.pop("BMANGA_USER_CONFIG_DIR", None)
        else:
            os.environ["BMANGA_USER_CONFIG_DIR"] = old_config
        shutil.rmtree(temp_root, ignore_errors=True)
    print(f"\n結果: 失敗 {len(FAILURES)} 件", flush=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    if code:
        sys.exit(code)
