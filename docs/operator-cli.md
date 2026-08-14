# Local operator console

The repository includes a Windows-first terminal workflow for running the
review-only cloud multimodal pipeline without using a web UI.

## Start

Double-click `run_operator.cmd` at the repository root. The window stays open
on setup or runtime failure and prints a short next step. The launcher prefers
`.venv\Scripts\python.exe`, then `venv\Scripts\python.exe`, then a standard
Python 3.11 installation, and finally `python` on `PATH`.

The equivalent terminal command is:

```text
python scripts/run_operator_cli.py
```

If no interpreter is found, create `.venv` and install `requirements-dev.txt`
before starting the console again. The launcher passes no user arguments to
the script, so paths and credentials are entered interactively.

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
