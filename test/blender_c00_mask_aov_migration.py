"""c00.blend を AOV ベースのマスクパイプラインへ移行する一回限りのスクリプト.

実行方法 (バックアップは別途取得しておくこと):

    "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background ^
        "D:/TM Dropbox/Share/B-Name/c_file/c00.blend" ^
        --python test/blender_c00_mask_aov_migration.py

変更内容:
1. コンポジターの 8 グループに含まれる ``コマ枠.png`` Image ノードを、
   ``コマ枠`` view layer から ``コマ枠拡張`` AOV を読む Render Layers ノードへ
   置換する。
2. カメラの背景画像から旧固定参照を全削除。
3. ``bpy.data.images`` から旧固定画像 (``コマ枠.png`` 系・``コマXX.001`` 系・
   ``ネーム画像.png`` 系・``ハッチング間隔.png``) を削除。
4. 孤立シェーダーグループ ``NodeGroup.001`` / ``NodeGroup.014`` を削除。
5. ``bpy.ops.wm.save_mainfile`` で同名保存。

注意: ファイル上書きを行うため、 事前に ``c00.blend.bak_<日付>`` を取得しておく。
"""
from __future__ import annotations

import sys
import bpy


MASK_VIEW_LAYER_NAME = "コマ枠"
MASK_AOV_NAME = "コマ枠拡張"

LEGACY_FRAME_IMAGE_NAMES = (
    "コマ枠.png",
    "コマ枠.png.001",
    "コマ枠0000.png",
    "コマ枠0000.png.001",
    "コマ枠線.png",
    "コマ枠拡張.png",
)
LEGACY_PER_COMA_IMAGE_PREFIXES = (
    "コマ01",
    "コマ02",
    "コマ03",
    "コマ04",
    "コマ05",
    "コマ06",
    "コマ07",
    "コマ08",
    "コマ09",
    "コマ10",
    "コマ11",
    "コマ12",
    "コマ13",
    "コマ14",
    "コマ00_1",
    "コマ00_2",
    "コマ00_3",
)
LEGACY_MISC_IMAGE_NAMES = (
    "ネーム画像.png",
    "ハッチング間隔.png",
)
ORPHAN_SHADER_GROUPS = ("NodeGroup.001", "NodeGroup.014")


def _is_legacy_image_name(name: str) -> bool:
    if name in LEGACY_FRAME_IMAGE_NAMES:
        return True
    if name in LEGACY_MISC_IMAGE_NAMES:
        return True
    for prefix in LEGACY_PER_COMA_IMAGE_PREFIXES:
        if name == prefix or name.startswith(prefix + ".") or name.startswith(prefix + "/"):
            return True
    return False


def _ensure_view_layer_and_aov(scene) -> None:
    vl = scene.view_layers.get(MASK_VIEW_LAYER_NAME)
    if vl is None:
        vl = scene.view_layers.new(MASK_VIEW_LAYER_NAME)
        print(f"  + view layer 新規: {MASK_VIEW_LAYER_NAME}")
    has_aov = any(a.name == MASK_AOV_NAME for a in vl.aovs)
    if not has_aov:
        aov = vl.aovs.add()
        aov.name = MASK_AOV_NAME
        aov.type = "COLOR"
        print(f"  + AOV 追加: {MASK_AOV_NAME}")


def _replace_image_node_with_rlayers(node_tree, image_node, scene) -> int:
    """1 つの Image ノードを RLayers + AOV に置換し、 下流 link を貼り直す.

    戻り値: 移し替えた link 数。
    """
    if image_node.bl_idname != "CompositorNodeImage":
        return 0
    nodes = node_tree.nodes
    links = node_tree.links

    rl = nodes.new("CompositorNodeRLayers")
    rl.scene = scene
    rl.layer = MASK_VIEW_LAYER_NAME
    rl.location = (image_node.location.x, image_node.location.y)
    rl.label = "コマ枠 AOV"

    aov_socket = rl.outputs.get(MASK_AOV_NAME)
    if aov_socket is None:
        # AOV ソケットは scene/view_layer の AOV 設定が反映されたタイミングで
        # 自動生成される。 強制的に depsgraph を更新する。
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        aov_socket = rl.outputs.get(MASK_AOV_NAME)
    if aov_socket is None:
        # 最終フォールバック: Image 出力を流用する (黒一面になるが構造は維持)
        aov_socket = rl.outputs.get("Image")

    moved_links = 0
    # 下流 link を AOV へ付け替え
    for link in list(image_node.outputs.get("Image").links if image_node.outputs.get("Image") else []):
        to_socket = link.to_socket
        if aov_socket is not None and to_socket is not None:
            try:
                links.new(aov_socket, to_socket)
                moved_links += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  ! link 再接続失敗 {to_socket.node.name}.{to_socket.name}: {exc!r}")
    alpha_out = image_node.outputs.get("Alpha")
    if alpha_out is not None:
        for link in list(alpha_out.links):
            to_socket = link.to_socket
            if aov_socket is not None and to_socket is not None:
                try:
                    links.new(aov_socket, to_socket)
                    moved_links += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! alpha link 再接続失敗 {to_socket.node.name}.{to_socket.name}: {exc!r}")

    # 旧 Image ノードを削除
    nodes.remove(image_node)
    return moved_links


def _migrate_node_groups(scene) -> int:
    total_replaced = 0
    target_image_names = {"コマ枠.png", "コマ枠.png.001", "コマ枠0000.png", "コマ枠0000.png.001"}
    for ng in list(bpy.data.node_groups):
        if ng.bl_idname != "CompositorNodeTree":
            continue
        # 対象画像を参照する Image ノードだけリストアップしてから削除 (反復中変更回避)
        image_nodes = [
            n for n in list(ng.nodes)
            if n.bl_idname == "CompositorNodeImage"
            and n.image is not None
            and str(n.image.name) in target_image_names
        ]
        if not image_nodes:
            continue
        print(f"[{ng.name}] {len(image_nodes)} 個の Image ノードを AOV に置換")
        for img_node in image_nodes:
            img_node_name = img_node.name  # remove 前に保存
            moved = _replace_image_node_with_rlayers(ng, img_node, scene)
            print(f"  - {img_node_name}: 下流 {moved} link を AOV へ移設")
            total_replaced += 1
    return total_replaced


def _clean_camera_backgrounds() -> int:
    removed = 0
    for cam in bpy.data.cameras:
        bgs = list(cam.background_images)
        for bg in bgs:
            img = bg.image
            if img is None:
                continue
            if _is_legacy_image_name(img.name):
                try:
                    cam.background_images.remove(bg)
                    removed += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! camera bg 削除失敗 {cam.name}/{img.name}: {exc!r}")
    return removed


def _remove_legacy_images() -> int:
    removed = 0
    for img in list(bpy.data.images):
        if _is_legacy_image_name(img.name):
            try:
                bpy.data.images.remove(img)
                removed += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  ! image 削除失敗 {img.name}: {exc!r}")
    return removed


def _remove_orphan_shader_groups() -> int:
    removed = 0
    for name in ORPHAN_SHADER_GROUPS:
        ng = bpy.data.node_groups.get(name)
        if ng is None:
            continue
        if ng.users > 0:
            print(f"  - {name}: ユーザー {ng.users} 件あり、 削除をスキップ")
            continue
        try:
            bpy.data.node_groups.remove(ng)
            removed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ! node group 削除失敗 {name}: {exc!r}")
    return removed


def _remove_dangling_image_nodes() -> int:
    """画像ノードのうち、 .image が None となったものを削除する.

    旧 ``コマXX.001`` 等を読んでいたノードは、 画像データ削除に伴って .image が
    None になっており、 コンポジターから出力が来ない死にノードになっている。
    """
    removed = 0
    for ng in list(bpy.data.node_groups):
        if ng.bl_idname != "CompositorNodeTree":
            continue
        targets = [n for n in list(ng.nodes)
                   if n.bl_idname == "CompositorNodeImage" and n.image is None]
        for n in targets:
            name = n.name
            try:
                ng.nodes.remove(n)
                removed += 1
                print(f"  - dangling Image node removed: {ng.name}/{name}")
            except Exception as exc:  # noqa: BLE001
                print(f"  ! dangling Image 削除失敗 {ng.name}/{name}: {exc!r}")
    # Scene compositor も同様
    for scene in bpy.data.scenes:
        nt = getattr(scene, "node_tree", None)
        if nt is None:
            continue
        targets = [n for n in list(nt.nodes)
                   if n.bl_idname == "CompositorNodeImage" and n.image is None]
        for n in targets:
            name = n.name
            try:
                nt.nodes.remove(n)
                removed += 1
                print(f"  - dangling Image node removed: Scene[{scene.name}]/{name}")
            except Exception as exc:  # noqa: BLE001
                print(f"  ! dangling Image 削除失敗 Scene[{scene.name}]/{name}: {exc!r}")
    return removed


def _purge_orphan_datablocks() -> int:
    """孤立したマテリアル / ノードグループ / メッシュ / 画像をまとめて掃除する.

    旧 `コマ枠.png` を参照していた NodeGroup.004 や material `コマ枠.001` などは
    fake_user フラグや 0 ユーザーでない見かけ上の参照で `orphans_purge` を
    通り抜けるため、 順序を決めて明示的に削除する。
    """
    removed = []

    # 1. material `コマ枠.001` (NodeGroup.004 を参照しているが、 オブジェクトには未割当)
    mat = bpy.data.materials.get("コマ枠.001")
    if mat is not None and not any(o.material_slots and any(slot.material is mat for slot in o.material_slots) for o in bpy.data.objects):
        try:
            mat.use_fake_user = False
            bpy.data.materials.remove(mat, do_unlink=True)
            removed.append("material:コマ枠.001")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! material コマ枠.001 削除失敗: {exc!r}")

    # 2. NodeGroup.004 (NodeGroup.001 / NodeGroup.014 を内包)
    for name in ("NodeGroup.004", "NodeGroup.001", "NodeGroup.014"):
        ng = bpy.data.node_groups.get(name)
        if ng is None:
            continue
        try:
            ng.use_fake_user = False
            bpy.data.node_groups.remove(ng, do_unlink=True)
            removed.append(f"node_group:{name}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! node_group {name} 削除失敗: {exc!r}")

    # 3. 最後に orphans_purge で連鎖する image / mesh / curve 等も掃除
    for i in range(3):
        try:
            bpy.ops.outliner.orphans_purge(
                do_local_ids=True,
                do_linked_ids=True,
                do_recursive=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ! orphans_purge 失敗 (iter={i}): {exc!r}")
            break

    for r in removed:
        print(f"  - removed {r}")
    return len(removed)


def main() -> int:
    scene = bpy.context.scene
    print(f"=== c00.blend AOV migration: {bpy.data.filepath} ===")
    print(f"-- scene={scene.name} --")

    _ensure_view_layer_and_aov(scene)
    bpy.context.view_layer.update()

    replaced = _migrate_node_groups(scene)
    print(f"\n[結果] 置換した画像→AOV ノード数: {replaced}")

    bg_removed = _clean_camera_backgrounds()
    print(f"[結果] 削除したカメラ背景: {bg_removed}")

    img_removed = _remove_legacy_images()
    print(f"[結果] 削除した旧固定画像: {img_removed}")

    ng_removed = _remove_orphan_shader_groups()
    print(f"[結果] 削除した孤立シェーダーグループ: {ng_removed}")

    dangling_removed = _remove_dangling_image_nodes()
    print(f"[結果] 削除した dangling Image ノード: {dangling_removed}")

    purge_iter = _purge_orphan_datablocks()
    print(f"[結果] orphans_purge 反復回数: {purge_iter}")

    # 保存
    try:
        bpy.ops.wm.save_mainfile()
        print(f"\nSAVED: {bpy.data.filepath}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n!! 保存失敗: {exc!r}")
        return 1
    return 0


if __name__ == "__main__":
    rc = main()
    if rc:
        sys.exit(rc)
