"""Deterministic collector: public-site daily visits -> ONE Observation.

Usage:
  KAWA_DSN=dbname=kawa KAWA_NODE=<node> \
  GOATCOUNTER_SITE=https://example.goatcounter.com GOATCOUNTER_API_TOKEN=... \
  GOATCOUNTER_SINCE=YYYY-MM-DD \
      python scripts/collect_goatcounter.py [--day YYYY-MM-DD] [--allow-partial]

First external-API collector on the deterministic-observation-ingestion path
(docs/deterministic-observation-ingestion-v0.1.md): Collector (GoatCounter
stats API) -> this adapter -> emit `observation.recorded`. No LLM anywhere.

Measurement semantics (review round 1, verified against upstream source):
  - endpoint is `/api/v0/stats/total` with explicit RFC3339 UTC bounds for the
    whole day — a bare `start=YYYY-MM-DD&end=YYYY-MM-DD` parses to midnight..
    midnight and counts ONE hourly bucket, and `/stats/hits` totals only the
    returned page of paths; both silently undercount.
  - GoatCounter's stored counter (`hit_counts.total`, GC >= 2.5) increments on
    FIRST VISIT only — the value is VISITS, not pageviews, so the predicate is
    `site_visits_daily`. Misnaming the measurement would be durable false
    attribution in an append-only log.
  - subject resolution (adapter contract §12): Phase 0 has no site-subject
    machinery and minting a subject UUID without a subject-creating event
    would invent an identity outside the doctrine — so the site identity is
    carried in the source binding (`site=<host>` in source_revision) and
    dedup keys on the exact (predicate, occurred_at, source_revision) tuple,
    so a second site never swallows another site's day.
  - occurred_at marks the observed UTC day; fetched_at marks when the API was
    read. Recording the current, incomplete day requires `--allow-partial`
    and is marked `partial=true` in the source binding. At most one partial
    is recorded per day; a later finalized run still records (the later,
    better Observation supersedes by recency, never by rewriting history).
  - days before GOATCOUNTER_SINCE (instrument coverage start) are REFUSED:
    the API returns 0 for days the counter did not exist, and recording that
    would convert "no instrument" into a measured zero. GOATCOUNTER_SINCE is
    REQUIRED — without a coverage floor a fresh deploy would happily record
    fake zeros, so absence is a configuration error, not a default.

Loud path: missing/invalid config, API non-200, malformed body, or DB
failure => nonzero exit (failed oneshot unit)."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request

from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.identity import IdentityContext
from kawa.storage.db import connect

PREDICATE = "site_visits_daily"


def _revision(host: str, day: str, partial: bool) -> str:
    """Fixed-form source binding — dedup relies on EXACT equality of this string."""
    return f"site={host} day={day} metric=first_visits partial={'true' if partial else 'false'}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The bearer token must never follow a redirect to another host."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise RuntimeError(f"refusing redirect ({code}) to {newurl}")


def _utc_today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_day_total(site: str, token: str, day: str) -> tuple[int, bytes]:
    """Return (total_visits, raw_body) for one whole UTC day.

    `total_utc` is preferred over `total` (round-2 F-03): with a non-UTC site
    display timezone GoatCounter's `total` may sum local-day buckets, while
    `total_utc` is the UTC-day-aligned count our occurred_at semantics claim.
    Both are equal when the site timezone is UTC (verified on the live site)."""
    url = (f"{site}/api/v0/stats/total"
           f"?start={day}T00:00:00Z&end={day}T23:59:59Z")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GoatCounter API {resp.status} for {url}")
        raw = resp.read()
    body = json.loads(raw)
    return int(body.get("total_utc", body["total"])), raw


def observation_exists(conn, occurred_at: str, revisions: tuple[str, ...]) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM event_observation WHERE predicate = %s "
                    "AND occurred_at = %s AND source_revision = ANY(%s) LIMIT 1",
                    (PREDICATE, occurred_at, list(revisions)))
        return cur.fetchone() is not None


def run(conn, *, site: str, token: str, day: str, partial_ok: bool,
        since: str, node_ref: str, credential_path: str, keys_path: str) -> dict:
    today = _utc_today()
    target = datetime.date.fromisoformat(day)
    day = target.isoformat()                     # canonical form only, everywhere below
    floor = datetime.date.fromisoformat(since)
    if floor > today:
        raise ValueError(f"GOATCOUNTER_SINCE {since} is in the future — configuration error")
    if target > today:
        raise ValueError(f"{day} is in the future")
    if target < floor:
        return {"day": day, "skipped": "before instrument coverage (GOATCOUNTER_SINCE)"}
    partial = target == today
    if partial and not partial_ok:
        return {"day": day, "skipped": "UTC day not complete (use --allow-partial to record anyway)"}

    host = urllib.parse.urlsplit(site).hostname or site
    occurred_at = f"{day}T00:00:00Z"
    # partial runs are bounded to one per day; a finalized run only defers to
    # an existing finalized Observation (it may follow a partial one).
    blocking = ((_revision(host, day, False), _revision(host, day, True))
                if partial else (_revision(host, day, False),))
    if observation_exists(conn, occurred_at, blocking):
        return {"day": day, "skipped": "Observation already recorded"}

    total, raw = fetch_day_total(site, token, day)
    cred = load_or_create_local_node(credential_path, node_ref=node_ref)
    keys = PublicKeyRegistry(keys_path)
    keys.register(cred.signing_key_ref, cred.public_pem())
    kawa = Kawa(conn, identity=IdentityContext.from_local_node(cred, actor_ref="goatcounter-collector"))
    ev = kawa.record_observation(
        PREDICATE, value_number=float(total), method="api_fetch",
        occurred_at=occurred_at,
        source_ref=f"{site}/api/v0/stats/total?start={day}T00:00:00Z&end={day}T23:59:59Z",
        source_revision=_revision(host, day, partial),
        content_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        fetched_at=_now())
    conn.commit()
    return {"day": day, "total": total, "partial": partial, "site": host,
            "event_id": ev.event_id}


def main() -> int:
    ap = argparse.ArgumentParser()
    yesterday = (_utc_today() - datetime.timedelta(days=1)).isoformat()
    ap.add_argument("--day", default=yesterday, help="UTC day to observe (default: yesterday)")
    ap.add_argument("--allow-partial", action="store_true",
                    help="permit recording the current, incomplete UTC day")
    ap.add_argument("--credential", default=os.path.expanduser("~/.kawa/node_credential.json"))
    ap.add_argument("--keys", default=os.path.expanduser("~/.kawa/keys.json"))
    args = ap.parse_args()

    site = os.environ.get("GOATCOUNTER_SITE", "").rstrip("/")
    token = os.environ.get("GOATCOUNTER_API_TOKEN", "")
    since = os.environ.get("GOATCOUNTER_SINCE", "")
    if not site or not token or not since:
        print("GOATCOUNTER_SITE / GOATCOUNTER_API_TOKEN / GOATCOUNTER_SINCE are required "
              "(SINCE is the instrument coverage floor — no default, fail closed)",
              file=sys.stderr)
        return 2
    if not site.startswith("https://"):
        print(f"GOATCOUNTER_SITE must be https:// (got {site!r})", file=sys.stderr)
        return 2
    node = os.environ.get("KAWA_NODE") or os.uname().nodename.split(".")[0]
    try:
        with connect() as conn:
            status = run(conn, site=site, token=token, day=args.day,
                         partial_ok=args.allow_partial, since=since,
                         node_ref=node, credential_path=args.credential, keys_path=args.keys)
    except Exception as exc:
        print(f"goatcounter collect FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
