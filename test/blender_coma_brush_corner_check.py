"""Test that brush + rounded corners doesn't produce spikes."""
import sys, importlib.util, types
import bpy
import math

import os.path; ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pkg = types.ModuleType("bnt"); pkg.__path__ = [ROOT]; sys.modules["bnt"] = pkg
for sub in ("utils","core"):
    m = types.ModuleType(f"bnt.{sub}"); m.__path__ = [f"{ROOT}/{sub}"]; sys.modules[f"bnt.{sub}"] = m

def _load(qn, p):
    s = importlib.util.spec_from_file_location(qn, p)
    m = importlib.util.module_from_spec(s); sys.modules[qn] = m; s.loader.exec_module(m); return m

_load("bnt.utils.log", f"{ROOT}/utils/log.py")
_load("bnt.utils.geom", f"{ROOT}/utils/geom.py")
_load("bnt.utils.border_geom", f"{ROOT}/utils/border_geom.py")
try:
    cp = _load("bnt.utils.coma_plane", f"{ROOT}/utils/coma_plane.py")
except Exception as e:
    print(f"[skip] cannot load: {e}")
    sys.exit(0)

# Test _soft_mask_corner_treatment_would_wrap
# Case 1: rounded + small blur (within radius) → no wrap
coma_small_blur = types.SimpleNamespace(
    border=types.SimpleNamespace(
        corner_type="rounded", corner_radius_mm=10.0,
        width_mm=10.0, blur_amount=1.0,  # distance = 5mm < 10mm
        style="brush", visible=True,
    )
)
distance = cp._soft_mask_distance_mm(coma_small_blur)
wrap = cp._soft_mask_corner_treatment_would_wrap(coma_small_blur)
print(f"Case 1 (small blur within radius): distance={distance}, wrap={wrap}")
assert distance == 5.0 and wrap is False
print("[ok] small blur within radius does NOT trigger wrap")

# Case 2: rounded + big blur > radius → wrap
coma_big_blur = types.SimpleNamespace(
    border=types.SimpleNamespace(
        corner_type="rounded", corner_radius_mm=5.0,
        width_mm=35.0, blur_amount=1.0,  # distance = 17.5mm > 5mm
        style="brush", visible=True,
    )
)
distance = cp._soft_mask_distance_mm(coma_big_blur)
wrap = cp._soft_mask_corner_treatment_would_wrap(coma_big_blur)
print(f"Case 2 (large blur > radius): distance={distance}, wrap={wrap}")
assert distance == 17.5 and wrap is True
print("[ok] large blur > radius triggers wrap detection")

# Case 3: square corners → no wrap
coma_square = types.SimpleNamespace(
    border=types.SimpleNamespace(
        corner_type="square", corner_radius_mm=0.0,
        width_mm=35.0, blur_amount=1.0,
        style="brush", visible=True,
    )
)
wrap = cp._soft_mask_corner_treatment_would_wrap(coma_square)
assert wrap is False
print("[ok] square corner type → no wrap detection")

# Case 4: build a mesh with wrap-triggering settings, verify no self-intersection
coma_test = types.SimpleNamespace(
    shape_type="polygon",
    rect_x_mm=0, rect_y_mm=0, rect_width_mm=0, rect_height_mm=0,
    vertices=[
        types.SimpleNamespace(x_mm=172.6, y_mm=88.8),
        types.SimpleNamespace(x_mm=192.3, y_mm=254.6),
        types.SimpleNamespace(x_mm=10.3, y_mm=283.5),
        types.SimpleNamespace(x_mm=10.3, y_mm=63.2),
    ],
    border=types.SimpleNamespace(
        corner_type="rounded", corner_radius_mm=5.0,
        width_mm=35.0, blur_amount=1.0,
        style="brush", visible=True, blur_dither=False, blur_curve_points=None,
    ),
)

mesh = bpy.data.meshes.new("test_brush_mesh")
cp._build_mesh_geometry(mesh, coma_test, soft_mask=True)
n_verts = len(mesh.vertices)
print(f"Mesh built with {n_verts} verts (large blur, rounded request)")
# With the fix, large blur should use SHARP polygon → 8 verts (4 outer + 4 inner)
assert n_verts == 8, f"Expected 8 verts (sharp polygon), got {n_verts}"
print("[ok] large-blur brush border uses sharp polygon → no spikes")

# Verify outer ring contains sharp polygon verts
outer = [(mesh.vertices[i].co.x * 1000, mesh.vertices[i].co.y * 1000) for i in range(4)]
expected = [(172.6, 88.8), (192.3, 254.6), (10.3, 283.5), (10.3, 63.2)]
for got, exp in zip(outer, expected):
    assert abs(got[0] - exp[0]) < 0.1 and abs(got[1] - exp[1]) < 0.1, f"Vert mismatch: {got} vs {exp}"
print("[ok] outer ring matches sharp polygon vertices")

# Now test small blur case (should keep rounded corners)
mesh2 = bpy.data.meshes.new("test_brush_mesh_small")
coma_test_small = types.SimpleNamespace(
    shape_type="polygon",
    rect_x_mm=0, rect_y_mm=0, rect_width_mm=0, rect_height_mm=0,
    vertices=coma_test.vertices,
    border=types.SimpleNamespace(
        corner_type="rounded", corner_radius_mm=20.0,  # large radius
        width_mm=35.0, blur_amount=0.5,  # distance = 8.75 < 20 → no wrap
        style="brush", visible=True, blur_dither=False, blur_curve_points=None,
    ),
)
cp._build_mesh_geometry(mesh2, coma_test_small, soft_mask=True)
n_verts2 = len(mesh2.vertices)
print(f"Small-blur mesh: {n_verts2} verts (should be > 8 due to rounding)")
assert n_verts2 > 8, f"Expected rounded polygon (>8 verts), got {n_verts2}"
print("[ok] small-blur brush border keeps rounded corners")

print("\nALL PASS")
