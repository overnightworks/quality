import pytest

from quality.report import (
    CannotJudge,
    Choice,
    Command,
    Example,
    Finding,
    Instruction,
    Note,
    NoteEvent,
    OneOf,
    Reason,
    Unparsable,
    printable,
    render,
    shell_word,
)


def test_an_event_less_finding_prints_its_headline_and_fix_escaped():
    """#97 rule 2: every field of a finding, fix included, prints without a raw control character."""
    finding = Finding("r", "p", None, "why\x1b", "fix\x1b[31m\n::error::f")
    assert render(finding) == "r p - why\\x1b\n  Fix: fix\\x1b[31m\\x0a::error::f"


@pytest.mark.parametrize(
    "name",
    ["plain.env", "umlaut-\u00e9.env", "-dash.env", "a:b.env", "*.env", "50%.env", "back\\slash.env", "=equals.env",
     "nbsp\u00a0.env", "e\u0301.env", "smile\U0001f600.env", "\u540d\u524d.env", "\u05e9\u05dd.env", "back\\u202e.env"],
)
def test_a_name_printable_unchanged_is_a_shell_word_and_printable_is_idempotent(name):
    """#97 rule 1, cell 17: a word exactly when `printable(x) == x`."""
    assert printable(name) == name
    assert shell_word(name) is not None


@pytest.mark.parametrize(
    "name", ["lat\udce9.env", "a\nb", "esc\x1b", "c1\x85", "del\x7f", "tab\t", "cr\r", "nul\0",
     "rlo\u202e", "lri\u2066", "rlm\u200f", "alm\u061c", "zwsp\u200b", "zwj\u200d", "shy\u00ad", "bom\ufeff", "ls\u2028",
     "ps\u2029", "tag\U000e0041", "langtag\U000e0001"]
)
def test_a_name_printable_changes_has_no_shell_word_and_printing_it_twice_changes_nothing(name):
    """#97 rule 1, cell 17: no word where `printable(x) != x`; the escape is idempotent."""
    assert printable(name) != name
    assert shell_word(name) is None
    assert printable(printable(name)) == printable(name)


@pytest.mark.parametrize(
    ("character", "printed"),
    [
        ("\u202e", "\\u202e"), ("\u2066", "\\u2066"), ("\u200f", "\\u200f"), ("\u061c", "\\u061c"), ("\u200b", "\\u200b"),
        ("\u200d", "\\u200d"), ("\u00ad", "\\u00ad"), ("\ufeff", "\\ufeff"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029"),
        ("\U000e0041", "\\U000e0041"), ("\U000e0001", "\\U000e0001"),
    ],
)
def test_a_format_or_separator_character_prints_as_its_code_point(character, printed):
    """#228 rule 7: a format character, U+2028 and U+2029 print as `\\uNNNN` (`\\UNNNNNNNN` above U+FFFF), by category."""
    assert printable(character) == printed


def test_a_refusal_without_steps_prints_its_fact_and_carries_it_as_a_reason():
    refusal = CannotJudge("no scanner")
    assert (str(refusal), refusal.reason) == ("no scanner", Reason("no scanner"))


def test_a_refusal_with_steps_prints_the_first_after_next_and_the_others_after_then():
    steps = (Instruction("install mise"), Command("mise install"), Command("run it", "in a shell"))
    refusal = CannotJudge("no scanner", steps)
    assert (str(refusal), refusal.reason) == ("no scanner; next: install mise; then: mise install; then: in a shell: run it", Reason("no scanner", steps))


def test_a_subclass_built_with_one_message_keeps_both_its_label_and_its_message():
    refusal = Unparsable("bad file")
    assert (refusal.label, str(refusal)) == ("unparsable", "bad file")


def test_alternative_commands_print_their_labels_and_commands_on_one_line():
    choice = OneOf("reinstall", (Choice("one", "here"), Choice("two", "there")))
    assert str(choice) == "reinstall, one of: here: one or there: two"


def test_an_example_holds_its_label_and_lines_and_equal_examples_hash_alike():
    example = Example("write it in a file like this", ("[table]", "key = 1"))
    same = Example("write it in a file like this", ("[table]", "key = 1"))
    assert (example.label, example.lines, example == same, hash(example) == hash(same)) == (
        "write it in a file like this", ("[table]", "key = 1"), True, True,
    )


@pytest.mark.parametrize(("line", "expected"), [(None, "a\\x1bb.txt"), (7, "a\\x1bb.txt:7")], ids=["no-line", "with-line"])
def test_a_note_locates_itself_by_its_escaped_path_and_its_line_where_it_has_one(line, expected):
    assert Note("a\x1bb.txt", NoteEvent.ALLOWED, "a fact", line).location == expected
