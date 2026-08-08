# 本体安定化 Phase R1 判断記録

日付: 2026-08-08

## 維持した利用者契約

- 通常の複製と削除は、レイヤー一覧で選択中の1件だけを対象にする。
- 通常複製はリンクを継承しない。リンク複製だけが明示的にリンクを作る。
- リンク中の1件を通常削除しても相手を削除せず、2件未満になったリンク集合だけを解消する。
- フォルダ削除は中のレイヤーと子フォルダを削除せず、一段上へ維持する。
- Alt+D&Dは確定前に正式データを変更しない。キャンセル、失敗、Undoで操作前へ戻る。

## 縮小した範囲

- UIから到達せず、上記契約と異なる「リンク対象や子孫をまとめて複製／削除する」専用Domain Commandは削除した。
- Phase R1はB-MANGA本体だけとし、Render／Linerの製品コードはmainとの差分0を維持した。
- 全面Composition移行、全面Interaction Kernel移行、独立目的の巨大ファイル分割は後続Phaseへ持ち込まなかった。

## 失敗時の契約

- Domain確定失敗はPropertyGroup、Domain binding、native GP／効果線、実Object、ラスターPNG、保存基準を同じ操作前snapshotへ戻す。
- 実Objectは不足だけでなく余剰も完全一致で拒否する。
- ラスターPNGを先に戻してからPropertyGroupと実Objectを再投影する。
- rollbackを完遂できない場合は作品を未読込状態へ移し、保存と追加編集を止める。

## 合否

- 対象unit／contract／Blender実機／headed UI: 合格
- 独立レビュー: Critical 0 / High 0
- 通常Extension: 未配備
