import pytest

from quality.literal_text import UnreadablePage, count_literal_texts

RAW_TEXT_ELEMENTS = ["script", "style", "textarea", "title", "iframe", "xmp", "noembed", "noframes"]
COMPONENT_NAMES = ["title", "textarea", "iframe", "xmp", "noembed", "noframes"]


@pytest.mark.parametrize(
    ("page", "count"),
    [
        pytest.param(
            "<script>const a = 'Hello world';</script><style>.a { content: 'x'; }</style>"
            "<p class=\"{kind}\">{words.title}</p><p>{#if ok}{:else}{/if}{@render row()}</p>"
            "<p>{count + 1}</p><p>{ok ? 'Saved' : label}</p>",
            0,
            id="expressions-blocks-script-and-style-pass",
        ),
        pytest.param("<h1>Log in</h1>", 1, id="literal-text-node-is-flagged"),
        pytest.param("<h1>{'Log in'}</h1>", 1, id="a-hole-holding-only-a-string-literal-is-flagged"),
        pytest.param("<h1>{`Log in`}</h1>", 1, id="a-template-literal-without-substitution-is-flagged"),
        pytest.param("<h1>{`Hello ${name}`}</h1>", 0, id="a-template-literal-with-a-substitution-passes"),
        pytest.param("<img alt=Logo>", 1, id="unquoted-literal-attribute-value-is-flagged-whole"),
        pytest.param("<img alt={'Company logo'}>", 1, id="a-hole-attribute-value-with-a-literal-is-flagged"),
        pytest.param('<Dialog title="Confirm"></Dialog>', 1, id="literal-flagged-attribute-on-a-component-is-flagged"),
        pytest.param('<title>Home</title>', 1, id="literal-page-title-is-flagged"),
        pytest.param('<input type="submit" value="Go">', 1, id="a-submit-input-value-is-flagged"),
        pytest.param('<input type="button" value="Go">', 1, id="a-button-input-value-is-flagged"),
        pytest.param('<input type="reset" value="Go">', 1, id="a-reset-input-value-is-flagged"),
        pytest.param('<button value="Go">{words.go}</button>', 1, id="a-button-without-a-type-is-a-submit-and-its-value-is-flagged"),
        pytest.param('<button type="submit" value="Go"></button>', 1, id="a-submit-button-value-is-flagged"),
        pytest.param('<input value="typed">', 0, id="a-plain-input-value-is-data-and-passes"),
        pytest.param('<button type="button" value="Go"></button>', 0, id="a-typed-button-value-passes"),
        pytest.param('<div label="Name"></div>', 1, id="label-is-flagged"),
        pytest.param('<div aria-description="Name"></div>', 1, id="aria-description-is-flagged"),
        pytest.param('<div aria-roledescription="Name"></div>', 1, id="aria-roledescription-is-flagged"),
        pytest.param('<div aria-valuetext="Name"></div>', 1, id="aria-valuetext-is-flagged"),
        pytest.param('<div title="Name" placeholder="Search" aria-label="Close" alt="x y"></div>', 4, id="every-listed-attribute-counts-once"),
        pytest.param('<meta name="description" content="A page">', 1, id="a-meta-description-is-flagged"),
        pytest.param('<meta name="keywords" content="x">', 0, id="a-meta-keywords-content-is-not-counted"),
        pytest.param('<p class="big" href="/a" id="x">{n}</p><b>42</b><i> -- </i><u>  </u>', 0, id="markup-digits-and-unflagged-attributes-pass"),
        pytest.param('<p title="2024">12:30</p>', 0, id="content-with-no-letter-is-not-counted"),
        pytest.param("<p>One</p><p>Two</p><p>Three words in one text</p>", 3, id="a-text-node-counts-once-not-per-word"),
        pytest.param('<p title="{n} items"></p>', 1, id="a-literal-piece-next-to-a-hole-in-an-attribute-counts"),
        pytest.param("<p>a < b and c</p>", 2, id="a-bare-less-than-splits-a-text-in-two"),
        pytest.param("", 0, id="an-empty-page-has-no-text"),
    ],
)
def test_the_reference_cases_give_the_same_counts(page, count):
    """LITERAL-2, LITERAL-3, LITERAL-4: the cases of presentator's catalog purity check count as it reports them."""
    assert count_literal_texts(page) == count


def test_a_literal_inside_a_larger_expression_is_not_counted():
    """LITERAL-3: only a hole that is one string literal counts."""
    assert count_literal_texts("<p>{ok ? 'Saved' : x}</p>") == 0


def test_a_component_prop_outside_the_attribute_list_is_not_counted():
    """LITERAL-2: only the listed attributes are read, on components too."""
    assert count_literal_texts('<Hero heading="Welcome" />') == 0


def test_a_string_in_a_script_block_is_not_counted():
    """LITERAL-3: the body of a script is code."""
    assert count_literal_texts("<script>toast('Saved today');</script>") == 0


def test_a_capitalised_prop_name_is_read_as_the_lowercase_attribute():
    """LITERAL-2: the parser lowercases names, so a component's `Label` is the attribute `label`."""
    assert count_literal_texts('<Field Label="Name" />') == 1


def test_an_html_hole_is_not_counted():
    """LITERAL-3: a hole starting with `@` renders nothing the scan sees."""
    assert count_literal_texts("<p>{@html '<b>Hello</b>'}</p>") == 0


def test_an_apostrophe_in_a_hole_comment_swallows_the_markup_to_the_next_quote():
    """LITERAL-3: an unpaired quote in a hole reads as a string up to the next quote, so the markup between is missed."""
    assert count_literal_texts("<p>{x // don't}</p><b>Real text</b><i>{y // it's}</i>") == 0


def test_a_single_apostrophe_in_a_hole_comment_is_an_unclosed_hole():
    """LITERAL-9: with one quote only, the string never ends and the hole is never closed."""
    with pytest.raises(UnreadablePage, match="{ that is never closed"):
        count_literal_texts("<p>{x // don't}</p><b>Real text</b>")


def test_an_unbalanced_brace_in_an_html_comment_is_an_unclosed_hole():
    """LITERAL-9: the fold reads a `{` in a comment as a hole."""
    with pytest.raises(UnreadablePage, match="{ that is never closed"):
        count_literal_texts("<!-- a { b --><p>Text</p>")


@pytest.mark.parametrize("name", COMPONENT_NAMES)
def test_a_component_named_like_a_raw_text_element_reads_its_child_markup_as_one_text(name):
    """LITERAL-2: the parser reads everything up to the end tag of these names as one text, whatever the case."""
    assert count_literal_texts(f"<{name.capitalize()}><b>One</b><i>Two</i></{name.capitalize()}>") == 1


@pytest.mark.parametrize("name", COMPONENT_NAMES)
def test_a_self_closing_component_named_like_a_raw_text_element_reads_no_child_text(name):
    """LITERAL-2: a self-closing one holds no child, and the markup after it is read as ever."""
    assert count_literal_texts(f"<{name.capitalize()} /><p>Hello</p>") == 1


@pytest.mark.parametrize("name", RAW_TEXT_ELEMENTS)
def test_an_unclosed_raw_text_element_is_an_unreadable_page(name):
    """LITERAL-9: the parser reads the rest of the page as the element's text, so a miss would be silent."""
    with pytest.raises(UnreadablePage, match=f"has a <{name}> that is opened and never closed"):
        count_literal_texts(f"<{name}>\n<p>Hello</p>")


def test_an_unclosed_hole_is_named_before_an_unclosed_element():
    """LITERAL-9: the fold runs before the parser."""
    with pytest.raises(UnreadablePage, match="{ that is never closed"):
        count_literal_texts("<script>\n<p>{ never")
