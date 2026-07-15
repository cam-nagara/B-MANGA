# 実装計画書: B-MANGA Liner「バンプ/ノーマル線」新設 + オブジェクト単位ロック機能

> **実施記録（2026-07-09）**: 本計画は同日実装完了。Part B（ロック）= Liner v0.3.169 / 本体 v0.6.452、Part A（バンプ線）= Liner v0.3.170 / 本体 v0.6.453。Phase A0 検証は `_verify/2026-07-09_bml_bump_line_probe/`、線幅較正は `_verify/2026-07-09_bml_bump_line_calibration/`、tokyo0004 実機確認は `_verify/2026-07-09_bml_bump_line_impl/` に記録。実装時の主な逸脱（バンプ線スタイルはシーン1系統・オフ時はパススルーノード残置 ほか）は CHANGELOG 該当エントリと AGENT_INBOX を参照。

- 作成日: 2026-07-09
- 対象: `addons/b_manga_line/`（B-MANGA Liner）
- 発端: ユーザー依頼（2026-07-09）
  1. 既存のラインとは別に、バンプやNormal（ノーマルマップ）からライン抽出するラインを追加する
  2. オブジェクトごとに、たとえ選択されていても、ラインの設定が更新されたり作り直されたりすることを防ぐロック機能を追加する
- ユーザー承認（2026-07-09 同日回答・確定）: ①方式A（レンダー時合成）で進める ②UI名称は「バンプ線」 ③ロック中もカメラ距離補正は維持
- 推奨実行モデル: Sonnet 5（Part B と Part A Phase A1。Part A Phase A0 の検証結果が想定と大きく食い違った場合は上位モデルで方式再検討）
- 実装順序の推奨: **Part B（ロック）→ Part A（バンプ/ノーマル線）**。ロックの対象列挙ヘルパーを先に整備すれば、新線種は最初からロック対応で実装できる。機能単位で別コミット・別バージョンにする。

## 前提（作業ツリーの現状・2026-07-09時点）

- 最新版: B-MANGA Liner v0.3.167 / B-MANGA 本体 v0.6.450（「すべてのラインを更新」ボタン）。**この差分は未コミット**。本計画の実装前にコミットを済ませること（コミット実行はユーザー指示に従う）。
- 既知の同期漏れ: `addons/b_manga_line/blender_manifest.toml` の `version` が `0.3.166` のまま（`addons/b_manga_line/__init__.py` の bl_info は 0.3.167）。次のコミットで同期すること。
- バージョン更新は毎回4ファイル: ルート `__init__.py` / ルート `blender_manifest.toml` / `addons/b_manga_line/__init__.py` / `addons/b_manga_line/blender_manifest.toml`。CHANGELOG.md へのエントリ追加も毎回必須。
- 遵守すべき確定方針（2026-07-07/08 仕様シグナル・実装済み）:
  - チェックボックス・数値変更は「設定反映＋未更新表示」のみ。重い処理は各線種の「更新」ボタンか「すべてのラインを更新」を押した時だけ走らせる。
  - 新機能もこの「明示更新ボタン方式」に必ず従う。

## 現状アーキテクチャの要点（調査済み・行番号非依存）

- 線種は4種。内部キーは `"outline"` / `"inner"`（UI名「稜谷線」）/ `"intersection"` / `"selection"`。`update_state.LINE_TARGETS` が正本の列挙。
- 設定は `core.py` の `BMangaLineSettings`（PropertyGroup）に一括定義され、`bpy.types.Object.bmanga_line_settings` としてオブジェクト単位に保存（LIBRARY_OVERRIDABLE）。
- パネル（`panels.py`）はアクティブオブジェクトの設定だけを表示し、編集値は `core._propagate` / `_set_prop_on_selected_targets` で選択中の全メッシュへ伝搬される。
- 更新系オペレーター（`operators.py`）:
  - `bmanga_line.apply`（ラインを適用）→ `presets.apply_line_settings`（フル再構築）
  - `bmanga_line.update_target`（作成）/ `bmanga_line.update_visual_target`（更新）→ `batch_update.refresh_target_visuals`
  - `bmanga_line.update_all_visual_targets`（すべてのラインを更新）→ `batch_update.refresh_all_target_visuals`
- 未更新表示は `update_state.py`（`obj["bml_pending_line_create_targets"]` / `..._visual_targets"]`、`pending_label()`）。
- 交差線はペアの一方が所有者（`intersection_lines._source_owns_intersection_pair`、決定的ルール）。再構築はほぼ常に `refresh_scene_intersections`（シーン単位走査）経由。実装の正本はファイル後半の「保存済み線方式」（`intersection_cache.py` へ委譲）で、前半の BOOLEAN/SDF/SHELL 実装は同名再定義により**到達不能なデッドコード**。
- オペレーターの対象列挙は各所で `context.selected_objects` を個別ループしており、**共通の列挙関数が無い**（ロック実装の主要リスク。Part B で共通化する）。
- ノーマルマップ/バンプ（`ShaderNodeNormalMap` / `ShaderNodeBump`）を参照・加工する既存コードは**存在しない**。
- 再利用できる既存基盤:
  - AOV線画合成 `aov_compositor.py`（レンダー時専用・採用済み機能。「線画合成ノードを作成」ボタン → `BML_LineAOVCompositeGroup`）
  - 既存サーフェス材質への非破壊ノード注入の前例 `outline_setup._ensure_surface_mask_aov`（`BML_ObjectMask` AOV追加）
  - mm→px換算 `camera_comp._target_pixels()` / `_get_scene_dpi()`（`scene.bmanga_work.paper.dpi`、既定600dpi。プライベート関数のため再利用時は公開ヘルパー化する）

---

# Part B: オブジェクト単位ロック機能（先行実装）

## B-1. 仕様

**目的**: ロックされたオブジェクトは、選択に含まれていても (1) ライン設定値が書き換わらない (2) ラインが更新・再構築・削除されない。

### ロックが「防ぐ」もの

1. 設定伝搬: 他オブジェクトの編集に巻き込まれた `core._propagate` / `_set_prop_on_selected_targets` / `_defer_line_setting` による値コピー。
2. パネルからの直接編集: アクティブオブジェクトがロック中の場合、ライン設定UI（線種セクション・詳細設定・カメラ補正設定）をグレーアウト（`layout.enabled = False`）。
3. 再構築・更新: `bmanga_line.apply` / `update_target` / `update_visual_target` / `update_all_visual_targets` / `sync_weights` の対象から除外（スキップ件数を `self.report` で通知）。
4. プリセット適用: `bmanga_line.preset_apply_selected` の対象から除外（設定コピー自体を止める）。
5. 削除: `bmanga_line.remove` の対象から除外（ロック解除してから削除する運用。誤爆防止を優先）。
6. 未更新表示: ロック中は `update_state.mark_pending*` を no-op にし、新たな「作成待ち/更新待ち」印を付けない（既存の印は保持し、解除後に再評価される）。
7. 交差線の巻き込み: ペアの**少なくとも一方がロック中**の交差ペアは現状維持（新規作成・削除・再構築・所有者変更のいずれもしない）。`refresh_scene_intersections` / `_refresh_source_intersections` / `_auto_targets` にこの判定を入れる。ロック解除後の次回更新で決定的所有権ルールにより自然に正常化される。

### ロックが「防がない」もの（確定仕様・2026-07-09 ユーザー承認済み）

- カメラ距離補正・遠距離ライン非表示のカメラ追従（`camera_comp._on_frame_change` / `refresh*`）は**ロック中も従来どおり動作させる**（確定）。根拠: 2026-07-07 仕様シグナル「線幅はカメラ距離補正を維持」（固定/再利用対象のラインでも補正は生かす方針）との整合。`camera_comp` へのロック判定追加は不要。
- 表示/非表示トグル（`lines_visible` 等）: 表示切替は破壊的でないため既定ではロック対象外とする。ただし伝搬経路が設定伝搬と同一のため、実装上は「ロック対象プロパティ」から除外するのではなく、**ロック中は全設定の伝搬を止める**単純ルールを優先する（表示だけ変えたい場合は解除してから行う）。仕様の単純さを優先した判断であり、CHANGELOGとマニュアルに明記する。
- オブジェクトの選択自体・「レンダリング範囲内を選択」: 選択は変更ではないため制限しない。

### ロックの粒度・保存場所

- `core.BMangaLineSettings` に `settings_locked: BoolProperty(name="ラインをロック", default=False)` を追加。
  - `update=` コールバックは付けない（伝搬させない。ロックは常にオブジェクト個別の状態）。
  - `presets._SETTING_FIELDS` には**追加しない**（プリセットで配布しない）。
  - LIBRARY_OVERRIDABLE は PropertyGroup 登録側の既存指定で自動的に有効。

## B-2. UI仕様

- `panels.py` の `_draw_actions` 内、「ラインを適用」ボタンの近くに1行追加:
  - オペレーター `bmanga_line.set_settings_lock`（新設、`lock: BoolProperty`）を「ロック」「解除」の2ボタンで並べる。対象は選択中メッシュ全体（ロックボタンは未ロックのみに作用、解除ボタンはロック済みのみに作用）。
  - 選択中のロック状況をラベル表示（例: 「ロック中: 2/5」。全て未ロックなら省略可）。
  - アクティブオブジェクトがロック中の場合、`pending_label` 表示の位置に `LOCKED` アイコン＋「ロック中（設定・更新は変更されません）」を表示。
- ボタン名はユーザー向け動詞句とする（グローバルルール「ユーザー向け文言・用語」）。案: 「選択をロック」/「ロック解除」。
- ライン設定パネル（`BMANGA_LINE_PT_line_settings`）・カメラパネル（`BMANGA_LINE_PT_camera`）・詳細設定ダイアログは、アクティブがロック中なら列全体を `enabled = False` にする。「すべてのラインを更新」ボタンは押下可のまま（ロック外の選択オブジェクトには効くため）。

## B-3. 実装手順

1. `core.py`: `settings_locked` プロパティ追加。判定ヘルパー `core.is_settings_locked(obj)`（settings 未初期化・None 安全）を新設。
2. `selection.py`: 共通列挙ヘルパーを新設（例: `updatable_mesh_objects(context)` — 選択中メッシュのうち `is_settings_locked` でないもの。既存 `selected_mesh_objects` の挙動は変えない）。
3. `core.py` の伝搬経路: `_propagate` / `_set_prop_on_selected_targets` / `_defer_line_setting` の対象ループにロック除外を追加（`settings_locked` プロパティ自身の書き換えは除外しない点に注意 — ロック解除操作が効かなくなる）。
4. `update_state.py`: `mark_pending` / `mark_pending_many` / `mark_property_pending`（と `*_many`）の冒頭でロック中オブジェクトを no-op に。
5. `operators.py`: `BMANGA_LINE_OT_apply` / `update_target` / `update_visual_target` / `update_all_visual_targets` / `sync_weights` / `remove` の対象列挙を手順2のヘルパーへ置換。スキップが発生した場合 `self.report({"INFO"}, ...)` で「ロック中のためn件を除外」を通知。
6. `presets.py`: `BMANGA_LINE_OT_preset_apply_selected` の `_selected_meshes` 相当にロック除外を追加。
7. `batch_update.py`: `refresh_target_visuals` / `refresh_all_target_visuals` の入口（`_line_objects` フィルタ付近）にも防御的にロック除外を追加（オペレーター経由以外の呼び出しへの保険）。
8. `intersection_lines.py`（**後半の生きている実装のみ**に手を入れる）: `_refresh_source_intersections` の冒頭で source がロック中なら現状維持で return。`_auto_targets` で相手がロック中の候補ペアを除外。`refresh_scene_intersections` の削除経路（所有者でなくなったキャッシュの掃除）でもロック中所有者のキャッシュは削除しない。
   - 注意: ファイル前半の BOOLEAN/SDF/SHELL 同名関数群はデッドコードのため**修正不要**。誤って前半だけ直すと無効改修になる。デッドコード削除自体は AGENT_INBOX の既存P2（残置コード整理）の範囲であり本計画ではやらない。
9. `panels.py`: B-2 のUI追加とグレーアウト制御。
10. テスト:
    - 新規 `test/blender_b_manga_line_settings_lock_check.py`: (a) 2オブジェクト選択で片方ロック→アクティブ側の設定変更がロック側へ伝搬しない (b) ロック中に `update_all_visual_targets` 実行→ロック側のモディファイア・素材が変化しない（更新前後でモディファイア設定値を比較） (c) ロック中は pending 印が付かない (d) 交差ペアの片側ロックで `refresh_scene_intersections` を実行してもロック側所有のキャッシュが再構築・削除されない (e) 解除後は通常どおり更新される。
    - `test/blender_b_manga_line_ui_controls_check.py`: `_draw_actions` のボタン列アサーション（`_assert_update_buttons_are_in_line_settings` 等）にロックボタン追加分の期待値を反映。
11. バージョン4ファイル更新（Liner 0.3.168 目安）+ CHANGELOG.md へエントリ追加。`addons/b_manga_line/blender_manifest.toml` の 0.3.166 同期漏れもここで解消。
12. 実機確認: Blender 5.1（`C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`）でテスト実行。tokyo0004 級での動作確認は必須ではない（ロックは軽量な判定のみで重い処理を追加しない）。

## B-4. 完了条件

1. ロック中オブジェクトが、選択に含まれた状態での設定変更・プリセット適用・全更新ボタンで一切変化しないことをテストで確認。
2. 交差ペアの片側ロックで線の二重化・消失が起きない。
3. 追加した判定がフレームハンドラ等の高頻度経路に重い処理を持ち込んでいない（判定はプロパティ読み取りのみ）。
4. 関連テスト全PASS・`py_compile` OK・バージョン4ファイル整合・CHANGELOG更新。
5. マニュアル未更新の旨を AGENT_INBOX の既存P1（マニュアル陳腐化）項へ追記。

---

# Part A: バンプ/ノーマル線（新線種）

## A-1. 目的とユーザー要望

マテリアルのバンプ/ノーマルマップに描かれたディテール（ゲームアセットのパネルライン・溝・段差など、ジオメトリに存在しない凹凸）から線を抽出し、既存4線種とは別の第5の線として出せるようにする。背景: tokyo0004 級の商用アセットで「綺麗に線が出ること」が品質基準（2026-07-09 仕様シグナル）。既存の稜谷線はジオメトリの辺角度ベースのため、テクスチャにしか無いディテールは拾えない。

- UI名称: **「バンプ線」**（確定・2026-07-09 ユーザー承認）。内部キーは `"bump"`。

## A-2. 方式の選定

### 採用（確定・2026-07-09 ユーザー承認）: 方式A「法線AOV＋コンポジターエッジ検出」（レンダー時合成）

シェーディング法線（ノーマルマップ/バンプ適用後の法線）を画像として取り出し、コンポジターでエッジ検出して線化する。

- 根拠:
  - 線幅を「印刷mm一致」（確定仕様）にできる唯一の方式。エッジ検出後の Dilate/Erode の px 量を `dpi/25.4 × mm` で算出（`camera_comp._target_pixels()` と同式）。
  - 採用済みのAOV線画合成基盤（`aov_compositor.py` の `BML_LineAOVCompositeGroup`）へ入力を1系統足す形で自然に統合できる。
  - 600dpi 印刷でもピクセル空間の線なのでシャープ。
- 既知のトレードオフ（ユーザーへ提示済みの前提で実装する）:
  - **線が見えるのはレンダリング時のみ**。通常のビューポート（マテリアルプレビュー等）には出ない。ビューポートコンポジターでのプレビューは大規模シーンで重くなる前歴（2026-07-07 のレンダービュー評価問題）があるため v1 では見送り、レンダー確認運用とする。
  - 「ラインのみを表示」中はサーフェスが白Emissionに差し替わるためシェーディング法線からノーマルマップが消え、バンプ線は出ない（v1 の既知制限としてマニュアル・CHANGELOGに明記）。

### 不採用（比較検討済み）

- 方式B「テクスチャ前処理」（Pillowでノーマルマップからラインテクスチャを生成し材質へオーバーレイ）: ビューポートでも常時見える利点はあるが、(1) 線幅がUV密度・距離依存になり印刷mm一致の確定仕様を満たせない (2) tokyo0004 のようなアセットはマテリアルを多数オブジェクトで共有しており、オブジェクト単位のオン/オフに材質複製が必要になる (3) 600dpi では線がテクスチャ解像度律速でぼける。ユーザーが「ビューポートで常に見えること」を必須とした場合の代替案として記録に残す。
- 方式C「材質内で幾何法線とシェーディング法線の差分から発光」: 線ではなく面領域が光る形になり線幅制御が不能。不採用。

## A-3. Phase A0 — 実機検証（実装前に必ず行う。結果は `_verify/日付_bml_bump_line_probe/` へ）

1. **標準Normalパスの内容確認**: Blender 5.1 実機で `view_layer.use_pass_normal` を有効化し、ノーマルマップ付き材質の平面を Eevee / Cycles でレンダリング。Normalパスにノーマルマップ由来の摂動が乗るかを確認。
   - 乗る場合: 材質注入なしで標準パスを使える（実装が大幅に軽くなる）。
   - 乗らない場合（どちらか一方でも）: 代替として材質へ `ShaderNodeOutputAOV`（AOV名案 `BML_BumpNormal`）を注入し、BSDF の Normal 入力に繋がるノード（`ShaderNodeNormalMap` / `ShaderNodeBump`）の出力を分岐して AOV へ流す。注入パターンは `outline_setup._ensure_surface_mask_aov` を踏襲（非破壊・追加のみ・カスタムプロパティで注入済み印）。Normal入力が未接続の材質は対象外（既存の稜谷線の領分）。
2. **オブジェクト単位マスク**: バンプ線を有効にしたオブジェクトだけに線を出すためのマスクを検証。第一候補は Cryptomatte(Object)＋`CompositorNodeCryptomatteV2`（材質を触らずオブジェクト名リストで決まる。共有マテリアル問題を回避できる）。レンダー時間・メモリへの影響を tokyo0004 で計測。だめなら `pass_index`＋IDマスクを比較（ユーザーの既存 pass_index 利用と衝突しないか確認）。
3. **エッジ検出品質**: コンポジターで法線RGB → `CompositorNodeFilter`(SOBEL) → ベクトル長 → しきい値（感度設定に対応）→ `CompositorNodeDilateErode`（mm→px）→ `CompositorNodeAntiAliasing` の順で線化し、tokyo0004 のノーマルマップディテール（建物パネルライン等）が線として抽出できるか目視確認（スクリーンショット保存）。
   - シルエット縁（オブジェクト輪郭）でも法線が急変しアウトラインと二重になるため、`BML_ObjectMask` AOV の収縮（Erode）で内部領域へ限定する案を同時に検証。
4. **ページ出力との整合**: 線画AOV合成は現状、最終出力（Composite）へ自動接続しない仕様（v0.3.92 の決定）。バンプ線を通常レンダー画像にも出すには「レンダー画像へのアルファオーバー合成」を新設する必要がある。B-MANGA のページ出力経路（`io/export_*` / B-MANGA Render）がコンポジット結果を拾うかを確認し、自動合成のオン/オフ設計（既定値）を決める材料にする。
5. tokyo0004 を開く場合は**読み取り専用コピー**で開く（AGENT_INBOX 記載の保存事故防止運用）。

**Phase A0 のゲート**: 3 の品質が実用に達しない、または 4 でページ出力へ載せる手段が無い場合は実装へ進まず、方式Bの再検討を含めて上位モデルで方針を再判断する。

### Phase A0 実施結果（2026-07-09 実施・ゲート合格）

詳細: `_verify/2026-07-09_bml_bump_line_probe/results.md`。Phase A1 実装への確定事項:

1. 標準Normalパスは Eevee/Cycles 両方でノーマルマップ/バンプ由来の摂動を反映する（差分0.72〜0.75で機械確認）。**材質へのAOV注入（A-4 手順4）は不要 — 実装しない**。法線ソースは `view_layer.use_pass_normal` の標準パスを使う。
2. オブジェクト単位マスクは **Cryptomatte(Object) 一択**（`matte_id` をPythonから設定可能・Eevee/Cycles両対応・tokyo0004規模1128オブジェクトでレンダー時間影響は誤差範囲）。pass_index+IDMask は Eevee で Object Index パス自体が出力されず使用不可。マスクなしでは密集シルエットで面が黒く潰れることを実測しており、マスクは省略不可。
3. シルエット誤検出はオブジェクトマスクの Erode で除外できることを確認（内部ディテールだけが残る）。エッジ検出チェーンに組み込むこと。
4. **mm→px の Dilate 幅は単純換算（dpi/25.4×mm）だと実測約3.4倍に太る**。Phase A1 でキャリブレーション（Sobel由来の初期線幅を考慮した補正、ピクセル計測での検証）を行うこと。完了条件A-5-2の「±1px」はこのキャリブレーション込みで判定する。
5. ページ出力: `utils/coma_thumb_output.py` の出力ソケット接続の付け替えで技術的に反映可能（障害なし）。ただし `addons/b_manga_render/eevr_bridge.py` の魚眼/eeVR経路はコンポジターを強制無効化しており**バンプ線は原理的に出ない** — 既知制限としてCHANGELOGに明記。
6. **Blender 5.1 でコンポジターAPIが変更されている**（`CompositorNodeFilter.filter_type` 等の廃止、Menu型ソケットへの移行）。Phase A1 のノード構築コードは results.md の該当節を必読の上で書くこと。
7. tokyo0004 実例: 仕組みは動作するが、ノーマルマップの起伏が弱い材質（マンホール等）では線が出にくい。感度（しきい値）の既定値はこの実測を踏まえて調整し、完了条件A-5-5の実機確認で再評価する。

## A-4. Phase A1 — 実装手順（Phase A0 合格後）

1. `core.py`: 新プロパティを `BMangaLineSettings` へ追加（既存の線種プレフィックス命名に合わせる）:
   - `bump_line_enabled`（チェックのみ。既存方針どおり `update=` は設定伝搬＋pending印のみ）
   - `bump_line_color` / `bump_line_thickness`（mm、既存線種と同じ単位系）/ `bump_line_threshold`（感度 0–1）
   - AOV名定数（`AOV_BUMP_NORMAL_NAME` 等）を `core.py` の既存 AOV 定数群へ追加。
2. 線種列挙の拡張（**モディファイアを持たない線種**である点に注意。全列挙箇所に足すのではなく、意味のある箇所だけに足す）:
   - `update_state.py`: `LINE_TARGETS` へ `"bump"` 追加、`_LABELS`（「バンプ線」）、`_VISUAL_PROPS` へ新プロパティ名、`targets_for_property()` のプレフィックス判定追加。バンプ線に「作成/更新」の区別は不要のため pending は visual 系のみ使う。
   - `presets.py`: `_SETTING_FIELDS` とプリセット PropertyGroup へ新4項目を追加（プリセット互換: 旧JSONに無い項目は既定値のままにする既存挙動を確認）。
   - **追加しない**箇所: `line_visibility.py`（モディファイア前提）・`camera_comp.py` の厚み補正系（画像空間で距離補正しない）・`presets.apply_line_settings` の線種分岐（ジオメトリ生成が無いため）。この非対称は実装コメントで明示する。
3. `aov_compositor.py` の拡張:
   - `_create_line_composite_group()` へバンプ線入力（法線ソース＋マスク→エッジ検出→しきい値→太らせ→AA→線色乗算）を追加し、既存の線種加算チェーンへ合流。ノードグループの世代管理（既存のノード所有権 `NODE_PREFIX` / グループ再構築規約）に従い、旧グループを開いた保存済みファイルでも再構築されること。
   - 法線ソースは Phase A0 の結果に従い「標準Normalパス」または「注入AOV」。マスクは Cryptomatte（対象オブジェクト名リストはシーン走査で `bump_line_enabled` かつ非ロックのオブジェクトから生成）。
   - しきい値・px幅は「バンプ線を更新」実行時に設定値から再計算してノードへ焼き込む（リアルタイム連動させない。明示更新方針に従う）。
   - レンダー画像への合成: Phase A0-4 の結果に基づき「バンプ線をレンダー画像に合成」を実装（アルファオーバー）。既定はバンプ線有効オブジェクトが存在する時のみ有効。v0.3.92 の「最終出力へ自動接続しない」決定と矛盾しないよう、接続の追加・撤去を機能のオン/オフと連動させ、ユーザーが手動で組んだ既存コンポジットノードを壊さないこと（既存の `NODE_PREFIX` 所有権管理を使い、自前ノード以外に触れない）。
4. 材質注入が必要な場合（Phase A0-1 の結果次第）: `outline_setup.py` に `_ensure_surface_mask_aov` と同型の `_ensure_bump_normal_aov()` を追加。対象は「バンプ線有効オブジェクトのサーフェス材質のうち Normal 入力が接続されているもの」。撤去関数（ラインを削除・機能オフ時）も対で実装。
5. `batch_update.py`: `refresh_target_visuals("bump", ...)` に対応する更新関数（マスク対象リスト再生成＋コンポジターノード再構築＋必要なら材質AOV注入/撤去）。`refresh_all_target_visuals` のループへ組み込み（対象に `bump_line_enabled` が1つも無ければスキップ）。
6. `operators.py`: `_LINE_TARGET_ITEMS` へ `"bump"` を追加（「更新」ボタン用）。「作成」ボタンはバンプ線には出さない。
7. `panels.py`: 線種セクション列（outline→inner→intersection→selection）の後に「バンプ線」セクションを追加: 有効チェック・色・太さ(mm)・感度、ヘッダーに「更新」ボタンのみ。セクション区切りは既存の線種間区切りに合わせる。
8. ロック連携（Part B が先行済み前提): バンプ線の対象列挙・マスク対象リスト生成で `core.is_settings_locked` を respect する。
9. テスト:
   - 新規 `test/blender_b_manga_line_bump_line_check.py`: ノーマルマップ付きキューブをテスト内で生成（外部 .blend 依存禁止 — フィクスチャはテスト内生成のグローバルルール）→ バンプ線有効化＋更新 → レンダリング → 出力画像でノーマルマップ由来ディテール位置に線ピクセルが存在すること・無効オブジェクトには出ないことをピクセル検査。Eevee/Cycles 両方。
   - `test/blender_b_manga_line_ui_controls_check.py`: 線種ボタン列のアサーション（`create_ops` は4種のまま、`visual_ops` に `"bump"` 追加、セパレータ数）を更新。
   - 既存の線画AOV合成の回帰テストがあれば実行し、合成グループ再構築の互換を確認。
10. バージョン4ファイル更新（Liner 0.3.169 目安）+ CHANGELOG.md。
11. 実機確認: tokyo0004（読み取り専用コピー）でバンプ線を数オブジェクトに適用し、レンダリングで品質・所要時間を確認。「すべてのラインを更新」の所要時間が悪化していないこと。

## A-5. 完了条件

1. ノーマルマップのみに存在するディテールが、バンプ線有効オブジェクトに限ってレンダリング画像上で線として出る（Eevee/Cycles、テストで機械検証）。
2. 線幅が mm 指定どおり（600dpi で mm×dpi/25.4 px ±1px 程度）であることをピクセル計測で確認。
3. 既存4線種・線画AOV合成・「ラインのみを表示」に回帰がない（関連テスト全PASS）。
4. チェックボックス変更で重い処理が走らない（明示更新方針の維持）。ロック中オブジェクトが対象に含まれない。
5. tokyo0004 級での「バンプ線を更新」所要時間が実用範囲（目安: 既存の線種別更新と同オーダー）。
6. バージョン整合・CHANGELOG更新・既知制限（レンダー時のみ／ラインのみ表示非対応）の明記。

---

# ユーザー判断（2026-07-09 回答受領・すべて確定）

1. 方式A（レンダー時合成）で進める — 承認。ビューポート非表示・「ラインのみを表示」非対応の制限もチャットで提示済みの上での承認。ただし Phase A0 のゲート（品質不合格時は方式再検討）は有効のまま。
2. UI名称: 「バンプ線」— 承認。
3. ロック中のカメラ距離補正: 維持 — 承認。

# リスク・注意

- Part A はコンポジター・レンダーパス・材質注入と関係先が広い。Phase A0 を飛ばして実装に入らないこと。
- `intersection_lines.py` の同名関数二重定義（前半デッドコード）に手を入れる際は必ず後半の実装を対象にすること。
- 本計画の実装中に `refresh_propagated_property`（未使用の旧経路）へ新規コードを足さないこと（デッドコードの延命になる）。
- マニュアル（`docs/B-MANGA_マニュアル.md`）は既存P1で全面更新待ちのため、本機能はCHANGELOGへの記載と AGENT_INBOX への追記で引き継ぐ。
