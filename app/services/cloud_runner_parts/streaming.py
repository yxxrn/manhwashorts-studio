"""Streaming visual evidence session extracted from cloud_multimodal."""

# ruff: noqa: F821 -- runtime globals come from the compatibility facade.
from __future__ import annotations

from .runtime import runtime_bound

_RUNTIME_NAMES = (
    'CloudStageError',
    'CloudStageRunner',
    'Mapping',
    'MemoryStageCache',
    'VISUAL_CACHE_IDENTITY_VERSION',
    'VISUAL_FINAL_FRESH_SINGLETON_ATTEMPTS',
    'VISUAL_STREAM_VERSION',
    'VisualStageResult',
    '_FixedVisualConcurrency',
    '_PANEL_LOCAL_REJECT_CODES',
    '_PANEL_LOCAL_REJECT_PREDICATES',
    '_ProviderConcurrencyGate',
    '_cache_key',
    '_classify_visual_failure',
    '_hash',
    '_panel_local_rejection_code',
    '_stream_error_category',
    '_stream_validate_rejection',
    '_stream_validate_row',
    '_stream_visual_chunk_cache_key',
    '_visual_checkpoint_seed_for_panel',
    '_visual_panel_identities',
    '_visual_panel_identity_hash',
    '_visual_request_estimated_bytes',
    'queue',
    'threading',
    'time',
)
_bound = runtime_bound(_RUNTIME_NAMES)


class _StreamingVisualEvidenceSession:
    @_bound
    def __init__(
        self,
        runner: CloudStageRunner,
        *,
        queue_size: int,
        max_panels: int,
        max_estimated_bytes: int,
        worker_count: int,
    ) -> None:
        if queue_size < 1:
            raise CloudStageError("cloud.visual_stream_config_invalid")
        self.runner = runner
        self.queue_size = int(queue_size)
        self.max_panels = int(max_panels)
        self.max_estimated_bytes = int(max_estimated_bytes)
        self.worker_count = int(worker_count)
        self._controller = _FixedVisualConcurrency(self.worker_count)
        self._provider_gate = _ProviderConcurrencyGate(self.worker_count)
        self._tasks: queue.Queue[Any] = queue.Queue(maxsize=self.queue_size)
        self._events: queue.Queue[Any] = queue.Queue(maxsize=max(2, self.queue_size * 2))
        self._stop = threading.Event()
        self._closed = False
        self._aborted = False
        self._submit_lock = threading.Lock()
        self._pending: list[CloudPanelInput] = []
        self._submitted: list[CloudPanelInput] = []
        self._batches: dict[int, tuple[CloudPanelInput, ...]] = {}
        self._next_batch_index = 0
        self._accepted: dict[str, dict[str, Any]] = {}
        self._rejected: dict[str, dict[str, Any]] = {}
        self._attempt_ledger: dict[str, dict[str, Any]] = {}
        self._missing: set[str] = set()
        self._failure_codes: list[str] = []
        self._failure_predicates: dict[str, int] = {}
        self._retry_count_total = 0
        self._writer_error: CloudStageError | None = None
        self._max_queue_depth = 0
        self.writer_thread_count = 1
        prompt = runner.prompts["visual"]
        self._checkpoint_scope = _hash(
            {
                "version": VISUAL_STREAM_VERSION,
                "model_identity_hash": runner.model_identity.identity_hash,
                "prompt_version": prompt[0],
                "prompt_sha256": prompt[1],
            }
        )
        self._checkpoint_seed = runner._checkpoint_load(self._checkpoint_scope)
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="visual-stream-writer",
        )
        self._writer_thread.start()
        self._workers = [
            threading.Thread(
                target=self._worker_loop,
                args=(index,),
                name=f"visual-stream-worker-{index}",
            )
            for index in range(self.worker_count)
        ]
        for worker in self._workers:
            worker.start()

    @_bound
    def _worker_runner(self) -> CloudStageRunner:
        return CloudStageRunner(
            provider=self.runner.provider,
            model_identity=self.runner.model_identity,
            cache=MemoryStageCache(),
            # The stream owns the explicit missing-panel retry budget.  Keep
            # transport retries out of each worker runner so a failed chunk
            # cannot amplify into nested whole-batch retries.
            max_attempts=1,
            min_request_interval_s=self.runner.min_request_interval_s,
            estimated_cost_per_request=self.runner.estimated_cost_per_request,
            allow_balloon_unknown=self.runner.allow_balloon_unknown,
            visual_checkpoint_path=None,
            # Batch-level work is already bounded by the stream.  Let tall
            # panel window repair use the same width while the shared provider
            # gate keeps the actual provider concurrency globally bounded.
            visual_parallel_workers=self.worker_count,
            provider_concurrency_gate=self._provider_gate,
        )

    @_bound
    def _flush_pending(self) -> None:
        if not self._pending:
            return
        batch = tuple(self._pending)
        self._pending = []
        batch_index = self._next_batch_index
        self._next_batch_index += 1
        self._batches[batch_index] = batch
        self._tasks.put((batch_index, batch))
        self._max_queue_depth = max(self._max_queue_depth, self._tasks.qsize())

    @_bound
    def submit(self, panel: CloudPanelInput) -> None:
        with self._submit_lock:
            if self._closed or self._aborted:
                raise CloudStageError("cloud.visual_stream_closed")
            if panel.prepared_order is None:
                raise CloudStageError("cloud.panel_lineage_invalid")
            if any(
                existing.panel_id == panel.panel_id
                or existing.prepared_order == panel.prepared_order
                for existing in self._submitted
            ):
                raise CloudStageError("cloud.panel_lineage_invalid")
            self._submitted.append(panel)
            self._pending.append(panel)
            pending_size = sum(_visual_request_estimated_bytes(item) for item in self._pending)
            if len(self._pending) >= self.max_panels or pending_size >= self.max_estimated_bytes:
                self._flush_pending()

    @_bound
    def _process_batch(
        self,
        worker_runner: CloudStageRunner,
        batch_index: int,
        batch: tuple[CloudPanelInput, ...],
    ) -> dict[str, Any]:
        identity_by_id = {
            panel.panel_id: _visual_panel_identity_hash(panel, index)
            for index, panel in enumerate(batch)
        }
        accepted: dict[str, dict[str, Any]] = {}
        rejected: dict[str, dict[str, Any]] = {}
        seeded_ids: list[str] = []
        seeded_rejected_ids: list[str] = []
        attempts_by_id = {panel.panel_id: 0 for panel in batch}
        for panel_index, panel in enumerate(batch):
            seeded = _visual_checkpoint_seed_for_panel(
                self._checkpoint_seed,
                panel,
                panel_index,
                identity_by_id[panel.panel_id],
            )
            if not isinstance(seeded, Mapping):
                continue
            if seeded.get("stream_checkpoint_version") != VISUAL_STREAM_VERSION:
                continue
            try:
                if seeded.get("terminal_status") == "rejected":
                    rejected[panel.panel_id] = _stream_validate_rejection(
                        seeded,
                        panel,
                        expected_identity_hash=identity_by_id[panel.panel_id],
                    )
                    seeded_rejected_ids.append(panel.panel_id)
                else:
                    accepted[panel.panel_id] = _stream_validate_row(
                        seeded,
                        panel,
                        expected_identity_hash=identity_by_id[panel.panel_id],
                    )
                    seeded_ids.append(panel.panel_id)
            except CloudStageError:
                continue
        pending = tuple(
            panel
            for panel in batch
            if panel.panel_id not in accepted and panel.panel_id not in rejected
        )
        error_code = ""
        categories: dict[str, int] = {}
        retries = 0
        partial_success_omission = False
        # Retry only the IDs omitted by a successful response.  A worker
        # runner is single-attempt; transport retry ownership stays in the
        # parent runner and is never multiplied by this loop.
        # The stream worker deliberately performs one provider attempt per
        # call.  The parent runner's configured attempt budget therefore owns
        # the missing-only retry count; do not silently collapse max_attempts
        # greater than two to one retry.
        retry_budget = max(0, self.runner.max_attempts - 1)
        for attempt in range(1 + retry_budget):
            if not pending:
                break
            for panel in pending:
                attempts_by_id[panel.panel_id] += 1
            try:
                # A worker handles multiple batches over its lifetime.  Do not
                # let a predicate from a previous batch classify a new
                # transport/coverage failure as panel-local.
                worker_runner.last_visual_failure_predicates = {}
                result = worker_runner.run_visual_evidence(pending)
                rows = {
                    str(row.get("panel_id", "")): dict(row)
                    for row in result.panels
                    if isinstance(row, Mapping)
                }
                before = len(accepted)
                for panel in pending:
                    row = rows.get(panel.panel_id)
                    if row is None:
                        continue
                    accepted[panel.panel_id] = row
                pending = tuple(panel for panel in pending if panel.panel_id not in accepted)
                if not pending or len(accepted) == before:
                    break
                # A successful partial batch is an attention miss, not a
                # reason to re-batch the omitted rows. Repair each omitted
                # canonical panel in isolation below, even when the provider
                # supplied no explicit failure predicate for the omission.
                partial_success_omission = True
                break
            except CloudStageError as exc:
                error_code = exc.code
                category = _stream_error_category(exc.code)
                categories[category] = categories.get(category, 0) + 1
                if attempt >= retry_budget:
                    break
                retries += 1
        batch_predicates = tuple(worker_runner.last_visual_failure_predicates)
        singleton_recovery_rows = bool(
            pending
            and (
                partial_success_omission
                or error_code == "cloud.provider_response_invalid"
                or (
                    not error_code
                    and len(batch_predicates) == 1
                    and (
                        batch_predicates[0] == "provider_response_invalid"
                        or batch_predicates[0] in _PANEL_LOCAL_REJECT_PREDICATES
                    )
                )
            )
        )
        if singleton_recovery_rows:
            still_missing: list[CloudPanelInput] = []
            for panel in pending:
                predicate = (
                    "provider_response_invalid"
                    if error_code == "cloud.provider_response_invalid"
                    else batch_predicates[0]
                    if len(batch_predicates) == 1
                    else ""
                )
                singleton_error = (
                    "cloud.provider_response_invalid"
                    if predicate == "provider_response_invalid"
                    else "cloud.visual_evidence_invalid"
                    if predicate
                    else "cloud.panel_coverage_incomplete"
                )
                recovered = False
                # A multi-panel attention/schema miss must not consume the
                # singleton recovery budget. Give recoverable batch failures
                # the configured number of isolated submissions; this remains
                # bounded and persistent failures are still quarantined.
                remaining_attempts = max(1, self.runner.max_attempts)
                for _ in range(remaining_attempts):
                    attempts_by_id[panel.panel_id] += 1
                    retries += 1
                    worker_runner.last_visual_failure_predicates = {}
                    try:
                        singleton = worker_runner.run_visual_evidence((panel,))
                        row = next(
                            (
                                dict(item)
                                for item in singleton.panels
                                if isinstance(item, Mapping)
                                and str(item.get("panel_id", "")) == panel.panel_id
                            ),
                            None,
                        )
                        if row is not None:
                            accepted[panel.panel_id] = row
                            recovered = True
                            break
                    except CloudStageError as exc:
                        singleton_error = exc.code
                        category = _stream_error_category(exc.code)
                        categories[category] = categories.get(category, 0) + 1
                        if exc.code == "cloud.provider_response_invalid":
                            predicate = "provider_response_invalid"
                            continue
                    predicates = tuple(worker_runner.last_visual_failure_predicates)
                    predicate = predicates[0] if len(predicates) == 1 else ""
                    if not predicate:
                        break
                if recovered:
                    continue

                # Rare provider/schema or semantic misses can persist across
                # retries owned by one long-lived worker runner. Before
                # quarantining a panel, confirm the failure once using a fresh
                # runner with an isolated cache/context. This touches only
                # already-failed outliers and shares the same global provider
                # concurrency gate.
                for _ in range(VISUAL_FINAL_FRESH_SINGLETON_ATTEMPTS):
                    fresh_runner = self._worker_runner()
                    fresh_runner.last_visual_failure_predicates = {}
                    attempts_by_id[panel.panel_id] += 1
                    retries += 1
                    fresh_row = None
                    try:
                        fresh_singleton = fresh_runner.run_visual_evidence((panel,))
                        fresh_row = next(
                            (
                                dict(item)
                                for item in fresh_singleton.panels
                                if isinstance(item, Mapping)
                                and str(item.get("panel_id", "")) == panel.panel_id
                            ),
                            None,
                        )
                    except CloudStageError as exc:
                        singleton_error = exc.code
                        category = _stream_error_category(exc.code)
                        categories[category] = categories.get(category, 0) + 1
                    finally:
                        worker_runner.request_count += fresh_runner.request_count
                        for key, value in fresh_runner.request_counts.items():
                            worker_runner.request_counts[key] = (
                                worker_runner.request_counts.get(key, 0) + value
                            )
                        worker_runner.estimated_cost_usd += fresh_runner.estimated_cost_usd

                    if fresh_row is not None:
                        accepted[panel.panel_id] = fresh_row
                        recovered = True
                        break
                    fresh_predicates = tuple(
                        fresh_runner.last_visual_failure_predicates
                    )
                    predicate = (
                        fresh_predicates[0]
                        if len(fresh_predicates) == 1
                        else predicate
                    )

                if recovered:
                    continue
                if (
                    _classify_visual_failure(
                        singleton_error,
                        singleton=True,
                        predicate=predicate,
                    )
                    == "panel_local_reject"
                ):
                    rejection_code = _panel_local_rejection_code(
                        singleton_error,
                        predicate,
                    )
                    rejected[panel.panel_id] = {
                        "panel_id": panel.panel_id,
                        "source_asset_id": panel.source_asset_id,
                        "source_order": panel.source_order,
                        "source_checksum": panel.source_checksum,
                        "cache_identity_hash": identity_by_id[panel.panel_id],
                        "cache_identity_version": VISUAL_CACHE_IDENTITY_VERSION,
                        "stream_checkpoint_version": VISUAL_STREAM_VERSION,
                        "terminal_status": "rejected",
                        "failure_scope": "panel_local_reject",
                        "rejection_code": rejection_code,
                        "reason_code": rejection_code,
                        "failure_predicate": predicate or rejection_code,
                        "attempt_count": attempts_by_id[panel.panel_id],
                    }
                    continue
                still_missing.append(panel)
                if not error_code:
                    error_code = singleton_error
            pending = tuple(still_missing)
        if pending and not error_code:
            error_code = "cloud.panel_coverage_incomplete"
        predicates = tuple(worker_runner.last_visual_failure_predicates)
        local_predicate = predicates[0] if len(predicates) == 1 else ""
        local_code = (
            not error_code
            or error_code == "cloud.panel_coverage_incomplete"
            or error_code in _PANEL_LOCAL_REJECT_CODES
        )
        if pending and local_code and local_predicate in _PANEL_LOCAL_REJECT_PREDICATES:
            rejection_code = _panel_local_rejection_code(
                error_code,
                local_predicate,
            )
            if (
                _classify_visual_failure(
                    rejection_code,
                    singleton=True,
                    predicate=local_predicate,
                )
                == "panel_local_reject"
            ):
                for panel in pending:
                    rejected[panel.panel_id] = {
                        "panel_id": panel.panel_id,
                        "source_asset_id": panel.source_asset_id,
                        "source_order": panel.source_order,
                        "source_checksum": panel.source_checksum,
                        "cache_identity_hash": identity_by_id[panel.panel_id],
                        "cache_identity_version": VISUAL_CACHE_IDENTITY_VERSION,
                        "stream_checkpoint_version": VISUAL_STREAM_VERSION,
                        "terminal_status": "rejected",
                        "failure_scope": "panel_local_reject",
                        "rejection_code": rejection_code,
                        "reason_code": rejection_code,
                        "failure_predicate": local_predicate,
                        "attempt_count": max(1, attempts_by_id[panel.panel_id]),
                    }
                error_code = rejection_code
                pending = ()
        if len(pending) == 1:
            panel = pending[0]
            predicate = next(
                iter(worker_runner.last_visual_failure_predicates),
                "",
            )
            if (
                _classify_visual_failure(
                    error_code,
                    singleton=True,
                    predicate=predicate,
                )
                == "panel_local_reject"
            ):
                rejection_code = _panel_local_rejection_code(
                    error_code,
                    predicate,
                )
                rejected[panel.panel_id] = {
                    "panel_id": panel.panel_id,
                    "source_asset_id": panel.source_asset_id,
                    "source_order": panel.source_order,
                    "source_checksum": panel.source_checksum,
                    "cache_identity_hash": identity_by_id[panel.panel_id],
                    "cache_identity_version": VISUAL_CACHE_IDENTITY_VERSION,
                    "stream_checkpoint_version": VISUAL_STREAM_VERSION,
                    "terminal_status": "rejected",
                    "failure_scope": "panel_local_reject",
                    "rejection_code": rejection_code,
                    "reason_code": rejection_code,
                    "failure_predicate": predicate or rejection_code,
                    "attempt_count": max(1, attempts_by_id[panel.panel_id]),
                }
        terminal_ledger = []
        for panel in batch:
            panel_id = panel.panel_id
            if panel_id in accepted:
                status = "accepted"
                attempt_count = attempts_by_id[panel_id]
            elif panel_id in rejected:
                status = "rejected"
                attempt_count = int(rejected[panel_id]["attempt_count"])
            else:
                status = "missing"
                attempt_count = attempts_by_id[panel_id]
            terminal_ledger.append(
                {
                    "panel_id": panel_id,
                    "cache_identity_hash": identity_by_id[panel_id],
                    "attempt_count": int(attempt_count),
                    "terminal_status": status,
                }
            )
        return {
            "batch_index": batch_index,
            "rows": list(accepted.values()),
            "seeded_ids": tuple(seeded_ids),
            "seeded_rejected_ids": tuple(seeded_rejected_ids),
            "rejected": list(rejected.values()),
            "missing_ids": tuple(
                panel.panel_id for panel in pending if panel.panel_id not in rejected
            ),
            "error_code": error_code,
            "retry_count": retries,
            "request_count": worker_runner.request_count,
            "request_counts": dict(worker_runner.request_counts),
            "estimated_cost_usd": worker_runner.estimated_cost_usd,
            "categories": categories,
            "failure_predicates": dict(worker_runner.last_visual_failure_predicates),
            "attempt_ledger": terminal_ledger,
        }

    @_bound
    def _worker_loop(self, worker_index: int) -> None:
        worker_runner = self._worker_runner()
        while True:
            task = self._tasks.get()
            try:
                if task is None:
                    return
                if self._stop.is_set():
                    continue
                batch_index, batch = task
                self._controller.acquire()
                started = time.monotonic()
                before_request_count = worker_runner.request_count
                before_request_counts = dict(worker_runner.request_counts)
                before_cost = worker_runner.estimated_cost_usd
                try:
                    event = self._process_batch(worker_runner, batch_index, batch)
                except Exception:
                    event = {
                        "batch_index": batch_index,
                        "rows": [],
                        "seeded_ids": (),
                        "missing_ids": tuple(panel.panel_id for panel in batch),
                        "error_code": "cloud.provider_request_failed",
                        "retry_count": 0,
                        "request_count": worker_runner.request_count,
                        "request_counts": dict(worker_runner.request_counts),
                        "estimated_cost_usd": worker_runner.estimated_cost_usd,
                        "categories": {"other_failure": 1},
                    }
                event["request_count"] = worker_runner.request_count - before_request_count
                event["request_counts"] = {
                    key: worker_runner.request_counts.get(key, 0)
                    - before_request_counts.get(key, 0)
                    for key in worker_runner.request_counts
                }
                event["estimated_cost_usd"] = worker_runner.estimated_cost_usd - before_cost
                self._controller.release(
                    panel_count=len(batch),
                    request_count=int(event.get("request_count", 0)),
                    latency_s=time.monotonic() - started,
                    categories=event.get("categories", {}),
                )
                event["worker_index"] = worker_index
                self._events.put(event)
            finally:
                self._tasks.task_done()

    @_bound
    def _writer_loop(self) -> None:
        while True:
            event = self._events.get()
            try:
                if event is None:
                    return
                self._consume_event(event)
            finally:
                self._events.task_done()

    @_bound
    def _consume_event(self, event: Mapping[str, Any]) -> None:
        if self._aborted:
            return
        for key, value in dict(event.get("request_counts", {})).items():
            if key in self.runner.request_counts:
                self.runner.request_counts[key] += int(value)
        self.runner.request_count += int(event.get("request_count", 0))
        self.runner.estimated_cost_usd += float(event.get("estimated_cost_usd", 0.0))
        self._retry_count_total += int(event.get("retry_count", 0))
        for key, value in dict(event.get("failure_predicates", {})).items():
            self._failure_predicates[str(key)] = self._failure_predicates.get(str(key), 0) + int(
                value
            )
        if self._writer_error is not None:
            return
        batch_index = int(event.get("batch_index", -1))
        batch = self._batches.get(batch_index)
        if batch is None:
            self._writer_error = CloudStageError("cloud.visual_stream_row_invalid")
            return
        expected = {panel.panel_id: panel for panel in batch}
        expected_hashes = {
            panel.panel_id: _visual_panel_identity_hash(panel, index)
            for index, panel in enumerate(batch)
        }
        seeded_ids = {str(panel_id) for panel_id in event.get("seeded_ids", ())}
        seeded_rejected_ids = {str(panel_id) for panel_id in event.get("seeded_rejected_ids", ())}
        chunk_key = _stream_visual_chunk_cache_key(
            batch,
            chunk_index=batch_index,
            model_identity=self.runner.model_identity,
            prompt=self.runner.prompts["visual"],
        )
        try:
            rows = event.get("rows", ())
            if not isinstance(rows, (list, tuple)):
                raise CloudStageError("cloud.visual_stream_row_invalid")
            for raw_row in rows:
                if not isinstance(raw_row, Mapping):
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                panel_id = str(raw_row.get("panel_id", ""))
                panel = expected.get(panel_id)
                if panel is None or panel_id in self._accepted:
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                clean = _stream_validate_row(
                    raw_row,
                    panel,
                    expected_identity_hash=expected_hashes[panel_id],
                )
                if panel_id in seeded_ids:
                    if clean.get("stream_checkpoint_version") != VISUAL_STREAM_VERSION:
                        raise CloudStageError("cloud.visual_stream_row_invalid")
                    clean["chunk_cache_key"] = chunk_key
                else:
                    clean["chunk_cache_key"] = chunk_key
                    clean["stream_checkpoint_version"] = VISUAL_STREAM_VERSION
                    self.runner._checkpoint_append(self._checkpoint_scope, clean)
                self._accepted[panel_id] = clean
            raw_rejections = event.get("rejected", ())
            if not isinstance(raw_rejections, (list, tuple)):
                raise CloudStageError("cloud.visual_stream_row_invalid")
            for raw_rejection in raw_rejections:
                if not isinstance(raw_rejection, Mapping):
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                panel_id = str(raw_rejection.get("panel_id", ""))
                panel = expected.get(panel_id)
                if panel is None or panel_id in self._accepted or panel_id in self._rejected:
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                clean_rejection = _stream_validate_rejection(
                    raw_rejection,
                    panel,
                    expected_identity_hash=expected_hashes[panel_id],
                )
                clean_rejection["chunk_cache_key"] = chunk_key
                if panel_id not in seeded_rejected_ids:
                    self.runner._checkpoint_append(
                        self._checkpoint_scope,
                        clean_rejection,
                    )
                self._rejected[panel_id] = clean_rejection
            for panel_id in event.get("missing_ids", ()):
                panel_id = str(panel_id)
                if (
                    panel_id not in expected
                    or panel_id in self._accepted
                    or panel_id in self._rejected
                ):
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                self._missing.add(panel_id)
            raw_attempts = event.get("attempt_ledger", ())
            if not isinstance(raw_attempts, (list, tuple)):
                raise CloudStageError("cloud.visual_stream_row_invalid")
            for raw_attempt in raw_attempts:
                if not isinstance(raw_attempt, Mapping):
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                panel_id = str(raw_attempt.get("panel_id", ""))
                if panel_id not in expected:
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                try:
                    attempt_count = int(raw_attempt.get("attempt_count", -1))
                except (TypeError, ValueError):
                    raise CloudStageError("cloud.visual_stream_row_invalid") from None
                if (
                    str(raw_attempt.get("cache_identity_hash", "")) != expected_hashes[panel_id]
                    or attempt_count < 0
                    or str(raw_attempt.get("terminal_status", ""))
                    not in {"accepted", "rejected", "missing"}
                ):
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                self._attempt_ledger[panel_id] = dict(raw_attempt)
            if event.get("error_code"):
                self._failure_codes.append(str(event["error_code"]))
        except CloudStageError as exc:
            self._writer_error = exc

    @_bound
    def _shutdown(self, *, aborted: bool) -> None:
        if self._closed:
            return
        self._closed = True
        if aborted:
            self._aborted = True
            self._stop.set()
        for _ in self._workers:
            self._tasks.put(None)
        self._tasks.join()
        for worker in self._workers:
            worker.join()
        self._events.put(None)
        self._events.join()
        self._writer_thread.join()

    @_bound
    def finish(self, panels: Sequence[CloudPanelInput]) -> VisualStageResult:
        if self._closed or self._aborted:
            raise CloudStageError("cloud.visual_stream_closed")
        self._flush_pending()
        self._shutdown(aborted=False)
        if self._writer_error is not None:
            raise self._writer_error
        ordered = CloudStageRunner._ordered_panels(tuple(panels))
        submitted_ids = tuple(item.panel_id for item in self._submitted)
        ordered_ids = tuple(item.panel_id for item in ordered)
        if len(submitted_ids) != len(ordered_ids) or set(submitted_ids) != set(ordered_ids):
            raise CloudStageError("cloud.panel_lineage_invalid")
        submitted_ids = {item.panel_id for item in ordered}
        terminal_ids = set(self._accepted) | set(self._rejected) | self._missing
        unresolved = submitted_ids - terminal_ids
        self._missing.update(unresolved)
        if self._missing:
            metrics = self._controller.snapshot()
            metrics.update(self._provider_gate.snapshot())
            metrics.update(
                {
                    "contract_version": VISUAL_STREAM_VERSION,
                    "writer_count": self.writer_thread_count,
                    "submitted_panel_count": len(ordered),
                    "accepted_panel_count": len(self._accepted),
                    "missing_panel_count": len(self._missing),
                    "missing_panel_ids": sorted(self._missing),
                    "rejected_panel_count": len(self._rejected),
                    "rejected_panel_ids": sorted(self._rejected),
                    "request_count": self.runner.request_count,
                    "request_counts": dict(self.runner.request_counts),
                    "retry_count": self._retry_count_total,
                    "max_queue_depth": self._max_queue_depth,
                    "terminal_failure_codes": sorted(set(self._failure_codes)),
                    "visual_failure_predicates": dict(self._failure_predicates),
                    "panel_attempt_ledger": [
                        dict(self._attempt_ledger[panel.panel_id])
                        for panel in ordered
                        if panel.panel_id in self._attempt_ledger
                    ],
                }
            )
            self.runner.last_visual_stream_metrics = metrics
            raise CloudStageError(
                "cloud.panel_coverage_incomplete",
                reviewable=True,
                safe_metadata={
                    "submitted_panel_count": len(ordered),
                    "accepted_panel_count": len(self._accepted),
                    "missing_panel_count": len(self._missing),
                },
            )
        accepted_ordered = tuple(panel for panel in ordered if panel.panel_id in self._accepted)
        if not accepted_ordered:
            metrics = self._controller.snapshot()
            metrics.update(self._provider_gate.snapshot())
            metrics.update(
                {
                    "contract_version": VISUAL_STREAM_VERSION,
                    "writer_count": self.writer_thread_count,
                    "submitted_panel_count": len(ordered),
                    "accepted_panel_count": 0,
                    "missing_panel_count": 0,
                    "missing_panel_ids": [],
                    "rejected_panel_count": len(self._rejected),
                    "rejected_panel_ids": sorted(self._rejected),
                    "request_count": self.runner.request_count,
                    "request_counts": dict(self.runner.request_counts),
                    "retry_count": self._retry_count_total,
                    "max_queue_depth": self._max_queue_depth,
                    "terminal_failure_codes": sorted(set(self._failure_codes)),
                    "visual_failure_predicates": dict(self._failure_predicates),
                    "panel_attempt_ledger": [
                        dict(self._attempt_ledger[panel.panel_id])
                        for panel in ordered
                        if panel.panel_id in self._attempt_ledger
                    ],
                }
            )
            self.runner.last_visual_stream_metrics = metrics
            raise CloudStageError(
                "visual.capacity_insufficient",
                reviewable=True,
                safe_metadata={
                    "submitted_panel_count": len(ordered),
                    "accepted_panel_count": 0,
                    "rejected_panel_count": len(self._rejected),
                },
            )
        rows = tuple(self._accepted[panel.panel_id] for panel in accepted_ordered)
        source = list(_visual_panel_identities(accepted_ordered))
        prompt = self.runner.prompts["visual"]
        result = VisualStageResult(
            panels=rows,
            source_hash=_hash(source),
            model_identity_hash=self.runner.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
            cache_identity_version=VISUAL_CACHE_IDENTITY_VERSION,
            panel_identity_hashes=tuple(
                str(self._accepted[panel.panel_id].get("cache_identity_hash", ""))
                for panel in accepted_ordered
            ),
            rejected_panels=tuple(
                self._rejected[panel.panel_id]
                for panel in ordered
                if panel.panel_id in self._rejected
            ),
            panel_attempt_ledger=tuple(
                self._attempt_ledger[panel.panel_id]
                for panel in ordered
                if panel.panel_id in self._attempt_ledger
            ),
        )
        key = _cache_key("visual", source, self.runner.model_identity, prompt)
        if self.runner.cache is not None:
            self.runner.cache.put(key, result.as_dict())
        metrics = self._controller.snapshot()
        metrics.update(self._provider_gate.snapshot())
        metrics.update(
            {
                "contract_version": VISUAL_STREAM_VERSION,
                "writer_count": self.writer_thread_count,
                "submitted_panel_count": len(ordered),
                "accepted_panel_count": len(rows),
                "missing_panel_count": len(self._missing),
                "missing_panel_ids": sorted(self._missing),
                "rejected_panel_count": len(self._rejected),
                "rejected_panel_ids": sorted(self._rejected),
                "request_count": self.runner.request_count,
                "request_counts": dict(self.runner.request_counts),
                "retry_count": self._retry_count_total,
                "max_queue_depth": self._max_queue_depth,
                "visual_failure_predicates": dict(self._failure_predicates),
                "panel_attempt_ledger": [
                    dict(self._attempt_ledger[panel.panel_id])
                    for panel in ordered
                    if panel.panel_id in self._attempt_ledger
                ],
            }
        )
        self.runner.last_visual_stream_metrics = metrics
        return result

    @_bound
    def abort(self) -> None:
        if self._closed:
            return
        self._shutdown(aborted=True)
