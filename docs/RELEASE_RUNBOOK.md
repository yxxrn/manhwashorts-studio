# Release Runbook

Use this checklist for code releases and production-output verification. It reflects
the current refactored architecture.

## Code preconditions

- Working tree contains only intended source/docs changes; runtime data is unstaged.
- Public facade signatures remain compatible unless a deliberate breaking change is
  documented and migrated.
- Application import graph remains acyclic.
- Historical docs are not being used as current commands or gate definitions.

## Verification gate

```bash
.venv/bin/ruff check app tests
.venv/bin/python -m compileall -q app tests
.venv/bin/pytest -q tests/contracts/test_service_dependency_graph.py
.venv/bin/pytest -q <affected paths>
.venv/bin/pytest -q
git diff --check
```

For docs-only changes, the documentation/architecture contract tests plus lint and
diff check are sufficient unless the docs change a code/test contract. For a
refactor, run the full suite before merging to `main`.

## Production artifact gate

- exact approved script identity is current
- evidence/source lineage checks pass
- TTS voice profile is consistent
- timeline/subtitle contracts pass
- final render succeeds and post-render QC passes
- final checksum and file size are recorded
- automatic thumbnail package exists and thumbnail QC passes

## Rights policy

Rights/source metadata must be retained and reviewable. Under the current default
`MS_REQUIRE_RIGHTS_DECLARATION=false`, rights findings are non-blocking audit/warning
information. Do not list missing rights metadata as a mandatory release blocker
unless enforcement was explicitly enabled for that deployment.

## Do not bypass

A release must not be made green by editing database rows, deleting QC history,
rewriting output checksums, patching a rendered MP4, or calling internal stage
modules to avoid facade validation. Fix generic defects in source, add regression
coverage, and rerun the appropriate supported boundary.

## Git publication

Merge only after the relevant gate is green. Confirm local `main` is a fast-forward
of the tested commit and verify the remote SHA after push. If GitHub credentials
exist only on the bridge, use a Git bundle from the execution host and push from
the authenticated bridge; never expose tokens in commands.
