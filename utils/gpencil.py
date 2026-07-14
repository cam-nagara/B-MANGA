"""Grease Pencil v3 ヘルパ.

計画書 10 章参照. v3 API のみ使用 (v2 は使わない)。

**Blender バージョン間の API 差異**:
- Blender 4.3〜4.x: ``bpy.data.grease_pencils_v3`` (v2 と並存していた時期)
- Blender 5.x: ``bpy.data.grease_pencils`` (v3 が既定化、サフィックス撤去)

両方を自動検出して同じ方法で扱えるよう ``_gp_data_blocks()`` でラップ。

現行モデルは「1 GP Object = 1 B-MANGA 手描きレイヤー」。各Object内には
実描画用の ``content`` レイヤーだけを置き、ページ／コマ／汎用フォルダーへの
所属と一覧上の並びはObjectの安定IDと管理メタデータで扱う。
"""

from __future__ import annotations

import math
from typing import Iterable

import bpy

from ..utils import log
from .gp_material_isolation import MATERIAL_OWNER_PROP
from .gp_material_isolation import ensure_unique_object_materials
from .gp_material_isolation import material_owner_id

_logger = log.get_logger(__name__)


def _gp_data_blocks():
    """v3 GreasePencil データブロックコレクションを返す.

    Blender 5.x は ``bpy.data.grease_pencils``、4.3〜4.x は
    ``bpy.data.grease_pencils_v3`` を公開する。どちらか存在するものを返す。
    どちらも無い場合は RuntimeError (v2 のみの古い Blender では動作しない)。
    """
    coll = getattr(bpy.data, "grease_pencils_v3", None)
    if coll is not None:
        return coll
    coll = getattr(bpy.data, "grease_pencils", None)
    if coll is not None:
        return coll
    raise RuntimeError(
        "Grease Pencil v3 data-blocks not available (requires Blender 4.3+)"
    )


# ---------- 命名規則 ----------

ROOT_COLLECTION_NAME = "B-MANGA"


def page_collection_name(page_id: str) -> str:
    """ページ Collection 名 = page_id 直接.

    旧仕様では ``page_{id}`` (例: ``page_p0001``) を使っていたが、Outliner
    mirror 統一により ``p0001`` のシンプル名に統合 (2026-04-30)。
    """
    return str(page_id) if page_id else ""


# ---------- GP v3 低レベル ----------


def ensure_gpencil(name: str):
    """名前つき GreasePencil v3 データブロックを取得/生成."""
    blocks = _gp_data_blocks()
    gp_data = blocks.get(name)
    if gp_data is None:
        gp_data = blocks.new(name)
    return gp_data


def ensure_gpencil_object(name: str, link_to_collection=True):
    """v3 GreasePencil Object を取得/生成."""
    obj = bpy.data.objects.get(name)
    if obj is None:
        gp_data = ensure_gpencil(name + "_data")
        obj = bpy.data.objects.new(name, gp_data)
        if link_to_collection and bpy.context.scene is not None:
            bpy.context.scene.collection.objects.link(obj)
    return obj


def ensure_layer(gp_data, layer_name: str):
    """GreasePencil v3 レイヤーを取得/生成."""
    layer = gp_data.layers.get(layer_name)
    if layer is None:
        layer = gp_data.layers.new(layer_name)
    return layer


# ---------- Grease Pencil マテリアル ----------

_DEFAULT_STROKE_MAT_NAME = "BManga_Pen_Black"
_LAYER_MATERIAL_PROP = "bmanga_material_name"
_LAYER_MATERIAL_PREFIX = "BManga_GP_Layer_"


def ensure_default_stroke_material(
    obj,
    name: str = _DEFAULT_STROKE_MAT_NAME,
    color: tuple = (0.0, 0.0, 0.0, 1.0),
):
    """GP Object に黒線ストロークマテリアルを確保・attach し active 化.

    Blender UI の「Add > Grease Pencil > Empty」等で GP オブジェクトを
    生成すると既定の黒線マテリアルが自動付与されるが、Python API で
    ``bpy.data.grease_pencils.new()`` / ``bpy.data.objects.new()`` から
    直接生成した場合はマテリアルが付かない。結果として Draw モードで
    ストロークがブラシ既定色 (Pencil 等ではごく淡色) で描画され、
    「白い線しか出ない」ように見える。
    この関数は ``BManga_Pen_Black`` マテリアルを確保し、GP Object の
    material slot に attach + active 化する。
    """
    if obj is None or obj.type != "GREASEPENCIL":
        return None

    slots = getattr(getattr(obj, "data", None), "materials", None)
    if slots is None:
        return None
    owner_id = material_owner_id(obj)
    # 既にこの Object 用の既定材があれば再利用する。別 Object の基準名材を
    # 毎回追加すると、再同期のたびにスロットが増えるためである。
    for index, existing in enumerate(slots):
        if existing is None:
            continue
        existing_owner = str(existing.get(MATERIAL_OWNER_PROP, "") or "")
        existing_name = str(getattr(existing, "name", "") or "")
        if existing_owner == owner_id or existing_name == name or existing_name.startswith(f"{name}."):
            ensure_unique_object_materials(obj)
            material = slots[index]
            try:
                obj.active_material_index = index
            except Exception:  # noqa: BLE001
                pass
            return material

    mat = bpy.data.materials.get(name)
    created = mat is None
    if created:
        mat = bpy.data.materials.new(name=name)

    # Object の material slot に追加し、共有されていた場合は対象 Object だけ
    # 専用コピーへ差し替えてから色などの可変値へ触れる。
    try:
        material_index = _material_slot_index(obj, mat)
        ensure_unique_object_materials(obj)
        slots = getattr(getattr(obj, "data", None), "materials", None)
        if slots is None:
            return None
        if material_index < 0 or material_index >= len(slots):
            return None
        mat = slots[material_index]
        gp_style = _ensure_gp_material_data(mat)
        if gp_style is not None and created:
            gp_style.show_stroke = True
            gp_style.color = color
            gp_style.show_fill = False
        obj.active_material_index = material_index
    except Exception:  # noqa: BLE001
        _logger.exception("ensure_default_stroke_material: attach failed")
    return mat


def _ensure_gp_material_data(mat):
    if mat is None:
        return None
    if getattr(mat, "grease_pencil", None) is None:
        try:
            bpy.data.materials.create_gpencil_data(mat)
        except (AttributeError, RuntimeError):
            pass
    return getattr(mat, "grease_pencil", None)


def _safe_material_suffix(name: str) -> str:
    cleaned = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in str(name))
    cleaned = cleaned.strip().strip(".")
    return cleaned or "Layer"


def _layer_material_name(layer) -> str:
    try:
        value = layer.get(_LAYER_MATERIAL_PROP, "")
        if value:
            return str(value)
    except Exception:  # noqa: BLE001
        pass
    return f"{_LAYER_MATERIAL_PREFIX}{_safe_material_suffix(getattr(layer, 'name', 'Layer'))}"


def _store_layer_material_name(layer, material_name: str) -> None:
    try:
        layer[_LAYER_MATERIAL_PROP] = material_name
    except Exception:  # noqa: BLE001
        pass


def _material_slot_index(obj, mat) -> int:
    mats = getattr(getattr(obj, "data", None), "materials", None)
    if mats is None or mat is None:
        return -1
    for i, existing in enumerate(mats):
        if existing is mat or getattr(existing, "name", None) == mat.name:
            return i
    try:
        mats.append(mat)
        return len(mats) - 1
    except Exception:  # noqa: BLE001
        _logger.exception("material slot append failed: %s", getattr(mat, "name", ""))
        return -1


def _assign_material_to_layer_strokes(layer, material_index: int) -> None:
    if material_index < 0:
        return
    frames = getattr(layer, "frames", None)
    if frames is None:
        return
    for frame in frames:
        drawing = getattr(frame, "drawing", None)
        strokes = getattr(drawing, "strokes", None)
        if strokes is None:
            continue
        for stroke in strokes:
            try:
                stroke.material_index = material_index
            except Exception:  # noqa: BLE001
                pass


def ensure_layer_material(
    obj,
    layer,
    *,
    activate: bool = False,
    assign_existing: bool = True,
):
    """GP レイヤー専用の内部マテリアルを確保し、必要なら active 化する.

    B-MANGA UI ではマテリアルを見せず、レイヤーの線色/塗り色として扱う。
    実体は Grease Pencil の描画仕様に合わせてレイヤーごとに 1 マテリアルを
    自動管理する。
    """
    if obj is None or layer is None or getattr(obj, "type", "") != "GREASEPENCIL":
        return None

    mat_name = _layer_material_name(layer)
    created = False
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(name=mat_name)
        created = True

    material_index = _material_slot_index(obj, mat)
    ensure_unique_object_materials(obj)
    # ensure_unique_object_materials() が共有GPデータを複製した場合も、現在の
    # Objectに属する内容レイヤーとMaterialへ参照を取り直す。
    layer_name = str(getattr(layer, "name", "") or "")
    current_layers = getattr(getattr(obj, "data", None), "layers", None)
    if current_layers is not None:
        layer = current_layers.get(layer_name) or getattr(current_layers, "active", None) or layer
    slots = getattr(getattr(obj, "data", None), "materials", None)
    if slots is None or material_index < 0 or material_index >= len(slots):
        return None
    mat = slots[material_index]
    _store_layer_material_name(layer, mat.name)

    style_missing = getattr(mat, "grease_pencil", None) is None
    gp_style = _ensure_gp_material_data(mat)
    if gp_style is not None:
        if created or style_missing:
            try:
                gp_style.show_stroke = True
            except Exception:  # noqa: BLE001
                pass
            try:
                gp_style.color = (0.0, 0.0, 0.0, 1.0)
            except Exception:  # noqa: BLE001
                pass
            try:
                gp_style.fill_color = (1.0, 1.0, 1.0, 1.0)
            except Exception:  # noqa: BLE001
                pass
            try:
                gp_style.show_fill = False
            except Exception:  # noqa: BLE001
                pass
    try:
        mat.diffuse_color = tuple(getattr(gp_style, "color", mat.diffuse_color))
    except Exception:  # noqa: BLE001
        pass

    if activate and material_index >= 0:
        try:
            obj.active_material_index = material_index
        except Exception:  # noqa: BLE001
            pass
    if assign_existing:
        _assign_material_to_layer_strokes(layer, material_index)
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def ensure_active_layer_material(obj, *, activate: bool = True):
    layers = getattr(getattr(obj, "data", None), "layers", None)
    if layers is None:
        return None
    layer = getattr(layers, "active", None)
    return ensure_layer_material(obj, layer, activate=activate)


def layer_effectively_hidden(layer) -> bool:
    """レイヤー自身または親フォルダが非表示なら True."""
    if bool(getattr(layer, "hide", False)):
        return True
    group = getattr(layer, "parent_group", None)
    while group is not None:
        if bool(getattr(group, "hide", False)):
            return True
        group = getattr(group, "parent_group", None)
    return False


def layer_effectively_locked(layer) -> bool:
    """レイヤー自身または親フォルダがロックなら True."""
    if bool(getattr(layer, "lock", False)):
        return True
    group = getattr(layer, "parent_group", None)
    while group is not None:
        if bool(getattr(group, "lock", False)):
            return True
        group = getattr(group, "parent_group", None)
    return False


def is_layer_group(node) -> bool:
    return hasattr(node, "children") and hasattr(node, "is_expanded")


def unique_layer_group_name(gp_data, base: str = "フォルダ") -> str:
    groups = getattr(gp_data, "layer_groups", None)
    if groups is None:
        return base
    existing = {group.name for group in groups}
    name = base
    i = 0
    while name in existing:
        i += 1
        name = f"{base}.{i:03d}"
    return name


def move_layer_to_group(gp_data, layer, group) -> bool:
    layers = getattr(gp_data, "layers", None)
    if layers is None or layer is None:
        return False
    try:
        layers.move_to_layer_group(layer, group)
    except Exception:  # noqa: BLE001
        _logger.exception("move layer to group failed")
        return False
    return True


def move_group_to_group(gp_data, group, parent_group) -> bool:
    groups = getattr(gp_data, "layer_groups", None)
    if groups is None or group is None:
        return False
    try:
        groups.move_to_layer_group(group, parent_group)
    except Exception:  # noqa: BLE001
        _logger.exception("move group to group failed")
        return False
    return True


def remove_layer_group_preserve_children(gp_data, group) -> bool:
    """フォルダを削除し、中身のレイヤー/子フォルダは親階層へ退避する."""
    groups = getattr(gp_data, "layer_groups", None)
    if groups is None or group is None:
        return False
    parent = getattr(group, "parent_group", None)
    for child in list(getattr(group, "children", [])):
        if is_layer_group(child):
            move_group_to_group(gp_data, child, parent)
        else:
            move_layer_to_group(gp_data, child, parent)
    try:
        groups.remove(group)
    except Exception:  # noqa: BLE001
        _logger.exception("remove layer group failed: %s", getattr(group, "name", ""))
        return False
    return True


# ---------- ページ Collection / GP ----------


def ensure_root_collection(scene):
    """ルート Collection ``B-MANGA`` を scene 直下に確保."""
    root = bpy.data.collections.get(ROOT_COLLECTION_NAME)
    if root is None:
        root = bpy.data.collections.new(ROOT_COLLECTION_NAME)
    if scene is not None and root.name not in scene.collection.children:
        # 他の親 (data-level 孤児化) に既にリンクされている場合は触らない。
        # scene.collection 直下に無ければリンクする。
        if not _is_linked_anywhere_in_scene(scene, root):
            scene.collection.children.link(root)
    return root


def _is_linked_anywhere_in_scene(scene, collection) -> bool:
    """scene 以下の任意の Collection 階層に ``collection`` が既にリンクされているか."""
    def walk(coll):
        if coll is None:
            return False
        for child in coll.children:
            if child == collection:
                return True
            if walk(child):
                return True
        return False

    return walk(scene.collection)


def ensure_page_collection(scene, page_id: str):
    """ページ Collection を Outliner mirror 経由で取得/生成して返す.

    新仕様: ``utils.outliner_model.ensure_page_collection`` に委譲し、
    bmanga_id で安定逆引きされる ``p0001`` 形式 Collection を返す。
    旧 ``page_p0001`` 名で生成されていた残置 Collection も移行のため
    リネームを試みる。
    """
    from . import outliner_model as om

    if scene is not None and page_id:
        # 旧名残置 Collection があれば mirror 統合のため bmanga_id を立てて取り込む
        old_name = f"page_{page_id}"
        old_coll = bpy.data.collections.get(old_name)
        if old_coll is not None:
            # bmanga_id "page_id" を持つ管理 Collection が既に別にあれば、
            # 旧 Collection の中身を新側へ移し替える前に mirror で新側を ensure。
            new_coll = om.ensure_page_collection(scene, page_id)
            if new_coll is not None and new_coll is not old_coll:
                # 旧 Collection の Object/子 Collection を新側へ移送
                for obj in list(old_coll.objects):
                    try:
                        if obj.name not in new_coll.objects:
                            new_coll.objects.link(obj)
                        old_coll.objects.unlink(obj)
                    except Exception:  # noqa: BLE001
                        pass
                for child in list(old_coll.children):
                    try:
                        if child.name not in new_coll.children:
                            new_coll.children.link(child)
                        old_coll.children.unlink(child)
                    except Exception:  # noqa: BLE001
                        pass
                # 残置 Collection を root から外し、データブロックも削除
                root = ensure_root_collection(scene)
                if old_coll.name in root.children:
                    try:
                        root.children.unlink(old_coll)
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    bpy.data.collections.remove(old_coll)
                except Exception:  # noqa: BLE001
                    pass
            return new_coll
        return om.ensure_page_collection(scene, page_id)
    # フォールバック (scene 不明時)
    root = ensure_root_collection(scene)
    coll = bpy.data.collections.get(page_id)
    if coll is None:
        coll = bpy.data.collections.new(page_id)
    if scene is not None and coll.name not in root.children:
        try:
            root.children.link(coll)
        except Exception:  # noqa: BLE001
            pass
    return coll


# ---------- 移行元となる旧集約 GP の識別名 ----------
# 通常処理では生成・取得せず、旧データの検出と安全な除去にだけ使う。

MASTER_GP_OBJECT_NAME = "bmanga_master_sketch"
MASTER_GP_DATA_NAME = "bmanga_master_sketch_data"


# ---------- 旧紙メッシュ互換 ----------

PAPER_MATERIAL_NAME = "BManga_Paper_White"


def _ensure_paper_material():
    """全ページ共有の白マテリアルを取得/生成 (Solid 表示で白く見せる)."""
    mat = bpy.data.materials.get(PAPER_MATERIAL_NAME)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(PAPER_MATERIAL_NAME)
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)  # Solid Display=Material 時の表示色
    mat.use_nodes = False
    return mat


def _paper_rgba_from_value(color_value) -> tuple[float, float, float, float]:
    """PropertyGroup / シーケンス由来の色値を紙表示用 RGBA に正規化."""
    if color_value is None:
        return (1.0, 1.0, 1.0, 1.0)
    try:
        r = float(color_value[0])
        g = float(color_value[1])
        b = float(color_value[2])
    except Exception:  # noqa: BLE001
        return (1.0, 1.0, 1.0, 1.0)
    alpha = 1.0
    try:
        alpha = float(color_value[3])
    except Exception:  # noqa: BLE001
        alpha = 1.0
    return (
        max(0.0, min(1.0, r)),
        max(0.0, min(1.0, g)),
        max(0.0, min(1.0, b)),
        max(0.0, min(1.0, alpha)),
    )


def sync_paper_material_color(color_value) -> object | None:
    """旧紙メッシュ共有マテリアルが残っていれば ``paper_color`` に同期."""
    mat = bpy.data.materials.get(PAPER_MATERIAL_NAME)
    if mat is None:
        return None
    rgba = _paper_rgba_from_value(color_value)
    try:
        if tuple(float(c) for c in mat.diffuse_color[:4]) != rgba:
            mat.diffuse_color = rgba
        mat.update_tag()
    except Exception:  # noqa: BLE001
        _logger.exception("sync_paper_material_color: material update failed")
        return mat

    # 互換: 過去ファイルに紙オブジェクトが残っていた場合だけ色を合わせる。
    for obj in tuple(bpy.data.objects):
        if not obj.name.startswith("page_") or not obj.name.endswith("_paper"):
            continue
        try:
            obj.color = rgba
        except Exception:  # noqa: BLE001
            pass
    return mat


def page_paper_object_name(page_id: str) -> str:
    return f"page_{page_id}_paper"


def page_paper_mesh_name(page_id: str) -> str:
    return f"page_{page_id}_paper_data"


def remove_page_paper(page_id: str) -> None:
    """ページ用紙メッシュを削除する。用紙表示は GPU overlay で行う."""
    obj_name = page_paper_object_name(page_id)
    mesh_name = page_paper_mesh_name(page_id)
    obj = bpy.data.objects.get(obj_name)
    if obj is not None:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            _logger.exception("remove paper object failed: %s", obj_name)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is not None and mesh.users == 0:
        try:
            bpy.data.meshes.remove(mesh)
        except Exception:  # noqa: BLE001
            _logger.exception("remove paper mesh failed: %s", mesh_name)


def remove_all_page_papers() -> None:
    """旧仕様で作られた page_XXXX_paper 系オブジェクト/メッシュを掃除する."""
    for obj in tuple(bpy.data.objects):
        name = str(getattr(obj, "name", "") or "")
        if name.startswith("page_") and (name.endswith("_paper") or "_paper." in name):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:  # noqa: BLE001
                _logger.exception("remove paper object failed: %s", name)
    for mesh in tuple(bpy.data.meshes):
        name = str(getattr(mesh, "name", "") or "")
        if name.startswith("page_") and (name.endswith("_paper_data") or "_paper_data." in name):
            if mesh.users != 0:
                continue
            try:
                bpy.data.meshes.remove(mesh)
            except Exception:  # noqa: BLE001
                _logger.exception("remove paper mesh failed: %s", name)


def remove_all_page_gpencils() -> None:
    """B-MANGA の全ページ Collection / GP オブジェクト / 紙メッシュを一括削除する.

    新規作品作成 (``work_new``) や作品クローズ (``work_close``) で、前作品の
    ``page_pNNNN`` Collection や ``page_pNNNN_sketch`` GP が残らないようにする。
    master GP (``bmanga_master_sketch``) と effect GP (``BManga_EffectLines``) は
    作品横断で使い回すため対象外。

    判定は ``page_<page_id>`` (Collection) / ``page_<page_id>_sketch`` (GP) の
    命名規則に基づく。手動で同名 Collection を作っている場合に巻き込まれる
    可能性があるため、Collection は ``B-MANGA`` ルート配下に登録されているもの
    だけを対象にする。
    """
    import re

    page_collection_pattern = re.compile(r"^page_p\d+$")
    page_gp_pattern = re.compile(r"^page_p\d+_sketch(?:\.\d+)?$")
    page_gp_data_pattern = re.compile(r"^page_p\d+_sketch_data(?:\.\d+)?$")

    # 1) 旧仕様 page_NNNN_sketch GP オブジェクトを削除
    for obj in tuple(bpy.data.objects):
        name = str(getattr(obj, "name", "") or "")
        if not page_gp_pattern.match(name):
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            _logger.exception("remove page GP object failed: %s", name)

    # 2) 紙メッシュ (page_pNNNN_paper / _paper_data) も併せて掃除
    remove_all_page_papers()

    # 3) page_pNNNN Collection を削除 (B-MANGA ルート配下のみ対象)
    root = bpy.data.collections.get(ROOT_COLLECTION_NAME)
    candidates: list[object] = []
    if root is not None:
        for coll in tuple(root.children):
            name = str(getattr(coll, "name", "") or "")
            if page_collection_pattern.match(name):
                candidates.append(coll)
    # ROOT_COLLECTION_NAME 配下に居なくても、命名一致する page_pNNNN は
    # 過去のリンク漏れで scene 直下に残っていることがあるため掃除する。
    for coll in tuple(bpy.data.collections):
        name = str(getattr(coll, "name", "") or "")
        if not page_collection_pattern.match(name):
            continue
        if coll not in candidates:
            candidates.append(coll)
    for coll in candidates:
        try:
            bpy.data.collections.remove(coll)
        except Exception:  # noqa: BLE001
            _logger.exception("remove page collection failed: %s", coll.name)

    # 4) 孤児になった GP データブロックを掃除
    try:
        blocks = _gp_data_blocks()
    except RuntimeError:
        blocks = None
    if blocks is not None:
        for data_block in tuple(blocks):
            name = str(getattr(data_block, "name", "") or "")
            if not page_gp_data_pattern.match(name):
                continue
            if getattr(data_block, "users", 0) != 0:
                continue
            try:
                blocks.remove(data_block)
            except Exception:  # noqa: BLE001
                _logger.exception("remove orphan GP data failed: %s", name)


def ensure_page_paper(
    scene,
    page_id: str,
    canvas_width_mm: float,
    canvas_height_mm: float,
    paper_color=None,
):
    """互換用 no-op。用紙表示は実メッシュではなく GPU overlay で行う."""
    _ = scene, canvas_width_mm, canvas_height_mm, paper_color
    remove_page_paper(page_id)
    return None


def get_page_paper(page_id: str):
    return bpy.data.objects.get(page_paper_object_name(page_id))


def _relink_object_to_collection_only(scene, obj, target_coll) -> None:
    """``obj`` を ``target_coll`` のみにリンクし、他の Collection からは外す.

    scene.collection 直下に残っていると overview の grid transform が効かない
    (scene.collection 直下のオブジェクトは Collection transform を持たない)。
    """
    # 既にリンク済みの Collection 一覧
    linked = [c for c in bpy.data.collections if obj.name in c.objects]
    # scene 直下リンクも外す
    if scene is not None and obj.name in scene.collection.objects:
        try:
            scene.collection.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass
    for c in linked:
        if c is target_coll:
            continue
        try:
            c.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass
    if obj.name not in target_coll.objects:
        try:
            target_coll.objects.link(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("link to %s failed", target_coll.name)


def get_page_collection(page_id: str):
    """既存の ``page_NNNN`` Collection を返す (無ければ None)."""
    return bpy.data.collections.get(page_collection_name(page_id))


# ---------- 見開き統合/解除用のリネーム・再リンクヘルパ ----------


def rename_gp_object_and_data(obj, new_obj_name: str, new_data_name: str | None = None) -> None:
    """GP Object と その data-block を安全に rename.

    Blender は衝突時に ``.001`` サフィックスを付けて別名で登録するため、
    事前に衝突チェックする。衝突がある場合は他方を先にリネームするなど
    呼出側で順序を調整すること。
    """
    if obj is None:
        return
    if obj.name != new_obj_name:
        obj.name = new_obj_name
    if new_data_name is not None and obj.data is not None and obj.data.name != new_data_name:
        obj.data.name = new_data_name


def rename_page_collection(old_id: str, new_id: str) -> object | None:
    """``page_<old_id>`` Collection を ``page_<new_id>`` に rename."""
    old_name = page_collection_name(old_id)
    new_name = page_collection_name(new_id)
    coll = bpy.data.collections.get(old_name)
    if coll is None:
        return None
    if coll.name != new_name:
        coll.name = new_name
    return coll


def relink_object_to_page(scene, obj, target_page_id: str) -> None:
    """``obj`` を ``page_<target_page_id>`` Collection のみリンクし直す.

    target Collection が無ければ生成。既に他の Collection にリンクされて
    いれば unlink。見開き統合/解除で GP を別ページ Collection へ移すときに
    使う。
    """
    if obj is None:
        return
    target = ensure_page_collection(scene, target_page_id)
    _relink_object_to_collection_only(scene, obj, target)


def add_stroke_to_drawing(
    drawing,
    points_xyz: Iterable[tuple[float, float, float]],
    radius: float = 0.01,
    radii: Iterable[float] | None = None,
    opacities: Iterable[float] | None = None,
    cyclic: bool = False,
    material_index: int | None = None,
    curve_type: str = "POLY",
    bezier_smooth: bool = False,
) -> bool:
    """GreasePencilDrawing に 1 ストロークを追加.

    Blender 5.x 系の API では ``drawing.add_strokes([n_points])`` で新規
    ストロークを作り、attribute API で ``position`` / ``radius`` を書き込む。
    動作しないバージョンでは False を返す。
    """
    pts = list(points_xyz)
    if not pts:
        return False
    point_radii = list(radii or [])
    point_opacities = list(opacities or [])
    try:
        start_index = len(getattr(drawing, "strokes", []))
        strokes = drawing.add_strokes([len(pts)])
        if strokes is None:
            stroke = drawing.strokes[start_index]
        else:
            stroke = strokes[0]
        stroke.cyclic = cyclic
        _apply_stroke_material(stroke, material_index)
        if hasattr(stroke, "points") and len(stroke.points) >= len(pts):
            for i, (x, y, z) in enumerate(pts):
                point = stroke.points[i]
                point.position = (x, y, z)
                if hasattr(point, "radius"):
                    point.radius = point_radii[i] if i < len(point_radii) else radius
                if hasattr(point, "opacity"):
                    opacity = point_opacities[i] if i < len(point_opacities) else 1.0
                    point.opacity = max(0.0, min(1.0, float(opacity)))
            _set_stroke_curve_type(drawing, start_index, stroke, pts, cyclic, curve_type, bezier_smooth)
            return True
        pos_attr = drawing.attributes.get("position")
        if pos_attr is None:
            return False
        offset = getattr(stroke.points, "offset", 0)
        for i, (x, y, z) in enumerate(pts):
            pos_attr.data[offset + i].vector = (x, y, z)
        rad_attr = drawing.attributes.get("radius")
        if rad_attr is not None:
            for i in range(len(pts)):
                rad_attr.data[offset + i].value = point_radii[i] if i < len(point_radii) else radius
        opacity_attr = drawing.attributes.get("opacity")
        if opacity_attr is not None:
            for i in range(len(pts)):
                opacity_attr.data[offset + i].value = max(
                    0.0,
                    min(1.0, float(point_opacities[i] if i < len(point_opacities) else 1.0)),
                )
        _apply_stroke_material(stroke, material_index)
        _set_stroke_curve_type(drawing, start_index, stroke, pts, cyclic, curve_type, bezier_smooth)
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.warning("add_stroke_to_drawing failed: %s", exc)
        return False


def _apply_stroke_material(stroke, material_index: int | None) -> None:
    if material_index is None:
        return
    try:
        mat_index = int(material_index)
        if mat_index >= 0:
            stroke.material_index = mat_index
    except Exception:  # noqa: BLE001
        pass


def _set_stroke_curve_type(
    drawing,
    stroke_index: int,
    stroke,
    pts: list[tuple[float, float, float]],
    cyclic: bool,
    curve_type: str,
    bezier_smooth: bool,
) -> None:
    if str(curve_type or "").upper() != "BEZIER":
        return
    try:
        drawing.set_types(type="BEZIER", indices=(int(stroke_index),))
    except Exception:  # noqa: BLE001
        return
    _set_bezier_handles(stroke, pts, cyclic, smooth=bezier_smooth)


def _set_bezier_handles(
    stroke,
    pts: list[tuple[float, float, float]],
    cyclic: bool,
    *,
    smooth: bool,
) -> None:
    points = getattr(stroke, "points", None)
    if points is None or len(points) < 2:
        return
    count = min(len(points), len(pts))
    for i in range(count):
        left = getattr(points[i], "handle_left", None)
        right = getattr(points[i], "handle_right", None)
        if left is None or right is None:
            continue
        prev_i = (i - 1) % count if cyclic else max(0, i - 1)
        next_i = (i + 1) % count if cyclic else min(count - 1, i + 1)
        p = pts[i]
        prev_p = pts[prev_i]
        next_p = pts[next_i]
        tangent = (next_p[0] - prev_p[0], next_p[1] - prev_p[1], next_p[2] - prev_p[2])
        if not smooth:
            tangent = (0.0, 0.0, 0.0)
        elif not cyclic and (i == 0 or i == count - 1):
            length = math.sqrt(sum(component * component for component in tangent))
            scale = 0.0 if length <= 1.0e-12 else 1.0 / 3.0
        else:
            scale = 1.0 / 6.0
        if not smooth:
            scale = 0.0
        try:
            left.position = (
                p[0] - tangent[0] * scale,
                p[1] - tangent[1] * scale,
                p[2] - tangent[2] * scale,
            )
            right.position = (
                p[0] + tangent[0] * scale,
                p[1] + tangent[1] * scale,
                p[2] + tangent[2] * scale,
            )
        except Exception:  # noqa: BLE001
            continue


def ensure_active_frame(layer, frame_number: int | None = None):
    """指定フレームに GreasePencilFrame を取得/生成.

    frame_number=None なら現在のシーンフレーム。
    """
    if frame_number is None:
        frame_number = bpy.context.scene.frame_current
    # v3 は layer.frames リストで管理。既存があれば再利用。
    for frame in layer.frames:
        if frame.frame_number == frame_number:
            return frame
    try:
        return layer.frames.new(frame_number=frame_number)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("frame.new failed: %s", exc)
        return None
