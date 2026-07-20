# キーマップ衝突回避の構造的作り直し 計画書 (2026-07-20)

## 背景・経緯

B-MANGA開発開始以降、「Blenderのショートカットキーがちょくちょく使えなくなる」という
ユーザー体感が繰り返し報告されている。関連する実インシデント:

- 2026-07-16: 編集モードの F(面張り)/K(ナイフ)/O(プロポーショナル編集)/T が
  永久に無効化される事故（`repair_stale_disabled_shortcuts` で自己修復を追加）
- 2026-07-18: 3Dビューのサイドバー開閉キー(N)が同様に無効化されたまま残る事故
  （`keymap/startup_repair.py` の起動時遅延リペアタイマーで対応）
- 2026-07-20: GP/ラスター両レイヤーで描画が一切できない事故。原因は
  `Image Paint`/`Grease Pencil Brush Stroke`等のLEFTMOUSEストロークが
  ユーザーキーコンフィグ上で無効化されていたこと（41件）。**この破損自体は
  下記「既存の衝突回避機構」が原因ではないと確認済み**（対象キー組合せに
  LEFTMOUSEは一切含まれない）が、調査の過程でユーザーキーコンフィグ全体では
  **446件**もの項目が無効化されていることが判明し、根本原因は未解明のまま
  「壊れたら都度自己修復する」対症療法を積み重ねている状態にある。

本計画は、上記の**土台となっている設計自体**（Blenderの既定キーマップ項目を
`kmi.active = False` で書き換えて衝突を退避する方式）を、根本的に
「書き換えない」設計へ置き換えることで、この種の事故の再発可能性そのものを
減らすことを目的とする。

詳細は [AGENT_INBOX.md](../AGENT_INBOX.md) の
「[P2] キーマップ衝突退避が user keyconfig を直接書き換える設計の再検討」を参照
（2026-07-16発見・2026-07-18/07-20追記）。

## 現行方式の仕組みと問題点

`keymap/keymap.py` の `KeymapState.disable_conflicting_keys()` は、B-MANGAタブが
表示されている間（`shortcut_visibility.any_shortcuts_allowed()` が True の間）、
以下を行う:

1. `_keymap_can_steal_view3d_shortcut()` で「名前に "3D View"/"Object"/"Mesh"/
   "Grease Pencil"/"Image Paint" を含む、または space_type が VIEW_3D」の
   **すべてのキーマップ**を対象として選び出す（ツール別キーマップも含む、
   かなり広い一致条件）
2. それらの中から、修飾キーなしの **O/F/K/T** に割り当てられた項目
   （Blender標準の `mesh.edge_face_add`・`mesh.knife_tool`・
   `wm.radial_control`・`wm.context_toggle` 等）を見つけ、`kmi.active = False`
   にして退避情報を `saved_conflicts` に保持する
3. B-MANGAタブが閉じられたら `restore_conflicting_keys()` で退避情報を基に
   `active = True` へ戻す

**問題点:**

- **復元はメモリ上の `saved_conflicts` 頼み。** Blenderクラッシュ・強制終了・
  タブを開いたままの終了などで復元が走らないと、無効化状態が
  `userpref.blend` へそのまま保存され、次回起動後も標準ショートカットが
  効かない。現状は「起動時に気づいて直す」自己修復（3箇所の呼び出し+
  起動タイマー）で被害を抑えているが、**壊れること自体は防げていない。**
- **対象の一致条件が広すぎる。** 名前の部分一致（`"Object" in km.name` 等）で
  拾うため、ZenUV・engon・kitops等、同梱していない他社製アドオンが
  似た命名のキーマップを持っていた場合、**そのアドオン自身のF/K/O/T
  ショートカットも巻き込みで無効化される可能性がある**（未検証・要確認）。
- **ペイント中の実害。** F(ブラシサイズのradial control)が Image Paint /
  GP描画・スカルプト・頂点・ウェイトの全モードで無効化される。これは
  設計上の意図的なトレードオフだが、ユーザーが把握していない場合
  「描画中にFが効かない」という体感の一因になり得る。
- **今回判明した446件の無効化**（41件のペイント系LEFTMOUSEを含む）は
  この仕組みが直接の原因ではないと確認済みだが、「`kmi.active`を書き換えて
  状態を保持する」という設計パターンそのものが、何らかの理由で破損に
  対して脆弱である可能性を示唆している。

## 代替方式の方向性: poll ゲート付き先取り登録

### 基本アイデア

Blenderのキーマップ解決は概ね **Tool > Mode(例: "Mesh"/"Object Mode") >
Modal > Space(例: "3D View") > Screen > Window** の優先順位で評価される。
現行方式でO/F/K/Tを退避しているのは、B-MANGA自身のO/F/K/T項目が
**Space層 ("3D View")** に登録されており、Mode層の既定項目
（例: "Mesh" キーマップの F = `mesh.edge_face_add`）より優先度が低いため。

そこで、**Blenderの既定項目を無効化する代わりに、B-MANGAの項目を
「既定項目と同じ優先度階層のキーマップ」に直接追加し、操作可能かどうかは
`poll()` で判定させる**方式へ移行する。`poll()` が False を返せば Blenderは
自動的にそのキーマップ項目をスキップして次候補（＝元々の既定項目）を
評価するため、**既定項目のactiveを一切触らずに済む。**

### 既に部分的に実証済みであること

- `_populate_object_mode_overrides()` は既にこのパターンを
  `bmanga.alt_reparent_drag`（Alt+LMB）で採用しており、コメントに
  「Tool層のpoll失敗時のfall-throughで確実に発火する」と明記されている
  ＝ **Mode層への直接登録+pollによるfall-throughは、このコードベース内で
  既に動作実績がある。**
- `bmanga.coma_knife_cut`（F）は既に `poll()` で
  `shortcut_visibility.shortcuts_allowed(context)` を判定し、`invoke()`でも
  条件を満たさなければ `PASS_THROUGH` を返す実装になっている
  （`operators/coma_knife_cut_op.py`）。
- `bmanga.set_mode_object`（O）も同様に `poll()` で `_shortcuts_allowed()`
  を判定済み（`operators/shortcut_op.py`）。
- つまり **O/F を担う2つの主要オペレーターは、操作側の条件分岐という意味では
  既に新方式に対応できる状態にある。** 未確認なのは
  `bmanga.layer_move_tool`（K, `operators/layer_move_op.py`）と
  `bmanga.text_tool`（T, `operators/text_op.py`）の poll 実装状況。

### 変更のおおまかな形

1. `disable_conflicting_keys` / `restore_conflicting_keys` / `saved_conflicts`
   による active 書き換えを廃止する。
2. `bmanga.set_mode_object`(O) / `bmanga.coma_knife_cut`(F) /
   `bmanga.layer_move_tool`(K) / `bmanga.text_tool`(T) の4オペレーターに、
   `shortcut_visibility.shortcuts_allowed(context)` ベースの `poll()` を
   （未実装なら）揃える。
3. これら4項目を、現在の "3D View"（Space層）だけでなく、O/F/K/Tの既定が
   存在しうる **Mode層キーマップすべて**に `head=True` で直接追加する。
   対象候補: "Mesh" / "Curve" / "Armature" / "Object Mode" /
   （必要なら "Lattice" / "Surface" / "Font"） /
   GP系すべて("Grease Pencil Draw/Edit/Sculpt/Vertex Paint/Weight Paint") /
   "Image Paint"。既存の `_populate_object_mode_overrides` の対象リストを
   土台に拡張する。
4. `set_bmanga_items_active` によるON/OFF切替は、これらO/F/K/T項目については
   不要になる（常時 `active=True` のまま `poll()` に判定を委ねる）。
   ※ SPACE/C/E/L/Ctrl+X/Ctrl+V 等の「ペイント中だけの便利キー」群は、
   モードキーマップ自体がB-MANGA外では評価されないため現行のON/OFF切替の
   ままで問題ない（優先度は今回のスコープ外）。
5. `ensure_paint_brush_strokes_enabled` 等、既存の自己修復関数群は
   **廃止しない**（O/F/K/T以外の原因不明の破損に対する安全網として残す）。

## 未検証・要確認事項（実装着手前に必ず解消すること）

1. **優先順位の実機検証。** 「addon kc の Mode層項目が default kc の
   Mode層項目に対して本当に優先されるか」を、対象キーマップごとに実機
   （Blender 5.2 LTS）で確認する。特にGP系キーマップはBlenderのバージョンで
   命名・階層が変わりうる（既存コードが起動時にdefault kcを走査して
   キーマップ名を動的検出しているのはこのため）。
2. **Tool層の扱い。** 現行の `disable_conflicting_keys` は space_type=VIEW_3D の
   ツール別キーマップ（例: "3D View Tool: Edit Mesh, Bisect"）にも及ぶ
   広い一致条件だが、Mode層への直接登録方式は「アクティブなツールが
   独自にF/K/O/Tを消費するケース」を救えない可能性がある。ツール層も
   対象に含めるか、割り切って許容するかを判断する。
3. **対象キーマップの網羅性。** 一覧を手動列挙する方式に切り替えると、
   Blenderアップデートで新設されるキーマップや、ユーザーが普段使わない
   オブジェクトタイプ（Surface/MetaBall等）のEdit Modeキーマップを
   見落とすリスクがある。現行の「名前の部分一致で自動検出」の網羅性の
   高さとのトレードオフを整理する。
4. **他アドオンとの共存。** 「他社アドオンのF/K/O/Tショートカットを
   巻き込みで無効化している可能性」は本計画のモチベーションの一つだが、
   実際にどのアドオンがどう影響を受けているかは未調査。新方式でこの
   巻き込みが解消されることを、代表的な同梱アドオン（ZenUV/engon/kitops等）
   で確認する。
5. **段階移行の是非。** 一気に全置換するのではなく、影響の大きい
   Mesh/Object Mode/Image Paint/GP Draw Modeから試験導入し、
   `disable_conflicting_keys` は当面フォールバックとして残す（対象4キーの
   うち移行済みのものだけ退避対象から除外する等）段階移行が安全かどうかを
   判断する。

## テスト方針

- 既存: `test/blender_shortcut_conflict_repair_check.py` /
  `test/blender_shortcut_key_conflict_check.py` /
  `test/blender_shortcut_visibility_check.py` /
  `test/blender_startup_shortcut_repair_timer_check.py` を新方式に合わせて
  更新する。
- 新規: 「B-MANGAタブの開閉・ページファイルの出入りを何度繰り返しても、
  Blender標準および他アドオンのキーマップ項目の `active` フラグが
  一度も変化しないこと」を確認する不変条件テストを追加する
  （現行方式では原理的に保証できなかった性質）。
- 新規: Mode層への直接登録後、対象オペレーターのpoll条件がFalseの状態で
  該当キーを押した際、Blender既定の操作（例: Meshモードでの面張り）が
  正常に発火することを確認する回帰テスト。

## 完了条件

- O/F/K/Tについて `kmi.active` の書き換え（disable/restore）が
  コードから消え、既定キーマップ項目のactiveへ一切触れずに衝突回避が
  成立している。
- 上記テスト方針の新規テストが揃い、全件合格している。
- 「未検証・要確認事項」がすべて実機確認済みとして解消されている。
- 移行後、少なくとも1つの実プロジェクト(page.blend)で通常のB-MANGA作業
  （ページ編集・GP描画・ラスター描画・コマ編集）を一通り行い、
  ショートカット動作に回帰がないことを確認する。

## 引き継ぎ

この計画書の実行には、Blenderのキーマップ優先順位に関する実機検証を伴う
ため、**「◯◯の計画書を実行して」の一言では着手せず**、まず「未検証・要確認
事項」の1〜2番（優先順位とTool層の実機確認）から着手することを推奨する。
実行モデルはSonnet 5級を推奨（実装自体は方針確定後の機械的な移植作業が
中心のため）。ただし優先順位の実機検証で想定と異なる挙動が見つかった場合は
設計の再検討が必要になるため、その時点でFable 5級への切替を検討する。
