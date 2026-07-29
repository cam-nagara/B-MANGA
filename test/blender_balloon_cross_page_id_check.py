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

        # ページ詳細は page.blend にだけ存在する。各ページを実際に開いて同じ
        # 位置・サイズのフキダシを作り、作品共通の採番と保存後の実体を検証する。
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        if "FINISHED" not in result:
            raise AssertionError(f"p1 を開けません: {result}")
        work = get_work(bpy.context)
        page1 = work.pages[0]
        result = bpy.ops.bmanga.balloon_add(
            "EXEC_DEFAULT", shape="rect", x_mm=30.0, y_mm=200.0,
            width_mm=40.0, height_mm=20.0,
        )
        if "FINISHED" not in result:
            raise AssertionError(f"p1 のフキダシを作成できません: {result}")
        id_p1 = str(page1.balloons[-1].id)
        ox1, _ = page_grid.page_total_offset_mm(work, bpy.context.scene, 0)
        wx1 = _balloon_world_x(id_p1)
        tol1 = float(work.paper.canvas_width_mm) * 0.5
        if wx1 is None or abs(wx1 - ox1) > tol1:
            raise AssertionError(
                f"p1 のフキダシ位置が違います: world_x={wx1} offset={ox1}"
            )
        if "FINISHED" not in bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT"):
            raise AssertionError("p1 を保存して作品一覧へ戻れません")

        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=1)
        if "FINISHED" not in result:
            raise AssertionError(f"p2 を開けません: {result}")
        work = get_work(bpy.context)
        page2 = work.pages[1]
        result = bpy.ops.bmanga.balloon_add(
            "EXEC_DEFAULT", shape="rect", x_mm=30.0, y_mm=200.0,
            width_mm=40.0, height_mm=20.0,
        )
        if "FINISHED" not in result:
            raise AssertionError(f"p2 のフキダシを作成できません: {result}")
        id_p2 = str(page2.balloons[-1].id)

        # 1) id がページ横断で一意
        if id_p1 == id_p2:
            raise AssertionError(
                f"別ページのフキダシ id が衝突しています: p1={id_p1} p2={id_p2} "
                "(実体オブジェクトが重なり、2 ページ目で表示されなくなる)"
            )

        # 2) 現在開いている p2 の実体がページファイル内の正しい位置に出る。
        ox2, _ = page_grid.page_total_offset_mm(work, bpy.context.scene, 1)
        wx2 = _balloon_world_x(id_p2)
        tol2 = float(work.paper.canvas_width_mm) * 0.5
        if wx2 is None or abs(wx2 - ox2) > tol2:
            raise AssertionError(
                f"p2 のフキダシ位置が違います: world_x={wx2} offset={ox2}"
            )

        # work を渡さない採番経路 (フキダシテキスト作成 / レイヤースタック作成 /
        # 複製 / 別ページへの移動) でもページ横断一意になることを確認する。
        from bmanga_dev_balloon_cross_page.operators.balloon_op import _allocate_balloon_id
        existing = {id_p1, id_p2}
        existing |= {str(b.id) for p in work.pages for b in p.balloons}
        existing |= {str(b.id) for b in getattr(work, "shared_balloons", [])}
        new_id_no_work = _allocate_balloon_id(page2)  # work 引数なし
        if new_id_no_work in existing:
            raise AssertionError(
                f"work を渡さない採番が既存 id と衝突しました: {new_id_no_work} "
                f"(複製/移動/テキスト作成 経路で重複フキダシが起きる) existing={sorted(existing)}"
            )
        if "FINISHED" not in bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT"):
            raise AssertionError("p2 を保存して作品一覧へ戻れません")

        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        if "FINISHED" not in result:
            raise AssertionError(f"保存後の p1 を開けません: {result}")
        work = get_work(bpy.context)
        page1 = work.pages[0]
        if id_p1 not in {str(balloon.id) for balloon in page1.balloons}:
            raise AssertionError("p1 のフキダシIDがページ再読込後に失われました")
        wx1 = _balloon_world_x(id_p1)
        ox1, _ = page_grid.page_total_offset_mm(work, bpy.context.scene, 0)
        tol1 = float(work.paper.canvas_width_mm) * 0.5
        if wx1 is None or abs(wx1 - ox1) > tol1:
            fill = bpy.data.objects.get(f"balloon_fill_mesh_{id_p1}")
            body = getattr(fill, "parent", None)
            body_parent = getattr(body, "parent", None)
            raise AssertionError(
                "再読込後の p1 フキダシ位置が違います: "
                f"x={page1.balloons[0].x_mm} world_x={wx1} offset={ox1} "
                f"body_local={tuple(body.location) if body else None} "
                f"body_parent={getattr(body_parent, 'name', None)} "
                f"parent_local={tuple(body_parent.location) if body_parent else None}"
            )

        print("BMANGA_BALLOON_CROSS_PAGE_ID_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
