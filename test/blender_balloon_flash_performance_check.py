"""Blender 実機用: ウニフラ / 白抜き線フキダシの移動と再同期を軽量確認。"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_balloon_flash_perf",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_balloon_flash_perf"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _make_flash_balloon(context, page, page_key: str, style: str):
    from bname_dev_balloon_flash_perf.core import balloon as balloon_core
    from bname_dev_balloon_flash_perf.operators import balloon_op
    from bname_dev_balloon_flash_perf.utils import balloon_curve_object

    entry = balloon_op._create_balloon_entry(
        context,
        page,
        shape="ellipse",
        x=18.0 if style == "uni_flash" else 86.0,
        y=36.0,
        w=72.0,
        h=48.0,
        parent_kind="page",
        parent_key=page_key,
    )
    with balloon_curve_object.defer_auto_sync():
        entry.line_style = style
        balloon_core.apply_balloon_line_style_defaults(entry, force=True)
        if style == "uni_flash":
            entry.spacing_mode = "distance"
            entry.spacing_distance_mm = 0.42
            entry.max_line_count = 1000
            entry.bundle_enabled = True
            entry.bundle_line_count = 5
            entry.white_underlay_width_percent = 135.0
        else:
            entry.flash_white_outline_count = 10
            entry.flash_white_outline_width_mm = 14.0
            entry.flash_white_outline_white_line_count = 72
            entry.flash_white_outline_black_line_count = 6
            entry.flash_white_outline_spacing_mm = 0.16
    body = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    if body is None:
        raise AssertionError("フキダシ本体を作成できません")
    return entry, body


def _flash_object(entry, flash_mesh_module):
    obj = bpy.data.objects.get(flash_mesh_module._flash_effect_line_mesh_object_name(entry.id))
    if obj is None or getattr(obj, "type", "") != "MESH":
        raise AssertionError(f"{entry.line_style}: 放射状の線実体がありません")
    if len(getattr(obj.data, "polygons", []) or []) <= 0:
        raise AssertionError(f"{entry.line_style}: 放射状の線実体が空です")
    return obj


def _assert_cached_and_move_light(style: str, context, page, page_key: str) -> None:
    from bname_dev_balloon_flash_perf.operators import balloon_op
    from bname_dev_balloon_flash_perf.utils import balloon_curve_object
    from bname_dev_balloon_flash_perf.utils import balloon_flash_effect_line_mesh

    entry, body = _make_flash_balloon(context, page, page_key, style)
    flash_obj = _flash_object(entry, balloon_flash_effect_line_mesh)
    mesh = flash_obj.data
    signature = str(flash_obj.get(balloon_flash_effect_line_mesh._FLASH_EFFECT_MESH_SIGNATURE_PROP, "") or "")
    if not signature:
        raise AssertionError(f"{style}: キャッシュ判定が記録されていません")

    original_generated = balloon_flash_effect_line_mesh._generated_strokes

    def fail_generated(_entry):
        raise AssertionError(f"{style}: 同じ設定で放射状の線を再生成しています")

    balloon_flash_effect_line_mesh._generated_strokes = fail_generated
    try:
        again = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        if again is None:
            raise AssertionError(f"{style}: 再同期でフキダシ本体が消えました")
        cached_obj = _flash_object(entry, balloon_flash_effect_line_mesh)
        if cached_obj.data is not mesh:
            raise AssertionError(f"{style}: 同じ設定で線メッシュが差し替わっています")
        before_x = float(body.location.x)
        before_y = float(body.location.y)
        balloon_op._move_balloon_with_texts(page, entry, float(entry.x_mm) + 6.0, float(entry.y_mm) - 4.0)
        moved = balloon_curve_object.find_balloon_object(entry.id)
        if moved is None:
            raise AssertionError(f"{style}: 移動後にフキダシ本体がありません")
        if abs(float(moved.location.x) - before_x) <= 1.0e-7 and abs(float(moved.location.y) - before_y) <= 1.0e-7:
            raise AssertionError(f"{style}: フキダシ本体が移動していません")
        moved_flash = _flash_object(entry, balloon_flash_effect_line_mesh)
        if moved_flash.data is not mesh:
            raise AssertionError(f"{style}: 移動だけで線メッシュが差し替わっています")
    finally:
        balloon_flash_effect_line_mesh._generated_strokes = original_generated

    calls = {"count": 0}

    def counted_generated(_entry):
        calls["count"] += 1
        return original_generated(_entry)

    balloon_flash_effect_line_mesh._generated_strokes = counted_generated
    try:
        with balloon_curve_object.defer_auto_sync():
            if style == "uni_flash":
                entry.spacing_distance_mm = float(entry.spacing_distance_mm) + 0.13
            else:
                entry.flash_white_outline_width_mm = float(entry.flash_white_outline_width_mm) + 1.0
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        if calls["count"] <= 0:
            raise AssertionError(f"{style}: 線設定の変更で再生成が走っていません")
    finally:
        balloon_flash_effect_line_mesh._generated_strokes = original_generated


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_flash_perf_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "BalloonFlashPerf.bname"))
        if "FINISHED" not in result:
            raise AssertionError("作品作成に失敗しました")

        from bname_dev_balloon_flash_perf.core.work import get_work
        from bname_dev_balloon_flash_perf.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        if work is None or not work.loaded:
            raise AssertionError("作品データを取得できません")
        page = work.pages[0]
        page_key = page_stack_key(page)

        _assert_cached_and_move_light("uni_flash", context, page, page_key)
        _assert_cached_and_move_light("white_outline", context, page, page_key)
        print("BNAME_BALLOON_FLASH_PERFORMANCE_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
