"""Blender 5.1実機: 購入素材メッシュ最適化の構造・原子性・保存確認."""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "test"))

from b_manga_line_test_utils import temporary_line_preset_store  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _select_only(*objects) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _material(name: str, color) -> bpy.types.Material:
    material = bpy.data.materials.new(name)
    material.diffuse_color = color
    return material


def _open_cylinder(name: str, segments: int = 8) -> bpy.types.Object:
    vertices = []
    for z_value in (-0.5, 0.5):
        for index in range(segments):
            angle = math.tau * index / segments
            vertices.append((math.cos(angle), math.sin(angle), z_value))
    faces = []
    for index in range(segments):
        nxt = (index + 1) % segments
        faces.append((index, nxt, nxt + segments, index + segments))

    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(_material(f"{name}_A", (0.8, 0.2, 0.1, 1.0)))
    mesh.materials.append(_material(f"{name}_B", (0.1, 0.3, 0.8, 1.0)))
    for polygon in mesh.polygons:
        polygon.use_smooth = True
        polygon.material_index = polygon.index % 2

    uv = mesh.uv_layers.new(name="UVMap")
    color = mesh.color_attributes.new(
        name="SurfaceTint",
        type="BYTE_COLOR",
        domain="CORNER",
    )
    normals = []
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            ring_index = vertex_index % segments
            u_value = ring_index / segments
            if polygon.index == segments - 1 and ring_index == 0:
                u_value = 1.0
            z_value = mesh.vertices[vertex_index].co.z + 0.5
            uv.data[loop_index].uv = (u_value, z_value)
            color.data[loop_index].color = (
                0.2 + polygon.material_index * 0.5,
                0.4,
                0.8 - polygon.material_index * 0.5,
                1.0,
            )
            normal = mesh.vertices[vertex_index].co.copy()
            normal.z = 0.0
            normal.normalize()
            normals.append(tuple(normal))
    mesh.normals_split_custom_set(normals)
    crease = mesh.attributes.new("crease_vert", "FLOAT", "POINT")
    for index, item in enumerate(crease.data):
        item.value = index / max(1, len(crease.data) - 1)
    mesh.edges[0].use_seam = True
    if hasattr(mesh.edges[0], "use_freestyle_mark"):
        mesh.edges[0].use_freestyle_mark = True
    if hasattr(mesh.polygons[0], "use_freestyle_mark"):
        mesh.polygons[0].use_freestyle_mark = True
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _simple_object(name: str, faces) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
        [],
        faces,
    )
    mesh.materials.append(_material(f"{name}_Material", (0.6, 0.6, 0.6, 1.0)))
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        co = mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (co.x, co.y)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _edge_counts(mesh) -> tuple[int, int]:
    counts = [0] * len(mesh.edges)
    for polygon in mesh.polygons:
        for edge_index in polygon.edge_keys:
            pair = tuple(sorted(edge_index))
            for edge in mesh.edges:
                if tuple(sorted(edge.vertices)) == pair:
                    counts[edge.index] += 1
                    break
    return sum(value == 1 for value in counts), sum(value > 2 for value in counts)


def _uv_corner_set(mesh, layer_name: str) -> set[tuple]:
    layer = mesh.uv_layers[layer_name]
    return {
        (
            int(loop.vertex_index),
            round(float(layer.data[loop.index].uv.x), 7),
            round(float(layer.data[loop.index].uv.y), 7),
        )
        for loop in mesh.loops
    }


def _color_corner_set(mesh, attribute_name: str) -> set[tuple]:
    attribute = mesh.color_attributes[attribute_name]
    return {
        (
            int(loop.vertex_index),
            *(round(float(value), 5) for value in attribute.data[loop.index].color),
        )
        for loop in mesh.loops
    }


def _assert_normals_preserved(source, candidate) -> None:
    target: dict[int, list[Vector]] = {}
    for loop in candidate.loops:
        target.setdefault(int(loop.vertex_index), []).append(
            candidate.corner_normals[loop.index].vector.copy()
        )
    for loop in source.loops:
        normal = source.corner_normals[loop.index].vector
        assert any(normal.dot(value) > 0.9999 for value in target[int(loop.vertex_index)])


def _run_expected_cancel() -> None:
    try:
        result = bpy.ops.bmanga_line.optimize_purchased_mesh("EXEC_DEFAULT")
    except RuntimeError:
        return
    assert result == {"CANCELLED"}


def _assert_direct_candidate(mod) -> None:
    from b_manga_line import mesh_optimizer_geometry as geometry

    obj = _open_cylinder("DirectCandidate")
    source = obj.data
    original_positions = [vertex.co.copy() for vertex in source.vertices]
    original_materials = [polygon.material_index for polygon in source.polygons]
    original_texspace = (tuple(source.texspace_location), tuple(source.texspace_size))
    vertical_edges = {
        tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1]))))
        for edge in source.edges
        if abs(int(edge.vertices[0]) - int(edge.vertices[1])) == 8
    }
    result = geometry.build_candidate(
        source,
        "DirectCandidate_Result",
        geometry.OptimizeOptions(passes=1),
    )
    candidate = result.mesh
    assert candidate is not None
    assert result.stats.split_edges > 0
    assert len(candidate.vertices) > len(source.vertices)
    assert len(candidate.polygons) > len(source.polygons)
    for index, position in enumerate(original_positions):
        assert (candidate.vertices[index].co - position).length < 1.0e-8
    new_radii = [
        math.hypot(vertex.co.x, vertex.co.y)
        for vertex in candidate.vertices[len(source.vertices) :]
        if abs(vertex.co.z) > 0.49
    ]
    assert new_radii and max(new_radii) > math.cos(math.pi / 8.0) + 0.01
    assert [layer.name for layer in candidate.uv_layers] == ["UVMap"]
    assert [item.name for item in candidate.color_attributes] == ["SurfaceTint"]
    assert candidate.has_custom_normals
    assert _uv_corner_set(source, "UVMap") <= _uv_corner_set(candidate, "UVMap")
    assert _color_corner_set(source, "SurfaceTint") <= _color_corner_set(
        candidate,
        "SurfaceTint",
    )
    _assert_normals_preserved(source, candidate)
    assert len(candidate.materials) == len(source.materials)
    candidate_materials = {polygon.material_index for polygon in candidate.polygons}
    assert candidate_materials == set(original_materials)
    assert any(edge.use_seam for edge in candidate.edges)
    if hasattr(candidate.edges[0], "use_freestyle_mark"):
        assert any(edge.use_freestyle_mark for edge in candidate.edges)
    if hasattr(candidate.polygons[0], "use_freestyle_mark"):
        assert any(polygon.use_freestyle_mark for polygon in candidate.polygons)
    candidate_pairs = {
        tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1]))))
        for edge in candidate.edges
    }
    assert vertical_edges <= candidate_pairs
    assert candidate.attributes.get("crease_vert") is not None
    source_crease = source.attributes["crease_vert"]
    candidate_crease = candidate.attributes["crease_vert"]
    for index in range(len(source.vertices)):
        assert abs(candidate_crease.data[index].value - source_crease.data[index].value) < 1.0e-7
    assert candidate.use_auto_texspace is False
    assert tuple(candidate.texspace_location) == original_texspace[0]
    assert tuple(candidate.texspace_size) == original_texspace[1]
    open_edges, non_manifold = _edge_counts(candidate)
    assert open_edges > 0 and non_manifold == 0
    close_result = geometry.build_candidate(
        source,
        "DirectCandidate_Close",
        geometry.OptimizeOptions(passes=2),
    )
    assert close_result.mesh is not None
    assert len(close_result.mesh.vertices) > len(candidate.vertices)
    assert len(close_result.mesh.polygons) > len(candidate.polygons)
    assert _uv_corner_set(source, "UVMap") <= _uv_corner_set(close_result.mesh, "UVMap")
    _assert_normals_preserved(source, close_result.mesh)
    bpy.data.meshes.remove(close_result.mesh)
    bpy.data.meshes.remove(candidate)
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_duplicate_cleanup() -> None:
    from b_manga_line import mesh_optimizer_geometry as geometry

    obj = _simple_object("Duplicate", ((0, 1, 2), (0, 1, 2)))
    result = geometry.build_candidate(
        obj.data,
        "Duplicate_Result",
        geometry.OptimizeOptions(),
    )
    assert result.mesh is not None
    assert result.stats.removed_duplicate_faces == 1
    assert len(result.mesh.polygons) == 1
    bpy.data.meshes.remove(result.mesh)
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_supported_face_types() -> None:
    from b_manga_line import mesh_optimizer_geometry as geometry

    vertices = (
        (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
        (2.0, 0.0, 0.0), (3.0, 0.0, 0.0), (3.0, 1.0, 0.0), (2.0, 1.0, 0.0),
        (4.0, 0.0, 0.0), (5.0, 0.0, 0.0), (5.4, 0.7, 0.0),
        (4.5, 1.3, 0.0), (3.7, 0.7, 0.0),
    )
    mesh = bpy.data.meshes.new("SupportedFaces_Mesh")
    mesh.from_pydata(vertices, [], ((0, 1, 2), (3, 4, 5, 6), (7, 8, 9, 10, 11)))
    obj = bpy.data.objects.new("SupportedFaces", mesh)
    bpy.context.scene.collection.objects.link(obj)
    geometry.validate_source_object(obj)
    result = geometry.build_candidate(
        mesh,
        "SupportedFaces_Result",
        geometry.OptimizeOptions(),
    )
    assert result.mesh is None
    assert result.stats.open_edges == 12
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_degenerate_cleanup() -> None:
    from b_manga_line import mesh_optimizer_geometry as geometry

    obj = _simple_object("Degenerate", ((0, 1, 2), (0, 0, 1)))
    result = geometry.build_candidate(
        obj.data,
        "Degenerate_Result",
        geometry.OptimizeOptions(),
    )
    assert result.mesh is not None
    assert result.stats.removed_degenerate_faces == 1
    assert len(result.mesh.polygons) == 1
    bpy.data.meshes.remove(result.mesh)
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_conflicting_color_rejection() -> None:
    from b_manga_line import mesh_optimizer_geometry as geometry

    obj = _simple_object("ColorConflict", ((0, 1, 2), (0, 1, 2)))
    color = obj.data.color_attributes.new(
        name="CornerColor",
        type="FLOAT_COLOR",
        domain="CORNER",
    )
    for index, item in enumerate(color.data):
        item.color = (1.0, 0.0, 0.0, 1.0) if index < 3 else (0.0, 0.0, 1.0, 1.0)
    try:
        geometry.build_candidate(
            obj.data,
            "ColorConflict_Result",
            geometry.OptimizeOptions(),
        )
    except geometry.UnsafeMeshError as exc:
        assert "UV・素材・法線" in str(exc)
    else:
        raise AssertionError("色属性の異なる重複面が拒否されませんでした")
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_loose_edge_rejection() -> None:
    from b_manga_line import mesh_optimizer_geometry as geometry

    mesh = bpy.data.meshes.new("LooseEdge_Mesh")
    mesh.from_pydata(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (2.0, 0.0, 0.0), (2.0, 1.0, 0.0)),
        ((3, 4),),
        ((0, 1, 2),),
    )
    obj = bpy.data.objects.new("LooseEdge", mesh)
    bpy.context.scene.collection.objects.link(obj)
    try:
        geometry.validate_source_object(obj)
    except geometry.UnsafeMeshError as exc:
        assert "面に属さない辺" in str(exc)
    else:
        raise AssertionError("面に属さない辺が拒否されませんでした")
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_batch_limit_cleanup(mod) -> None:
    obj = _open_cylinder("BatchLimit")
    original = obj.data
    try:
        mod._prepare(
            [obj],
            mod._options(bpy.context.scene),
            max_batch_faces=1,
        )
    except mod.UnsafeMeshError as exc:
        assert "安全上限" in str(exc)
    else:
        raise AssertionError("一括候補面数の上限が機能していません")
    assert obj.data == original
    assert not any("BatchLimit" in mesh.name and "Candidate" in mesh.name for mesh in bpy.data.meshes)
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_commit_rollback(mod) -> None:
    obj = _open_cylinder("CommitRollback")
    original_mesh = obj.data
    obj.bmanga_line_settings.auto_subdivision_for_midpoint = True
    expected = {
        "bml_soup_mesh_line_repaired": True,
        "bml_sheet_mesh": False,
        "bml_sheet_signature": "before",
        "bml_pending_line_create_targets": "inner",
        "bml_reflected_fp_outline": "fingerprint",
    }
    for key, value in expected.items():
        obj[key] = value
    prepared, unchanged = mod._prepare([obj], mod._options(bpy.context.scene))
    assert prepared and unchanged == 0
    original_clear = mod._clear_line_state

    def _fail_after_clear(target):
        original_clear(target)
        raise RuntimeError("forced rollback")

    mod._clear_line_state = _fail_after_clear
    try:
        try:
            mod._commit(prepared, "STANDARD")
        except RuntimeError as exc:
            assert str(exc) == "forced rollback"
        else:
            raise AssertionError("確定中エラーでロールバックされませんでした")
    finally:
        mod._clear_line_state = original_clear
    assert obj.data == original_mesh
    assert obj.bmanga_line_settings.auto_subdivision_for_midpoint
    assert not obj.get(mod.OPTIMIZED_PROP, False)
    for key, value in expected.items():
        assert obj[key] == value
    assert not any("CommitRollback" in mesh.name and "Candidate" in mesh.name for mesh in bpy.data.meshes)
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_atomic_rejection(mod) -> None:
    good = _open_cylinder("AtomicGood")
    bad = _simple_object("AtomicBad", ((0, 1, 2), (2, 1, 0)))
    good_mesh = good.data
    bad_mesh = bad.data
    good_counts = (len(good.data.vertices), len(good.data.polygons))
    bad_counts = (len(bad.data.vertices), len(bad.data.polygons))
    _select_only(good, bad)
    _run_expected_cancel()
    assert good.data == good_mesh and bad.data == bad_mesh
    assert (len(good.data.vertices), len(good.data.polygons)) == good_counts
    assert (len(bad.data.vertices), len(bad.data.polygons)) == bad_counts
    assert "表裏が重なる面" in bpy.context.scene.bmanga_line_mesh_optimize_error
    assert not any("BML_OptimizedCandidate" in mesh.name for mesh in bpy.data.meshes)
    bpy.data.objects.remove(good, do_unlink=True)
    bpy.data.objects.remove(bad, do_unlink=True)


def _assert_non_manifold_rejection() -> None:
    obj = _simple_object(
        "NonManifold",
        ((0, 1, 2), (1, 0, 3), (0, 1, 4)),
    )
    mesh = obj.data
    _select_only(obj)
    _run_expected_cancel()
    assert obj.data == mesh
    assert "3面以上が接続する辺" in bpy.context.scene.bmanga_line_mesh_optimize_error
    bpy.data.objects.remove(obj, do_unlink=True)


def _assert_unchanged_object_not_touched(mod) -> None:
    curved = _open_cylinder("MixedCurved")
    flat = _simple_object("MixedFlat", ((0, 1, 2),))
    curved.bmanga_line_settings.auto_subdivision_for_midpoint = True
    flat.bmanga_line_settings.auto_subdivision_for_midpoint = True
    flat_mesh = flat.data
    _select_only(curved, flat)
    assert bpy.ops.bmanga_line.optimize_purchased_mesh("EXEC_DEFAULT") == {"FINISHED"}
    assert curved.get(mod.OPTIMIZED_PROP) is True
    assert not curved.bmanga_line_settings.auto_subdivision_for_midpoint
    assert flat.data == flat_mesh
    assert not flat.get(mod.OPTIMIZED_PROP, False)
    assert flat.bmanga_line_settings.auto_subdivision_for_midpoint
    bpy.data.objects.remove(curved, do_unlink=True)
    bpy.data.objects.remove(flat, do_unlink=True)


def _assert_operator_and_roundtrip(mod) -> None:
    obj = _open_cylinder("Roundtrip")
    old_mesh = obj.data
    old_name = old_mesh.name
    obj.bmanga_line_settings.auto_subdivision_for_midpoint = True
    _select_only(obj)
    result = bpy.ops.bmanga_line.optimize_purchased_mesh("EXEC_DEFAULT")
    assert result == {"FINISHED"}
    assert obj.data != old_mesh
    assert obj.get(mod.OPTIMIZED_PROP) is True
    assert not obj.bmanga_line_settings.auto_subdivision_for_midpoint
    obj.bmanga_line_settings.auto_subdivision_for_midpoint = True
    assert not obj.bmanga_line_settings.auto_subdivision_for_midpoint
    assert old_name not in bpy.data.meshes
    assert obj.data.name.startswith(f"{old_name}_Optimized")
    assert bpy.context.scene.bmanga_line_mesh_optimize_result.startswith("最適化 1件")
    snapshot = (
        len(obj.data.vertices),
        len(obj.data.polygons),
        tuple(layer.name for layer in obj.data.uv_layers),
        tuple(material.name for material in obj.data.materials),
        bool(obj.data.has_custom_normals),
    )
    with tempfile.TemporaryDirectory(prefix="bml_mesh_optimizer_") as temp_dir:
        path = str(Path(temp_dir) / "roundtrip.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        loaded = bpy.data.objects["Roundtrip"]
        restored = (
            len(loaded.data.vertices),
            len(loaded.data.polygons),
            tuple(layer.name for layer in loaded.data.uv_layers),
            tuple(material.name for material in loaded.data.materials),
            bool(loaded.data.has_custom_normals),
        )
        assert restored == snapshot
        assert loaded.get(mod.OPTIMIZED_PROP) is True


def main() -> None:
    with temporary_line_preset_store():
        import b_manga_line as mod

        mod.register()
        try:
            _clear_scene()
            assert hasattr(bpy.types.Scene, "bmanga_line_mesh_optimize_quality")
            assert getattr(bpy.types, "BMANGA_LINE_PT_mesh_optimizer", None) is not None
            _assert_direct_candidate(mod)
            _assert_duplicate_cleanup()
            _assert_supported_face_types()
            _assert_degenerate_cleanup()
            _assert_conflicting_color_rejection()
            _assert_loose_edge_rejection()
            _assert_batch_limit_cleanup(mod.mesh_optimizer)
            _assert_commit_rollback(mod.mesh_optimizer)
            _assert_atomic_rejection(mod)
            _assert_non_manifold_rejection()
            _assert_unchanged_object_not_touched(mod.mesh_optimizer)
            _assert_operator_and_roundtrip(mod.mesh_optimizer)
        finally:
            mod.unregister()
    print("B-MANGA Liner mesh optimizer check: PASS")


if __name__ == "__main__":
    main()
