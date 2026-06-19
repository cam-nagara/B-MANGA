"""Command execution engine for B-MANGA Render cards."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import sys

import bpy

from . import batch_log, bmanga_context, core, eevr_bridge


@dataclass
class _RenderSession:
    film_transparent: bool = False
    engine: str = ""
    view_layers: dict[str, bool] = field(default_factory=dict)
    collection_excludes: list[tuple[object, bool]] = field(default_factory=list)
    node_mutes: list[tuple[object, bool]] = field(default_factory=list)
    output_nodes: list[tuple[object, str, str]] = field(default_factory=list)


_SESSION: _RenderSession | None = None

# 親プリセット (プリセット実行) のための実行中プリセット名スタック。
# 深さ1固定 (親→子の1段のみ)。循環・自己参照・多段ネストの防止に使う。
_PRESET_RUN_STACK: list[str] = []

# 「プリセットを実行」完了時に報告する実行コマンド数。子プリセット
# (プリセット実行で呼んだもの) の中身も合算する。プリセット実行コマンド
# 自体 (ディスパッチャ) は数えず、実際に動いたコマンドだけを数える。
_EXEC_COUNT = 0


def _iter_node_trees(scene):
    seen: set[int] = set()
    for attr in ("node_tree", "compositing_node_group"):
        tree = getattr(scene, attr, None)
        if tree is None:
            continue
        key = _node_tree_key(tree)
        if key not in seen:
            seen.add(key)
            yield tree
    for node_group in bpy.data.node_groups:
        if node_group is not None and _node_tree_key(node_group) not in seen:
            seen.add(_node_tree_key(node_group))
            yield node_group


def _node_tree_key(node_tree) -> int:
    try:
        return int(node_tree.as_pointer())
    except (AttributeError, ReferenceError, TypeError, ValueError):
        return id(node_tree)


def _iter_nodes_recursive(node_tree, seen=None):
    if node_tree is None:
        return
    seen = set() if seen is None else seen
    key = _node_tree_key(node_tree)
    if key in seen:
        return
    seen.add(key)
    for node in getattr(node_tree, "nodes", []):
        yield node
        if getattr(node, "type", "") == "GROUP":
            yield from _iter_nodes_recursive(getattr(node, "node_tree", None), seen)


def _iter_all_nodes(scene):
    seen: set[int] = set()
    for tree in _iter_node_trees(scene):
        yield from _iter_nodes_recursive(tree, seen)


def _begin_session(scene) -> None:
    global _SESSION
    if _SESSION is not None:
        _restore_session(scene)
    session = _RenderSession()
    session.film_transparent = bool(getattr(scene.render, "film_transparent", False))
    session.engine = str(getattr(scene.render, "engine", ""))
    for layer in scene.view_layers:
        if hasattr(layer, "use"):
            session.view_layers[layer.name] = bool(layer.use)
            layer.use = False
        layer_collection = getattr(layer, "layer_collection", None)
        if layer_collection is not None:
            for item in _iter_layer_collections(layer_collection):
                if hasattr(item, "exclude"):
                    session.collection_excludes.append((item, bool(item.exclude)))
    for node in _iter_all_nodes(scene):
        if hasattr(node, "mute"):
            session.node_mutes.append((node, bool(node.mute)))
            if getattr(node, "type", "") == "OUTPUT_FILE":
                node.mute = True
        if getattr(node, "type", "") == "OUTPUT_FILE":
            session.output_nodes.append(
                (
                    node,
                    str(_get_output_node_directory(node)),
                    str(_get_output_node_file_name(node)),
                )
            )
    scene.render.film_transparent = True
    _SESSION = session


def _restore_session(scene) -> None:
    global _SESSION
    session = _SESSION
    if session is None:
        return
    scene.render.film_transparent = session.film_transparent
    if session.engine:
        scene.render.engine = session.engine
    for layer in scene.view_layers:
        if hasattr(layer, "use") and layer.name in session.view_layers:
            layer.use = session.view_layers[layer.name]
    for layer_collection, exclude in session.collection_excludes:
        try:
            layer_collection.exclude = exclude
        except ReferenceError:
            pass
    for node, mute in session.node_mutes:
        try:
            node.mute = mute
        except ReferenceError:
            pass
    for node, directory, file_name in session.output_nodes:
        try:
            _set_output_node_directory(node, directory)
            _set_output_node_file_name(node, file_name)
        except ReferenceError:
            pass
    _SESSION = None


def _set_view_layer(scene, name: str, enabled: bool) -> None:
    layer = scene.view_layers.get(name)
    if layer is not None and hasattr(layer, "use"):
        layer.use = enabled


def _find_layer_collection(layer_collection, collection_name: str):
    if getattr(layer_collection.collection, "name", "") == collection_name:
        return layer_collection
    for child in layer_collection.children:
        found = _find_layer_collection(child, collection_name)
        if found is not None:
            return found
    return None


def _iter_layer_collections(layer_collection):
    yield layer_collection
    for child in getattr(layer_collection, "children", []):
        yield from _iter_layer_collections(child)


def _set_collection_exclude(scene, collection_name: str, exclude: bool, view_layer_name: str = "") -> None:
    view_layers = [scene.view_layers.get(view_layer_name)] if view_layer_name else scene.view_layers
    for view_layer in view_layers:
        if view_layer is None:
            continue
        layer_coll = _find_layer_collection(view_layer.layer_collection, collection_name)
        if layer_coll is not None:
            layer_coll.exclude = exclude


def _set_node_mute(scene, node_name: str, mute: bool) -> int:
    count = 0
    for node in _iter_all_nodes(scene):
        if getattr(node, "name", "") == node_name or getattr(node, "label", "") == node_name:
            if hasattr(node, "mute"):
                node.mute = mute
                count += 1
    return count


def _node_matches_label(node, label: str) -> bool:
    if not label:
        return True
    values = _output_match_values(node)
    return any(label in str(value) for value in values)


def _output_match_values(node) -> tuple[str, ...]:
    parent = getattr(node, "parent", None)
    values: list[str] = [
        str(getattr(node, "name", "") or ""),
        str(getattr(node, "label", "") or ""),
        str(getattr(parent, "name", "") or "") if parent is not None else "",
        str(getattr(parent, "label", "") or "") if parent is not None else "",
    ]
    for socket in getattr(node, "inputs", []) or []:
        values.append(str(getattr(socket, "name", "") or ""))
    for collection_name in ("file_output_items", "file_slots", "layer_slots"):
        for item in getattr(node, collection_name, []) or []:
            values.append(str(getattr(item, "name", "") or ""))
            values.append(str(getattr(item, "path", "") or ""))
            values.append(str(getattr(item, "file_name", "") or ""))
    return tuple(values)


def _set_output_group(group_name: str, label: str, mute: bool) -> int:
    group = bpy.data.node_groups.get(group_name)
    if group is None:
        return 0
    count = 0
    for node in _iter_nodes_recursive(group):
        if getattr(node, "type", "") == "OUTPUT_FILE" and _node_matches_label(node, label):
            node.mute = mute
            count += 1
    return count


def _set_input_in_node_tree(node_tree, input_name: str, value: float) -> int:
    count = 0
    for node in _iter_nodes_recursive(node_tree):
        for socket in getattr(node, "inputs", []):
            if getattr(socket, "name", "") == input_name and hasattr(socket, "default_value"):
                if _set_socket_value(socket, value):
                    count += 1
    return count


def _set_socket_value(socket, value: float) -> bool:
    current = getattr(socket, "default_value", None)
    candidates = []
    if isinstance(current, bool):
        candidates.append(bool(round(float(value))))
    elif isinstance(current, int):
        candidates.append(int(round(float(value))))
    elif isinstance(current, float):
        candidates.append(float(value))
    else:
        candidates.append(value)
    candidates.extend((int(round(float(value))), float(value), bool(round(float(value)))))
    for candidate in candidates:
        try:
            socket.default_value = candidate
            return True
        except (TypeError, ValueError):
            continue
    return False


def _set_aov_input(target_name: str, input_name: str, value: float) -> int:
    collection = bpy.data.collections.get(target_name)
    if collection is not None:
        count = 0
        for obj in collection.all_objects:
            if getattr(obj, "type", "") != "MESH":
                continue
            for slot in getattr(obj, "material_slots", []):
                material = getattr(slot, "material", None)
                if material is None or not getattr(material, "use_nodes", False):
                    continue
                count += _set_input_in_node_tree(material.node_tree, input_name, value)
        return count

    count = 0
    for group in bpy.data.node_groups:
        if target_name and target_name not in group.name:
            continue
        count += _set_input_in_node_tree(group, input_name, value)
    return count


def _set_output_name(scene, name: str) -> None:
    name = bmanga_context.default_output_name(scene, name)
    scene.render.filepath = name
    for node in _iter_all_nodes(scene):
        if getattr(node, "type", "") != "OUTPUT_FILE":
            continue
        _set_output_node_file_name(node, name)


def _set_output_folder(scene, folder: str) -> None:
    folder = bmanga_context.default_output_folder(scene, folder)
    scene["bmanga_render_output_folder"] = folder
    for node in _iter_all_nodes(scene):
        if getattr(node, "type", "") == "OUTPUT_FILE":
            _set_output_node_directory(node, folder)


def _get_output_node_directory(node) -> str:
    if hasattr(node, "directory"):
        return str(getattr(node, "directory", "") or "")
    return str(getattr(node, "base_path", "") or "")


def _set_output_node_directory(node, folder: str) -> None:
    if hasattr(node, "directory"):
        node.directory = folder
    if hasattr(node, "base_path"):
        node.base_path = folder


def _get_output_node_file_name(node) -> str:
    if hasattr(node, "file_name"):
        return str(getattr(node, "file_name", "") or "")
    slots = list(getattr(node, "file_slots", []) or [])
    if slots:
        return str(getattr(slots[0], "path", "") or "")
    return ""


def _set_output_node_file_name(node, name: str) -> None:
    if hasattr(node, "file_name"):
        node.file_name = name
    for collection_name in ("file_slots", "layer_slots"):
        for slot in getattr(node, collection_name, []) or []:
            if hasattr(slot, "path"):
                slot.path = name


_IMAGE_VARIANT_PATTERN = re.compile(r"(?:_?(image|assembled|front|right|back|left|top|bottom))$", re.IGNORECASE)
_IMAGE_VARIANT_PRIORITY = {
    "": 0,
    "assembled": 1,
    "image": 2,
    "front": 3,
    "right": 3,
    "back": 3,
    "left": 3,
    "top": 3,
    "bottom": 3,
}


def _image_stem(value: str) -> str:
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    if ".png" in name.lower():
        return re.split(r"\.png", name, flags=re.IGNORECASE)[0]
    return Path(name).stem


def _image_variant(value: str) -> str:
    match = _IMAGE_VARIANT_PATTERN.search(_image_stem(value))
    return match.group(1).lower() if match else ""


def _normalized_image_key(value: str, *, strip_variant: bool = True) -> str:
    name = _image_stem(value)
    name = re.sub(r"^c\d{2,}_", "", name, flags=re.IGNORECASE)
    if strip_variant:
        name = _IMAGE_VARIANT_PATTERN.sub("", name)
    name = re.sub(r"0{3,5}_?$", "", name)
    name = re.sub(r"[\s_]+", "", name)
    return name.lower()


def _prefer_image_path(current: Path | None, candidate: Path) -> Path:
    if current is None:
        return candidate
    current_rank = (_IMAGE_VARIANT_PRIORITY.get(_image_variant(current.name), 9), str(current))
    candidate_rank = (_IMAGE_VARIANT_PRIORITY.get(_image_variant(candidate.name), 9), str(candidate))
    return candidate if candidate_rank < current_rank else current


def _iter_png_files(*folders: str) -> tuple[dict[str, Path], dict[str, Path]]:
    exact: dict[str, Path] = {}
    loose: dict[str, Path] = {}
    for folder in folders:
        if not folder:
            continue
        directory = Path(bpy.path.abspath(str(folder))).resolve()
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.png")):
            exact_key = _normalized_image_key(path.name, strip_variant=False)
            loose_key = _normalized_image_key(path.name, strip_variant=True)
            if exact_key and exact_key not in exact:
                exact[exact_key] = path
            if loose_key:
                loose[loose_key] = _prefer_image_path(loose.get(loose_key), path)
    return exact, loose


def _image_node_keys(node, *, strip_variant: bool = True, include_image: bool = True) -> list[str]:
    image = getattr(node, "image", None)
    values = [
        str(getattr(node, "name", "") or ""),
        str(getattr(node, "label", "") or ""),
    ]
    if include_image and image is not None:
        values.extend(
            [
                str(getattr(image, "name", "") or ""),
                str(getattr(image, "filepath", "") or ""),
            ]
        )
    keys = []
    seen = set()
    for value in values:
        key = _normalized_image_key(value, strip_variant=strip_variant)
        if key and key not in seen:
            keys.append(key)
            seen.add(key)
    return keys


def _assign_image_node(node, path: Path) -> bool:
    image = getattr(node, "image", None)
    try:
        if image is None:
            image = bpy.data.images.load(str(path), check_existing=True)
            node.image = image
        else:
            image.filepath = str(path)
        image.reload()
        return True
    except Exception:  # noqa: BLE001
        return False


def _sync_generated_images(scene) -> tuple[int, int]:
    output_folder = str(scene.get("bmanga_render_output_folder", "") or "")
    fisheye_folder = str(scene.get("bmanga_render_fisheye_output_dir", "") or "")
    default_folder = bmanga_context.default_output_folder(scene, "")
    exact_png_by_key, loose_png_by_key = _iter_png_files(output_folder, fisheye_folder, default_folder)
    matched = 0
    for node in _iter_all_nodes(scene):
        if getattr(node, "type", "") != "IMAGE":
            continue
        key_groups = (
            (exact_png_by_key, _image_node_keys(node, strip_variant=False, include_image=False)),
            (loose_png_by_key, _image_node_keys(node, strip_variant=True, include_image=False)),
            (exact_png_by_key, _image_node_keys(node, strip_variant=False, include_image=True)),
            (loose_png_by_key, _image_node_keys(node, strip_variant=True, include_image=True)),
        )
        for png_by_key, keys in key_groups:
            path = next((png_by_key[key] for key in keys if key in png_by_key), None)
            if path is not None and _assign_image_node(node, path):
                matched += 1
                break
    scene["bmanga_render_reload_candidate_count"] = len(exact_png_by_key)
    scene["bmanga_render_reload_match_count"] = matched
    return matched, len(exact_png_by_key)


def _reload_images() -> int:
    scene = bpy.context.scene
    _sync_generated_images(scene)
    count = 0
    for image in bpy.data.images:
        try:
            image.reload()
            count += 1
        except Exception:  # noqa: BLE001
            pass
    scene["bmanga_render_reload_image_count"] = count
    return count


def _resolve_engine_identifier(engine: str) -> str:
    """保存値のエンジン識別子を、起動中の Blender が実際に持つ識別子へ読み替える。

    EEVEE の識別子は Blender 版で揺れる:
      4.2〜: ``BLENDER_EEVEE_NEXT``（EEVEE Next 導入で旧 EEVEE を置換）
      5.x〜: ``BLENDER_EEVEE``（EEVEE Next が正式版に昇格し接尾辞を廃止）
    既存プリセットは ``BLENDER_EEVEE_NEXT`` を保存しているため、scene へ代入する
    前に実機の有効値へ変換する。該当しない/判定不能ならそのまま返す（無害）。
    """
    try:
        available = {
            item.identifier
            for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
        }
    except Exception:  # noqa: BLE001
        return engine
    if not available or engine in available:
        return engine
    eevee_aliases = ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT")
    if engine in eevee_aliases:
        for alias in eevee_aliases:
            if alias in available:
                return alias
    return engine


def _configure_render(scene, engine: str, sample_count: int) -> None:
    engine = _resolve_engine_identifier(engine)
    scene.render.engine = engine
    if engine == "CYCLES" and hasattr(scene, "cycles"):
        scene.cycles.samples = max(1, int(sample_count))
    elif engine in {"BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"} and hasattr(scene, "eevee"):
        if hasattr(scene.eevee, "taa_render_samples"):
            scene.eevee.taa_render_samples = max(1, int(sample_count))


def _ensure_renderable_view_layers(scene) -> None:
    layers = [layer for layer in scene.view_layers if hasattr(layer, "use")]
    if layers and not any(bool(layer.use) for layer in layers):
        for layer in layers:
            layer.use = True


def _output_directories(scene) -> list[str]:
    """そのシーンが出力に使い得るフォルダ一覧（計測の生成ファイル検出用）。

    実ファイルの更新時刻で「このレンダーで出た出力」を判定するため、
    走査対象のフォルダを集める。env 無効時は計測側が呼ばないので軽量。
    """
    dirs: list[str] = []

    def _add(path: str) -> None:
        if not path:
            return
        try:
            abspath = str(Path(bpy.path.abspath(str(path))).resolve())
        except Exception:  # noqa: BLE001
            return
        if abspath not in dirs:
            dirs.append(abspath)

    try:
        filepath = str(getattr(scene.render, "filepath", "") or "")
        if filepath:
            _add(str(Path(bpy.path.abspath(filepath)).parent))
    except Exception:  # noqa: BLE001
        pass
    _add(str(scene.get("bmanga_render_output_folder", "") or ""))
    _add(str(scene.get("bmanga_render_fisheye_output_dir", "") or ""))
    try:
        _add(bmanga_context.default_output_folder(scene, ""))
    except Exception:  # noqa: BLE001
        pass
    for node in _iter_all_nodes(scene):
        if getattr(node, "type", "") == "OUTPUT_FILE":
            _add(_get_output_node_directory(node))
    return dirs


def _batch_output_dirs(scene) -> list[str]:
    """計測が有効な時だけ出力フォルダ一覧を集める（無効時は走査しない）。

    通常UI操作(env 未設定)では走査・パス解決を一切行わず空を返す。計測フックの
    引数評価が通常レンダーにコストを乗せないための薄いラッパ。
    """
    return _output_directories(scene) if batch_log.is_enabled() else []


def _render(scene, engine: str, sample_count: int) -> None:
    _configure_render(scene, engine, sample_count)
    _ensure_renderable_view_layers(scene)
    bpy.ops.render.render()


def _is_fisheye_enabled(scene) -> bool:
    return core.fisheye_enabled(scene)


def _render_layer(scene, group_name: str, label: str, engine: str, sample_count: int) -> None:
    _set_output_group(group_name, "", True)
    _set_output_group(group_name, label, False)
    _render(scene, engine, sample_count)


def _run_fisheye_or_layer(scene, command, mode: str, preset_name: str = "") -> None:
    if not _is_fisheye_enabled(scene):
        _render_layer(scene, command.node_group_name, command.label_contains, command.engine, command.sample_count)
        return
    _setup_eevr_from_command(scene, command, preset_name)
    if mode == "IMAGE":
        eevr_bridge.render_image()
    elif mode == "FACES":
        eevr_bridge.render_faces()
    elif mode == "ASSEMBLE":
        eevr_bridge.assemble_images()


def _setup_eevr_from_command(scene, command, preset_name: str = "") -> None:
    if not eevr_bridge.setup(
        scene,
        getattr(scene, "camera", None),
        output_dir=bmanga_context.default_output_folder(scene, str(getattr(command, "folder_path", "") or "")),
        output_name=bmanga_context.default_output_name(scene, str(getattr(command, "text_value", "") or ""), preset_name),
    ):
        raise RuntimeError("魚眼設定を現在のカメラに合わせられません")


def _run_command(context, command, preset_name: str = "") -> None:
    global _EXEC_COUNT
    scene = context.scene
    kind = command.command_type
    if kind != "RUN_PRESET":
        # プリセット実行ディスパッチャは数えず、実際に動くコマンドを数える。
        _EXEC_COUNT += 1
    if kind == "STATE_BEGIN":
        _begin_session(scene)
    elif kind == "STATE_END":
        _restore_session(scene)
    elif kind == "SET_VIEW_LAYER":
        _set_view_layer(scene, command.view_layer_name, command.view_layer_enabled)
    elif kind == "SET_COLLECTION_EXCLUDE":
        _set_collection_exclude(scene, command.collection_name, command.exclude_collection, command.view_layer_name)
    elif kind == "SET_NODE_MUTE":
        _set_node_mute(scene, command.node_name, command.mute)
    elif kind == "SET_OUTPUT_GROUP":
        _set_output_group(command.node_group_name, command.label_contains, command.mute)
    elif kind == "SET_AOV_INPUT":
        _set_aov_input(command.node_group_name, command.input_name, command.float_value)
    elif kind == "SET_OUTPUT_NAME":
        _set_output_name(scene, command.text_value)
    elif kind == "SET_OUTPUT_FOLDER":
        _set_output_folder(scene, command.folder_path)
    elif kind == "RELOAD_IMAGES":
        _reload_images()
    elif kind == "RENDER":
        with batch_log.render_timer(scene, command.label_contains or "レンダー", command.engine, command.sample_count, _batch_output_dirs(scene)):
            _render(scene, command.engine, command.sample_count)
    elif kind == "RENDER_LAYER":
        with batch_log.render_timer(scene, command.label_contains, command.engine, command.sample_count, _batch_output_dirs(scene)):
            _render_layer(scene, command.node_group_name, command.label_contains, command.engine, command.sample_count)
    elif kind == "FISHEYE_RENDER_IMAGE_OR_LAYER":
        with batch_log.render_timer(scene, command.label_contains or "魚眼画像", command.engine, command.sample_count, _batch_output_dirs(scene)):
            _run_fisheye_or_layer(scene, command, "IMAGE", preset_name)
    elif kind == "FISHEYE_RENDER_FACES_OR_LAYER":
        with batch_log.render_timer(scene, command.label_contains or "魚眼各面", command.engine, command.sample_count, _batch_output_dirs(scene)):
            _run_fisheye_or_layer(scene, command, "FACES", preset_name)
    elif kind == "FISHEYE_ASSEMBLE_OR_LAYER":
        with batch_log.render_timer(scene, command.label_contains or "魚眼合成", command.engine, command.sample_count, _batch_output_dirs(scene)):
            _run_fisheye_or_layer(scene, command, "ASSEMBLE", preset_name)
    elif kind == "EEVR_SETUP":
        _setup_eevr_from_command(scene, command, preset_name)
    elif kind == "EEVR_RENDER_IMAGE":
        _setup_eevr_from_command(scene, command, preset_name)
        with batch_log.render_timer(scene, command.label_contains or "魚眼画像", command.engine, command.sample_count, _batch_output_dirs(scene)):
            eevr_bridge.render_image()
    elif kind == "EEVR_RENDER_FACES":
        _setup_eevr_from_command(scene, command, preset_name)
        with batch_log.render_timer(scene, command.label_contains or "魚眼各面", command.engine, command.sample_count, _batch_output_dirs(scene)):
            eevr_bridge.render_faces()
    elif kind == "EEVR_ASSEMBLE":
        _setup_eevr_from_command(scene, command, preset_name)
        with batch_log.render_timer(scene, command.label_contains or "魚眼合成", command.engine, command.sample_count, _batch_output_dirs(scene)):
            eevr_bridge.assemble_images()
    elif kind == "OPERATOR" and command.operator_idname:
        eevr_bridge.run_operator(command.operator_idname)
    elif kind == "RUN_PRESET":
        _run_child_preset(context, str(getattr(command, "target_preset_name", "") or ""))


def _run_child_preset(context, target_name: str) -> None:
    """親プリセットから子プリセットを順番に実行する (深さ1固定).

    退避/復元は子プリセットが自前で行う前提。親は子を並べるだけ。
    多段ネスト・循環・自己参照は実行せず警告ログを出す。
    """
    name = str(target_name or "").strip()
    if not name:
        return
    if len(_PRESET_RUN_STACK) >= 2:
        print(f"[B-MANGA Render] 多段のプリセット実行は無視します: {name}")
        return
    if name in _PRESET_RUN_STACK:
        print(f"[B-MANGA Render] 循環/自己参照のプリセット実行を回避: {name}")
        return
    state = core.get_state(context)
    if state is None:
        return
    child = next((p for p in state.presets if p.name == name), None)
    if child is None:
        print(f"[B-MANGA Render] プリセットが見つかりません: {name}")
        return
    _PRESET_RUN_STACK.append(name)
    try:
        for command in child.commands:
            if getattr(command, "enabled", False):
                _run_command(context, command, child.name)
    finally:
        _PRESET_RUN_STACK.pop()


def _sync_bmanga_coma_output_layout(context) -> None:
    scene = getattr(context, "scene", None) if context is not None else None
    if scene is None:
        return
    for module in tuple(sys.modules.values()):
        name = str(getattr(module, "__name__", "") or "")
        if not name.endswith(".utils.coma_camera"):
            continue
        func = getattr(module, "resync_coma_camera_output_layout", None)
        if callable(func):
            func(context)
            return
        func = getattr(module, "update_render_border_from_current_coma", None)
        if callable(func):
            func(context)
            return


def run_active_preset(context) -> int:
    preset = core.active_preset(context)
    if preset is None:
        return 0
    global _EXEC_COUNT
    _EXEC_COUNT = 0
    scene = context.scene
    blend_path = str(getattr(bpy.data, "filepath", "") or "")
    batch_log.begin_preset(scene, str(getattr(preset, "name", "") or ""), blend_path)
    error_text = ""
    ok = False
    try:
        core._apply_output_resolution_mode(scene)
        _sync_bmanga_coma_output_layout(context)
        _PRESET_RUN_STACK.clear()
        _PRESET_RUN_STACK.append(str(getattr(preset, "name", "") or ""))
        try:
            for command in preset.commands:
                if not command.enabled:
                    continue
                _run_command(context, command, preset.name)
        finally:
            _PRESET_RUN_STACK.clear()
        ok = True
    except Exception as exc:  # noqa: BLE001
        error_text = str(exc)
        raise
    finally:
        _restore_session(scene)
        batch_log.set_exec_count(_EXEC_COUNT)
        batch_log.finalize(scene, ok=ok, error=error_text)
    return _EXEC_COUNT


def run_preset_by_name(context, name: str) -> int:
    """名前を指定してプリセットを実行する（連続実行アプリ向けの入口）。

    アクティブプリセットを一時的に指定名へ切り替えてから実行する。
    見つからない場合は RuntimeError。
    """
    target = str(name or "").strip()
    state = core.get_state(context)
    if state is None or not state.presets:
        raise RuntimeError("プリセットがありません")
    idx = next((i for i, p in enumerate(state.presets) if str(p.name) == target), -1)
    if idx < 0:
        available = ", ".join(str(p.name) for p in state.presets)
        raise RuntimeError(f"プリセットが見つかりません: {target}（候補: {available}）")
    core.set_active_preset_index(context, idx)
    return run_active_preset(context)
