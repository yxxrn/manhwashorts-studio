"""Deterministic terminal presentation contracts for the local operator."""

from __future__ import annotations

import importlib


def _ui_module():
    return importlib.import_module("app.services.operator_ui")


def test_no_color_menu_is_readable_in_a_narrow_terminal():
    ui = _ui_module()

    rendered = ui.render_menu(
        provider="configured",
        model="ag/gemini-3.6-flash-high",
        project="final_test",
        job="READY_TO_RENDER",
        color=False,
        width=48,
    )

    lines = rendered.splitlines()
    assert "ManhwaShorts Studio" in rendered
    assert "1) Setup/change cloud provider" in rendered
    assert "0) Exit / Keluar" in rendered
    assert all(len(line) <= 48 for line in lines)
    assert "\x1b[" not in rendered


def test_color_menu_uses_semantic_badges_and_can_be_disabled():
    ui = _ui_module()

    colored = ui.render_menu(
        provider="verified",
        model="vision-model",
        project="chapter",
        job="NEEDS_REVIEW",
        color=True,
        width=72,
    )
    plain = ui.render_status("Provider", "verified", tone="success", color=False)

    assert "\x1b[" in colored
    assert "NEEDS_REVIEW" in colored
    assert "verified" in plain
    assert "\x1b[" not in plain


def test_status_redaction_never_returns_key_shaped_text():
    ui = _ui_module()

    rendered = ui.render_status(
        "Provider",
        "https://gateway.invalid/v1?token=hidden",
        tone="info",
        color=False,
        secret="sk-test-secret-123456",
    )

    assert "sk-test-secret-123456" not in rendered
    assert "[redacted]" in rendered
    assert "token=hidden" not in rendered
