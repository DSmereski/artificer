"""Unit tests for gateway.conversation_markers."""

from __future__ import annotations

from gateway.conversation_markers import (
    confirmation_no,
    confirmation_yes,
    parse_remember,
    sanitize_hive_reply,
    scan,
    strip_markers,
)


# ---------------------------------------------------------------- strip_markers


def test_strip_markers_empty():
    assert strip_markers("") == ""
    assert strip_markers("just plain text") == "just plain text"


def test_strip_markers_removes_generate_image():
    text = "Sure! Here you go.\n[GENERATE_IMAGE] a dragon"
    assert strip_markers(text) == "Sure! Here you go."


def test_strip_markers_removes_all_known_kinds():
    text = (
        "Heya.\n"
        "[ASK_USER] what colour skin?\n"
        "[VAULT_LOOKUP] night elf details\n"
        "[REMEMBER] {\"category\":\"x\",\"title\":\"t\",\"body\":\"b\"}\n"
        "[CONFIRM_IMAGE] {\"prompt\":\"x\"}\n"
        "[GENERATE_IMAGE] something\n"
    )
    assert strip_markers(text) == "Heya."


def test_strip_markers_preserves_brackets_in_text():
    text = "I told her [things] then said:\n[GENERATE_IMAGE] foo"
    assert strip_markers(text) == "I told her [things] then said:"


# ---------------------------------------------------------------- scan: ask_user


def test_scan_ask_user():
    hits = scan("ok\n[ASK_USER] what hair colour?")
    assert hits.ask_user is not None
    assert hits.ask_user.question == "what hair colour?"
    assert hits.ask_user.options == []
    assert hits.confirm_image is None
    assert hits.generate_image is None


def test_scan_ask_user_caps_long_question():
    long_q = "x" * 1000
    hits = scan(f"[ASK_USER] {long_q}")
    assert hits.ask_user is not None
    assert len(hits.ask_user.question) <= 500


def test_scan_ask_user_with_options():
    raw = '[ASK_USER] {"question":"What hair colour?","options":["jet black","silver","violet","let me describe it"]}'
    hits = scan(raw)
    assert hits.ask_user is not None
    assert hits.ask_user.question == "What hair colour?"
    assert hits.ask_user.options == ["jet black", "silver", "violet", "let me describe it"]


def test_scan_ask_user_caps_option_count():
    opts = [f"opt-{i}" for i in range(20)]
    raw = '[ASK_USER] {"question":"Q","options":' + str(opts).replace("'", '"') + '}'
    hits = scan(raw)
    assert hits.ask_user is not None
    assert len(hits.ask_user.options) <= 8


def test_scan_ask_user_drops_overlong_option():
    raw = '[ASK_USER] {"question":"Q","options":["short","' + "x" * 200 + '","also short"]}'
    hits = scan(raw)
    assert hits.ask_user is not None
    assert hits.ask_user.options == ["short", "also short"]


def test_scan_ask_user_json_falls_back_when_question_missing():
    # No question field at all → should be treated as no valid ask.
    raw = '[ASK_USER] {"options":["a","b"]}'
    hits = scan(raw)
    assert hits.ask_user is None


# ---------------------------------------------------------------- scan: image markers


def test_scan_generate_image_plain_text():
    hits = scan("[GENERATE_IMAGE] a dragon in the sky")
    assert hits.generate_image == {"prompt": "a dragon in the sky"}


def test_scan_generate_image_json():
    raw = '[GENERATE_IMAGE] {"prompt":"x","aspect":"portrait","loras":["A"]}'
    hits = scan(raw)
    assert hits.generate_image["aspect"] == "portrait"
    assert hits.generate_image["loras"] == ["A"]


def test_scan_confirm_image_json():
    raw = '[CONFIRM_IMAGE] {"prompt":"x","aspect":"square"}'
    hits = scan(raw)
    assert hits.confirm_image["aspect"] == "square"
    assert hits.generate_image is None


def test_scan_confirm_image_multiline_json():
    """Regression: pretty-printed JSON across multiple lines must parse.

    qwen-hive sometimes emits JSON with newlines and indentation. The old
    regex matched lazy-non-greedy up to `\\n` and captured nothing.
    """
    raw = (
        "Going to render this:\n"
        "[CONFIRM_IMAGE] {\n"
        '  "prompt": "Hive in a black silk dress",\n'
        '  "aspect": "portrait",\n'
        '  "loras": ["Real Beauty"]\n'
        "}\n"
        "Looks good?"
    )
    hits = scan(raw)
    assert hits.confirm_image is not None
    assert hits.confirm_image["aspect"] == "portrait"
    assert hits.confirm_image["loras"] == ["Real Beauty"]


def test_scan_generate_image_multiline_json():
    raw = (
        "[GENERATE_IMAGE] {\n"
        '  "prompt": "a dragon",\n'
        '  "aspect": "ultrawide"\n'
        "}"
    )
    hits = scan(raw)
    assert hits.generate_image is not None
    assert hits.generate_image["aspect"] == "ultrawide"


def test_strip_markers_removes_multiline_json():
    raw = (
        "Going to render this:\n"
        "[CONFIRM_IMAGE] {\n"
        '  "prompt": "x"\n'
        "}\n"
        "looks good?"
    )
    out = strip_markers(raw)
    assert "Going to render this:" in out
    assert "looks good?" in out
    assert "CONFIRM_IMAGE" not in out
    assert "{" not in out  # balanced JSON should be gone


def test_extract_handles_strings_with_braces_in_json():
    raw = '[CONFIRM_IMAGE] {"prompt":"like { this } in a string","aspect":"square"}'
    hits = scan(raw)
    assert hits.confirm_image is not None
    assert hits.confirm_image["aspect"] == "square"


# ---------------------------------------------------------------- scan: remember


def test_parse_remember_valid():
    raw = '{"category":"people","title":"penguin","body":"green eyes"}'
    out = parse_remember(raw)
    assert out["category"] == "people"
    assert out["title"] == "penguin"
    assert out["body"] == "green eyes"
    assert out["audience"] == ["hive", "claude-code"]  # default


def test_parse_remember_with_audience():
    raw = '{"category":"x","title":"y","body":"z","audience":["hive"]}'
    out = parse_remember(raw)
    assert out["audience"] == ["hive"]


def test_parse_remember_missing_required_returns_none():
    assert parse_remember('{"category":"x","title":"y"}') is None
    assert parse_remember('{"title":"y","body":"z"}') is None


def test_parse_remember_too_long_returns_none():
    raw = '{"category":"x","title":"' + "y" * 300 + '","body":"z"}'
    assert parse_remember(raw) is None


def test_parse_remember_malformed_returns_none():
    assert parse_remember("not json") is None
    assert parse_remember('{"x":1') is None


def test_scan_remember_via_marker():
    raw = '[REMEMBER] {"category":"people","title":"t","body":"b"}'
    hits = scan(raw)
    assert hits.remember is not None
    assert hits.remember["title"] == "t"


# ---------------------------------------------------------------- scan: vault_lookup


def test_scan_vault_lookup():
    hits = scan("[VAULT_LOOKUP] sylvanas appearance")
    assert hits.vault_lookup == "sylvanas appearance"


def test_scan_vault_lookup_caps_query():
    hits = scan("[VAULT_LOOKUP] " + "x" * 500)
    assert hits.vault_lookup is not None
    assert len(hits.vault_lookup) <= 300


# ---------------------------------------------------------------- mixed turns


def test_scan_handles_multiple_markers_in_one_reply():
    raw = (
        "Looking that up.\n"
        "[VAULT_LOOKUP] night elf colours\n"
        "[REMEMBER] {\"category\":\"meta\",\"title\":\"t\",\"body\":\"b\"}\n"
    )
    hits = scan(raw)
    assert hits.vault_lookup == "night elf colours"
    assert hits.remember is not None
    assert hits.generate_image is None


# ---------------------------------------------------------------- confirmations


def test_confirmation_yes():
    for token in ["yes", "Yes", "y", "yeah", "yep", "go", "do it",
                  "looks good", "yes please", "go for it"]:
        assert confirmation_yes(token), token


def test_confirmation_no():
    for token in ["no", "No", "n", "cancel", "nope", "stop", "abort"]:
        assert confirmation_no(token), token


def test_confirmation_neither():
    for token in ["maybe", "make it cooler", "the eyes should be green"]:
        assert not confirmation_yes(token), token
        assert not confirmation_no(token), token


# ---------------------------------------------------------------- sanitize_hive_reply


def test_sanitize_strips_status_lines():
    raw = (
        "Here you go.\n"
        "Status: Initializing render engine... 15% complete.\n"
        "Status: Loading high-res textures... 40% complete.\n"
        "Status: Finalizing... 100% complete.\n"
        "There she is."
    )
    out = sanitize_hive_reply(raw)
    assert "Status:" not in out
    assert "complete" not in out.lower()
    assert "Here you go." in out
    assert "There she is." in out


def test_sanitize_strips_initializing_prose():
    raw = "Sure!\nInitializing render engine...\nGenerating image now...\nDone."
    out = sanitize_hive_reply(raw)
    assert "Initializing" not in out
    assert "Generating" not in out
    assert "Sure!" in out
    assert "Done." in out


def test_sanitize_strips_image_requested_artifact():
    raw = "[Image requested: a sunset]\nReally rendering now."
    out = sanitize_hive_reply(raw)
    assert "Image requested" not in out
    assert "Really rendering now." in out


def test_sanitize_strips_marker_and_progress_together():
    raw = (
        "Sure thing.\n"
        "[GENERATE_IMAGE] cat in a box\n"
        "Status: 50% complete.\n"
        "Done!"
    )
    out = sanitize_hive_reply(raw)
    assert "GENERATE_IMAGE" not in out
    assert "Status:" not in out
    assert "Sure thing." in out
    assert "Done!" in out


def test_sanitize_collapses_blank_lines():
    raw = "first\n\n\n\nsecond\n\n\n\nthird"
    out = sanitize_hive_reply(raw)
    # No more than one blank line between paragraphs.
    assert "\n\n\n" not in out


def test_sanitize_returns_empty_for_pure_marker_reply():
    raw = '[GENERATE_IMAGE] {"prompt":"x"}'
    out = sanitize_hive_reply(raw)
    assert out == ""


# ---------------------------------------------------------------- lenient implicit markers


def test_scan_implicit_ask_user_without_prefix():
    # Hive's small model sometimes drops the [ASK_USER] prefix and
    # emits raw JSON. The scanner falls back to treating it as an
    # implicit ASK_USER so chips still render.
    raw = '{"question":"What hair colour?","options":["black","silver","let me describe it"]}'
    hits = scan(raw)
    assert hits.ask_user is not None
    assert hits.ask_user.question == "What hair colour?"
    assert "black" in hits.ask_user.options


def test_scan_implicit_confirm_image_without_prefix():
    raw = '{"prompt":"a dragon in fog","aspect":"ultrawide"}'
    hits = scan(raw)
    assert hits.confirm_image is not None
    assert hits.confirm_image["aspect"] == "ultrawide"
    # Implicit detection should NOT also fire generate_image.
    assert hits.generate_image is None


def test_scan_explicit_marker_wins_over_lenient():
    # When BOTH a [GENERATE_IMAGE] marker AND naked JSON appear, the
    # explicit one wins; the lenient fallback only fires when nothing
    # matched.
    raw = (
        '[GENERATE_IMAGE] {"prompt":"explicit"}\n'
        '{"prompt":"would-be-implicit"}'
    )
    hits = scan(raw)
    assert hits.generate_image is not None
    assert hits.generate_image["prompt"] == "explicit"
    assert hits.confirm_image is None


def test_sanitize_strips_naked_json_payload():
    # Naked-JSON gets re-routed by scan() so the bubble must NOT show it.
    raw = '{"question":"Q?","options":["a","b"]}\n\nFollow-up text.'
    out = sanitize_hive_reply(raw)
    assert "{" not in out
    assert "Follow-up text." in out
