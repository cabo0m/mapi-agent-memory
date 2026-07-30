from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.runtime.writer_guard import (  # noqa: E402
    configure_writer_guard,
    mutation_writer_guard,
    release_writer_guard,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--mode", choices=("try", "hold", "read_only"), required=True)
    parser.add_argument("--instance-key", default="probe")
    parser.add_argument("--hold-seconds", type=float, default=30.0)
    args = parser.parse_args()

    os.environ["MAPI_WRITER_GUARD_ENABLED"] = "1"
    os.environ["MAPI_WRITER_MODE"] = "read_only" if args.mode == "read_only" else "active"
    os.environ["MAPI_WRITER_INSTANCE_KEY"] = args.instance_key
    os.environ["MAPI_WRITER_HEARTBEAT_SECONDS"] = "0.25"
    os.environ["MAPI_WRITER_STALE_SECONDS"] = "2"

    try:
        status = configure_writer_guard(db_path=args.db_path)
    except RuntimeError as exc:
        print(
            json.dumps(
                {
                    "status": "denied",
                    "error": str(exc),
                    "instance_key": args.instance_key,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 2

    guard = mutation_writer_guard(required=True)
    print(
        json.dumps(
            {
                "status": "ready",
                "mode": status["mode"],
                "lease_held": status["lease_held"],
                "lock_path": status["lock_path"],
                "mutations_allowed": guard["allowed"],
                "reason_codes": guard.get("reason_codes") or [],
                "instance_key": args.instance_key,
            },
            sort_keys=True,
        ),
        flush=True,
    )

    if args.mode == "hold":
        time.sleep(max(0.1, args.hold_seconds))
    release_writer_guard()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
