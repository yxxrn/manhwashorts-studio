from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PREVIEW_SCRIPT = ROOT / "scripts" / "review" / "render_codex_manual_preview.py"


def _preview_module():
    spec = importlib.util.spec_from_file_location("sentence_karaoke_preview", PREVIEW_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _timed_words(*words: tuple[str, float, float]) -> list[dict[str, object]]:
    return [
        {
            "cue_index": index + 1,
            "spoken_token_index": index,
            "text": text,
            "start_s": start,
            "end_s": end,
        }
        for index, (text, start, end) in enumerate(words)
    ]


def test_sentence_grouping_respects_spoken_punctuation_and_keeps_word_timing():
    module = _preview_module()

    groups = module.build_sentence_caption_groups(
        "First beat. Then a longer turn?",
        _timed_words(
            ("FIRST", 0.0, 0.4),
            ("BEAT", 0.4, 0.8),
            ("THEN", 0.9, 1.3),
            ("A", 1.3, 1.5),
            ("LONGER", 1.5, 1.9),
            ("TURN", 1.9, 2.3),
        ),
    )

    assert len(groups) == 2
    assert [word.text for word in groups[0].words] == ["FIRST", "BEAT"]
    assert [word.text for word in groups[1].words] == ["THEN", "A", "LONGER", "TURN"]
    assert (groups[0].start_time, groups[0].end_time) == (0.0, 0.8)
    assert (groups[1].start_time, groups[1].end_time) == (0.9, 2.3)


def test_sentence_ass_repeats_full_sentence_and_scales_only_active_word():
    from app.services.render import build_sentence_karaoke_ass

    module = _preview_module()
    group = module.KaraokeSentenceGroup(
        group_id="sentence-1",
        words=tuple(
            module.KaraokeWord(text, start, end)
            for text, start, end in (
                ("FIRST", 0.0, 0.4),
                ("BEAT", 0.4, 0.8),
                ("MATTERS", 0.8, 1.2),
            )
        ),
        start_time=0.0,
        end_time=1.2,
    )

    ass = build_sentence_karaoke_ass(
        [group],
        width=1080,
        height=1920,
        font_name="Barber Chop",
        max_chars=28,
        max_lines=2,
        active_scale=1.08,
    )
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]

    assert len(dialogues) == 3
    assert all("FIRST" in line and "BEAT" in line and "MATTERS" in line for line in dialogues)
    assert all("\\c&H0000FFFF&" in line for line in dialogues)
    assert all("\\fscx108\\fscy108" in line for line in dialogues)
    assert all("\\fscx100\\fscy100" in line for line in dialogues)
    assert all("\\pos(540,1075)" in line for line in dialogues)
    assert all("?" not in line and "!" not in line for line in dialogues)


def test_sentence_ass_preserves_timing_boundaries_and_stable_two_line_wrap():
    from app.services import subtitle_karaoke
    from app.services.render import build_sentence_karaoke_ass

    module = _preview_module()
    spoken = "The battlefield is broken while the argument gives way."
    groups = subtitle_karaoke.build_sentence_caption_groups(
        spoken,
        [
            {"word": word, "start": index * 0.5, "end": (index + 1) * 0.5}
            for index, word in enumerate(spoken.split())
        ],
    )

    ass = build_sentence_karaoke_ass(
        groups,
        width=1080,
        height=1920,
        max_chars=module.CAPTION_MAX_CHARS,
        max_lines=2,
    )
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogues) == len(spoken.split())
    assert all(line.count("\\N") <= 1 for line in dialogues)
    assert dialogues[0].startswith("Dialogue: 0,0:00:00.00,0:00:00.50,")
    assert dialogues[-1].startswith("Dialogue: 0,0:00:04.00,0:00:04.50,")
    assert all(len(re.findall(r"\\N", line)) <= 1 for line in dialogues)


def test_sentence_grouping_rejects_missing_or_punctuated_display_words():
    module = _preview_module()

    with pytest.raises(ValueError, match="subtitle.word_timing_missing"):
        module.build_sentence_caption_groups("One sentence.", [])

    with pytest.raises(ValueError, match="subtitle.display_punctuation"):
        module.KaraokeWord("NO!", 0.0, 1.0)

    with pytest.raises(ValueError, match="subtitle.word_timing_invalid"):
        module.KaraokeWord("WORD", -0.1, 1.0)


def test_sentence_ass_rejects_three_line_overflow():
    from app.services.render import RenderError, build_sentence_karaoke_ass

    module = _preview_module()
    words = tuple(
        module.KaraokeWord(f"WORD{index}", index * 0.2, (index + 1) * 0.2)
        for index in range(20)
    )
    group = module.KaraokeSentenceGroup("too-long", words, 0.0, 4.0)

    with pytest.raises(RenderError, match="subtitle overflow"):
        build_sentence_karaoke_ass([group], 1080, 1920, max_chars=20, max_lines=2)


def test_semantic_chunking_is_deterministic_and_avoids_orphans():
    module = _preview_module()
    spoken = (
        "The battlefield breaks while the wounded pair searches for a way out "
        "and the marked man waits calmly behind them."
    )
    raw_words = spoken.split()
    timed = _timed_words(
        *(
            (re.sub(r"[^A-Z0-9]", "", word.upper()), index * 0.4, (index + 1) * 0.4)
            for index, word in enumerate(raw_words)
        )
    )

    first = module.build_sentence_caption_groups(spoken, timed)
    second = module.build_sentence_caption_groups(spoken, timed)

    assert len(first) >= 2
    assert [group.group_id for group in first] == [group.group_id for group in second]
    assert [tuple(word.text for word in group.words) for group in first] == [
        tuple(word.text for word in group.words) for group in second
    ]
    assert any(group.words[0].text in {"WHILE", "AND"} for group in first[1:])
    assert all(len(group.words) >= 2 for group in first)
    assert all(group.end_time - group.start_time >= 1.0 for group in first)
    assert all(
        len(module.wrap_caption(" ".join(word.text for word in group.words), module.CAPTION_MAX_CHARS))
        <= module.CAPTION_MAX_LINES
        for group in first
    )
    assert all(
        len(line.split()) >= 2
        for group in first
        for line in module.wrap_caption(
            " ".join(word.text for word in group.words), module.CAPTION_MAX_CHARS
        )
        if len(module.wrap_caption(" ".join(word.text for word in group.words), module.CAPTION_MAX_CHARS)) > 1
    )


def test_sentence_ass_has_hard_two_line_default_and_phone_readable_style():
    from app.services.render import build_sentence_karaoke_ass

    module = _preview_module()
    group = module.KaraokeSentenceGroup(
        "readable",
        tuple(
            module.KaraokeWord(text, index * 0.4, (index + 1) * 0.4)
            for index, text in enumerate(
                ("THE", "BATTLEFIELD", "BREAKS", "WHILE", "THE", "PAIR", "ESCAPES")
            )
        ),
        0.0,
        2.9,
    )
    ass = build_sentence_karaoke_ass([group], 1080, 1920)
    style = next(line for line in ass.splitlines() if line.startswith("Style: Caption,"))
    fields = style.split(",")
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]

    assert int(fields[2]) >= 72
    assert int(fields[19]) >= 100
    assert int(fields[20]) >= 100
    assert all(line.count("\\N") <= 1 for line in dialogues)
    assert "WrapStyle: 2" in ass


def test_sentence_ass_default_rejects_any_three_line_group():
    from app.services.render import RenderError, build_sentence_karaoke_ass

    module = _preview_module()
    words = tuple(
        module.KaraokeWord(f"WORD{index}", index * 0.2, (index + 1) * 0.2)
        for index in range(20)
    )
    group = module.KaraokeSentenceGroup("default-overflow", words, 0.0, 4.0)

    with pytest.raises(RenderError, match="subtitle overflow"):
        build_sentence_karaoke_ass([group], 1080, 1920, max_chars=20)
