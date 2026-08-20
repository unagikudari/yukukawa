"""Deterministic self-telemetry collector: this node's cpu/mem/disk/gpu as
Observations (plan-fleet-telemetry #189 rev 3, step 2).

Usage:
  KAWA_DSN=dbname=kawa KAWA_NODE=<node> python scripts/collect_node_telemetry.py

Families and predicates come from kawa.projections.facet_registry.REGISTRY
(the frozen §A mirror). Failure semantics are §B, exactly:

  * tool ABSENT (no nvidia-smi/rocm-smi)  -> family not emitted (absence)
  * tool FAILS with no parseable stdout   -> family skipped, exit non-zero
  * multi-target partial failure (df)     -> per-qualifier skip, named in
    status; readable targets still emit; family failure ONLY when nothing
    parses
  * no value is ever defaulted — a failed read emits nothing

occurred_at == fetched_at by declaration (§C: a local read has no transport
gap). The qualifier travels in the source binding as the fixed-form TRAILING
field `qualifier=<value>` of source_revision — trailing so qualifiers that
contain spaces (mount paths can) survive intact; the reducer captures to
end-of-string. (The Observation payload has no qualifier field; key=value
source_revision is the same convention as the archive/goatcounter
collectors.)

Fleet reality (measured 2026-08-17): the fleet's AMD nodes have NO rocm-smi
(command not found; their VRAM is amdgpu sysfs — /sys/class/drm/card*/device/
mem_info_vram_*). The rocm-smi fallback here is generic-Linux support; the
amdgpu-sysfs reader lands WITH multi-node telemetry (#189 §F deferral, where
those nodes start writing at all). Until then a GPU-less-tool node honestly
reports gpu: absence.

Log growth (review (e)): ~11 predicates x 10-min cadence = ~1.6k events/day
on this node. Retention/pruning of high-frequency telemetry is owned by the
durability-policy / scoped-replication workstream (segments + archive) — this
collector deliberately introduces no second store and no local pruning; if
cadence must drop before that lands, the timer interval is the operator knob.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
import sys

from kawa import nodehealth
from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.identity import IdentityContext
from kawa.projections.facet_registry import REGISTRY
from kawa.storage.db import connect

_DF_CMD = ["df", "-B1", "--local", "--output=target,size,avail",
           "-x", "tmpfs", "-x", "devtmpfs", "-x", "efivarfs", "-x", "overlay",
           "-x", "squashfs"]
_NVIDIA_CMD = ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used",
               "--format=csv,noheader,nounits"]
_ROCM_CMD = ["rocm-smi", "--showmeminfo", "vram", "--json"]


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Reading(object):
    """One (predicate, qualifier, value) with its raw evidence."""
    def __init__(self, predicate: str, qualifier: str, value, source_ref: str, raw: bytes):
        assert predicate in REGISTRY, predicate
        self.predicate, self.qualifier, self.value = predicate, qualifier, value
        self.source_ref, self.raw = source_ref, raw


def read_cpu() -> list[Reading]:
    raw = open("/proc/loadavg", "rb").read()
    load1 = float(raw.split()[0])
    cores = os.cpu_count()
    out = [Reading("node_cpu_load1", "", load1, "file:///proc/loadavg", raw)]
    if cores is not None:                       # absence over invention
        out.append(Reading("node_cpu_cores", "", float(cores), "file:///proc/cpuinfo",
                           str(cores).encode()))
    return out


def read_mem() -> list[Reading]:
    raw = open("/proc/meminfo", "rb").read()
    kv = {}
    for line in raw.decode().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].rstrip(":") in ("MemTotal", "MemAvailable"):
            kv[parts[0].rstrip(":")] = float(parts[1]) * 1024.0
    out = []
    if "MemTotal" in kv:
        out.append(Reading("node_mem_total_bytes", "", kv["MemTotal"], "file:///proc/meminfo", raw))
    if "MemAvailable" in kv:
        out.append(Reading("node_mem_available_bytes", "", kv["MemAvailable"],
                           "file:///proc/meminfo", raw))
    return out


def parse_df(stdout: bytes) -> tuple[list[Reading], list[str]]:
    """Per-qualifier semantics (§B): each parseable line emits; unparseable
    lines are skipped and named. Returns (readings, skipped_lines)."""
    readings: list[Reading] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for line in stdout.decode(errors="replace").splitlines()[1:]:
        # rsplit from the numeric tail: mount paths may contain spaces
        # (review finding (a)) — the LAST two fields are size/avail
        parts = line.rsplit(None, 2)
        try:
            mount, size, avail = parts[0].strip(), float(parts[1]), float(parts[2])
            if not mount.startswith("/") or mount in seen:
                raise ValueError(mount)
            seen.add(mount)
        except (ValueError, IndexError):
            skipped.append(line.strip()[:80])
            continue
        readings.append(Reading("node_disk_total_bytes", mount, size, "cmd://df", stdout))
        readings.append(Reading("node_disk_free_bytes", mount, avail, "cmd://df", stdout))
    return readings, skipped


def read_disk(status: dict) -> list[Reading]:
    try:
        proc = subprocess.run(_DF_CMD, capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"df unavailable: {exc}")
    readings, skipped = parse_df(proc.stdout)
    if skipped:
        status.setdefault("skipped_mounts", []).extend(skipped)
    if not readings:                             # nothing parsed at all = family failure
        raise RuntimeError(f"df produced no parseable output (exit {proc.returncode}, "
                           f"stderr {proc.stderr.decode(errors='replace')[:120]!r})")
    # exit!=0 WITH parseable rows = partial failure: per-qualifier skip already
    # applied above; record the loud detail without failing the family (§B)
    if proc.returncode != 0:
        status.setdefault("skipped_mounts", []).append(
            f"df exit {proc.returncode}: {proc.stderr.decode(errors='replace')[:120]}")
    return readings


def read_gpu() -> list[Reading] | None:
    """None = no tool present (absence). A present-but-failing tool does NOT
    stop the chain (review finding (b): nvidia-smi with an unloaded driver
    must still let rocm-smi try); the family fails loudly only when every
    PRESENT tool failed."""
    errors: list[str] = []
    for cmd, parse in ((_NVIDIA_CMD, _parse_nvidia), (_ROCM_CMD, _parse_rocm)):
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=20)
        except FileNotFoundError:
            continue
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{cmd[0]}: {exc}")
            continue
        if proc.returncode != 0:
            errors.append(f"{cmd[0]} exit {proc.returncode}: "
                          f"{proc.stderr.decode(errors='replace')[:120]!r}")
            continue
        readings = parse(proc.stdout)
        if readings is None:
            errors.append(f"{cmd[0]} output unparseable")
            continue
        return readings
    if errors:
        raise RuntimeError("; ".join(errors))
    return None


def _parse_nvidia(stdout: bytes) -> list[Reading] | None:
    readings: list[Reading] = []
    for line in stdout.decode(errors="replace").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            return None
        idx, name, total_mib, used_mib = parts
        readings.append(Reading("node_gpu_model", idx, name, "cmd://nvidia-smi", stdout))
        readings.append(Reading("node_gpu_vram_total_bytes", idx,
                                float(total_mib) * 1024 * 1024, "cmd://nvidia-smi", stdout))
        readings.append(Reading("node_gpu_vram_used_bytes", idx,
                                float(used_mib) * 1024 * 1024, "cmd://nvidia-smi", stdout))
    return readings or None


def _parse_rocm(stdout: bytes) -> list[Reading] | None:
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    readings: list[Reading] = []
    for card, info in sorted(data.items()):
        if not isinstance(info, dict):
            continue
        total = info.get("VRAM Total Memory (B)")
        used = info.get("VRAM Total Used Memory (B)")
        idx = card.replace("card", "")
        if total is not None:
            readings.append(Reading("node_gpu_vram_total_bytes", idx, float(total),
                                    "cmd://rocm-smi", stdout))
        if used is not None:
            readings.append(Reading("node_gpu_vram_used_bytes", idx, float(used),
                                    "cmd://rocm-smi", stdout))
    return readings or None


def collect(status: dict) -> tuple[list[Reading], bool]:
    """Run every family per §B. Returns (readings, all_families_ok)."""
    readings: list[Reading] = []
    ok = True
    for family, fn in (("cpu", read_cpu), ("mem", read_mem),
                       ("disk", lambda: read_disk(status))):
        try:
            readings.extend(fn())
        except Exception as exc:                 # family failure: skip whole family, loud
            status.setdefault("failed_families", {})[family] = str(exc)[:200]
            ok = False
    try:
        gpu = read_gpu()
        if gpu is None:
            status["gpu"] = "no tool present (absence, not zero)"
        else:
            readings.extend(gpu)
    except Exception as exc:
        status.setdefault("failed_families", {})["gpu"] = str(exc)[:200]
        ok = False
    return readings, ok


def main() -> int:
    node = os.environ.get("KAWA_NODE") or os.uname().nodename.split(".")[0]
    status: dict = {"ts": _now(), "node": node}
    readings, ok = collect(status)
    cred = load_or_create_local_node(os.path.expanduser("~/.kawa/node_credential.json"),
                                     node_ref=node)
    keys = PublicKeyRegistry(os.path.expanduser("~/.kawa/keys.json"))
    keys.register(cred.signing_key_ref, cred.public_pem())
    now = _now()
    with connect() as conn:
        k = Kawa(conn, identity=IdentityContext.from_local_node(
            cred, actor_ref="telemetry-collector"))
        for r in readings:
            spec = REGISTRY[r.predicate]
            kwargs = {spec.value_column: r.value}
            k.record_observation(
                r.predicate, method=spec.method, occurred_at=now,   # == fetched_at (§C)
                source_ref=r.source_ref,
                source_revision=f"tool={r.source_ref} qualifier={r.qualifier}",
                content_digest="sha256:" + hashlib.sha256(r.raw).hexdigest(),
                fetched_at=now, **kwargs)
        conn.commit()
    status["emitted"] = len(readings)
    status_file = os.path.join(nodehealth.status_dir(), "telemetry.status")
    os.makedirs(os.path.dirname(status_file), exist_ok=True)
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)
    print(json.dumps(status, indent=2))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
