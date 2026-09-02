#!/usr/bin/env python3
"""Manage isolated YouTube Studio browser accounts for ManhwaShorts."""

from __future__ import annotations

import argparse
import json

from app.services.youtube_accounts import YouTubeBrowserAccountRegistry


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    add = sub.add_parser("add")
    add.add_argument("account_id")
    add.add_argument("label", nargs="?", default="")
    default = sub.add_parser("default")
    default.add_argument("account_id")
    rename = sub.add_parser("rename")
    rename.add_argument("account_id")
    rename.add_argument("label")
    profile = sub.add_parser("profile")
    profile.add_argument("account_id", nargs="?", default=None)
    args = parser.parse_args()

    registry = YouTubeBrowserAccountRegistry()
    try:
        if args.command == "list":
            print(json.dumps(registry.describe(), indent=2))
        elif args.command == "add":
            account = registry.create(account_id=args.account_id, label=args.label)
            print(account.profile_dir)
        elif args.command == "default":
            account = registry.update(args.account_id, make_default=True)
            print(account.account_id)
        elif args.command == "rename":
            account = registry.update(args.account_id, label=args.label)
            print(account.label)
        elif args.command == "profile":
            print(registry.get(args.account_id).profile_dir)
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
