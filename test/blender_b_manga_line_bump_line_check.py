"""B-MANGA Line: bump line (normal-map edge extraction, render-time composite).

計画書 docs/bml_bump_normal_line_and_lock_plan_2026-07-09.md Part A / A-4 手順9
の完了条件（A-5）を検証する。外部 .blend には依存せず、ノーマルマップ付きの
平面をテスト内で bpy.data.images.new + foreach_set で生成する。

検証内容（Eevee/Cycles 両方）:
  (i)   バンプ線を有効にしたオブジェクトのノーマルマップの溝位置に、
        レンダリング画像上で線ピクセルが出る
  (ii)  バンプ線を有効にしていないオブジェクトには出ない
        （同一パターンのノーマルマップを持つ対照オブジェクトと比較）
  (iii) 線幅が mm 指定どおり（600dpiフォールバック基準で ±1px程度）
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import aov_compositor, core, update_state  # noqa: E402


TMP_DIR = ROOT / "_verify" / "2026-07-09_bml_bump_line_impl" / "test_render_tmp"
RES = 512
TEX_RES = 256
ORTHO_SCALE = 5.0
PLANE_SIZE = 1.6
PLANE_A_X = -1.0
PLANE_B_X = 1.0
BUMP_COLOR = (1.0, 0.0, 0.0, 1.0)
BUMP_THICKNESS_MM = 0.3
BUMP_THRESHOLD = 0.65
FALLBACK_DPI = 600.0


def log(msg: str) -> None:
    print(f"[BUMP_LINE_TEST] {msg}", flush=True)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for datablocks in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.images,
        bpy.data.node_groups,
    ):
        for item in list(datablocks):
            if item.users == 0:
                datablocks.remove(item)


def _make_single_flank_normal_map(name: str) -> bpy.types.Image:
    """平坦部の中央1箇所だけ法線が変化する段差ノーマルマップを生成する.

    キャリブレーション実測（_verify/2026-07-09_bml_bump_line_calibration/）と
    同型のフィクスチャ。ジオメトリは完全な平面のまま、テクスチャだけが
    ディテールを持つ（＝稜谷線では拾えず、バンプ線でのみ拾える想定のケース）。
    """
    img = bpy.data.images.new(name, width=TEX_RES, height=TEX_RES, alpha=True, float_buffer=True)
    img.colorspace_settings.name = "Non-Color"
    pixels = [0.0] * (TEX_RES * TEX_RES * 4)
    tilt = 0.4
    edge_x = TEX_RES // 2
    for y in range(TEX_RES):
        for x in range(TEX_RES):
            idx = (y * TEX_RES + x) * 4
            r = 0.5 - tilt if x < edge_x else 0.5 + tilt
            pixels[idx + 0] = r
            pixels[idx + 1] = 0.5
            pixels[idx + 2] = 0.85
            pixels[idx + 3] = 1.0
    img.pixels.foreach_set(pixels)
    img.pack()
    return img


def _make_bump_plane(
    name: str,
    location_x: float,
    normal_img: bpy.types.Image,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_plane_add(size=PLANE_SIZE, location=(location_x, 0.0, 0.0))
    obj = bpy.context.active_object
    obj.name = name
    uv_layer = obj.data.uv_layers.active.data
    expected_uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    for loop_idx, uv in zip(range(4), expected_uvs):
        uv_layer[loop_idx].uv = uv

    mat = bpy.data.materials.new(f"{name}_Mat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    bsdf.inputs["Base Color"].default_value = (0.6, 0.6, 0.6, 1.0)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.location = (-300, 0)
    tex.image = normal_img
    tex.interpolation = "Closest"
    nmap = nt.nodes.new("ShaderNodeNormalMap")
    nmap.location = (0, 0)
    nmap.inputs["Strength"].default_value = 1.0
    nt.links.new(tex.outputs["Color"], nmap.inputs["Color"])
    nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    obj.data.materials.append(mat)
    return obj


def _make_camera() -> bpy.types.Object:
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("BML_Bump_Cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ORTHO_SCALE
    cam_obj = bpy.data.objects.new("BML_Bump_Cam", cam_data)
    scene.collection.objects.link(cam_obj)
    cam_obj.location = (0.0, 0.0, 5.0)
    cam_obj.rotation_euler = (0.0, 0.0, 0.0)
    scene.camera = cam_obj
    return cam_obj


def build_scene() -> dict:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    normal_img = _make_single_flank_normal_map("BML_Bump_NormalMap")
    plane_a = _make_bump_plane("BML_Bump_PlaneA", PLANE_A_X, normal_img)
    plane_b = _make_bump_plane("BML_Bump_PlaneB", PLANE_B_X, normal_img)

    cam = _make_camera()

    sun = bpy.data.lights.new("BML_Bump_Sun", type="SUN")
    sun.energy = 2.0
    sun_obj = bpy.data.objects.new("BML_Bump_Sun", sun)
    scene.collection.objects.link(sun_obj)
    sun_obj.rotation_euler = (0.4, 0.2, 0.1)

    world = bpy.data.worlds.new("BML_Bump_World")
    world.use_nodes = True
    scene.world = world

    scene.render.resolution_x = RES
    scene.render.resolution_y = RES
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"

    return {"plane_a": plane_a, "plane_b": plane_b, "cam": cam}


def _set_engine(scene, engine_key: str) -> str:
    if engine_key == "EEVEE":
        for cand in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                scene.render.engine = cand
                return cand
            except TypeError:
                continue
        raise RuntimeError("No EEVEE engine available")
    if engine_key == "CYCLES":
        try:
            scene.render.engine = "CYCLES"
        except TypeError as exc:
            raise RuntimeError("CYCLES not available") from exc
        scene.cycles.samples = 16
        scene.cycles.use_denoising = False
        return "CYCLES"
    raise ValueError(engine_key)


def _world_x_to_pixel(world_x: float, resolution: int) -> int:
    normalized = (world_x + ORTHO_SCALE / 2.0) / ORTHO_SCALE
    return int(round(normalized * resolution))


def _render_to_pixels(scene, stem: str) -> tuple[int, int, list[float]]:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_DIR / f"{stem}.png"
    scene.render.filepath = str(path)
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h = img.size
        buf = list(img.pixels)
    finally:
        bpy.data.images.remove(img)
    return w, h, buf


def _row_redness(buf, w, h, x, y) -> float:
    """指定ピクセルの「赤み」（R - max(G,B)）。バンプ線色(1,0,0,1)前提の検出指標."""
    idx = (y * w + x) * 4
    r, g, b = buf[idx], buf[idx + 1], buf[idx + 2]
    return r - max(g, b)


def _redness_run_width(buf, w, h, y, cx, *, threshold: float = 0.3, search_radius: int = 40) -> int:
    on = [
        _row_redness(buf, w, h, x, y) > threshold
        for x in range(max(0, cx - search_radius), min(w, cx + search_radius))
    ]
    if not any(on):
        return 0
    # 最大連続ランを返す
    best = 0
    cur = 0
    for flag in on:
        cur = cur + 1 if flag else 0
        best = max(best, cur)
    return best


def run_case(engine_key: str) -> None:
    objs = build_scene()
    b_manga_line.register()
    try:
        scene = bpy.context.scene
        engine_used = _set_engine(scene, engine_key)
        log(f"engine={engine_used}: scene built")

        plane_a = objs["plane_a"]
        plane_b = objs["plane_b"]

        bpy.ops.object.select_all(action="DESELECT")
        plane_a.select_set(True)
        bpy.context.view_layer.objects.active = plane_a

        settings_a = plane_a.bmanga_line_settings
        settings_a.bump_line_enabled = True
        settings_a.bump_line_color = BUMP_COLOR
        settings_a.bump_line_thickness = BUMP_THICKNESS_MM
        settings_a.bump_line_threshold = BUMP_THRESHOLD

        # 明示更新方針: チェック/数値変更だけでは更新待ちのはず、更新ボタンで反映
        pending = update_state.pending_visual_targets(plane_a)
        assert "bump" in pending, (engine_used, pending)

        assert bpy.ops.bmanga_line.reflect_target.poll()
        result = bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="bump")
        assert result == {"FINISHED"}, (engine_used, result)
        assert "bump" not in update_state.pending_visual_targets(plane_a)

        # レンダー(1): PlaneAのみバンプ線有効
        w, h, buf_main = _render_to_pixels(scene, f"main_{engine_used}")
        log(f"engine={engine_used}: main render done ({w}x{h})")

        # レンダー(2): ベースライン（バンプ線を無効化して再同期・撤去確認込み）。
        # 撤去後は Cryptomatte/Sobel等の重い処理ノードのみ除去され、
        # レンダーパイプラインを壊さないための最小限のパススルー
        # （RenderLayers→GroupOutputの2ノードのみ）は意図的に残る
        # （aov_compositor.sync_bump_line_render_composite のdocstring参照）。
        settings_a.bump_line_enabled = False
        result = bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target="bump")
        assert result == {"FINISHED"}, result
        tree = scene.compositing_node_group
        leftover = [
            n for n in tree.nodes
            if n.name.startswith(aov_compositor.BUMP_COMPOSITE_NODE_PREFIX)
        ] if tree is not None else []
        assert len(leftover) <= 2, (engine_used, [n.name for n in leftover])
        heavy_leftover = [n for n in leftover if "Cryptomatte" in n.name or "Sobel" in n.name]
        assert not heavy_leftover, (engine_used, heavy_leftover)
        w2, h2, buf_baseline = _render_to_pixels(scene, f"baseline_{engine_used}")
        assert (w2, h2) == (w, h)
        log(f"engine={engine_used}: baseline render done")

        px_a = _world_x_to_pixel(PLANE_A_X, w)
        px_b = _world_x_to_pixel(PLANE_B_X, w)
        cy = h // 2

        # (i) PlaneAの溝位置: メイン画像では赤み(バンプ線色)が検出できる
        redness_a_main = max(
            _row_redness(buf_main, w, h, x, cy)
            for x in range(px_a - 20, px_a + 20)
        )
        assert redness_a_main > 0.3, (engine_used, "PlaneA groove not detected", redness_a_main)

        # ベースライン(バンプ線オフ)では同位置に赤みが出ない
        redness_a_baseline = max(
            _row_redness(buf_baseline, w, h, x, cy)
            for x in range(px_a - 20, px_a + 20)
        )
        assert redness_a_baseline < 0.05, (
            engine_used, "baseline unexpectedly red at PlaneA groove", redness_a_baseline,
        )

        # (ii) PlaneBの溝位置: 同一パターンのノーマルマップを持つが
        # bump_line_enabled=False のため、メイン画像とベースラインで
        # ピクセル値が変化しない（=バンプ線が出ていない）
        for x in range(px_b - 20, px_b + 20):
            idx = (cy * w + x) * 4
            idx_baseline = (cy * w + x) * 4
            main_px = buf_main[idx:idx + 4]
            baseline_px = buf_baseline[idx_baseline:idx_baseline + 4]
            assert all(
                abs(a - b) < 1.0e-4 for a, b in zip(main_px, baseline_px)
            ), (engine_used, "PlaneB (disabled) pixel changed", x, main_px, baseline_px)
        redness_b_main = max(
            _row_redness(buf_main, w, h, x, cy)
            for x in range(px_b - 20, px_b + 20)
        )
        assert redness_b_main < 0.05, (engine_used, "PlaneB leaked bump line", redness_b_main)
        log(f"engine={engine_used}: (i)(ii) detection/exclusion OK")

        # (iii) 線幅が mm 指定どおり(±1px程度)
        target_px = BUMP_THICKNESS_MM * FALLBACK_DPI / 25.4
        measured_px = _redness_run_width(buf_main, w, h, cy, px_a)
        log(
            f"engine={engine_used}: target_px={target_px:.2f} "
            f"measured_px={measured_px} diff={abs(measured_px - target_px):.2f}"
        )
        assert measured_px > 0, (engine_used, "no measurable bump line width")
        assert abs(measured_px - target_px) <= 1.5, (
            engine_used, target_px, measured_px,
        )
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass


def main() -> None:
    for engine_key in ("EEVEE", "CYCLES"):
        run_case(engine_key)
    print("BMANGA_LINE_BUMP_LINE_OK")
    bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
