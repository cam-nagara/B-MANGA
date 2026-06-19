"""Blender実機用: アドオン有効化時に効果線表示用ノードを作らないことを確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import time
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_lazy_effect_nodes",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_lazy_effect_nodes"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    started = time.perf_counter()
    mod.register()
    return mod, time.perf_counter() - started


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_lazy_effect_nodes_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod, register_sec = _load_addon()

        from bmanga_dev_lazy_effect_nodes.utils import geometry_nodes_bridge as gn

        group_name = gn._group_name("effect_line")  # noqa: SLF001 - 実機監査
        if bpy.data.node_groups.get(group_name) is not None:
            raise AssertionError("アドオン有効化だけで効果線表示用ノードが作られています")

        work_dir = temp_root / "LazyEffectNodes.bmanga"
        result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")

        group = bpy.data.node_groups.get(group_name)
        if group is None or len(group.nodes) == 0:
            raise AssertionError("作品作成時に効果線表示用ノードが準備されていません")

        work_blend = work_dir / "work.blend"
        if not work_blend.is_file():
            raise AssertionError("作品ファイルが保存されていません")

        bpy.ops.wm.open_mainfile(filepath=str(work_blend))
        group = bpy.data.node_groups.get(group_name)
        if group is None or len(group.nodes) == 0:
            raise AssertionError("保存した作品ファイルに効果線表示用ノードが残っていません")

        # load_post が予約した遅延準備を一度解除し、以降の手動予約テストと分離する。
        gn.unregister()

        bpy.data.node_groups.remove(group)
        if bpy.data.node_groups.get(group_name) is not None:
            raise AssertionError("旧作品相当の欠損状態を作れませんでした")
        if not gn.ensure_effect_line_node_group_for_work(bpy.context):
            raise AssertionError("旧作品相当の欠損状態から表示用データを補完できません")
        group = bpy.data.node_groups.get(group_name)
        if group is None or len(group.nodes) == 0:
            raise AssertionError("補完後の効果線表示用データが空です")

        bpy.data.node_groups.remove(group)
        if not gn.schedule_effect_line_node_group_for_work(bpy.context, delay=60.0):
            raise AssertionError("読み込み後の遅延準備を予約できません")
        if not bool(gn._EFFECT_LINE_PREP_TIMER_SCHEDULED):  # noqa: SLF001 - 実機監査
            raise AssertionError("遅延準備の予約状態が記録されていません")
        mod.unregister()
        mod = None
        if bool(gn._EFFECT_LINE_PREP_TIMER_SCHEDULED):  # noqa: SLF001 - 実機監査
            raise AssertionError("アドオン無効化後も遅延準備の予約が残っています")

        print(f"BMANGA_LAZY_EFFECT_NODES_OK register_sec={register_sec:.4f}", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
