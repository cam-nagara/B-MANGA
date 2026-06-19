# B-MANGA / B-MANGA Render AI監査マニフェスト

最終更新: 2026-05-09 (Codex)

## 目的

B-MANGA本体とB-MANGA Renderを、コード・仕様・Blender実機・AI目視の複数観点で確認するための標準手順です。

通常のテストは「内部状態が正しいか」を確認します。AI監査ではそれに加えて、ページ、コマ、コマ枠、テキスト、フキダシ、効果線、ラスター、画像、出力画像が実際に見える証拠を生成し、AIに画像とJSONを見せて確認させます。

## 実行コマンド

このチャットで **「AI監査」** とだけ言えば、AIエージェントは確認を挟まずに完全監査を実行します。

標準監査:

```powershell
python test/bmanga_ai_audit_runner.py --profile standard --keep-going
```

B-MANGA Renderの実レンダーを含む完全監査:

```powershell
python test/bmanga_ai_audit_runner.py --profile full --keep-going --include-slow
```

画面スクリーンショットを使う監査も含める場合:

```powershell
python test/bmanga_ai_audit_runner.py --profile full --keep-going --include-slow --allow-ui
```

PowerShellから同じ監査を実行する場合:

```powershell
.\test\run_ai_audit.ps1
```

短時間確認:

```powershell
.\test\run_ai_audit.ps1 -Fast
```

出力先を固定する場合:

```powershell
python test/bmanga_ai_audit_runner.py --profile standard --out-dir .codex/ai_audit/latest --keep-going
```

既定のBlender実行ファイルは `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe` です。別の場所にある場合は `--blender` または `BMANGA_BLENDER_EXE` で指定します。

## 生成物

監査ランナーは `.codex/ai_audit/<日時>/` に次を生成します。

| 生成物 | 用途 |
|---|---|
| `summary.json` | 実行結果の要約 |
| `audit_manifest.json` | 監査項目、結果、証拠ファイル一覧 |
| `AI_REVIEW_PROMPT.md` | AIにそのまま渡す監査依頼文 |
| `inventory/script_inventory.json` | 全スクリプトの行数、クラス、関数、注意フラグ |
| `inventory/script_inventory.md` | 人間/AIが読みやすい棚卸し表 |
| `inventory/code_review_batches.md` | 3〜4ファイル単位のAIコード監査バッチ |
| `cases/*/stdout.txt` | 各Blender実機テストの標準出力 |
| `cases/*/stderr.txt` | 各Blender実機テストのエラー出力 |
| `cases/*/evidence/*` | AI目視用画像、JSON、SVG |

## 監査プロファイル

| プロファイル | 内容 |
|---|---|
| `inventory` | コード棚卸しと構文確認のみ |
| `standard` | B-MANGA本体とB-MANGA Renderの主要な実機監査 |
| `visual` | AI目視用画像を作る監査のみ |
| `render` | B-MANGA Render関連のみ |
| `full` | c00連動、全プリセット、画面操作監査を含む最大構成 |

`full` でも、`c00.blend` や eeVR zip が見つからない項目はスキップされます。場所を変える場合は `--c00-blend`、`--eevr-zip`、または同名の環境変数で指定します。

## AIに見せる順番

1. `AI_REVIEW_PROMPT.md`
2. `summary.json`
3. `audit_manifest.json`
4. `inventory/script_inventory.md`
5. `cases/*/evidence/` 内の画像
6. 失敗または違和感のある項目の `stdout.txt` / `stderr.txt`
7. 必要に応じて `inventory/code_review_batches.md` の各バッチ

## AIへの報告要求

AIには次の形式で報告させます。

```text
重要度: 高/中/低
対象: B-MANGA または B-MANGA Render
問題: 具体的な症状
根拠: 確認した画像/JSON/ログ
影響: ユーザーにどう見えるか
修正方針: 最小限の方針
```

スタイルやコメントの好みは無視し、実際にユーザー操作で問題が起きるものだけを報告対象にします。

## 現在の対象軸

- ページ、コマ、コマ枠、下絵、用紙背景
- テキスト、フキダシ、効果線、ラスター、画像、線画系レイヤー
- 表示/非表示、ロック、詳細設定、マスク、表示順
- アウトライナー、レイヤー順序、ページ直下/コマ内/ページ外の配置
- 保存、再読み込み、アドオン無効時の見え方
- B-MANGA Renderのパネル、プリセット、カード、出力設定
- c00連動、魚眼出力、レンダー準備、出力画像

監査対象を増やした場合は、既存の `test/blender_*_check.py` 形式で個別テストを追加し、`test/bmanga_ai_audit_runner.py` の監査ケースに登録します。
