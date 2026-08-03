import pytest

from app.schemas import RenderRequestIn


def test_render_profiles_validate():
    assert RenderRequestIn(profile="Balanced").profile == "Balanced"
    for profile in ("Auto", "Calm", "Dynamic", "No motion"):
        assert RenderRequestIn(profile=profile).profile == profile
    with pytest.raises(ValueError):
        RenderRequestIn(profile="Random")
