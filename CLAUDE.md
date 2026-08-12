# kawa — Claude Code Bootstrap

memorybroker (`unagikudari/memory-broker`) から派生した独立サービス。event-sourced な continuity / retrieval / coordination / authority substrate。

## まずここ — 現在地は kawa 自身に訊く

**このリポジトリは自分の実装状況を kawa の projection で持っている。** 何をやるべきかは README でも issue でもなく、live state から取る:

```bash
cd ~/kawa && KAWA_DSN=dbname=kawa .venv/bin/python scripts/brief.py
```

⚠️ **必ず `.venv/bin/python`** — 素の `python` は無い、`python3` は `ModuleNotFoundError: No module named 'kawa'`。venv 必須。これが seamless-resume を一度壊した罠 (2026-08-13)。

brief は open plan・roadmap 位置・**次の actionable Work** を返す。それが再開点。`plan-roadmap` が v0.5 §23 の 13-step 実装ロードマップ (`docs/specification-v0.5.md` が現行 consolidated spec)。

## 開発の型 (この repo の規律 — 逸脱しない)

各 step は **plan-first + 二段の独立敵対レビュー**を通してから実装する:

1. planning issue を書く (`gh issue create --body-file` で file 化 — heredoc の code-fence backtick が `$()` を誤爆させるので Write ツール推奨)。REAL / DEFERRED の scope 境界を **保証で** 切る (component 名でなく)。
2. plan review round 1 → 修正要求を rev 2 に全織り込み → round 2。レビューは broker の dispatch_task で別 executor へ (evo-gemini = 最速・gh 可、yurei-codex = plan レビューの質最高・gh 無しなので note を転載)。
3. approve 時: issue body+comments の sha256 を snapshot Observation (`content_digest`) にして plan event へ `based_on` link — 承認した本文を kawa の hash chain に束縛 (#98 §2 の暫定形)。
4. 実装 → 実装レビュー (evo-gemini)。**自分が攻撃点として挙げた穴は verdict が green でも塞ぐ** (step 5 の freshness/resolvability が実例)。
5. merge → kawa に Result 記録 (`bridge.complete_review` / `record_result`) で次 step が unblock。

新 doc は `specification-v0.5.md` §26 と `supersession-matrix-v0.1.md` に index。それで C4 drift が 1 増えるので `registry/drift-baseline.json` に 1 行足して 31 維持 (README は topic 一覧で contract を列挙しない規約)。

## テスト / migration

- テストは **env 無しで安全**: fixture は `KAWA_TEST_DSN_A/B` (既定 `kawa_test_a/b`) しか見ず、`KAWA_DSN` (=本番 dogfood) を無視する (#91/#92 の事故対応)。`.venv/bin/python -m pytest -q`。
- migration は ledger 方式で各ファイル 1 回だけ適用 (`scripts/apply_migrations.py`、`schema_migrations` 表)。clean clone は `createdb kawa kawa_test_a kawa_test_b` → 各に適用。
- drift lint: `.venv/bin/python scripts/lint_vocabulary_drift.py` (31 = main baseline)。

## 現在の到達点 (2026-08-13)

v0.5 §23 の step 0–7 が merge 済み: event identity 正典化 / trunk 語彙 / **epistemic nucleus** (Observation・Claim・link・derived standing、Fact 廃止) / **SQL-first retrieval** / **Work DAG + work.retired** / **security plane** (Process Incarnation・Credential Broker・Work Attestation) / **participant introduction** / **wake-pull + ghost-participant の構造解**。次は step 8 (real Node identity + Node Incarnation + 2-node replication、effect identity=exactly-once もこの圏内)。

> Actors pass through Kawa. Events remain. Understanding changes.
