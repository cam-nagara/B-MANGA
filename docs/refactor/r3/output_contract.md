# Phase R3 本体出力契約

## 結論

B-MANGA本体UIから到達する単ページ、複数ページ、見開き分割のPNG、JPEG、TIFF、PSD、PDFは、指定DPIを物理解像度として保持し、製品writerに依存しないreaderで再読込できる。

## 実測

- 試験用紙: 40 × 20 mm、144 dpi。見開き1件と通常ページ1件を使用。
- PNG: Pillow再読込で約143.993 dpi。pHYsの整数換算誤差内。
- JPEG／TIFF: Pillow再読込で144 × 144 dpi。
- PSD: Image Resource ID 1005を独立parserで再読込し144 dpi。レイヤー名、順序、透明maskの0／非0 alphaを確認。
- PDF: 2ページの見開き非分割と3ページの見開き分割を再読込。MediaBoxは144 dpiから算出した物理寸法と一致し、DeviceRGBを保持。
- 代表成果物と全reader記録: `_verify/2026-08-08_r3_output_external_reader/`

## 修正前後

修正前v0.6.605は単ページPNGの再読込でDPI metadata欠落を再現した。v0.6.606ではフラット画像、レイヤー付き／統合PSD、PDFへ同じeffective DPIを渡す。

PSDは既存Image Resource blockからID 1005だけを置換し、他のblockをbyte単位で維持する。psd-tools writerと内蔵fallback writerのどちらでも同じ処理を通る。見開き分割PSDを含む成果物は同一フォルダー内のstageを検証してから確定し、失敗時に既存出力を戻す。

## UIと製品境界

Panel、Operator、Property、shortcut、label、既定値、条件表示は変更していない。B-MANGA Render／Linerは同時register／unregisterを2巡しただけで、固有機能や製品コードを認定・変更していない。通常Extensionには配備していない。
