"""Blender 実機用: 多重線「長さ変化 (主線寄り/遠い側)」がキャッシュ署名に反映され、
スライダー変更で多重線メッシュが再生成されることの回帰テスト.

背景 (2026-07-23):
  フキダシ多重線メッシュ (`ensure_balloon_multi_line_mesh`) は `_band_geometry_signature`
  が一致するとキャッシュを返して再構築をスキップする。この署名を作る
  `_geometry_key_for_entry` が長さ変化について旧・単一プロパティ
  `thorn_multi_line_length_scale_percent` のみを見ており、UI が実際に書き込む
  `..._near_percent` / `..._far_percent` を含んでいなかった。
  その結果「長さ変化 (主線寄り/遠い側)」スライダーを動かしても署名が変わらず、
  キャッシュがヒットしてメッシュが再生成されない = 見た目が変わらない、という回帰。

このテストは:
  1. `_geometry_key_for_entry` が far / near の変更で変化すること (根本契約)
  2. `ensure_balloon_curve_object` 経由で多重線メッシュの頂点が far / near の変更で
     実際に変わること (エンドツーエンド)
  を検証する。修正前のコードでは 1・2 とも変化せず失敗する。

走らせ方 (ユーザーの作業中 Blender を汚さないよう factory-startup 推奨):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe" --background ^
    --factory-startup --python-exit-code 1 ^
    --python "d:/Develop/Blender/B-MANGA/test/blender_balloon_multiline_length_signature_check.py"
"""

from __future__ import annotations

import hashlib
import importlib.util
import struct
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
_ADDON_MOD = "bmanga_dev_ml_length_sig"
SENTINEL = "BMANGA_MULTILINE_LENGTH_SIGNATURE_CHECK_OK"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        _ADDON_MOD,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_ADDON_MOD] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _reset_work():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_ml_len_sig_"))
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "MlLenSig.bmanga"))  # type: ignore[attr-defined]
    assert "FINISHED" in result, result
    return bpy.context


def _page_key():
    from bmanga_dev_ml_length_sig.utils.layer_hierarchy import page_stack_key
    return page_stack_key(bpy.context.scene.bmanga_work.pages[0])


def _add_thorn_multi_balloon(page, parent_key):
    entry = page.balloons.add()
    entry.id = "m_len_sig_thorn"
    entry.title = "len_sig_thorn"
    entry.shape = "thorn"
    entry.x_mm = 20.0
    entry.y_mm = 80.0
    entry.width_mm = 60.0
    entry.height_mm = 60.0
    entry.parent_kind = "page"
    entry.parent_key = parent_key
    entry.line_style = "double"
    entry.line_width_mm = 1.2
    entry.line_color = (0.0, 0.0, 0.0, 1.0)
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.fill_opacity = 100.0
    entry.opacity = 100.0
    entry.multi_line_count = 3
    entry.multi_line_direction = "outside"
    entry.multi_line_width_mm = 0.6
    entry.multi_line_spacing_mm = 0.9
    entry.multi_line_width_scale_percent = 100.0
    entry.multi_line_spacing_scale_percent = 100.0
    entry.thorn_multi_line_valley_width_pct = 100.0
    entry.thorn_multi_line_peak_width_pct = 100.0
    entry.thorn_multi_line_length_scale_near_percent = 100.0
    entry.thorn_multi_line_length_scale_far_percent = 100.0
    entry.thorn_multi_line_cross_enabled = False
    return entry


def _multi_line_mesh_hash(balloon_id: str) -> str | None:
    """再生成後の多重線メッシュの頂点座標ハッシュ (無ければ None)."""
    from bmanga_dev_ml_length_sig.utils import balloon_line_mesh as blm
    name = blm._multi_line_mesh_object_name(balloon_id)
    obj = bpy.data.objects.get(name)
    if obj is None or obj.data is None:
        return None
    h = hashlib.md5()
    verts = list(obj.data.vertices)
    h.update(struct.pack("<i", len(verts)))
    for v in verts:
        h.update(struct.pack("<3f", *v.co))
    return h.hexdigest()


def _rebuild(context, entry, page) -> None:
    from bmanga_dev_ml_length_sig.utils import balloon_curve_object as bco
    bco.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)


def _export_outline_and_groups(entry):
    """書き出し経路の多重線バンド群 (と本体アウトライン) を mm で返す."""
    from bmanga_dev_ml_length_sig.io import export_balloon as eb
    from bmanga_dev_ml_length_sig.utils import balloon_shapes
    from bmanga_dev_ml_length_sig.utils.geom import Rect
    rect = Rect(0.0, 0.0, float(entry.width_mm), float(entry.height_mm))
    outline = balloon_shapes.outline_for_entry(entry, rect)
    groups = eb._multi_ring_band_polygons(outline, entry, sharp=eb._body_sharp_corners(entry))
    return outline, groups


def _export_multi_hash(entry):
    """書き出し多重線ポリゴンの頂点ハッシュと総頂点数を返す."""
    _outline, groups = _export_outline_and_groups(entry)
    h = hashlib.md5()
    total = 0
    for group in groups:
        for outer, holes in group:
            for (x, y) in outer:
                h.update(struct.pack("<2f", float(x), float(y)))
                total += 1
            for hole in holes:
                for (x, y) in hole:
                    h.update(struct.pack("<2f", float(x), float(y)))
                    total += 1
    return h.hexdigest(), total


def _rasterize_export(entry, path) -> bool:
    """書き出し経路が生成する本体輪郭+多重線リングを PNG に描いて目視確認用に保存."""
    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001
        return False
    outline, groups = _export_outline_and_groups(entry)
    all_pts = list(outline)
    for group in groups:
        for outer, _holes in group:
            all_pts.extend(outer)
    if len(all_pts) < 3:
        return False
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    pad, scale = 4.0, 8.0
    width_px = max(1, int((maxx - minx + 2 * pad) * scale))
    height_px = max(1, int((maxy - miny + 2 * pad) * scale))

    def to_px(p):
        return ((p[0] - minx + pad) * scale, (maxy - p[1] + pad) * scale)

    base = Image.new("RGBA", (width_px, height_px), (255, 255, 255, 255))
    ImageDraw.Draw(base).polygon([to_px(p) for p in outline], outline=(170, 170, 170, 255))
    # 実パイプライン同様、各リンググループを個別の透明レイヤに描いてアルファ合成する
    # (順に塗り→穴を消すと外側リングの穴が内側リングを消してしまうため)。
    for group in groups:
        layer = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 0))
        dl = ImageDraw.Draw(layer)
        for outer, _holes in group:
            if len(outer) >= 3:
                dl.polygon([to_px(p) for p in outer], fill=(210, 30, 30, 255))
        for _outer, holes in group:
            for hole in holes:
                if len(hole) >= 3:
                    dl.polygon([to_px(p) for p in hole], fill=(0, 0, 0, 0))
        base = Image.alpha_composite(base, layer)
    base.convert("RGB").save(str(path))
    return True


def main() -> None:
    context = _reset_work()
    from bmanga_dev_ml_length_sig.utils import balloon_curve_object as bco

    page = context.scene.bmanga_work.pages[0]
    pk = _page_key()
    entry = _add_thorn_multi_balloon(page, pk)

    # --- 1. 根本契約: _geometry_key_for_entry が far / near で変わる -----------
    key_base = bco._geometry_key_for_entry(entry)

    entry.thorn_multi_line_length_scale_far_percent = 30.0
    key_far = bco._geometry_key_for_entry(entry)
    assert key_far != key_base, (
        "遠い側(far)の長さ変化が geometry 署名に反映されていない "
        "(_geometry_key_for_entry がキャッシュ無効化を起こさない = 回帰)"
    )

    entry.thorn_multi_line_length_scale_near_percent = 40.0
    key_near = bco._geometry_key_for_entry(entry)
    assert key_near != key_far, (
        "主線寄り(near)の長さ変化が geometry 署名に反映されていない"
    )

    # --- 2. エンドツーエンド: 多重線メッシュの頂点が far / near で実際に変わる ---
    # near/far を既定(100/100)に戻して初期メッシュを生成
    entry.thorn_multi_line_length_scale_near_percent = 100.0
    entry.thorn_multi_line_length_scale_far_percent = 100.0
    _rebuild(context, entry, page)
    mesh_base = _multi_line_mesh_hash(entry.id)
    assert mesh_base is not None, "多重線メッシュが生成されていない (前提崩れ)"

    # 遠い側を 30% に: 外側リングが切り詰められ頂点が変わるはず
    entry.thorn_multi_line_length_scale_far_percent = 30.0
    _rebuild(context, entry, page)
    mesh_far = _multi_line_mesh_hash(entry.id)
    assert mesh_far is not None, "多重線メッシュが消えた (far 変更後)"
    assert mesh_far != mesh_base, (
        "遠い側(far)を変えても多重線メッシュが再生成されない "
        "(キャッシュがヒットして頂点が変わらない = ユーザー報告のバグ)"
    )

    # 主線寄りを 40% に: 内側リングも切り詰められ頂点が更に変わるはず
    entry.thorn_multi_line_length_scale_near_percent = 40.0
    _rebuild(context, entry, page)
    mesh_near = _multi_line_mesh_hash(entry.id)
    assert mesh_near is not None, "多重線メッシュが消えた (near 変更後)"
    assert mesh_near != mesh_far, (
        "主線寄り(near)を変えても多重線メッシュが再生成されない"
    )

    # --- 3. 書き出し(PNG/PSD)経路: 長さ変化が export の多重線ポリゴンに反映される ---
    entry.thorn_multi_line_length_scale_near_percent = 100.0
    entry.thorn_multi_line_length_scale_far_percent = 100.0
    exp_base, exp_base_total = _export_multi_hash(entry)
    assert exp_base_total > 0, "書き出し多重線ポリゴンが空 (far=100 前提崩れ)"

    entry.thorn_multi_line_length_scale_far_percent = 30.0
    exp_far, exp_far_total = _export_multi_hash(entry)
    assert exp_far_total > 0, "書き出し多重線ポリゴンが空 (far=30 で生成器が何も返していない)"
    assert exp_far != exp_base, (
        "遠い側(far)を変えても書き出しの多重線が変わらない "
        "(export に長さ変化が反映されていない = 画面と出力が食い違う)"
    )

    entry.thorn_multi_line_length_scale_near_percent = 40.0
    exp_near, exp_near_total = _export_multi_hash(entry)
    assert exp_near_total > 0, "書き出し多重線ポリゴンが空 (near=40)"
    assert exp_near != exp_far, "主線寄り(near)を変えても書き出しの多重線が変わらない"

    # 曲線形状 (cloud) スモーク: 長さ変化ONで例外なく多重線ポリゴンが出る
    # (ml_straight を形状種別で判定する修正の回帰防止)。
    entry.shape = "cloud"
    entry.thorn_multi_line_length_scale_near_percent = 100.0
    entry.thorn_multi_line_length_scale_far_percent = 40.0
    exp_cloud, exp_cloud_total = _export_multi_hash(entry)
    assert exp_cloud_total > 0, "曲線形状(cloud)の書き出し多重線が空 (長さ変化ONで生成器が失敗?)"
    entry.shape = "thorn"
    entry.thorn_multi_line_length_scale_near_percent = 40.0
    entry.thorn_multi_line_length_scale_far_percent = 30.0

    # 目視確認用ラスタライズ (far=30/near=100 と far=100 を _verify へ)
    out_dir = ROOT / "_verify" / "2026-07-23_multiline_length_export"
    out_dir.mkdir(parents=True, exist_ok=True)
    entry.thorn_multi_line_length_scale_near_percent = 100.0
    entry.thorn_multi_line_length_scale_far_percent = 100.0
    ras_a = _rasterize_export(entry, out_dir / "thorn_export_far100.png")
    entry.thorn_multi_line_length_scale_far_percent = 30.0
    ras_b = _rasterize_export(entry, out_dir / "thorn_export_far30_near100.png")

    print(f"  key_base={key_base[:12]} key_far={key_far[:12]} key_near={key_near[:12]}")
    print(f"  mesh_base={mesh_base[:12]} mesh_far={mesh_far[:12]} mesh_near={mesh_near[:12]}")
    print(f"  export_base={exp_base[:12]} export_far={exp_far[:12]} export_near={exp_near[:12]}")
    print(f"  export_totals base={exp_base_total} far={exp_far_total} near={exp_near_total} raster={ras_a and ras_b}")
    print(SENTINEL)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
