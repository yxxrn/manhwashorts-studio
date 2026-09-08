#!/usr/bin/env python3
"""Ensure the production Suwayomi extension set is installed and usable."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import suwayomi

STORE_URL = "https://github.com/keiyoushi/extensions/raw/repo/index.pb"
REQUIRED = (
    ("eu.kanade.tachiyomi.extension.en.asurascans", "6247824327199706550", "Asura Scans (EN)"),
    ("eu.kanade.tachiyomi.extension.en.readcomicsonline", "7185601298150078890", "Read Comics Online (EN)"),
)

STORES_QUERY = "{ extensionStores { nodes { name indexUrl } } }"
EXTENSIONS_QUERY = "{ extensions { nodes { pkgName name versionName isInstalled } } }"
ADD_STORE = "mutation($input:AddExtensionStoreInput!){addExtensionStore(input:$input){clientMutationId}}"
FETCH = "mutation($input:FetchExtensionsInput!){fetchExtensions(input:$input){clientMutationId}}"
INSTALL = "mutation($input:UpdateExtensionInput!){updateExtension(input:$input){extension{pkgName name versionName isInstalled}}}"


def main() -> int:
    state = suwayomi.ensure_sidecar()
    if not state.get("available"):
        raise SystemExit(f"Suwayomi is unavailable: {state.get('error') or 'unknown error'}")
    client = suwayomi.client()
    stores = (client.graphql(STORES_QUERY).get("extensionStores") or {}).get("nodes") or []
    if not any(str(row.get("indexUrl") or "") == STORE_URL for row in stores):
        client.graphql(ADD_STORE, {"input": {"indexUrl": STORE_URL}})
    client.graphql(FETCH, {"input": {}})

    extensions = (client.graphql(EXTENSIONS_QUERY).get("extensions") or {}).get("nodes") or []
    by_pkg = {str(row.get("pkgName") or ""): row for row in extensions}
    for package, _source_id, label in REQUIRED:
        row = by_pkg.get(package)
        if not row:
            raise SystemExit(f"Required Suwayomi extension not found in Keiyoushi store: {package}")
        if not bool(row.get("isInstalled")):
            result = client.graphql(INSTALL, {"input": {"id": package, "patch": {"install": True}}})
            installed = (result.get("updateExtension") or {}).get("extension") or {}
            if not bool(installed.get("isInstalled")):
                raise SystemExit(f"Failed to install Suwayomi extension: {package}")
        print(f"Extension ready: {label} ({package})")

    expected = {source_id: label for _package, source_id, label in REQUIRED}
    deadline = time.monotonic() + 30.0
    missing = set(expected)
    while time.monotonic() < deadline:
        sources = {str(row.get("id") or ""): row for row in client.sources()}
        missing = set(expected) - set(sources)
        if not missing:
            for source_id, label in expected.items():
                print(f"Source ready: {label} ({source_id})")
            return 0
        time.sleep(0.5)
    names = ", ".join(f"{expected[source_id]}:{source_id}" for source_id in sorted(missing))
    raise SystemExit(f"Suwayomi extensions installed but required sources did not load: {names}")


if __name__ == "__main__":
    raise SystemExit(main())
