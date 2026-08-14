# Local operator console

The repository includes a Windows-first terminal workflow for running the
review-only cloud multimodal pipeline without using a web UI.

## Start

Double-click `run_operator.cmd` at the repository root. The window stays open
on setup or runtime failure and prints a short next step. The launcher prefers
`.venv\Scripts\python.exe`; otherwise the PowerShell boundary in
`scripts/operator_launcher.ps1` validates `py -3.11`, `py -3`, then `python`
in that order. The executable and selector arguments are kept as separate
PowerShell arrays and invoked with `&`, so a broken `py.exe`, `py.ini`, Store
stub, or path containing spaces falls through safely instead of being joined
into a malformed command. No user-specific Python path, global pip, or PATH
mutation is used.

The equivalent terminal command is:

```text
python scripts/bootstrap_operator_cli.py
```

After the environment is ready, the cross-platform direct entrypoint remains
`python scripts/run_operator_cli.py`. On first run, the bootstrap creates or
repairs the repository `.venv` in place and installs only the authoritative
runtime `requirements.txt` (never `requirements-dev.txt`). It verifies
SQLAlchemy, Pillow, FastAPI/Pydantic, cryptography/BYOK, and the operator
module before launching the menu. A deterministic fingerprint of the exact
requirements bytes and Python version is stored inside `.venv`; a healthy
matching environment skips pip. Stale fingerprints or failed imports trigger
an in-place repair without deleting the venv.

If no Python 3.11+ interpreter is found, install Python 3.11+ and retry. Offline,
proxy, SSL, venv, and package failures have separate sanitized recovery codes;
the venv is preserved and the success marker is not written after a failed
install. The launcher passes no user arguments to the script, so paths and
credentials are entered interactively.

## Menu

The console provides:

1. Setup/change cloud provider
2. Test connection
3. Fetch/select model
4. Import/run one chapter folder
5. Run a batch parent folder
6. Resume failed/pending jobs
7. View status/review blockers
0. Exit

Provider setup accepts an OpenAI-compatible base URL, an optional explicit
`/models` URL, a provider label, and a hidden API-key prompt. A blank base URL
uses the selected provider's configured default. The URL must be plain
`http(s)` without credentials, query strings, or fragments. The model list is
validated as a structured `{"data": [{"id": "..."}]}` response, sorted
deterministically, and selected by number, filter, or an explicitly listed
`manual:<model-id>` value.

Credentials are saved only through the existing encrypted BYOK service. The
key is held in memory for the bounded model request and BYOK verification, then
discarded; it is not written to CLI arguments, logs, JSON state, exceptions,
or this document. The optional visual capability check requires an explicit
confirmation because it can make a billable provider request. A model name
alone never proves vision capability.

For one chapter, paste or drag a folder path. Supported images are ordered by
case-insensitive filename, then filename, and unsupported files reject the
folder. A confirmation is required before import and cloud calls. Batch mode
uses direct child folders in the same deterministic order, displays request
budget settings, and isolates each job. Existing ingest/segmentation,
`CloudBatchService`, and JSON job-state/resume services remain the execution
boundaries; the CLI does not duplicate provider or subprocess logic.

Review state is kept under the existing ignored paths
`data/cloud-multimodal-jobs/` and `data/segmentation-review/`. Status output
shows only safe job IDs, states, stable error codes, review counts, provider
labels, key hints, and selected model IDs. `READY_TO_RENDER` means the AI and
narrative stages passed their gates. It does not authorize voiced/final render:
voice timing, editorial approval, rights, and publication gates remain active.

No voice/TTS/audio, publication, local vision fallback, source-media commit, or
provider call is part of the CLI implementation. Without a verified BYOK
credential, setup-independent menu items such as exit and local path checks
still work, while cloud actions fail closed with sanitized messages.
