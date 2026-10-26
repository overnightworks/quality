"""#184: the file-suffix rule, a tracked file without a suffix is executable, a link, a dotfile, tool-mandated or declared."""

import subprocess

import pytest
from conftest import hygiene_output
from test_emergency import ONE_JOB

from quality import hygiene

CONFIG = "quality/config.toml"
GUIDANCE = {"AGENTS.md": "# guidance\n"}
EXAMPLE_SHA = "1" * 40


def declaring(path: str, reason: str = "the build reads it by this name", rule: str = "file-suffix") -> str:
    return f'[[layout.allow]]\npath = "{path}"\nrule = "{rule}"\nreason = "{reason}"\n'


def findings(output: str) -> list[str]:
    return [line.split()[1] for line in output.splitlines() if line.startswith("file-suffix ")]


def in_index(repo, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(repo), *arguments], check=True)


def test_a_file_without_a_suffix_is_red(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "EDGE_SHA": "abc\n"})
    code, output = hygiene_output(capsys, repo)
    assert (code, findings(output)) == (1, ["EDGE_SHA"])


def test_the_tool_mandated_names_and_dotfiles_are_clean(build_repo, capsys):
    names = ["LICENSE", "Dockerfile", "Makefile", "CODEOWNERS", ".gitignore", "docs/.keep", "deploy/Dockerfile"]
    repo = build_repo({**GUIDANCE, **{name: "x" for name in names}})
    assert findings(hygiene_output(capsys, repo)[1]) == []


def test_names_compare_in_exact_case(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "license": "x", "makefile": "x"})
    assert findings(hygiene_output(capsys, repo)[1]) == ["license", "makefile"]


def test_an_executable_a_symlink_and_a_gitlink_are_clean(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "scripts/run": "#!/bin/sh\n", "scripts/stray": "x"})
    in_index(repo, "update-index", "--chmod=+x", "scripts/run")
    (repo / "latest").symlink_to("AGENTS.md")
    in_index(repo, "add", "--", "latest")
    in_index(repo, "update-index", "--add", "--cacheinfo", f"160000,{EXAMPLE_SHA},vendor")
    assert findings(hygiene_output(capsys, repo)[1]) == ["scripts/stray"]


def test_a_symlink_is_judged_by_its_own_name_whatever_it_points_at(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "target.txt": "x"})
    (repo / "latest").symlink_to("target.txt")
    (repo / "latest.txt").symlink_to("missing")
    in_index(repo, "add", "--", "latest", "latest.txt")
    assert findings(hygiene_output(capsys, repo)[1]) == []


def test_the_mode_comes_from_the_index_not_the_disk(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "run": "#!/bin/sh\n"})
    (repo / "run").chmod(0o755)
    assert findings(hygiene_output(capsys, repo)[1]) == ["run"]


def test_findings_are_all_printed_sorted_by_path_with_both_ways_out(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "z/NOTES": "x", "EDGE_SHA": "x", "a/b/TODO": "x"})
    code, output = hygiene_output(capsys, repo)
    assert (code, findings(output)) == (1, ["EDGE_SHA", "a/b/TODO", "z/NOTES"])
    assert "[[layout.allow]]" in output
    assert "git update-index" not in output


def test_a_declared_file_is_clean_and_a_declaration_for_a_compliant_file_stays_clean(build_repo, capsys):
    config = ONE_JOB + declaring("docs/EDGE_SHA") + declaring("LICENSE") + declaring("docs/guide.md")
    repo = build_repo({**GUIDANCE, "docs/EDGE_SHA": "x", "LICENSE": "x", "docs/guide.md": "x", CONFIG: config})
    assert findings(hygiene_output(capsys, repo)[1]) == []


def test_a_declaration_for_an_untracked_path_is_a_finding(build_repo, capsys):
    repo = build_repo({**GUIDANCE, CONFIG: ONE_JOB + declaring("gone/NOTES")})
    code, output = hygiene_output(capsys, repo)
    assert (code, findings(output)) == (1, ["gone/NOTES"])
    assert "[[layout.allow]]" in output


def test_a_declaration_is_exact_so_another_case_is_not_it(build_repo, capsys):
    repo = build_repo({**GUIDANCE, "notes": "x", CONFIG: ONE_JOB + declaring("Notes")})
    assert findings(hygiene_output(capsys, repo)[1]) == ["Notes", "notes"]


@pytest.mark.parametrize(
    "path",
    ["docs/", "*", "docs/*", "a?b", "[ab]", "../NOTES", "a/../NOTES", "/etc/NOTES", "", " "],
    ids=["folder", "star", "glob", "question", "bracket", "parent", "inner parent", "absolute", "empty", "blank"],
)
def test_an_invalid_declared_path_cannot_be_judged_and_shows_an_example(build_repo, capsys, path):
    code, output = hygiene_output(capsys, build_repo({**GUIDANCE, CONFIG: ONE_JOB + declaring(path)}))
    assert code == 2
    assert "[[layout.allow]]" in output
    assert 'rule = "file-suffix"' in output


@pytest.mark.parametrize(
    "config",
    [
        declaring("NOTES") + declaring("NOTES", "again"),
        declaring("NOTES", rule="script-name"),
        declaring("NOTES", reason="two\\nlines"),
        declaring("NOTES", reason=" "),
        declaring("NOTES") + "extra = 1\n",
        '[[layout.allow]]\npath = "NOTES"\nrule = "file-suffix"\n',
        '[[layout.allow]]\npath = 3\nrule = "file-suffix"\nreason = "r"\n',
        "[layout]\nignore = []\n",
        '[layout]\nallow = "x"\n',
        "[layout]\nenabled = false\n",
    ],
    ids=["duplicate", "unknown rule", "multi-line reason", "blank reason", "unknown key", "no reason",
         "path not text", "unknown table key", "allow not a list", "no off switch"],
)
def test_a_malformed_layout_table_cannot_be_judged(build_repo, capsys, config):
    code, output = hygiene_output(capsys, build_repo({**GUIDANCE, "NOTES": "x", CONFIG: ONE_JOB + config}))
    assert code == 2
    assert "file-suffix cannot judge" in output
    assert "[[layout.allow]]" in output


def test_the_layout_table_is_separate_from_the_root_table(build_repo, capsys):
    root = '[[root.allow]]\nentry = "NOTES"\nreason = "r"\n'
    repo = build_repo({**GUIDANCE, "NOTES": "x", CONFIG: ONE_JOB + root})
    assert findings(hygiene_output(capsys, repo)[1]) == ["NOTES"]


def test_a_path_with_a_control_character_prints_escaped(build_repo, capsys):
    output = hygiene_output(capsys, build_repo({**GUIDANCE, "bad\x01name": "x"}))[1]
    assert "file-suffix bad\\x01name - " in output
    assert "\x01" not in output


def test_an_index_record_that_cannot_be_read_cannot_be_judged(build_repo, capsys, monkeypatch):
    repo = build_repo(GUIDANCE)
    listing = hygiene._git_listing

    def garbled(path, command):
        done = listing(path, command)
        return subprocess.CompletedProcess(command, 0, b"not-a-mode abc 0\tAGENTS.md\0") if "-s" in command else done

    monkeypatch.setattr(hygiene, "_git_listing", garbled)
    code, output = hygiene_output(capsys, repo)
    assert (code, "file-suffix cannot judge" in output) == (2, True)
