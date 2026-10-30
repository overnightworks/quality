"""#184: the file-suffix rule, a tracked file without a suffix is executable, a link, a dotfile, tool-mandated or declared."""


import pytest

from quality import layout
from quality.output import FIX_LABEL
from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import hygiene_output, refusals, rows_of, rule_findings
from tests.support.git_repositories import put
from tests.support.layout import (
    CONFIG,
    CONTROL_CHARACTERS,
    EXAMPLE_SHA,
    GUIDANCE,
    assert_prints_as_kind,
    assert_prints_escaped_row,
    assert_row_is_kind,
    declaring,
    in_index,
    red,
    rule_ids_in,
)
from tests.support.merge import ONE_JOB


def test_a_file_without_a_suffix_is_red(build_repo, capsys):
    """#184 sentence 1: a tracked file without a suffix is a finding."""
    repo = build_repo({**GUIDANCE, "EDGE_SHA": "abc\n"})
    code = hygiene_output(capsys, repo)[0]
    assert (code, red(repo)) == (1, ["EDGE_SHA"])


def test_the_tool_mandated_names_and_dotfiles_are_clean(build_repo):
    """#184 sentence 1: tool-mandated names and dotfiles are clean."""
    names = ["LICENSE", "Dockerfile", "Makefile", "CODEOWNERS", ".gitignore", "docs/.keep", "deploy/Dockerfile"]
    repo = build_repo({**GUIDANCE, **{name: "x" for name in names}})
    assert red(repo) == []


def test_names_compare_in_exact_case(build_repo):
    """#184 sentence 4: names compare in exact case."""
    repo = build_repo({**GUIDANCE, "license": "x", "makefile": "x"})
    assert red(repo) == ["license", "makefile"]


def test_an_executable_a_symlink_and_a_gitlink_are_clean(build_repo):
    """#184 sentence 1: executables, symlinks and gitlinks are clean."""
    repo = build_repo({**GUIDANCE, "scripts/run": "#!/bin/sh\n", "scripts/stray": "x"})
    in_index(repo, "update-index", "--chmod=+x", "scripts/run")
    (repo / "latest").symlink_to("AGENTS.md")
    in_index(repo, "add", "--", "latest")
    in_index(repo, "update-index", "--add", "--cacheinfo", f"160000,{EXAMPLE_SHA},vendor")
    assert red(repo) == ["scripts/stray"]


def test_a_symlink_is_judged_by_its_own_name_whatever_it_points_at(build_repo):
    """#184 sentence 2: a link is never dereferenced."""
    repo = build_repo({**GUIDANCE, "target.txt": "x"})
    (repo / "latest").symlink_to("target.txt")
    (repo / "latest.txt").symlink_to("missing")
    in_index(repo, "add", "--", "latest", "latest.txt")
    assert red(repo) == []


def test_the_mode_comes_from_the_index_not_the_disk(build_repo):
    """#184 sentence 2: the index is the only source of a path's mode."""
    repo = build_repo({**GUIDANCE, "run": "#!/bin/sh\n"})
    (repo / "run").chmod(0o755)
    assert red(repo) == ["run"]


def test_findings_are_all_printed_sorted_by_path_with_both_ways_out(build_repo, capsys):
    """#184 sentence 4: all findings, sorted by path, naming both ways out."""
    repo = build_repo({**GUIDANCE, "z/NOTES": "x", "EDGE_SHA": "x", "a/b/TODO": "x"})
    code, output = hygiene_output(capsys, repo)
    found = rule_findings(repo, Rule.FILE_SUFFIX)
    rows = [row for finding in found for row in rows_of(output, finding)]
    assert (code, red(repo)) == (1, ["EDGE_SHA", "a/b/TODO", "z/NOTES"])
    assert ([row.fix_follows for row in rows], output.count(f" {FIX_LABEL} {found[0].fix}")) == ([False, False, True], 1)
    assert [finding.fix for finding in found if "git " in finding.fix] == []
    put(repo / CONFIG, ONE_JOB + "".join(declaring(finding.path) for finding in found))
    assert red(repo) == []


def test_a_declared_file_is_clean_and_a_declaration_for_a_compliant_file_stays_clean(build_repo):
    """#184 sentence 3: a declared file is clean; a declaration for a compliant file stays clean."""
    config = ONE_JOB + declaring("docs/EDGE_SHA") + declaring("LICENSE") + declaring("docs/guide.md")
    repo = build_repo({**GUIDANCE, "docs/EDGE_SHA": "x", "LICENSE": "x", "docs/guide.md": "x", CONFIG: config})
    assert red(repo) == []


def test_a_declaration_for_an_untracked_path_is_a_finding(build_repo, capsys):
    """#184 sentence 3: a declared path that is not tracked is a finding."""
    repo = build_repo({**GUIDANCE, CONFIG: ONE_JOB + declaring("gone/NOTES")})
    code = hygiene_output(capsys, repo)[0]
    assert (code, red(repo)) == (1, ["gone/NOTES"])
    assert_prints_as_kind(capsys, repo, Rule.FILE_SUFFIX, FindingEvent.UNUSED)


def test_a_declaration_is_exact_so_another_case_is_not_it(build_repo):
    """#184 sentence 3: a declaration is an exact path."""
    repo = build_repo({**GUIDANCE, "notes": "x", CONFIG: ONE_JOB + declaring("Notes")})
    assert red(repo) == ["Notes", "notes"]


@pytest.mark.parametrize(
    "path",
    ["docs/", "*", "docs/*", "a?b", "[ab]", "../NOTES", "a/../NOTES", "/etc/NOTES", "", " ", "./NOTES", "a//b"],
    ids=["folder", "star", "glob", "question", "bracket", "parent", "inner parent", "absolute", "empty", "blank", "dot slash", "double slash"],
)
def test_an_invalid_declared_path_cannot_be_judged_and_shows_an_example(build_repo, capsys, path):
    """#184 sentence 3: a folder, pattern, `..` or absolute path is exit 2."""
    code, output = hygiene_output(capsys, build_repo({**GUIDANCE, CONFIG: ONE_JOB + declaring(path)}))
    assert code == 2
    assert "[[layout.allow]]" in output
    assert 'rule = "file-suffix"' in output


@pytest.mark.parametrize(
    "config",
    [
        declaring("NOTES") + declaring("NOTES", "again"),
        declaring("NOTES", rule="script-size"),
        declaring("NOTES", reason="two\\nlines"),
        declaring("NOTES", reason=" "),
        declaring("NOTES") + "extra = 1\n",
        '[[layout.allow]]\npath = "NOTES"\nrule = "file-suffix"\n',
        '[[layout.allow]]\npath = 3\nrule = "file-suffix"\nreason = "r"\n',
        "[layout]\nignore = []\n",
        '[layout]\nallow = "x"\n',
        "[layout]\nenabled = false\n",
    ],
    ids=[
        "duplicate",
        "unknown rule",
        "multi-line reason",
        "blank reason",
        "unknown key",
        "no reason",
        "path not text",
        "unknown table key",
        "allow not a list",
        "no off switch",
    ],
)
def test_a_malformed_layout_table_cannot_be_judged(build_repo, capsys, config):
    """#184 sentence 3: a duplicate, unknown rule or key, or a reason that is not one-line text is exit 2."""
    repo = build_repo({**GUIDANCE, "NOTES": "x", CONFIG: ONE_JOB + config})
    code, output = hygiene_output(capsys, repo)
    assert code == 2
    assert Rule.FILE_SUFFIX in refusals(repo)
    assert "[[layout.allow]]" in output


def test_the_layout_table_is_separate_from_the_root_table(build_repo):
    """#184 sentence 3: `[[layout.allow]]` is separate from `[[root.allow]]`."""
    root = '[[root.allow]]\nentry = "NOTES"\nreason = "r"\n'
    repo = build_repo({**GUIDANCE, "NOTES": "x", CONFIG: ONE_JOB + root})
    assert red(repo) == ["NOTES"]


@pytest.mark.parametrize(("character", "escaped"), CONTROL_CHARACTERS)
def test_a_path_with_a_control_format_or_separator_character_prints_escaped(build_repo, capsys, character, escaped):
    """#184 sentence 4: a finding prints the path escaped."""
    assert_prints_escaped_row(capsys, build_repo, Rule.FILE_SUFFIX, character, escaped)


def test_an_index_record_that_cannot_be_read_cannot_be_judged(build_repo, capsys, fake_git):
    """#184 sentence 2: index data that cannot be read is exit 2."""
    repo = build_repo(GUIDANCE)
    fake_git(r"""case " $* " in *" -s "*) printf 'not-a-mode abc 0\tAGENTS.md\0'; exit 0;; esac""")
    code, _ = hygiene_output(capsys, repo)
    assert (code, Rule.FILE_SUFFIX in refusals(repo)) == (2, True)


SHELL_HEADER = "#!/usr/bin/env bash\n# Runs the static checks.\nset -euo pipefail\n"
SCRIPT_RULES = (Rule.SCRIPT_NAME, Rule.SCRIPT_HEADER)
CLEAN_SCRIPTS = {Rule.SCRIPT_NAME: [], Rule.SCRIPT_HEADER: []}


def script_findings(repo) -> dict[Rule, list[str]]:
    return {rule: red(repo, rule) for rule in SCRIPT_RULES}


@pytest.mark.parametrize(
    ("files", "executable", "scripts"),
    [
        ({"tools/a": "#!/bin/sh\n"}, [], ["tools/a"]),
        ({"tools/a": "echo\n"}, ["tools/a"], ["tools/a"]),
        ({"frontend/scripts/a.sh": "echo\n", "scripts/deep/b.py": "x = 1\n", "scripts/c.mjs": "x\n"}, [],
         ["frontend/scripts/a.sh", "scripts/c.mjs", "scripts/deep/b.py"]),
        ({"scripts/BACKUP.md": "x\n", "scripts/x.json": "{}\n", "scripts/x.txt": "x\n"}, [], []),
        ({"tests/scripts/a.py": "#!/bin/sh\n", "tests/b.sh": "echo\n", "scripts/tests/c.sh": "echo\n"}, ["tests/b.sh"], []),
        ({"docs/a.md": "# #!/bin/sh\n", "src/a.py": "x = 1\n"}, [], []),
    ],
    ids=["shebang", "executable", "scripts folder at any depth", "data beside a script", "tests folder", "no script"],
)
def test_what_a_script_is(build_repo, files, executable, scripts):
    """#190 sentence 1: executable, a shebang, or .sh/.py/.mjs under scripts/; a tests/ path never; data never."""
    repo = build_repo({**GUIDANCE, **files})
    for name in executable:
        in_index(repo, "update-index", "--chmod=+x", name)
    assert red(repo, Rule.SCRIPT_HEADER) == scripts


def test_a_link_is_never_a_script(build_repo):
    """#190 sentence 1: links (120000, 160000) are never scripts."""
    repo = build_repo({**GUIDANCE, "scripts/target_it.sh": SHELL_HEADER})
    (repo / "scripts" / "go.sh").symlink_to("target_it.sh")
    in_index(repo, "add", "--", "scripts/go.sh")
    in_index(repo, "update-index", "--add", "--cacheinfo", f"160000,{EXAMPLE_SHA},scripts/vendor.sh")
    assert script_findings(repo) == CLEAN_SCRIPTS


@pytest.mark.parametrize(
    "path",
    ["scripts/check.sh", "scripts/prove-x.py", "scripts/Run_it.sh", "scripts/run__it.sh", "scripts/_run.sh", "scripts/run_.sh",
     "scripts/run.it.sh"],
)
def test_a_script_name_that_is_not_two_snake_case_words_is_red(build_repo, path):
    """#190 sentence 2: check.sh and prove-x.py are red."""
    repo = build_repo({**GUIDANCE, path: SHELL_HEADER})
    assert script_findings(repo) == {Rule.SCRIPT_NAME: [path], Rule.SCRIPT_HEADER: []}


@pytest.mark.parametrize(
    "path", ["scripts/run_static_checks.sh", "scripts/verify_release.py", "scripts/step_2.sh", "frontend/scripts/a1_b2.mjs"]
)
def test_a_snake_case_script_name_is_clean(build_repo, path):
    """#190 sentence 2: run_static_checks.sh and verify_release.py are clean."""
    repo = build_repo({**GUIDANCE, path: "// Builds it.\n" if path.endswith(".mjs") else SHELL_HEADER})
    assert script_findings(repo) == CLEAN_SCRIPTS


def test_a_script_outside_a_scripts_folder_is_red_unless_tool_mandated_or_a_dotfile(build_repo):
    """#190 sentence 3: an executable docker/entrypoint.sh is red; tool-mandated names and dotfiles are exempt."""
    names = ["docker/entrypoint.sh", "Dockerfile", "Makefile", "docker/.hook"]
    repo = build_repo({**GUIDANCE, **{name: SHELL_HEADER for name in names}})
    for name in names:
        in_index(repo, "update-index", "--chmod=+x", name)
    assert script_findings(repo) == {Rule.SCRIPT_NAME: ["docker/entrypoint.sh"], Rule.SCRIPT_HEADER: []}


def test_one_declaration_covers_the_shape_and_the_location(build_repo):
    """#190 sentence 3: one script-name entry covers both shape and location for its path."""
    config = ONE_JOB + declaring("docker/Entry-Point.sh", rule="script-name")
    repo = build_repo({**GUIDANCE, "docker/Entry-Point.sh": SHELL_HEADER, CONFIG: config})
    in_index(repo, "update-index", "--chmod=+x", "docker/Entry-Point.sh")
    assert script_findings(repo) == CLEAN_SCRIPTS


@pytest.mark.parametrize(
    ("content", "is_red"),
    [
        ("#!/usr/bin/env bash\nset -euo pipefail\n", True),
        ("#!/usr/bin/env bash\n# shellcheck disable=SC2034\nset -euo pipefail\n", True),
        ("#!/usr/bin/env bash\n\n\n\n# Runs it.\nset -e\n", False),
        ("#!/usr/bin/env bash\nset -e\nset -u\n# Runs it.\n", False),
        ("#!/usr/bin/env bash\nset -e\nset -u\nset -o pipefail\n# Late.\n", True),
        ("#!/usr/bin/env bash\n# shellcheck disable=SC2034\n# Runs it.\n", False),
        ("#!/usr/bin/env python3\n# noqa\n# type: ignore\nx = 1\n", True),
        ("#!/usr/bin/env python3\n# ruff: noqa\n# pyright: basic\n# -*- coding: utf-8 -*-\nx = 1\n", True),
        ("#!/usr/bin/env python3\n# ruff: noqa: T201 - the script owns its output\nprint(1)\n", True),
        ('"""Runs it."""\nx = 1\n', False),
        ("#!/usr/bin/env python3\n'''Runs it.'''\n", False),
        ('# /// script\n# requires-python = ">=3.12"\n# ///\n# Runs it.\nx = 1\n', False),
        ('# /// script\n# requires-python = ">=3.12"\n# ///\nx = 1\n', True),
        ('#!/usr/bin/env python3\n# /// script\n# dependencies = []\n# ///\n"""Doc."""\n', False),
        ("# -*- coding: utf-8 -*-\n# /// script\n# dependencies = []\n# ///\nprint(1)\n", True),
        ("# coding: utf-8\nprint(1)\n", True),
        ('r"""Runs the checks."""\nprint(1)\n', False),
        ("# -*- coding: utf-8 -*-\nimport os\nimport sys\n# Runs checks.\n", True),
        ('b"""Runs checks."""\nprint(1)\n', True),
        ('f"""Runs checks."""\nprint(1)\n', True),
        ("", True),
    ],
    ids=["code first", "directive only", "blank lines do not count", "third line", "fourth line", "directive then comment",
         "noqa and type", "tool directives", "output owner directive", "docstring first", "single-quoted docstring", "after a PEP 723 block",
         "PEP 723 then code", "shebang, block, docstring", "encoding then PEP 723 then code", "plain encoding line",
         "prefixed docstring", "encoding line counts toward the window", "bytes literal", "f-string", "empty"],
)
def test_a_script_needs_a_comment_or_docstring_in_its_first_three_lines(build_repo, content, is_red):
    """#190 sentence 4: a non-directive comment or docstring within the first three non-blank lines; python-print's
    output owner line is a directive (#225 cell 7)."""
    repo = build_repo({**GUIDANCE, "scripts/run_it.py": content})
    assert red(repo, Rule.SCRIPT_HEADER) == (["scripts/run_it.py"] if is_red else [])


@pytest.mark.parametrize(
    ("content", "is_red"),
    [("// Builds it.\nexport {};\n", False), ("/* Builds it. */\nexport {};\n", False),
     ("// eslint-disable\nexport {};\n", True), ("# Builds it.\nexport {};\n", True)],
    ids=["line comment", "block comment", "eslint directive", "hash is no mjs comment"],
)
def test_the_comment_marker_of_an_mjs_script_is_slashes(build_repo, content, is_red):
    """#190 sentence 4: `//` or `/*` for .mjs."""
    repo = build_repo({**GUIDANCE, "scripts/build_it.mjs": content})
    assert red(repo, Rule.SCRIPT_HEADER) == (["scripts/build_it.mjs"] if is_red else [])


def test_the_header_is_judged_for_presence_never_content(build_repo):
    """#190 sentence 4: presence only."""
    repo = build_repo({**GUIDANCE, "scripts/run_it.sh": "#!/bin/sh\n#\nset -e\n"})
    assert red(repo, Rule.SCRIPT_HEADER) == []


def test_a_script_header_declaration_covers_only_the_header(build_repo):
    """#190 sentence 6: script-header is a rule; the declared file is clean of it and the other rule still judges it."""
    config = ONE_JOB + declaring("scripts/bad.sh", rule="script-header")
    repo = build_repo({**GUIDANCE, "scripts/bad.sh": "set -e\n", CONFIG: config})
    assert script_findings(repo) == {Rule.SCRIPT_NAME: ["scripts/bad.sh"], Rule.SCRIPT_HEADER: []}


@pytest.mark.parametrize("rule", SCRIPT_RULES)
def test_a_declaration_for_an_untracked_script_names_its_own_rule(build_repo, capsys, rule):
    """#190 sentence 6: a declared path that is not tracked is a finding naming its own rule."""
    repo = build_repo({**GUIDANCE, CONFIG: ONE_JOB + declaring("scripts/gone.sh", rule=rule)})
    code = hygiene_output(capsys, repo)[0]
    assert (code, red(repo, rule), red(repo)) == (1, ["scripts/gone.sh"], [])
    assert_prints_as_kind(capsys, repo, rule, FindingEvent.UNUSED)


def test_a_declaration_for_a_compliant_script_stays_clean(build_repo, capsys):
    """#190 sentence 6: a declaration for a file that already complies is clean."""
    config = ONE_JOB + declaring("scripts/run_it.sh", rule="script-name") + declaring("scripts/run_it.sh", rule="script-header")
    repo = build_repo({**GUIDANCE, "scripts/run_it.sh": SHELL_HEADER, CONFIG: config})
    assert hygiene_output(capsys, repo)[0] == 0


def test_script_findings_are_all_printed_sorted_by_path_in_exact_case(build_repo, capsys):
    """#190 sentence 7: all findings, sorted by path, exact case."""
    repo = build_repo({**GUIDANCE, "scripts/b.sh": "x\n", "scripts/B.sh": "x\n", "a/scripts/c.py": "x = 1\n"})
    code = hygiene_output(capsys, repo)[0]
    paths = ["a/scripts/c.py", "scripts/B.sh", "scripts/b.sh"]
    assert (code, script_findings(repo)) == (1, {Rule.SCRIPT_NAME: paths, Rule.SCRIPT_HEADER: paths})


def test_a_script_that_cannot_be_read_cannot_be_judged(build_repo, capsys):
    """#190 sentence 5: a file that cannot be read is exit 2."""
    repo = build_repo({**GUIDANCE, "scripts/run_it.sh": SHELL_HEADER})
    (repo / "scripts" / "run_it.sh").chmod(0)
    code, _ = hygiene_output(capsys, repo)
    assert (code, Rule.SCRIPT_HEADER in refusals(repo)) == (2, True)


def test_a_file_under_tests_is_not_read_for_a_shebang(build_repo, capsys):
    """#190 sentence 5: the content is read only for the files that need it, and a tests/ path never does."""
    repo = build_repo({**GUIDANCE, "tests/data.bin": "x\n"})
    (repo / "tests" / "data.bin").chmod(0)
    assert hygiene_output(capsys, repo)[0] == 0


def test_a_suffixless_file_is_a_tracked_finding_the_door_prints_as_its_row(build_repo, capsys):
    """#283 O12 and picture :48: the kind `tracked`, no trailing period, the row equal to the finding."""
    assert_prints_as_kind(capsys, build_repo({**GUIDANCE, "docs/EDGE_SHA": "x"}), Rule.FILE_SUFFIX, FindingEvent.TRACKED)


def test_three_suffixless_files_print_three_rows_and_one_fix_after_the_third(build_repo, capsys):
    """#228 rule 7, decision 4: a fix shared by consecutive findings prints once, under the last."""
    repo = build_repo({**GUIDANCE, "docs/EDGE_SHA": "x", "docs/b/TODO": "x", "docs/NOTES": "x"})
    output = hygiene_output(capsys, repo)[1]
    rows = [row for finding in rule_findings(repo, Rule.FILE_SUFFIX) for row in rows_of(output, finding)]
    assert ([row.fix_follows for row in rows], output.count(f" {FIX_LABEL} ")) == ([False, False, True], 1)


LAYOUT_ALLOWANCE_SCENARIOS = [
    (Rule.FILE_SUFFIX, {"docs/NOTES": "x"}),
    (Rule.SCRIPT_NAME, {"scripts/check.sh": SHELL_HEADER}),
    (Rule.SCRIPT_HEADER, {"scripts/run_it.sh": "set -e\n"}),
    (Rule.TEST_LAYOUT, {"pyproject.toml": "", "src/p/x.py": "", "tests/helpers.py": ""}),
    (Rule.TEST_IMPORT, {"pyproject.toml": "", "tests/conftest.py": "", "tests/support/y.py": "import conftest\n"}),
]


@pytest.mark.parametrize(("rule", "files"), LAYOUT_ALLOWANCE_SCENARIOS, ids=[rule.value for rule, _ in LAYOUT_ALLOWANCE_SCENARIOS])
def test_the_allowance_fix_names_the_findings_own_rule_and_its_entry_clears_the_finding(build_repo, rule, files):
    """#283 decision 12: the one rule id a fix names is the finding's own, and the entry it asks for is accepted."""
    repo = build_repo({**GUIDANCE, **files})
    (finding,) = rule_findings(repo, rule)
    assert rule_ids_in(finding.fix) == {rule.value}
    put(repo / CONFIG, ONE_JOB + declaring(finding.path, rule=rule))
    assert red(repo, rule) == []


def test_a_path_declared_under_two_rules_and_untracked_is_one_equal_stale_row_per_rule(build_repo, capsys):
    """#283 O18: the rows read alike and name no rule; deleting every entry of the path clears both."""
    config = ONE_JOB + declaring("scripts/gone.sh", rule=Rule.SCRIPT_NAME) + declaring("scripts/gone.sh", rule=Rule.SCRIPT_HEADER)
    repo = build_repo({**GUIDANCE, CONFIG: config})
    found = [finding for rule in SCRIPT_RULES for finding in rule_findings(repo, rule)]
    output = hygiene_output(capsys, repo)[1]
    assert [(finding.rule, finding.event) for finding in found] == [(rule, FindingEvent.UNUSED) for rule in SCRIPT_RULES]
    assert (len({(finding.reason, finding.fix) for finding in found}), rule_ids_in(found[0].fix)) == (1, set())
    assert [len(rows_of(output, finding)) for finding in found] == [2, 2]
    put(repo / CONFIG, ONE_JOB)
    assert script_findings(repo) == CLEAN_SCRIPTS


def test_script_name_is_misnamed_where_the_shape_breaks_and_misplaced_where_only_the_place_does(build_repo, capsys):
    """#283 O19: both broken is `misnamed` and holds the place-only fact; all three share one fix, printed once."""
    paths = ["scripts/check.sh", "tools/Entry.sh", "tools/entry_point.sh"]
    repo = build_repo({**GUIDANCE, **dict.fromkeys(paths, SHELL_HEADER)})
    for path in paths[1:]:
        in_index(repo, "update-index", "--chmod=+x", path)
    found = {finding.path: finding for finding in rule_findings(repo, Rule.SCRIPT_NAME)}
    output = hygiene_output(capsys, repo)[1]
    assert [found[path].event for path in paths] == [FindingEvent.MISNAMED, FindingEvent.MISNAMED, FindingEvent.MISPLACED]
    assert found["tools/entry_point.sh"].reason in found["tools/Entry.sh"].reason
    assert len({finding.fix for finding in found.values()}) == 1
    rows = [row for path in paths for row in rows_of(output, found[path])]
    assert [row.fix_follows for row in rows] == [False, False, True]
    for path, event in zip(paths, [FindingEvent.MISNAMED, FindingEvent.MISNAMED, FindingEvent.MISPLACED], strict=True):
        assert_row_is_kind(output, found[path], event)


def test_a_script_without_a_header_is_a_no_header_finding(build_repo, capsys):
    """#283 O12: the fact names the window it checks, never the file."""
    repo = build_repo({**GUIDANCE, "scripts/run_it.sh": "set -e\n"})
    finding = assert_prints_as_kind(capsys, repo, Rule.SCRIPT_HEADER, FindingEvent.NO_HEADER, str(layout.HEADER_LINES))
    assert finding.path not in finding.reason
