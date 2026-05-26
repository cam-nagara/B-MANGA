# フキダシのフチ・多重線を Shapely buffer 方式に統一する計画

作成日: 2026-05-27
対象: B-Name v0.6.102 以降
ベース commit: 2f71a2e ([claude] feat: 雲フキダシ主線を Shapely buffer 方式に刷新…)

## ゴール

雲フキダシ主線は v0.6.102 で「Shapely Polygon.buffer + mapbox-earcut 三角分割」方式に統一済み。
任意の線幅でも自己交差なくクリーンに描画できることが確認できた。

同様の自己交差問題は **外側フチ・内側フチ・多重線** にも原理的に発生するので、これらも
**主線と同じ Shapely buffer 方式** に作り直す。

ジオメトリノードでは self-intersection を解決できないため、Python 側で Shapely に委譲する
方針を継続する。

## 現状の問題点

- 外側フチ・内側フチは `utils/balloon_multiline_curve.py:append_edge_paths`
  経由で、`_offset_closed_outline*` (純 Python の手書きオフセット) でカーブを生成している。
- 多重線は同ファイル `append_closed_multi_line_paths` で、リングごとに
  `_offset_closed_outline_smooth` + `_band_loops_for_side` で band を作っている。
- いずれも本体カーブから一定距離 offset した平行曲線を組み立てる方式で、
  本体の山間隔より広い距離を取ると自己交差が起きる。
- 谷の鋭角部分の処理は cosine 減衰や arc smoothing でごまかしているが、
  根本的な解決にはなっていない。
- 現状の `balloon_curve_object._sync_curve_geometry` は、上記カーブを本体カーブ
  オブジェクトの splines に追加し、geometry_nodes_bridge 経由の Curve→Mesh
  変換で描画している。

## v0.6.102 で確立した方針

新規参考実装は `utils/balloon_line_mesh.py` に集中している:

- `_stroke_band_outside_union(samples, line_width_m, valley_sharp)`
  - 本体サンプル列を Shapely `Polygon` 化し `polygon.buffer(W, join_style=…)` で外側帯を作る
  - `outer.difference(body)` でアニュラスポリゴンを得る
  - 戻り値は `(outer_ring, holes)`
- `_build_band_mesh_from_union(mesh, outer_ring, holes, z_m)`
  - mapbox-earcut でホール付きポリゴンを三角分割し Blender `Mesh` に流し込む
- `python_deps.ensure_bundled_wheels_on_path()` を import 前に呼ぶこと

参考の幾何方針:
- 帯 = `body_poly.buffer(distance, join_style=…)` − `body_poly`
- join_style 1 = round (谷で丸み), 2 = mitre (谷で鋭角, mitre_limit でクランプ),
  3 = bevel
- 自己交差は buffer 内部で勝手にマージされるので考慮不要

## 実装タスク

### Phase 1: 外側フチ・内側フチを Mesh band 方式に移行

1. `balloon_line_mesh.py` に汎用 stroke API を切り出す:
   ```python
   def build_offset_band_mesh(
       body_samples,           # body curve samples (2D)
       *,
       offset_distance_m,      # 外側 (positive) or 内側 (negative) オフセット距離
       band_width_m,           # 帯の幅 (= line width)
       valley_sharp: bool,
       z_offset_m: float,
   ) -> shapely band polygon
   ```
   現在の `_stroke_band_outside_union` を、offset と band_width を分離した汎用版に拡張。

2. 既存の `balloon_line_mesh` モジュールに以下を追加:
   - `balloon_outer_edge_mesh_<id>`: 外側フチ用 Mesh オブジェクト (本体外側)
   - `balloon_inner_edge_mesh_<id>`: 内側フチ用 Mesh オブジェクト (本体内側)
   - `ensure_balloon_outer_edge_mesh(...)`, `ensure_balloon_inner_edge_mesh(...)`

3. `balloon_curve_object._sync_curve_geometry` で:
   - `append_edge_paths(...)` の呼出を 外側/内側フチ別に分岐
   - 雲フキダシ (Shapely 対応シェイプ) の場合は `append_edge_paths` を呼ばず、
     新 Mesh ベースの `ensure_balloon_*_edge_mesh` を呼ぶ
   - 雲フキダシ以外 (rect, octagon, ellipse 等) は従来通り `append_edge_paths`

4. `balloon_curve_object.cleanup_orphan_balloon_objects` で
   `balloon_outer_edge_mesh_*` `balloon_inner_edge_mesh_*` も
   orphan 削除対象に加える (`balloon_line_mesh.cleanup_orphan_line_meshes`
   と同じパターン)。

### Phase 2: 多重線も Mesh band 方式に移行

1. 多重線は最大 12 リング作るので、リング毎に Mesh オブジェクトを作る:
   `balloon_multi_line_mesh_<id>_ring<N>` (N = 1..11)

2. `append_closed_multi_line_paths(...)` の呼出を雲フキダシ時はバイパスし、
   新 `ensure_balloon_multi_line_meshes(...)` を呼ぶ。

3. 各リングは「本体から `base_distance_mm + spacing_mm * ring_index` 離れた位置に
   `ring_width_mm` の帯」なので、`build_offset_band_mesh(
   offset_distance_m=base+spacing*i+ring_width/2, band_width_m=ring_width)`
   で構築する。
   - 注: 多重線の `direction` は "inside" / "outside" / "both"。各方向ごとに帯を作る。

4. 多重線の thorn (トゲ) 用 valley/peak 幅変化 (
   `thorn_multi_line_valley_width_mm` 等) は雲には適用しないので、まずは
   等幅リング (`_band_loops_for_side` 経路) のみ対応する。トゲは従来コード維持。

### Phase 3: 形状ごとの分岐整理

- Shapely 対応シェイプ: `cloud` (今回追加), 必要に応じ `fluffy`, `thorn-curve`
- 非対応シェイプ (rect, octagon, ellipse, thorn, custom): 従来コード継続

`balloon_line_mesh.py` で形状判定を一元化:
```python
_SHAPELY_SHAPES = {"cloud"}  # 段階的に "fluffy", "thorn-curve" を追加
def uses_shapely_band(entry) -> bool:
    return balloon_shapes.normalize_shape(entry.shape) in _SHAPELY_SHAPES
```

### Phase 4: AI 目視検証

- ヘッドレス Blender で以下のパターンをレンダリングし、PIL で並べて画像保存:
  - 線幅 0.5mm / 5mm × 外側フチ enabled / disabled
  - 線幅 0.5mm / 5mm × 内側フチ enabled / disabled
  - 線幅 0.5mm × 多重線 count=3 (inside / outside / both)
  - 線幅 5mm × 多重線 count=3 (inside / outside / both)
  - 谷を尖らせる ON / OFF
- すべて自己交差なく本体塗りが完全な雲形状で保持されること、
  各帯が独立して重ならず描画されることを確認。

### Phase 5: チェンジログ・version bump・コミット

- `__init__.py` と `blender_manifest.toml` を 0.6.103 に bump (PATCH +0.0.1)
- `CHANGELOG.md` の先頭にエントリ追加
- 既存の `wheels/README.md` (shapely / mapbox-earcut の追加分は v0.6.102 で記載済) はそのまま
- コミットメッセージ: `[claude] feat: フキダシのフチ・多重線も Shapely buffer 方式に統一`

## 検証時の注意

- メモリルール「起動中 Blender へインプレース再読込しない」に注意。
  Blender プロセスの再起動が必要。
- Blender バージョンは 5.1.1 (`C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`)。
- AI 目視は Read tool で PNG をインライン表示してユーザーに見せる。
- 線が太いとき (5mm 超) の自己交差テストを必ず含めること (今回の主目的)。

## 関連ファイル

- `utils/balloon_line_mesh.py` — 参考実装 + 新 API 追加先
- `utils/balloon_multiline_curve.py` — 既存のフチ・多重線実装 (旧方式)。雲時は呼ばない経路にする
- `utils/balloon_curve_object.py` — `_sync_curve_geometry` で旧/新方式の分岐を入れる
- `utils/python_deps.py` — Shapely / mapbox-earcut の import 前提 (既存)
- `core/balloon.py` — `cloud_valley_sharp` 等のプロパティ定義 (既存)
- `panels/balloon_panel.py`, `operators/layer_detail_op.py` — UI (既存、変更不要)

## ベース commit / バージョン

- Base: 2f71a2e (v0.6.102)
- Target: v0.6.103 以降
