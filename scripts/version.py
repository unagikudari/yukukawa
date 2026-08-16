"""kawa version <subject> — per-subject typed version facets (#140 V1, plan-version-read).

Usage:  .venv/bin/python scripts/version.py [subject] [--json]
        subject: node[:<id>] (default: local node, printed) | doc:<relative_path> |
                 policy:<name> | schema | package
Exit:   0 success · 2 malformed subject · 3 unknown subject id · 4 authority read failure
"""
import json
import sys

from kawa.version_read import SubjectError, exit_code_for, render_compact, version_read

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--json"]
    try:
        read = version_read(args[0] if args else None)
    except SubjectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(exc.exit_code)
    if "--json" in sys.argv:
        print(json.dumps(read, indent=2))
    else:
        print(render_compact(read))
    sys.exit(exit_code_for(read))
