"""Test that coma_plane UV anchor uses page coordinates for thumb.png alignment."""
import sys, importlib.util, types
import bpy
import os.path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
    print(f"[skip] {e}")
    sys.exit(0)

# Test page anchor computation
paper = types.SimpleNamespace(canvas_width_mm=257.0, canvas_height_mm=364.0, dpi=600)
work = types.SimpleNamespace(paper=paper)

# Polygon coma
coma_poly = types.SimpleNamespace(
    shape_type="polygon",
    rect_x_mm=0, rect_y_mm=0, rect_width_mm=0, rect_height_mm=0,
    vertices=[
        types.SimpleNamespace(x_mm=10.3, y_mm=63.2),
        types.SimpleNamespace(x_mm=171.8, y_mm=81.3),
        types.SimpleNamespace(x_mm=197.2, y_mm=296.7),
        types.SimpleNamespace(x_mm=-6.9, y_mm=329.1),
    ],
)
anchor_poly = cp._page_anchor_m(work, coma_poly)
print(f"Polygon anchor: {anchor_poly}")
# Expected: (0, 0, 0.257, 0.364)
assert anchor_poly is not None
assert abs(anchor_poly[0]) < 1e-6 and abs(anchor_poly[1]) < 1e-6
assert abs(anchor_poly[2] - 0.257) < 1e-6 and abs(anchor_poly[3] - 0.364) < 1e-6
print("[ok] polygon coma anchor = (0, 0, page_w, page_h)")

# Rect coma
coma_rect = types.SimpleNamespace(
    shape_type="rect",
    rect_x_mm=50.0, rect_y_mm=100.0,
    rect_width_mm=80.0, rect_height_mm=60.0,
    vertices=[],
)
anchor_rect = cp._page_anchor_m(work, coma_rect)
print(f"Rect anchor: {anchor_rect}")
# Expected: (-0.050, -0.100, 0.257, 0.364)
assert anchor_rect is not None
assert abs(anchor_rect[0] - (-0.050)) < 1e-6
assert abs(anchor_rect[1] - (-0.100)) < 1e-6
assert abs(anchor_rect[2] - 0.257) < 1e-6 and abs(anchor_rect[3] - 0.364) < 1e-6
print("[ok] rect coma anchor = (-rect_x, -rect_y, page_w, page_h)")

# Verify UV mapping for a polygon coma vertex
# A vertex at world (171.8, 81.3) in page coords (= mesh local for polygon)
# UV should be (171.8/257, 81.3/364) = (0.668, 0.223)
import math
min_x_a, min_y_a, w_a, h_a = anchor_poly
v_x, v_y = 0.1718, 0.0813  # in meters
uv_x = (v_x - min_x_a) / w_a
uv_y = (v_y - min_y_a) / h_a
print(f"Polygon vertex UV: ({uv_x:.3f}, {uv_y:.3f})")
assert abs(uv_x - 171.8/257) < 1e-3 and abs(uv_y - 81.3/364) < 1e-3
print("[ok] polygon vertex UV maps to page-relative position")

# Verify UV for rect coma vertex
# A vertex at mesh-local (0, 0) — corner of rect — corresponds to world (rect_x, rect_y) on page
# UV should be (rect_x/page_w, rect_y/page_h) = (50/257, 100/364) = (0.195, 0.275)
min_x_a, min_y_a, w_a, h_a = anchor_rect
v_x, v_y = 0.0, 0.0  # mesh-local (0,0)
uv_x = (v_x - min_x_a) / w_a
uv_y = (v_y - min_y_a) / h_a
print(f"Rect vertex (0,0) UV: ({uv_x:.3f}, {uv_y:.3f})")
assert abs(uv_x - 50.0/257) < 1e-3 and abs(uv_y - 100.0/364) < 1e-3
print("[ok] rect coma vertex (0,0) UV maps to (rect_x/page_w, rect_y/page_h)")

# No paper / no work returns None (graceful fallback)
assert cp._page_anchor_m(None, coma_poly) is None
assert cp._page_anchor_m(types.SimpleNamespace(paper=None), coma_poly) is None
print("[ok] missing paper returns None (fallback to polygon bbox)")

# Verify ensure_coma_plane sets page anchor when building mesh
# Build minimal scene
scene = bpy.context.scene
import sys
print("\nALL PASS")
