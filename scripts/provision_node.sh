#!/usr/bin/env bash
# Provision THIS machine as a Kawa node up to the readiness gate.
#
# Codifies README "Joining an existing Basin (replica / participant node)"
# steps 1-3 (repo+venv, local kawa DB, node identity bootstrap) and the readiness
# check. It STOPS at the security plane: trust enrollment and replication wiring
# (steps 4-5) are operator acts and are only PRINTED, never performed unattended.
#
# A Basin is a set of Kawa Nodes sharing a continuity/discovery domain (spec §12).
# Idempotent: safe to re-run. Joining is host-bound — run it ON the node that is
# joining the Basin. There is no remote "make node X a participant" switch.
#
# Lesson baked in (2026-08-15): a node is a participant ONLY if the readiness
# check below succeeds on the node itself. PostgreSQL `sub_<node>_from_<peer>`
# subscriptions are the broker mesh, NOT Kawa; never infer node status from them.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
KAWA_DSN="${KAWA_DSN:-dbname=kawa}"
PY=".venv/bin/python"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# 1. venv + editable install ------------------------------------------------
say "1/4  Python venv + dependencies"
if [ ! -x "$PY" ]; then
  python3 -m venv .venv
fi
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -e '.[dev]'
echo "    venv ready: $($PY --version)"

# 2. local kawa database + migrations --------------------------------------
say "2/4  local kawa database + migrations"
db_name="$(printf '%s' "$KAWA_DSN" | sed -n 's/.*dbname=\([^ ]*\).*/\1/p')"
db_name="${db_name:-kawa}"
if ! psql "$KAWA_DSN" -c '\q' 2>/dev/null; then
  # createdb honours the same libpq params encoded in KAWA_DSN's host/port/user
  createdb "$db_name" 2>/dev/null || {
    echo "    !! could not create database '$db_name'." >&2
    echo "       First-time PostgreSQL: sudo -u postgres createuser --createdb \"\$USER\"" >&2
    exit 1
  }
  echo "    created database: $db_name"
else
  echo "    database reachable: $db_name"
fi
KAWA_DSN="$KAWA_DSN" "$PY" scripts/apply_migrations.py
echo "    migrations applied (sql/0001..NNNN)"

# 3. node identity (auto-created; the real key is git-ignored operator data) -
say "3/4  node identity"
KAWA_DSN="$KAWA_DSN" "$PY" - <<'PYEOF'
import os
from kawa.domain.credential import load_or_create_local_node
path = os.path.expanduser(os.environ.get("KAWA_NODE_IDENTITY", "~/.kawa/node_credential.json"))
os.makedirs(os.path.dirname(path), exist_ok=True)
cred = load_or_create_local_node(path, node_ref=os.environ.get("KAWA_NODE") or os.uname().nodename.split(".")[0])
print(f"    node credential: {path}  (ref={getattr(cred, 'node_ref', '?')})")
PYEOF

# 4. readiness gate — the definitive proof this node is provisioned ---------
say "4/4  readiness check"
if KAWA_DSN="$KAWA_DSN" "$PY" scripts/brief.py >/dev/null 2>&1; then
  echo "    ✓ brief.py succeeds — repo + venv + DB present. Node is READY."
else
  echo "    ✗ brief.py failed — provisioning incomplete. Fix above before proceeding." >&2
  exit 1
fi

# Operator steps this installer will NOT perform (security plane) -----------
cat <<'EOF'

== Remaining OPERATOR steps (cross the security plane — not automated) ==

  4. Replication from canonical. Run the pull loop / supervisor with BOTH DSNs
     NAMED and node-trust enrolled (canonical trusts this node's key; this node
     trusts the source). Trust enrollment is an operator act, never automatic:

       KAWA_DSN='dbname=kawa' \
       KAWA_SOURCE_DSN='host=<canonical-tailscale-ipv6> dbname=kawa user=kawa_replica password=...' \
       KAWA_NODE=<this-node> .venv/bin/python scripts/replica_pull.py \
         --keys ~/.kawa/keys.json --trust ~/.kawa/trust.json --credential ~/.kawa/node_credential.json

     Then enable ops/systemd/kawa-replica-pull.timer (or kawa-supervisor.service)
     with KAWA_NODE set to this node.

  5. Fleet brief opt-in (this fleet's broker integration, optional). On THIS
     node's own broker (127.0.0.1:8000), source=manual — the ownership trigger
     rejects a cross-host write:
       node_fact  host_name=<this-node>  kawa_participant=true
       node_fact  host_name=<this-node>  kawa_brief_display=true

  Re-run the readiness check any time:
       KAWA_DSN=dbname=kawa .venv/bin/python scripts/brief.py
EOF
