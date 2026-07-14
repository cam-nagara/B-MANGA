"""Blender実機: 見開き page.blend の結合・解除と失敗rollbackを検証する。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
TRACKED_IDS = {"spread_gp_page", "spread_gp_coma", "spread_effect_page", "spread_effect_coma"}


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _page_index(work, page_id: str) -> int:
    for index, page in enumerate(work.pages):
        if str(page.id) == page_id:
            return index
    raise AssertionError(f"ページがありません: {page_id}")


def _page(work, page_id: str):
    return work.pages[_page_index(work, page_id)]


def _add_stroke(obj, seed: float) -> None:
    from bmanga_dev.utils import gpencil, layer_object_model

    layer = layer_object_model.content_layer(obj)
    assert layer is not None
    frame = gpencil.ensure_active_frame(layer)
    assert frame is not None and frame.drawing is not None
    assert gpencil.add_stroke_to_drawing(
        frame.drawing,
        [(seed * 0.001, 0.01, 0.0), ((seed + 10.0) * 0.001, 0.02, 0.0)],
    )


def _create_layers(scene, page_id: str, seed: float) -> None:
    from bmanga_dev.utils import effect_line_object, gp_object_layer

    specs = (
        ("gp", "spread_gp_page", "page", page_id),
        ("gp", "spread_gp_coma", "coma", f"{page_id}:c01"),
        ("effect", "spread_effect_page", "page", page_id),
        ("effect", "spread_effect_coma", "coma", f"{page_id}:c01"),
    )
    for index, (kind, stable_id, parent_kind, parent_key) in enumerate(specs):
        if kind == "gp":
            obj = gp_object_layer.create_layer_gp_object(
                scene=scene,
                bmanga_id=stable_id,
                title=stable_id,
                z_index=300 + index * 10,
                parent_kind=parent_kind,
                parent_key=parent_key,
            )
        else:
            obj = effect_line_object.create_effect_line_object(
                scene=scene,
                bmanga_id=stable_id,
                title=stable_id,
                z_index=300 + index * 10,
                parent_kind=parent_kind,
                parent_key=parent_key,
            )
        assert obj is not None
        _add_stroke(obj, seed + index)


def _build_page(page_id: str, x_mm: float) -> None:
    from bmanga_dev.core.work import get_work
    from bmanga_dev.io import spread_page_content

    work = get_work(bpy.context)
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=_page_index(work, page_id))
    assert result == {"FINISHED"}, result
    work = get_work(bpy.context)
    page = _page(work, page_id)
    page.title = f"Source {page_id}"
    page.offset_x_mm = x_mm / 10.0
    page.offset_y_mm = x_mm / 20.0
    page.comas.clear()
    coma = page.comas.add()
    coma.id = "c01"
    coma.coma_id = "c01"
    coma.rect_x_mm = x_mm
    coma.rect_y_mm = 10.0
    coma.rect_width_mm = 40.0
    coma.rect_height_mm = 60.0
    for stable_id in sorted(TRACKED_IDS):
        ref = coma.layer_refs.add()
        ref.layer_id = stable_id
    page.balloons.clear()
    balloon = page.balloons.add()
    balloon.id = "balloon_0001"
    balloon.text_id = "text_0001"
    balloon.x_mm = x_mm
    balloon.y_mm = 30.0
    balloon.width_mm = 20.0
    balloon.height_mm = 14.0
    page.texts.clear()
    text = page.texts.add()
    text.id = "text_0001"
    text.parent_balloon_id = "balloon_0001"
    text.body = f"page {page_id}"
    text.x_mm = x_mm + 3.0
    text.y_mm = 32.0
    _create_layers(bpy.context.scene, page_id, x_mm)
    links = {
        "gp:spread_gp_page": "group-page",
        "gp:spread_gp_coma": "group-coma",
        "effect:spread_effect_page": "group-page-effect",
        "effect:spread_effect_coma": "group-coma-effect",
        f"balloon:{page_id}:balloon_0001": "group-balloon",
        f"text:{page_id}:text_0001": "group-balloon",
    }
    bpy.context.scene[spread_page_content.LINK_PROP] = json.dumps(links)
    result = bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
    assert "FINISHED" in result, result


def _protected_hashes(work_dir: Path) -> dict[str, str]:
    result = {}
    for path in sorted(work_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(work_dir)
        if relative.parts[0] in {"p0001", "p0002"} or relative.name in {"work.json", "pages.json"}:
            result[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _tracked_objects():
    return [
        obj for obj in bpy.context.scene.objects
        if str(obj.get("bmanga_id", "") or "") in TRACKED_IDS
        or str(obj.get("bmanga_id", "") or "").startswith(("gp_", "effect_"))
        and str(obj.get("bmanga_title", "") or "").startswith("spread_")
    ]


def _assert_no_source_content_dirs(work_dir: Path, stage: str) -> None:
    invalid = {}
    for page_id in ("p0001", "p0002"):
        page_dir = work_dir / page_id
        if not page_dir.exists():
            continue
        items = list(page_dir.rglob("*"))
        unsafe = [
            str(path.relative_to(page_dir))
            for path in items
            if not (
                path.parent == page_dir
                and path.name == "page_preview.png"
                and path.is_file()
                and not path.is_symlink()
            )
        ]
        if not items or unsafe:
            invalid[page_id] = unsafe or ["<empty>"]
    assert not invalid, f"{stage}: 元ページ内容が再生成されました: {invalid}"


def _verify_merged(spread_id: str) -> None:
    from bmanga_dev.core.work import get_work
    from bmanga_dev.io import spread_page_content

    work = get_work(bpy.context)
    page = _page(work, spread_id)
    assert page.title == ""
    assert len(page.comas) == 2
    assert {str(item.coma_id) for item in page.comas} == {"c01", "c02"}
    assert len({str(item.id) for item in page.balloons}) == 2
    assert len({str(item.id) for item in page.texts}) == 2
    objects = _tracked_objects()
    assert len(objects) == 8, [(obj.name, obj.get("bmanga_id")) for obj in objects]
    keys = {(str(obj.get("bmanga_kind", "")), str(obj.get("bmanga_id", ""))) for obj in objects}
    assert len(keys) == 8
    assert {str(obj.get(spread_page_content.SOURCE_PAGE_PROP, "")) for obj in objects} == {
        "p0001", "p0002"
    }
    for obj in objects:
        assert str(obj.get("bmanga_parent_key", "") or "").startswith(spread_id)
    links = json.loads(str(bpy.context.scene.get(spread_page_content.LINK_PROP, "{}")))
    assert len(links) == 12
    assert len(set(links.values())) == 10


def _verify_split_page(page_id: str, expected_x: float) -> None:
    from bmanga_dev.core.work import get_work

    work = get_work(bpy.context)
    page = _page(work, page_id)
    assert page.title == f"Source {page_id}"
    assert abs(float(page.offset_x_mm) - expected_x / 10.0) < 1.0e-4
    assert abs(float(page.offset_y_mm) - expected_x / 20.0) < 1.0e-4
    assert len(page.comas) == 1 and page.comas[0].coma_id == "c01"
    assert len(page.balloons) == 1 and page.balloons[0].id == "balloon_0001"
    actual_balloon_x = float(page.balloons[0].x_mm)
    assert abs(actual_balloon_x - expected_x) < 1.0e-4, (
        page_id,
        actual_balloon_x,
        expected_x,
    )
    assert len(page.texts) == 1 and page.texts[0].id == "text_0001"
    objects = _tracked_objects()
    assert len(objects) == 4, [(obj.name, obj.get("bmanga_id")) for obj in objects]
    assert {str(obj.get("bmanga_id", "")) for obj in objects} == TRACKED_IDS
    for obj in objects:
        parent = str(obj.get("bmanga_parent_key", "") or "")
        assert parent in {page_id, f"{page_id}:c01"}
    links = json.loads(str(bpy.context.scene.get("bmanga_layer_link_groups", "{}")))
    assert links == {
        "gp:spread_gp_page": "group-page",
        "gp:spread_gp_coma": "group-coma",
        "effect:spread_effect_page": "group-page-effect",
        "effect:spread_effect_coma": "group-coma-effect",
        f"balloon:{page_id}:balloon_0001": "group-balloon",
        f"text:{page_id}:text_0001": "group-balloon",
    }


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_spread_roundtrip_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "SpreadRoundtrip.bmanga"))
        assert result == {"FINISHED"}, result
        from bmanga_dev.core.work import get_work

        work = get_work(bpy.context)
        work.work_info.page_number_end = 2
        bpy.context.view_layer.update()
        assert [str(page.id) for page in work.pages[:2]] == ["p0001", "p0002"]
        _build_page("p0001", 12.0)
        _build_page("p0002", 24.0)
        work = get_work(bpy.context)
        work_dir = Path(work.work_dir)

        # 確定途中の強制失敗で、ディレクトリ・JSON・メモリを完全復元する。
        before = _protected_hashes(work_dir)
        try:
            result = bpy.ops.bmanga.pages_merge_spread(
                "EXEC_DEFAULT", left_index=0, fail_phase="after_directory_install"
            )
        except RuntimeError as exc:
            assert "強制失敗: after_directory_install" in str(exc), exc
            result = {"CANCELLED"}
        assert result == {"CANCELLED"}, result
        after = _protected_hashes(work_dir)
        assert after == before, {
            key: (before.get(key), after.get(key))
            for key in sorted(set(before) | set(after))
            if before.get(key) != after.get(key)
        }
        assert [str(page.id) for page in get_work(bpy.context).pages[:2]] == ["p0001", "p0002"]
        assert not (work_dir / "p0001-0002").exists()

        result = bpy.ops.bmanga.pages_merge_spread("EXEC_DEFAULT", left_index=0)
        assert result == {"FINISHED"}, result
        spread_id = "p0001-0002"
        assert (work_dir / spread_id / "page.blend").is_file()
        _assert_no_source_content_dirs(work_dir, "結合直後")
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert result == {"FINISHED"}, result
        _assert_no_source_content_dirs(work_dir, "見開きを開いた直後")
        _verify_merged(spread_id)
        assert "FINISHED" in bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
        _assert_no_source_content_dirs(work_dir, "見開きを閉じた直後")

        # 保存後の再読込でも両ページ由来の実体が残る。
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert result == {"FINISHED"}, result
        _assert_no_source_content_dirs(work_dir, "見開きを再度開いた直後")
        _verify_merged(spread_id)
        assert "FINISHED" in bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
        _assert_no_source_content_dirs(work_dir, "見開きを再度閉じた直後")

        result = bpy.ops.bmanga.pages_split_spread("EXEC_DEFAULT", spread_index=0)
        assert result == {"FINISHED"}, result
        split_json_hashes = {}
        for page_id, x_mm in (("p0001", 12.0), ("p0002", 24.0)):
            page_json_path = work_dir / page_id / "page.json"
            split_json_hashes[page_id] = hashlib.sha256(page_json_path.read_bytes()).hexdigest()
            page_json = json.loads(page_json_path.read_text(encoding="utf-8"))
            assert abs(float(page_json["balloons"][0]["xMm"]) - x_mm) < 1.0e-4, (
                page_id,
                page_json,
            )
        for page_id, x_mm in (("p0001", 12.0), ("p0002", 24.0)):
            work = get_work(bpy.context)
            result = bpy.ops.bmanga.open_page_file(
                "EXEC_DEFAULT", index=_page_index(work, page_id)
            )
            assert result == {"FINISHED"}, result
            _verify_split_page(page_id, x_mm)
            assert "FINISHED" in bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
            for pending_id in (("p0002",) if page_id == "p0001" else ()):
                pending_path = work_dir / pending_id / "page.json"
                assert hashlib.sha256(pending_path.read_bytes()).hexdigest() == split_json_hashes[pending_id]
        print("BMANGA_SPREAD_CONTENT_ROUNDTRIP_OK")
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
