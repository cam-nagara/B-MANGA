# B-MANGA プロジェクトルール (AI ツール連携ハブ)

このファイルは **Claude Code / Codex / Gemini CLI など複数の AI コーディングツールで本プロジェクトを共有開発する** ための連携ハブです。**新しいセッションを開始したらまずこのファイルを読み、最後に「コミット前チェックリスト」を満たしてから書き込みを行ってください。**

最終更新: 2026-07-10 (Codex)

---

## 0. 最初に読むべきもの (セッション開始時)

順番に読むこと。すべて当リポジトリ内。

1. このファイル (`AGENTS.md`) — 連携ルール / 現在の作業状態 / コード領域マップ
2. [`docs/B-MANGA_設計意図.md`](docs/B-MANGA_設計意図.md) — **仕様の正本**。ユーザー意図の蓄積。実装と食い違ったらこちらが優先
3. [`docs/B-MANGA-overview-plan.md`](docs/B-MANGA-overview-plan.md) — 「全ページ俯瞰」+「コマ毎 .blend」の確定アーキテクチャ
4. [`docs/outliner_object_layer_plan_2026-04-30.md`](docs/outliner_object_layer_plan_2026-04-30.md) — **直近の大規模リファクタ計画 (Outliner 中心 Object レイヤー化)**。 2026-04-30 時点で Phase 0〜6 まで完了し legacy/migration コードは削除済 (commit `b2177cb`)
5. [`CHANGELOG.md`](CHANGELOG.md) の冒頭 30〜50 行 — 直近の修正記録 / 検証結果
6. グローバル `~/.codex/AGENTS.md` (= `~/.claude/CLAUDE.md` ハードリンク) — 全プロジェクト共通ルール (応答言語、コミット規約、Blender UI 値ルール、ファイルサイズ制限、品質ルール等)

`docs/` には他にも計画書がある (魚眼 / ラスター / restructure / clip 出力 / viewport reparent)。 必要に応じて参照。

---

## 1. 対象環境

- **Blender: 5.1.2** (`ec6e62d40fa9`)。 API は 5.1.2 仕様で確認すること。 Windows での実行ファイルは `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`
- アドオン形式: Blender Extensions (`blender_manifest.toml`)
- Python: Blender 5.1.2 同梱版 (3.11)
- 同梱 wheel: `wheels/` 配下 (`PyPSD`, `Pillow` 等)。 Blender Extensions の wheel ロード経由で import 可能

---

## 2. 現在進行形のアーキテクチャ状態 (Snapshot: 2026-05-28)

直近で大型のデータ構造刷新が完了している。 古い記述で迷ったときはここを参照。

### 2.1 完了済みの大型移行

- **(完了 / 2026-07-11 Codex, v0.6.480)** Meldexシナリオ取込。安全なローカル受信、既存ページ不足分だけの追加、コマ非生成、タイプ名と完全一致するフキダシ／テキストプリセット、標準フキダシfallback、改行・ルビ保持、テキスト連動サイズ、再送時の重複防止をB-MANGAとMeldexへ実装した。計画: [`docs/meldex_scenario_import_plan_2026-07-11.md`](docs/meldex_scenario_import_plan_2026-07-11.md)

- **Outliner 中心の Object/Collection レイヤー化** (Phase 0〜6 / 2026-04-30 完了)
  - レイヤーは PropertyGroup 単独ではなく、**Blender 実 Object として存在し、安定 ID は custom property** (`bmanga_kind` / `bmanga_id` / `bmanga_parent_key` / `bmanga_z_index` / `bmanga_title` / `bmanga_managed`) に保持
  - ページ / コマ / 汎用フォルダは Collection
  - 命名規則: `P0001__p0001__1ページ` (ページ) / `C0010__c01__コマ1` (コマ) / `F0030__folder_xxxxxx__人物` (フォルダ) / `L0040__text__セリフ本文` (レイヤー)。 prefix 文字は `P/C/F/L`
  - **legacy/migration コードは削除済** (commit `b2177cb`)。 復活させない
  - 詳細: [`docs/outliner_object_layer_plan_2026-04-30.md`](docs/outliner_object_layer_plan_2026-04-30.md) §3.1〜§3.2
- **ページ一覧 + ページ用blend + コマ用blendの三段構成** (2026-06-01 Codex, v0.6.230)。`work.blend` はページ一覧専用の軽量ファイル、各ページのフキダシ / 効果線 / テキスト / ラスター等は `pNNNN/page.blend`、コマの 3D データは従来どおり `pNNNN/cNN/cNN.blend` に保存する。旧 `work.blend` 一元化方針は 55p/80p 級作品の重さ対策として撤回。詳細: [`docs/page_file_stage_plan_2026-06-01.md`](docs/page_file_stage_plan_2026-06-01.md)、旧経緯: [`docs/B-MANGA-overview-plan.md`](docs/B-MANGA-overview-plan.md)
- **ディレクトリ構造改修** (`pages/` 階層撤去, `pNNNN/cNN/` フラット化, `passes/` 配置)。 [`docs/B-MANGA-restructure-plan-2026-04-28.md`](docs/B-MANGA-restructure-plan-2026-04-28.md)
- **コマ独立 `.blend` 方式維持** + 用紙設定共有 + 紙面側即時反映。 [`docs/B-MANGA_設計意図.md`](docs/B-MANGA_設計意図.md) §0.6.2〜§0.6.3
- **paper_bg は実 Mesh Object** (ラスター paint のジラジラノイズ解消) + 範囲外 paper_bg は viewport hide
- **start_side 切替時、コマ配下レイヤーも page_grid offset に追従** (2026-05-01 修正、 サブコレクション再帰走査)
- **Z リフトは 0.1 刻みのページ毎 per-page rank 方式**
- **(完了 / 2026-05-17 Claude Code, v0.5.46)** 枠線ボカシブラシ線種 + 枠線プリセット (枠線+白フチ) + コマ作成ツール (矩形/折れ線 自動判別) + 効果線入り抜きの「範囲」(%/長さmm)。 ヘッドレス実機テスト PASS (`test/blender_border_preset_coma_tool_check.py`)。 画面目視は要対話 Blender (本ブランチ読込時)。 計画: [`docs/border_preset_coma_tool_plan_2026-05-16.md`](docs/border_preset_coma_tool_plan_2026-05-16.md)
- **(完了 / 2026-05-23 Codex, v0.6.063)** フキダシ実体を単一の編集可能カーブへ移行。B-MANGA は作成・詳細設定・明示再生成を担当し、表示とレンダリングは保存済み Blender 実体が担う。旧 `balloon_fill_*` / `balloon_source_*` 実体は再同期時に削除する。詳細: [`docs/balloon_curve_source_plan_2026-05-23.md`](docs/balloon_curve_source_plan_2026-05-23.md)
- **(完了 / 2026-05-27 Claude Code, v0.6.129〜v0.6.133)** フキダシ ジオメトリノード最小化 (Phase A〜E)。 全描画責務 (塗り / 主線 / 外側フチ / 内側フチ / 多重線 / しっぽ主線) を Python メッシュ (Shapely + mapbox_earcut) で焼き込み、 ジオメトリノードグループ `BManga_GN_BalloonCurveRender` を完全撤去。 フキダシ本体カーブから geometry node modifier も消えた (commits `8cda170`, `02dd661`, `aab0d34`, `c3061a8`)。 `utils/balloon_curve_render_nodes.py`: 1393 行 → 70 行 (-95%)。 移動・サイズ変更・詳細設定変更でのノードグループ評価コストがゼロに。 詳細: [`docs/balloon_node_minimization_plan_2026-05-27.md`](docs/balloon_node_minimization_plan_2026-05-27.md)
- **(完了 / 2026-05-27〜28 Claude Code, v0.6.103〜v0.6.143)** フキダシのフチ・多重線・主線を Shapely buffer 方式に統一 + 主線「谷/山の線幅」動的化。 外側フチ / 内側フチ / 多重線も Shapely buffer ベースの Mesh band に作り直し (`docs/plans/fringe_multiline_shapely_2026-05-27.md` の計画完了)。 さらに 主線 dynamic で 「ベース太さを保ったまま 谷頂点だけ pinch + 辺は直線」 を実現 (`utils/balloon_line_mesh.py` の `_build_dynamic_multi_line_polygons` を外側アライメント sample-direct 化)。 関連実装は `utils/balloon_line_mesh.py` に集約。 詳細は CHANGELOG 冒頭 (v0.6.138〜v0.6.143)。
- **(完了 / 2026-05-05 Codex 起点, コード検証 2026-05-28 Claude Code)** 作品要素の実体化 第一段階。 テキスト (`utils/text_real_object.py`: Pillow 画像を貼った Mesh 平面, 旧 Empty 削除) / 画像レイヤー (`utils/image_real_object.py`: 透明テクスチャ平面) / コマ枠線 (`utils/coma_border_object.py`: 実カーブ, 通常線・破線・点線・二重線・白フチ) / フキダシ本体・塗り・しっぽ / 用紙背景・ガイド線・セーフライン外塗り を Blender 実オブジェクトとして同期済み。 mirror 同期は `utils/layer_object_sync.py` `_mirror_image_text_objects` 経由で配線済み。 アドオン無効時も作品要素が Blender データに残る。 詳細: [`docs/bmanga_real_object_safety_plan_2026-05-05.md`](docs/bmanga_real_object_safety_plan_2026-05-05.md)
- **(完了 / 2026-06-28 Codex, v0.6.398〜v0.6.400)** フキダシ / NURBSフキダシ / 効果線の入口・線設定統合。フキダシツールとNURBSフキダシツールを画面上は統合し、NURBSフキダシをフキダシツールの「なめらか自由形状」プリセットとして扱うようにした。画像パス、効果線、フキダシ線の共有選択肢と保存対象を共通定義へ集約し、効果線とフキダシの白抜き線設定UIを共通部品化した。既存のNURBSフキダシ操作は互換用として維持。詳細: [`docs/balloon_effect_tool_unification_plan_2026-06-28.md`](docs/balloon_effect_tool_unification_plan_2026-06-28.md)
- **(完了 / 2026-07-10 Codex, v0.6.476 / B-MANGA Liner v0.3.194)** B-MANGA Linerの「中間頂点用ライン細分化」を全面ライン殻から基準カメラ輪郭の曲線化へ変更。元メッシュ成分を完全に維持し、浅い連続角だけを滑らかに補間し、鋭角・端点・分岐を保持する。生成ラインは別インスタンスとして出力し、ワイヤー表示や後段のメッシュ評価へ混入させない。詳細: [`docs/bml_line_local_subdivision_plan_2026-07-10.md`](docs/bml_line_local_subdivision_plan_2026-07-10.md)
- **(進行中 / 2026-07-11 Codex)** B-MANGA Linerへ、購入素材の表示メッシュ自体を法線誘導で滑らかにし、同じ確定メッシュから全線種を生成する「購入素材メッシュ最適化」を追加中。永続バックアップを残さず、候補検証後の一括確定とする。詳細: [`docs/bml_purchased_mesh_optimizer_plan_2026-07-11.md`](docs/bml_purchased_mesh_optimizer_plan_2026-07-11.md)

### 2.2 直近のバグ修正トピック (2026-05-01 まで)

- ページ範囲外 (`in_page_range=False`) の paper_bg を viewport から hide
- `start_side` 切替でコマ配下レイヤーが旧位置に取り残される問題
- 新規作品の `page_number_start/end` 確定保証
- `msgbus.subscribe` を `ViewLayer.active_layer_collection` に修正
- マスククリップ Boolean solver / コマ stem fallback

`CHANGELOG.md` 冒頭が常に最新。

### 2.3 まだ詰める余地のあるテーマ

(着手前にユーザーへ確認推奨)

- **B-MANGA Render 分離 (進行中 / 2026-05-05 Codex 起点, コード検証 2026-05-28 Claude Code)**。 B-MANGA 本体はページ一覧での作画 + コマ用blendファイルでの 3D 配置までに限定し、 出力プリセット / 魚眼レンダリング / PSD・PDF 等の完成画像書き出しは `addons/b_manga_render/` へ分離する。
  - **済**: 独立アドオン土台 (`addons/b_manga_render/`, 10 ファイル / 約 2336 行) / カード型プリセット UI / eeVR 連携。
  - **未 (Phase R4 + R1 一部)**: B-MANGA 本体に出力系がまだ現役登録されている — 「ページ出力」パネル (`panels/__init__.py` `_MODULES` 内 `export_panel`) / `bmanga.export_page`・`export_all_pages`・`export_pdf` (`operators/io_op.py`, `operators/__init__.py` 内 `io_op`) / `io/export_*.py`。 魚眼モード / 縮小モード / Pencil+4 線幅保存 / ページ画像スケールは 2026-06-02 に B-MANGA Render へ移動済み。 残りを B-MANGA Render へ移植し本体側を未登録化する作業が残っている。
  - 詳細: [`docs/b_manga_render_separation_plan_2026-05-05.md`](docs/b_manga_render_separation_plan_2026-05-05.md)
- **効果線 Geometry Nodes 化 / カーブ正本化 (進行中 / 2026-05-23 Codex 起点, コード検証 2026-05-28 Claude Code)**。 フキダシ側は GN 完全撤去済み (上記 v0.6.133)。 効果線側は依然 `BManga_GN_EffectLine` ノードグループで本体生成中 (`utils/effect_line_object.py` が `geometry_nodes_bridge.ensure_modifier(display, "effect_line", …)` で GN modifier を付与, `utils/geometry_nodes_bridge.py` `_build_effect_line_nodes`)。 `strokes` 渡し時に静的メッシュ化する分岐は一部実装済みだが、 フキダシと同様の「編集可能形状を正本にする」本格移行は未完了。 始点/終点形状への同方式適用、 グリースペンシルからの登録、 手編集後の補助線再生成ルールも次段階。 詳細: [`docs/geometry_nodes_generation_plan_2026-05-21.md`](docs/geometry_nodes_generation_plan_2026-05-21.md)、[`docs/balloon_curve_source_plan_2026-05-23.md`](docs/balloon_curve_source_plan_2026-05-23.md)
- **コマ内容の不透明度マスク方式 (完了 / 2026-05-25 Codex, v0.6.075)**。コマ内のフキダシ / 効果線 / GP / ラスター / 画像 / テキストを破壊的に切らず、ページ ID + 表示ページ番号 + コマ ID を含むコマ単位の不透明度マスクで見切る。PSD はコマフォルダのレイヤーマスクを正とし、個別レイヤーへ重複マスクを付けない。詳細: [`docs/coma_content_opacity_mask_plan_2026-05-25.md`](docs/coma_content_opacity_mask_plan_2026-05-25.md)
- PSD 書き出し強化は B-MANGA Render 側で扱う。 コマ形状レイヤーマスク / 個別レイヤー保持
- `.clip` 直書き — 現時点で見送り。 deferred 計画あり ([`docs/clip_export_deferred_plan.md`](docs/clip_export_deferred_plan.md))
- 魚眼 F1+F2 ([`docs/B-MANGA-fisheye-plan-2026-04-28.md`](docs/B-MANGA-fisheye-plan-2026-04-28.md))
- ラスターレイヤー強化 ([`docs/B-MANGA-raster-layer-plan-2026-04-28.md`](docs/B-MANGA-raster-layer-plan-2026-04-28.md))

---

## 3. コード領域マップ (どのファイルが何を担当するか)

新しい関数を書く前に、 配置先がここで決まる。 既存の巨大ファイルにむやみに追記しない (グローバルルール: 1 ファイル 1000 行 / 1 関数 50 行)。

### 3.1 トップ階層

| 階層 | 役割 |
|------|------|
| `core/` | データ構造 (`PropertyGroup`)。 `BMangaWorkData` (`core/work.py`) / `BMangaWorkInfo` (`core/work_info.py`) / `BMangaPageEntry` / `BMangaComaEntry` / `BMangaLayerFolder` / `BMangaRasterLayer` / `BMangaImageLayer` / `BMangaBalloonEntry` / `BMangaTextEntry` / 効果線 / 用紙 / コマ枠 / モード |
| `operators/` | `bpy.types.Operator`。 ユーザー操作起点の処理 |
| `panels/` | N パネル UI (sidebar) |
| `ui/` | overlay (GPU 描画) と context menu |
| `keymap/` | キーマップ登録 (`viewport_ops.py` は viewport-side のホットキー) |
| `utils/` | ロジックヘルパー。 直接 Blender state を触らない / Operator 以外から呼べる純粋ロジックの保管所 |
| `io/` | 永続化 (`work.blend` `pNNNN/page.blend` `pNNNN/page.json` `cNN.blend` `cNN.json` / PSD 書き出し / Meldex 受信) |
| `typography/` | 縦書き / ルビ / 縦中横 / 行頭禁則 / metrics / レンダラ |
| `test/` | Blender 実機テスト (後述) |
| `presets/` | 用紙プリセット |
| `wheels/` | Blender Extensions 同梱 wheel |
| `addons/b_manga_render/` | B-MANGA Render。 出力プリセット / 魚眼レンダリング / 完成画像書き出しを扱う独立アドオン |

### 3.2 触る頻度が高い領域 (2026-05 時点)

- **レイヤー Object 同期**: `utils/layer_object_sync.py` `utils/outliner_model.py` `utils/outliner_watch.py` `utils/object_naming.py`
- **Outliner ↔ B-MANGA 双方向同期**: `utils/active_collection_sync.py` `utils/outliner_watch.py` `panels/outliner_layer_panel.py`
- **page_grid 配置**: `utils/page_grid.py` (start_side / read_direction 反映)
- **paper_bg**: `utils/paper_bg_object.py`
- **GP オブジェクトレイヤー**: `utils/gp_object_layer.py` `utils/gp_layer_parenting.py`
- **Alt+ドラッグ reparent**: `operators/alt_reparent_op.py` `utils/layer_reparent.py` `ui/reparent_overlay.py`
- **コマ枠 / カット**: `core/coma_border.py` `operators/coma_knife_cut_op.py` `operators/coma_edge_*` `utils/border_geom.py`
- **テキスト IME / 縦書き**: `operators/text_op.py` `operators/text_edit_runtime.py` `typography/`
- **修復オペレータ (整合性回復)**: `operators/repair_op.py`

### 3.3 触る前に必ず関連を読むべきファイル群

これらは **複数モジュール間の契約** を持つので単独編集すると影響が広い。

- `core/work.py` (`BMangaWorkData` ルート PropertyGroup) — pages / comas / layers / folders を握る (`Scene.bmanga_work` に PointerProperty で登録)
- `utils/active_target.py` — 「現在の編集対象 (page / coma)」解決ヘルパー。 多数の Operator が依存
- `utils/layer_hierarchy.py` — Object 階層構築
- `utils/handlers.py` — `bpy.app.handlers` 登録 (現状: `load_post` / `save_pre`)。 ハンドラを増やす場合はここで一元登録する
- `__init__.py` — register / unregister。 新規クラス追加時はここを更新

---

## 4. 役割分担と衝突回避

### 4.1 単一ブランチ運用前提

両ツールとも `main` (またはユーザー指定の作業ブランチ) で作業しているため、**同じ時間帯に同じファイルを触らない** ことが衝突回避の唯一の防衛線。

### 4.2 セッション開始時に必ず実行する確認

```bash
# 直近の他ツール作業を必ず確認 (どちらのツールでも実行)
git log --since='2 days ago' --pretty='%h %ad [%an] %s' --date=short

# 自分以外のツールが触ったファイルを把握
git log --since='2 days ago' --grep='\[claude\]' --name-only   # Codex 側で実行
git log --since='2 days ago' --grep='\[codex\]'  --name-only   # Claude 側で実行
git log --since='2 days ago' --grep='\[gemini\]' --name-only   # Claude/Codex 側で実行
```

直近 2 日以内に他ツールが触った領域を **これから触る場合**:

1. そのコミットの差分を `git show <hash>` で読み、設計意図を理解する
2. CHANGELOG に該当エントリがあるか確認
3. 関連する `docs/*.md` 計画書がアップデートされているか確認
4. 不明瞭なら **作業を開始する前にユーザーへ確認**

### 4.3 大規模変更を始めるときのプロトコル

「データ構造変更 / アーキテクチャ変更 / 多数ファイルにまたがるリネーム」を始める前に:

1. ユーザーへ意図を確認し、計画書を `docs/<topic>_plan_<date>.md` に新規作成
2. **このファイル §2.1 (現在進行形のアーキテクチャ状態) に「進行中」エントリを追記**
3. 完了後 §2.1 を「完了済み」へ移動し、 関連 commit hash を残す

これによって他ツールが新セッションを開いたときに「いま大型工事中」を即座に検知できる。

### 4.4 触ってはいけない / 慎重に扱うもの

- **legacy/migration コード** — 既に `b2177cb` で削除済。 復活させない
- `wheels/*.whl` — 直接編集禁止 (バイナリ)
- `presets/paper/*.json` — ユーザー作成プリセット。 開発側からの上書きは原則しない
- `.claude/` `.codex/` 配下 — `.gitignore` 済。 個別ツールのワークスペース。 push しない
- `*.blend` ファイルは git に入れない (`.gitignore` 未指定だが運用上の合意)

---

## 5. コミット規約 (グローバルルール再掲 + 本プロジェクト追加)

**ユーザーの明示的指示があるまでコミット・プッシュしない。**

2026-05-18 以降の B-MANGA 開発セッションでは、ユーザーから修正・実装依頼を受けた場合、その依頼自体を「修正後にコミットする」明示指示として扱う。修正と検証が完了したら、ユーザー確認に回す前に確認可能な単位でコミットする。プッシュは従来どおり、ユーザーが明示した場合のみ実行する。

### 5.1 件名 prefix

- Claude Code: `[claude] <type>: <message>`
- Codex: `[codex] <type>: <message>`
- Gemini CLI: `[gemini] <type>: <message>`

`<type>` は `feat` / `fix` / `refactor` / `docs` / `merge` / `revert` / `debug` のいずれか。

### 5.2 Co-Authored-By トレーラー

- Claude Code: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` (モデルが変わったらバージョン部を更新)
- Codex: `Co-Authored-By: OpenAI Codex <noreply@openai.com>`
- Gemini: `Co-Authored-By: Gemini <noreply@google.com>`

### 5.3 CHANGELOG 更新ルール

ユーザー操作で挙動が変わる修正 (バグ修正 / 機能追加 / アーキテクチャ変更) は **同じコミットで `CHANGELOG.md` 冒頭にエントリを追加** すること。 フォーマットは既存エントリに倣う:

```markdown
## YYYY-MM-DD — タイトル

### 症状
### 原因
### 修正
### 検証 (Blender 5.1.1 実機)
```

純粋なコメント / 内部リファクタは CHANGELOG 任意。

### 5.4 アドオンバージョン bump ルール

ユーザー操作で挙動が変わるコミットは **同じコミットでアドオンバージョンを必ず上げる**。 Blender Extensions Platform / 開発者の手元のいずれでも 「同じバージョン番号で違う挙動」 を許さないため。

**bump 対象 (両方更新)**:
- `blender_manifest.toml` の ``version = "X.Y.Z"`` (これが authoritative)。Blender Extensions の検証は `0.6.003` のような leading zero を受け付けないため、manifest では `0.6.3` のように保存する。
- `__init__.py` の ``bl_info["version"] = (X, Y, Z)`` (legacy 互換)。tuple なので `0.6.003` ではなく `(0, 6, 3)` のように保存する。

**B-MANGA 表記ルール**:
- CHANGELOG / AGENTS.md / ユーザー向け報告では、ユーザーが明示しない限り `0.Y.ZZZ` 形式で表記する。例: `0.6.000`, `0.6.001`, `0.6.079`。
- ユーザーが明示しない限り、新機能 / アーキテクチャ変更 / データ構造変更でも MINOR を勝手に上げず、PATCH (右端3桁) を1つ進める。例: `0.6.003` の次は `0.6.004`。
- MINOR / MAJOR を上げるのは、ユーザーが明示した場合、または PATCH が `999` に達した場合のみ。例: `0.6.999` の次は `0.7.000` とし、 `0.6.1000` にはしない。

**bump 不要 (CHANGELOG も任意)**:
- 純粋なコメント / docstring のみの変更
- AGENTS.md / docs 整備のみ
- test/ のみの追加
- ロガーレベル調整等の内部ログ変更

迷ったら **bump する** 方を選ぶ。 同一日内に複数コミットする場合は最後の commit でまとめて bump せず、 **挙動変更のあった各 commit ごとに bump** すること (途中の commit を cherry-pick したときバージョン整合性が崩れないよう)。

直近の事例 (参考): v0.5.79 B-MANGAタブ表示中のツール切替修正 → v0.6.000 フキダシ形状拡張 → v0.6.001 効果線・アウトライナー構成更新 → v0.6.002 B-MANGAパネル上のツール切り替え修正 → v0.6.003 フキダシ単一オブジェクト化。

### 5.5 コミット前チェックリスト

- [ ] 件名 prefix が `[claude]` / `[codex]` / `[gemini]` のいずれかになっている
- [ ] Co-Authored-By トレーラーが正しい
- [ ] §4.2 のコマンドで他ツール作業を確認した
- [ ] ユーザー操作影響のある変更は CHANGELOG に追記した
- [ ] **§5.4 に従い ``blender_manifest.toml`` + ``__init__.py`` の version を bump した** (純粋なコメント / docs / test 追加のみなら省略可)
- [ ] 大型変更なら §4.3 に従い計画書 + §2.1 を更新した
- [ ] §6 の実機テストを (該当するものは) 走らせた
- [ ] 1 ファイル 1000 行 / 1 関数 50 行を超えていない (グローバルルール)
- [ ] secrets が含まれていない (`.env` 等)

---

## 6. テスト

### 6.1 実機テスト (重要)

`test/` 配下に Blender 実機を起動して動作を検証するスクリプトが多数ある。 ファイル名は `blender_*_check.py` 規約。

ビューポート上の色・不透明度・表示順・最前面・シェーディング・選択ハンドルなど、画面の見た目を変える修正では、プロパティ値だけを確認して完了扱いにしない。 `bpy.ops.screen.screenshot` または `bpy.ops.render.opengl(view_context=True)` で実際の3Dビュー画像を保存し、代表ピクセル/領域の色・明度・表示有無を検証すること。 B-MANGA はビューをテクスチャ表示/マテリアル表示へ切り替えるため、対象オブジェクト単体のプロパティ確認だけでは不十分。 UI画面が必要なら `--background` ではなく `--factory-startup --python test/xxx_visual_check.py` のようなUI実機テストを追加・実行する。

| 領域 | テストファイル |
|------|---------------|
| Alt+ドラッグ reparent (Phase A / B) | `test/blender_alt_reparent_phase_a_check.py` / `test/blender_alt_reparent_phase_b_outside_check.py` |
| コマテンプレート | `test/blender_coma_template_check.py` |
| context menu | `test/blender_context_menu_commands_check.py` |
| detail settings runtime | `test/blender_detail_settings_runtime_check.py` |
| 魚眼 F1+F2 | `test/blender_fisheye_f1f2_check.py` |
| 汎用フォルダ | `test/blender_layer_folder_check.py` |
| レイヤースタック D&D reparent | `test/blender_layer_stack_dnd_reparent_check.py` |
| レイヤースタック UI | `test/blender_layer_stack_ui_behavior_check.py` |
| B-MANGA Liner 線幅遠近減衰 | `test/blender_b_manga_line_width_falloff_check.py` |
| ラスター paint | `test/blender_raster_layer_paint_check.py` |
| セーフライン外塗りのビュー表示 | `test/blender_safe_area_fill_viewport_visual_check.py` (`--background` なし) |
| 共有レイヤースキーマ | `test/blender_shared_layer_schema_check.py` |
| テキスト IME (3 種) | `test/blender_text_ime_*_check.py` |
| restructure E2E | `test/blender_restructure_e2e.py` |

実行例 (Windows / bash):

```bash
'/c/Program Files/Blender Foundation/Blender 5.1/blender.exe' --background --python test/blender_xxx_check.py
```

### 6.2 単体テスト (pytest 形式)

`test/test_*.py` は Blender 非依存の純 Python ロジックテスト (`test_paths.py` `test_stroke_style.py` `test_view_event_region.py`)。 Blender 同梱 Python から `python -m pytest test/test_*.py` で実行。

### 6.3 「微細挙動テスト」「全体チェック」「全ファイル全行チェック」「徹底チェック」

グローバルルール (`~/.codex/AGENTS.md`) に定義あり。 ユーザーがその文言で指示してきたら、それぞれの定義に従う。 本プロジェクトでは特に:

- **データ構造変更後** は徹底チェック (関数チェーン追跡 + 状態整合性確認) を必ず行う
- **UI 微細挙動** は `test/blender_*_check.py` に決定的ケースを追加して再現可能にする (グローバルルール「微細挙動テスト」参照)

### 6.4 「AI監査」ワンワード実行

ユーザーがこのプロジェクトで **「AI監査」** とだけ発言した場合、確認を挟まずに次を実行する。

```powershell
python test/bmanga_ai_audit_runner.py --profile full --keep-going --include-slow
```

実行後は `.codex/ai_audit/<日時>/AI_REVIEW_PROMPT.md` と代表的な目視画像を確認し、B-MANGA / B-MANGA Renderの問題有無を日本語で要約する。Blenderの通常画面を開く監査まで含める必要がある場合のみ、ユーザーは **「AI監査UI」** と言う。この場合は次を実行する。

```powershell
python test/bmanga_ai_audit_runner.py --profile full --keep-going --include-slow --allow-ui
```

短時間確認だけでよい場合は **「高速AI監査」** と言う。この場合は次を実行する。

```powershell
python test/bmanga_ai_audit_runner.py --profile standard --keep-going
```

---

## 7. ツール固有メモ

### 7.1 Claude Code

- 自動メモリ: `C:\Users\niken\.claude\projects\D--Develop-Blender-B-MANGA\memory\` に永続化。 既存エントリ: 「Blender 5.1.1 が対象」
- 自動メモリには **コードから derive 可能なものは書かない** (グローバルルール)。 仕様意図 / プロジェクトの非自明な経緯のみ
- worktree 内で動作することがある (`.claude/worktrees/<name>/`)。 commit はそのままユーザー main に取り込まれる前提

### 7.2 Codex

- セッション ID は CLI 起動時に発行。 `.codex/` ワークスペースは `.gitignore` 済
- 計画書を作るときは `docs/<topic>_plan_<date>.md` に置き、 §2.1 を更新する

### 7.3 Gemini CLI

- 現状の commit 履歴では未使用 (2026-05-02 時点)
- 採用する場合は本セクションへ運用メモを追記

---

## 8. B-MANGA 固有の挙動ルール (グローバル + プロジェクト共通)

### 8.1 ツール継続性

B-MANGA のツールは、**明示的な終了操作または別ツールへの切り替えがあるまで継続する**。 一回のクリック / カット / 移動 / 入力確定だけで、 オブジェクトモードや通常選択状態へ戻してはいけない。

### 8.2 Blender UI 値ルール (グローバル再掲)

ユーザーの数値 / 色 / 角度 / サイズ / 座標 / 倍率の指定は、**常に Blender UI 上の表記値**として解釈する。 `FloatVectorProperty(subtype="COLOR")` は scene-linear RGB を保持し UI では sRGB 表示される — UI の `0.7` を内部値に直接代入しない。 必要なら sRGB ↔ linear 変換を挟む。

### 8.3 ファイル文字コード

CSV ファイルを生成する場合は **必ず BOM 付き UTF-8** で出力 (Excel での文字化け対策)。

### 8.4 PSD 書き出し時の優先

コマは「コマ形状のレイヤーマスクが掛かったレイヤーフォルダ」として出す方針。 GP / フキダシ / 効果線 / テキスト / 画像レイヤーは可能な限り個別レイヤーとして残す ([`docs/B-MANGA_設計意図.md`](docs/B-MANGA_設計意図.md) §0.6.6)。

### 8.5 ユーザー向け報告は Blender UI 表記のみ

ユーザーへの進捗報告 / 修正説明 / 検証結果は、 **Blender 画面に表示される日本語の UI 表記** だけで構成すること。 内部のコード識別子 / 関数名 / Python API 名 / PropertyGroup 名 / アウトライナー上の prefix 付き内部文字列 (例: ``L0210__effect__効果線_focus``) を生で出してはならない。

**Why:** ユーザーは一切コードを見ない設計で B-MANGA を使う。 内部名を出されると「読めない情報を押し付けられた」 と感じる。 過去複数回 (グローバルルールの「Blender UI 値ルール」明示後を含む) 「コードを見ない / UI 表記で語れ」 と再注意を受けている。

**使ってよい用語:** 「フキダシ」「効果線」「テキスト」「コマ」「ページ」「アウトライナー」「コマ用blendファイル」「プリファレンス」「コマ編集モード」「ページ一覧」「下絵_コマ」「ラスター」 など Blender 画面 / B-MANGA UI ラベルに出る名前。

**出さない:** 関数名 / モジュール名 / `bpy.ops.xxx` / カスタムプロパティキー / Python ステップごとのスタックトレース。 例外はユーザーが自発的にコード修正を依頼してきたときだけ。

---

## 9. 引き継ぎプロトコル

### 9.1 調査セッション → 開発セッション

- 引き継ぎプロンプトは **タイトル含め 200 字以内**
- 詳細は計画書 (`docs/<topic>_plan_<date>.md`) に書き、 プロンプトには「計画書パス + 実行指示」だけ書く

### 9.2 ツール A → ツール B (異なる AI)

- ツール A は最後に CHANGELOG を更新し、 進行中作業があれば §2.1 に「進行中」エントリを残す
- ツール B はセッション開始時に §0 と §4.2 を実行
- 不明点はユーザーに確認してから着手

---

## 10. このファイルのメンテナンス

- **§2 (アーキテクチャ状態) は最も劣化しやすい**。 大型変更を完了したら必ず更新
- **§3 (コード領域マップ) はディレクトリが増減したら更新**
- 最終更新日とどのツールが更新したかを冒頭に記録 (例: `最終更新: 2026-05-02 (Claude Code)`)
- 200 行を超え始めたら、 セクション単位で `docs/agent_*.md` に分離してリンクする
