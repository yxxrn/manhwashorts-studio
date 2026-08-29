"""Single-source duration policy and production-boundary contracts."""

from app.constants import (
    DEFAULT_TARGET_SECONDS,
    PROJECT_DURATION_MAX_SECONDS,
    PROJECT_DURATION_MIN_SECONDS,
    STANDARD_FINAL_DURATION_MAX_SECONDS,
    STANDARD_FINAL_DURATION_MIN_SECONDS,
)


def test_duration_defaults_have_one_product_contract():
    from app.config import Settings
    from app.models import Project
    from app.schemas import ProjectCreate

    assert (PROJECT_DURATION_MIN_SECONDS, PROJECT_DURATION_MAX_SECONDS) == (10, 90)
    assert DEFAULT_TARGET_SECONDS == 55
    assert (STANDARD_FINAL_DURATION_MIN_SECONDS, STANDARD_FINAL_DURATION_MAX_SECONDS) == (50.0, 60.0)
    assert Settings.model_fields["default_target_seconds"].default == DEFAULT_TARGET_SECONDS
    assert ProjectCreate.model_fields["target_duration"].default == DEFAULT_TARGET_SECONDS
    assert Project.__table__.c.target_duration.default.arg == DEFAULT_TARGET_SECONDS


def test_reference_profiles_use_the_standard_final_window():
    from app.services import reference_profile

    for profile in (
        reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        reference_profile.REFERENCE_MATCHED_SHORTS_V2,
    ):
        assert profile.duration_min_s == STANDARD_FINAL_DURATION_MIN_SECONDS
        assert profile.duration_max_s == STANDARD_FINAL_DURATION_MAX_SECONDS
