# Phase 2 完了報告 — Settings Contract・詳細ダイアログ・プリセット

実施日: 2026-07-29
対象: B-MANGA Next v0.6.602
計画: `docs/bmanga_full_refactoring_plan_2026-07-28.md` Phase 2

## 結論

Phase 0で記録した全RNA Propertyを再分類し、ユーザー設定と永続Domain候補を機械可読な`FieldSpec`へ固定した。詳細設定の3入口、対象解決、取消境界、preset codec、UI/internal単位変換、dirty/cache/test責務を同じ契約から検査できる。

## 実装結果

- 現行RNA投影: 1,644
- 安定field ID: 1,569
- 新schema候補: 840
  - 永続Domain投影: 795
  - ユーザー設定: 45
- Session State投影: 480
- 派生表示投影: 31
- 外部アドオン境界投影: 293
- preset codec: 631
- 詳細UI binding: 210
- UI有効化条件: 17
- Phase 0台帳との差: 廃止済みOperator入力1件
  - `BMANGA_OT_coma_enter_from_list.index`

`SKIP_SAVE`、選択index、展開状態、読込状態、表示cache、旧角丸互換投影は新schemaから除外した。長さ、割合、角度、色について、Blender UI値と内部値の変換責務をFieldSpecへ記録した。

詳細設定はレイヤー一覧、右クリック、プリセット歯車の3入口を`DetailTargetRequest`と共通resolverへ統一した。OK、キャンセル、Esc、開始失敗、プリセット切替は共通のtransaction dispositionで判定する。詳細UIの全`layout.prop`はFieldSpec検査付きlayoutを通り、未登録のB-MANGA RNA項目を描画へ追加すると即時失敗する。

## 検証結果

### 設定characterization

- schema対象: 840
- 永続Domainの実`.blend` codec往復: 795
- 現行JSON codec往復: 729
  - 実encoder／decoder binding: 733
- 隔離`userpref.blend`往復: 45
- 値変更・取消・保存再読込: 805
- 構造Property型検査: 36
- owner: 34
- 実cache signature対象: 301
  - 実signature binding: 412
- Blender 5.2 LTSで全件合格

各scalar fieldは独立したCollectionProperty要素で検査し、変更後の値、キャンセル後の復元値、`.blend`保存・Blender再起動後の値を照合した。現行work/page JSONの実encoder・decoderも直接呼び、色空間変換や丸め後の期待値を固定した。フキダシ形状、しっぽ、しっぽ点、コマ頂点、レイヤー参照、ルビ・文字style span等の入れ子も親codec経由で検査した。ユーザー設定はcase固有の`BLENDER_USER_CONFIG`へ隔離し、`meldex_enabled=True`を含む実アドオン設定を`userpref.blend`へ保存・再読込した。受信サーバーはプロセス環境の明示gateで起動前に停止し、検査がローカル待受を作らないことも確認した。

全FieldSpecはfield単位の実`blend-rna`／`json-adapter`／`userpref` binding、dirty callback、cache依存、個別characterization IDを持つ。JSON対象729 fieldは733本の実adapter結線へ、cache対象301 fieldは412本の実signature結線へ接続した。フキダシの通常style、入れ子shape、効果線由来の全ウニフラ／白抜き項目は実preset snapshot/applyを双方向に通した。

### 対象検証

- 詳細ダイアログ必須: 25/25
- プリセット必須: 20/20
- 設定契約純Python: 17/17
- 詳細契約／認定基盤を含む補助純Python: 62/62
- 初回全検証で検出した2ケースの修正後対象再検証: 2/2
- RNA Collection参照安定性の回帰: 10/10反復合格

### 最終統一認定

- Gate: PASS
- 全case: 459/459
- 必須: 426/426
- passed: 426
- historical: 21
- support: 12
- failure / skip / timeout / crash: 0
- 実行時間: 5,422.789秒
- 現行source hash不一致: 0
- 欠落結果／gate error: 0
- 証跡: `_verify/2026-07-29_phase2/full_all_final_05`

途中の初回全検証では、完全一致名`percentage`を割合単位として扱わない生成規則を検出して修正した。次の走行では既存のフキダシ多重線視覚検査がBlender RNA Collection拡張後の古い要素参照を保持する不安定性を検出し、stable IDで毎回再解決するよう修正した。

最終候補の初回全459件では、詳細設定後のObject Tool復帰テストが右クリックメニューの先頭選択に依存して180秒で停止し、詳細子操作テストがページCollection追加前の古いRNA参照を保持して1件失敗した。前者は右クリックメニューと同じ固定`bmanga_id`／`kind`で製品Operatorを直接起動し、復帰後の選択維持・実ドラッグ・OK／キャンセルを検証する決定的ケースへ変更した。右クリックのkeymap、対象選択、メニュー項目、Operator接続は別の必須ケースで維持した。後者は追加前に安定IDを固定し、正本CollectionPropertyから対象を再取得した。対象2/2と独立再レビュー合格後、全459件を新しい証跡先で最初から再実行した。

## 独立レビュー

初回独立レビューは重大0・高4。指摘は、実codecを通らないcharacterization、フキダシ入れ子／コマ背景を欠くpreset台帳、未知B-MANGA ownerをBlender標準RNAとして許す検査、codec／dirty／cache／testの抽象ラベルだった。

4件の修正後も、JSON adapterの過大申告、callback名だけのcache分類、ユーザー設定検査中の受信サーバー起動、条件付きフキダシJSONの前提復元、`meldex_enabled=True`の保存確認不足を独立再レビューで検出し、すべて修正した。`_mm`名はBlender `unit=LENGTH`より優先し、`BMangaTextEntry.ruby_gap_mm`等をUI mm／内部mmとして固定した。

最終コードレビューは重大0・高0・中0。全検証で検出した2件のテスト修正後も別の独立再レビューを行い、製品不具合を隠さず契約の検出力を維持しているとして重大0・高0・中0で合格した。
