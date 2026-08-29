from types import SimpleNamespace

import pytest


def test_review_preview_preserves_nested_pipeline_failure_code(monkeypatch, tmp_path):
    from app.services import pipeline

    captured_jobs = []

    class FakeRenderJob:
        def __init__(self, **values):
            self.__dict__.update(values)
            captured_jobs.append(self)

    class FakeDatabase:
        def add(self, _value):
            return None

        def flush(self):
            return None

    project = SimpleNamespace(status=None)
    monkeypatch.setattr(pipeline, "RenderJob", FakeRenderJob)
    monkeypatch.setattr(pipeline, "get_project", lambda *_args: project)
    monkeypatch.setattr(pipeline, "project_scenes", lambda *_args: (object(),))
    monkeypatch.setattr(
        pipeline,
        "build_render_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            pipeline.PipelineError("render.encoder_unavailable: encoder missing")
        ),
    )

    with pytest.raises(pipeline.PipelineError, match="render\\.encoder_unavailable"):
        pipeline.render_silent_review_preview(
            FakeDatabase(),
            "project-a",
            review_source_root=tmp_path,
            output_dir=tmp_path,
        )

    assert captured_jobs[0].error_code == "render.encoder_unavailable"
