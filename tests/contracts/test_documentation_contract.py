"""Guard active documentation against known stale architecture instructions."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTIVE_DOCS = (
    'README.md',
    'AGENTS.md',
    'docs/INDEX.md',
    'docs/STATUS.md',
    'docs/ARCHITECTURE.md',
    'docs/MAINTAINER_GUIDE.md',
    'docs/OPERATIONS.md',
    'docs/RELEASE_RUNBOOK.md',
    'docs/AGENT.md',
    'docs/COPYRIGHT.md',
    'docs/API.md',
    'docs/MOTION_COMIC.md',
    'docs/P0_EDITORIAL.md',
    'docs/UI.md',
    'docs/BYOK.md',
    'docs/TTS_OPTIONS.md',
    'docs/VISUAL_SELECTION.md',
    'docs/GPU.md',
    'docs/YOUTUBE_SETUP.md',
    'docs/operator-cli.md',
)

STALE_PHRASES = (
    'tests/test_cloud_multimodal_mass_production.py',
    'production publication remains blocked until a real source has a verified rights declaration',
    'Rights failures block release.',
    '`rights.undeclared_assets` blocks the render.',
    'Publication: explicit approval + rights + QC.',
    'target_duration` must be 10–60',
    'Target duration: `60–90s`; ideal `70–85s`',
)


def _active_text() -> str:
    chunks = []
    for relative in ACTIVE_DOCS:
        path = ROOT / relative
        assert path.is_file(), f'missing active documentation: {relative}'
        chunks.append(f'\n--- {relative} ---\n{path.read_text(encoding="utf-8")}')
    return ''.join(chunks)


def test_active_documentation_does_not_restore_removed_paths_or_gates():
    text = _active_text()
    for phrase in STALE_PHRASES:
        assert phrase not in text, f'stale active documentation phrase: {phrase}'


def test_current_rights_default_is_documented_consistently():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    operations = (ROOT / 'docs/OPERATIONS.md').read_text(encoding='utf-8')
    assert '| `MS_REQUIRE_RIGHTS_DECLARATION` | `false` |' in readme
    assert '`MS_REQUIRE_RIGHTS_DECLARATION=false`' in operations
    env_example = (ROOT / '.env.example').read_text(encoding='utf-8')
    assert 'MS_REQUIRE_RIGHTS_DECLARATION=false' in env_example


def test_maintainer_docs_name_current_facades_and_test_layout():
    text = _active_text()
    for required in ('pipeline_stages/', 'cloud_runner_parts/', 'tests/cloud/', 'tests/contracts/'):
        assert required in text


def test_active_documentation_has_no_removed_root_test_paths():
    text = _active_text()
    stale = sorted(set(re.findall(r"tests/test_[A-Za-z0-9_]+\.py", text)))
    assert stale == [], f"stale root test paths in active docs: {stale}"


def test_active_relative_markdown_links_resolve():
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+\.md(?:#[^)]+)?)\)")
    failures = []
    for relative in ACTIVE_DOCS:
        source = ROOT / relative
        for raw_target in link_pattern.findall(source.read_text(encoding="utf-8")):
            target = raw_target.split("#", 1)[0]
            if target.startswith(("http://", "https://")):
                continue
            resolved = (source.parent / target).resolve()
            if not resolved.is_file():
                failures.append(f"{relative} -> {target}")
    assert failures == [], "broken active documentation links: " + "; ".join(failures)
