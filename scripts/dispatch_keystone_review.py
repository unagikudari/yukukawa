"""Dispatch the keystone (Operation & Effect Identity) review THROUGH the Kawa bridge.

Kawa records the review request (Work + context); the broker is only the transport. When the
runtime returns, complete_review() records the full body as a durable Kawa Result — so this
review, unlike vendor-A's 3 bare payloads, is preserved in Kawa.
"""
from __future__ import annotations

import os

from kawa.adapters import broker_bridge as bridge
from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.storage.db import connect

CONTEXT = """[kawa Operation & Effect Identity v0.1 (keystone) 独立敵対的レビュー — 第2レーン、refute-by-default]
vendor-A 第1レーン は NOT_CONFORMING / surviving_counterexamples=true と判定したが本文を返さなかった。
あなたは具体的な反例を出す任務。取得: git checkout spec/operation-identity → docs/operation-effect-identity-v0.1.md。
関連(FROZEN): consistency-and-authority-v0.1.md (③④, effect identity E, F7/F8/gate-22), subject-identity-and-lineage-v0.1.md。PR #60, refs #59。
攻撃点: (a) effect_identity E = digest(semantic_operation, subject_ref, scope, effect_params) の衝突/分裂
(同一効果が別 E / 別効果が同一 E)。(b) この層の E と ③④ Actuator.CommitToken の E の統一が破れる経路。
(c) work_identity = digest(plan_ref, semantic_operation, subject_ref, scope) の Plan revision preserve/supersede 曖昧性、work_ref が hidden SoT でない主張の検証。
(d) idempotency_class 4分類の取りこぼし、execution_unknown→verification Work が unsafe retry を防ぐか。
(e) duplicate 分類 (E, scope, outcome, authority) が #59 §1.7 の2反例を正しく分類するか。
(f) registry の invariant_class が ③④ gate-22 と二重定義/乖離する穴。(g) #1/#2/#6/#7 を導出で閉じているか、隠れた第5の独立選択。
各攻撃点で具体シナリオ + surviving counterexample の有無(あれば最小トレース + 修正案)。必ず本文全文を返す。"""


def main() -> int:
    conn = connect()
    k = Kawa(conn, identity=IdentityContext.from_credential_file(
        actor_ref=os.environ.get("KAWA_ACTOR_REF", "vendor-local")))
    k.create_plan("plan-keystone", "kawa", "review & freeze the Operation & Effect Identity keystone (#60)")
    k.set_plan_lifecycle("plan-keystone", "running")
    k.derive_work("w-review-keystone", "plan-keystone", "review", role_requirement="Reviewer")

    bridge.request_review(conn, work_ref="w-review-keystone", context=CONTEXT,
                          target_agent="vendor-B")
    print("dispatch recorded in Kawa. pending:")
    for work_ref, ctx, agent in bridge.pending_dispatches(conn):
        print(f"  {work_ref} -> {agent}  (context {len(ctx)} chars stored in Kawa)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
