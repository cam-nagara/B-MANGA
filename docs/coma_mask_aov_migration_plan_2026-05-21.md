# コマ用 blend のマスク AOV 化計画 2026-05-21

## 背景

旧 `c00.blend` テンプレートおよび B-MANGA Render プリセットは、 ベイク済みの固定 PNG (`コマ枠.png`) を「コマの形」と「用紙サイズ」の正本として扱っていた。 現行 B-MANGA はコマ形状をページ一覧側の実オブジェクトで管理しているため、 静止 PNG が陳腐化し、 形状変更や用紙サイズ変更に追従しない。

本計画は、 静止 PNG を撤去し、 B-MANGA が生成するメッシュ + AOV パイプラインへ移行する。

## 確定事項

1. **メッシュ・マテリアル・コレクション・view layer・AOV の命名は既存を踏襲する**:
   - メッシュ + マテリアル + コレクション: `コマ枠`
   - view layer: `コマ枠`
   - AOV (COLOR): `コマ枠拡張`
2. **メッシュ頂点は B-MANGA が生成する**: 現在コマの多角形 (mm) を z=0 平面に配置。 配置は overview と同じ世界座標 (ページ位置にぴったり)。
3. **更新タイミング**: `load_post` (コマ用 blend を開いた直後) と `save_pre` (保存直前)。
4. **解像度同期**: `coma_camera.configure_render_for_current_coma` が既存で用紙サイズ × DPI を反映済み。 変更不要。
5. **コンポジター差し替え**: 8 グループの `コマ枠.png` Image ノードを、 `コマ枠` view layer の `コマ枠拡張` AOV を読む Render Layers ノードへ置換。
6. **削除対象**:
   - 静止画像 `コマ枠.png` 系全 (`コマ枠.png`, `コマ枠.png.001`, `コマ枠0000.png`, `コマ枠0000.png.001`, `コマ枠線.png`, `コマ枠拡張.png`)
   - 旧カメラ背景画像 `コマ01.001`〜`コマ14.001`, `コマ00_1.001`〜`コマ00_3.001`, `ネーム画像.png`, `ハッチング間隔.png`
   - 孤立シェーダーグループ `NodeGroup.001`, `NodeGroup.014`

## 移行手順

### Phase 1: B-MANGA コード

- 新規 `utils/coma_mask_object.py`:
  - `ensure_coma_mask_mesh(scene, work, page_id, coma_id)`: メッシュ・マテリアル・コレクション・view layer・AOV を冪等に確保。 メッシュ頂点を現在コマの多角形で上書き。
  - `remove_coma_mask_mesh(scene)`: 旧メッシュを片付ける (テストやリセット用)。
- `utils/handlers.py::_bmanga_on_load_post`: コマ用 blend 判定の分岐に `ensure_coma_mask_mesh` 呼び出しを追加。
- `utils/handlers.py::_bmanga_on_save_pre`: 同じく save_pre のコマ判定分岐に追加。

### Phase 2: c00.blend マイグレーション (一回限り)

`test/blender_c00_mask_aov_migration.py` を新規作成:

1. `c00.blend.bak_YYYYMMDD` を作成。
2. `c00.blend` を開く。
3. コンポジターの 8 グループに対し、 `コマ枠.png` の `画像.069` ノードを、 同じ位置で `Render Layers` ノードに置換 (layer=`コマ枠`, AOV出力=`コマ枠拡張`)。 後段ソケットの接続を維持。
4. カメラ背景画像から旧固定画像 21 枚を全削除。
5. `bpy.data.images` から旧固定画像 (上記 6 画像 + 20 個の `コマXX.001` / `コマ00_X.001` 系) を削除。
6. `bpy.data.node_groups` から `NodeGroup.001`, `NodeGroup.014` を削除。
7. 保存。

### Phase 3: B-MANGA Render プリセット

`addons/b_manga_render/preset_library.py`:
- `_page_output` と `_all_output` (旧 `ページ` / `全コマ統合` 経路) は AGENTS.md / 監査ドキュメントに従い、 現行 B-MANGA 仕様外として既存どおり `旧出力シーン互換: ...` 表記で残す (変更最小)。
- 新規 AOV ベースの参照は、 既存プリセットが内部で呼ぶコンポジターノードの中身が AOV に切り替わるため、 プリセット側は変更不要。

### Phase 4: テスト

`test/blender_coma_mask_aov_check.py` を新規作成:
- コマ用 blend テンプレートを開く。
- B-MANGA の `ensure_coma_mask_mesh` を呼ぶ。
- `bpy.data.objects['コマ枠']` が現在コマの頂点数と一致することを確認。
- `bpy.context.scene.view_layers['コマ枠'].aovs['コマ枠拡張']` が有効であることを確認。
- コンポジターの 8 グループに `Render Layers` ノードが含まれ、 旧 Image ノードが無いことを確認。

## バージョン bump

v0.6.024 → v0.6.025 (PATCH)。

## ロールバック

`c00.blend.bak_YYYYMMDD` を `c00.blend` にコピーして戻す。 B-MANGA コード側は `mark_all_externally_finished` 同様の安全機構が無いため、 該当コミットを `git revert` で戻す。
