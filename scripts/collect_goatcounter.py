"""Deterministic collector: public-site daily visits -> ONE Observation.

Usage:
  KAWA_DSN=dbname=kawa KAWA_NODE=<node> \
  GOATCOUNTER_SITE=https://example.goatcounter.com GOATCOUNTER_API_TOKEN=... \
  GOATCOUNTER_SINCE=YYYY-MM-DD \
      python scripts/collect_goatcounter.py [--day YYYY-MM-DD] [--allow-partial]
                                            [--max-fetches N]

With no `--day` the run RECONCILES: it derives every day from the coverage
floor to yesterday that has no Observation yet, and collects the oldest
`--max-fetches` of them. There is no lookback window — see `reconcile()`.

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

Why a reconciliation and not just yesterday (2026-08-20): the collector ran once a day
against exactly one day, so a single transient API failure dropped that day
FOREVER — the next run had already moved on to the next day. That is what lost
2026-08-19 (two `HTTP 404` failures on days whose URLs answered 200 hours
later). The schema already separates `occurred_at` (the day measured) from
`fetched_at` (when the API was read), so a day collected late is recorded
truthfully rather than approximately; and `observation_exists` already makes a
re-run of a recorded day a no-op that never touches the API. Reconciling against the log
therefore needs no new state — it is the one-day loop made convergent, and the
failure it repairs is repaired without a retry heuristic.

Loud path: missing/invalid config, API non-200, malformed body, or DB
failure => nonzero exit (failed oneshot unit). Under reconciliation ONE day's
failure no longer suppresses the others: every day is attempted, the failures
are named in the output, and the exit is still nonzero."""
from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request

from kawa import nodehealth
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


def status_file() -> str:
    return os.path.join(nodehealth.status_dir(), "goatcounter.status")


STATUS_FILE = None      # sentinel: resolved per call (see nodehealth.status_dir)


def write_status(status: dict, *, node: str, ok: bool, path: str | None = None) -> None:
    """Leave a machine-readable trace of EVERY run, succeeded or failed.

    The collector previously left none. Its two 404 failures were therefore
    visible only in journald, where nothing looks, and the missing day was
    found by hand three days later. A status file is only half a delivery
    route — the reader is `scripts/brief.py`, which every session-start hook
    already prints — but writing it on the failure path too is the half that
    was actually missing: a status that is only written when things went well
    cannot report that things went badly."""
    path = path or STATUS_FILE or status_file()   # never frozen at import
    if parent := os.path.dirname(path):
        os.makedirs(parent, exist_ok=True)        # dirname("x.status") is "", which raises
    payload = {"ts": _now(), "node": node, "ok": ok, **status}
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)      # a reader never sees a half-written status
    except Exception:
        # a status that cannot be serialised must not leave a half-written
        # sibling behind either: the PREVIOUS status stays authoritative
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def recorded_days(conn, host: str, days: list[str]) -> set[str]:
    """Which of `days` already carry a FINALIZED Observation for this site.

    One query for the whole range, then the revision comparison happens in
    Python against `_revision()` — the dedup key stays defined in exactly one
    place. Assembling the same string in SQL would be a second spelling of one
    concept, which is the defect class this repo keeps finding."""
    if not days:
        return set()
    with conn.cursor() as cur:
        cur.execute("SELECT occurred_at, source_revision FROM event_observation "
                    "WHERE predicate = %s AND occurred_at >= %s AND occurred_at <= %s",
                    (PREDICATE, f"{days[0]}T00:00:00Z", f"{days[-1]}T00:00:00Z"))
        rows = cur.fetchall()
    want = {_revision(host, d, False): d for d in days}
    return {want[rev] for _, rev in rows if rev in want}


def reconcile(conn, *, since: str, partial_ok: bool, max_fetches: int,
              site: str, **kw) -> dict:
    """Record every day in [coverage floor, yesterday] that has no Observation.

    There is deliberately NO window. Round-1 review of #228 confirmed what a
    trailing window costs: a day that is not repaired before it ages past the
    window falls off the back and is never revisited, and — worse — the status
    file goes back to `ok:true` the moment the gap leaves the window, so the
    collector actively reports health over a permanent hole. Bounding the
    LOOKBACK was the wrong bound.

    What actually needs bounding is API calls, so that is what is bounded:
    the missing days are derived from the log, the oldest `max_fetches` are
    collected this run, and the rest are named in `deferred` rather than
    silently dropped. A backlog therefore drains over successive runs and the
    series still converges, because the floor is the coverage start and never
    moves forward on its own.

    A run that ends with the series still incomplete reports `ok:false`. For a
    collector whose product IS a complete daily series, a hole is not a
    healthy state, and self-clearing on the next good run keeps that honest."""
    today = _utc_today()
    floor = datetime.date.fromisoformat(since)
    if floor > today:
        raise ValueError(f"GOATCOUNTER_SINCE {since} is in the future — configuration error")
    if max_fetches < 1:
        raise ValueError(f"--max-fetches must be >= 1 (got {max_fetches})")

    last = today - datetime.timedelta(days=1)
    days, cur = [], floor
    while cur <= last:
        days.append(cur.isoformat())
        cur += datetime.timedelta(days=1)

    host = urllib.parse.urlsplit(site).hostname or site
    missing = [d for d in days if d not in recorded_days(conn, host, days)]
    targets = missing[:max_fetches]          # oldest first: the tail is the fragile end
    deferred = missing[len(targets):]

    results, failed = [], []
    for day in targets + ([today.isoformat()] if partial_ok else []):
        try:
            results.append(run(conn, day=day, since=since, partial_ok=partial_ok,
                               site=site, **kw))
        except Exception as exc:
            conn.rollback()
            failed.append({"day": day, "error": f"{type(exc).__name__}: {exc}"})

    done = {r["day"] for r in results if "event_id" in r}
    still_missing = [d for d in missing if d not in done]
    return {"range": [floor.isoformat(), last.isoformat()],
            "recorded": [r for r in results if "event_id" in r],
            "skipped": [r for r in results if "skipped" in r],
            "failed": failed,
            "deferred": deferred,
            "missing": still_missing}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None,
                    help="observe exactly this UTC day (default: reconcile every "
                         "missing day since the coverage floor)")
    ap.add_argument("--max-fetches", type=int, default=10,
                    help="API calls per run when --day is absent (default: 10); "
                         "the remaining missing days are named and drain next run")
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
        common = dict(site=site, token=token, partial_ok=args.allow_partial,
                      since=since, node_ref=node,
                      credential_path=args.credential, keys_path=args.keys)
        with connect() as conn:
            if args.day is not None:
                status = run(conn, day=args.day, **common)
            else:
                status = reconcile(conn, max_fetches=args.max_fetches, **common)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        print(f"goatcounter collect FAILED: {err}", file=sys.stderr)
        _try_write_status({"error": err}, node=node, ok=False)
        return 2
    # STUCK days fail; DRAINING days defer. Round 2 of #228 caught the earlier
    # rule (`ok` iff the series is complete) crying wolf: a first run against a
    # floor 300 days back would fire OnFailure for ~30 consecutive runs of a
    # backlog draining exactly as designed, indistinguishable from a real
    # fault. `missing` is exactly `failed` ∪ `deferred`, and a day that cannot
    # be collected lands in `failed` on EVERY run — so keying the loud path on
    # failures alone loses no stuck day and silences the expected backfill.
    # The backlog stays visible in the payload without being an alarm.
    ok = not status.get("failed")
    _try_write_status(status, node=node, ok=ok)
    print(json.dumps(status, indent=2))
    if not ok:
        for f in status.get("failed", []):
            print(f"goatcounter collect FAILED for {f['day']}: {f['error']}", file=sys.stderr)
        if status.get("missing"):
            print(f"goatcounter series incomplete: {len(status['missing'])} day(s) "
                  f"missing ({', '.join(status['missing'][:5])}"
                  f"{'…' if len(status['missing']) > 5 else ''})", file=sys.stderr)
        return 2
    return 0


def _try_write_status(status: dict, *, node: str, ok: bool) -> None:
    """Never let the reporting channel become the thing that fails the run."""
    try:
        write_status(status, node=node, ok=ok)
    except Exception as exc:                      # pragma: no cover - defensive
        print(f"goatcounter status file unwritable: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
