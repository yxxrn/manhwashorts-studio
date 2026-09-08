#!/usr/bin/env python3
"""Manage isolated YouTube Studio browser accounts for ManhwaShorts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.youtube_accounts import YouTubeBrowserAccountRegistry
from app.services.youtube_browser import BrowserPublishError, YouTubeStudioBrowserPublisher


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    ensure = sub.add_parser("ensure")
    ensure.add_argument("account_id")
    ensure.add_argument("label", nargs="?", default="")
    add = sub.add_parser("add")
    add.add_argument("account_id")
    add.add_argument("label", nargs="?", default="")
    default = sub.add_parser("default")
    default.add_argument("account_id")
    rename = sub.add_parser("rename")
    rename.add_argument("account_id")
    rename.add_argument("label")
    cookie_import = sub.add_parser("import-cookies")
    cookie_import.add_argument("account_id")
    cookie_import.add_argument("cookies_file", type=Path)
    profile = sub.add_parser("profile")
    profile.add_argument("account_id", nargs="?", default=None)
    args = parser.parse_args()

    registry = YouTubeBrowserAccountRegistry()
    try:
        if args.command == "list":
            print(json.dumps(registry.describe(), indent=2))
        elif args.command == "ensure":
            wanted = registry.normalize_account_id(args.account_id)
            existing = {row.account_id: row for row in registry.list_accounts()}
            account = existing.get(wanted)
            if account is None:
                account = registry.create(account_id=wanted, label=args.label)
            print(account.profile_dir)
        elif args.command == "add":
            account = registry.create(account_id=args.account_id, label=args.label)
            print(account.profile_dir)
        elif args.command == "default":
            account = registry.update(args.account_id, make_default=True)
            print(account.account_id)
        elif args.command == "rename":
            account = registry.update(args.account_id, label=args.label)
            print(account.label)
        elif args.command == "import-cookies":
            publisher = YouTubeStudioBrowserPublisher(account_id=args.account_id)
            if publisher._profile_has_persisted_google_auth(publisher.profile_dir):
                result = {"authenticated": True, "method": "existing_profile", "account_id": publisher.account_id}
            else:
                content = args.cookies_file.read_text(encoding="utf-8")
                result = publisher.import_netscape_cookies(content)
            print(json.dumps(result, indent=2))
        elif args.command == "profile":
            print(registry.get(args.account_id).profile_dir)
    except (ValueError, OSError, BrowserPublishError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
