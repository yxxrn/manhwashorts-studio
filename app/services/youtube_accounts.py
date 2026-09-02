"""Local registry for browser-isolated YouTube Studio accounts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings

_ACCOUNT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class YouTubeBrowserAccount:
    account_id: str
    label: str
    profile_dir: Path


class YouTubeBrowserAccountRegistry:
    """Keep account labels separate while Chrome auth remains in each profile."""

    def __init__(self) -> None:
        self.base_dir = Path(settings.youtube_browser_accounts_dir).expanduser()
        self.legacy_profile_dir = Path(settings.youtube_browser_profile_dir).expanduser()
        self.registry_path = self.base_dir / "accounts.json"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.base_dir.chmod(0o700)

    @staticmethod
    def normalize_account_id(value: str) -> str:
        account_id = value.strip().casefold()
        if not _ACCOUNT_ID_RE.fullmatch(account_id):
            raise ValueError(
                "account_id must use lowercase letters, numbers, '-' or '_' and be at most 32 characters"
            )
        return account_id

    def _seed(self) -> dict:
        return {
            "version": 1,
            "default_account_id": "default",
            "accounts": [
                {
                    "account_id": "default",
                    "label": "Existing YouTube account",
                    "profile_dir": str(self.legacy_profile_dir),
                    "created_at": datetime.now(UTC).isoformat(),
                }
            ],
        }

    def _load(self) -> dict:
        if not self.registry_path.is_file():
            return self._seed()
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid YouTube browser account registry: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("accounts"), list):
            raise ValueError("invalid YouTube browser account registry structure")
        if not payload.get("accounts"):
            return self._seed()
        return payload

    def _save(self, payload: dict) -> None:
        temp = self.registry_path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(self.registry_path)
        self.registry_path.chmod(0o600)

    @staticmethod
    def _record_to_account(record: dict) -> YouTubeBrowserAccount:
        return YouTubeBrowserAccount(
            account_id=str(record["account_id"]),
            label=str(record.get("label") or record["account_id"]),
            profile_dir=Path(str(record["profile_dir"])).expanduser(),
        )

    def list_accounts(self) -> list[YouTubeBrowserAccount]:
        payload = self._load()
        return [self._record_to_account(row) for row in payload["accounts"]]

    def default_account_id(self) -> str:
        payload = self._load()
        return str(payload.get("default_account_id") or payload["accounts"][0]["account_id"])

    def get(self, account_id: str | None = None) -> YouTubeBrowserAccount:
        payload = self._load()
        wanted = self.normalize_account_id(account_id) if account_id else str(
            payload.get("default_account_id") or payload["accounts"][0]["account_id"]
        )
        for row in payload["accounts"]:
            if row.get("account_id") == wanted:
                account = self._record_to_account(row)
                account.profile_dir.mkdir(parents=True, exist_ok=True)
                account.profile_dir.chmod(0o700)
                return account
        raise ValueError(f"unknown YouTube browser account: {wanted}")

    def create(self, *, account_id: str, label: str) -> YouTubeBrowserAccount:
        account_id = self.normalize_account_id(account_id)
        clean_label = label.strip()[:120] or account_id
        payload = self._load()
        if any(row.get("account_id") == account_id for row in payload["accounts"]):
            raise ValueError(f"YouTube browser account already exists: {account_id}")
        profile_dir = self.base_dir / account_id
        profile_dir.mkdir(parents=True, exist_ok=False)
        profile_dir.chmod(0o700)
        marker = profile_dir / ".manhwashorts-account.json"
        marker.write_text(
            json.dumps({"account_id": account_id, "label": clean_label}, indent=2) + "\n",
            encoding="utf-8",
        )
        marker.chmod(0o600)
        payload["accounts"].append(
            {
                "account_id": account_id,
                "label": clean_label,
                "profile_dir": str(profile_dir),
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        self._save(payload)
        return self.get(account_id)

    def update(
        self,
        account_id: str,
        *,
        label: str | None = None,
        make_default: bool = False,
    ) -> YouTubeBrowserAccount:
        wanted = self.normalize_account_id(account_id)
        payload = self._load()
        target = None
        for row in payload["accounts"]:
            if row.get("account_id") == wanted:
                target = row
                break
        if target is None:
            raise ValueError(f"unknown YouTube browser account: {wanted}")
        if label is not None:
            target["label"] = label.strip()[:120] or wanted
        if make_default:
            payload["default_account_id"] = wanted
        self._save(payload)
        account = self.get(wanted)
        marker = account.profile_dir / ".manhwashorts-account.json"
        marker.write_text(
            json.dumps({"account_id": account.account_id, "label": account.label}, indent=2) + "\n",
            encoding="utf-8",
        )
        marker.chmod(0o600)
        return account

    def describe(self) -> list[dict]:
        default_id = self.default_account_id()
        rows = []
        for account in self.list_accounts():
            rows.append(
                {
                    "account_id": account.account_id,
                    "label": account.label,
                    "profile_dir": str(account.profile_dir),
                    "is_default": account.account_id == default_id,
                    "profile_exists": account.profile_dir.is_dir(),
                }
            )
        return rows
