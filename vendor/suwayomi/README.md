# Suwayomi sidecar

`python3 scripts/setup_suwayomi.py` installs the pinned official Suwayomi Server JAR here.
The binary is intentionally ignored by Git. ManhwaShorts launches it as a separate localhost-only, headless process and communicates through Suwayomi GraphQL.

Upstream: https://github.com/Suwayomi/Suwayomi-Server
Pinned release: v2.3.2243
License: Mozilla Public License 2.0 (upstream software; separate from ManhwaShorts)

Suwayomi intentionally has no default online extension repository. Add a compatible extension store/source according to the upstream documentation before using title search.
