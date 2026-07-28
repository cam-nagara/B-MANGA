"""ルビ付き・フキダシなしテキストの画像/メッシュ縦横比回帰テスト."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import time

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_text_ruby_consistency"
VERIFY_DIR = ROOT / "_verify" / "2026-07-28_text_handle_fix"
VERIFY_PNG = VERIFY_DIR / "scenario_text_without_balloon_healed.png"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def _mesh_coords(obj) -> tuple[tuple[float, float, float], ...]:
    return tuple(tuple(float(value) for value in vertex.co) for vertex in obj.data.vertices)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_text_ruby_consistency_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        result = bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "TextRubyConsistency.bmanga")
        )
        assert "FINISHED" in result, result

        from bmanga_dev_text_ruby_consistency.utils import text_real_object

        scene = bpy.context.scene
        work = scene.bmanga_work
        page = work.pages[0]
        with text_real_object.suspend_auto_sync():
            entry = page.texts.add()
            entry.id = "scenario_without_balloon"
            entry.title = "フキダシなしシナリオ"
            entry.body = "・幽奈、テレつつ、嬉しげかつやや困り顔"
            entry.writing_mode = "vertical"
            entry.x_mm = 30.0
            entry.y_mm = 40.0
            entry.width_mm = 10.0
            entry.height_mm = 100.0
            entry.parent_kind = "page"
            entry.parent_key = page.id
            entry.parent_balloon_id = ""

        obj = text_real_object.ensure_text_real_object(
            scene=scene,
            entry=entry,
            page=page,
        )
        assert obj is not None
        image_without_ruby = text_real_object._image_for_object(obj)
        assert image_without_ruby is not None
        size_without_ruby = tuple(image_without_ruby.size)

        # 旧不具合を再現: ルビ追加後に画像を作り直さずメッシュだけを広げ、
        # それを最新の描画署名として誤記録した保存済み実体を作る。
        with text_real_object.suspend_auto_sync():
            span = entry.ruby_spans.add()
            span.start = entry.body.index("幽奈")
            span.length = 2
            span.ruby_text = "ゆうな"
            span.style = "group"
        assert text_real_object._refresh_existing_text_mesh(scene, entry, page)
        obj[text_real_object._TEXT_RENDER_SIGNATURE_PROP] = (
            text_real_object._entry_render_signature(entry)
        )
        size_with_ruby = text_real_object._expected_image_size(entry)
        assert size_without_ruby[0] < size_with_ruby[0]
        assert not text_real_object.is_text_real_object_current(obj, entry)

        healed = text_real_object.ensure_text_real_object(
            scene=scene,
            entry=entry,
            page=page,
        )
        assert healed is obj
        healed_image = text_real_object._image_for_object(healed)
        assert healed_image is not None
        assert tuple(healed_image.size) == size_with_ruby
        assert text_real_object.is_text_real_object_current(healed, entry)

        xs = [float(vertex.co.x) for vertex in healed.data.vertices]
        ys = [float(vertex.co.y) for vertex in healed.data.vertices]
        mesh_ratio = (max(xs) - min(xs)) / (max(ys) - min(ys))
        image_ratio = float(healed_image.size[0]) / float(healed_image.size[1])
        # 300dpiへの丸めで最大1px弱の比率差は許容する。
        assert abs(mesh_ratio - image_ratio) < 5.0e-4, (mesh_ratio, image_ratio)

        # JSON読込中と同じ同期抑止区間では、free-transform callbackが
        # ルビ未読込の途中状態でメッシュや描画署名を確定してはならない。
        coords_before = _mesh_coords(healed)
        image_before = healed_image
        with text_real_object.suspend_auto_sync():
            entry.free_transform_enabled = True
            entry.free_transform_top_right = (2.0, 1.0)
        assert _mesh_coords(healed) == coords_before
        transformed = text_real_object.ensure_text_real_object(
            scene=scene,
            entry=entry,
            page=page,
        )
        assert transformed is healed
        assert text_real_object._image_for_object(transformed) is image_before
        assert _mesh_coords(transformed) != coords_before
        assert text_real_object.is_text_real_object_current(transformed, entry)
        started = time.perf_counter()
        for _index in range(1000):
            assert text_real_object.text_real_object_geometry_looks_current(
                transformed,
                entry,
            )
        geometry_check_ms = (time.perf_counter() - started) * 1000.0
        assert geometry_check_ms < 250.0, geometry_check_ms
        VERIFY_DIR.mkdir(parents=True, exist_ok=True)
        scene.render.image_settings.file_format = "PNG"
        healed_image.save_render(filepath=str(VERIFY_PNG), scene=scene)
        from PIL import Image

        with Image.open(VERIFY_PNG) as opened:
            rgba = opened.convert("RGBA")
            white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            white.alpha_composite(rgba)
            white.convert("RGB").save(VERIFY_PNG)

        print(
            "BMANGA_TEXT_RUBY_MESH_IMAGE_CONSISTENCY_OK "
            f"old={size_without_ruby} healed={size_with_ruby} "
            f"checks1000={geometry_check_ms:.3f}ms image={VERIFY_PNG}"
        )
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
