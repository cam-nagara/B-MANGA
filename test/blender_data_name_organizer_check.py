"""Blender実機用: 実データ名整理がページ/コマの現在順へ揃うことを確認。"""

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
        "bname_dev_data_name_organizer",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_data_name_organizer"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _reset_comas(page) -> None:
    while len(page.comas):
        page.comas.remove(len(page.comas) - 1)


def _add_rect_coma(page, coma_id: str, title: str, x_mm: float) -> None:
    entry = page.comas.add()
    entry.id = coma_id
    entry.coma_id = coma_id
    entry.title = title
    entry.shape_type = "rect"
    entry.rect_x_mm = x_mm
    entry.rect_y_mm = 20.0
    entry.rect_width_mm = 60.0
    entry.rect_height_mm = 80.0


def _write_marker(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _prepare_swapped_work(work_dir: Path):
    from bname_dev_data_name_organizer.utils import layer_object_sync
    from bname_dev_data_name_organizer.utils import paths

    result = bpy.ops.bname.work_new(filepath=str(work_dir))
    if "FINISHED" not in result:
        raise AssertionError(f"作品作成に失敗しました: {result}")
    work = bpy.context.scene.bname_work
    work.paper.read_direction = "left"
    if "FINISHED" not in bpy.ops.bname.page_add("EXEC_DEFAULT"):
        raise AssertionError("ページ追加に失敗しました")

    first_old = work.pages[0]
    second_old = work.pages[1]
    _write_marker(Path(work.work_dir) / first_old.id / "origin_page_one.txt", "page-one")
    _write_marker(Path(work.work_dir) / second_old.id / "origin_page_two.txt", "page-two")

    _reset_comas(second_old)
    _add_rect_coma(second_old, "c02", "old-c02", 120.0)
    _add_rect_coma(second_old, "c01", "old-c01", 10.0)
    second_old.active_coma_index = 0
    for coma_id in ("c01", "c02"):
        _write_marker(
            Path(work.work_dir) / second_old.id / coma_id / f"origin_{coma_id}.txt",
            coma_id,
        )
        _write_marker(
            Path(work.work_dir) / second_old.id / coma_id / f"{coma_id}.json",
            "{}",
        )
        _write_marker(
            Path(work.work_dir) / second_old.id / coma_id / f"{coma_id}_preview.png",
            "preview",
        )

    balloon = second_old.balloons.add()
    balloon.id = "balloon_data_name"
    balloon.parent_kind = "coma"
    balloon.parent_key = f"{second_old.id}:c02"

    work.pages.move(1, 0)
    work.active_page_index = 0
    layer_object_sync.mirror_work_to_outliner(bpy.context.scene, work)
    assert paths.page_dir(Path(work.work_dir), "p0001").is_dir()
    assert paths.page_dir(Path(work.work_dir), "p0002").is_dir()
    return work


def _assert_organized(work) -> None:
    from bname_dev_data_name_organizer.utils import paths

    root = Path(work.work_dir)
    page0 = work.pages[0]
    page1 = work.pages[1]
    if (page0.id, page1.id) != ("p0001", "p0002"):
        raise AssertionError(f"ページIDが整理されていません: {[page.id for page in work.pages]}")
    if not (root / "p0001" / "origin_page_two.txt").is_file():
        raise AssertionError("1ページ目の実データが p0001 に移動していません")
    if not (root / "p0002" / "origin_page_one.txt").is_file():
        raise AssertionError("2ページ目の実データが p0002 に移動していません")

    order = [(coma.title, coma.coma_id) for coma in page0.comas]
    if order != [("old-c02", "c01"), ("old-c01", "c02")]:
        raise AssertionError(f"コマIDが読む順に整理されていません: {order}")
    if page0.active_coma_index != 0:
        raise AssertionError(f"選択コマが追随していません: {page0.active_coma_index}")
    if not (root / "p0001" / "c01" / "origin_c02.txt").is_file():
        raise AssertionError("先に読むコマの実データが c01 に移動していません")
    if not (root / "p0001" / "c02" / "origin_c01.txt").is_file():
        raise AssertionError("後に読むコマの実データが c02 に移動していません")
    if not paths.coma_json_path(root, "p0001", "c01").is_file():
        raise AssertionError("整理後のコマ設定ファイルがありません")
    if not paths.coma_preview_path(root, "p0001", "c01").is_file():
        raise AssertionError("整理後のコマプレビュー名が揃っていません")
    if page0.balloons[0].parent_key != "p0001:c01":
        raise AssertionError(f"コマ内要素の参照先が更新されていません: {page0.balloons[0].parent_key}")


def _assert_spread_numbering(root: Path) -> None:
    from bname_dev_data_name_organizer.utils import paths

    result = bpy.ops.bname.work_new(filepath=str(root / "SpreadDataName.bname"))
    if "FINISHED" not in result:
        raise AssertionError(f"見開き確認用の作品作成に失敗しました: {result}")
    if "FINISHED" not in bpy.ops.bname.page_add("EXEC_DEFAULT"):
        raise AssertionError("見開き確認用の2ページ目追加に失敗しました")
    if "FINISHED" not in bpy.ops.bname.page_add("EXEC_DEFAULT"):
        raise AssertionError("見開き確認用の3ページ目追加に失敗しました")

    work = bpy.context.scene.bname_work
    work_dir = Path(work.work_dir)
    spread = work.pages[1]
    spread.id = "p0005-0006"
    spread.dir_rel = "p0005-0006/"
    spread.spread = True
    spread.original_pages.clear()
    ref = spread.original_pages.add()
    ref.page_id = "p0005"
    ref = spread.original_pages.add()
    ref.page_id = "p0006"
    _write_marker(work_dir / "p0005-0006" / "origin_spread.txt", "spread")
    _write_marker(work_dir / "p0003" / "origin_page_three.txt", "page-three")

    result = bpy.ops.bname.organize_data_names("EXEC_DEFAULT")
    if "FINISHED" not in result:
        raise AssertionError(f"見開き込みの実データ名整理に失敗しました: {result}")
    ids = [str(page.id) for page in work.pages]
    if ids != ["p0001", "p0002-0003", "p0004"]:
        raise AssertionError(f"見開き込みのページIDが整理されていません: {ids}")
    refs = [str(ref.page_id) for ref in work.pages[1].original_pages]
    if refs != ["p0002", "p0003"]:
        raise AssertionError(f"見開き元ページ情報が更新されていません: {refs}")
    if not (work_dir / "p0002-0003" / "origin_spread.txt").is_file():
        raise AssertionError("見開きページの実データが p0002-0003 に移動していません")
    if not paths.page_dir(work_dir, "p0004").joinpath("origin_page_three.txt").is_file():
        raise AssertionError("見開き後の通常ページ実データが p0004 に移動していません")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_data_name_organizer_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        _assert_spread_numbering(temp_root)
        work = _prepare_swapped_work(temp_root / "DataNameOrganizer.bname")

        result = bpy.ops.bname.organize_data_names("EXEC_DEFAULT")
        if "FINISHED" not in result:
            raise AssertionError(f"実データ名整理に失敗しました: {result}")
        _assert_organized(work)

        result = bpy.ops.bname.enter_coma_mode("EXEC_DEFAULT")
        if "FINISHED" not in result:
            raise AssertionError(f"コマ用blendファイルを開けません: {result}")
        expected = Path(temp_root / "DataNameOrganizer.bname" / "p0001" / "c01" / "c01.blend").resolve()
        actual = Path(bpy.data.filepath).resolve()
        if actual != expected:
            raise AssertionError(f"開いたコマ用blendファイルが違います: expected={expected}, actual={actual}")

        print("BNAME_DATA_NAME_ORGANIZER_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
