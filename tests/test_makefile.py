import pytest

from quality.makefile import RecipeLine, make_goals, reached_recipe_lines

CHECK = "uv run design-catalogs --check"
COVERED = "\n".join(
    [
        ".PHONY: lint wire-check words-check",
        "lint: wire-check words-check",
        "\t$(MAKE) wire-check",
        "\t$(MAKE) words-check",
        "wire-check:",
        "\techo wired",
        "words-check:",
        f"\t{CHECK}",
    ]
)


def makefile(*lines: str) -> str:
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    ("command", "goals"),
    [
        pytest.param(("make", "lint"), ("lint",), id="one goal"),
        pytest.param(("make", "VAR=1", "lint"), ("lint",), id="a variable before the goal is skipped"),
        pytest.param(("make", "lint", "VAR=1"), ("lint",), id="a variable after the goal is skipped"),
        pytest.param(("/usr/bin/make", "lint"), ("lint",), id="the base name of a path"),
        pytest.param(("make", "lint", "words-check"), ("lint", "words-check"), id="two goals"),
        pytest.param(("make", "-j4", "lint"), (), id="a flag makes no call"),
        pytest.param(("make", "-C", "sub", "lint"), (), id="make -C makes no call"),
        pytest.param(("make",), (), id="bare make has no goal"),
        pytest.param(("make", "$(T)"), (), id="a word with a dollar sign makes no call"),
        pytest.param(("make", "a=b"), (), id="only a variable, no goal"),
        pytest.param(("make", "lint", "$X"), (), id="a dollar word after a goal makes no call"),
        pytest.param(("uv", "run", "make", "lint"), (), id="a runner form is no call of make"),
    ],
)
def test_make_goals_are_the_goal_words_of_a_make_call(command, goals):
    """CATALOG-12: the goals of a make call by base name; a flag, a word with `$` or `=` that is no variable, or no goal makes no call."""
    assert make_goals(command) == goals


def test_a_rule_reached_through_a_prerequisite_yields_its_recipe_line_with_its_number():
    """CATALOG-14, CATALOG-15, CATALOG-18: the covered app's shape; `words-check` is a prerequisite of `lint`, so its recipe line
    is reached, and `$(MAKE)` lines are followed in the same file."""
    assert RecipeLine(CHECK, 8) in reached_recipe_lines(COVERED, ["lint"])


def test_a_multi_name_rule_gives_its_recipe_to_each_name():
    """CATALOG-14: each name before the colon has the same prerequisites and recipe."""
    text = makefile("wire-check words-check:", f"\t{CHECK}")
    assert reached_recipe_lines(text, ["words-check"]) == (RecipeLine(CHECK, 2),)


def test_an_order_only_prerequisite_is_a_prerequisite_as_any_other():
    """CATALOG-14: a word after `|` is a prerequisite; the recipe of the goal is still read."""
    text = makefile("words-check: | prep", f"\t{CHECK}")
    assert reached_recipe_lines(text, ["words-check"]) == (RecipeLine(CHECK, 2),)


def test_a_prefix_of_at_plus_or_blanks_is_dropped_from_the_recipe_line():
    """CATALOG-17: `@`, `+` and blanks before the command are dropped."""
    text = makefile("words-check:", f"\t@ +{CHECK}")
    assert reached_recipe_lines(text, ["words-check"]) == (RecipeLine(CHECK, 2),)


def test_blank_lines_and_comment_lines_between_recipe_lines_are_skipped():
    """CATALOG-15: a blank line or a line whose first character is `#` between recipe lines is skipped."""
    text = makefile("words-check:", "", "# a note", f"\t{CHECK}")
    assert reached_recipe_lines(text, ["words-check"]) == (RecipeLine(CHECK, 4),)


def test_a_trailing_backslash_joins_the_next_line_into_one_recipe_line():
    """CATALOG-15: the joined recipe line carries the number of its first physical line."""
    text = makefile("words-check:", "\tuv run \\", "\tdesign-catalogs --check")
    ((line),) = reached_recipe_lines(text, ["words-check"])
    assert line.number == 2
    assert "design-catalogs --check" in line.text


def test_a_line_that_ends_the_rule_stops_its_recipe():
    """CATALOG-15: any other line ends the rule; a later tab line is no recipe line of it."""
    text = makefile("words-check:", f"\t{CHECK}", "FOO = 1", "\techo x")
    assert reached_recipe_lines(text, ["words-check"]) == (RecipeLine(CHECK, 2),)


@pytest.mark.parametrize(
    ("text", "goal", "lines"),
    [
        pytest.param(
            makefile("define-x:", "lint:", f"\t{CHECK}"),
            "lint",
            (RecipeLine(CHECK, 3),),
            id="a rule whose name starts with define",
        ),
        pytest.param(
            makefile("ifdef_x = 1", "lint:", f"\t{CHECK}"),
            "lint",
            (RecipeLine(CHECK, 3),),
            id="a variable whose name starts with ifdef",
        ),
        pytest.param(
            makefile("endif-x:", f"\t{CHECK}"),
            "endif-x",
            (RecipeLine(CHECK, 2),),
            id="a rule whose name starts with endif",
        ),
    ],
)
def test_a_name_that_starts_with_a_directive_word_is_no_directive(text, goal, lines):
    """CATALOG-14, CATALOG-16: a directive is its keyword as a word of its own; a rule or an assignment whose name begins with
    one is read as the rule or the assignment it is."""
    assert reached_recipe_lines(text, [goal]) == lines


@pytest.mark.parametrize(
    ("text", "goal", "lines"),
    [
        pytest.param(
            makefile("words-check:", "\techo $$HOME"),
            "words-check",
            (RecipeLine("echo $HOME", 2),),
            id="a double dollar becomes one dollar",
        ),
        pytest.param(
            makefile("lint:", "\t${MAKE} words-check", "words-check:", f"\t{CHECK}"),
            "lint",
            (RecipeLine("make words-check", 2), RecipeLine(CHECK, 4)),
            id="a braced MAKE becomes make and its goal is followed",
        ),
        pytest.param(
            makefile("lint:", "\techo $$(MAKE) words-check", "words-check:", f"\t{CHECK}"),
            "lint",
            (RecipeLine("echo $(MAKE) words-check", 2),),
            id="a double dollar before MAKE stays text and its goal is not followed",
        ),
    ],
)
def test_make_and_dollar_substitutions_read_as_the_manual_gives_them(text, goal, lines):
    """CATALOG-17, CATALOG-18: `$$` becomes `$`, `${MAKE}` becomes `make` and is followed as a make call, and `$$(MAKE)` is
    the text `$(MAKE)`, which is no make call."""
    assert reached_recipe_lines(text, [goal]) == lines


@pytest.mark.parametrize(
    ("text", "goals"),
    [
        pytest.param(COVERED, ["nope"], id="a goal the file does not define"),
        pytest.param(
            makefile("ifeq (1,1)", "x:", "endif-x:", "\techo", "lint:", f"\t{CHECK}", "endif"),
            ["lint"],
            id="a rule whose name starts with endif does not end the conditional",
        ),
        pytest.param(makefile("words-check:", f"\t-{CHECK}"), ["words-check"], id="a minus prefix ignores the failure"),
        pytest.param(
            makefile("ifeq (1,1)", "words-check:", f"\t{CHECK}", "endif"),
            ["words-check"],
            id="a rule inside a conditional",
        ),
        pytest.param(
            makefile("ifeq (1,1)", "words-check:", "\techo a", "else", "words-check:", f"\t{CHECK}", "endif"),
            ["words-check"],
            id="both branches of a conditional",
        ),
        pytest.param(
            makefile("define X", "words-check:", f"\t{CHECK}", "endef"),
            ["words-check"],
            id="a define block",
        ),
        pytest.param(
            makefile("words-check:", "\techo a", "words-check:", f"\t{CHECK}"),
            ["words-check"],
            id="a second recipe for one target",
        ),
        pytest.param(
            makefile("words-check:", f"\t{CHECK}", "words-check:", "\techo a"),
            ["words-check"],
            id="a second recipe after the first",
        ),
        pytest.param(makefile("words-check::", f"\t{CHECK}"), ["words-check"], id="a double-colon rule"),
        pytest.param(makefile("%-check:", f"\t{CHECK}"), ["words-check"], id="a pattern rule"),
        pytest.param(makefile("words-check: ; " + CHECK), ["words-check"], id="a recipe after a semicolon"),
        pytest.param(
            makefile("words-check: X=1", f"\t{CHECK}"),
            ["words-check"],
            id="a target-specific assignment is no rule",
        ),
        pytest.param(makefile(".IGNORE:", COVERED), ["lint"], id="the .IGNORE target"),
        pytest.param(makefile(".RECIPEPREFIX = >", COVERED), ["lint"], id="the .RECIPEPREFIX variable"),
        pytest.param(makefile(".ONESHELL:", COVERED), ["lint"], id="the .ONESHELL target"),
        pytest.param(makefile("include x.mk", COVERED), ["lint"], id="an include line"),
        pytest.param(makefile("-include x.mk", COVERED), ["lint"], id="a -include line"),
        pytest.param(makefile("sinclude x.mk", COVERED), ["lint"], id="a sinclude line"),
        pytest.param(makefile("MAKEFLAGS += -i", COVERED), ["lint"], id="an assignment to MAKEFLAGS"),
        pytest.param(makefile("export MAKEFLAGS = -i", COVERED), ["lint"], id="an export of MAKEFLAGS"),
        pytest.param(makefile("override MAKEFLAGS += -i", COVERED), ["lint"], id="an override of MAKEFLAGS"),
        pytest.param(makefile("private MAKEFLAGS := -i", COVERED), ["lint"], id="a private assignment to MAKEFLAGS"),
        pytest.param(makefile("MAKEFLAGS ::= -i", COVERED), ["lint"], id="a double-colon assignment to MAKEFLAGS"),
    ],
)
def test_each_gap_reads_as_no_call(text, goals):
    """CATALOG-12, CATALOG-14, CATALOG-16, CATALOG-17, CATALOG-19, CATALOG-20, CATALOG-22: an undefined goal, a line the rule
    does not follow, a `-` prefixed line, a whole file with a directive that the scan cannot see through; each reads with no
    recipe line that is the check."""
    assert CHECK not in [line.text for line in reached_recipe_lines(text, goals)]


def test_a_cycle_of_make_calls_ends_where_it_closes():
    """CATALOG-18: each target is followed once, so `a` and `b` calling each other return."""
    text = makefile("a: b", "\t$(MAKE) b", "b: a", "\t$(MAKE) a")
    assert CHECK not in [line.text for line in reached_recipe_lines(text, ["a"])]
