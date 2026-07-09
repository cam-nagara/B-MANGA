# B-MANGA Liner 全体高速化 フェーズ3 修正計画書

- 作成日: 2026-07-10（Claude Fable 5 調査セッション。4系統の並行コード監査＋裏取り済み）
- 実行者想定: Codex（開発セッション）。推奨実行モデル: 中位モデル（GPT-5標準 / Claude Sonnet 5級）
- 状態: 未着手
- 前提: フェーズ1（`docs/bml_width_vertex_falloff_plan_2026-07-10.md`）・フェーズ2ステップB（生成線幅の属性化、v0.3.178/179）は実装済み。線幅の再計算・書き込みは解決済みで本計画の対象外。
- 目標: tokyo0004級（数百オブジェクト・素材994・三角ポリゴンスープ）で「すべてのラインを反映」を十秒台、**変更なしの反映（no-op）を1秒未満**、フレーム再生の常駐コスト削減。
- 本計画書は行番号非依存。参照はファイル名＋関数名。
- **進め方の原則: ステップごとに「計測→実装→回帰テスト→再計測」。** ベンチは `_verify/2026-07-10_bml_width_perf/` の流儀を踏襲し、`_verify/2026-07-10_bml_phase3_perf/` に新設（§7）。

## 0. 重複提案の禁止（既実施の対策）

CHANGELOG v0.3.140〜179 で既に実施済み: 設定変更とライン生成の分離（明示更新方式）、交差線・稜谷線の保存済み線方式、線種別更新の分離、「ラインのみを表示」の軽量素材差し替え化、mesh_fingerprint による反映スキップ、線幅のnumpy化・属性化。これらを作り直さないこと。以下は**その網から漏れている経路**だけを扱う。

---

## ステップ1: 交差線 — 無変更でもフル再検出が走る問題（効果: 高・最優先）

対象: `intersection_lines.py`（後半の「保存済み線方式」の定義群が実効コード。前半の同名関数群は死コード、§6参照）/ `intersection_cache.py` / `batch_update.py` / `plane_filter.py`

### 1a. 無変更スキップ（検出結果の再利用を機能させる）

- 問題: `refresh_scene_intersections` → `apply_intersection_lines` → `intersection_cache.apply_cached_intersection_lines` → `build_cached_segments` の内側に変更検知が無く、呼ばれたら無条件でBVH構築＋評価メッシュ抽出＋三角形交差計算をやり直す。`reflect._classify` の指紋ガードは「reflectがそのオブジェクトのheavy経路を起動するか」しか守らず、`reflect_all` 終盤の `presets._refresh_after_line_settings(sources=all_heavy)` や下記1bの経路からは素通しになる。2026-07-08のユーザー方針「重い交差検出と軽い線表示を分離し、検出結果を保存・再利用する」の**再利用側が実質未完成**。
- 修正: `apply_intersection_lines`（または `build_cached_segments` 呼び出し直前）で、①ソースの交差用指紋（`mesh_fingerprint` の intersection 変種＝matrix・作成範囲込み）②**対象ペア相手側それぞれの指紋**（相手メッシュ編集・移動でも再検出が必要）③ペア構成（対象オブジェクト名の集合）の3点を前回値と比較し、全て一致すれば検出をスキップして既存キャッシュを維持する。指紋の保存はキャッシュ書き込み成功時のみ。
- テスト必須: (i) 無変更で2回目の反映が検出スキップになる (ii) ソース移動・相手移動・相手メッシュ編集・作成範囲設定変更・ペア増減の各ケースで正しく再検出される。

### 1b. `batch_update._update_intersections` のシーン全体refreshをやめる

- 問題: 交差線系プロパティ変更のたび `intersection_lines.refresh_scene_intersections(context.scene)` を**sources指定なし＝全シーン**で呼ぶ（`batch_update.py` の `_update_intersections`。裏取り済み）。対象外オブジェクトの検出まで巻き込む。
- 修正: `refresh_scene_intersections(context.scene, sources=objects)` のように対象限定で呼ぶ（関数が sources 引数を持つ形へ。全体整合が必要な既存経路は従来どおり全シーンでよい）。1a のスキップが入れば実害は減るが、呼び出し規模の限定は別途正しい。

### 1c. `_auto_targets` の O(N²×頂点数) 解消

- 問題: ソース1個ごとに `scene.objects` 全走査。さらに候補ごとに `_source_owns_intersection_pair` 内で `plane_filter.is_sheet_mesh(source)` を再計算し、`is_sheet_mesh` → `_mesh_signature` → `_local_dimensions` が**キャッシュヒット判定の前に毎回、素のPythonで全頂点走査**する。数百オブジェクトで O(N²·V)。
- 修正: ①`is_sheet_mesh(source)` はソースごとに1回だけ計算してループへ渡す ②`_mesh_signature` を軽量化（全頂点走査をやめ、頂点数・辺数・`bound_box`・`matrix_world` 由来の署名へ。キャッシュ精度が落ちる懸念があれば「軽量署名が一致→従来署名は計算しない、軽量署名が不一致→従来計算で確定」の2段構え）③候補絞り込みにワールドAABB重なり判定を先行させる（`_bounds_overlap` 相当を候補ループの先頭へ）。

### 1d. `cleanup_orphan_cache_objects` の毎回全走査をやめる

- 問題: `refresh_scene_intersections` の末尾で毎回無条件に `bpy.data.objects` を2パス全走査。
- 修正: キャッシュコレクション（`_cache_collection`）配下のオブジェクトだけを走査する。加えて、この refresh でキャッシュの削除・再作成が発生しなかった場合はスキップ。

### 1e. `_evaluated_mesh_data` のnumpy化とメモ化

- 問題: 頂点変換 `[transform @ v.co for v in mesh.vertices]`・三角形抽出・法線計算が素のPythonループ。さらに同一ターゲットが複数ソースの相手になる場合、ソースごとに `evaluated_get()+to_mesh()` から全部やり直す。
- 修正: `foreach_get("co", ...)`＋numpy行列変換（`width_math.py` の実装パターンを流用）へ置換。`refresh_scene_intersections` 1回の実行内で「オブジェクト→抽出済みデータ」の辞書を持ち回して使い回す（呼び出しをまたぐ永続キャッシュにはしない。ライフサイクル管理が複雑になるため）。

---

## ステップ2: 反映の定常コスト — 「何もしない反映」を本当に軽くする（効果: 中〜高）

対象: `reflect.py` / `selection.py` / `presets.py` / `mesh_fingerprint.py`

### 2a. `_classify` の指紋計算をオブジェクト単位で共有

- 問題: `reflect._classify` はターゲットごとに `mesh_fingerprint.matches(obj, target, scene=scene)` を呼ぶ（裏取り済み）。頂点・辺チェックサム部分は outline/inner/selection で完全に同一データなのに、4線種有効なら同一メッシュを最大4回全走査する。無風の反映でもこのコストが必ず出る。
- 修正: 1回の `dispatch_target`/`reflect_all` 実行内で、オブジェクトごとの基礎チェックサムを1回だけ計算して4ターゲットで使い回す（`mesh_fingerprint.compute` に計算済み基礎値を渡せる引数を足すか、reflect側に per-dispatch キャッシュ辞書を持つ）。intersection の追加情報（matrix・作成範囲）は従来どおり別途付与。

### 2b. `selection.updatable_mesh_objects` の O(N²) 除去

- 問題: `if obj not in items:` のリスト線形探索。`context.selected_objects` は重複を持たないため無意味なコスト。
- 修正: チェックを削除するか set で判定。数行の修正。

### 2c. プリセット適用の差分ゲート

- 問題: `presets.BMANGA_LINE_OT_preset_apply_selected.execute` が値の差分を見ずに全オブジェクトへ `update_state.mark_pending(obj)`（全5種・kind="create"）を打つため、同じプリセットの再適用でも次回反映が**確実に全ターゲットheavy**へ落ちる。
- 修正: `copy_preset_to_settings` で実際に値が変わったターゲットにのみ `mark_pending` を打つ（コピー関数が変更有無を返す形へ）。「有効なのに未作成」のケースは `_classify` の `enabled and not reflected_before → heavy` が既に拾うため、pending を打たなくても新規作成は担保される（裏取り済み）。この前提が成り立つことをテストで確認すること。

### 2d. `apply_line_settings` のターゲット非依存処理を1回に

- 問題: `reflect_all` はターゲットごとに `apply_line_settings(obj, ..., line_targets=(target,))` を呼ぶが、内部の `subdivision_lod.ensure_auto_subdivision`（bmesh四角面化を含む）・`camera_comp.store_unit_reference`・`modifier_stack.reorder_line_modifiers` はターゲットに依存せず、オブジェクト×有効ターゲット数（最大4回）重複実行される。
- 修正: これら共通処理を `apply_line_settings` から分離し、reflect のオブジェクトループでターゲット処理の後に1回だけ実行する形へ。

---

## ステップ3: アウトライン・素材系（効果: 高〜中）

対象: `outline_setup.py` / `outline_fast_update.py` / `presets.py`

### 3a. `_mesh_boundary_edge_count` のキャッシュ・重複排除・numpy化

- 問題: 全ポリゴン×全辺キーの純Pythonループ（タプル生成＋辞書）で境界辺数を数える。①light経路（色変更だけの反映等）でも `update_modifier_rim` 経由で毎回走る ②heavy経路の `apply_outline` では `_configure_solidify_shape`→`_needs_boundary_outline_tube`→（ラインのみ表示中は）`_configure_line_only_solidify_shape` で**同一メッシュに最大3回**走る。三角ポリゴンスープに最も刺さる残置Pythonループ。
- 修正: ①境界辺数をオブジェクトカスタムプロパティへキャッシュし、mesh_fingerprint（2aの基礎チェックサム）が不変なら再計算しない ②1回の apply_outline 内では計算結果を引数で持ち回して1回に ③計算自体を `mesh.edges`/`mesh.polygons` の `foreach_get` ＋numpy集計へ置換（`edge_keys` のPython辞書集計をやめ、ループ内ポリゴン辺参照数のカウントへ）。

### 3b. `_ensure_surface_mask_aov` の既設定スキップ

- 問題: 素材へ `PROP_SURFACE_AOV_MASK = True` を書き込むのに**読み取りに一切使わず**、毎回ノードツリーを線形走査してAOVノード有無を判定。素材が多数オブジェクトで共有される tokyo0004（994素材）では同じ素材を何度も走査。
- 修正: 冒頭で `mat.get(PROP_SURFACE_AOV_MASK)` が真ならノード走査をスキップ。素材のノードを外部で削除された場合の自己修復が必要なら、反映1回につき素材ごとに1回だけ走査するセッション内 set で重複を防ぐ。

### 3c. `outline_fast_update` フォールバック頻度の計測（実装ではなく計測）

- 問題: 既存アウトラインの軽量更新 `update_existing_outline` は素材スロット健全性チェック等が1つでも落ちるとフル `apply_outline` へフォールバックする。tokyo0004 の複雑素材構成でどの程度フル経路へ落ちているか不明。
- 修正: フォールバック発生時にオブジェクト名と失敗理由を print するデバッグ計測を §7 ベンチに組み込み、結果を報告。高頻度なら原因別の対策を**別計画として提案**（このステップでは直さない）。

---

## ステップ4: 稜谷線・選択線の heavy 経路（効果: 高〜中）

対象: `inner_line_cache.py` / `inner_line_chains.py` / `inner_line_repair.py` / `selection_lines.py` / `vertex_analysis.py`

### 4a. `_evaluated_inner_mesh_data` の `view_layer.update()` バッチ化

- 問題: 稜谷線の heavy 経路はオブジェクトごとに `_disabled_line_modifiers` → `bpy.context.view_layer.update()` → `evaluated_get/to_mesh` を実行。reflect側は「view_layer更新はターゲットごとに1回」を意図しているのに、ここでオブジェクト数だけ**シーン全体の依存グラフ再評価**が走る。数百オブジェクトの初回一括反映で支配的になり得る。
- 修正: reflect の heavy 対象一覧を先に確定し、inner_line_cache へ「一括プリフェッチ」APIを追加する: 全対象のラインモディファイア表示を一括で無効化 → `view_layer.update()` を1回 → 各オブジェクトの `to_mesh` を連続実行 → 一括で復元。既存のオブジェクト単位APIは互換のため残し、内部でプリフェッチ済みデータがあれば使う形。復元の例外安全（try/finally）を必ず維持。

### 4b. 全辺ループの定数無駄の除去

- `inner_line_cache._selected_edge_graph` / `inner_line_chains._collect_selected_graph` / `_edge_attr_value`: `mesh.attributes.get(名前)` を**辺ごとにループ内で毎回**呼んでいる。ループ外で1回解決して渡す。
- `inner_line_chains.update_chain_id_attribute` の全件 `-1` リセット: `attr.data.foreach_set("value", ...)` へ置換。
- `selection_lines.sync_freestyle_edge_attribute`: 全辺Pythonコピー＋辺ごとの属性名解決。`foreach_get`/`foreach_set` の一括転送へ置換。この関数は light 経路（スライダー調整の反映）からも毎回無条件に呼ばれるため、転送前に「元属性の内容が前回と同じならスキップ」の軽量ゲート（チェックサム）も入れる。

### 4c. `_write_cache_mesh` の3属性書き込みを `foreach_set` 化

- 法線・幅・元頂点インデックスを1件ずつ書いている。フェーズ2で `vertex_analysis._write_float_point_attribute` に前例あり。同じパターンへ。

### 4d. `_sync_cache_widths_from_owner` / `stored_width_weight` の属性解決を外へ

- キャッシュ点ごとに `obj.data.attributes.get(group_name)` を再解決している。ループ外で1回解決し、最終的に `foreach_get`/`foreach_set` の一括転送へ。light 経路（太さスライダー反映）の頻出コスト。

### 4e. `vertex_analysis._calc_midpoint_factor` の鋭角判定キャッシュ

- `_build_sharp_graph` と `_hard_endpoint_anchors` が同じ `_edge_is_sharp`（`calc_face_angle()`）判定を実質3回近く重複計算。辺indexキーの配列に1回計算して両者で参照。
- さらに: 中間頂点の線幅調整が有効な場合、この計算はカメラ非依存なのに参照モードの毎フレーム線幅更新（`camera_comp._apply_reference_line_width` → `_prepare_style_weights`）から毎回呼ばれる。ステップ5cのキャッシュと併せて解消する。

### 4f. `inner_line_repair` のロード時全件再計算スキップ

- 問題: ファイルを開くたび、稜谷線モディファイアを持つ全オブジェクトで `update_chain_id_attribute`（全辺bmesh走査）を無条件再実行。chain_id 属性は .blend に永続化されているのに毎回作り直している。大規模シーンのロード時間を押し上げる。
- 修正: chain_id 属性が存在し、辺数が一致し、ツリー世代ラベルが現行なら再計算をスキップ。不一致時のみ従来どおり修復。

---

## ステップ5: 常駐コスト（フレーム再生・アニメーション時。効果: 中）

対象: `camera_comp.py`（ハンドラ部）/ `auto_smooth_guard.py` / `subdivision_lod.py`

### 5a. `_on_frame_change` の2パス統合

- 問題: `_update_camera_compensation` と `_update_visibility` がそれぞれ `scene.objects` 全走査から始まり、毎フレーム2パス走る。
- 修正: 対象抽出（`_line_width_objects` 相当）を1回にまとめ、同じループ内で線幅と可視性を処理する。挙動は不変のまま走査回数だけ半減。

### 5b. `_update_visibility` の bound_box 二重変換統合

- 問題: カリング用の `_object_world_sphere`（bound_box 8頂点のワールド変換）と距離制限用の `object_distance_from_camera`（同じ8頂点を再変換）が独立実行される。
- 修正: 8頂点のワールド座標を1回計算し、境界球と最短距離の両方をそこから導出する共通ヘルパーへ。

### 5c. 参照モードのスタイルウェイト毎フレーム再計算をキャッシュ

- 問題: `_apply_reference_line_width` は `has_width_controls` が真なら毎フレーム `compute_and_apply_weights`（4eのbmesh計算を含む）を無条件再実行する。この計算はメッシュと設定にのみ依存し、カメラには依存しない。
- 修正: （2aの基礎チェックサム＋関連設定値タプル）をキーに前回結果の有効性を判定し、不変ならウェイト再計算・再書き込みをスキップ。**頂点単位均一化モード（`use_uniform_line_width`）はカメラ依存のため対象外**（従来どおり毎回計算。ただしフェーズ1でnumpy化済みなので軽い）。

### 5d. 低優先の小掃除

- `auto_smooth_guard`: save_pre と save_post が同一の全オブジェクト走査を実行。save_pre で異常0件なら save_post をスキップ。
- `subdivision_lod`: 自分の `mod[sid]` 書き込みが depsgraph_update_post を再誘発してもう1周走る。書き込み直後の同一オブジェクト通知を無視する短命ガード（`camera_comp._updating` と同パターン）。

---

## ステップ6: 死コードの隔離（性能ではなく、監査・保守コストの削減）

- `intersection_lines.py` は同名関数群（`apply_intersection_lines` / `refresh_scene_intersections` 等）が**前半（SHELL/Boolean/SDF方式・約570行）と後半（保存済み線方式）で二重定義**されており、Pythonの解決順で後半だけが実効。`intersection_shell.py` の検出・プロキシ生成ロジックも `register()` 未呼び出しで実質死コード（既存インボックスP2「同名関数二重定義によるデッドコード」と同件）。
- ただし `intersection_shell.sync_proxy_subdivision_for_target` は `subdivision_lod` から**現役で呼ばれ**、オブジェクトごとに `bpy.data.objects` 全走査する。プロキシ機構自体が死んでいるなら呼び出しごと削除、生かすなら owner→proxy の逆引きを呼び出しバッチごとに1回だけ構築する。
- 対応: 前半の死コード群を削除（またはファイル分離して未登録化）し、削除後に交差線の全回帰テストを実行。**このステップだけは挙動変更リスクが低い一方で差分が大きいため、独立コミットに分けること。**

---

## 7. 計測（各ステップの前後で必ず実行）

`_verify/2026-07-10_bml_phase3_perf/bench_phase3.py` を新設（`bench_width_refresh.py` の流儀を踏襲、`--factory-startup --background` で実行）。シーンはスクリプト内で生成:

- **S1 無風反映**: 300オブジェクト（各5千頂点・4線種有効・反映済み）で、変更なしの「すべてのラインを反映」の所要時間。目標: 1秒未満。
- **S2 交差線無変更**: S1の状態で交差線設定を同値更新→反映。検出スキップが効いて0.1秒台になること。
- **S3 初回一括反映**: 100オブジェクトの初回「すべてのラインを反映」（1a〜4aの効果測定。view_layer.update回数も計測ログに出す）。
- **S4 フレーム送り**: S1シーンで frame_change を100回発火させ、1フレームあたりの平均ハンドラ時間。
- **S5 プリセット再適用**: 同一プリセットを300オブジェクトへ再適用→反映（2cの効果測定）。

結果は `results.md` に「ステップ適用前 / 各ステップ適用後」の表で追記。**改善が確認できないステップは差し戻して原因を報告**（効果のない複雑化を残さない）。

## 8. テスト・受け入れ条件

1. 既存回帰テストが全てPASS: 線幅系（falloff / fisheye）、反映系（reflect / register_reenable ※既知の既存赤1件は除く）、交差線・稜谷線の素材順チェック等、`test/blender_b_manga_line_*_check.py` のうち本改修に関係する範囲を各ステップ後に実行。
2. 新設テスト: 1a の再検出条件網羅（無変更スキップ／ソース移動・相手移動・相手編集・範囲変更・ペア増減で再検出）、2c の「未作成＋pendingなしでも新規作成される」確認。
3. §7 のS1〜S5計測値が results.md に前後比較で記録され、S1が1秒未満・S2が検出スキップ動作。
4. 一括修正の禁止: ステップ単位（最低でも1〜6の粒度）でコミットを分ける。コミット実行自体はユーザー指示待ち（`[codex]` 規約）。
5. CHANGELOG・バージョン更新（ステップまとめて1エントリでよい）。
6. AGENT_INBOX.md の該当P1項目へ結果を追記（完了 or 残課題）。

## 9. 実行後にユーザーへ伝えること

- S1〜S5の前後数値（表）。
- 「無変更の反映」「交差線」「初回一括」「フレーム再生」のそれぞれが体感でどう変わるか1行ずつ。
- 3c（アウトライン高速更新のフォールバック頻度）の計測結果と、高頻度だった場合の次の一手の提案。
