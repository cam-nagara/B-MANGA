# Phase R4 独立最終レビュー

- 日付: 2026-08-08
- 対象: main `c8ad0d70c8e6` から B-MANGA Next v0.6.607 までの本体差分
- 結論: Critical 0 / High 0 / 既知のin-scopeユーザー機能不具合 0

データ損失、保存／Save As／再起動復旧、Undo／Redo、Domain・PropertyGroup・Object・JSON契約、rollback、Meldex HTTP、Asset取込、UI凍結、認定ランナー、秘密情報、Render／Liner境界を読取専用reviewerが確認した。

初回レビューでは、配布manifestに除外規則がなく検証成果物・別製品・作品データをZIPへ収録し得る問題と、保存復旧時に作品全体の古い`.bmanga-t`を所有証跡なしで削除し得る低頻度のデータ削除境界を検出した。前者はBlender公式の`[build].paths_exclude_pattern`、後者は正規の`work.blend`／`page.blend`／コマ`scene.blend`の隣かつ実在sourceに限定して修正した。

修正後の境界再レビューでは、素材フォルダーの同名ファイル、symlink／junctionによる作品外参照が削除対象にならないことを原文と回帰テストで確認し、残存データ削除リスク0と判定した。公式builderで生成したZIPも468項目を実査し、禁止物、必須物欠落、重複、path traversal、秘密情報はいずれも0だった。

本レビュー後に製品コード・テストを凍結し、本体full suiteを1回実行した。380/380、必須359/359、予期しないskip／crash／timeout／golden不整合0で合格した。通常版v0.6.599への配備とRender／Liner固有機能の認定は実施していない。
