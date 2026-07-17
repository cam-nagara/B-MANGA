# Blender 5.2 LTS 移行 修正計画書（2026-07-18・調査セッション作成）

## 実施結果（2026-07-18・開発セッション・Sonnet 5）

**Phase A〜C すべて完了。** ユーザーは manifest 判断①「(a) 3つとも 5.2.0 へ引き上げ」を選択。

- **想定外の追加破壊を実機で発見**: 5.2 では GN ソケット識別子だけでなく、**Modifier上の任意のカスタムプロパティ全般が読み書き不能**（`TypeError: this type doesn't support IDProperties`。GN以外の SUBSURF 等でも同様と実機確認）。§0 の記述より対象が広く、`_OWNER_KEY` 等の世代タグ・チェーン角度キャッシュ・遅延表示フラグも影響。best-effort書込＋既存の代替判定手段へのフォールバックで解決。特に `intersection_lines.py::_queue_deferred_viewport_modifier` は旧コードが書込失敗時に `return` していたため **5.2でビューポート遅延表示機構が丸ごと無効化される実害**を検出・修正。
- **`mod.properties.inputs[id]` の返り値の形が5.2実機で2種類存在**（§0 3.の想定より複雑）: RNA構造体（`.value`属性、Float/Object/Material等で観測）と素の`IDPropertyGroup`（`["value"]`添字、Bool/Int/Color/String/Vectorで観測、条件は特定できず非決定的挙動の可能性）の両方が実際に出現するため、互換ヘルパーは両方を試す実装とした（実機で機能検証済み: GeometryNodeTransformのScale入力を実際に動かして確認）。
- テストヘルパー側は当初想定の16ファイルを超え、最終的に本番以外で以下を修正: b_manga_line系テスト12本、B-MANGA本体連携テスト（`blender_geometry_nodes_bridge_check.py`）1本、balloon手動検証スクリプト3本、計16本。
- **検証結果**: `addons/b_manga_line/` の自動テスト75本中74本がBlender 5.2.0 LTS実機でグリーン（残り1本 `blender_b_manga_line_tokyo0004_large_audit` は `--phase` 引数必須の既存仕様で本migrationと無関係）。Phase A/Bで触れた全ファイルをBlender 5.1.2実機でも再検証し退行なし。
- **未実施**（低優先・別セッション向けとして AGENT_INBOX.md 完了記録に記載）: §6 の残るGUI手動確認、EEVEE見た目変化の個別承認、tokyo0004級実作品の通し目視。

---

- 目的: B-MANGA / B-MANGA Liner / B-MANGA Render を Blender 5.2 LTS（2026-07-14 リリース、2028年7月までサポート）で正しく動作させる。グローバルルールは 2026-07-18 に「アドオン開発の基準バージョン = 5.2 LTS」へ改定済み。
- 実行環境: `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`（5.2.0 LTS, build 2026-07-14, インストール確認済み）。旧 5.1 も併存しており互換確認に使用可。
- 推奨実行モデル: Sonnet 5（原因・置換仕様とも本計画で確定済みのため）。Phase A-2/A-4 の一括置換部分はサブエージェント（Haiku級）併用可。
- 本計画は行番号非依存（関数名・処理内容で特定する）。行番号が書かれている場合は 2026-07-18 時点の参考値。

## 0. 実機確認済みの事実（このセッションで検証済み・再調査不要）

1. **Blender 5.2 の同梱 Python は 3.13.13**（5.1 は 3.13.9）。`wheels/` の cp313 ホイール（Pillow・PyPSD 等）はそのまま互換。**wheels の作り直しは不要。**
2. **GN モディファイアへの旧形式アクセスは 5.2 で完全廃止**。`mod[identifier] = value` は `TypeError: bpy_struct[key] = val: id properties not supported for this type`、`mod.get(identifier)` / `mod[identifier]` 読取も `TypeError: this type doesn't support IDProperties` で失敗する（5.2.0 実機で確認）。B-MANGA 側は大半が `except Exception` で握りつぶすため、**クラッシュではなく「設定値が黙って反映されない」形で壊れる**。
3. **新 API の正確な形**（5.2.0 実機で動作確認済み）:
   - 書込/読取: `item = mod.properties.inputs["<identifier>"]`（属性アクセス `mod.properties.inputs.Socket_2` も可）→ `item.value = 5.0`
   - 属性入力: `item.type = "ATTRIBUTE"` / `item.attribute_name = "attr"`（値に戻すときは `item.type = "VALUE"`）
   - 出力属性名: `mod.properties.outputs["<identifier>"].attribute_name = "attr"`
   - Object/Material 等のポインタ型も `item.value = obj` で代入可
   - 識別子（`Socket_2` 等）の体系自体は 5.1 と同一。`group.interface.items_tree` の `item.identifier` も従来どおり使える
   - 5.1 には `mod.properties` が存在しない（`AttributeError`）→ **`hasattr(mod, "properties")` で新旧を判別できる**
4. **Compare / Random Value ノードの型別ソケットが 5.2 で統合**（5.2.0 実機でソケット一覧を確認済み）:
   - FunctionNodeCompare: 旧 `A`/`B`(FLOAT), `A_INT`/`B_INT`(INT), `A_VEC3`/`B_VEC3`, `A_COL`/`B_COL`, `A_STR`/`B_STR`, `C`, `Angle`, `Epsilon`（全型ぶん常設・enabled切替）→ **新: `A`/`B` の2本のみ**（data_type/operation に応じて動的生成。INT の EQUAL では Epsilon 等は存在しない）。出力 `Result` は不変。
   - FunctionNodeRandomValue: 旧 `Min`/`Max`(VEC), `Min_001`/`Max_001`(FLOAT), `Min_002`/`Max_002`(INT), `Probability`, 出力 `Value`〜`Value_003` → **新: `Min`/`Max`/`ID`/`Seed`、出力 `Value` の1組のみ**。
   - 影響の型: 旧識別子指定 → `KeyError`。旧インデックス指定（INT用の `inputs[2]`/`inputs[3]` 等）→ `IndexError` または**別ソケットへの誤代入（サイレント誤動作）**。
5. **5.2 実機テストの現状**（`--background --factory-startup --python-exit-code 1` で実施）:
   - 合格: `blender_b_manga_line_register_reenable_check` / `blender_effect_line_end_fill_check` / `blender_startup_shortcut_repair_timer_check` / `blender_b_manga_line_bump_line_check`
   - 失敗①: `blender_addon_enable_lazy_effect_nodes_check` — `utils/geometry_nodes_bridge.py` の `_socket_by_identifier(compare.inputs, "B_INT")` が `KeyError: 'B_INT'`（`_focus_line_count_socket` → `_compare_int_socket` 経由、効果線GNグループ構築が失敗）
   - 失敗②: `blender_b_manga_line_auto_subdivision_check` — `addons/b_manga_line/outline_local_subdivision.py` の `_silhouette_curve` で Compare(INT) の `inputs[3]` が `IndexError`（size 2）
6. **Smooth by Angle**（Blender組込GN）のソケット識別子 `Input_0`/`Input_1`/`Socket_1` は 5.2 でも不変。ただし読取手段（`mod.get`）が上記2の理由で全滅するため `auto_smooth_guard.py` は要修正。
7. 削除API（`paint.eraser_brush*`・sculpt automasking・`template_palette` の color 引数・`active_asset_library` のindex対応）への依存は**コード全走査で0件**。IDPropertyへの保存は全箇所 JSON 文字列化済みでネスト上限(1024)の影響なし。評価メッシュの名前依存も0件（キャッシュは全て `as_pointer()` キー）。レンダーエンジン識別子は `command_runner.py::_resolve_engine_identifier` が enum 動的解決済みで壊れない。

## 1. 修正方針（コア判断）

- **互換ヘルパー方式を採用する**（推奨）。5.2 専用に書き捨てず、次の2つの小さな共通関数に集約する:
  - `set_gn_modifier_input(mod, identifier, value)` / `get_gn_modifier_input(mod, identifier, default)` / `set_gn_modifier_output_attribute(mod, identifier, name)`: `hasattr(mod, "properties")` なら新API、なければ旧 `mod[identifier]` 形式。→ 移行期間中も 5.1 でテスト可能、`blender_version_min` の判断（§5）と独立に進められる。
  - `compare_socket(node, name)` 等のソケット解決: **ソケットは「identifier直指定」でも「index指定」でもなく、`enabled` なソケットを表示名（"A"/"B"/"Min"/"Max"/"ID"/"Seed"/"Value"）で解決する**。5.1 では型別ソケット（例: INT時の enabled な "A" = `A_INT`）に、5.2 では統合ソケットにそのまま一致するため、両版で同一コードが動く。
- 例外の握りつぶしはこの機会に緩めない・増やさない（既存P1「except pass 約1,350箇所」とは切り離す）。ただし新設する共通ヘルパー内では失敗をロガーへ出す。

## 2. Phase A: 実行時破壊の解消（最優先・機能停止バグ）

対象は「効果線・フキダシ・アウトライン・内側線・交差線」の生成系ほぼ全部。

### A-1. GNモディファイア入力書込の中枢（B-MANGA本体）
- `utils/geometry_nodes_bridge.py` の `_set_modifier_value()`（`modifier[identifier] = value` を型分岐で行う唯一の低レベル関数）と `ensure_modifier()`（約180ソケットをループ適用する唯一の同期経路）を §1 の互換ヘルパーへ置換。
- ここを直せば `utils/effect_line_object.py` / `operators/work_op.py` 側は無修正で吸収される（直接 `mod[]` に触れていない）。

### A-2. Compare/RandomValue の識別子ハードコード（B-MANGA本体）
- `utils/geometry_nodes_bridge.py`: `_socket_by_identifier()` の呼出元 `_shape_compare` / `_compare_int_socket` / `_compare_float_socket` / `_compare_float_sockets`（識別子 "A_INT"/"B_INT"/"A"/"B" をハードコード）→ §1 の enabled+名前ベース解決へ。
- `utils/geometry_nodes_functional.py`: `_compare_int()`（同パターン。フキダシの尻尾・本体形状・白抜き線判定などから多数呼出）→ 同上。

### A-3. b_manga_line の GNモディファイア読み書きヘルパー群（13ファイル・重複実装の統合）
- 新規共通モジュール（例: `addons/b_manga_line/gn_socket_compat.py`、1000行以内・責務単位）を作り、以下の各ファイルの自前ヘルパー（`_find_socket_id` + `mod[sid]` 読み書き）を置換:
  - `inner_lines.py`（`apply_inner_lines` / `update_parameters` / `_modifier_socket_float` / `_modifier_socket_bool`）
  - `intersection_lines.py`（`_set_modifier_parameters` / `_set_multi_modifier_parameters`）
  - `intersection_shell.py`（`_set_modifier_input_if_changed` / `_modifier_target_collection`）
  - `intersection_cache.py` / `inner_line_cache.py`（各 `_set_modifier_input_if_changed`）
  - `inner_line_repair.py`（`_modifier_float` / `_modifier_bool`）
  - `outline_setup.py`（`ensure_sheet_outline` / `sync_sheet_outline_width` / `sheet_outline_world_width` / `_set_node_input_if_changed` / `_modifier_target_collection`）
  - `outline_width_attribute.py`（`ensure_outline_width_attribute`）
  - `outline_local_subdivision.py`（`_set_input` / `sync_scene_cameras`。※ `mod[_OWNER_KEY]` は通常カスタムプロパティなので対象外）
  - `outline_fast_update.py`（`_update_existing_sheet_outline`）
  - `subdivision_lod.py`（`sync_generated_line_subdivision` の現在値読取）
  - `auto_smooth_guard.py`（`_remember_modifier_settings` / `_repair_modifier`。識別子定数 `Input_1`/`Socket_1` は5.2でも有効、アクセス方法だけ変更）
- 本番コード合計 約25〜30関数。

### A-4. Compare/RandomValue のインデックス指定（サイレント誤動作型・約25箇所以上）
- `FunctionNodeCompare` 生成直後の `inputs[0]`〜`inputs[3]` 番号指定を §1 の名前ベース解決へ置換:
  - `inner_line_cache.py`（smoothing判定）/ `curve_smoothing_nodes.py`（shallow_turn）/ `inner_lines.py`（幅カーブ・中点係数・素材判定・エッジ角度・チェーン選択の6箇所）/ `intersection_cache.py` / `intersection_lines.py`（4箇所）/ `intersection_shell_node_helpers.py`（12箇所）/ `intersection_shell.py`（分岐次数判定）/ `outline_width_attribute.py` / `outline_local_subdivision.py`（カメラ正対判定・境界面判定 `_silhouette_curve` ← 実機で IndexError 確認済み）
- `FunctionNodeRandomValue`: `inner_lines.py::_add_jittered_midpoint_factor` と `intersection_shell_node_helpers.py::_add_jittered_midpoint_factor_from_output`（`inputs[2]`/`inputs[3]`=旧INT位置のMin/Max、`outputs[1]`=旧Float Value）→ 名前 "Min"/"Max"/"Value" ベースへ。
- 既存の名前指定箇所（`outline_setup.py` の `inputs["A"]`/`inputs["B"]` 等）は 5.2 でもそのまま動くはずだが、Phase B のテストで確認する。

### A-5. ノードグループの再生成トリガー
- B-MANGA の `BManga_GN_EffectLine` 系と b_manga_line の生成ツリーは、旧 Blender で保存された .blend 内に**旧構成のノードグループが焼き付いている**。修正後、既存作品を開いた際に古いツリーが残らないか確認し、必要なら既存の再構築経路（バージョンタグ／`ensure_node_group` の作り直し判定）で再生成されることをテストで保証する。
- リリースノートの互換注意「GNツール入りアセットは 5.2 で開いて再保存が必要」も、アセットバンドル（`utils/asset_bundle.py` 経由で配布する .blend）があれば対象に含める。

## 3. Phase B: テスト側の追随と5.2での回帰確認

1. テストヘルパーの同型パターン修正（少なくとも16ファイル。代表: `test/blender_geometry_nodes_bridge_check.py` の `return modifier[identifier]`、`blender_b_manga_line_intersection_shell_method_check.py`、`blender_b_manga_line_uniform_width_check.py`、`blender_b_manga_line_offset_controls_check.py`、`blender_balloon_*_check.py` 系）。本番と同じ共通ヘルパーを test 側からも import する形へ寄せ、重複定義を増やさない。
2. 失敗確認済みの2本（`blender_addon_enable_lazy_effect_nodes_check` / `blender_b_manga_line_auto_subdivision_check`）を 5.2 でグリーン化。
3. `test/bmanga_ai_audit_runner.py`（23ケース）を 5.2 実行ファイルで全実行。既知の既存失敗（AGENT_INBOX 記載分）と新規失敗を区別して集計する。
4. **EEVEE の見た目変化に注意**: 5.2 は Screen Space Raytracing / Fast GI / AO の全面改修で「既存シーンが暗くなり得る」と明記されている。ピクセル閾値チェック（約47本）とビジュアル監査で差分が出た場合、(a) 実バグ、(b) 5.2 のレンダリング変化、を切り分け、(b) は基準値を承認の上で更新する。B-MANGA Liner の「ラインのみ表示」（ワールド背景白）とAOVコンポジット経路は必ず実画像で確認する。
5. テスト実行コマンドの既定を 5.2 パスへ変更（`AGENTS.md` の実行例、および `test/` 内にパスをハードコードしている約10箇所）。

## 4. Phase C: バージョン表記・宣言の更新

1. `AGENTS.md`（プロジェクト）: 環境節「Blender: 5.1.2」「Python: 3.11（実態と乖離・既存P2）」・実行コマンド例・検証節の 5.1 表記を 5.2 LTS へ。※「Python: 5.2 同梱版は 3.13.13」に修正（既存の 3.11 誤記も同時解消）。
2. `blender_manifest.toml` ×3（本体 / b_manga_line / b_manga_render）: `blender_version_min` の決定（§5 の判断事項①）と、5.1.1 記載コメントの更新。
3. マニュアル: `docs/B-MANGA_マニュアル.md`（対象: Blender 5.1.2）と `docs/B-MANGA-Render_マニュアル.md`（同 5.1.1）の対象バージョン行、`docs/B-MANGA_AI監査マニフェスト.md` の実行パス。
4. コード内コメント: `preferences.py`（5.1.2 記述）、`addons/b_manga_render/command_ui.py`（アイコン識別子の 5.1 前提コメント → 5.2 で識別子存続を確認して更新）、`addons/b_manga_line/aov_compositor.py`（5.1 API 変更コメント5箇所）。
5. 低優先（任意・一括置換可）: test/ の docstring・コメント内「5.1.1/5.1.2」約86ファイル。機能に影響しないため、Phase A/B 完了後の掃除タスクとしてよい。CHANGELOG・spec-snapshot・過去計画書は履歴資料のため**更新しない**。

## 5. ユーザー判断が必要な事項（着手時に選択肢を提示）

1. **`blender_version_min` の扱い**
   - (a) **3つとも `5.2.0` へ引き上げ（推奨）**: 検証実態と一致。4.3〜5.0 未検証問題（既存P1）も同時解消。
   - (b) 互換ヘルパーを活かし `5.1.0` とする: 移行期に 5.1 ユーザーを許容。ただし 5.1 での回帰テスト維持コストが乗る。
   - (c) `4.3.0` のまま: 非推奨（未検証宣言の既存P1が残り続ける）。
2. **EEVEE 見た目差分の基準更新**: 5.2 のレンダリング変化でビジュアルテスト基準・作品の見え方が変わった場合、どこまでを「新基準」として承認するか（差分画像を提示して個別確認）。

## 6. 残る手動・GUI確認事項（Phase B と並行）

- E キーの消しゴム切替（`operators/shortcut_op.py::BMANGA_OT_toggle_eraser_brush`）: Essentials アセット `Eraser Hard` / `Eraser Stroke` の名前・パスが 5.2 の同梱ブラシ更新後も有効か（5.2 は GP バンドルブラシを更新、旧「Default Eraser」設定は削除済み）。
- 5.2 新設「未保存画像があるファイル保存時の確認ダイアログ」が、B-MANGA の保存トランザクション（ページ/コマ blend の連続保存・ラスターレイヤーのテクスチャペイント画像）と干渉しないか。干渉する場合は新設のユーザープリファレンス（画像保存挙動）での回避を検討。
- サイドバータブ常時表示化・タブのドラッグ切替など 5.2 の UI 挙動変更下で、タブ表示検知（`keymap/keymap.py` の衝突退避・v0.6.543 の遅延自己修復）が従来どおり機能するか（起動修復テスト自体は 5.2 合格済み）。
- インタラクティブコンポジターの実行契機変更が、B-MANGA Liner の AOV「ラインのみ表示」のビューポート反映に影響しないか。
- tokyo0004 級の実作品を 5.2 で開き、効果線・フキダシ・ライン・書き出し（PSD/PNG）を通しで目視確認。

## 7. 実施順序と目安

1. Phase A-1 → A-2（B-MANGA 本体の中枢2ファイル。ここだけで効果線・フキダシは復旧）
2. Phase A-3 → A-4（b_manga_line 13ファイル+共通モジュール新設）
3. Phase A-5 + Phase B（テスト追随・5.2 回帰・見た目差分切り分け）
4. Phase C（表記更新・manifest 判断①をユーザーへ提示）
5. §6 の GUI 確認 → 残件を AGENT_INBOX へ
- 規模感: 本番約25〜30関数+ノード構築約25箇所+テストヘルパー16ファイル。1〜2開発セッション（Sonnet 5）想定。
- 完了条件: 5.2 実機で「失敗2本のグリーン化+audit runner 23ケースの既知失敗以外グリーン+効果線/フキダシ/ライン生成の実画像確認」。
