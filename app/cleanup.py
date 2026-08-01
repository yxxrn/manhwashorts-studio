#!/usr/bin/env python
"""Lightweight cleanup CLI.

Usage examples:
    python -m manhwashorts.cleanup --dry-run
    python -m manhwashorts.cleanup
    python -m manhwashorts.cleanup --force
"""

from __future__ import annotations

import argparse
import sys

from app.services import cleanup


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ManhwaShorts lightweight data cleanup (Fase 0.1)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force aggressive cleanup even if not over limit",
    )
    parser.add_argument(
        "--usage",
        action="store_true",
        help="Only show current disk usage and exit",
    )
    args = parser.parse_args()

    if args.usage:
        usage = cleanup.get_data_usage()
        print(f"Total data: {usage['total_human']}")
        print(f"  tmp:    {cleanup._human_size(usage['tmp_bytes'])}")
        print(f"  output: {cleanup._human_size(usage['output_bytes'])}")
        print(f"  storage:{cleanup._human_size(usage['storage_bytes'])}")
        print(f"Limit:    {cleanup._human_size(usage['max_bytes'])}")
        print(f"Over limit: {usage['over_limit']}")
        return 0

    if args.dry_run:
        print("=== DRY RUN ===")
        usage = cleanup.get_data_usage()
        print(f"Current usage: {usage['total_human']}")
        print(f"Over limit: {usage['over_limit']}")

        # We can't easily simulate without deleting, so just report policy
        print(f"Would delete tmp older than {cleanup.settings.tmp_retention_days} days")
        print(f"Would delete unreferenced output older than {cleanup.settings.output_retention_days} days")
        if usage["over_limit"] or args.force:
            print("Would run in AGGRESSIVE mode")
        return 0

    print("Running cleanup...")
    result = cleanup.run_cleanup(force=args.force)

    print(f"Freed: {result['freed_human']}")
    print(f"Before: {result['before']['total_human']}")
    print(f"After : {result['after']['total_human']}")
    print(f"tmp freed   : {cleanup._human_size(result['tmp_freed'])}")
    print(f"output freed: {cleanup._human_size(result['output_freed'])}")
    print(f"Over limit now: {result['over_limit']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
