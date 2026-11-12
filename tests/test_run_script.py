import pytest

from quality.run_script import Tool, segments, tool_arguments

PYTEST = Tool(commands=frozenset({"pytest", "py.test"}), module="pytest")
PLAYWRIGHT = Tool(commands=frozenset({"playwright"}), module="playwright")


def opened(text: str) -> list[tuple[tuple[str, ...], str]]:
    return [(part.words, part.opened_by) for part in segments(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a b", [(("a", "b"), "")]),
        ("a | b", [(("a",), ""), (("b",), "|")]),
        ("a |& b", [(("a",), ""), (("b",), "|&")]),
        ("a && b", [(("a",), ""), (("b",), "&&")]),
        ("a || b", [(("a",), ""), (("b",), "||")]),
        ("a; b", [(("a",), ""), (("b",), ";")]),
        ("a & b", [(("a",), ""), (("b",), "&")]),
        ("a | b | c", [(("a",), ""), (("b",), "|"), (("c",), "|")]),
        ("x=$(a b)", [(("x=$",), ""), (("a", "b"), "(")]),
        ("(a; b)", [(("a",), "("), (("b",), ";")]),
    ],
    ids=[
        "the line's start", "pipe", "pipe with standard error", "and", "or", "semicolon", "background", "pipeline of three",
        "substitution", "group",
    ],
)
def test_a_segment_keeps_the_separator_that_opened_it(text, expected):
    """RUNTIME-2."""
    assert opened(text) == expected


def test_the_segments_of_a_shells_c_string_stand_after_the_shell_opened_by_the_flag():
    """RUNTIME-2, RUNTIME-3."""
    assert opened("bash -c 'a | b' && c") == [
        (("bash", "-c", "a | b"), ""),
        (("a",), "-c"),
        (("b",), "|"),
        (("c",), "&&"),
    ]


def test_a_c_string_with_a_substitution_is_read_whole():
    """RUNTIME-3: the string is split as a script of its own, not at its substitution."""
    assert opened('bash -c "apt-get install $(cat pkgs)"') == [
        (("bash", "-c", "apt-get install $(cat pkgs)"), ""),
        (("apt-get", "install", "$"), "-c"),
        (("cat", "pkgs"), "("),
    ]


def test_a_c_string_the_reader_cannot_split_is_refused():
    """RUNTIME-6."""
    with pytest.raises(ValueError, match="quotation"):
        segments("bash -c \"apt-get install 'x\"")


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (["pytest", "-q"], ("-q",)),
        (["py.test"], ()),
        (["./.venv/bin/pytest", "x"], ("x",)),
        (["python", "-m", "pytest", "-n", "2"], ("-n", "2")),
        (["python3.14", "-W", "error", "-m", "pytest"], ()),
        (["uv", "run", "--locked", "pytest", "-q"], ("-q",)),
        (["uv", "run", "python", "-m", "pytest"], ()),
        (["uvx", "pytest@8", "-q"], ("-q",)),
        (["npx", "pytest@8", "-q"], ("-q",)),
        (["npx", "--yes", "pytest", "-q"], ("-q",)),
        (["npx", "-p", "pkg", "pytest", "-q"], ("-q",)),
        (["bunx", "pytest", "-q"], ("-q",)),
        (["pnpm", "exec", "pytest", "-q"], ("-q",)),
        (["pnpm", "dlx", "pytest", "-q"], ("-q",)),
        (["npm", "exec", "pytest", "-q"], ("-q",)),
        (["yarn", "pytest", "-q"], ("-q",)),
        (["yarn", "dlx", "pytest", "-q"], None),
        (["yarn", "exec", "pytest", "-q"], None),
        (["pnpm", "pytest", "-q"], None),
        (["bun", "x", "pytest", "-q"], None),
        (["npx", "other"], None),
        (["uv", "run", "pytest@8"], None),
        (["pip", "install", "pytest"], None),
        (["pytest-cov"], None),
    ],
)
def test_the_arguments_of_a_pytest_call_follow_the_command_the_module_or_a_runner(words, expected):
    """RUNTIME-3: PYTEST-3's reading, shared."""
    assert tool_arguments(words, PYTEST) == expected


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (["playwright", "install", "--with-deps"], ("install", "--with-deps")),
        (["npx", "playwright", "install", "--with-deps", "chromium"], ("install", "--with-deps", "chromium")),
        (["npx", "playwright@1.49.0", "install", "--with-deps"], ("install", "--with-deps")),
        (["uvx", "playwright@1.49", "install", "--with-deps"], ("install", "--with-deps")),
        (["python", "-m", "playwright", "install-deps"], ("install-deps",)),
        (["uv", "run", "playwright", "install", "--with-deps"], ("install", "--with-deps")),
        (["pnpm", "exec", "playwright", "install"], ("install",)),
        (["pnpm", "dlx", "playwright@1", "install"], ("install",)),
        (["yarn", "playwright", "install", "--with-deps"], ("install", "--with-deps")),
        (["yarn", "dlx", "playwright", "install", "--with-deps"], None),
        (["pnpm", "playwright", "install", "--with-deps"], None),
        (["bun", "x", "playwright", "install", "--with-deps"], None),
        (["pytest", "-q"], None),
    ],
)
def test_the_arguments_of_a_playwright_call_follow_the_command_the_module_or_a_runner(words, expected):
    """RUNTIME-3."""
    assert tool_arguments(words, PLAYWRIGHT) == expected
