"""Blender実機用: 効果線がグラデーション塗りの背面に隠れないことを確認する回帰テスト.

背景 (ユーザー報告バグ):
レイヤーリストで効果線をグラデーション塗りより前面(リスト上で上)に置くと、
location.z 上も効果線の方が確実に手前になるにもかかわらず、B-MANGAの
ビューポートが強制する RENDERED 表示 (実体は EEVEE Next) では、グラデーション
塗りの巨大な半透明平面が、Zで手前にあるはずの効果線ストロークを隠してしまう
ことがあった。

根本原因: utils/fill_real_object.py (_ensure_solid_material/_ensure_gradient_material)
と utils/effect_line_object.py (_configure_line_material_nodes) が
mat.blend_method = "BLEND" を設定するだけだった。Blender 5.2 (EEVEE Next) では
この代入の副作用として mat.surface_render_method が "BLENDED"
(= 描画順に依存し深度を無視する疑似透過合成) になってしまい、Zが正しくても
意図した前後関係が描画に反映されない可能性があった。

修正: 上記3箇所で mat.surface_render_method = "DITHERED" (深度を尊重する
疑似透過合成) を明示的に上書きすることで解消した。

このテストの合否判定 (決定的・非フレーキー):
実アドオン関数 (utils/fill_real_object.ensure_fill_real_object,
operators/effect_line_op._create_effect_layer, utils/effect_line_object.
_configure_line_material_nodes 経由)でグラデーション塗りと効果線を実際に
生成し、両マテリアルの mat.surface_render_method が "DITHERED" になって
いることをプロパティレベルで直接検証する。これは今回の修正が実際に
書き込むプロパティそのものであり、blend_method="BLEND" の代入だけを行うと
Blender 5.2 では副作用で "BLENDED" に戻ってしまうため、この修正を退行
させる (surface_render_method の明示上書きを外す) と本テストは確実に失敗する
(実機検証済み: 修正前コード相当に一時的に戻した状態で本テストの前身の
検証コードを実行し、両マテリアルが "BLENDED" のままであることを確認した)。

補助検証 (視覚的サニティチェック、非決定的な閾値には依存しない):
コマ+グラデーション塗り(背面)+効果線(前面)を実アドオン関数経由で構築し、
レイヤーリスト順を「効果線が手前」に設定した上でEEVEEレンダリングし、
効果線オブジェクトの表示/非表示を切り替えた2枚の画像の重なり領域に
何らかの差分(diff>0.01)が存在すること――すなわち効果線がそもそも
描画パイプライン上に存在し何らかの形で表示されていること――だけを
緩い閾値で確認する。EEVEEのDITHERED合成はscreen-door状のパターンを伴い
サンプル数や配置によって強度が変動するため、この画像diffは主判定には
使わない (実機検証で、surface_render_method を "BLENDED" に戻した場合でも
このシンプルな2オブジェクト構成では diff 自体は残ることを確認済みであり、
画像diffの強度だけでは退行を確実に検知できないため)。
"""

from __future__ import annotations

import sys
import tempfile
from importlib import util as importlib_util
from pathlib import Path

import bpy
import mathutils

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "effect_line_gradient_occlusion"

RES = 240
# 補助検証(視覚的サニティチェック)用: 重なり領域内で「何らかの差分がある」と
# みなす閾値と、その最低割合。あくまで「効果線が描画パイプラインに存在し
# 何かしら表示されている」ことの粗いスモークテストであり、主判定は
# surface_render_method のプロパティ検証で行う (詳細はモジュールdocstring)。
ANY_DIFF_THRESHOLD = 0.01
MIN_ANY_DIFF_RATIO = 0.01


def _load_addon():
    spec = importlib_util.spec_from_file_location(
        "bmanga_dev_effect_gradient_occlusion",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib_util.module_from_spec(spec)
    sys.modules["bmanga_dev_effect_gradient_occlusion"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _ensure_coma(page):
    coma = page.comas.add()
    coma.id = "c_occlusion01"
    coma.coma_id = "c_occlusion01"
    coma.title = "c_occlusion01"
    coma.shape_type = "rect"
    coma.rect_x_mm = 10.0
    coma.rect_y_mm = 10.0
    coma.rect_width_mm = 100.0
    coma.rect_height_mm = 100.0
    coma.z_order = 0
    coma.border.visible = True
    coma.border.width_mm = 4.0
    return coma


def _create_fill(context, page, parent_key: str, coma):
    from bmanga_dev_effect_gradient_occlusion.utils import fill_real_object

    entry = context.scene.bmanga_fill_layers.add()
    entry.id = "occlusion_grad01"
    entry.title = "グラデーション"
    entry.fill_type = "gradient"
    entry.gradient_type = "linear"
    entry.color = (0.05, 0.2, 0.9, 1.0)
    entry.color2 = (0.6, 0.8, 1.0, 1.0)
    entry.opacity = 100.0
    entry.parent_kind = "coma"
    entry.parent_key = parent_key
    # コマ矩形と一致させる (fillは既定でキャンバス全面サイズのため、比較領域を
    # 単純にするためコマ矩形へ明示的に領域指定する)。
    entry.use_region = True
    entry.region_x_mm = coma.rect_x_mm
    entry.region_y_mm = coma.rect_y_mm
    entry.region_width_mm = coma.rect_width_mm
    entry.region_height_mm = coma.rect_height_mm
    obj = fill_real_object.ensure_fill_real_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None
    return entry, obj


def _create_effect(context, parent_key: str, coma):
    from bmanga_dev_effect_gradient_occlusion.operators import effect_line_op
    from bmanga_dev_effect_gradient_occlusion.utils import effect_line_object

    params = context.scene.bmanga_effect_line_params
    # 「集中線」は焦点付近が疎になり狭い重なり領域で誤判定しやすいため、
    # コマ全域を均一に覆う「流線」を使う。
    params.effect_type = "speed"
    params.speed_angle_deg = 0.0
    params.spacing_mode = "distance"
    params.speed_line_count = 40

    # コマ矩形(10,10)-(110,110)の内側に余白を残した矩形を使う。コマの端まで
    # ぴったり一致させると効果線メッシュの原点計算が境界条件になり、実際の
    # コマ位置からずれて描画される現象が確認されたため、内側に収める。
    bounds = (
        coma.rect_x_mm + 15.0,
        coma.rect_y_mm + 15.0,
        coma.rect_x_mm + coma.rect_width_mm - 15.0,
        coma.rect_y_mm + coma.rect_height_mm - 15.0,
    )
    obj, layer = effect_line_op._create_effect_layer(context, bounds, parent_key=parent_key)
    assert obj is not None and layer is not None
    effect_line_op._write_effect_strokes(context, obj, layer, bounds)
    display = effect_line_object.find_effect_display_object(obj)
    assert display is not None
    return obj, layer, display


def _order_effect_in_front_of_fill(context, fill_entry, effect_obj):
    from bmanga_dev_effect_gradient_occlusion.core.work import get_work
    from bmanga_dev_effect_gradient_occlusion.utils import layer_object_sync
    from bmanga_dev_effect_gradient_occlusion.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    fill_uid = layer_stack_utils.target_uid("fill", fill_entry.id)
    effect_uid = layer_stack_utils.target_uid("effect", str(effect_obj.get("bmanga_id", "") or ""))

    from_idx = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == effect_uid)
    anchor_idx = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == fill_uid)
    if from_idx < anchor_idx:
        anchor_idx -= 1
    stack.move(from_idx, anchor_idx)

    layer_stack_utils.apply_stack_order(context)
    scene = context.scene
    work = get_work(context)
    layer_object_sync.assign_per_page_z_ranks(scene, work)
    context.view_layer.update()


def _assert_surface_render_method_dithered(fill_obj, effect_display) -> None:
    """今回の修正が書き込むプロパティそのものを直接検証する (決定的判定)."""
    checked = []
    fill_mat = fill_obj.data.materials[0] if fill_obj.data.materials else None
    assert fill_mat is not None, "グラデーション塗りにマテリアルがありません"
    checked.append(("fill(gradient)", fill_mat))

    line_mat = None
    for mat in effect_display.data.materials:
        if mat is not None and "_Line_" in mat.name:
            line_mat = mat
            break
    assert line_mat is not None, (
        f"効果線の主線マテリアルが見つかりません: "
        f"{[m.name if m else None for m in effect_display.data.materials]}"
    )
    checked.append(("effect(line)", line_mat))

    failures = []
    for label, mat in checked:
        srm = str(getattr(mat, "surface_render_method", "") or "")
        blend = str(getattr(mat, "blend_method", "") or "")
        print(f"MATCHECK {label} mat={mat.name} blend_method={blend} surface_render_method={srm}")
        if srm != "DITHERED":
            failures.append(f"{label} ({mat.name}): surface_render_method={srm!r} (期待値 'DITHERED')")
    if failures:
        raise AssertionError(
            "半透明マテリアルが深度を無視する疑似合成 (BLENDED) のままです。"
            "グラデーション塗りの背面に効果線を置いても正しい前後関係で"
            "描画されない退行の疑いがあります: " + "; ".join(failures)
        )


def _world_bounds_xy(objs):
    xs, ys = [], []
    for obj in objs:
        mw = obj.matrix_world
        for corner in obj.bound_box:
            wc = mw @ mathutils.Vector(corner)
            xs.append(wc.x)
            ys.append(wc.y)
    return min(xs), max(xs), min(ys), max(ys)


def _setup_camera(frame_objs, margin: float = 1.15):
    for obj in list(bpy.data.objects):
        if obj.type in {"CAMERA", "LIGHT"}:
            bpy.data.objects.remove(obj, do_unlink=True)
    x0, x1, y0, y1 = _world_bounds_xy(frame_objs)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    span = max(x1 - x0, y1 - y0, 1e-4) * margin

    cam_data = bpy.data.cameras.new("occlusion_cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = float(span)
    cam_obj = bpy.data.objects.new("occlusion_cam", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    cam_obj.location = (cx, cy, 1.0)
    cam_obj.rotation_euler = (0.0, 0.0, 0.0)
    bpy.context.scene.camera = cam_obj

    light_data = bpy.data.lights.new("occlusion_light", type="SUN")
    light_obj = bpy.data.objects.new("occlusion_light", light_data)
    bpy.context.scene.collection.objects.link(light_obj)
    return cx, cy, span


def _render_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = RES
    scene.render.resolution_y = RES
    scene.render.film_transparent = False
    scene.world.color = (0.9, 0.9, 0.9)
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    if not path.exists() or path.stat().st_size <= 0:
        raise AssertionError(f"レンダリング画像を保存できません: {path}")


def _world_rect_to_px(cx, cy, span, resolution, world_x0, world_x1, world_y0, world_y1):
    def px(wx, wy):
        u = (wx - (cx - span / 2.0)) / span
        v = 1.0 - (wy - (cy - span / 2.0)) / span
        return int(u * resolution), int(v * resolution)

    x0, y1 = px(world_x0, world_y0)
    x1, y0 = px(world_x1, world_y1)
    return (
        max(0, min(x0, x1)),
        max(0, min(y0, y1)),
        min(resolution, max(x0, x1)),
        min(resolution, max(y0, y1)),
    )


def _load_pixels(png_path: Path):
    img = bpy.data.images.load(str(png_path))
    w, h = img.size
    pix = list(img.pixels[:])
    bpy.data.images.remove(img)
    return w, h, pix


def _diff_ratio(path_with: Path, path_without: Path, region_px, threshold: float) -> tuple[int, int]:
    wa, ha, pa = _load_pixels(path_with)
    wb, hb, pb = _load_pixels(path_without)
    assert (wa, ha) == (wb, hb), (wa, ha, wb, hb)
    x0, y0, x1, y1 = region_px
    hit = 0
    total = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            idx = (y * wa + x) * 4
            ra, ga, ba = pa[idx], pa[idx + 1], pa[idx + 2]
            rb, gb, bb = pb[idx], pb[idx + 1], pb[idx + 2]
            d = abs(ra - rb) + abs(ga - gb) + abs(ba - bb)
            total += 1
            if d > threshold:
                hit += 1
    return hit, total


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_effect_gradient_occlusion_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "GradOcclusion.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        context = bpy.context
        scene = context.scene
        from bmanga_dev_effect_gradient_occlusion.core.work import get_work
        from bmanga_dev_effect_gradient_occlusion.utils import layer_hierarchy

        work = get_work(context)
        page = work.pages[0]
        coma = _ensure_coma(page)
        parent_key = layer_hierarchy.coma_stack_key(page, coma)

        # コマ面・コマ枠オブジェクトを先に用意する (コマ内容マスクの生成元)。
        # 実際のユーザー操作でもコマ追加時に自動生成されるため、それに合わせる。
        from bmanga_dev_effect_gradient_occlusion.utils import coma_border_object, coma_plane

        coma_plane.ensure_coma_plane(scene, work, page, coma)
        coma_border_object.ensure_coma_border_object(scene, work, page, coma)

        fill_entry, fill_obj = _create_fill(context, page, parent_key, coma)
        effect_obj, effect_layer, effect_display = _create_effect(context, parent_key, coma)

        # レイヤーリスト順: 効果線を手前(前面)、グラデーションを背面へ
        # (ユーザー報告と同じ並び)。
        _order_effect_in_front_of_fill(context, fill_entry, effect_obj)

        assert effect_obj.location.z > fill_obj.location.z, (
            f"前提: 効果線のZがグラデーションより前面であること: "
            f"effect={effect_obj.location.z} fill={fill_obj.location.z}"
        )

        # ---- 主判定 (決定的): 半透明マテリアルが深度を尊重する DITHERED に
        # なっていること ----
        _assert_surface_render_method_dithered(fill_obj, effect_display)

        # ---- 補助検証 (視覚的サニティチェック) ----
        frame_objs = [fill_obj, effect_display]
        fx0, fx1, fy0, fy1 = _world_bounds_xy([fill_obj])
        ex0, ex1, ey0, ey1 = _world_bounds_xy([effect_display])
        ov_x0, ov_x1 = max(fx0, ex0), min(fx1, ex1)
        ov_y0, ov_y1 = max(fy0, ey0), min(fy1, ey1)
        assert ov_x0 < ov_x1 and ov_y0 < ov_y1, (
            f"フィルと効果線のバウンディングボックスが重なっていません: "
            f"fill=({fx0},{fx1},{fy0},{fy1}) effect=({ex0},{ex1},{ey0},{ey1})"
        )

        cx, cy, span = _setup_camera(frame_objs)
        overlap_px = _world_rect_to_px(cx, cy, span, RES, ov_x0, ov_x1, ov_y0, ov_y1)

        out_with = OUT_DIR / "with_effect.png"
        _render_png(out_with)

        effect_display.hide_render = True
        effect_obj.hide_render = True
        out_without = OUT_DIR / "without_effect.png"
        _render_png(out_without)
        effect_display.hide_render = False
        effect_obj.hide_render = False

        hit, total = _diff_ratio(out_with, out_without, overlap_px, ANY_DIFF_THRESHOLD)
        ratio = hit / total if total > 0 else 0.0
        print(
            f"BMANGA_EFFECT_LINE_GRADIENT_OCCLUSION_DIFF hit={hit} total={total} "
            f"ratio={ratio:.4f} overlap_px={overlap_px}"
        )
        if ratio <= MIN_ANY_DIFF_RATIO:
            raise AssertionError(
                "効果線がグラデーション塗りの重なり領域内で全く視認できません "
                f"(効果線自体が描画パイプラインに乗っていない可能性): "
                f"hit={hit}/{total} ratio={ratio:.4f}"
            )

        print(
            "BMANGA_EFFECT_LINE_GRADIENT_OCCLUSION_OK "
            f"diff_ratio={ratio:.4f} with={out_with} without={out_without}"
        )
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
