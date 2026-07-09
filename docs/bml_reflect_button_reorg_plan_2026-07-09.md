# B-MANGA Liner ボタン再編（「反映」統合）計画書

作成日: 2026-07-09（Claude Code 調査セッション）
推奨実行モデル: Sonnet 5（計画書に基づく実装）
関連: AGENT_INBOX.md 仕様シグナル 2026-07-09（ボタン再編の提案）、同 P2「線種別『作成』ボタンに新規適用時の付帯処理が無い」（本計画で解消）

## 0. ユーザー確定条件（2026-07-09 明言・確定仕様）

1. 各線種の「作成」「更新」ボタンを **「反映」1つに統合**する（無ければ作成、有れば更新）。
2. 「反映」を押したとき**変更待ちが何も無ければ何もしない**。ただし**メッシュ編集後であれば作り直す**。
3. メインパネルの「ラインを適用」「ラインを削除」ボタンを**廃止**。「すべてのラインを更新」を**「すべてのラインを反映」に改名**し、「ラインを適用」が担っていた処理（AOVパス準備・「ラインのみを表示」中の白色反映・シーン反映・待ち解除）を**引き継ぐ**。その**直下に「すべてのラインを削除」**を配置。
4. **削除系のボタンはすべて、押下時に確認ダイアログを表示**する（すべてのラインを削除／中間頂点用サブディビジョンの削除／プリセットの削除）。
5. **後方互換性は一切考慮不要**（旧オペレーターID・旧プロパティの温存は不要。テストは書き換える）。

## 1. 背景（現状の実態）

- 線種別「作成」（`bmanga_line.update_target` → `presets.apply_line_settings`）は既に反映型:
  有効なら作成/再構築、チェックOFFなら削除、を1オペレーターで行う（重い経路。稜谷線・交差線は再検出を伴う）。
- 線種別「更新」（`bmanga_line.update_visual_target` → `batch_update.refresh_target_visuals`）は
  **作成済みモディファイアがあるオブジェクトのみ**を対象に線幅・色・オフセット・カーブ等の見た目だけを更新する（軽い経路。未作成の線は作らない）。
- 設定変更時は `update_state`（update_state.py）が**設定項目ごとに「作成待ち(create)」「更新待ち(visual)」を自動区別**して
  オブジェクトのカスタムプロパティに記録済み。パネル下部の「作成待ち/更新待ち」表示もこれ。
  → **統合後の軽い/重いの自動振り分けはこの既存記録をそのまま使える。**
- 「ラインを適用」（`bmanga_line.apply`）だけが持つ付帯処理:
  `outline_setup.ensure_aov_passes` / `presets._refresh_after_line_settings(sources=…)` /
  `presets._reflect_applied_display_settings`（「ラインのみを表示」ON中の白色反映。v0.3.168/0.3.172 の修正を含む）/ 全線種の待ち解除。
  線種別「作成」はこれらを（交差線の一部を除き）呼んでいない = 既知の穴（AGENT_INBOX P2）。
- 性能上の制約（確定済み設計意図 2026-07-07/08）: 線幅・色いじりのループは軽いまま維持すること。
  交差線の再検出（`refresh_scene_intersections`）は tokyo0004 級で十秒台かかるため、不要時に走らせない。

## 2. 新しいボタン構成

### メインパネル（BMANGA_LINE_PT_main / _draw_actions）
| 現状 | 変更後 |
|---|---|
| ラインを適用（大ボタン） | **削除** |
| 選択をロック / ロック解除 | 変更なし |
| リンク素材のラインを補正 / リンク素材へ選択設定を上書き | 変更なし |
| ラインを削除 | **削除**（ライン設定パネルへ移動・改名） |
| 選択情報・「作成待ち/更新待ち」表示 | 表示文言を「**反映待ち: <線種>…**」へ統一 |

### ライン設定パネル（BMANGA_LINE_PT_line_settings / _draw_line_settings）上から:
1. **「すべてのラインを反映」**（旧「すべてのラインを更新」。scale_y=1.2 維持）
2. **「すべてのラインを削除」**（旧「ラインを削除」を移動・改名。確認ダイアログ付き）
3. 「詳細設定」（変更なし）
4. 中間頂点用サブディビジョン行: **[チェックボックス「中間頂点用サブディビジョンを自動設定」][反映][削除]**
   （旧「作成/更新/削除」の3ボタンから「作成」を廃止。作成=チェックON+反映で等価。削除は確認ダイアログ付き）
5. 各線種セクション（アウトライン/稜谷線/交差線/選択線）: 見出し右のボタンを **「反映」1つ**に（旧「作成」「更新」を統合）
6. バンプ線セクション: 「更新」→**「反映」**に改名のみ（元々「作成」概念なし）

### プリセットパネル
- 「削除」（`bmanga_line.preset_delete`）に確認ダイアログを追加（対象プリセット名を表示）。

## 3. オペレーター再編（後方互換不要のため旧IDは廃止）

| 旧 | 新 | 備考 |
|---|---|---|
| `bmanga_line.update_target` + `bmanga_line.update_visual_target` | `bmanga_line.reflect_target`（target: EnumProperty） | §4のディスパッチ |
| `bmanga_line.apply` + `bmanga_line.update_all_visual_targets` | `bmanga_line.reflect_all` | §5 |
| `bmanga_line.remove` | `bmanga_line.remove_all` | 挙動は現行踏襲+確認ダイアログ |
| `bmanga_line.update_auto_subdivision`（CREATE/UPDATE/DELETE） | 同ID・action=（REFLECT/DELETE） | REFLECT=旧UPDATE。DELETEに確認ダイアログ |
| `bmanga_line.preset_delete` | 同ID | 確認ダイアログ追加のみ |

- poll: `reflect_target` / `reflect_all` は「選択にメッシュがある」こと（未適用オブジェクトへの新規作成があるため）。
  `remove_all` は「選択に `has_line` がある」こと（現行踏襲）。
- ロック挙動は現行踏襲: 反映系・削除系とも `selection.updatable_mesh_objects()` でロック中を除外し、除外件数をレポートに添える。
  「すべてのラインを反映」はロック中でも押下可（ロック外の選択には効く）という現行のグレーアウト仕様を維持。
- テスト用の逃げ道として `reflect_target` / `reflect_all` に `force_rebuild: BoolProperty(default=False, options={'SKIP_SAVE'})`
  を持たせる（True なら待ち状態・指紋に関係なく重い経路）。UIには出さない。

## 4. 「反映」のディスパッチ仕様（線種×オブジェクトごと）

新モジュール `addons/b_manga_line/reflect.py` に判定と実行を実装する（batch_update.py は1394行で追記禁止水準のため新設。1関数50行以内に分割）。

対象オブジェクト・対象線種ごとに、上から順に最初に該当した経路を実行する:

| # | 条件 | 経路 |
|---|---|---|
| 1 | バンプ線（target=bump） | 常に現行の軽い同期（`batch_update._update_bump_lines` 相当）。指紋・待ち判定は使わない |
| 2 | 線種有効 かつ モディファイア無し | **重い経路**（新規作成。作成範囲外スキップ等は `apply_line_settings` の現行判定に委ねる） |
| 3 | 線種無効 かつ モディファイア有り | **重い経路**（`apply_line_settings` が削除を行う。確認ダイアログは不要 — 削除「ボタン」ではなく反映の結果のため） |
| 4 | 作成待ち(create)印あり | **重い経路**（再構築） |
| 5 | 指紋不一致 または 指紋未保存（§6） | **重い経路**（メッシュ編集後の作り直し） |
| 6 | 更新待ち(visual)印のみあり | **軽い経路**（`refresh_target_visuals`） |
| 7 | 上記いずれも無し | **何もしない**（件数を「変更なし」としてレポート） |

- 重い経路 = `presets.apply_line_settings(obj, context, line_targets=(target,))` を軸に、現行 `update_target` の
  交差線特例（重い経路を実行した交差線ソースがある場合のみ `refresh_scene_intersections(sources=…)` → `camera_comp.refresh_objects` → `ensure_aov_passes`）を踏襲。
- 軽い経路 = 現行 `refresh_target_visuals(target, …)` そのまま。
- 待ち印の解除: 重い経路 = その線種の create/visual 両方を解除。軽い経路 = visual のみ解除（現行どおり）。
- 重い経路が1件でも走った場合の付帯処理（線種別「反映」にも適用 — AGENT_INBOX P2 の穴をここで塞ぐ）:
  `ensure_aov_passes` と `_reflect_applied_display_settings(該当オブジェクト)` を実行する。
- レポート例: 「アウトライン: 作成/再作成 2件・見た目更新 3件・変更なし 5件（ロック中のため1件を除外）」。

## 5. 「すべてのラインを反映」仕様

- 選択中の更新可能メッシュ全部 × 全線種（outline/inner/intersection/selection/bump）に §4 のディスパッチを適用。
- 加えて現行「すべてのラインを更新」が持つ中間頂点用サブディビジョン同期
  （`_update_auto_subdivision` + `_update_match_subsurf_viewport_to_render`、ライン無しオブジェクトへの `_refresh_plain_auto_subdivision` 相当）を維持。
- 「ラインを適用」から引き継ぐ付帯処理（重い経路が1件でも走った場合）:
  1. `outline_setup.ensure_aov_passes(scene)`
  2. `presets._refresh_after_line_settings(context, sources=重い経路を実行したオブジェクト)`
  3. `presets._reflect_applied_display_settings(重い経路を実行したオブジェクト, context)`
     （「ラインのみを表示」ON中に新規反映した素材の白色化。v0.3.168/0.3.172 の修正経路を必ず通す）
  4. 対象オブジェクトの全線種の待ち印解除（重い/軽いの別に応じて §4 のルール）
- 交差線のシーン反映は「交差線で重い経路を実行したソースがある場合」だけ実行（軽い経路のみで済んだ押下では走らせない — 2026-07-07/08 の性能意図の維持）。

## 6. メッシュ編集検出（指紋方式）

新ファイル `addons/b_manga_line/mesh_fingerprint.py` を新設する。イベントハンドラは追加しない（誤検知・自己発火リスクを避け、ボタン押下時にのみ判定する）。

- **指紋の構成要素**（`compute(obj, target)` が文字列を返す）:
  - 頂点数・辺数・面数
  - 頂点座標配列のチェックサム（`foreach_get` でバイト列化 → `zlib.adler32`。数百万頂点でも十数ms程度）
  - 辺の頂点インデックス配列のチェックサム（トポロジ変更検出）
  - 非BMLモディファイアの署名（名前・タイプ・show_render のタプル列。BML管理モディファイア
    — アウトラインSolidify・稜谷線/交差線/選択線GN・自動サブディビジョン — は除外）
  - **交差線（target=intersection）のみ** `matrix_world` を丸めて追加
    （交差線だけは他オブジェクトとの位置関係に依存するため。移動した相手を選択に含めて反映を押せば作り直される。
    他線種はオブジェクトに追従するため transform を含めない — 移動のたびに無駄な再構築をしないため）
- **保存先**: オブジェクトのカスタムプロパティ `bml_reflected_fp_<target>`（線種ごと。
  例: アウトラインだけ反映した直後に稜谷線の分まで「反映済み」扱いにならないよう、共有指紋にはしない）。
- **保存タイミング**: §4 の重い経路が成功した直後に、その線種の指紋を保存する。
- **未保存の扱い**: 指紋が無い = 変更ありとみなして重い経路（既存シーンは初回押下で作り直され、以後は正しく判定される。後方互換不要のため移行処理は書かない）。
- **削除時**: `remove_all` および反映による線種削除の際に該当線種の指紋プロパティを削除する。
- 既知の限界（計画書に明記し、実装時にマニュアル文言にも反映）:
  - 反映は**選択オブジェクト基準**。移動・編集したオブジェクトを選択せずに押しても作り直されない（現行の「作成」ボタンと同じ運用）。
  - シェイプキー・アーマチュア変形などモディファイア評価後だけが変わる編集は座標チェックサムに現れない場合がある
    （非BMLモディファイアの署名変化では検出できる範囲のみ）。必要になったらチェックON→OFF等ではなく `force_rebuild` の
    UI露出を検討する（今回はスコープ外）。

## 7. 確認ダイアログ仕様（削除系ボタン共通）

- 対象: `remove_all`（すべてのラインを削除）/ `update_auto_subdivision` の DELETE / `preset_delete`。
- `invoke()` で `context.window_manager.invoke_confirm(self, event, title=…, message=…, confirm_text="削除", icon='WARNING')` を使う
  （Blender 4.1以降の拡張シグネチャ。実装時に同梱APIリファレンス `data/api` で 5.1 の引数名を確認し、無い引数は落とす）。
- メッセージには対象件数を入れる。例:
  - remove_all: 「選択中の N オブジェクトからすべてのライン（アウトライン・稜谷線・交差線・選択線・自動サブディビジョン）を削除します。（ロック中の M 件は対象外）」
  - サブディビジョン削除: 「選択中の N オブジェクトから中間頂点用サブディビジョンを削除します。」
  - preset_delete: 「プリセット『<名前>』を削除します。」
- ヘッドレステストは `EXEC_DEFAULT`（execute直呼び）なのでダイアログの影響を受けない。
  ダイアログ表示そのものは実機GUIでの手動確認項目とする（§11）。

## 8. 待ち表示の統一と update_state の整理

- `update_state.pending_label()` の表示を「作成待ち: …／更新待ち: …」から **「反映待ち: <線種>…」に統一**
  （create/visual の内部区別はディスパッチに必要なので**保持**。表示だけ統合）。
- 後方互換不要のため、旧プロパティ `bml_pending_line_update_targets`（PROP_PENDING_TARGETS、legacy読み取り専用）への
  参照を削除してよい（`pending_create_targets` の legacy 合算を除去）。

## 9. 実装手順（フェーズ順）

1. **Phase 1 — 新モジュールと新オペレーター**
   - `mesh_fingerprint.py` 新設（§6）。
   - `reflect.py` 新設（§4のディスパッチ。§5の全反映用エントリも `reflect.py` に置き、operators.py からは薄く呼ぶ）。
   - operators.py: `reflect_target` / `reflect_all` / `remove_all` を追加し、
     `apply` / `update_target` / `update_visual_target` / `update_all_visual_targets` を削除。
     `update_auto_subdivision` の action を（REFLECT/DELETE）へ変更。
   - panels.py: §2 のボタン構成へ変更（メインパネルから適用/削除を除去、ライン設定パネルの並べ替え、各セクション「反映」1つ化、バンプ線改名）。
2. **Phase 2 — 確認ダイアログ**（§7。preset_delete は presets.py 内）。
3. **Phase 3 — 表示統一・整理**（§8）。
4. **Phase 4 — テスト改修**（§10）。
5. **Phase 5 — 記録**: CHANGELOG追記、B-MANGA Liner / B-MANGA のバージョン更新（プロジェクト規約どおり）、
   AGENT_INBOX の P2「線種別『作成』ボタンに新規適用時の付帯処理が無い」を完了節へ移動、
   P1「ユーザーマニュアル陳腐化」項へ本UI変更を追記。コミットはユーザー指示があってから。

## 10. テスト改修

旧IDを使用するテスト（2026-07-09 grep 結果。`_verify/` と `addons/b_manga_line/_test_batch3.py` は対象外 —
後者は未追跡の残骸で別P2で移動/削除判断待ち。旧ID廃止によりそのままでは動かなくなる旨を同P2に追記する）:

- 機械的置換で済む見込み（旧ID→新ID。`update_target`/`update_visual_target` → `reflect_target`、
  `apply`/`update_all_visual_targets` → `reflect_all`、`remove` → `remove_all`）:
  `batch_apply_refresh_check` / `auto_smooth_save_guard_check` / `offset_controls_check` /
  `outline_enable_with_intersections_check` / `preset_visibility_check` / `separate_line_colors_check` /
  `sheet_and_proxy_follow_check` / `sheet_mesh_exclusion_check` / `toggle_matrix_check` /
  `midpoint_jitter_check` / `generated_update_scope_check` / `register_reenable_check` / `tokyo0004_large_audit`
- 置換に加えて**期待値の見直しが必要**（旧「作成=毎回再構築」「更新=見た目のみ」の2ボタン前提を書いている）:
  - `control_update_scope_check` / `uniform_width_check`: 「同じ反映を2回押す」型の箇所は、
    設定変更（=待ち印）を挟むか `force_rebuild=True` を渡す形へ書き換える。
  - `ui_controls_check`: パネル上のボタン列挙アサーション（作成/更新の2ボタン・update_all の存在）を §2 の新構成へ更新。
  - `settings_lock_check`: `apply`→`reflect_all` 置換 + ロック除外レポートの検証は現行踏襲。
  - `update_all_targets_check`: `reflect_all` の poll 変更（メッシュ選択があれば True になる）に合わせて期待値修正。
  - `auto_subdivision_check`: action="CREATE" 呼び出しを「プロパティON + REFLECT」へ、"UPDATE" を "REFLECT" へ書き換え。
  - `bump_line_check`: `update_visual_target(target="bump")` → `reflect_target(target="bump")`。
- **新規テスト** `test/blender_b_manga_line_reflect_dispatch_check.py` を追加し、§4 の表を直接検証する:
  1. 未適用+有効 → 反映で作成される
  2. 色だけ変更（visual待ち）→ 反映が軽い経路（稜谷線GNツリーの再構築が起きないことをツリー参照の同一性等で確認）
  3. 待ち無し → 反映で何も起きない（モディファイア・指紋・レポートが不変）
  4. 待ち無し+メッシュ編集（頂点移動/トポロジ変更/非BLMモディファイア追加）→ 反映で再構築され指紋が更新される
  5. チェックOFF+反映 → 線種削除・指紋プロパティ削除
  6. 「ラインのみを表示」ON中に未適用オブジェクトへ reflect_all → 新素材が白色出力へ切り替わる（apply付帯処理の引き継ぎ確認。`_verify/2026-07-09_line_only_restore/` のケース7を移植・昇格してよい）
  7. 交差線: 軽い経路のみの押下で `refresh_scene_intersections` が呼ばれない（呼び出しフックまたは実行時間で確認）

## 11. 検証方法

- ヘッドレス実機: `"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --factory-startup --python-exit-code 1 --python test/<対象>.py`
  （`--factory-startup` 必須 — AGENT_INBOX P2「ヘッドレス実機テストが --factory-startup なしでハングする」参照）
- 対象: §10 の改修テスト全部 + 新規 reflect_dispatch_check + 既存回帰
  （`inner_intersection_material_order_check` / `shared_tree_regeneration_check` / `settings_lock_check` / `bump_line_check` / `fisheye_width_check`）。全PASSが完了条件。
- `python -m compileall addons/b_manga_line` 相当の構文チェック。
- 旧ID残存チェック: `bmanga_line.(apply|update_target|update_visual_target|update_all_visual_targets|remove)\b` が
  実行コード・test/ に残っていないこと（docs/CHANGELOG の歴史記録は除く）。
- **手動確認（ユーザーまたはGUI+MCPスクリーンショット）**: ①3つの削除ボタンで確認ダイアログが出て、キャンセルで何も消えない
  ②ボタン配置が §2 どおり ③tokyo0004 級で「色変更→反映」が体感即時、「メッシュ編集→反映」で作り直しが走る。

## 12. 完了条件

1. §2 のボタン構成が実機UIで確認できる（旧ボタンが存在しない）。
2. §4/§5/§6 の挙動が新規テストで機械的に検証されている（§10-新規テストの7ケース全PASS）。
3. 削除系3ボタンすべてに確認ダイアログ（手動確認）。
4. §11 のヘッドレステスト全PASS・旧ID残存ゼロ。
5. CHANGELOG・バージョン・AGENT_INBOX（P2完了移動+マニュアル項追記）の記録完了。

## 13. スコープ外（本計画ではやらない）

- `force_rebuild` のUI露出（シェイプキー等の検出限界への対応。要望が出たら別途）。
- リンク素材系ボタン・ロック仕様・プリセット適用フローの変更。
- マニュアル本文の全面更新（既存P1で追跡）。
- `_test_batch3.py` の移動/削除（既存P2で追跡。本計画で旧IDが廃止され同ファイルは動作しなくなる旨だけ追記）。
