"""B-MANGA Line: generated inner/intersection tubes keep their line colors."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import outline_setup  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for datablocks in (bpy.data.meshes, bpy.data.materials, bpy.data.node_groups):
        for item in list(datablocks):
            if item.users == 0:
                datablocks.remove(item)


def _surface_material(name: str):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    return mat


def _assert_generated_material(mat: bpy.types.Material, label: str) -> None:
    assert mat is not None, f"{label} の線素材が作成されていません"
    assert bool(mat.get(outline_setup.PROP_DOUBLE_SIDED, False)), (
        f"{label} の線素材が両面表示になっていません"
    )
    assert not bool(getattr(mat, "use_backface_culling", False)), (
        f"{label} の線素材が背面法カリングのままです"
    )
    assert mat.use_nodes and mat.node_tree is not None, f"{label} の線素材にノードがありません"
    assert any(
        node.bl_idname == "ShaderNodeRGB" and getattr(node, "label", "") == "BML_Color"
        for node in mat.node_tree.nodes
    ), f"{label} の線素材に線色ノードがありません"


def _make_source() -> bpy.types.Object:
    mesh = bpy.data.meshes.new("BML_generated_inner_source_mesh")
    mesh.from_pydata(
        [
            (-1.0, -0.5, 0.0),
            (-1.0, 0.0, 0.4),
            (-1.0, 0.5, 0.0),
            (1.0, -0.5, 0.0),
            (1.0, 0.0, 0.4),
            (1.0, 0.5, 0.0),
        ],
        [],
        [(0, 3, 4, 1), (1, 4, 5, 2)],
    )
    mesh.update()
    obj = bpy.data.objects.new("内部線_線色確認", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(_surface_material("内部線_白面"))
    settings = obj.bmanga_line_settings
    settings.inner_line_color = (1.0, 0.0, 0.0, 1.0)
    settings.intersection_color = (0.0, 1.0, 0.0, 1.0)
    return obj


def _assert_restore_repairs_old_inner_material(obj: bpy.types.Object) -> None:
    mat = outline_setup.get_line_material(obj, "inner")
    mat[outline_setup.PROP_DOUBLE_SIDED] = False
    mat.use_backface_culling = True
    outline_setup.repair_scene_line_materials(bpy.context.scene)
    _assert_generated_material(mat, "既存内部線")


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    obj = _make_source()
    _assert_generated_material(outline_setup.get_line_material(obj, "inner"), "内部線")
    _assert_generated_material(outline_setup.get_line_material(obj, "intersection"), "交差線")
    _assert_restore_repairs_old_inner_material(obj)
    print("[PASS] generated inner/intersection line materials keep line colors", flush=True)
    bpy.ops.wm.quit_blender()


if __name__ == "__main__":
    main()
