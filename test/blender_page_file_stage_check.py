"""ページ一覧とページ用blendファイルが分離されることを確認."""

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
        "bmanga_dev_page_file_stage",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_page_file_stage"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _mainfile() -> Path:
    return Path(bpy.data.filepath).resolve()


def _managed_kind_count(kind: str) -> int:
    return sum(
        1
        for obj in bpy.data.objects
        if str(obj.get("bmanga_kind", "") or "") == kind
        and bool(obj.get("bmanga_managed", False))
    )


def _page_preview_objects() -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.data.objects
        if str(obj.get("bmanga_kind", "") or "") == "page_preview"
    ]


def _visible_page_preview_objects() -> list[bpy.types.Object]:
    return [obj for obj in _page_preview_objects() if not bool(getattr(obj, "hide_viewport", False))]


def _page_preview_object_ids() -> set[str]:
    return {
        str(obj.get("bmanga_page_preview_page_id", "") or obj.get("bmanga_id", "") or "")
        for obj in _page_preview_objects()
    }


def _page_preview_object_for(page_id: str):
    page_id = str(page_id or "")
    for obj in _page_preview_objects():
        if str(obj.get("bmanga_page_preview_page_id", "") or obj.get("bmanga_id", "") or "") == page_id:
            return obj
    return None


def _assert_preview_cache_hidden() -> None:
    visible = _visible_page_preview_objects()
    assert not visible, f"ページプレビュー平面は表示に使わず非表示キャッシュにします: {[obj.name for obj in visible]}"


_WORK_FILE_FORBIDDEN_OBJECT_PROPS = {
    "bmanga_coma_plane_kind",
    "bmanga_coma_mask_kind",
    "bmanga_coma_border_kind",
    "bmanga_coma_white_margin_kind",
    "bmanga_coma_plane_owner_id",
    "bmanga_coma_mask_owner_id",
    "bmanga_coma_border_owner_id",
    "bmanga_coma_white_margin_owner_id",
    "bmanga_paper_bg_kind",
    "bmanga_paper_guide_kind",
    "bmanga_work_info_text_kind",
}

_COMA_RUNTIME_OBJECT_PROPS = {
    "bmanga_coma_plane_kind",
    "bmanga_coma_mask_kind",
    "bmanga_coma_border_kind",
    "bmanga_coma_white_margin_kind",
    "bmanga_coma_plane_owner_id",
    "bmanga_coma_mask_owner_id",
    "bmanga_coma_border_owner_id",
    "bmanga_coma_white_margin_owner_id",
}

_WORK_FILE_FORBIDDEN_OBJECT_NAMES = {
    "bmanga_master_sketch",
    "BManga_EffectLines",
}

_PAGE_CONTENT_KINDS = {
    "balloon",
    "text",
    "image",
    "raster",
    "gp",
    "effect",
    "effect_display",
    "effect_frame_source",
    "effect_shape_source",
    "effect_density_source",
}


def _owner_page_id(obj) -> str:
    parent_key = str(obj.get("bmanga_parent_key", "") or "")
    if ":" in parent_key:
        return parent_key.split(":", 1)[0]
    if parent_key.startswith("p") and len(parent_key) >= 5:
        return parent_key
    for prop in ("bmanga_paper_bg_page_id", "bmanga_paper_guide_page_id"):
        page_id = str(obj.get(prop, "") or "")
        if page_id:
            return page_id
    for prop in (
        "bmanga_coma_plane_owner_id",
        "bmanga_coma_mask_owner_id",
        "bmanga_coma_border_owner_id",
        "bmanga_coma_white_margin_owner_id",
        "bmanga_work_info_text_owner_id",
    ):
        owner = str(obj.get(prop, "") or "")
        if ":" in owner:
            return owner.split(":", 1)[0]
        if owner:
            return owner
    return ""


def _forbidden_work_file_objects() -> list[str]:
    out = []
    for obj in bpy.data.objects:
        if str(obj.get("bmanga_kind", "") or "") == "page_preview":
            continue
        if obj.name in _WORK_FILE_FORBIDDEN_OBJECT_NAMES:
            out.append(obj.name)
            continue
        if str(obj.get("bmanga_kind", "") or "") in _PAGE_CONTENT_KINDS:
            out.append(obj.name)
            continue
        if any(str(obj.get(prop, "") or "") for prop in _WORK_FILE_FORBIDDEN_OBJECT_PROPS):
            out.append(obj.name)
    return sorted(out)


def _coma_runtime_owner_pages() -> set[str]:
    pages = set()
    for obj in bpy.data.objects:
        if not any(str(obj.get(prop, "") or "") for prop in _COMA_RUNTIME_OBJECT_PROPS):
            continue
        owner_page = _owner_page_id(obj)
        if owner_page:
            pages.add(owner_page)
    return pages


def _forbidden_work_file_collections() -> list[str]:
    out = []
    for coll in bpy.data.collections:
        kind = str(coll.get("bmanga_kind", "") or "")
        if kind == "page_preview":
            continue
        coll_id = str(coll.get("bmanga_id", "") or "")
        parent_key = str(coll.get("bmanga_parent_key", "") or "")
        if kind in {"page", "coma", "folder"}:
            out.append(coll.name)
        elif coll.name.startswith("p") and len(coll.name) >= 5:
            out.append(coll.name)
        elif coll_id.startswith("p"):
            out.append(coll.name)
        elif parent_key.startswith("p"):
            out.append(coll.name)
    return sorted(out)


def _assert_work_file_preview_only() -> None:
    forbidden_objects = _forbidden_work_file_objects()
    forbidden_collections = _forbidden_work_file_collections()
    assert not forbidden_objects, f"作品ファイルにページ実体が残っています: {forbidden_objects}"
    assert not forbidden_collections, f"作品ファイルにページ階層が残っています: {forbidden_collections}"
    assert not _coma_runtime_owner_pages(), "作品ファイルにコマ枠実体が残っています"


def _assert_page_file_current_page_runtime_only(page_id: str) -> None:
    offenders = []
    for obj in bpy.data.objects:
        if str(obj.get("bmanga_kind", "") or "") == "page_preview":
            continue
        kind = str(obj.get("bmanga_kind", "") or "")
        has_runtime_prop = any(str(obj.get(prop, "") or "") for prop in _WORK_FILE_FORBIDDEN_OBJECT_PROPS)
        if kind not in _PAGE_CONTENT_KINDS and not has_runtime_prop:
            continue
        owner_page = _owner_page_id(obj)
        if owner_page and owner_page != page_id:
            offenders.append((obj.name, owner_page))
    assert not offenders, f"ページファイルに他ページの実体が残っています: {offenders}"
    runtime_pages = _coma_runtime_owner_pages()
    assert runtime_pages == {page_id}, f"ページファイルのコマ枠実体が現在ページ以外を含んでいます: {runtime_pages}"


def _assert_current_page_runtime_aligned(page_index: int) -> None:
    from bmanga_dev_page_file_stage.utils import page_grid

    scene = bpy.context.scene
    work = scene.bmanga_work
    page = work.pages[page_index]
    page_id = str(getattr(page, "id", "") or "")
    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, page_index)

    def assert_xy(obj, expected_x_mm: float, expected_y_mm: float) -> None:
        actual_x = float(obj.location.x) * 1000.0
        actual_y = float(obj.location.y) * 1000.0
        if abs(actual_x - expected_x_mm) > 0.01 or abs(actual_y - expected_y_mm) > 0.01:
            raise AssertionError(
                f"{obj.name} の位置がページ一覧上の位置とずれています: "
                f"expected=({expected_x_mm:.3f}, {expected_y_mm:.3f}), "
                f"actual=({actual_x:.3f}, {actual_y:.3f})"
            )

    page_objects = [
        obj
        for obj in bpy.data.objects
        if str(obj.get("bmanga_paper_bg_page_id", "") or "") == page_id
        or str(obj.get("bmanga_paper_guide_page_id", "") or "") == page_id
    ]
    assert page_objects, f"{page_id} の用紙実体がありません"
    for obj in page_objects:
        assert_xy(obj, ox_mm, oy_mm)

    for coma in getattr(page, "comas", []) or []:
        owner = f"{page_id}:{getattr(coma, 'id', '')}"
        local_x = 0.0
        local_y = 0.0
        if str(getattr(coma, "shape_type", "rect") or "rect") == "rect":
            local_x = float(getattr(coma, "rect_x_mm", 0.0) or 0.0)
            local_y = float(getattr(coma, "rect_y_mm", 0.0) or 0.0)
        coma_objects = [
            obj
            for obj in bpy.data.objects
            if str(obj.get("bmanga_coma_plane_owner_id", "") or "") == owner
            or str(obj.get("bmanga_coma_mask_owner_id", "") or "") == owner
            or str(obj.get("bmanga_coma_border_owner_id", "") or "") == owner
            or str(obj.get("bmanga_coma_white_margin_owner_id", "") or "") == owner
        ]
        assert coma_objects, f"{owner} のコマ実体がありません"
        for obj in coma_objects:
            assert_xy(obj, ox_mm + local_x, oy_mm + local_y)


def _managed_object(kind: str, bmanga_id: str):
    for obj in bpy.data.objects:
        if (
            str(obj.get("bmanga_kind", "") or "") == kind
            and str(obj.get("bmanga_id", "") or "") == bmanga_id
            and bool(obj.get("bmanga_managed", False))
        ):
            return obj
    return None


def _add_page_only_probe() -> None:
    from bmanga_dev_page_file_stage.utils import object_naming as on

    obj = bpy.data.objects.new("page_only_balloon_probe", None)
    bpy.context.scene.collection.objects.link(obj)
    obj[on.PROP_KIND] = "balloon"
    obj[on.PROP_ID] = "page_only_balloon_probe"
    obj[on.PROP_PARENT_KEY] = "p0001"
    obj[on.PROP_MANAGED] = True


def _add_current_page_preview_balloon(work) -> None:
    entry = work.pages[0].balloons.add()
    entry.id = "preview_balloon"
    entry.title = "preview_balloon"
    entry.parent_kind = "page"
    entry.parent_key = "p0001"
    entry.shape = "ellipse"
    entry.x_mm = 52.0
    entry.y_mm = 70.0
    entry.width_mm = 64.0
    entry.height_mm = 44.0
    entry.fill_color = (1.0, 0.0, 0.0, 1.0)
    entry.fill_opacity = 100.0
    entry.line_style = "solid"
    entry.line_width_mm = 0.8


def _add_other_page_balloon_entry() -> None:
    """p0002 のページ用 blend を開いてフキダシデータを作り、保存して戻る.

    v0.6.279 以降、作品ファイルは他ページの詳細を保持しない (保存ガードで
    page.json にも書かれない) ため、対象ページを開いて作成する。
    """
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=1)
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bmanga_work
    entry = work.pages[1].balloons.add()
    entry.id = "other_page_balloon"
    entry.title = "other_page_balloon"
    entry.parent_key = "p0002"
    entry.x_mm = 20.0
    entry.y_mm = 20.0
    entry.width_mm = 40.0
    entry.height_mm = 30.0
    result = bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
    assert result == {"FINISHED"}, result


def _image_has_red_area(path: Path) -> bool:
    from PIL import Image

    image = Image.open(path).convert("RGBA")
    step = max(1, min(image.width, image.height) // 96)
    for y in range(0, image.height, step):
        for x in range(0, image.width, step):
            r, g, b, a = image.getpixel((x, y))
            if a > 180 and r > 180 and g < 120 and b < 120:
                return True
    return False


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_file_stage_"))
    mod = None
    try:
        mod = _load_addon()
        work_dir = temp_root / "PageFileStage.bmanga"
        result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
        assert result == {"FINISHED"}, result
        from bmanga_dev_page_file_stage.utils import page_preview_object

        bpy.context.scene.bmanga_coma_camera_settings.name_bg_images_opacity = 25.0
        assert abs(page_preview_object._preview_opacity_factor(bpy.context.scene) - 1.0) < 0.001  # noqa: SLF001
        assert bpy.context.scene.bmanga_work.work_info.display_work_name.position == "bottom-left"
        assert _mainfile() == (work_dir / "work.blend").resolve()
        _assert_work_file_preview_only()

        for _ in range(3):
            result = bpy.ops.bmanga.page_add()
            assert result == {"FINISHED"}, result
        _assert_work_file_preview_only()

        _add_other_page_balloon_entry()
        result = bpy.ops.bmanga.open_page_file(index=0)
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0001" / "page.blend").resolve()
        assert bool(getattr(bpy.context.scene, "bmanga_overview_mode", False)) is True
        assert str(getattr(bpy.context.scene, "bmanga_current_page_id", "")) == "p0001"
        bpy.context.scene.bmanga_coma_camera_settings.name_bg_images_opacity = 25.0
        assert abs(page_preview_object._preview_opacity_factor(bpy.context.scene) - 1.0) < 0.001  # noqa: SLF001
        assert bpy.data.collections.get("p0002") is None
        assert _managed_object("balloon", "other_page_balloon") is None
        _assert_page_file_current_page_runtime_only("p0001")
        _assert_current_page_runtime_aligned(0)
        from bmanga_dev_page_file_stage.utils import coma_border_object, coma_plane, layer_object_sync

        work = bpy.context.scene.bmanga_work
        other_page = work.pages[1]
        # 現行仕様ではページ用 blend に他ページの詳細データは無いため、
        # 汚染プローブ用に一時的なコマデータを直接作って実体を生成する
        assert len(other_page.comas) == 0, "ページ用 blend が他ページの詳細を保持しています"
        probe_coma = other_page.comas.add()
        probe_coma.id = "c01"
        probe_coma.coma_id = "c01"
        probe_coma.shape_type = "rect"
        probe_coma.rect_x_mm = 10.0
        probe_coma.rect_y_mm = 10.0
        probe_coma.rect_width_mm = 60.0
        probe_coma.rect_height_mm = 40.0
        coma_plane.ensure_coma_plane(bpy.context.scene, work, other_page, probe_coma)
        coma_border_object.ensure_coma_border_object(bpy.context.scene, work, other_page, probe_coma)
        assert "p0002" in _coma_runtime_owner_pages()
        layer_object_sync.mirror_work_to_outliner(bpy.context.scene, work)
        other_page.comas.clear()  # 後始末: 他ページの詳細を持たない状態へ戻す
        _assert_page_file_current_page_runtime_only("p0001")
        assert bpy.data.collections.get("p0002") is None
        previews = _page_preview_objects()
        assert len(previews) == 4, _page_preview_object_ids()
        assert _page_preview_object_ids() == {"p0001", "p0002", "p0003", "p0004"}
        _assert_preview_cache_hidden()
        assert (work_dir / "p0002" / "page_preview.png").is_file()
        assert str(getattr(bpy.context.scene, "bmanga_page_preview_range_mode", "")) == "ALL"
        assert abs(float(getattr(bpy.context.scene, "bmanga_page_preview_resolution_percentage", 0.0)) - 25.0) < 0.001
        assert bpy.ops.bmanga.coma_knife_cut.poll()
        assert bpy.ops.bmanga.coma_create_tool.poll()
        assert bpy.ops.bmanga.balloon_tool.poll()
        assert bpy.ops.bmanga.text_tool.poll()
        assert bpy.ops.bmanga.effect_line_tool.poll()
        assert bpy.ops.bmanga.layer_move_tool.poll()
        assert bpy.ops.bmanga.mask_regenerate_all.poll()
        assert bpy.ops.bmanga.mask_remove_orphans.poll()
        assert bpy.ops.bmanga.repair_hierarchy.poll()
        assert "FINISHED" in bpy.ops.bmanga.mask_regenerate_all("EXEC_DEFAULT")
        _assert_page_file_current_page_runtime_only("p0001")
        assert "FINISHED" in bpy.ops.bmanga.mask_remove_orphans("EXEC_DEFAULT")
        _assert_page_file_current_page_runtime_only("p0001")
        assert "FINISHED" in bpy.ops.bmanga.repair_hierarchy("EXEC_DEFAULT")
        _assert_page_file_current_page_runtime_only("p0001")

        from bmanga_dev_page_file_stage.utils import page_grid
        from bmanga_dev_page_file_stage.ui import overlay
        from bmanga_dev_page_file_stage.operators import coma_knife_cut_op

        work = bpy.context.scene.bmanga_work
        assert overlay._page_file_overview_indices(bpy.context.scene, work) == {0, 1, 2, 3}  # noqa: SLF001
        rects = page_preview_object.preview_rects_mm(bpy.context.scene, work)
        assert "p0002" in rects
        index, x0, y0, x1, y1 = rects["p0002"]
        assert index == 1
        ox, oy = page_grid.page_total_offset_mm(work, bpy.context.scene, 1)
        assert abs(x0 - ox) < 0.001 and abs(y0 - oy) < 0.001
        assert abs((x1 - x0) - float(work.paper.canvas_width_mm)) < 0.001
        assert abs((y1 - y0) - float(work.paper.canvas_height_mm)) < 0.001
        hit = page_preview_object.page_index_at_world_mm(
            bpy.context.scene,
            work,
            (x0 + x1) * 0.5,
            (y0 + y1) * 0.5,
        )
        assert hit == 1
        current_ox, current_oy = page_grid.page_total_offset_mm(work, bpy.context.scene, 0)
        coma = work.pages[0].comas[0]
        coma_hit = coma_knife_cut_op._find_coma_at_world(
            work,
            current_ox + float(coma.rect_x_mm) + float(coma.rect_width_mm) * 0.5,
            current_oy + float(coma.rect_y_mm) + float(coma.rect_height_mm) * 0.5,
        )
        assert coma_hit == (0, 0), f"ページファイル上のコマを検出できません: {coma_hit}"
        # 他ページのコマ位置は (詳細がメモリに無いため) ディスクの page.json から取る
        import json as _json

        other_pj = _json.loads((work_dir / "p0002" / "page.json").read_text(encoding="utf-8"))
        other_comas = other_pj.get("comas", [])
        assert other_comas, "p0002 の page.json にコマがありません"
        other_rect = other_comas[0].get("shape", {}).get("rect", {})
        other_ox, other_oy = page_grid.page_total_offset_mm(work, bpy.context.scene, 1)
        other_hit = coma_knife_cut_op._find_coma_at_world(
            work,
            other_ox + float(other_rect.get("x", 0.0)) + float(other_rect.get("widthMm", 0.0)) * 0.5,
            other_oy + float(other_rect.get("y", 0.0)) + float(other_rect.get("heightMm", 0.0)) * 0.5,
        )
        assert other_hit is None, f"ページファイル上で他ページのコマを検出しています: {other_hit}"
        bpy.context.scene.bmanga_page_preview_enabled = False
        assert all(obj.hide_viewport for obj in _page_preview_objects())
        assert overlay._page_file_overview_indices(bpy.context.scene, work) == {0}  # noqa: SLF001
        bpy.context.scene.bmanga_page_preview_enabled = True
        _assert_preview_cache_hidden()
        bpy.context.scene.bmanga_page_preview_range_mode = "NEAR"
        rects = page_preview_object.preview_rects_mm(bpy.context.scene, work)
        assert set(rects) == {"p0001", "p0002"}
        assert overlay._page_file_overview_indices(bpy.context.scene, work) == {0, 1}  # noqa: SLF001
        _assert_preview_cache_hidden()
        preview_obj = _page_preview_object_for("p0002")
        assert preview_obj is not None
        mat = preview_obj.active_material
        assert mat is not None and mat.node_tree is not None
        assert any(getattr(node, "type", "") == "EMISSION" for node in mat.node_tree.nodes)
        opacity_node = mat.node_tree.nodes.get(page_preview_object.PREVIEW_OPACITY_NODE)
        assert opacity_node is not None
        assert abs(float(opacity_node.outputs[0].default_value) - 1.0) < 0.001
        assert abs(float(getattr(mat, "diffuse_color", (1.0, 1.0, 1.0, 0.0))[3]) - 1.0) < 0.001
        before_cut = len(work.pages[0].comas)
        cut_target = work.pages[0].comas[0]
        cut_x = float(cut_target.rect_x_mm) + float(cut_target.rect_width_mm) * 0.5
        cut_y0 = float(cut_target.rect_y_mm) + 2.0
        cut_y1 = float(cut_target.rect_y_mm) + float(cut_target.rect_height_mm) - 2.0
        assert coma_knife_cut_op._apply_cut_to_coma(
            work,
            work.pages[0],
            0,
            work_dir,
            (cut_x, cut_y0),
            (cut_x, cut_y1),
        )
        coma_knife_cut_op._sync_layer_stack_after_cut(bpy.context)
        assert len(work.pages[0].comas) == before_cut + 1
        _assert_preview_cache_hidden()
        _assert_page_file_current_page_runtime_only("p0001")
        # 解像度%の変更は、表示中 (前後ページ → p0002 が見えている) のプレビューを
        # 新しいサイズで再生成する。v0.6.280 以降は表示対象だけ再生成するため、
        # 非表示にする前に確認する。「画像解像度%」はページ実解像度
        # (用紙サイズ×DPI) に対する割合・長辺1536px上限なので、上限未満になる
        # 10% を指定し、期待サイズは実装と同じ計算で求める。
        bpy.context.scene.bmanga_page_preview_resolution_percentage = 10.0
        expected_preview_size = page_preview_object._image_size(  # noqa: SLF001
            work, bpy.context.scene, work.pages[1]
        )
        from PIL import Image

        preview_size = Image.open(work_dir / "p0002" / "page_preview.png").size
        assert tuple(preview_size) == tuple(expected_preview_size), (
            preview_size,
            expected_preview_size,
        )
        assert max(preview_size) < 1536, preview_size
        preview_image = Image.open(work_dir / "p0002" / "page_preview.png").convert("RGBA")
        r, g, b, a = preview_image.getpixel((preview_image.width // 2, preview_image.height // 2))
        assert a == 255 and max(r, g, b) > 200, (r, g, b, a)
        bpy.context.scene.bmanga_overview_cols = 6
        bpy.context.scene.bmanga_overview_gap_mm = 0.0
        bpy.context.scene.bmanga_page_preview_enabled = False
        _assert_preview_cache_hidden()

        _add_current_page_preview_balloon(work)
        _add_page_only_probe()
        result = bpy.ops.bmanga.work_save()
        assert result == {"FINISHED"}, result
        assert _managed_kind_count("balloon") >= 1
        assert _image_has_red_area(work_dir / "p0001" / "page_preview.png")

        result = bpy.ops.bmanga.exit_page_file()
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "work.blend").resolve()
        work = bpy.context.scene.bmanga_work
        bpy.context.scene.bmanga_page_preview_range_mode = "NEAR"
        assert page_preview_object.preview_page_indices(bpy.context.scene, work) == set(range(len(work.pages)))
        bpy.context.scene.bmanga_page_preview_range_mode = "ALL"
        assert page_preview_object.preview_page_indices(bpy.context.scene, work) == set(range(len(work.pages)))
        _assert_work_file_preview_only()
        assert _managed_kind_count("balloon") == 0
        assert _image_has_red_area(work_dir / "p0001" / "page_preview.png")
        assert not bpy.ops.bmanga.coma_knife_cut.poll()
        assert not bpy.ops.bmanga.coma_create_tool.poll()
        assert not bpy.ops.bmanga.balloon_tool.poll()
        assert not bpy.ops.bmanga.text_tool.poll()
        assert not bpy.ops.bmanga.effect_line_tool.poll()
        assert not bpy.ops.bmanga.layer_move_tool.poll()
        work.paper.show_guides = not bool(work.paper.show_guides)
        work.safe_area_overlay.bleed_outer_enabled = not bool(work.safe_area_overlay.bleed_outer_enabled)
        _assert_work_file_preview_only()

        result = bpy.ops.bmanga.open_page_file(index=0)
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0001" / "page.blend").resolve()
        assert _managed_kind_count("balloon") >= 1
        assert _managed_object("balloon", "page_only_balloon_probe") is not None
        assert bpy.data.collections.get("p0002") is None
        _assert_page_file_current_page_runtime_only("p0001")
        assert len(_page_preview_objects()) >= 1
        assert int(getattr(bpy.context.scene, "bmanga_overview_cols", -1)) == 6
        assert abs(float(getattr(bpy.context.scene, "bmanga_overview_gap_mm", -1.0))) < 0.001
        assert not bool(getattr(bpy.context.scene, "bmanga_page_preview_enabled", True))
        assert str(getattr(bpy.context.scene, "bmanga_page_preview_range_mode", "")) == "ALL"
        assert abs(float(getattr(bpy.context.scene, "bmanga_page_preview_resolution_percentage", 0.0)) - 10.0) < 0.001

        result = bpy.ops.bmanga.page_select(index=1)
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0002" / "page.blend").resolve()
        assert str(getattr(bpy.context.scene, "bmanga_current_page_id", "")) == "p0002"
        assert int(getattr(bpy.context.scene.bmanga_work, "active_page_index", -1)) == 1
        assert bool(getattr(bpy.context.scene, "bmanga_overview_mode", False)) is True
        assert bpy.data.collections.get("p0001") is None
        assert _managed_object("balloon", "page_only_balloon_probe") is None
        assert _managed_object("balloon", "other_page_balloon") is not None
        _assert_preview_cache_hidden()
        _assert_page_file_current_page_runtime_only("p0002")
        _assert_current_page_runtime_aligned(1)

        work = bpy.context.scene.bmanga_work
        work.active_page_index = 1
        work.pages[1].active_coma_index = 0
        result = bpy.ops.bmanga.enter_coma_mode()
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0002" / "c01" / "c01.blend").resolve()
        work = bpy.context.scene.bmanga_work
        bpy.context.scene.bmanga_page_preview_enabled = True
        bpy.context.scene.bmanga_page_preview_range_mode = "NEAR"
        assert page_preview_object.preview_page_indices(bpy.context.scene, work) == {0, 1, 2}
        bpy.context.scene.bmanga_page_preview_range_mode = "ALL"
        assert page_preview_object.preview_page_indices(bpy.context.scene, work) == set(range(len(work.pages)))
        bpy.context.scene.bmanga_coma_camera_settings.name_bg_images_opacity = 25.0
        assert abs(page_preview_object._preview_opacity_factor(bpy.context.scene) - 0.25) < 0.001  # noqa: SLF001

        result = bpy.ops.bmanga.exit_coma_mode()
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0002" / "page.blend").resolve()

        print("BMANGA_PAGE_FILE_STAGE_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
