# Phase 1 認定基盤

## 一括認定

`test/certification_manifest.json` は `test/*.py` の静的inventoryである。追加・削除・内容変更後は次を実行する。

```powershell
python -m tools.certification.build_manifest --root .
python -m tools.certification --root . --out _verify/<date>_certification
```

manifest未登録、source hash差分、必須skip、completion token欠落、traceback、crash、timeout、成果物契約差分は認定失敗になる。`support`と`historical`は理由とreview IDを必須とし、必須テストにはできない。

## Golden更新

Goldenは生成者が直接更新しない。候補をpending proposalとして固定し、画像をユーザーが確認した後、別のapproval IDでapproved registryを新規作成する。

```powershell
python -m tools.certification.golden_cli --root . propose `
  --requested-by <producer> --created-at <ISO-8601> `
  --out _verify/<date>/golden_proposal.json <artifact...>

python -m tools.certification.golden_cli --root . approve `
  --proposal _verify/<date>/golden_proposal.json `
  --approval-id <approval-record> --approved-at <ISO-8601> `
  --out docs/refactor/<phase>/golden_registry.json

python -m tools.certification.golden_cli --root . verify `
  --registry docs/refactor/<phase>/golden_registry.json
```

出力先は既存ファイルを上書きしない。差し替え時は新しいproposalとapprovalを作り、旧registryを履歴として残す。Golden画像は `_verify/<date>_<feature>/` に保持し、registryへbyte数とSHA-256を固定する。

## 失敗注入と観測

`bmanga_core` はBlender非依存packageである。現行adapterと将来のTransactionは同じ `FaultPoint`、`operation_span`、counterを使う。通常起動は注入0で、テストが明示的にarmした回数だけ失敗する。環境変数による注入も `BMANGA_ENABLE_FAULT_INJECTION=1` が無ければ無効である。

## Phase 1 合格記録

- 認定実行器の独立レビュー: Critical 0 / High 0
- Phase 1 最終判定: 合格
- manifest: 456 case（必須423、履歴21、補助12）
- Operator照合: 312 Operator、430入力、静的`bpy.ops` 128呼出し
- 対象再検証:
  - 基盤単体17/17
  - 期待Traceback wrapper 9/9
  - wrapper変更影響26/26
  - fault注入／用紙ガイド／preview 3/3
- Golden承認: `user-approval-2026-07-29`
- 最終全件認定: `_verify/2026-07-29_phase1/full_all_final_05`
  - 456/456結果、必須423/423
  - failure／skip／timeout／crash 0
  - `gate_pass=true`

全件認定はmanifestとsource hash、承認Goldenを検査した後に実行する。summaryの`gate_pass=true`、必須失敗・skip・timeout・crashがすべて0であることをPhase 2開始条件とする。
