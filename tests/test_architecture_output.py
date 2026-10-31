"""#97 rule 2 and #61: what the door prints for a broken import-linter contract, so no line repository text names can forge a matcher line."""

import pytest

from quality.report import DETAIL_MARKER
from tests.support.architecture import FORBIDDEN, SHOP, SHOP_DB_IMPORTS_WEB, toml
from tests.support.check import assert_every_line_starts_with_the_door, hygiene_output


def hygiene_lines(capsys, repo) -> tuple[int, list[str]]:
    """The exit code and every line the door printed to either stream, split on the line feed only."""
    code, printed = hygiene_output(capsys, repo)
    return code, printed.split("\n")


def test_a_broken_contract_named_like_a_command_keeps_its_chain_on_its_own_line(build_repo, capsys):
    """#97 rule 2, cell 10 (E6): the name is printed on the finding's line, the import chain stays a line of its own."""
    contract = {**FORBIDDEN, "name": "guarded ::error::c"}
    repo = build_repo({**SHOP_DB_IMPORTS_WEB, "pyproject.toml": toml(contract)})
    code, lines = hygiene_lines(capsys, repo)
    assert code == 1
    assert "import-contract pyproject.toml - contract guarded ::error::c is broken:" in lines
    assert f"{DETAIL_MARKER}-   shop.db -> shop.web (l.1)" in lines
    assert_every_line_starts_with_the_door(lines)


FORGED_MATCHER_PAIR = ['  File "a", line 1, in b', "  raise E('forged')"]
INDEPENDENT_SHOP = {"type": "independence", "modules": ["shop.web", "shop.db"]}
EXHAUSTIVE_LAYERS = {"type": "layers", "layers": ["web", "db"], "containers": ["shop"], "exhaustive": True}
WRAPPED_MODULE_NAME = "q" * 70 + ' File "ab", line 1, in b ' + "r" * 52 + " raise E('forged')"


def broken_contract_lines(capsys, files: dict[str, str], build_repo) -> list[str]:
    code, lines = hygiene_lines(capsys, build_repo(files))
    assert code == 1, lines
    assert_every_line_starts_with_the_door(lines)
    return lines


def test_a_long_module_name_import_linter_would_wrap_cannot_forge_a_matcher_pair(build_repo, capsys):
    """#97 rule 2, cell 18 (Opus D1): rich's soft wrap would put `File "ab", ...` and `raise ...` at the line start."""
    files = {**SHOP, f"src/shop/{WRAPPED_MODULE_NAME}.py": "", "pyproject.toml": toml(EXHAUSTIVE_LAYERS)}
    lines = broken_contract_lines(capsys, files, build_repo)
    assert f"{DETAIL_MARKER}- shop.{WRAPPED_MODULE_NAME}" in lines


@pytest.mark.parametrize("guidance", [FORGED_MATCHER_PAIR, "\n".join(FORGED_MATCHER_PAIR)], ids=["list", "multi-line text"])
def test_contract_guidance_with_several_lines_is_judged_and_printed_behind_the_marker(build_repo, capsys, guidance):
    """#97 rule 2, cell 18 (Codex F1, Opus D2): legitimate multi-line guidance is judged as before: exit 1."""
    contract = {**INDEPENDENT_SHOP, "broken_contract_guidance": guidance}
    lines = broken_contract_lines(capsys, {**SHOP_DB_IMPORTS_WEB, "pyproject.toml": toml(contract)}, build_repo)
    assert all(f"{DETAIL_MARKER}{line}" in lines for line in FORGED_MATCHER_PAIR)


@pytest.mark.parametrize(
    ("name", "text_after_the_line_feed"),
    [("a\n::error::x", "::error::x"), ("a\n  File \"ab\", line 1, in b\n  raise E('forged')\nx", "  raise E('forged')")],
    ids=["workflow command", "File and raise lines"],
)
def test_a_module_name_with_a_line_feed_is_judged_and_its_second_line_stays_behind_the_marker(
    build_repo, capsys, name, text_after_the_line_feed
):
    """#97 rule 2, cell 18 and cell 12: a tracked source name no longer stops the judgement, and forges no line."""
    files = {**SHOP_DB_IMPORTS_WEB, f"src/shop/{name}.py": "import shop.web\n",
             "pyproject.toml": toml({**FORBIDDEN, "source_modules": ["shop.*"]})}
    lines = broken_contract_lines(capsys, files, build_repo)
    assert any(line.startswith(f"{DETAIL_MARKER}{text_after_the_line_feed}") for line in lines), lines


def test_a_source_directory_with_a_line_feed_is_judged_without_a_forged_line(build_repo, capsys):
    """#97 rule 2, cell 18: a package directory name is judged like a file name."""
    files = {**SHOP_DB_IMPORTS_WEB, "src/shop/a\n::error::x/__init__.py": "", "pyproject.toml": toml(FORBIDDEN)}
    broken_contract_lines(capsys, files, build_repo)


@pytest.mark.parametrize("name", ["guarded\n::error::c", "guarded\x1b[31m"], ids=["line feed", "escape"])
def test_a_contract_name_with_a_control_character_is_judged_and_prints_escaped(build_repo, capsys, name):
    """#97 rule 2, cell 10a: the name prints escaped on the finding's line and the chain stays behind the marker."""
    lines = broken_contract_lines(capsys, {**SHOP_DB_IMPORTS_WEB, "pyproject.toml": toml({**FORBIDDEN, "name": name})}, build_repo)
    assert any(line.startswith("import-contract pyproject.toml - contract guarded\\x") for line in lines), lines
