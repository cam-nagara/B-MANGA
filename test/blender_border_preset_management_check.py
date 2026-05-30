"""Blender実機用: コマ枠線プリセットの追加・改名・複製・削除・並べ替え確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


class _RecordingLayout:
    def __init__(self, records: list[tuple[str, str, str]]) -> None:
        self.records = records
        self.enabled = True
        self.active = True
        self.operator_context = "INVOKE_DEFAULT"

    def row(self, **_kwargs):
        return self

    def column(self, **_kwargs):
        return self

    def box(self):
        return self

    def label(self, text: str = "", **_kwargs) -> None:
        self.records.append(("label", "", text))

    def prop(self, _data, prop_name: str, text: str = "", **_kwargs) -> None:
        self.records.append(("prop", prop_name, text))

    def operator(self, op_id: str, text: str = "", **_kwargs):
        self.records.append(("operator", op_id, text))
        return type("_Op", (), {})()

    def separator(self) -> None:
        self.records.append(("separator", "", ""))

    def template_curve_mapping(self, *_args, **_kwargs) -> None:
        self.records.append(("curve", "", ""))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_border_preset_manage",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_border_preset_manage"] = mod
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
    coma.title = "preset_manage_probe"
    coma.shape_type = "rect"
    coma.rect_width_mm = 80.0
    coma.rect_height_mm = 60.0
    coma.border.visible = True
    coma.border.style = "solid"
    coma.border.width_mm = 0.75
    coma.white_margin.enabled = True
    coma.white_margin.width_mm = 0.5
    page.active_coma_index = 0
    return coma


def _names(border_presets, work_dir: Path) -> list[str]:
    return [preset.name for preset in border_presets.list_all_presets(work_dir)]


def _source(border_presets, work_dir: Path, name: str) -> str:
    preset = border_presets.load_preset_by_name(name, work_dir)
    assert preset is not None, f"プリセットが見つかりません: {name}"
    return preset.source


def _assert_rejected(callable_op, message: str) -> None:
    try:
        result = callable_op()
    except RuntimeError:
        return
    assert "CANCELLED" in result, message


def _assert_detail_ui(context, coma) -> None:
    from bname_dev_border_preset_manage.panels import coma_detail_panel

    records: list[tuple[str, str, str]] = []
    coma_detail_panel.draw_coma_border_settings(_RecordingLayout(records), context, coma)
    ops = {op_id for kind, op_id, _text in records if kind == "operator"}
    for op_id in (
        "bname.border_preset_add_local",
        "bname.border_preset_rename",
        "bname.border_preset_duplicate",
        "bname.border_preset_delete",
        "bname.border_preset_move",
    ):
        assert op_id in ops, f"コマ詳細設定に操作ボタンがありません: {op_id}"


def _assert_management_ops(context, work, page, coma) -> None:
    from bname_dev_border_preset_manage.io import border_presets

    work_dir = Path(work.work_dir)
    wm = context.window_manager
    initial = _names(border_presets, work_dir)
    for required in ("標準", "線無し", "極太", "輪郭ぼかし"):
        assert required in initial, f"同梱プリセットが見つかりません: {required}"

    result = bpy.ops.bname.border_preset_add_local(
        preset_name="管理A",
        description="追加テスト",
    )
    assert "FINISHED" in result, result
    assert "管理A" in _names(border_presets, work_dir)
    assert _source(border_presets, work_dir, "管理A") == "local"
    assert wm.bname_border_preset_selector == "管理A"
    assert coma.border.preset_name == "管理A"

    result = bpy.ops.bname.border_preset_rename(
        preset_name="管理A",
        new_name="管理B",
    )
    assert "FINISHED" in result, result
    names = _names(border_presets, work_dir)
    assert "管理A" not in names and "管理B" in names
    assert wm.bname_border_preset_selector == "管理B"
    assert coma.border.preset_name == "管理B"

    wm.bname_border_preset_selector = "標準"
    result = bpy.ops.bname.border_preset_duplicate(
        preset_name="標準",
        new_name="標準コピー管理",
    )
    assert "FINISHED" in result, result
    names = _names(border_presets, work_dir)
    assert "標準" in names and "標準コピー管理" in names
    assert _source(border_presets, work_dir, "標準コピー管理") == "local"

    _assert_rejected(
        lambda: bpy.ops.bname.border_preset_rename(
            preset_name="標準コピー管理",
            new_name="標準",
        ),
        "既存名への改名が拒否されていません",
    )
    assert "標準コピー管理" in _names(border_presets, work_dir)

    wm.bname_border_preset_selector = "輪郭ぼかし"
    result = bpy.ops.bname.border_preset_rename(
        preset_name="輪郭ぼかし",
        new_name="ぼかし改名管理",
    )
    assert "FINISHED" in result, result
    names = _names(border_presets, work_dir)
    assert "輪郭ぼかし" not in names and "ぼかし改名管理" in names
    assert "輪郭ぼかし" in {preset.name for preset in border_presets.list_global_presets()}
    assert _source(border_presets, work_dir, "ぼかし改名管理") == "local"

    result = bpy.ops.bname.border_preset_delete(preset_name="管理B")
    assert "FINISHED" in result, result
    assert "管理B" not in _names(border_presets, work_dir)

    wm.bname_border_preset_selector = "線無し"
    result = bpy.ops.bname.border_preset_delete(preset_name="線無し")
    assert "FINISHED" in result, result
    assert "線無し" not in _names(border_presets, work_dir)
    assert "線無し" in {preset.name for preset in border_presets.list_global_presets()}

    _assert_rejected(
        lambda: bpy.ops.bname.border_preset_delete(preset_name="存在しないプリセット"),
        "存在しないプリセットの削除が拒否されていません",
    )

    for name in ("並べ替えA", "並べ替えB", "並べ替えC"):
        result = bpy.ops.bname.border_preset_add_local(preset_name=name)
        assert "FINISHED" in result, result
    names = _names(border_presets, work_dir)
    assert names.index("並べ替えA") < names.index("並べ替えB") < names.index("並べ替えC")

    result = bpy.ops.bname.border_preset_move(preset_name="並べ替えC", direction="UP")
    assert "FINISHED" in result, result
    names = _names(border_presets, work_dir)
    assert names.index("並べ替えA") < names.index("並べ替えC") < names.index("並べ替えB")

    result = bpy.ops.bname.border_preset_move(preset_name="並べ替えC", direction="UP")
    assert "FINISHED" in result, result
    names = _names(border_presets, work_dir)
    assert names.index("並べ替えC") < names.index("並べ替えA") < names.index("並べ替えB")

    result = bpy.ops.bname.border_preset_move(preset_name="並べ替えC", direction="DOWN")
    assert "FINISHED" in result, result
    names = _names(border_presets, work_dir)
    assert names.index("並べ替えA") < names.index("並べ替えC") < names.index("並べ替えB")

    index_path = work_dir / "assets" / "borders" / border_presets.PRESET_INDEX_FILENAME
    assert index_path.is_file(), "プリセットの並び順ファイルがありません"
    assert "並べ替えC" in _names(border_presets, work_dir)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_border_preset_manage_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "BorderPresetManage.bname"))
        assert "FINISHED" in result, result

        context = bpy.context
        work = context.scene.bname_work
        page = work.pages[0]
        coma = _make_coma(page)
        _assert_detail_ui(context, coma)
        _assert_management_ops(context, work, page, coma)
        print("BNAME_BORDER_PRESET_MANAGEMENT_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
