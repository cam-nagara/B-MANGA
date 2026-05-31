"""Blender実機用: アセット登録サムネイルのパターン網羅確認."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_asset_thumbnail",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_asset_thumbnail"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _item_by_uid(context):
    from bname_dev_asset_thumbnail.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    return {layer_stack_utils.stack_item_uid(item): item for item in stack}


def _assert_preview(coll, label: str) -> None:
    preview = getattr(coll, "preview", None)
    if preview is None:
        raise AssertionError(f"{label}: サムネイルがありません")
    width, height = int(preview.image_size[0]), int(preview.image_size[1])
    pixels = list(preview.image_pixels_float)
    if width <= 0 or height <= 0 or len(pixels) != width * height * 4:
        raise AssertionError(f"{label}: サムネイル画像が空です")
    if not bool(getattr(preview, "is_image_custom", False)):
        raise AssertionError(f"{label}: カスタムサムネイルではありません")
    values = []
    dark_pixels = 0
    alpha_pixels = 0
    for i in range(0, len(pixels), 4):
        r, g, b, a = pixels[i:i + 4]
        if a > 0.5:
            alpha_pixels += 1
        values.extend((r, g, b))
        if a > 0.5 and max(r, g, b) < 0.35:
            dark_pixels += 1
    if alpha_pixels < width * height * 0.9:
        raise AssertionError(f"{label}: 不透明ピクセルが不足しています")
    if max(values) - min(values) < 0.35:
        raise AssertionError(f"{label}: サムネイルの濃淡が不足しています")
    if dark_pixels < 12:
        raise AssertionError(f"{label}: 内容を示す線・輪郭が不足しています")


def _preview_pixels(coll) -> list[float]:
    preview = getattr(coll, "preview", None)
    if preview is None:
        return []
    return list(preview.image_pixels_float)


def _assert_preview_differs(
    pixels_a: list[float],
    pixels_b: list[float],
    label_a: str,
    label_b: str,
) -> None:
    if not pixels_a or not pixels_b or len(pixels_a) != len(pixels_b):
        raise AssertionError(f"{label_a} と {label_b}: サムネイル比較ができません")
    total = 0.0
    samples = 0
    for i in range(0, len(pixels_a), 4):
        total += abs(pixels_a[i] - pixels_b[i])
        total += abs(pixels_a[i + 1] - pixels_b[i + 1])
        total += abs(pixels_a[i + 2] - pixels_b[i + 2])
        samples += 3
    diff = total / max(1, samples)
    if diff < 0.005:
        raise AssertionError(f"{label_a} と {label_b}: 形状差がサムネイルに反映されていません")


def _find_entry_by_title(collection, title: str):
    for entry in collection:
        if str(getattr(entry, "title", "") or "") == title:
            return entry
    return None


def _register_payload(context, uids: list[str], name: str, target):
    from bname_dev_asset_thumbnail.utils import asset_bundle

    items = _item_by_uid(context)
    missing = [uid for uid in uids if uid not in items]
    if missing:
        available = "\n".join(f"  {uid}" for uid in sorted(items))
        raise AssertionError(f"{name}: 登録元レイヤーが一覧にありません: {missing}\n{available}")
    payload = asset_bundle.build_payload(context, [items[uid] for uid in uids], name=name)
    coll = asset_bundle.create_collection_asset(context, payload, target=target)
    if coll.asset_data is None:
        raise AssertionError(f"{name}: アセット登録されていません")
    _assert_preview(coll, name)
    return coll


def _make_balloon(context, page, *, title: str, shape: str, x: float, y: float, w: float, h: float):
    from bname_dev_asset_thumbnail.operators import balloon_op
    from bname_dev_asset_thumbnail.utils import balloon_curve_object

    entry = page.balloons.add()
    entry.id = balloon_op._allocate_balloon_id(page, context.scene.bname_work)
    entry.title = title
    entry.shape = shape
    entry.x_mm = x
    entry.y_mm = y
    entry.width_mm = w
    entry.height_mm = h
    entry.parent_kind = "page"
    entry.parent_key = page.id
    if shape == "rect":
        entry.rounded_corner_enabled = True
        entry.rounded_corner_radius_mm = 4.0
    if shape == "thorn":
        entry.spike_count = 18
        entry.spike_depth_mm = 7.0
    if shape == "cloud":
        entry.cloud_bump_width_mm = 12.0
        entry.cloud_bump_height_mm = 4.0
    balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    return entry


def _make_text(context, page, *, title: str, body: str, x: float, y: float, w: float, h: float, mode: str):
    from bname_dev_asset_thumbnail.operators import text_op
    from bname_dev_asset_thumbnail.utils import text_real_object

    entry = page.texts.add()
    entry.id = text_op._allocate_text_id(page)
    entry.title = title
    entry.body = body
    entry.x_mm = x
    entry.y_mm = y
    entry.width_mm = w
    entry.height_mm = h
    entry.writing_mode = mode
    entry.parent_kind = "page"
    entry.parent_key = page.id
    text_real_object.ensure_text_real_object(scene=context.scene, entry=entry, page=page)
    return entry


def _make_effect(context, page, *, title: str, x: float, y: float, w: float, h: float):
    from bname_dev_asset_thumbnail.operators import effect_line_op

    obj, layer = effect_line_op._create_effect_layer(
        context,
        (float(x), float(y), float(w), float(h)),
        parent_key=page.id,
    )
    if obj is None or layer is None:
        raise AssertionError(f"{title}: 効果線を作成できません")
    layer.name = title
    return obj, layer


def _balloon_uid(page, entry) -> str:
    from bname_dev_asset_thumbnail.utils import layer_stack as layer_stack_utils

    return layer_stack_utils.target_uid("balloon", f"{page.id}:{entry.id}")


def _text_uid(page, entry) -> str:
    from bname_dev_asset_thumbnail.utils import layer_stack as layer_stack_utils

    return layer_stack_utils.target_uid("text", f"{page.id}:{entry.id}")


def _effect_uid(layer) -> str:
    from bname_dev_asset_thumbnail.utils import layer_stack as layer_stack_utils

    return layer_stack_utils.target_uid("effect", layer_stack_utils._node_stack_key(layer))


def _select_objects(objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    if objects:
        bpy.context.view_layer.objects.active = objects[0]


def _fake_asset_browser_context(objects: list[bpy.types.Object], library_path: Path):
    params = SimpleNamespace(
        asset_library_reference="BNameThumb",
        catalog_id="11111111-1111-1111-1111-111111111111",
    )
    area = SimpleNamespace(
        type="FILE_BROWSER",
        x=0,
        y=0,
        width=480,
        height=900,
        spaces=SimpleNamespace(active=SimpleNamespace(browse_mode="ASSETS", params=params)),
    )
    return SimpleNamespace(
        selected_objects=objects,
        active_object=objects[0] if objects else None,
        screen=SimpleNamespace(areas=[area]),
        preferences=SimpleNamespace(
            filepaths=SimpleNamespace(
                asset_libraries=[SimpleNamespace(name="BNameThumb", path=str(library_path))]
            )
        ),
    )


def _assert_saved_libraries(paths: list[tuple[Path, str]]) -> None:
    for blend_path, label in paths:
        if not blend_path.exists():
            raise AssertionError(f"{label}: 外部ライブラリファイルがありません")
        bpy.ops.wm.open_mainfile(filepath=str(blend_path))
        asset_collections = [coll for coll in bpy.data.collections if coll.asset_data is not None]
        if not asset_collections:
            raise AssertionError(f"{label}: 外部ライブラリ内にアセットがありません")
        _assert_preview(asset_collections[0], f"{label} (開き直し)")


def main() -> None:
    mod = None
    temp_root = Path(tempfile.mkdtemp(prefix="bname_asset_thumbnail_"))
    saved: list[tuple[Path, str]] = []
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        from bname_dev_asset_thumbnail.utils import asset_bundle
        from bname_dev_asset_thumbnail.utils import balloon_curve_object
        from bname_dev_asset_thumbnail.utils import effect_line_object
        from bname_dev_asset_thumbnail.utils import layer_links
        from bname_dev_asset_thumbnail.utils import layer_stack as layer_stack_utils
        from bname_dev_asset_thumbnail.utils import text_real_object

        result = bpy.ops.bname.work_new(filepath=str(temp_root / "AssetThumbnail.bname"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")

        context = bpy.context
        work = context.scene.bname_work
        page = work.pages[0]
        external_root = temp_root / "AssetLibrary"
        external_target = asset_bundle.AssetBrowserTarget("BNameThumb", "", str(external_root), True)

        balloon_specs = [
            ("楕円フキダシ", "ellipse", 16, 24, 42, 22),
            ("角丸フキダシ", "rect", 72, 25, 46, 25),
            ("雲フキダシ", "cloud", 132, 25, 50, 28),
            ("トゲフキダシ", "thorn", 18, 72, 54, 30),
            ("八角フキダシ", "octagon", 86, 75, 46, 26),
        ]
        text_specs = [
            ("縦書きテキスト", "縦書き", 18, 124, 22, 48, "vertical"),
            ("横書きテキスト", "横書きテキスト", 56, 132, 52, 16, "horizontal"),
            ("長文テキスト", "長いセリフのテスト", 126, 125, 58, 36, "vertical"),
        ]
        for title, shape, x, y, w, h in balloon_specs:
            _make_balloon(context, page, title=title, shape=shape, x=x, y=y, w=w, h=h)
        for title, body, x, y, w, h, mode in text_specs:
            _make_text(context, page, title=title, body=body, x=x, y=y, w=w, h=h, mode=mode)
        effects = [
            _make_effect(context, page, title="集中線", x=22, y=186, w=46, h=38),
            _make_effect(context, page, title="横長効果線", x=82, y=190, w=72, h=28),
            _make_effect(context, page, title="縦長効果線", x=164, y=176, w=30, h=58),
        ]

        layer_stack_utils.sync_layer_stack_after_data_change(context)
        balloons = [_find_entry_by_title(page.balloons, title) for title, *_rest in balloon_specs]
        texts = [_find_entry_by_title(page.texts, title) for title, *_rest in text_specs]
        if any(entry is None for entry in balloons) or any(entry is None for entry in texts):
            raise AssertionError("作成したフキダシまたはテキストを再取得できません")
        balloon_uids = [_balloon_uid(page, entry) for entry in balloons]
        text_uids = [_text_uid(page, entry) for entry in texts]
        effect_uids = [_effect_uid(layer) for _obj, layer in effects]

        cases: list[tuple[str, list[str]]] = []
        cases.extend((f"単体フキダシ {entry.title}", [uid]) for entry, uid in zip(balloons, balloon_uids))
        cases.extend((f"単体テキスト {entry.title}", [uid]) for entry, uid in zip(texts, text_uids))
        cases.extend((f"単体効果線 {layer.name}", [uid]) for (_obj, layer), uid in zip(effects, effect_uids))

        texts[0].parent_balloon_id = balloons[0].id
        layer_links.link_uids(context, [balloon_uids[0], text_uids[0]])
        layer_links.link_uids(context, [effect_uids[0], effect_uids[1]])
        layer_links.link_uids(context, [balloon_uids[1], balloon_uids[2]])
        layer_links.link_uids(context, [balloon_uids[3], text_uids[2], effect_uids[2]])
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        cases.extend(
            [
                ("リンク フキダシ＆テキスト", [balloon_uids[0], text_uids[0]]),
                ("リンク 効果線＆効果線", [effect_uids[0], effect_uids[1]]),
                ("リンク フキダシ＆フキダシ", [balloon_uids[1], balloon_uids[2]]),
                ("リンク フキダシ＆テキスト＆効果線", [balloon_uids[3], text_uids[2], effect_uids[2]]),
            ]
        )

        balloon_previews: dict[str, list[float]] = {}
        for label, uids in cases:
            local = _register_payload(context, uids, label, asset_bundle.AssetBrowserTarget("LOCAL"))
            _assert_preview(local, f"{label} (現在のファイル)")
            if label.startswith("単体フキダシ "):
                balloon_previews[label.removeprefix("単体フキダシ ")] = _preview_pixels(local)
            before = set(external_root.glob("*.blend"))
            external = _register_payload(context, uids, label, external_target)
            _assert_preview(external, f"{label} (外部ライブラリ)")
            new_files = sorted(set(external_root.glob("*.blend")) - before)
            if len(new_files) != 1:
                raise AssertionError(f"{label}: 外部ライブラリの保存ファイル数が正しくありません")
            saved.append((new_files[0], label))

        _assert_preview_differs(
            balloon_previews.get("楕円フキダシ", []),
            balloon_previews.get("雲フキダシ", []),
            "楕円フキダシ",
            "雲フキダシ",
        )
        _assert_preview_differs(
            balloon_previews.get("楕円フキダシ", []),
            balloon_previews.get("トゲフキダシ", []),
            "楕円フキダシ",
            "トゲフキダシ",
        )

        object_candidates = [
            balloon_curve_object.find_balloon_object(balloons[4].id),
            text_real_object.find_text_object(page.id, texts[1].id),
            effect_line_object.find_effect_display_object(effects[0][0]) or effects[0][0],
        ]
        objects = [obj for obj in object_candidates if obj is not None]
        if len(objects) != 3:
            raise AssertionError("オブジェクト登録用の実体を取得できません")
        _select_objects(objects)
        object_coll = asset_bundle.register_selected_objects_as_asset(
            context,
            name="オブジェクト複数",
        )
        if object_coll is None:
            raise AssertionError("オブジェクトアセットを登録できません")
        _assert_preview(object_coll, "オブジェクト複数")
        before = set(external_root.glob("*.blend"))
        object_external = asset_bundle.register_selected_objects_as_asset(
            _fake_asset_browser_context(objects, external_root),
            name="オブジェクト複数",
        )
        if object_external is None:
            raise AssertionError("外部ライブラリへオブジェクトアセットを登録できません")
        _assert_preview(object_external, "オブジェクト複数 (外部ライブラリ)")
        new_files = sorted(set(external_root.glob("*.blend")) - before)
        if len(new_files) != 1:
            raise AssertionError("オブジェクト複数: 外部ライブラリの保存ファイル数が正しくありません")
        saved.append((new_files[0], "オブジェクト複数"))

        _assert_saved_libraries(saved)
        print("BNAME_ASSET_THUMBNAIL_PATTERNS_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    main()
