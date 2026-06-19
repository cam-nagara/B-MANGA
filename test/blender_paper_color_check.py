"""Blender 実機用: ``paper.paper_color`` 変更が paper_bg Material に即時反映されるかの回帰テスト.

期待: 用紙パネルで ``paper_color`` を変えると、 共有 Material
``BManga_PaperBackground`` の Emission Color と ``mat.diffuse_color`` が
即時に追従する (Solid 表示 + color_type=MATERIAL でビューポートに反映)。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _emission_color(mat) -> tuple[float, float, float, float]:
    for node in mat.node_tree.nodes:
        if node.type == "EMISSION":
            c = node.inputs["Color"].default_value
            return (float(c[0]), float(c[1]), float(c[2]), float(c[3]))
    raise AssertionError("Emission node not found in BManga_PaperBackground")


def _approx_equal(a, b, tol: float = 1e-5) -> bool:
    return all(abs(float(x) - float(y)) < tol for x, y in zip(a, b))


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_paper_color_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PaperColor.bmanga"))
        assert result == {"FINISHED"}, result

        from bmanga_dev.core.work import get_work
        from bmanga_dev.utils import paper_bg_object as pbg

        work = get_work(bpy.context)
        assert work is not None
        paper = work.paper

        mat = bpy.data.materials.get(pbg.PAPER_BG_MATERIAL_NAME)
        assert mat is not None, "BManga_PaperBackground material should exist after work_new"
        assert _approx_equal(mat.diffuse_color, (1.0, 1.0, 1.0, 1.0)), tuple(mat.diffuse_color)
        assert _approx_equal(_emission_color(mat), (1.0, 1.0, 1.0, 1.0)), _emission_color(mat)

        # paper_bg Object が同 Material を参照していることを確認
        bg_obj = bpy.data.objects.get(f"{pbg.PAPER_BG_NAME_PREFIX}{paper}")  # placeholder, see below
        for obj in bpy.data.objects:
            if obj.get(pbg.PROP_BG_KIND) == "page":
                bg_obj = obj
                break
        assert bg_obj is not None, "paper_bg Object should exist for the initial page"
        assert bg_obj.data.materials and bg_obj.data.materials[0] is mat

        # 1. RED に変更 → diffuse / emission が追従
        paper.paper_color = (0.8, 0.1, 0.1, 1.0)
        assert _approx_equal(mat.diffuse_color, (0.8, 0.1, 0.1, 1.0)), tuple(mat.diffuse_color)
        assert _approx_equal(_emission_color(mat), (0.8, 0.1, 0.1, 1.0)), _emission_color(mat)

        # 2. GREEN に変更 → 連続変更も追従
        paper.paper_color = (0.1, 0.7, 0.2, 1.0)
        assert _approx_equal(mat.diffuse_color, (0.1, 0.7, 0.2, 1.0)), tuple(mat.diffuse_color)
        assert _approx_equal(_emission_color(mat), (0.1, 0.7, 0.2, 1.0)), _emission_color(mat)

        # 3. WHITE に戻る → 戻し操作も追従
        paper.paper_color = (1.0, 1.0, 1.0, 1.0)
        assert _approx_equal(mat.diffuse_color, (1.0, 1.0, 1.0, 1.0)), tuple(mat.diffuse_color)
        assert _approx_equal(_emission_color(mat), (1.0, 1.0, 1.0, 1.0)), _emission_color(mat)

        # 4. Material は同一インスタンスのまま (Mesh 側 slot を破棄していない)
        mat_after = bpy.data.materials.get(pbg.PAPER_BG_MATERIAL_NAME)
        assert mat_after is mat
        assert bg_obj.data.materials[0] is mat
    finally:
        if mod is not None:
            mod.unregister()

    print("BMANGA_PAPER_COLOR_OK")


if __name__ == "__main__":
    main()
