"""The functions lizard reads out of a script file: which suffix has a reader, how a function is named and what its
span and CCN are once a callback's branches are counted in the function that holds it."""

import pytest

from quality.baseline_file import BASELINE_PATH, Metric
from quality.ratchet import measure
from quality.ratchet_scripts import reads_functions
from quality.report import FindingEvent, NoteEvent
from quality.rules import Rule
from tests.support.check import check_output as run
from tests.support.check import refusal_row, rule_findings, rule_notes
from tests.support.door import FIX_WORDS, door_in
from tests.support.git_repositories import build_repo_with_source
from tests.support.source_code import typescript_function

EMPTY_BASELINE = ""


def reading(suffix: str, code: str) -> list[tuple[str, int, int, int]]:
    """Each function as the size rule keys it: symbol, first line, lines and CCN, in source order."""
    measurements = measure({f"src/file{suffix}": code.encode()}).measurements
    lengths = {key.symbol: measured for key, measured in measurements.items() if key.metric is Metric.FUNCTION_LINES}
    complexities = {key.symbol: measured.value for key, measured in measurements.items() if key.metric is Metric.CCN}
    return [(symbol, measured.line, measured.value, complexities[symbol]) for symbol, measured in lengths.items()]


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [(".ts", True), (".js", True), (".mjs", True), (".svelte", True), (".vue", True)]
    + [(suffix, False) for suffix in (".tsx", ".jsx", ".mts", ".cts", ".cjs", ".css", ".py", ".rs", "")],
)
def test_only_the_five_suffixes_of_the_contract_have_a_function_reader(suffix, expected):
    assert reads_functions(suffix) is expected


FIXED_DEFECTS = [
    pytest.param(
        "function a(): number {\n  return 1;\n}\nconst k = 2;\nfunction b() {\n  return 2;\n}\n",
        [("a", 1, 3, 1), ("b", 5, 3, 1)],
        id="a return type followed by a statement",
    ),
    pytest.param(
        "function first() {\n  wrap(() => {\n    for (const type of items) go();\n  });\n}\nfunction second() {\n  return 1;\n}\n",
        [("first", 1, 5, 2), ("second", 6, 3, 1)],
        id="the identifier type in a nested for-of",
    ),
]

OPEN_MISREADINGS = [
    pytest.param(
        "async function created(): Promise<void> {\n  open();\n  type('name', 'talks');\n  await done();\n}\nfunction next() {\n  return 1;\n}\n",
        [("created", 1, 3, 1), ("next", 6, 3, 1)],
        id="a statement calling a function named type ends the function there",
    ),
    pytest.param(
        "function read() {\n  const capturing = () => page.evaluate(() => (window as unknown as {\n    x: number\n  }).x)\n  return capturing\n}\n",
        [("read", 1, 6, 1), ("capturing", 2, 1, 1)],
        id="as with an object type over lines ends an expression-bodied arrow there",
    ),
    pytest.param(
        "const seated = (\n  first: number,\n  second: number,\n) => {\n  go(first, second);\n};\n",
        [("seated", 4, 3, 1)],
        id="an arrow whose parameters span lines starts at their last line",
    ),
    pytest.param(
        "export function compare<\n  T extends { id: string }\n>(a: T, b: T): number {\n  return a.id.localeCompare(b.id);\n}\n",
        [("compare", 3, 3, 1)],
        id="type parameters that span lines start at their last line",
    ),
    pytest.param(
        "const handlers = new Map<string, (frame: never) => void>();\n",
        [("handlers", 1, 1, 1)],
        id="a function type as a type argument reads as a function",
    ),
    pytest.param(
        "export function mount<P>(c: C<P>, p: P): R;\nexport function mount(c: C): R;\nexport function mount(c: C, p?: unknown): R {\n  return go(c, p);\n}\n",
        [("mount", 1, 1, 1), ("mount#2", 2, 1, 1), ("mount#3", 3, 3, 1)],
        id="each overload signature reads as a function of its own",
    ),
    pytest.param(
        "function table(has, c) {\n  return { playing: has\n    ? playing({ a: c.a ?? [], b: c.b ?? null })\n    : null };\n}\n",
        [("table", 1, 5, 2), ("playing", 3, 2, 3)],
        id="a call in a ternary branch reads as a function and takes its holder's branches",
    ),
    pytest.param(
        "function read() {\n  const channels = (color: string) => color.match(/[\\d.]+/g)!.slice(0, 3).map(Number) as [number, number, number]\n  return { track: channels(a), fault: b === null ? null : channels(b) }\n}\n",
        [("read", 1, 4, 1), ("channels", 2, 2, 2)],
        id="a regex literal before ! runs an expression body one statement on",
    ),
]

NAMED_ONLY = [
    pytest.param("describe('x', () => {\n  if (a && b && c) go();\n});\n", [], id="a callback no named function holds is no entry"),
    pytest.param("export default () => {\n  if (a) go();\n};\n", [], id="an anonymous default export is no entry"),
    pytest.param("const table = { 'quoted': () => 1 };\n", [], id="an arrow under a quoted key is no entry"),
    pytest.param(
        "describe('x', () => {\n  it('y', () => {\n    go();\n  });\n  function setup() {\n    if (a) go();\n  }\n});\n",
        [("setup", 5, 3, 2)],
        id="a named function inside a callback is its own entry",
    ),
]

CALLBACK_BRANCHES = [
    pytest.param(
        "function attach(items) {\n  items.forEach((item) => {\n    if (item.a && item.b) go(item);\n  });\n}\n",
        [("attach", 1, 5, 3)],
        id="a callback's branches count in its named holder",
    ),
    pytest.param(
        "function attach(items) {\n  items.forEach((a) => {\n    if (a.x || a.y) go();\n  });\n  items.map((b) => b.p ? b.q : b.r);\n}\n",
        [("attach", 1, 6, 4)],
        id="every callback of the holder adds its branches",
    ),
    pytest.param(
        "function attach(items) {\n  items.forEach((a) => {\n    if (a.x) go();\n  });\n  function inner() {\n    if (z) y();\n  }\n}\n",
        [("attach", 1, 8, 2), ("inner", 5, 3, 2)],
        id="a named function inside another adds nothing to the outer one and takes its own callbacks",
    ),
    pytest.param(
        "function outer() {\n  function inner() {\n    run(() => {\n      if (z) y();\n    });\n  }\n}\n",
        [("outer", 1, 7, 1), ("inner", 2, 5, 2)],
        id="a callback counts in the smallest named function holding it",
    ),
    pytest.param(
        "const speakerFrom = (from: number) => (frame: number) => (frame >= from ? 1 : null);\n",
        [("speakerFrom", 1, 1, 2)],
        id="a curried arrow carries the inner function's branches in the outer name",
    ),
]

KEYS = [
    pytest.param(
        "class A {\n  render() {\n    return 1;\n  }\n}\nclass B {\n  render() {\n    return 2;\n  }\n}\n",
        [("render", 2, 3, 1), ("render#2", 7, 3, 1)],
        id="the same method name in two classes is numbered without its class",
    ),
    pytest.param(
        "class A {\n  get v() {\n    return 1;\n  }\n  set v(x) {\n    this.x = x;\n  }\n}\n",
        [("get v", 2, 3, 1), ("set v", 5, 3, 1)],
        id="an accessor pair keeps lizard's names",
    ),
    pytest.param("window.onload = () => {\n  go();\n};\n", [("window.onload", 1, 3, 1)], id="an assignment target stands in full"),
    pytest.param(
        "function f() {\n  return 1;\n}\nconst o = { f() { return 2; } };\nfunction f2() {\n  return 3;\n}\nfunction f() {\n  return 4;\n}\n",
        [("f", 1, 3, 1), ("f#2", 4, 1, 1), ("f2", 5, 3, 1), ("f#3", 8, 3, 1)],
        id="a repeated name is numbered in source order whatever lizard lists first",
    ),
]

MARKUP_AND_SCRIPT = (
    '<script lang="ts">\n  function pick(index: number): string {\n    return items[index] ?? "";\n  }\n</script>\n\n'
    "<ul>\n  {#each items as item}<li on:click={() => { if (a && b) go(); }}>{item}</li>{/each}\n</ul>\n\n"
    '<script context="module" lang="ts">\n  export function later(): void {\n    go();\n  }\n</script>\n'
)


@pytest.mark.parametrize("code, expected", [*FIXED_DEFECTS, *OPEN_MISREADINGS, *NAMED_ONLY, *CALLBACK_BRANCHES, *KEYS])
def test_a_typescript_file_yields_its_named_functions_with_span_ccn_including_callback_branches_and_numbered_keys(code, expected):
    assert reading(".ts", code) == expected


@pytest.mark.parametrize("suffix", [".js", ".mjs"])
def test_javascript_files_are_read_by_the_javascript_reader(suffix):
    assert reading(suffix, "function a() {\n  if (x) y();\n}\nexport const b = () => {\n  go();\n};\n") == [("a", 1, 3, 2), ("b", 4, 3, 1)]


@pytest.mark.parametrize("suffix", [".svelte", ".vue"])
def test_every_script_block_of_a_component_is_read_at_the_lines_of_the_file_and_markup_is_not_read(suffix):
    assert reading(suffix, MARKUP_AND_SCRIPT) == [("pick", 2, 3, 2), ("later", 12, 3, 1)]


@pytest.mark.integration
@pytest.mark.parametrize(
    "path", ["src/api.tsx", "src/api.jsx", "src/api.mts", "src/api.cts", "src/api.cjs", "src/app.css"]
)
def test_a_file_of_a_lines_only_suffix_has_no_function_entry(build_repo, capsys, path):
    """SIZE-4: a 120-line function in a file of a suffix measured by lines only is no finding, and neither is a CSS file."""
    repo = build_repo({"src/a.py": "x = 1\n", path: typescript_function(120), BASELINE_PATH: EMPTY_BASELINE})
    code, output = run(capsys, repo)
    assert (code, rule_notes(repo, Rule.SIZE), rule_findings(repo, Rule.SIZE)) == (0, [], []), output


@pytest.mark.integration
@pytest.mark.parametrize("suffix", ["ts", "js", "mjs", "svelte", "vue"])
def test_a_function_of_120_lines_in_a_measured_script_suffix_is_new_debt_at_its_own_line(build_repo, capsys, suffix):
    """SIZE-4: against a baseline the 120-line function of a script file is new debt at the line the file has it."""
    path = f"src/api.{suffix}"
    source = typescript_function(120)
    repo = build_repo({"src/a.py": "x = 1\n", path: f"<script lang=\"ts\">\n{source}</script>\n" if suffix in {"svelte", "vue"} else source, BASELINE_PATH: EMPTY_BASELINE})
    code, output = run(capsys, repo)
    (finding,) = rule_findings(repo, Rule.SIZE)
    assert (code, finding.path, finding.event, finding.line, "function big has 120 lines" in finding.fact) == (
        1,
        path,
        FindingEvent.NEW_DEBT,
        2 if suffix in {"svelte", "vue"} else 1,
        True,
    ), output


@pytest.mark.integration
def test_a_css_file_of_800_lines_is_new_debt_with_no_function_entry(build_repo, capsys):
    """SIZE-1, SIZE-3: a CSS file is debt from 800 lines and is read for no function."""
    repo = build_repo({"src/app.css": ".a { color: red; }\n" * 800, BASELINE_PATH: EMPTY_BASELINE})
    code, output = run(capsys, repo)
    (finding,) = rule_findings(repo, Rule.SIZE)
    assert (code, finding.path, finding.event, finding.line) == (1, "src/app.css", FindingEvent.NEW_DEBT, None), output


NESTED = "(" * 5_000 + "1" + ")" * 5_000
UNREADABLE_SCRIPTS = pytest.mark.parametrize(
    ("name", "source", "reason"),
    [
        ("src/api.ts", b"export const a = '\xe9';\n", "not UTF-8"),
        ("src/api.ts", f"export function f() {{\n  return {NESTED};\n}}\n".encode(), "nested too deeply to read"),
        ("src/api.js", f"function f() {{\n  return {NESTED};\n}}\n".encode(), "nested too deeply to read"),
        ("src/Page.vue", f"<script>\nfunction f() {{\n  return {NESTED};\n}}\n</script>\n".encode(), "nested too deeply to read"),
        ("src/Page.svelte", f"<script>\nfunction f() {{\n  return {NESTED};\n}}\n</script>\n".encode(), "nested too deeply to read"),
    ],
    ids=["not-utf-8", "typescript-nested", "javascript-nested", "vue-nested", "svelte-nested"],
)


@pytest.mark.integration
@UNREADABLE_SCRIPTS
def test_without_a_baseline_the_size_rule_notes_an_unreadable_script_file(build_repo, capsys, name, source, reason):
    """C4, SIZE-9: with no baseline a script file that is not UTF-8 or nested too deeply to read is a note naming the
    reason; the verdict is unchanged."""
    repo = build_repo_with_source(build_repo, {"src/ok.py": "x = 1\n"}, name, source)
    code, output = run(capsys, repo)
    (note,) = rule_notes(repo, Rule.SIZE)
    assert (code, note.path, note.event, note.fact) == (0, name, NoteEvent.NOT_MEASURED, reason), output


@pytest.mark.integration
@UNREADABLE_SCRIPTS
def test_against_a_baseline_an_unreadable_script_file_is_unparsable(build_repo, capsys, name, source, reason):
    """C4, SIZE-10: with a baseline such a file makes the debt row `✗ not checked`, naming it, exit 2."""
    repo = build_repo_with_source(build_repo, {BASELINE_PATH: EMPTY_BASELINE}, name, source)
    code, output = run(capsys, repo)
    assert code == 2
    assert f"{refusal_row('debt')}{name} is {reason}" in output


@pytest.mark.integration
@UNREADABLE_SCRIPTS
def test_a_fix_refuses_an_unreadable_script_file_and_writes_nothing(build_repo, capsys, name, source, reason):
    """C4, SIZE-11: a fix refuses such a file with `✗ not fixed`, exit 2, writing no baseline."""
    repo = build_repo_with_source(build_repo, {}, name, source)
    assert door_in(repo, *FIX_WORDS) == 2
    assert f"{refusal_row('debt', 'not fixed')}{name} is {reason}" in capsys.readouterr().out
    assert not (repo / BASELINE_PATH).exists()
