"""フキダシ id がページ横断で一意になり、作成直後に正しいページへ実体が出ることを確認.

ページ単位採番だと別ページのフキダシと id が衝突し、実体オブジェクト名 (id 由来) が重なって
2 ページ目以降のフキダシが 1 ページ目の位置に作られ、当該ページでは表示されなかった
(保存時の採番し直しで初めて直る)。作成時点で一意 id を割り当て、正しいページ位置に
実体が出ることを検証する。
"""

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
        "bmanga_dev_balloon_cross_page",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_balloon_cross_page"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _balloon_world_x(balloon_id: str) -> float | None:
    # 実際に塗りつぶして見えるフキダシ本体 (塗りメッシュ) の world 位置で判定する。
    # 本体カーブは stamp 後に L0000__balloon__<id> へ改名されるが、塗りメッシュは
    # balloon_fill_mesh_<id> のまま。
    obj = bpy.data.objects.get(f"balloon_fill_mesh_{balloon_id}")
    if obj is None:
        obj = bpy.data.objects.get(f"balloon_line_mesh_{balloon_id}")
    if obj is None:
        return None
    return float(obj.matrix_world.translation.x) * 1000.0


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_balloon_cross_page_"))
    mod = None
    try:
        mod = _load_addon()
        if "FINISHED" not in bpy.ops.bmanga.work_new(filepath=str(temp_root / "BalloonCrossPage.bmanga")):
            raise AssertionError("作品作成に失敗しました")

        from bmanga_dev_balloon_cross_page.core.work import get_work
        from bmanga_dev_balloon_cross_page.utils import page_grid

        context = bpy.context
        work = get_work(context)
        while len(work.pages) < 2:
            if "FINISHED" not in bpy.ops.bmanga.page_add():
                raise AssertionError("ページ追加に失敗しました")
        if len(work.pages) < 2:
            raise AssertionError("検証には 2 ページ以上必要です")

        # p1 と p2 に同じ位置・サイズのフキダシを作成
        work.active_page_index = 0
        bpy.ops.bmanga.balloon_add('EXEC_DEFAULT', shape='rect', x_mm=30.0, y_mm=200.0, width_mm=40.0, height_mm=20.0)
        id_p1 = str(work.pages[0].balloons[-1].id)

        work.active_page_index = 1
        bpy.ops.bmanga.balloon_add('EXEC_DEFAULT', shape='rect', x_mm=30.0, y_mm=200.0, width_mm=40.0, height_mm=20.0)
        id_p2 = str(work.pages[1].balloons[-1].id)

        # 1) id がページ横断で一意
        if id_p1 == id_p2:
            raise AssertionError(
                f"別ページのフキダシ id が衝突しています: p1={id_p1} p2={id_p2} "
                "(実体オブジェクトが重なり、2 ページ目で表示されなくなる)"
            )

        # 2) それぞれの実体が「自分のページ」のオフセット付近に出ている
        ox1, _ = page_grid.page_total_offset_mm(work, context.scene, 0)
        ox2, _ = page_grid.page_total_offset_mm(work, context.scene, 1)
        wx1 = _balloon_world_x(id_p1)
        wx2 = _balloon_world_x(id_p2)
        if wx1 is None or wx2 is None:
            raise AssertionError(f"フキダシ実体が見つかりません: p1obj={wx1} p2obj={wx2}")
        # ページ幅の半分(約110mm)以内なら「そのページ上」と判定
        tol = abs(ox2 - ox1) * 0.5
        if abs(wx1 - ox1) > tol:
            raise AssertionError(f"p1 のフキダシがページ1の位置にありません: world_x={wx1:.1f} offset={ox1:.1f}")
        if abs(wx2 - ox2) > tol:
            raise AssertionError(
                f"p2 のフキダシがページ2の位置にありません (1ページ目に作られている): "
                f"world_x={wx2:.1f} page2_offset={ox2:.1f} page1_offset={ox1:.1f}"
            )

        # work を渡さない採番経路 (フキダシテキスト作成 / レイヤースタック作成 /
        # 複製 / 別ページへの移動) でもページ横断一意になることを確認する。
        from bmanga_dev_balloon_cross_page.operators.balloon_op import _allocate_balloon_id
        existing = {str(b.id) for p in work.pages for b in p.balloons}
        existing |= {str(b.id) for b in getattr(work, "shared_balloons", [])}
        new_id_no_work = _allocate_balloon_id(work.pages[1])  # work 引数なし
        if new_id_no_work in existing:
            raise AssertionError(
                f"work を渡さない採番が既存 id と衝突しました: {new_id_no_work} "
                f"(複製/移動/テキスト作成 経路で重複フキダシが起きる) existing={sorted(existing)}"
            )

        print("BMANGA_BALLOON_CROSS_PAGE_ID_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
