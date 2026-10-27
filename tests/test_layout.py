"""#184: the file-suffix rule, a tracked file without a suffix is executable, a link, a dotfile, tool-mandated or declared."""

import subprocess

import pytest
from conftest import hygiene_output
from test_emergency import ONE_JOB

CONFIG = "quality/config.toml"
GUIDANCE = {"AGENTS.md": "# guidance\n"}
EXAMPLE_SHA = "1" * 40


def declaring(path: str, reason: str = "the build reads it by this name", rule: str = "file-suffix") -> str:
    return f'[[layout.allow]]\npath = "{path}"\nrule = "{rule}"\nreason = "{reason}"\n'


def findings(output: str, rule: str = "file-suffix") -> list[str]:
    return [line.split()[1] for line in output.splitlines() if line.startswith(f"{rule} ")]


def in_index(repo, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(repo), *arguments], check=True)


def test_a_file_without_a_suffix_is_red(build_repo, capsys):
    """#184 sentence 1: a tracked file without a suffix is a finding."""
    repo = build_repo({**GUIDANCE, "EDGE_SHA": "abc\n"})
    code, output = hygiene_output(capsys, repo)
    assert (code, findings(output)) == (1, ["EDGE_SHA"])


def test_the_tool_mandated_names_and_dotfiles_are_clean(build_repo, capsys):
    """#184 sentence 1: tool-mandated names and dotfiles are clean."""
    names = ["LICENSE", "Dockerfile", "Makefile", "CODEOWNERS", ".gitignore", "docs/.keep", "deploy/Dockerfile"]
    repo = build_repo({**GUIDANCE, **{name: "x" for name in names}})
    assert findings(hygiene_output(capsys, repo)[1]) == []


def test_names_compare_in_exact_case(build_repo, capsys):
    """#184 sentence 4: names compare in exact case."""
    repo = build_repo({**GUIDANCE, "license": "x", "makefile": "x"})
    assert findings(hygiene_output(capsys, repo)[1]) == ["license", "makefile"]


def test_an_executable_a_symlink_and_a_gitlink_are_clean(build_repo, capsys):
    """#184 sentence 1: executables, symlinks and gitlinks are clean."""
    repo = build_repo({**GUIDANCE, "scripts/run": "#!/bin/sh\n", "scripts/stray": "x"})
    in_index(repo, "update-index", "--chmod=+x", "scripts/run")
    (repo / "latest").symlink_to("AGENTS.md")
    in_index(repo, "add", "--", "latest")
    in_index(repo, "update-index", "--add", "--cacheinfo", f"160000,{EXAMPLE_SHA},vendor")
    assert findings(hygiene_output(capsys, repo)[1]) == ["scripts/stray"]


def test_a_symlink_is_judged_by_its_own_name_whatever_it_points_at(build_repo, capsys):
    """#184 sentence 2: a link is never dereferenced."""
    repo = build_repo({**GUIDANCE, "target.txt": "x"})
    (repo / "latest").symlink_to("target.txt")
    (repo / "latest.txt").symlink_to("missing")
    in_index(repo, "add", "--", "latest", "latest.txt")
    assert findings(hygiene_output(capsys, repo)[1]) == []


def test_the_mode_comes_from_the_index_not_the_disk(build_repo, capsys):
    """#184 sentence 2: the index is the only source of a path's mode."""
    repo = build_repo({**GUIDANCE, "run": "#!/bin/sh\n"})
    (repo / "run").chmod(0o755)
    assert findings(hygiene_output(capsys, repo)[1]) == ["run"]


def test_findings_are_all_printed_sorted_by_path_with_both_ways_out(build_repo, capsys):
    """#184 sentence 4: all findings, sorted by path, naming both ways out."""
    repo = build_repo({**GUIDANCE, "z/NOTES": "x", "EDGE_SHA": "x", "a/b/TODO": "x"})
    code, output = hygiene_output(capsys, repo)
    assert (code, findings(output)) == (1, ["EDGE_SHA", "a/b/TODO", "z/NOTES"])
    assert "[[layout.allow]]" in output
    assert "git update-index" not in output


def test_a_declared_file_is_clean_and_a_declaration_for_a_compliant_file_stays_clean(build_repo, capsys):
    """#184 sentence 3: a declared file is clean; a declaration for a compliant file stays clean."""
    config = ONE_JOB + declaring("docs/EDGE_SHA") + declaring("LICENSE") + declaring("docs/guide.md")
    repo = build_repo({**GUIDANCE, "docs/EDGE_SHA": "x", "LICENSE": "x", "docs/guide.md": "x", CONFIG: config})
    assert findings(hygiene_output(capsys, repo)[1]) == []


def test_a_declaration_for_an_untracked_path_is_a_finding(build_repo, capsys):
    """#184 sentence 3: a declared path that is not tracked is a finding."""
    repo = build_repo({**GUIDANCE, CONFIG: ONE_JOB + declaring("gone/NOTES")})
    code, output = hygiene_output(capsys, repo)
    assert (code, findings(output)) == (1, ["gone/NOTES"])
    assert "[[layout.allow]]" in output


def test_a_declaration_is_exact_so_another_case_is_not_it(build_repo, capsys):
    """#184 sentence 3: a declaration is an exact path."""
    repo = build_repo({**GUIDANCE, "notes": "x", CONFIG: ONE_JOB + declaring("Notes")})
    assert findings(hygiene_output(capsys, repo)[1]) == ["Notes", "notes"]


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
    code, output = hygiene_output(capsys, build_repo({**GUIDANCE, "NOTES": "x", CONFIG: ONE_JOB + config}))
    assert code == 2
    assert "file-suffix cannot judge" in output
    assert "[[layout.allow]]" in output


def test_the_layout_table_is_separate_from_the_root_table(build_repo, capsys):
    """#184 sentence 3: `[[layout.allow]]` is separate from `[[root.allow]]`."""
    root = '[[root.allow]]\nentry = "NOTES"\nreason = "r"\n'
    repo = build_repo({**GUIDANCE, "NOTES": "x", CONFIG: ONE_JOB + root})
    assert findings(hygiene_output(capsys, repo)[1]) == ["NOTES"]


def test_a_path_with_a_control_character_prints_escaped(build_repo, capsys):
    """#184 sentence 4: a finding prints the path escaped."""
    output = hygiene_output(capsys, build_repo({**GUIDANCE, "bad\x01name": "x"}))[1]
    assert "file-suffix bad\\x01name - " in output
    assert "\x01" not in output


def test_an_index_record_that_cannot_be_read_cannot_be_judged(build_repo, capsys, fake_git):
    """#184 sentence 2: index data that cannot be read is exit 2."""
    repo = build_repo(GUIDANCE)
    fake_git(r"""case " $* " in *" -s "*) printf 'not-a-mode abc 0\tAGENTS.md\0'; exit 0;; esac""")
    code, output = hygiene_output(capsys, repo)
    assert (code, "file-suffix cannot judge" in output) == (2, True)


SHELL_HEADER = "#!/usr/bin/env bash\n# Runs the static checks.\nset -euo pipefail\n"
SCRIPT_RULES = ("script-name", "script-header")
CLEAN_SCRIPTS = {"script-name": [], "script-header": []}


def script_findings(output: str) -> dict[str, list[str]]:
    return {rule: findings(output, rule) for rule in SCRIPT_RULES}


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
def test_what_a_script_is(build_repo, capsys, files, executable, scripts):
    """#190 sentence 1: executable, a shebang, or .sh/.py/.mjs under scripts/; a tests/ path never; data never."""
    repo = build_repo({**GUIDANCE, **files})
    for name in executable:
        in_index(repo, "update-index", "--chmod=+x", name)
    assert findings(hygiene_output(capsys, repo)[1], "script-header") == scripts


def test_a_link_is_never_a_script(build_repo, capsys):
    """#190 sentence 1: links (120000, 160000) are never scripts."""
    repo = build_repo({**GUIDANCE, "scripts/target_it.sh": SHELL_HEADER})
    (repo / "scripts" / "go.sh").symlink_to("target_it.sh")
    in_index(repo, "add", "--", "scripts/go.sh")
    in_index(repo, "update-index", "--add", "--cacheinfo", f"160000,{EXAMPLE_SHA},scripts/vendor.sh")
    assert script_findings(hygiene_output(capsys, repo)[1]) == CLEAN_SCRIPTS


@pytest.mark.parametrize(
    "path",
    ["scripts/check.sh", "scripts/prove-x.py", "scripts/Run_it.sh", "scripts/run__it.sh", "scripts/_run.sh", "scripts/run_.sh",
     "scripts/run.it.sh"],
)
def test_a_script_name_that_is_not_two_snake_case_words_is_red(build_repo, capsys, path):
    """#190 sentence 2: check.sh and prove-x.py are red."""
    repo = build_repo({**GUIDANCE, path: SHELL_HEADER})
    assert script_findings(hygiene_output(capsys, repo)[1]) == {"script-name": [path], "script-header": []}


@pytest.mark.parametrize(
    "path", ["scripts/run_static_checks.sh", "scripts/verify_release.py", "scripts/step_2.sh", "frontend/scripts/a1_b2.mjs"]
)
def test_a_snake_case_script_name_is_clean(build_repo, capsys, path):
    """#190 sentence 2: run_static_checks.sh and verify_release.py are clean."""
    repo = build_repo({**GUIDANCE, path: "// Builds it.\n" if path.endswith(".mjs") else SHELL_HEADER})
    assert script_findings(hygiene_output(capsys, repo)[1]) == CLEAN_SCRIPTS


def test_a_script_outside_a_scripts_folder_is_red_unless_tool_mandated_or_a_dotfile(build_repo, capsys):
    """#190 sentence 3: an executable docker/entrypoint.sh is red; tool-mandated names and dotfiles are exempt."""
    names = ["docker/entrypoint.sh", "Dockerfile", "Makefile", "docker/.hook"]
    repo = build_repo({**GUIDANCE, **{name: SHELL_HEADER for name in names}})
    for name in names:
        in_index(repo, "update-index", "--chmod=+x", name)
    assert script_findings(hygiene_output(capsys, repo)[1]) == {"script-name": ["docker/entrypoint.sh"], "script-header": []}


def test_one_declaration_covers_the_shape_and_the_location(build_repo, capsys):
    """#190 sentence 3: one script-name entry covers both shape and location for its path."""
    config = ONE_JOB + declaring("docker/Entry-Point.sh", rule="script-name")
    repo = build_repo({**GUIDANCE, "docker/Entry-Point.sh": SHELL_HEADER, CONFIG: config})
    in_index(repo, "update-index", "--chmod=+x", "docker/Entry-Point.sh")
    assert script_findings(hygiene_output(capsys, repo)[1]) == CLEAN_SCRIPTS


@pytest.mark.parametrize(
    ("content", "red"),
    [
        ("#!/usr/bin/env bash\nset -euo pipefail\n", True),
        ("#!/usr/bin/env bash\n# shellcheck disable=SC2034\nset -euo pipefail\n", True),
        ("#!/usr/bin/env bash\n\n\n\n# Runs it.\nset -e\n", False),
        ("#!/usr/bin/env bash\nset -e\nset -u\n# Runs it.\n", False),
        ("#!/usr/bin/env bash\nset -e\nset -u\nset -o pipefail\n# Late.\n", True),
        ("#!/usr/bin/env bash\n# shellcheck disable=SC2034\n# Runs it.\n", False),
        ("#!/usr/bin/env python3\n# noqa\n# type: ignore\nx = 1\n", True),
        ("#!/usr/bin/env python3\n# ruff: noqa\n# pyright: basic\n# -*- coding: utf-8 -*-\nx = 1\n", True),
        ('"""Runs it."""\nx = 1\n', False),
        ("#!/usr/bin/env python3\n'''Runs it.'''\n", False),
        ('# /// script\n# requires-python = ">=3.12"\n# ///\n# Runs it.\nx = 1\n', False),
        ('# /// script\n# requires-python = ">=3.12"\n# ///\nx = 1\n', True),
        ('#!/usr/bin/env python3\n# /// script\n# dependencies = []\n# ///\n"""Doc."""\n', False),
        ("# -*- coding: utf-8 -*-\n# /// script\n# dependencies = []\n# ///\nprint(1)\n", True),
        ("# coding: utf-8\nprint(1)\n", True),
        ('r"""Runs the checks."""\nprint(1)\n', False),
        ("", True),
    ],
    ids=["code first", "directive only", "blank lines do not count", "third line", "fourth line", "directive then comment",
         "noqa and type", "tool directives", "docstring first", "single-quoted docstring", "after a PEP 723 block",
         "PEP 723 then code", "shebang, block, docstring", "encoding then PEP 723 then code", "plain encoding line",
         "prefixed docstring", "empty"],
)
def test_a_script_needs_a_comment_or_docstring_in_its_first_three_lines(build_repo, capsys, content, red):
    """#190 sentence 4: a non-directive comment or docstring within the first three non-blank lines."""
    repo = build_repo({**GUIDANCE, "scripts/run_it.py": content})
    assert findings(hygiene_output(capsys, repo)[1], "script-header") == (["scripts/run_it.py"] if red else [])


@pytest.mark.parametrize(
    ("content", "red"),
    [("// Builds it.\nexport {};\n", False), ("/* Builds it. */\nexport {};\n", False),
     ("// eslint-disable\nexport {};\n", True), ("# Builds it.\nexport {};\n", True)],
    ids=["line comment", "block comment", "eslint directive", "hash is no mjs comment"],
)
def test_the_comment_marker_of_an_mjs_script_is_slashes(build_repo, capsys, content, red):
    """#190 sentence 4: `//` or `/*` for .mjs."""
    repo = build_repo({**GUIDANCE, "scripts/build_it.mjs": content})
    assert findings(hygiene_output(capsys, repo)[1], "script-header") == (["scripts/build_it.mjs"] if red else [])


def test_the_header_is_judged_for_presence_never_content(build_repo, capsys):
    """#190 sentence 4: presence only."""
    repo = build_repo({**GUIDANCE, "scripts/run_it.sh": "#!/bin/sh\n#\nset -e\n"})
    assert findings(hygiene_output(capsys, repo)[1], "script-header") == []


def test_a_script_header_declaration_covers_only_the_header(build_repo, capsys):
    """#190 sentence 6: script-header is a rule; the declared file is clean of it and the other rule still judges it."""
    config = ONE_JOB + declaring("scripts/bad.sh", rule="script-header")
    repo = build_repo({**GUIDANCE, "scripts/bad.sh": "set -e\n", CONFIG: config})
    assert script_findings(hygiene_output(capsys, repo)[1]) == {"script-name": ["scripts/bad.sh"], "script-header": []}


@pytest.mark.parametrize("rule", SCRIPT_RULES)
def test_a_declaration_for_an_untracked_script_names_its_own_rule(build_repo, capsys, rule):
    """#190 sentence 6: a declared path that is not tracked is a finding naming its own rule."""
    repo = build_repo({**GUIDANCE, CONFIG: ONE_JOB + declaring("scripts/gone.sh", rule=rule)})
    code, output = hygiene_output(capsys, repo)
    assert (code, findings(output, rule), findings(output)) == (1, ["scripts/gone.sh"], [])
    assert "[[layout.allow]]" in output


def test_a_declaration_for_a_compliant_script_stays_clean(build_repo, capsys):
    """#190 sentence 6: a declaration for a file that already complies is clean."""
    config = ONE_JOB + declaring("scripts/run_it.sh", rule="script-name") + declaring("scripts/run_it.sh", rule="script-header")
    repo = build_repo({**GUIDANCE, "scripts/run_it.sh": SHELL_HEADER, CONFIG: config})
    assert hygiene_output(capsys, repo)[0] == 0


def test_script_findings_are_all_printed_sorted_by_path_in_exact_case(build_repo, capsys):
    """#190 sentence 7: all findings, sorted by path, exact case."""
    repo = build_repo({**GUIDANCE, "scripts/b.sh": "x\n", "scripts/B.sh": "x\n", "a/scripts/c.py": "x = 1\n"})
    code, output = hygiene_output(capsys, repo)
    paths = ["a/scripts/c.py", "scripts/B.sh", "scripts/b.sh"]
    assert (code, script_findings(output)) == (1, {"script-name": paths, "script-header": paths})


def test_a_script_that_cannot_be_read_cannot_be_judged(build_repo, capsys):
    """#190 sentence 5: a file that cannot be read is exit 2."""
    repo = build_repo({**GUIDANCE, "scripts/run_it.sh": SHELL_HEADER})
    (repo / "scripts" / "run_it.sh").chmod(0)
    code, output = hygiene_output(capsys, repo)
    assert (code, "script-header cannot judge" in output) == (2, True)


def test_a_file_under_tests_is_not_read_for_a_shebang(build_repo, capsys):
    """#190 sentence 5: the content is read only for the files that need it, and a tests/ path never does."""
    repo = build_repo({**GUIDANCE, "tests/data.bin": "x\n"})
    (repo / "tests" / "data.bin").chmod(0)
    assert hygiene_output(capsys, repo)[0] == 0
