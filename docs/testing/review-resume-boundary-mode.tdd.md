# Review resume boundary-mode TDD evidence

## Source and user journey

The journey was derived from the 2026-08-24 local production incident: an
operator resuming a `NEEDS_REVIEW` job from the review-only CLI must retain the
same review-only safety policy used for an initial import/run. A resume must not
silently switch to the production segmentation policy.

## Task report

- RED: `python -m pytest tests/test_operator_cli.py -q -k resume_jobs_preserves_review_only_boundary`
  executed the new regression and failed with
  `KeyError: 'review_only_preview'`.
- GREEN: the same regression plus the existing review-run boundary test passed
  after `OperatorCLI.resume_jobs()` forwarded the explicit review-only flag,
  upscale policy, and output directory.
- Regression: `python -m pytest tests/test_operator_cli.py tests/test_strip_segmentation.py tests/test_cloud_multimodal_mass_production.py -q`
  completed with `245 passed`; seven existing Pillow deprecation warnings were
  emitted.

## Test specification

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| 1 | Menu 6 preserves `review_only_preview=True` for resumed jobs | `test_resume_jobs_preserves_review_only_boundary` | unit | PASS |
| 2 | Menu 6 preserves the silent-review upscale policy and output boundary | `test_resume_jobs_preserves_review_only_boundary` | unit | PASS |
| 3 | The normal review execution adapter still forwards all review boundaries | `test_operator_review_run_passes_source_and_output_boundaries_to_service` | unit | PASS |
| 4 | Segmentation and cloud multimodal focused regressions remain green | focused 245-test command above | integration matrix | PASS |

## Coverage and known gaps

The fix changes argument forwarding rather than segmentation geometry. The
focused operator/segmentation/cloud matrix covers the affected boundary. The
real provider resume remains the required end-to-end proof; tests alone do not
claim a preview, narration, render, or QC result. A scoped coverage attempt with
`--cov=app.services.operator_cli` could not run because `pytest-cov` is not
installed in the repository environment; no coverage percentage is claimed.

## Merge evidence

- RED checkpoint: `eab666c8223d082708fc4eee1472207d0408f99f`
- GREEN checkpoint: recorded by the following fix commit.
