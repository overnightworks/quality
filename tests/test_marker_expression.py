import itertools

import pytest
from _pytest.mark.expression import Expression as PytestExpression

from quality.marker_expression import MarkerExpressionError, parse

MARKERS = ("a", "b", "c", "live")
ACCEPTED = [
    pytest.param("", id="empty"),
    pytest.param("  ", id="blank"),
    pytest.param("live", id="one name"),
    pytest.param("not live", id="negation"),
    pytest.param("not not a", id="double negation"),
    pytest.param("a and b or c", id="and binds tighter than or"),
    pytest.param("a or b and c", id="or before and"),
    pytest.param("not (a or b)", id="negated group"),
    pytest.param("a and (b or not c)", id="nested group"),
    pytest.param("(a)", id="parenthesised name"),
    pytest.param("a-b.c[1]:/x+y", id="punctuation inside a name"),
    pytest.param("android and notlive", id="keywords only as whole words"),
    pytest.param("live(site='x', retries=3, flag=True, none=None, down=-1)", id="keyword arguments"),
    pytest.param("not live(site='x') and a", id="keyword arguments in a conjunction"),
    pytest.param("\tlive\tand\ta", id="tabs"),
]
REJECTED = [
    pytest.param("a and", id="and without right side"),
    pytest.param("(a", id="open group"),
    pytest.param("a)", id="closed group without opening"),
    pytest.param("and a", id="leading and"),
    pytest.param("a b", id="two names"),
    pytest.param("not", id="lone not"),
    pytest.param("a or or b", id="two ors"),
    pytest.param("a(", id="open call"),
    pytest.param("a()", id="call without arguments"),
    pytest.param("a(x)", id="argument without value"),
    pytest.param("a(x=)", id="argument without value after equals"),
    pytest.param("a(1=2)", id="argument name no identifier"),
    pytest.param("a(class=1)", id="argument name a keyword"),
    pytest.param("a(x=b)", id="argument value a name"),
    pytest.param("a(x='open)", id="unterminated string"),
    pytest.param('a(x="b\\c")', id="escape in string"),
    pytest.param("'a'", id="string outside arguments"),
    pytest.param("a $b", id="dollar"),
    pytest.param("a ! b", id="bang"),
    pytest.param("a=b", id="equals outside arguments"),
    pytest.param("a,b", id="comma outside arguments"),
    pytest.param("${{ matrix.m }}", id="workflow expression"),
]


def pytest_accepts(text: str) -> bool:
    try:
        PytestExpression.compile(text)
    except SyntaxError:
        return False
    return True


def reader_accepts(text: str) -> bool:
    try:
        parse(text)
    except MarkerExpressionError:
        return False
    return True


@pytest.mark.parametrize("text", [*ACCEPTED, *REJECTED])
def test_the_reader_accepts_what_pytests_grammar_accepts_and_refuses_the_rest(text):
    """LIVE-3: the reader agrees with pytest's own grammar on which expressions are expressions."""
    assert reader_accepts(text) == pytest_accepts(text)


@pytest.mark.parametrize("text", ACCEPTED)
def test_the_reader_selects_the_same_marker_sets_as_pytest(text):
    """LIVE-3: for every set of markers a test can carry, the reader and pytest select the same tests."""
    reference = PytestExpression.compile(text)
    expression = parse(text)
    for size in range(len(MARKERS) + 1):
        for marked in itertools.combinations(MARKERS, size):
            assert expression.holds(marked) == reference.evaluate(matcher_of(marked)), marked


def matcher_of(marked: tuple[str, ...]):
    """The matcher pytest takes: a name matches when the test carries it, and a name with arguments never does."""

    def matches(name: str, /, **arguments: object) -> bool:
        return name in marked and not arguments

    return matches


@pytest.mark.parametrize(
    ("text", "selects_live"),
    [
        pytest.param("live", True, id="the name"),
        pytest.param("not live", False, id="negated"),
        pytest.param("e2e and not live", False, id="narrowed"),
        pytest.param("not (live or e2e)", False, id="negated group"),
        pytest.param("e2e", True, id="another name"),
        pytest.param("not e2e", True, id="negated another name"),
        pytest.param("live or x", True, id="or another name"),
        pytest.param("live and not live", False, id="contradiction"),
        pytest.param("x or not x", True, id="tautology"),
        pytest.param("not live and x", False, id="negation and a name"),
        pytest.param("live(site='x')", True, id="call with arguments is another name"),
        pytest.param("not live(site='x')", True, id="negated call with arguments is another name"),
        pytest.param("", False, id="empty selects nothing"),
    ],
)
def test_a_test_marked_live_can_be_selected_by_an_expression_that_is_not_false_for_it(text, selects_live):
    """LIVE-3: an expression can select the marker when some set of other markers makes it true beside it."""
    assert parse(text).can_select("live") is selects_live


def test_an_expression_nested_past_the_recursion_limit_is_refused():
    """LIVE-5: a text the reader cannot follow is no expression."""
    with pytest.raises(MarkerExpressionError):
        parse("(" * 5000 + "a" + ")" * 5000)
