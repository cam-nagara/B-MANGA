"""Blender実機用: B-MANGA Render全プリセットの実レンダーをAI目視用に一覧化する."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLEND = Path(r"D:\TM Dropbox\Share\B-MANGA\c_file\c00.blend")
OUT_DIR = Path(
    os.environ.get("BMANGA_RENDER_VISUAL_OUT", "")
    or (ROOT / ".codex" / "visual" / "bmanga_render_presets")
)
RESOLUTION = int(os.environ.get("BMANGA_RENDER_VISUAL_RES", "96"))
SAMPLE_OVERRIDE = int(os.environ.get("BMANGA_RENDER_VISUAL_SAMPLES", "1"))
RENDER_MODE = os.environ.get("BMANGA_RENDER_VISUAL_MODE", "RENDER").upper()
FORCE_ENGINE = os.environ.get("BMANGA_RENDER_VISUAL_FORCE_ENGINE", "BLENDER_WORKBENCH")
PROXY_COLLECTIONS = (
    ("レイアウト", (0.95, 0.20, 0.20, 1.0)),
    ("アタリ", (1.00, 0.55, 0.10, 1.0)),
    ("キャラ", (0.20, 0.75, 0.25, 1.0)),
    ("背景", (0.20, 0.45, 1.00, 1.0)),
    ("効果", (0.85, 0.25, 1.00, 1.0)),
    ("エフェクト", (0.55, 0.20, 0.95, 1.0)),
    ("植物", (0.10, 0.55, 0.15, 1.0)),
    ("空", (0.25, 0.80, 1.00, 1.0)),
    ("グラデ_白", (0.95, 0.95, 0.95, 1.0)),
    ("グラデ_黒", (0.05, 0.05, 0.05, 1.0)),
    ("フォグ", (0.70, 0.70, 0.85, 1.0)),
    ("雲", (0.80, 0.90, 1.00, 1.0)),
    ("コマ枠", (0.0, 0.0, 0.0, 1.0)),
)


def _load_render_package():
    package_root = ROOT / "addons" / "b_manga_render"
    spec = importlib.util.spec_from_file_location(
        "bmanga_render_visual",
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_render_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _font(ImageFont, *, size: int):
    for path in (
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    ):
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _ensure_pillow_path() -> None:
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    candidates = [
        ROOT / "wheels" / "_installed" / f"pillow-12.2.0-{tag}-{tag}-win_amd64",
        ROOT / "wheels" / f"pillow-12.2.0-{tag}-{tag}-win_amd64.whl",
        ROOT / "wheels" / "_installed" / "pillow-12.2.0-cp313-cp313-win_amd64",
        ROOT / "wheels" / "pillow-12.2.0-cp313-cp313-win_amd64.whl",
        ROOT / "wheels" / "_installed" / "pillow-12.2.0-cp312-cp312-win_amd64",
        ROOT / "wheels" / "pillow-12.2.0-cp312-cp312-win_amd64.whl",
        ROOT / "wheels" / "_installed" / "pillow-12.2.0-cp311-cp311-win_amd64",
        ROOT / "wheels" / "pillow-12.2.0-cp311-cp311-win_amd64.whl",
    ]
    for wheel in candidates:
        if wheel.exists() and str(wheel) not in sys.path:
            sys.path.insert(0, str(wheel))
            return


def _iter_node_trees(scene):
    seen: set[int] = set()
    for attr in ("node_tree", "compositing_node_group"):
        tree = getattr(scene, attr, None)
        if tree is not None:
            key = int(tree.as_pointer())
            if key not in seen:
                seen.add(key)
                yield tree
    for group in bpy.data.node_groups:
        if group is not None:
            key = int(group.as_pointer())
            if key not in seen:
                seen.add(key)
                yield group


def _walk_nodes(node_tree, seen=None):
    if node_tree is None:
        return
    seen = set() if seen is None else seen
    key = int(node_tree.as_pointer())
    if key in seen:
        return
    seen.add(key)
    for node in getattr(node_tree, "nodes", []):
        yield node
        if getattr(node, "type", "") == "GROUP":
            yield from _walk_nodes(getattr(node, "node_tree", None), seen)


def _redirect_file_outputs(scene) -> None:
    from bmanga_render_visual import command_runner

    node_out = OUT_DIR / "node_outputs"
    node_out.mkdir(parents=True, exist_ok=True)
    command_runner._set_output_folder(scene, str(node_out))


def _ensure_camera(scene) -> None:
    if scene.camera is not None:
        return
    cam_data = bpy.data.cameras.new("B-MANGA Render監査カメラ")
    cam = bpy.data.objects.new("B-MANGA Render監査カメラ", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0.0, -6.0, 4.0)
    cam.rotation_euler = (1.1, 0.0, 0.0)
    scene.camera = cam


def _required_scene_names(preset_library) -> tuple[set[str], set[str]]:
    view_layers = set()
    collections = {name for name, _color in PROXY_COLLECTIONS}
    for commands in preset_library.BUILTIN_PRESETS.values():
        for command in commands:
            kind = command.get("command_type", "")
            if kind == "SET_VIEW_LAYER":
                view_layers.add(command.get("view_layer_name", ""))
            elif kind == "SET_COLLECTION_EXCLUDE":
                collections.add(command.get("collection_name", ""))
    return {name for name in view_layers if name}, {name for name in collections if name}


def _create_proxy_scene(preset_library):
    view_layer_names, collection_names = _required_scene_names(preset_library)
    scene = bpy.data.scenes.new("BMangaRenderVisualProxy")
    default_layer = scene.view_layers[0]
    first_name = sorted(view_layer_names)[0] if view_layer_names else "レイアウト"
    default_layer.name = first_name
    for name in sorted(view_layer_names):
        if name != first_name and scene.view_layers.get(name) is None:
            scene.view_layers.new(name=name)
    for name in sorted(collection_names):
        if bpy.data.collections.get(name) is None:
            coll = bpy.data.collections.new(name)
        else:
            coll = bpy.data.collections[name]
        if coll.name not in scene.collection.children:
            try:
                scene.collection.children.link(coll)
            except RuntimeError:
                pass
    window = getattr(bpy.context, "window", None)
    if window is not None:
        window.scene = scene
    return scene


def _configure_scene(scene) -> None:
    _ensure_camera(scene)
    try:
        scene.use_nodes = False
    except Exception:
        pass
    scene.render.resolution_x = RESOLUTION
    scene.render.resolution_y = RESOLUTION
    scene.render.resolution_percentage = 100
    scene.render.use_border = False
    if hasattr(scene.render, "use_crop_to_border"):
        scene.render.use_crop_to_border = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    if hasattr(scene, "cycles"):
        scene.cycles.samples = SAMPLE_OVERRIDE
        if hasattr(scene.cycles, "use_denoising"):
            scene.cycles.use_denoising = False
    if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = SAMPLE_OVERRIDE
    for view_layer in scene.view_layers:
        if hasattr(view_layer, "use_pass_combined"):
            view_layer.use_pass_combined = True
    if scene.camera is not None and getattr(scene.camera, "type", "") == "CAMERA":
        from mathutils import Vector

        scene.camera.data.type = "PERSP"
        scene.camera.data.lens = 35.0
        scene.camera.location = (0.0, -13.0, 5.4)
        direction = Vector((0.0, 0.0, 0.0)) - scene.camera.location
        scene.camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _ensure_visual_proxies(scene) -> None:
    if bpy.data.objects.get("BMANGA_RENDER_AUDIT_PROXY_レイアウト") is not None:
        return
    light_data = bpy.data.lights.new("BMANGA_RENDER_AUDIT_LIGHT", "AREA")
    light = bpy.data.objects.new("BMANGA_RENDER_AUDIT_LIGHT", light_data)
    light.location = (0.0, -4.0, 8.0)
    light_data.energy = 450.0
    light_data.size = 5.0
    scene.collection.objects.link(light)
    for index, (collection_name, color) in enumerate(PROXY_COLLECTIONS):
        coll = bpy.data.collections.get(collection_name)
        if coll is None:
            continue
        mat = bpy.data.materials.new(f"BMANGA_RENDER_AUDIT_MAT_{collection_name}")
        mat.diffuse_color = color
        x = (index % 5 - 2) * 1.25
        y = (index // 5 - 1) * 1.05
        mesh = bpy.data.meshes.new(f"BMANGA_RENDER_AUDIT_MESH_{collection_name}")
        size = 0.46
        verts = [
            (-size, -size, -size),
            (size, -size, -size),
            (size, size, -size),
            (-size, size, -size),
            (-size, -size, size),
            (size, -size, size),
            (size, size, size),
            (-size, size, size),
        ]
        faces = [
            (0, 1, 2, 3),
            (4, 7, 6, 5),
            (0, 4, 5, 1),
            (1, 5, 6, 2),
            (2, 6, 7, 3),
            (3, 7, 4, 0),
        ]
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(f"BMANGA_RENDER_AUDIT_PROXY_{collection_name}", mesh)
        obj.location = (x, y, 0.0)
        obj.data.materials.append(mat)
        coll.objects.link(obj)
        text_curve = bpy.data.curves.new(f"BMANGA_RENDER_AUDIT_TEXT_{collection_name}", "FONT")
        text_curve.body = collection_name
        text_curve.align_x = "CENTER"
        text_curve.size = 0.18
        text = bpy.data.objects.new(f"BMANGA_RENDER_AUDIT_LABEL_{collection_name}", text_curve)
        text.location = (x, y, 0.62)
        text.rotation_euler = (1.25, 0.0, 0.0)
        text.data.materials.append(mat)
        coll.objects.link(text)


def _resolve_image_path(path: Path) -> Path | None:
    candidates = [path, path.with_suffix(".png")]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def _image_stats(path: Path) -> dict:
    try:
        _ensure_pillow_path()
        from PIL import Image, ImageStat
    except Exception:
        return {"opened": False, "blank": False}
    with Image.open(path) as image:
        converted = image.convert("RGB")
        stat = ImageStat.Stat(converted)
        extrema = converted.getextrema()
    blank = all(lo == hi for lo, hi in extrema)
    return {
        "opened": True,
        "size": list(converted.size),
        "mean": [round(v, 2) for v in stat.mean],
        "blank": bool(blank),
    }


def _render_snapshot(scene, command, index: int, preset_name: str, render_kind: str) -> dict:
    from bmanga_render_visual import command_runner

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in preset_name)
    path = OUT_DIR / "renders" / f"{index:03d}_{safe_name}_{render_kind}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(path)
    original_engine = str(getattr(command, "engine", "") or "CYCLES")
    engine = FORCE_ENGINE or original_engine
    if engine not in {"CYCLES", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH"}:
        engine = "BLENDER_WORKBENCH"
    samples = max(1, min(SAMPLE_OVERRIDE, int(getattr(command, "sample_count", SAMPLE_OVERRIDE) or 1)))
    command_runner._configure_render(scene, engine, samples)
    command_runner._ensure_renderable_view_layers(scene)
    if scene.render.engine == "CYCLES" and hasattr(scene, "cycles"):
        scene.cycles.samples = samples
    started = time.perf_counter()
    bpy.ops.render.render(write_still=False)
    render_result = bpy.data.images.get("Render Result")
    if render_result is not None:
        render_result.save_render(str(path), scene=scene)
    elapsed = time.perf_counter() - started
    actual = _resolve_image_path(path)
    if actual is None:
        raise RuntimeError(f"レンダー画像が作成されませんでした: {path}")
    stats = _image_stats(actual)
    return {
        "kind": render_kind,
        "path": str(actual),
        "engine": scene.render.engine,
        "preset_engine": original_engine,
        "render_mode": RENDER_MODE,
        "samples": samples,
        "seconds": round(elapsed, 3),
        **stats,
    }


def _run_render_command(scene, command, preset_name: str, render_index: int) -> dict:
    from bmanga_render_visual import command_runner

    kind = command.command_type
    if kind == "RENDER":
        return _render_snapshot(scene, command, render_index, preset_name, "render")
    if kind == "RENDER_LAYER":
        command_runner._set_output_group(command.node_group_name, "", True)
        command_runner._set_output_group(command.node_group_name, command.label_contains, False)
        return _render_snapshot(scene, command, render_index, preset_name, "layer")
    if kind in {
        "FISHEYE_RENDER_IMAGE_OR_LAYER",
        "FISHEYE_RENDER_FACES_OR_LAYER",
        "FISHEYE_ASSEMBLE_OR_LAYER",
    }:
        if bool(getattr(scene, "fisheye_layout_mode", False)):
            if not command_runner.eevr_bridge.setup(scene, getattr(scene, "camera", None)):
                raise RuntimeError("eeVRアドオン未登録のため魚眼レンダーは実行不可")
        command_runner._set_output_group(command.node_group_name, "", True)
        command_runner._set_output_group(command.node_group_name, command.label_contains, False)
        return _render_snapshot(scene, command, render_index, preset_name, "fisheye_or_layer")
    raise AssertionError(kind)


def _run_one_preset(context, preset, preset_index: int) -> dict:
    from bmanga_render_visual import command_runner

    scene = context.scene
    _ensure_visual_proxies(scene)
    renders = []
    errors = []
    command_count = 0
    render_index = 0
    try:
        for command in preset.commands:
            if not command.enabled:
                continue
            command_count += 1
            _redirect_file_outputs(scene)
            if command.command_type in {
                "RENDER",
                "RENDER_LAYER",
                "FISHEYE_RENDER_IMAGE_OR_LAYER",
                "FISHEYE_RENDER_FACES_OR_LAYER",
                "FISHEYE_ASSEMBLE_OR_LAYER",
            }:
                render_index += 1
                try:
                    renders.append(_run_render_command(scene, command, f"{preset_index:02d}_{preset.name}", render_index))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{command.name}: {exc}")
            else:
                try:
                    command_runner._run_command(context, command)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{command.name}: {exc}")
    finally:
        command_runner._restore_session(scene)
    return {
        "preset": preset.name,
        "command_count": command_count,
        "render_count": len(renders),
        "renders": renders,
        "errors": errors,
    }


def _check_ui_toggles(context) -> list[dict]:
    from bmanga_render_visual import core

    scene = context.scene
    state = core.get_state(context)
    preset = core.active_preset(context)
    command = preset.commands[0] if preset is not None and preset.commands else None
    checks = []

    def flip(label, target, attr):
        before = bool(getattr(target, attr))
        setattr(target, attr, not before)
        after = bool(getattr(target, attr))
        setattr(target, attr, before)
        checks.append({"label": label, "before": before, "after": after, "ok": after is (not before)})

    flip("魚眼出力 / 魚眼モード", scene, "fisheye_layout_mode")
    flip("魚眼出力 / 縮小モード", scene, "reduction_mode")
    if state is not None:
        flip("B-MANGA Render / 出力完了時アラーム再生", state, "sound_enabled")
    if command is not None:
        flip("カード / 有効", command, "enabled")
        if hasattr(command, "view_layer_enabled"):
            flip("カード / ビューレイヤー有効化", command, "view_layer_enabled")
        if hasattr(command, "exclude_collection"):
            flip("カード / コレクション除外", command, "exclude_collection")
        if hasattr(command, "mute"):
            flip("カード / ミュート", command, "mute")
    return checks


def _write_contact_sheet(results: list[dict], toggle_checks: list[dict]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "b_manga_render_visual_presets.json"
    json_path.write_text(
        json.dumps({"toggles": toggle_checks, "presets": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        _ensure_pillow_path()
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return json_path

    thumb_w = 96
    thumb_h = 96
    row_h = 118
    width = 1640
    height = 130 + row_h * max(1, len(results)) + 28 * max(1, len(toggle_checks))
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    title_font = _font(ImageFont, size=18)
    font = _font(ImageFont, size=12)
    draw.text((24, 18), "B-MANGA Render 全出力プリセット 実レンダー AI目視シート", fill=(0, 0, 0), font=title_font)
    ok_presets = sum(1 for item in results if not item["errors"])
    draw.text(
        (24, 50),
        f"プリセット: {len(results)} / エラーなし: {ok_presets} / 解像度: {RESOLUTION}px / サンプル: {SAMPLE_OVERRIDE}",
        fill=(0, 0, 0),
        font=font,
    )
    y = 84
    draw.text((24, y), "UIチェック", fill=(0, 0, 0), font=font)
    y += 24
    for check in toggle_checks:
        fill = (236, 249, 236) if check["ok"] else (255, 234, 234)
        draw.rectangle((24, y, 700, y + 22), fill=fill, outline=(170, 190, 170))
        draw.text((34, y + 5), "OK" if check["ok"] else "NG", fill=(0, 120, 0) if check["ok"] else (180, 0, 0), font=font)
        draw.text((84, y + 5), f"{check['label']}  {check['before']} -> {check['after']}", fill=(0, 0, 0), font=font)
        y += 28
    y += 12
    for index, item in enumerate(results):
        has_error = bool(item["errors"])
        fill = (236, 249, 236) if not has_error else (255, 238, 224)
        draw.rectangle((20, y, width - 20, y + row_h - 8), fill=fill, outline=(180, 190, 180))
        draw.text((34, y + 10), f"{index + 1:02d}. {item['preset']}", fill=(0, 0, 0), font=font)
        draw.text((34, y + 32), f"カード {item['command_count']} / レンダー {item['render_count']}", fill=(0, 0, 0), font=font)
        if item["errors"]:
            draw.text((34, y + 54), " / ".join(item["errors"])[:130], fill=(150, 50, 0), font=font)
        if not item["renders"]:
            draw.text((360, y + 42), "レンダーなし", fill=(60, 60, 60), font=font)
        for thumb_index, render in enumerate(item["renders"][:10]):
            x = 360 + thumb_index * (thumb_w + 18)
            try:
                with Image.open(render["path"]) as img:
                    thumb = img.convert("RGB")
                    thumb.thumbnail((thumb_w, thumb_h))
                    sheet.paste(thumb, (x, y + 10))
            except Exception:
                draw.rectangle((x, y + 10, x + thumb_w, y + 10 + thumb_h), outline=(180, 0, 0))
                draw.text((x + 5, y + 45), "画像不可", fill=(180, 0, 0), font=font)
            label = f"{thumb_index + 1}:{render.get('engine', '')}"
            if render.get("blank"):
                label += " blank"
            draw.text((x, y + 10 + thumb_h), label[:18], fill=(0, 0, 0), font=font)
        y += row_h
    image_path = OUT_DIR / "b_manga_render_visual_presets.png"
    sheet.save(image_path)
    return image_path


def main() -> None:
    blend_path = Path(os.environ.get("BMANGA_C00_BLEND", str(DEFAULT_BLEND)))
    if not blend_path.exists():
        raise FileNotFoundError(blend_path)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=str(blend_path))
    render = _load_render_package()
    try:
        render.command_runner._reload_images = lambda: 0
        proxy_scene = _create_proxy_scene(render.preset_library)
        _configure_scene(proxy_scene)
        _ensure_visual_proxies(proxy_scene)
        bpy.ops.bmanga_render.load_builtin_presets(reset=True)
        state = proxy_scene.bmanga_render_state
        toggle_checks = _check_ui_toggles(bpy.context)
        results = []
        for index, preset in enumerate(state.presets):
            state.active_preset_index = index
            _configure_scene(proxy_scene)
            proxy_scene.fisheye_layout_mode = False
            results.append(_run_one_preset(bpy.context, preset, index + 1))
        sheet = _write_contact_sheet(results, toggle_checks)
        errors = {item["preset"]: item["errors"] for item in results if item["errors"]}
        print(f"BMANGA_RENDER_VISUAL_PRESETS_DONE visual={sheet} presets={len(results)} errors={len(errors)}")
        assert not errors, errors
        assert all(check["ok"] for check in toggle_checks), toggle_checks
    finally:
        try:
            render.unregister()
        except Exception:
            pass
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
