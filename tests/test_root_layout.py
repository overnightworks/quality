"""#144: the root-layout rule, every root entry on quality's base list or declared with a reason."""

import subprocess

import pytest

from quality.config import CONFIG_PATH, ROOT_ALLOWANCE_EXAMPLE
from quality.output import FIX_LABEL
from quality.report import FindingEvent
from quality.root_layout import ROOT_BASE_DIRECTORIES, ROOT_BASE_ENTRIES, ROOT_BASE_FILES
from quality.rules import Rule
from tests.support.check import copy_block, env_row_stands, finding_rows, hygiene_output, rows_of, rule_findings
from tests.support.config_refusals import KNOWN_KEYS, ONE_LINE_NEED
from tests.support.git_repositories import put
from tests.support.layout import (
    CONTROL_CHARACTERS,
    assert_prints_as_kind,
    assert_prints_escaped_row,
    assert_row_is_kind,
    red,
    rule_ids_in,
)
from tests.support.merge import ONE_JOB
from tests.support.verdict import verdict_of

CONFIG = "quality/config.toml"
GUIDANCE = {"AGENTS.md": "# guidance\n"}


def declaring(entry: str, reason: str = "built apart from the package") -> str:
    return f'[[root.allow]]\nentry = "{entry}"\nreason = "{reason}"\n'


def test_two_strays_are_two_findings_and_a_directory_counts_once(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "notes.txt": "x", "misc/a.py": "x", "misc/b.py": "x"})
    code = hygiene_output(capsys, repo)[0]
    assert (code, red(repo, Rule.ROOT_LAYOUT)) == (1, ["misc/", "notes.txt"])


def test_an_untracked_file_is_green_until_it_is_added(build_repo, capsys):
    repo = build_repo(GUIDANCE)
    (repo / "scratch.png").write_text("x")
    assert red(repo, Rule.ROOT_LAYOUT) == []
    subprocess.run(["git", "-C", str(repo), "add", "--", "scratch.png"], check=True)
    assert red(repo, Rule.ROOT_LAYOUT) == ["scratch.png"]


def test_every_base_list_entry_is_green_without_a_config(build_repo, capsys):
    files = {name: "x" for name in ROOT_BASE_FILES}
    files |= {"CLAUDE.md": "@AGENTS.md\n", "AGENTS.md": "# guidance\n", "compose.yaml": "services: {}\n"}
    files |= {"compose.yml": "services: {}\n", "docker-compose.yaml": "services: {}\n", "docker-compose.yml": "services: {}\n"}
    files |= {f"{directory}f": "x" for directory in ROOT_BASE_DIRECTORIES}
    assert red(build_repo(files), Rule.ROOT_LAYOUT) == []


ROOT = f"{CONFIG_PATH} [[root.allow]]"
ROOT_NAME_NEED = "entry as one name directly in the root, a directory with a trailing /, not a path"


@pytest.mark.parametrize(
    "entry",
    ["a/b", "/", ".", "..", ".git", "./", "", " ", "src/pkg/", "/abs"],
    ids=["inner slash", "slash only", "dot", "dot dot", "dot git", "dot slash", "empty", "blank", "deeper path", "leading slash"],
)
def test_an_invalid_declaration_cannot_be_judged_and_shows_an_example(build_repo, capsys, entry):
    repo = build_repo({**GUIDANCE, CONFIG: declaring(entry)})
    code, output = hygiene_output(capsys, repo)
    assert code == 2
    assert f"{ROOT} entry 1 needs {ROOT_NAME_NEED}" in output
    assert output.count(copy_block(ROOT_ALLOWANCE_EXAMPLE)) == 1


@pytest.mark.parametrize(
    ("config", "fact"),
    [
        ('[[root.allow]]\nentry = "x/"\nreason = "two\\nlines"\n', f"{ROOT} entry 1 needs {ONE_LINE_NEED}"),
        ('[[root.allow]]\nentry = "x/"\nreason = "r"\nextra = 1\n', f"{ROOT} entry 1 needs exactly an entry and a reason"),
        ('[[root.allow]]\nentry = "x/"\n', f"{ROOT} entry 1 needs exactly an entry and a reason"),
        ('[[root.allow]]\nentry = 3\nreason = "r"\n', f"{ROOT} entry 1 needs {ROOT_NAME_NEED}"),
        ("[root]\nignore = []\n", f"{CONFIG_PATH} has unknown key ignore - {KNOWN_KEYS}"),
        ('[root]\nallow = "x"\n', f"{CONFIG_PATH} needs [[root.allow]] as an array of tables"),
    ],
    ids=["multi-line reason", "unknown key", "no reason", "entry not text", "unknown table key", "allow not a list"],
)
def test_a_malformed_root_table_cannot_be_judged(build_repo, capsys, config, fact):
    code, output = hygiene_output(capsys, build_repo({**GUIDANCE, CONFIG: config}))
    assert code == 2
    assert fact in output
    assert output.count(copy_block(ROOT_ALLOWANCE_EXAMPLE)) == 1


def test_a_declared_collecting_directory_is_green(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "misc/a.py": "x", CONFIG: declaring("misc/")})
    assert red(repo, Rule.ROOT_LAYOUT) == []


def test_a_wrongly_cased_name_is_unlisted_and_its_fact_names_the_base_list_spelling(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "readme.md": "x", "SRC/a.py": "x"})
    found = {finding.path: finding for finding in rule_findings(repo, Rule.ROOT_LAYOUT)}
    assert {path: finding.event for path, finding in found.items()} == dict.fromkeys(["SRC/", "readme.md"], FindingEvent.UNLISTED)
    spelling = {path: next(entry for entry in ROOT_BASE_ENTRIES if entry.casefold() == path.casefold()) for path in found}
    assert [spelling[path] in found[path].reason for path in found] == [True, True]
    assert [spelling[path] in found[other].reason for path, other in (("SRC/", "readme.md"), ("readme.md", "SRC/"))] == [False, False]


def test_a_file_named_like_an_allowed_directory_is_red(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "src": "x"})
    assert red(repo, Rule.ROOT_LAYOUT) == ["src"]


def test_a_root_symlink_counts_by_its_own_name(build_repo, capsys):
    repo = build_repo(GUIDANCE)
    (repo / "latest").symlink_to("AGENTS.md")
    subprocess.run(["git", "-C", str(repo), "add", "--", "latest"], check=True)
    assert red(repo, Rule.ROOT_LAYOUT) == ["latest"]


def test_the_same_entry_declared_twice_cannot_be_judged_and_is_named(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "plans/a.md": "x", CONFIG: declaring("plans/") + declaring("plans/", "again")})
    code, output = hygiene_output(capsys, repo)
    assert code == 2
    assert f'{ROOT} declares entry = "plans/" twice; one entry per root entry' in output
    assert output.count(copy_block(ROOT_ALLOWANCE_EXAMPLE)) == 1


@pytest.mark.parametrize(("character", "escaped"), CONTROL_CHARACTERS, ids=["control", "escape", "right-to-left override"])
def test_a_name_with_a_control_character_prints_escaped(build_repo, capsys, character, escaped):
    repo = build_repo({**GUIDANCE, f"bad{character}name": "x"})
    assert_prints_escaped_row(capsys, repo, Rule.ROOT_LAYOUT, f"bad{escaped}name")


def test_no_key_of_the_root_table_switches_the_rule_off(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "notes.txt": "x", CONFIG: ONE_JOB + "[root]\nenabled = false\n"})
    assert hygiene_output(capsys, repo)[0] == 2


def test_the_rule_is_on_in_a_repository_without_a_config(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "notes.txt": "x"})
    code = hygiene_output(capsys, repo)[0]
    assert (code, red(repo, Rule.ROOT_LAYOUT)) == (1, ["notes.txt"])


def test_a_declared_entry_is_green_and_a_base_list_entry_may_be_declared(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "Makefile": "x", CONFIG: declaring("Makefile") + declaring("AGENTS.md")})
    assert red(repo, Rule.ROOT_LAYOUT) == []


def test_a_declaration_matching_no_tracked_entry_is_red_and_names_it(build_repo, capsys):
    repo = build_repo({**GUIDANCE, CONFIG: declaring("plans/")})
    code = hygiene_output(capsys, repo)[0]
    assert (code, red(repo, Rule.ROOT_LAYOUT)) == (1, ["plans/"])
    assert_prints_as_kind(capsys, repo, Rule.ROOT_LAYOUT, FindingEvent.UNUSED)


def test_a_directory_declared_without_its_slash_is_told_the_slash_is_what_is_tracked(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "frontend/a.js": "x", CONFIG: declaring("frontend")})
    stale = next(finding for finding in rule_findings(repo, Rule.ROOT_LAYOUT) if finding.event is FindingEvent.UNUSED)
    assert_row_is_kind(hygiene_output(capsys, repo)[1], stale, FindingEvent.UNUSED, "frontend/")


def test_a_finding_names_the_entry_and_both_ways_out_without_a_local_path(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "notes.txt": "x"})
    finding = assert_prints_as_kind(capsys, repo, Rule.ROOT_LAYOUT, FindingEvent.UNLISTED)
    assert (finding.path, str(repo) in f"{finding.reason} {finding.fix}") == ("notes.txt", False)
    assert rule_ids_in(finding.fix) == set()
    put(repo / CONFIG, ONE_JOB + declaring(finding.path))
    assert red(repo, Rule.ROOT_LAYOUT) == []


def test_removing_a_declaration_makes_the_entry_red(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "misc/a.py": "x", CONFIG: declaring("misc/")})
    assert red(repo, Rule.ROOT_LAYOUT) == []
    put(repo / CONFIG, None)
    assert red(repo, Rule.ROOT_LAYOUT) == ["misc/"]


def test_moving_an_entry_without_its_declaration_leaves_the_declaration_red(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "docs/misc/a.py": "x", CONFIG: declaring("misc/")})
    assert red(repo, Rule.ROOT_LAYOUT) == ["misc/"]


def test_the_rule_writes_nothing_and_prints_no_command(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "notes.txt": "x"})
    before = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, check=True).stdout
    output = hygiene_output(capsys, repo)[1]
    after = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, check=True).stdout
    assert (before, after) == (b"", b"")
    assert ["git " in row.fix for finding in rule_findings(repo, Rule.ROOT_LAYOUT) for row in rows_of(output, finding)] == [False]


def test_a_tracked_env_file_is_red_under_env_file_and_under_root_layout(build_repo, capsys):
    repo = build_repo({**GUIDANCE, ".env": "A=1\n"})
    assert (red(repo, Rule.ROOT_LAYOUT), env_row_stands(hygiene_output(capsys, repo)[1])) == ([".env"], True)


def test_tsconfig_in_the_root_is_red_unless_declared(build_repo, capsys):
    assert red(build_repo({**GUIDANCE, "tsconfig.json": "{}"}), Rule.ROOT_LAYOUT) == ["tsconfig.json"]


def test_a_hundred_strays_print_a_hundred_sorted_findings(build_repo, capsys):
    names = [f"stray{number:03d}.txt" for number in range(100)]
    repo = build_repo({**GUIDANCE, **{name: "x" for name in reversed(names)}})
    output = hygiene_output(capsys, repo)[1]
    rows = [row for name in names for row in finding_rows(output, name)]
    assert (red(repo, Rule.ROOT_LAYOUT), len(rows), [row.fix_follows for row in rows]) == (names, 100, [False] * 99 + [True])
    assert output.count(f" {FIX_LABEL} ") == 1


def test_a_declaration_is_no_loosening_at_the_verdict(pull_request, capsys):
    repo, commit_base = pull_request
    commit_base(None, ONE_JOB)
    put(repo / CONFIG, ONE_JOB + declaring("frontend/"))
    code, output = verdict_of(repo, capsys)
    assert (code, "loosened" in output, "verdict: green" in output) == (0, False, True)
