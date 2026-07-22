# 2D合成プレビュー移行 実装計画書（2026-07-22）

- 種別: 恒久対策の実装計画書（調査セッション成果物）
- 対象: B-MANGA ページ編集モードのインタラクティブ表示
- 方針: EEVEEの3D透過ソート依存をやめ、書き出しと同じPillow 2D合成をプレビューに使う
- 推奨実行モデル: **Sonnet 5級**（計画書ありの実装のため。設計判断が崩れた場合のみ上位モデルへ戻す）
- 本計画書は行番号非依存（ファイルパス＋関数名で記述）

---

## ① 背景と目的

### 経緯（遮蔽バグ群）

B-MANGAはページ・コマ・各種レイヤー（GP/画像/パターンカーブ/ラスター/塗り/フキダシ/テキスト/効果線）を、3Dビューポート上の実オブジェクト（平面メッシュ＋半透明マテリアル、location.z の薄いZ帯で重なり順を表現）として EEVEE の RENDERED シェーディングで表示している。この「EEVEEの3D透過ソートに前後関係の解決を委ねる」設計には以下の限界が実機調査で判明した。

- **効果線×グラデーション塗りの遮蔽バグ**: v0.6.569 で `surface_render_method=DITHERED` 化により緩和したが、完全解決の実証はできていない。
- **ラスター×効果線の遮蔽**: 2回の徹底調査でも再現条件・真因を特定できず未解決。
- EEVEEの透過合成はマテリアル方式の調整だけでは制御しきれず、「レイヤーリスト順＝見た目の重なり順」という漫画制作ツールの根幹保証をEEVEE側に持たせるのは構造的に脆い。

### なぜ2D合成方式か

PNG/PSD書き出しパイプライン（`io/export_pipeline.py`）は EEVEE を一切使わず、全レイヤーを Pillow で個別ラスタ化し、レイヤーリスト順に**確定的に**アルファ合成している。ここでは順序が崩れることは原理的にない。インタラクティブ表示も同じ合成結果を平面テクスチャとして表示すれば:

- 遮蔽バグ群が構造的に消える（プレビュー＝書き出し結果の同一性が保証される）
- 効果線主線だけ Principled BSDF（照明依存）という非対称も一緒に消える
- レイヤー1件ごとの Boolean Modifier 評価・per-comaマテリアル増殖・TAAちらつきなどの周辺負荷も削減方向へ向かう

これはユーザーが選択した方針である。

### 反対意見とその評価（正直に記す）

**反対意見（部分的に成り立つ）**:

1. v0.6.569 の DITHERED 化で効果線×グラデは緩和済み。raster×効果線は再現条件不明＝発生頻度が低い可能性があり、恒久対策に13回超のセッションを投じるのは過剰投資では。
2. 2D合成方式は大規模サブシステム新設であり、移行期間中は3D経路と2D経路の二重保守が生じ、リリース前の安定性をむしろ下げるリスクがある。
3. より安価な代替案（全マテリアルのEmission+DITHERED統一、Z帯間隔拡大、効果線マテリアルのEmission化のみ先行）を試し切っていない。

**推進側の反論**:

- (a) 「リスト順＝見た目の重なり順」の保証をEEVEE透過ソートに委ねる構造的脆さは、DITHERED緩和や安価案では解消されない。「たまに順序が崩れるが再現条件不明」は、書き出し（常に正しい順序）とプレビューが食い違うことを意味し、ユーザーが最も不安視する種類の品質問題。
- (b) Phase 0 の成果（グラデーションnumpy化・パターンカーブ書き出し欠落の解消・性能実測）は、2D化を中止しても書き出し改善としてそのまま価値が残る。
- (c) 全フェーズを実験フラグ（既定OFF）配下で進めるため、リリースを跨いだ段階導入が可能。リリース前安定性との二者択一ではない。
- (d) 照明非対称・Boolean評価コスト・TAAちらつき・PSD低速の一部という周辺問題も同時に解消へ向かう。

**結論**: 「Phase 0 だけ先行実施し、最小プロトタイプ検証と性能実測を見てから本体着手を最終判断する」形にする。これにより過剰投資リスクと構造的解決の利益を両取りする。Phase 0 完了時点で中止しても損失は小さい。

---

## ② 現状アーキテクチャの要点

### 書き出しパイプライン（2D合成エンジンの正体）

- `io/export_pipeline.py` の `build_page_layers(work, page, options)` が中核。全レイヤーを Pillow で個別ラスタ化し `ExportLayer(name, image, left, top, group_path, visible, opacity, blend_mode, stack_uid, stack_parent_key)` のリストを作り、`_flatten_layers` → `_composite_layer` で下から順に確定的アルファ合成（multiply/screen/overlay/add も ImageChops で実装済み）。
- 各レイヤーは**bbox限定の小キャンバス**（`_canvas_for_bbox`）＋ left/top オフセットで持たれており、差分再描画・レイヤー単位キャッシュに好適な構造。
- `io/export_stack_order.py` の `apply_coma_preview_order` がレイヤーリスト順→合成順の変換を実装済み。coma_preview 行（`utils/layer_stack.py` の `COMA_PREVIEW_KIND`）を境界に side="front"/"back" の分割抽出まで対応済み。ただし `layer_object_sync._coma_stack_order` という私有関数に依存。
- `ExportOptions.dpi_override` でプレビュー用低DPI化に既対応。PNG/JPEG書き出しは数秒で完了する実績。

### レイヤー種別ごとのラスタ化コスト

| 種別 | 実装 | コスト |
|---|---|---|
| 塗り(solid) | `_render_fill_layer` | 軽 |
| 塗り(グラデ) | 同上 | **極重**（全キャンバスをPythonの二重forループ+putpixelで1画素ずつ計算。最重要ボトルネック） |
| 画像 | `_render_image_layer`（mtimeキー付き `_IMAGE_FILE_CACHE`） | 軽〜中 |
| ラスター | `io/export_raster.py`（**work_dir/raster/*.png のディスク読み前提**） | 中 |
| GP/効果線 | `_gp_layers` → `_render_gp_object_layers`（Python点単位ループ+PIL線描画） | ストローク数比例、中〜重 |
| フキダシ | `io/export_balloon.py render_balloon_layer` | 中 |
| テキスト | `_render_text_layer`（typography組版） | 中 |
| コマプレビュー | `_render_coma_preview_layer`（ディスクPNG＋コマ形状マスク） | 中 |
| コマ枠線/白フチ/背景 | Pillowポリゴン描画 | 軽 |
| **パターンカーブ(image_path)** | **書き出しパイプラインに存在しない（重要ギャップ）** | — |

### 既存の2D画像プレビュー基盤（前例）

- **ページ一覧モード**: `utils/page_preview_object.py`。`render_page` によるPillow合成PNGを平面メッシュ（`_ensure_plane_mesh`）＋Emission/Transparent Mixマテリアル（`_ensure_material`、colorspace=sRGB、照明非依存）に貼る。mtimeベースstale判定（`_preview_png_fresh_for_page`）、タイマーデバウンス（`schedule_sync_page_previews`）あり。
- **コマ編集モード**: `utils/coma_camera_refs.py`。同じくPillow合成した下絵をカメラ `background_images` に貼る。front/back 分割・紙とコマ背景を除いた透明合成PNG（`ensure_page_content_overlay`）まで実装済み。ただし別blendの一時ロード（`bpy.data.libraries.load`）経路は重い。
- **ファイルレス画像更新の前例**: `utils/coma_camera._ensure_hatching_image` が `bpy.data.images.new` + `pixels.foreach_set` + `update()` でディスクI/Oなしの画像生成をしている。インタラクティブ更新はこの経路を使える技術的裏付け。
- 既存基盤は**すべて「保存済みファイルのmtime」起点**であり、未保存の編集内容を反映するライブ経路は存在しない（新設対象）。

### 変更検知・操作フローの要点

- **全データ変更の共通ファンネル**: `utils/layer_stack.py::sync_layer_stack_after_data_change`（88ファイルから呼ばれる）。再合成トリガーの最有力挿入点。
- **プロパティ変更**: `core/` 各PropertyGroupのupdateコールバック（balloon 269箇所、effect_line 149箇所など）→ `utils/*_real_object.py` の `on_*_entry_changed` へ集約されている。個別updateには触らず、この集約層に挿す。
- **アクティブレイヤー切替**: `utils/layer_stack.py::select_stack_index` が単一経路（UIList直接クリックも `core/layer_stack.py::_on_active_layer_stack_index_changed` 経由で合流）。
- **ドラッグ・変形**: `operators/object_tool_op.py` が「ドラッグ中＝毎フレームentry更新 / 確定時＝`_finish_drag` → `sync_layer_stack_after_data_change` → undo push」の明確な2相構造。対象キーは `self._drag_keys` で列挙済み。
- **Undo/Redo**: `utils/handlers.py` の undo_post → `history_runtime.schedule_reconcile` に全面invalidateを相乗り可能。
- **GP描画**: Blender標準オペレーターに委譲。`utils/outliner_watch.py::_on_depsgraph_update_post` はGPのgeometry更新を意図的にスキップ中（拡張可能だが、描画中の高頻度発火への対策が必須）。
- **ヒットテスト**: `operators/object_tool_op.py::hit_object_at_event` 以下は全て**データモデル上の純2D幾何判定**（GPUレイキャスト・深度・ピクセル読み不使用）。選択状態も WindowManager の独自JSONプロパティ管理。**表示を2D合成画像に差し替えてもヒットテスト・選択・ハンドル表示は原理的に無傷**。

### 表示オブジェクトの分類

- **見た目専用（2D合成へ置換可能）**: fill / image / image_path / balloon一式 / text平面 / effect表示Mesh / raster平面（非ペイント時）/ coma_plane背景 / coma_white_margin / paper_bgの白 / work_info_text。
- **3Dのまま残す**: GP Object（描画編集ターゲット、遮蔽バグ未発生）、ペイント中のraster平面、編集オーバーレイ類（gradient_handle、テキストカーソル、コマ枠ハンドル、paper_guide、カメラ）。
- **保存互換**: 正本データは .bmanga フォルダのJSON＋ラスターPNG＋page.blend内GPデータ。表示Objectは `_saved_runtime_objects_look_current`（`utils/layer_object_sync.py`）の fast path が依存しており、生成停止時はこの層の書き換えが必要。旧実体purgeは `purge_legacy_masks_collection` が前例。

---

## ③ 最終推奨設計: 「単一レンダラー × 描き味防衛ハイブリッド」

### 基本原則

1. **唯一のレンダラー原則**: プレビューは `build_page_layers` + `_flatten_layers` + `apply_coma_preview_order` を `dpi_override` だけ変えて呼ぶ薄いラッパとし、**プレビュー専用の描画コードを一切持たない**。書き出しに無い機能（パターンカーブ）は書き出し側に追加してから使う。プレビューと書き出しの画素一致を golden 同値テストで常設検証し、この原則をプロジェクトAGENTS.mdに明記する。
2. **描き味防衛3原則**:
   - (a) アクティブレイヤー（GP/効果線コントローラ/ペイント中raster/選択中の操作対象）は**常に3D実体のまま**、front/back 2枚の合成板で挟む（sandwich構成）。描画中に合成更新が原理的に不要になる。
   - (b) 入力イベント経路（ペン描画・MOUSEMOVE・updateコールバック）では **dirtyマーク以外何もしない**。合成は `bpy.app.timers` のデバウンスキューでのみ実行。`PAINT_GREASE_PENCIL` モード滞在中はキュー凍結。
   - (c) `ExportLayer`（bbox限定画像）を stack_uid キーでキャッシュし、変更レイヤーのみ再ラスタ化→再flatten（差分再合成）。

### 表示の仕組み

- 合成結果は `page_preview_object.py` の平面＋Emission/Transparent Mixマテリアル方式を共通化して流用（colorspace=sRGB、照明非依存）。
- 画像転送はディスクを経由せず `bpy.data.images` + `pixels.foreach_set` + `update()`（`coma_camera._ensure_hatching_image` パターン）。ディスク保存（page_preview.png）はページを閉じる時のみ。
- 通常時は全レイヤー1枚合成。アクティブがGP/効果線/ペイント中rasterのときのみ front板＋アクティブ3D実体＋back板 の3層。sandwich でもEEVEE透過は2〜3枚に減るため制御可能な範囲（Phase 0 でプロトタイプ実証）。
- プロパティ調整の体感対策として「**低解像度で即時反映→高解像度で遅延差し替え**」の2段更新を合成サービスの基本機能に含める。

### 変更検知・再合成トリガー（挿入点は4系統に限定）

1. `sync_layer_stack_after_data_change` — dirty化＋デバウンス再合成の一元挿入
2. `on_*_entry_changed`（`utils/*_real_object.py` 層）— 該当 stack_uid の dirty マーク
3. `select_stack_index` / `apply_stack_order` — sandwich切替・再flatten（並べ替えは再ラスタ化なし）
4. undo_post（`history_runtime.schedule_reconcile`）— 全面invalidate

`los.suppress_sync` / `history_runtime.is_restoring` の再帰抑止ガードを尊重し、合成処理からdepsgraph再トリガーのループを作らない。

### ヒットテスト・選択

現行の純2D幾何判定をそのまま使う（表示方式非依存のため変更不要）。`coma_hit_visibility.world_point_visible_in_parent` のレイヤー前後考慮の有無だけ実装前に精読する。

### ドラッグ・変形

ドラッグ開始時に `_drag_keys` 対象を除いた front/back をキャッシュから再flatten（再ラスタ化ゼロ）し、対象のキャッシュ済みbbox画像を一時平面に貼ってオフセット追従。回転・自由変形中はPIL affine近似、確定時（`_finish_drag`）に正式再ラスタ化。

### 保存互換・フォールバック

- GP Object・効果線コントローラ・rasterのImage参照は従来どおり .blend 保存を継続。
- 全フェーズを**実験フラグ（既定OFF）**配下に置く。Phase 3〜5 は見た目専用実体の生成を続けたまま hide するだけなので、フラグOFFで即時に現行表示へ戻せる。
- 生成コードの物理削除は最終フェーズ完了＋最低1リリース温存＋問題報告ゼロ確認後。
- 旧ファイル→新版は `purge_legacy_masks_collection` 同型の開時migrationで吸収。新ファイル→旧版は旧版mirrorが実体を自前再生成するため開ける（fast pathが外れ初回が遅くなる旨をマニュアルに明記）。

### 不採用とした案

「問題レイヤーだけ per-layer マテリアル焼き込み（各3D平面のテクスチャをPillowラスタに置換）」は不採用。レイヤー間の合成は依然EEVEE透過ソートに委ねられ遮蔽バグ解消の根拠が薄く、書き出しとも最終形とも異なる「第3の描画経路」を一時的に作ることになり、唯一のレンダラー原則に反する使い捨て実装になるため。

---

## ④ フェーズ分割された実装計画

遮蔽バグ群が実際に消えるのは Phase 3（表示切替）だが、**Phase 0 の最小プロトタイプで方式の有効性（遮蔽バグ消失）を着手直後に実機実証する**。Phase 0〜2 は表示に一切触れないため既存機能への回帰リスクが低い。

### Phase 0: 実測・最小プロトタイプ検証・前提整備（GO/NO-GO判断点）

- **目的**: 方式全体の有効性実証と性能前提の確定。中止してもここまでの成果は書き出し改善として残る。
- **変更対象**:
  1. 遮蔽バグ再現シーンで「front/back合成板2枚（Emission+Transparent Mix、薄いZ帯）＋GP/効果線実体」の最小プロトタイプを手動スクリプトで構築し、効果線×グラデ・raster×効果線の遮蔽消失を実機確認。
  2. `io/export_pipeline.py::_render_fill_layer` のグラデーションputpixel二重ループを numpy 化（書き出し高速化・PSD8分問題への効果も同時測定。golden比較は許容誤差±1/255）。
  3. パターンカーブ(image_path)が現行書き出しに出ない事実をユーザーへ確認の上、`io/export_image_path.py` として新規レンダラーを**書き出しパイプラインへ**追加（プレビュー専用実装にしない）。
  4. `layer_object_sync._coma_stack_order` を公開APIへ昇格。
  5. 計測: `build_page_layers` 全量時間（実寸/50%/25%）、1レイヤー再ラスタ時間（効果線多ストローク含む）、`pixels.foreach_set` 転送時間（長辺1024/2048/4096）、GPペイント中の `depsgraph_update_post` 発火頻度。
- **完了条件（数値ゲート）**: 25%DPI全量合成1秒以内・1レイヤー150ms以内・foreach_set(2048)100ms以内。未達なら後続へ進まず設計に立ち返る。
- **リスク**: image_path追加は既存作品の書き出し結果を変える（ユーザー確認必須）。グラデnumpy化のgolden崩れ。プロトタイプで遮蔽バグが消えない場合は計画全体を再検討（最重要リスクを最初に潰す）。
- **検証**: 既存PNG/JPEG書き出しのgolden比較全件パス。プロトタイプのスクリーンショットで既知遮蔽バグ2種の消失確認。計測値一覧を本計画書へ追記してGO判定。

#### Phase 0 実施結果（2026-07-22・Sonnet 5・実機Blender 5.2 LTSで実測、証跡: `_verify/2026-07-22_phase0_sandwich_prototype/`）

**判定: GO（本体 Phase 1 以降へ進める）**

1. **sandwich最小プロトタイプ（最重要リスク）**: グラデーション塗り×効果線で実機EEVEEレンダリングを行い、同一ズーム画角での比較により「現行方式（グラデーション/効果線とも3D実体）では効果線がほぼ視認不能」「sandwich方式（グラデーションをPillow合成の2D板に置換＋効果線は3D実体のまま）では効果線が完全にクリアに視認できる」ことを画像で確認した（`_verify/2026-07-22_phase0_sandwich_prototype/gradient_baseline_with_effect.png` vs `gradient_sandwich_with_effect.png`）。**遮蔽バグ消失を確認**。
   - raster×効果線も同一手法で検証したが、今回のテスト構成（コマ全面を覆う不透明ラスター＋流線効果線）では現行方式でも効果線がある程度視認できており、sandwich方式との劇的な差は確認できなかった（raster側は元々の再現条件が不安定という前回セッションの知見と整合）。raster×effectの完全解決の実証はPhase 3以降の実データでの確認に持ち越す。
   - 検証中、2D合成板の座標変換で2つのバグを発見・修正して確定した実装パターンを得た: (a) `bpy.data.images.new()` で生成した画像に `pack()` を呼ぶと `foreach_set()` で書き込んだ画素データが失われる（`pack()` を呼ばず `foreach_set()` → `update()` の順のみにすること）。(b) ページワールドオフセットは `layer_object_sync._resolve_page_world_offset_mm()`（Phase 0-4で `coma_stack_order` 同様に公開APIへ揃えることを推奨）を直接使うこと。fill実体の `location` から逆算すると原点規約の違いで不整合になる。
2. **グラデーションnumpy化**: 導入済み（`io/export_pipeline.py::_render_fill_layer`）。3種類のグラデーション×3サイズ（64×64/183×259/301×187）で旧putpixelループ実装との**ピクセル完全一致（誤差0/255、許容誤差±1/255の目標を上回る精度）**を確認。速度は同条件で**26.7倍**、実寸B4・600dpi（6071×8598px、約5220万画素）で**線形2.88秒・放射2.58秒**（旧実装は同条件で外挿すると約8分相当 — AGENT_INBOX記載の「作品ファイル起点PSD書き出しが8分超」と時間感覚が一致し、主因の有力な物的証拠となった）。
3. **パターンカーブ(image_path)の書き出し欠落**: 実機確認で確定後、ユーザー承認を得て**実装完了**（`io/export_image_path.py` 新規作成 + `build_page_layers` へ組み込み）。ビューポートと同じ幾何ヘルパー(`utils/image_path_object.py` の私有関数群)と配色ロジック(`utils/path_content.py`)を再利用し、スタンプ(図形/画像)・リボン(repeat/stretch)両対応、2倍スーパーサンプリング、linear→sRGB変換済み。回帰テスト `test/blender_image_path_export_check.py` を新設(出力位置・リボン・コママスク・非表示・コマ内リスト順反映の5項目)。検証中に判明した留意点2件: (a) entryをスクリプトから直接構築する際は `image_path_object.suspend_auto_sync()` で囲まないと、構築途中のビューポート自動同期が `path_points_json` を座標書き戻しで破壊する(テスト作法として同テストに記録)。(b) 書き出しのリスト順反映(`apply_coma_preview_order`)は「コマプレビュー行が書き出しレイヤー内に存在する時だけ」発火するため、プレビューPNG未生成の未編集コマではコマ内の並べ替えが書き出しへ反映されない(既存仕様・全レイヤー種別共通。2D合成プレビュー本体設計への入力事項)。
4. **`_coma_stack_order` の公開API昇格**: 完了。`layer_object_sync.coma_stack_order()` にリネームし、`io/export_stack_order.py` の呼び出し元も追従。回帰確認済み。
5. **性能計測**: 代表的な重量ページ（グラデーション・効果線80本・ラスター・フキダシ・テキストを含むコマ1つ）で計測。
   - `build_page_layers`+`render_page` 全量: 100%(600dpi,6071×8598px)=7.56秒 / 50%(300dpi)=1.77秒 / **25%(150dpi)=0.39秒**（ゲート1秒以内を満たす）
   - 1レイヤー単体再ラスタ（グラデーション, 1200×1600px）=0.057秒、効果線の差分寄与=0.016秒（**ゲート150ms以内を満たす**）
   - `pixels.foreach_set` 転送: 長辺1024px=1.6ms / 長辺2048px=5.3ms（**ゲート100ms以内を満たす、19倍の余裕**）/ 長辺4096px=26.1ms
   - GPペイント中の `depsgraph_update_post` 発火頻度: **未計測**。ヘッドレス実行では実際のペン入力（MOUSEMOVEイベント連打）を再現できないため、この項目のみ次の判断点（Phase 3着手前、または実機GUIでの手動計測）に持ち越す。プログラム的な単発更新では1回のみ発火することは確認済みだが、実際の連続描画時の頻度の代替にはならない。
6. **数値ゲート判定**: 25%DPI全量合成1秒以内 ✅ (0.39秒) / 1レイヤー150ms以内 ✅ (57ms) / foreach_set(2048)100ms以内 ✅ (5.3ms) — **全ゲート達成**。

### Phase 1: 合成サービス＋レイヤーキャッシュ＋ファイルレス出力（表示は変えない）

- **変更対象**: 新規 `utils/preview_composite.py`（＋必要なら `preview_composite_cache.py`、各1000行以内）。stack_uid キーの ExportLayer キャッシュとdirty判定、差分再ラスタ化、`apply_coma_preview_order` の front/back 分割を「アクティブレイヤー位置での分割」へ一般化する引数追加、`pixels.foreach_set` によるファイルレスImage転送、低解像度即時＋高解像度遅延差し替えの2段更新、`bpy.app.timers` デバウンスキュー。表示平面は `page_preview_object` の `_ensure_plane_mesh`/`_ensure_material` を共通化。raster を `bpy.data.images` のメモリ内画素から読む入力オプションを `io/export_raster.py` へ追加。デバッグ用手動合成オペレーターのみ追加。
- **リスク**: `bpy.context` 暗黙依存（bmanga_*_layers群）をタイマー文脈で使う際のscene明示引き回し。キャッシュメモリ上限とLRU設計。colorspace/アルファ前乗算の整合ミス。
- **検証・完了条件**: **golden同値テスト常設** — 同一ページ・同一DPIで書き出しPNGと合成バッファの画素一致をheadlessで機械検証（front+back+紙＝`render_page` 一致）。キャッシュ有効時の再合成時間短縮を計測確認。

### Phase 2: 更新トリガーの一元接続

- **変更対象**: 上記③の4系統（`sync_layer_stack_after_data_change` / `on_*_entry_changed` 層 / `select_stack_index`・`apply_stack_order` / undo_post）にのみ挿入。数百のupdateコールバック個別には触らない（挿入箇所チェックリストを作成して機械的に消し込み）。
- **リスク**: 再入ループ（合成処理がdepsgraphを触って再トリガー）。`on_*_entry_changed` の網羅漏れ。
- **検証・完了条件**: headless微細挙動テスト — プロパティ変更/並べ替え/undo・redo/フォルダ操作の各ケースで合成バッファの画素アサート。デバウンス中の連続変更が最終状態と一致すること。

### Phase 3: 表示切替（実験フラグON時のみ）— 遮蔽バグ解消の本丸

- **変更対象**: フラグON時、見た目専用実体を `hide_viewport=True`（**生成は継続**＝即時フォールバック維持）。`select_stack_index` の入口/出口で sandwich 切替（アクティブがGP/効果線/ペイント中rasterなら front/back 分割、それ以外は1枚）。切替順序は「先に合成更新→後に実体hide」で二重表示を防止。`PAINT_GREASE_PENCIL` 滞在中は合成キュー完全凍結、`_leave_grease_pencil_draw_modes` で焼き込み。`outliner_watch` のGP geometryスキップは維持。
- **リスク**: アクティブ切替時の再分割レイテンシ（キャッシュ済みなら貼り合わせのみの見込み、超過時は2段更新へ）。GP表示中のTAA未settle問題は残存（既存同等）。`coma_hit_visibility` とクリック優先順位の整合は実装前に精読。
- **検証・完了条件**: 既知遮蔽バグ2種の再現手順で消失確認（DITHERED緩和に依存しない状態で）。`_verify` 常設目視監査にケース追加。**ペン高速連続ストローク中のFPSがフラグOFF時と同等**（描き味の合否基準）。リスト順＝見た目順の並べ替えテスト。Undo/Redo連打の整合性。

### Phase 4: ドラッグ・変形の追従（Phase 3と連続実施しユーザー官能評価）

- **変更対象**: `_start_object_drag`/`_start_rotation_drag`/`_enter_free_transform` 開始時に対象抜き front/back をキャッシュ再flatten（再ラスタ化ゼロ）、対象のキャッシュ済みbbox画像を一時平面に貼りオフセット追従（回転・自由変形中はPIL affine近似）。`_finish_drag`/`_confirm_free_transform` → `sync_layer_stack_after_data_change` 経由で正式再ラスタ化。フラグON時は非表示実体のupdate再生成コストを `suspend_auto_sync` 相当でドラッグ2相に合わせ抑止。
- **判断点**: **P3+P4完了時点でユーザーに実機を触ってもらい、方式の官能合否を取る**（最終フェーズ前の主要判断点）。
- **リスク**: ドラッグ開始の対象抜き再flattenが唯一の同期処理 — Phase 0実測で予算超過ならアクティブ選択時の先行計算へ。affine近似と確定表示のズレの許容度はユーザー確認。
- **検証・完了条件**: ドラッグ中フレーム時間16ms以内の実測。確定後合成と書き出しのgolden一致。回転・変形の近似/確定差の目視監査。

### Phase 5: ズーム解像度戦略（生成停止より前に品質を確定）

- **変更対象**: ビュー距離連動の段階DPI（25%/50%/100%、長辺上限4096px）をデバウンス＋ヒステリシス付きで実装。コマクローズアップ時はコマ矩形限定の部分合成（`build_page_layers` へコマ矩形クリップ引数追加。全量→crop方式は使わない）。切替は再合成完了後に差し替える二重バッファ。解像度%は `bmanga_page_preview_resolution_percentage` の姉妹設定として公開。
- **判断点**: B4/600dpi実寸は上限4096を超え完全等倍のシャープさは出ない制約をユーザーへ明示し、許容ラインを確認。許容されない場合はタイル化の追加設計が必要（その判断を Phase 6 より前に行うための順序）。
- **検証・完了条件**: ズーム連続操作のFPS計測とちらつき目視監査。各解像度段の合成時間実測で既定値決定。ここでユーザーの品質合否を確定。

### Phase 6: 3D実体の生成停止・Boolean撤去・保存互換（全検証合格後のみ）

- **変更対象**: `mirror_work_to_outliner` の見た目専用実体生成を停止し、`purge_legacy_masks_collection` と同型の開時migration purgeを追加。`mask_apply.py` のBoolean Modifier注入撤去（クリップは `export_group_masks`/`export_soft_mask` が担当）。`_saved_runtime_objects_look_current`（fast path）・`page_file_scene.py` のpurge系・`outliner_watch.mark_entry_counts_synced` の期待集合を合成キャッシュ鮮度判定ベースへ書き換え。GP Object・効果線コントローラ・rasterのImage参照は従来どおり保存継続。ディスク保存はページを閉じる時のみ（`sync_page_previews` へ合成結果を流用）。ユーザーマニュアル・CHANGELOG更新（新ファイルを旧版で開くと初回全再生成で遅い旨を明記）。
- **前提**: 着手前にこのフェーズ限定の徹底チェック級影響調査を再実施。フラグ既定ON化後も生成コードを最低1リリース温存し、問題報告ゼロ確認後に物理削除。
- **リスク**: 全フェーズ中最大の影響範囲。fast path/purge/件数照合の書き換え漏れ→開くたび全再生成の退行や誤purge。
- **検証・完了条件**: 旧版ファイル→新版/新版ファイル→旧版の互換テスト（自動＋手動）。ファイルオープン時間の前後計測。フルテストスイート全件グリーン。golden一式。リリース前チェックリスト実施。

---

## ⑤ 着手前に実機検証すべき前提（Phase 0 の中身）

1. **【最重要】** 遮蔽バグ再現シーンで sandwich 最小プロトタイプ（front/back合成板2枚＋GP/効果線実体）を組み、効果線×グラデ・raster×効果線の遮蔽が消えることを実機確認（消えなければ計画全体を再検討）。
2. 代表的な重量ページでの `build_page_layers` 全量合成時間を dpi_override 別（実寸/50%/25%）に実測 — 目標: 25%で1秒以内（グラデnumpy化後）。
3. `img.pixels.foreach_set` の転送時間を長辺1024/2048/4096で実測 — 2段更新の解像度設計の根拠。
4. Blender 5.2 LTS でGPペン描画中（ストローク進行中）の `depsgraph_update_post` 発火頻度・粒度の実測 — 合成キュー凍結とデバウンス値の根拠。
5. パターンカーブ(image_path)が現行PNG/PSD書き出しに本当に出力されていないかの実機確認＋意図的仕様か否かのユーザー確認（書き出し結果が変わる変更のため）。
6. GP/効果線のPillowラスタ品質（丸キャップ・joint近似・点毎opacity16分割）がズーム表示でユーザーの目視に耐えるかのサンプル確認。
7. `coma_hit_visibility.world_point_visible_in_parent` がレイヤー前後（手前レイヤーによる遮蔽）まで考慮するかの精読 — 2D合成後の見た目とクリック優先順位の整合確認。
8. 1レイヤー単体の再ラスタライズ時間（特に効果線・GP多ストローク）実測 — 差分再合成の実効性とデバウンス窓の根拠。
9. 作品ファイル起点PSD 8分超問題の主因切り分け（グラデputpixel/page.json逐次読込/PSDラスタ量）— Phase 0のnumpy化前後で計測し、合成パイプライン流用時に同じ地雷を踏まないことの確認。

---

## ⑥ 全体規模見積もりと推奨実行モデル

- **開発セッション 13〜19回が目安**（計画書ありのため **Sonnet 5級での実装を推奨**）。
  - Phase 0 = 2〜3（プロトタイプ検証＋numpy化＋image_path新規レンダラーが重い）
  - Phase 1 = 2〜3 / Phase 2 = 1〜2 / Phase 3 = 2〜3 / Phase 4 = 2 / Phase 5 = 1〜2 / Phase 6 = 2〜3＋最終全体回帰1
- **高リスク箇所**: (1) Phase 0 のプロトタイプ検証（方式が否定されれば計画停止 — ただし早期に判明するのが利点）、(2) Phase 3 のアクティブ切替レイテンシと描き味FPS、(3) Phase 6 の fast path/purge/件数照合の書き換え（影響層が最も広い）。
- Phase 0 の実測が目標未達の場合、numpy合成化やコマ単位部分合成の追加設計で **+2〜4セッションの上振れ**があり得る。
- Phase 0〜2 は表示に触れないため回帰リスクが低く、Phase 3 以降も実験フラグ既定OFFでリリースを跨げる（リリース準備と並行可能）。
- 設計判断が必要な問題（計画前提の崩れ・影響範囲の想定超え）が出たら、Sonnet 5のまま進めず上位モデルでの再調査・計画書更新へ戻す。

---

## ⑦ 未決事項（ユーザー判断が必要な点）

1. **【回答済み: 実装する】パターンカーブの書き出し追加**: 2026-07-22 ユーザー承認済み・実装完了（`io/export_image_path.py`、Phase 0 実施結果の項3参照）。
2. **【回答済み: GO】Phase 0 完了時点のGO/NO-GO**: 2026-07-22 実施。sandwich最小プロトタイプで遮蔽バグ消失を確認、数値ゲート3項目すべて達成。本体（Phase 1以降）へ進める判断とした。詳細はPhase 0節の「実施結果」参照。
3. **P3+P4 完了時の官能合否**: 実機を触ってもらい、描き味・見た目の合否判定。
4. **ズーム時の等倍シャープさの許容ライン**（Phase 5）: 長辺上限4096pxの制約で完全等倍のシャープさは出ない。許容できない場合はタイル化の追加設計（＋セッション増）。
5. **コマ枠線(coma_border)の扱い**: 合成画像に含めるか、シャープさ維持のため3D Curveのまま残すか。
6. **回転・自由変形中のaffine近似のズレの許容度**（Phase 4 で実物確認）。

---

## チャット報告用の1〜2行説明

「レイヤーの重なり順がたまに崩れる問題を根本から直すため、画面表示を書き出しと同じ確実な合成方式に切り替える計画です。まず2〜3回の準備セッション（Phase 0）で効果を実機確認してから本体に進むので、無駄になるリスクは小さく抑えてあります。」

着手の一言: 「**coma_2d_composite_preview の計画書の Phase 0 を実行して**」でPhase 0（実測・プロトタイプ検証）に着手できます（Sonnet 5での実行を推奨。ただしPhase 0はGO/NO-GO判断を含むため、判断部分は上位モデルでも可）。
