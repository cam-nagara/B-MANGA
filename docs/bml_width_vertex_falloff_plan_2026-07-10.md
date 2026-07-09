# B-MANGA Liner 線幅: 頂点単位の遠近減衰スライダー追加 + numpy高速化 修正計画書（フェーズ1）

- 作成日: 2026-07-10（Claude Fable 5 調査セッションで作成。実装は本計画書に従うこと）
- 実行者想定: Codex（開発セッション）。推奨実行モデル: 中位モデル（GPT-5標準 / Claude Sonnet 5級）
- 状態: 未着手
- 続編: `docs/bml_width_speedup_phase2_plan_2026-07-10.md`（本計画の実装完了が前提。実測とさらなる高速化）
- 本計画書は行番号非依存。参照はすべてファイル名＋関数名・プロパティ名で行う。行番号が現物とずれても関数名で探すこと。

## 1. 背景（ユーザー要望 2026-07-10）

- 「線幅の均一化（オブジェクト単位）」（`use_camera_compensation`）は、オブジェクトの**バウンディングボックス中心の1点**（`camera_comp._reference_point_for_mesh`）でカメラ距離を測るため、巨大・広範囲に広がるオブジェクトが混在すると線幅がまちまちになる。
- ユーザーの要望は2点:
  1. 線幅のカメラ距離計測を**頂点単位**にして、巨大オブジェクトでも破綻しないようにする。
  2. 画面上の線幅を均一にするだけでなく、**「カメラからの距離に比例して線幅が細くなっていく」挙動も選べる**ようにする。しかも極力速く。
- 既存の「線幅の均一化（頂点単位）」（`use_uniform_line_width`）に頂点単位計測の仕組みは既にある。本計画は、そこへ「遠近減衰の強さ」スライダーを追加し、同時に計算経路をnumpy化して高速化する。

## 2. 確定仕様

### 2.1 新プロパティ

`core.py` の `bmanga_line_settings` プロパティグループへ追加:

- 名前: `line_width_distance_falloff`（FloatProperty）
- UI表示名: 「遠近減衰の強さ」
- description: 「0: どの距離でも画面上の線幅を均一にする / 1: カメラから遠いほど距離に比例して細くする / 2: 遠近を誇張する」
- `default=0.0, min=0.0, max=2.0`
- `update=` コールバックは既存の `_on_camera_influence_changed` と同型（`_defer_line_setting(self, context, "line_width_distance_falloff")` を呼ぶだけ）。即時反映せず「反映」ボタン待ちにする既存方式を守ること。

### 2.2 数式（頂点単位モード時）

記号: `target_px` = 線幅mm × DPI ÷ 25.4（既存 `camera_comp._target_pixels`）、`d_ref` = 「線幅基準距離 (m)」（既存 `line_width_reference_distance`、0.001でクランプ）、`p` = 遠近減衰の強さ。

頂点 v のカメラ空間座標 `local = camera.matrix_world.inverted() @ world_co` に対して:

- 距離 `d(v)`:
  - PERSP / ORTHO: `d(v) = max(0.001, -local.z)`（カメラ前方深度）
  - PANO（魚眼含む）: `d(v) = max(0.001, |local|)`（放射距離。魚眼は真横・後方も写るため）
- 1pxあたりワールド幅 `wpp(v)`（既存 `_world_per_pixel` と同一式を維持）:
  - PERSP: `2 · d(v) · tan(angle_y/2) / render_height`
  - PANO: `d(v) × _panorama_radians_per_pixel(...)`
  - ORTHO: `ortho_scale / render_width`（頂点非依存の定数）
- **目標ワールド線幅**: `W(v) = target_px × wpp(v) × (d_ref / d(v))^p`
- 適用は既存フローのまま: `thickness = max(W)`、頂点グループweight `= W(v)/max(W)`（さらに既存スタイルウェイトと乗算）。

意味: 画面上の線幅が `指定線幅 × (d_ref/d)^p` になる。`p=0` で完全均一（現行の頂点単位均一化と**完全一致**）、`p=1` で距離に正確に比例して細く、`p=2` で誇張。ORTHOカメラでも `d(v)` に基づく減衰は効かせる（遠近感の演出として意図どおり）。

### 2.3 UI

`panels.py` の `_draw_camera` 内、`col.prop(settings, "use_uniform_line_width")` の直下に追加:

```python
sub = col.column(align=True)
sub.enabled = settings.use_uniform_line_width
sub.prop(settings, "line_width_distance_falloff")
```

スライダーは「線幅の均一化（頂点単位）」がオンのときだけ有効。オブジェクト単位モード（`use_camera_compensation`）には効かせない（現状維持）。

### 2.4 互換性

- 既定値 0.0 のため、既存 .blend・既存ユーザーの挙動は**一切変わらない**こと（p=0 で現行出力とバイナリ一致レベルの同値。数値誤差は許容 1e-6 相対）。
- 後方互換の特別処理は不要（プロパティが無い旧ファイルは既定値 0.0 になる）。

### 2.5 スコープ外（変更しないもの）

- 「線幅の均一化（オブジェクト単位）」（`_compensated_width_for_mesh`）と既定モード（`_apply_reference_target_line_width`）のロジック。
- バンプ線（画像空間のコンポジター処理。頂点概念がない）。
- 板ポリの境界チューブ（`BML_SheetOutline`）: `sync_sheet_outline_width` が単一スカラーをGNソケットへ入れる方式のため、**頂点単位ウェイト自体が現行でも非対応**。本計画でも対象外とし、既知の制約として §7 のテストで assert しないこと（フェーズ2計画書に任意項目として記載あり）。
- 魚眼・パノラマの換算式そのもの（v0.3.175 で確定済み。式の分岐・定数を**フォークして複製しない**こと。§3.1 参照）。
- 反映タイミングの仕組み（「反映」ボタン方式・frame_change_post ハンドラ）は現状維持。

## 3. 実装ステップ

### 3.0 前提の把握（着手前に必読するコード）

- `addons/b_manga_line/camera_comp.py`: `_world_per_pixel` / `_panorama_radians_per_pixel` / `_target_pixels` / `_uniform_widths_for_mesh` / `_apply_uniform_target_line_width` / `_apply_targeted_line_widths` / `_line_width_reference_distance`
- `addons/b_manga_line/vertex_analysis.py`: `compute_and_apply_weights` / `multiply_width_weights` / `_write_vertex_group_weights` / `width_group_name`
- `addons/b_manga_line/core.py`: `_defer_line_setting` / `_on_camera_influence_changed` / `bmanga_line_settings` のプロパティ定義群
- `addons/b_manga_line/update_state.py`: `_VISUAL_PROPS` / `targets_for_property`
- `addons/b_manga_line/panels.py`: `_draw_camera`

### 3.1 新モジュール `addons/b_manga_line/width_math.py`（numpy頂点一括計算）

巨大ファイル抑制ルール（camera_comp.py は既に1000行超）のため、新規計算コードは新モジュールへ置く。

内容:

1. **スカラー係数ヘルパー**: カメラ型ごとの「wpp = 係数 × 距離」の係数（PERSP: `2·tan(angle_y/2)/render_height`、PANO: `_panorama_radians_per_pixel` の戻り値、ORTHO: 定数wpp）を返す関数。**魚眼の式は `camera_comp._panorama_radians_per_pixel` を import して使い、式を複製しない**（単一の変換元を守る。CHANGELOG v0.3.175 の経緯参照）。既存のスカラー版 `_world_per_pixel`（基準点1点の計算に使用中）は残してよいが、係数部分を共通ヘルパー経由に揃え、二重定義を作らないこと。
2. **numpy一括計算関数**（例: `vertex_widths_and_depths(scene, camera, obj, width_m) -> (np.ndarray, np.ndarray)`）:

```python
n = len(mesh.vertices)
co = np.empty(n * 3, dtype=np.float64)
mesh.vertices.foreach_get("co", co)
co = co.reshape(n, 3)
mw = np.array(obj.matrix_world, dtype=np.float64)          # 4x4
world = co @ mw[:3, :3].T + mw[:3, 3]
inv = np.array(camera.matrix_world.inverted(), dtype=np.float64)  # ★逆行列はここで1回だけ
local = world @ inv[:3, :3].T + inv[:3, 3]
# PERSP/ORTHO: depth = np.maximum(0.001, -local[:, 2])
# PANO:        depth = np.maximum(0.001, np.linalg.norm(local, axis=1))
# widths = target_px * wpp(depth)   （§2.2の式）
```

- 現行の `_uniform_widths_for_mesh` は**頂点ごとに** `camera.matrix_world.inverted()` を再計算している（`_world_per_pixel` 内）。これが主要ボトルネックの一つ。numpy版では上のとおり1回に外出しする。
- 対象メッシュは現行同様 `obj.data`（元メッシュ）。評価後メッシュは使わない。
- numpy はBlender同梱のものを使う（アドオンは現在numpy未使用のため import 追加は本モジュールが初）。

3. **減衰適用**: `W = widths * (d_ref / depth) ** p`（`p == 0.0` なら乗算をスキップして完全同値を保証）。

### 3.2 `camera_comp.py` の改修

- `_apply_uniform_target_line_width` を改修: `_uniform_widths_for_mesh`（純Pythonループ）の呼び出しを width_math のnumpy版に置き換え、`settings.line_width_distance_falloff` を §2.2 の式で適用してから、従来どおり max 正規化 → 頂点グループ書き込み → `_apply_target_width(obj, target, max_width)`。
- 旧 `_uniform_widths_for_mesh` は他に呼び出し元がなければ削除（残すならnumpy版へ委譲させ、二重実装を残さない）。

### 3.3 `vertex_analysis.py` の書き込み経路最適化（読み戻し排除・書き込み1回化）

現状の無駄（調査で確認済み）:

- `multiply_width_weights` は、直前に `compute_and_apply_weights` が書いたばかりの頂点グループを `vg.weight(i)` ＋ try/except で**1頂点ずつ読み戻して**乗算し、**もう一度全頂点を書き直す**（書き込み2回・読み戻しN回・例外処理N回）。呼び出し元は `camera_comp._apply_uniform_target_line_width` の1箇所のみ（確認済み）。

改修:

1. `compute_and_apply_weights` を「計算」と「書き込み」に分離する（例: `compute_weights(obj, settings, target) -> list[float] | None` を新設し、既存関数はそれを呼んで書き込むだけにする）。スタイルウェイトは元々Python配列として組み立ててから書いており、読み戻しなしで取り出せる。
2. `_apply_uniform_target_line_width` 側では: スタイルウェイト配列（無ければ全1.0）× 正規化幅配列 を numpy で乗算し、`_write_vertex_group_weights` を**1回だけ**呼ぶ。
3. `multiply_width_weights` は不要になるため削除する（他の呼び出し元が無いことを再確認のこと。`_verify/` 配下の過去スナップショットのヒットは無視してよい）。
4. **注意**: `_prepare_style_weights` は書き込み以外の副作用（スタイル未使用時の `clear_width_weights`、outline時の `outline_width_attribute.ensure/remove`、Solidifyの `mod.vertex_group` 設定）を持つ。均一化オフの既存経路（`_apply_reference_target_line_width` / `_apply_compensated_target_line_width` → `_apply_target_style_weights`）を**壊さないこと**。分離後も両経路で従来と同じ副作用が起きることを確認する。
5. `_write_vertex_group_weights` の `vg.add([i], w, "REPLACE")` ループ自体は本フェーズでは維持してよい（Solidify用アウトラインは実頂点グループが必須）。さらなる高速化はフェーズ2計画書で扱う。

### 3.4 `core.py` / `update_state.py`（プロパティ登録と反映機構）

- `core.py`: §2.1 のプロパティを追加。update コールバックは `_on_camera_influence_changed` と同じパターンで新設（`_propagating` ガード → `_defer_line_setting`）。
- `update_state.py`: `_VISUAL_PROPS` に `"line_width_distance_falloff"` を追加。ターゲットはプレフィックス無しのため既存フォールバック（`_GEOMETRY_LINE_TARGETS` = outline/inner/intersection/selection）で正しい。これにより「反映」ボタンの軽量経路（`reflect.dispatch_target` → `batch_update.refresh_target_visuals` → `camera_comp.refresh_objects`）へ自動的に載る。

### 3.5 `panels.py`

§2.3 のとおりスライダーを追加。

### 3.6 バージョン・記録

- アドオンのバージョンをプロジェクト慣例に従い1つ上げ、`CHANGELOG.md` に「頂点単位モードへ『遠近減衰の強さ』を追加＋線幅計算のnumpy高速化」を追記（既存エントリの書式に合わせる）。
- **コミット・プッシュはユーザーの明示指示があるまで行わない**（グローバル規約。行う場合は `[codex] feat: ...` 形式）。

## 4. テスト（新設・必須）

新規: `test/blender_b_manga_line_width_falloff_check.py`。既存の `test/blender_b_manga_line_fisheye_width_check.py` の構成（素の assert、`main()` で register→検証→finally unregister、PASS を print、ヘッドレス実行）を踏襲する。

実行方法（プロジェクト慣例）:

```bash
'/c/Program Files/Blender Foundation/Blender 5.1/blender.exe' --factory-startup --background --python test/blender_b_manga_line_width_falloff_check.py
```

（`--factory-startup` を付けないとヘッドレスがハングする既知事象あり。AGENT_INBOX 参照）

検証ケース:

1. **p=0 後方互換**: 奥行き2m〜60mに広がる細分化平面＋PERSPカメラで頂点単位モードを反映し、書き込まれた頂点グループweightと `thickness` から復元した `W(v)` が「画面上均一」（`W(v)/wpp(d_v)` が全頂点で一定、相対誤差1e-4以内）であること。
2. **p=1 距離比例**: 同シーンで `W(v)` が全頂点でほぼ一定（ワールド幅一定＝画面上は距離に反比例して細くなる）であること。
3. **p=2 誇張**: 距離が2倍の頂点の画面上幅が 1/4 になること（式で直接検証）。
4. **カメラ型網羅**: PERSP / ORTHO / PANO(FISHEYE_EQUIDISTANT) の3型で、numpy一括計算の結果が「mathutilsで1頂点ずつ素朴に計算した参照実装」（テスト内にローカル定義）と相対誤差1e-6で一致すること。魚眼の既存回帰テスト `blender_b_manga_line_fisheye_width_check.py` も**引き続きPASSすること**（式を共通化した影響の検知）。
5. **ライン種**: アウトライン（Solidify + VG_LINE_WIDTH）と稜谷線（GN + 稜谷線用頂点グループ）の両経路で 1〜2 が成立すること。
6. **スタイルウェイト併用**: 中間頂点の線幅調整（width controls）を有効にした状態で、最終weightが「スタイルウェイト×正規化幅」の積になっていること（読み戻し排除後の等価性確認）。
7. **性能スモーク**: 10万頂点メッシュで頂点単位反映の所要時間を計測して print する（assertはしない。数値はフェーズ2の実測の基準値になるため、出力をそのまま残す）。

テスト追加後、プロジェクト `AGENTS.md` §6.1 のテスト一覧表へ1行追記する（登録必須のランナーは無いが、表の陳腐化を防ぐため）。

## 5. エッジケース・落とし穴

- `d(v)` は必ず 0.001 でクランプ（カメラ背後・カメラ位置の頂点で発散させない）。
- 頂点数0のメッシュ、`bmanga_line_settings` 無し、対象モディファイア無し（選択線等の early return）は既存の分岐を維持。
- 頂点グループweightは0〜1にクランプされる。`W(v)/max(W)` は構造上 (0,1] なので問題ないが、`max(W)` の 1e-9 下限クランプ（既存）を維持。
- 非一様スケールのオブジェクト: ワールド幅→モディファイア値の換算は既存 `scale_utils.modifier_thickness_for_world_width` 経由を変えない。
- 稜谷線キャッシュ（`inner_line_cache`）: キャッシュはVGのweightを焼き込む方式。反映フローでキャッシュが再構築され新しいweightを拾うこと（既存の均一化モードと同じ経路）を手動確認項目に含める。
- Blender UI値ルール: スライダー値・線幅mm・距離mはすべてUI表記値として扱う（内部変換を挟まない）。

## 6. 受け入れ条件（すべて満たすこと）

1. §4 のテストが全ケースPASS（新設＋魚眼回帰）。
2. p=0 で既存挙動と完全同値（§4-1）。既定値0のため既存ユーザー影響なし。
3. 10万頂点メッシュの頂点単位反映が、改修前の実装より高速（§4-7 の計測値を改修前後で比較し、計測結果を計画書実行報告に数値で含める。目標: 従来比5倍以上、絶対値1秒未満。未達でも実装は完了とし、数値をフェーズ2実測へ引き継ぐ）。
4. UIに「遠近減衰の強さ」が表示され、頂点単位モードオフ時はグレーアウト。変更→「反映」で線幅が変わり、「反映」を押すまでは変わらない。
5. ruff 等の静的検査（導入済みであれば）がグリーン。
6. CHANGELOG・バージョン更新済み。コミットはユーザー指示待ち。

## 7. 実行後にユーザーへ伝えること（チャット報告に含める）

- 巨大・分散オブジェクト混在シーンでの使い方: 「線幅の均一化（頂点単位）」をオン＋「遠近減衰の強さ」で均一（0）〜距離比例（1）〜誇張（2）を調整。
- 板ポリの境界チューブとバンプ線は頂点単位減衰の対象外（既存制約）。
- 計測した性能数値（改修前後）。
- 続きの一言: 「『線幅フェーズ2の計画書を実行して』でさらなる高速化（実測→属性fast-path→GN検討）に進めます」。
