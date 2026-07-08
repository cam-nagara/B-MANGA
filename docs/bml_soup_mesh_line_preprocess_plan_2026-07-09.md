# B-MANGA Liner: ポリゴンスープ資産のライン前処理 修正計画書（2026-07-09）

## 背景（2026-07-09 調査セッションの結論）

tokyo0004 (`D:\TM Dropbox\Share\Assets\Japanese Streetscape Tokyo 0004\Japanese_Streetscape_Tokyo_0004.blend`) での実機テストで、v0.3.163（誤カリング修正）・v0.3.164（境界チューブのSolidify反転打ち消し）適用後も、輪郭カバレッジ92.8%（正解=オブジェクトIDレンダリングの輪郭、計測: `_verify/2026-07-09_tokyo0004_outline_coverage/run_coverage_check.py`）で頭打ちになる。

残る欠落と黒パッチの根本原因は**メッシュのポリゴンスープ化**（頂点が面ごとに分割され、面同士が非連結。FBX/ゲームアセットに典型）:

1. **細い柱の線欠落**（街灯 S_JPStreetLight_0_012/014、消火栓 S_HydrantBillboard_0_002、バナー支柱 Object25668 等）
   - 背面法シェルは面の法線方向にしか膨らまないため、非連結の面ではシルエット横方向への拡張が起きず、線が構造的に描けない。
   - 検証済みの否定仮説: 線幅ウェイト（値は正常）／法線再計算（非連結のため向き不定で無効）／リム面可視化（差分ゼロ）。
   - レイ列挙（`ray_enumerate.py`）で「culled面→不可視リム→白面→白の背後の発光面」の並びを確認済み。
2. **黒パッチ**（S_JPBuildingA_2_002 の窓パネル等）
   - 法線が内向き/混在の面で、シェルの発光面が表面を覆う。非連結が原因で recalc_face_normals 単体では直らない。

## 方針（採用案）

「ライン適用」（`bmanga_line.apply` → `presets.apply_line_settings` → `outline_setup.apply_outline`）の前処理として、対象メッシュに**距離ウェルド＋法線再計算**を行う。

### 実装内容

1. `subdivision_lod.quadrangulate_mesh_for_auto_subdivision` と同じ流儀で新関数を作る（配置先は新ファイル `addons/b_manga_line/mesh_line_repair.py` を推奨。既存巨大ファイルへは追加しない）:
   - 共有メッシュ（users>1）は対象オブジェクト専用にコピーしてから処理（quadrangulate の既存実装を踏襲）。
   - bmesh で `remove_doubles`（しきい値はオブジェクトローカル単位で、ワールド換算 0.1mm 程度を目安にスケール除算）→ `recalc_face_normals` → to_mesh。
   - 実行条件: 「バラバラ面」判定（例: 境界エッジ数/全エッジ数 が高い、または頂点数≒面数×頂点/面）を満たすメッシュのみ。判定関数は `outline_setup._mesh_boundary_edge_ratio` を再利用。
   - 一度処理したメッシュはカスタムプロパティで記録し、再適用時にスキップ。
2. UI: ライン設定に「メッシュを線用に補正（ウェルド＋法線）」チェックボックスを追加。**初期値はユーザー決定に従う**（2026-07-09 提示時の推奨は初期値オン）。オフにすると前処理をスキップ。
3. 呼び出し位置: `apply_outline` 冒頭（`ensure_surface_material_slot` の前）。「アウトラインの更新」でも同経路を通ること。

### リスクと言い訳できない点（ユーザーへ提示済みであること）

- ウェルドはUVシーム・ハードエッジ用の頂点分割を統合するため、**表面の陰影・UVが変わる可能性がある**（メッシュデータの改変。共有メッシュはコピーするので他オブジェクト・他ファイルへは波及しない）。
- カスタム分割法線（custom split normals）を持つメッシュでは法線データが失われる可能性 → 処理前に有無を確認し、持つ場合はスキップして警告を出す設計が安全。

### 検証（必須）

1. `_verify/2026-07-09_tokyo0004_outline_coverage/run_coverage_check.py` を再実行し、カバレッジ **95%以上**（現状92.8%）。
2. 同ディレクトリの `fullres_streetlight014.png` / `fullres_hydrant002.png` 相当の領域を再クロップし、柱の線の出現を目視確認（AI目視可）。
3. 黒パッチ: S_JPBuildingA_2_002 の窓パネル領域のフル解像度クロップで消失を確認。
4. 回帰: `test/blender_b_manga_line_sheet_and_proxy_follow_check.py` / `blender_b_manga_line_sheet_mesh_exclusion_check.py` / `blender_b_manga_line_preset_visibility_check.py` / `blender_b_manga_line_distance_visibility_preserves_check.py` が全てPASS。
5. tokyo0004 の「ラインを適用」所要時間が現状（約55秒/137メッシュ）から大きく悪化しないこと（+10秒以内目安）。

### バージョン・記録

- B-MANGA Liner のバージョンを上げ、CHANGELOG に症状/原因/修正/検証の形式で記録（v0.3.164 の項に前例あり）。
- AGENT_INBOX の「[P1] tokyo0004系アセットの残る線欠落・黒パッチ」を完了へ移動。

## 推奨実行モデル

Sonnet 5（計画済み実装のため）。実装中に前提が崩れた場合（ウェルドで別の破綻が出る等）は中断して上位モデルでの再調査を提案すること。
