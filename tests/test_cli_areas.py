"""A run of `quality check AREA ...` judges, prints, counts and starts only the areas it names."""

import pytest

from quality import check, checkout, cli, pin, ratchet, scanners
from quality.output import Glyph
from quality.report import NoteEvent
from quality.rules import Area
from tests.support.advisories import uv_lock
from tests.support.check import NoteLine, area_block, area_fact, area_row, scanner_rows
from tests.support.door import CHECK_WORD, fixed
from tests.support.python_lint import fake_ruff
from tests.support.scanners import CLEAN_SCANS, fake_gitleaks, fake_scanner, fake_zizmor, flagging, vetting
from tests.support.secrets import FAKE_KEY


def run_check_of(repo, capsys, *words, **keywords) -> tuple[int, str, str]:
    return fixed(capsys, repo, CHECK_WORD, *words, **keywords)


def rows_of(out: str) -> list[str]:
    """The area of each row: the second word of each line that opens with exactly two spaces."""
    return [line.split()[1] for line in out.splitlines() if line.startswith("  ") and not line.startswith("   ")]


def scanned_repo(build_repo):
    return build_repo({".github/workflows/w.yml": "on: push\n", "uv.lock": uv_lock(), "src/a.py": "x = 1\n"})


def recording_scanners(served_scanners, elsewhere):
    """The door's keywords for scanners that note their name in the returned file before they answer clean."""
    calls = elsewhere / "calls"
    for name, answer in CLEAN_SCANS.items():
        fake_scanner(served_scanners, name, f"echo {name} >> {calls}\n{answer}")
    zizmor = fake_zizmor(elsewhere / "zizmor", f"echo {scanners.ZIZMOR} >> {calls}\necho '[]'")
    ruff = fake_ruff(elsewhere, f"echo {scanners.RUFF} >> {calls}\necho '[]'")
    return {"locate_zizmor": zizmor, "locate_ruff": lambda: str(ruff)}, calls


def calls_of(calls) -> list[str]:
    return calls.read_text().splitlines() if calls.exists() else []


@pytest.mark.parametrize(
    ("area", "started"),
    [
        pytest.param(Area.FILES, [], id="files"),
        pytest.param(Area.DEBT, [], id="debt"),
        pytest.param(Area.IMPORTS, [], id="imports"),
        pytest.param(Area.SECRETS, [scanners.GITLEAKS], id="secrets"),
        pytest.param(Area.WORKFLOWS, [scanners.ACTIONLINT, scanners.ZIZMOR], id="workflows"),
        pytest.param(Area.DEPENDENCIES, [scanners.OSV_SCANNER], id="dependencies"),
        pytest.param(Area.CODE, [scanners.RUFF], id="code"),
    ],
)
def test_a_named_area_starts_only_its_scanners_and_prints_only_its_row(build_repo, served_scanners, elsewhere, capsys, area, started):
    """CHECK-3, CHECK-6: a named area starts only its own scanners and prints only its own row."""
    keywords, calls = recording_scanners(served_scanners, elsewhere)
    _, out, _ = run_check_of(scanned_repo(build_repo), capsys, area, **keywords)
    assert (rows_of(out), calls_of(calls)) == ([area], started)


@pytest.mark.parametrize(
    ("words", "other", "rows"),
    [
        pytest.param(list(Area), [], ["files", "debt", "imports", "secrets", "workflows", "dependencies", "code"], id="every area named"),
        pytest.param(["code", "files"], ["files", "code"], ["files", "code"], id="reversed"),
        pytest.param(["files", "code", "files"], ["files", "code"], ["files", "code"], id="repeated"),
    ],
)
def test_a_run_of_named_areas_equals_the_same_request_spelt_otherwise(build_repo, served_scanners, elsewhere, capsys, words, other, rows):
    """CHECK-3, ROW-8: areas named in any order, repeated or all at once judge each area once, in the order of the
    rows, which the cell `every area named` spells out as the seven names."""
    keywords, _ = recording_scanners(served_scanners, elsewhere)
    repo = scanned_repo(build_repo)
    _, named_out, _ = run_check_of(repo, capsys, *words, **keywords)
    _, other_out, _ = run_check_of(repo, capsys, *other, **keywords)
    assert (rows_of(named_out), named_out) == (rows, other_out)


@pytest.mark.parametrize(
    ("words", "code", "last_line"),
    [
        pytest.param(["files"], 1, "! 2 findings in 1 area · <1s", id="files"),
        pytest.param(["secrets"], 0, "✓ clean · 1 area · <1s", id="secrets"),
        pytest.param(["debt"], 0, "✓ clean · 1 area · debt measured, not enforced · <1s", id="debt"),
        pytest.param(["imports"], 0, "✓ clean · 1 area · imports not enforced · <1s", id="imports"),
        pytest.param(["debt", "imports"], 0, "✓ clean · 2 areas · debt measured, not enforced · imports not enforced · <1s", id="debt and imports"),
        pytest.param(["files", "secrets"], 1, "! 2 findings in 1 area · 1 area clean · <1s", id="files and secrets"),
    ],
)
def test_a_selected_run_counts_what_it_judged_and_nothing_else(build_repo, capsys, words, code, last_line):
    """CHECK-7, CHECK-8, CLOSING-8, CLOSING-10: the verdict counts the named areas only, says what those areas only
    measured, and the exit is the worst of theirs."""
    done, out, _ = run_check_of(build_repo({".env": "A=1\n"}), capsys, *words)
    assert (done, out.splitlines()[-1]) == (code, last_line)


def refused_stderr(areas_refused: str) -> str:
    return f"error: {areas_refused} could not check\nnext: {cli.REFUSED_AREAS_NEXT.text}\n"


@pytest.mark.parametrize(
    ("files", "words", "code", "has_refused_row", "stderr"),
    [
        pytest.param({"compose.yaml": "services: ["}, ["debt"], 0, False, "", id="a refused files rule beside debt"),
        pytest.param({"compose.yaml": "services: ["}, ["files"], 2, True, refused_stderr("1 of 1 area"), id="the refused area alone"),
        pytest.param({"compose.yaml": "services: ["}, ["files", "debt"], 2, True, refused_stderr("1 of 2 areas"), id="the refused area and another"),
        pytest.param({"quality/config.toml": "[secrets]\nignore = []\n"}, ["imports"], 0, False, "", id="a refused config beside imports"),
        pytest.param({"quality/config.toml": "[secrets]\nignore = []\n"}, ["secrets"], 2, True, refused_stderr("1 of 1 area"), id="a refused config read by the area"),
    ],
)
def test_a_refusal_outside_the_named_areas_changes_nothing(build_repo, capsys, files, words, code, has_refused_row, stderr):
    """CHECK-6, CHECK-7, CHECK-8: a refusal outside the named areas changes no row, count or exit."""
    done, out, err = run_check_of(build_repo(files), capsys, *words)
    assert (done, "✗" in out, err) == (code, has_refused_row, stderr)


@pytest.mark.parametrize(
    ("area", "unneeded"),
    [
        pytest.param(Area.IMPORTS, ("allow_lists", "modes", "sources"), id="imports"),
        pytest.param(Area.WORKFLOWS, ("allow_lists", "modes", "sources"), id="workflows"),
        pytest.param(Area.SECRETS, ("modes", "sources"), id="secrets"),
        pytest.param(Area.DEPENDENCIES, ("modes", "sources"), id="dependencies"),
        pytest.param(Area.CODE, ("allow_lists", "sources"), id="code"),
        pytest.param(Area.DEBT, ("modes",), id="debt"),
        pytest.param(Area.FILES, ("sources",), id="files"),
    ],
)
def test_a_named_area_starts_no_shared_read_it_does_not_need(build_repo, served_scanners, elsewhere, capsys, monkeypatch, area, unneeded):
    """CHECK-6: a named area starts none of the shared reads that only an area not named needs."""
    def never_read(*_arguments, **_keywords):
        pytest.fail("a read this area does not need was started")

    reads = {"allow_lists": (check, "read_allow_lists"), "modes": (checkout, "index_modes"), "sources": (ratchet, "read_sources")}
    for name in unneeded:
        monkeypatch.setattr(*reads[name], never_read)
    keywords, _ = recording_scanners(served_scanners, elsewhere)
    _, out, _ = run_check_of(scanned_repo(build_repo), capsys, area, **keywords)
    assert rows_of(out) == [area]


@pytest.mark.parametrize(
    ("words", "exit_of_actionlint", "stderr_line", "vet_answer", "code", "shown", "rows", "row"),
    [
        pytest.param(
            ["workflows", "-v"], 0, "a harmless note", 'echo "[]" > "$report"; exit 0', 0, "a harmless note",
            [(scanners.ACTIONLINT, "0", ("a harmless note",)), (scanners.ZIZMOR, "0", ())], (Glyph.CLEAN, False),
            id="cleared",
        ),
        pytest.param(
            ["workflows", "-v"], 0, f"aws_access_key_id = {FAKE_KEY}", flagging(FAKE_KEY), 0, scanners.NOT_CLEARED,
            [
                (scanners.ACTIONLINT, f"0 · 1 stderr line withheld · {scanners.NOT_CLEARED}", ()),
                (scanners.ZIZMOR, "0", ()),
            ],
            (Glyph.CLEAN, False),
            id="not cleared",
        ),
        pytest.param(
            ["workflows"], 3, "a harmless note", 'echo "[]" > "$report"; exit 0', 2, "a harmless note",
            [], (Glyph.COULD_NOT_CHECK, True),
            id="refused cleared",
        ),
        pytest.param(
            ["workflows"], 3, f"aws_access_key_id = {FAKE_KEY}", flagging(FAKE_KEY), 2, scanners.NOT_CLEARED,
            [], (Glyph.COULD_NOT_CHECK, True),
            id="refused not cleared",
        ),
    ],
)
def test_a_selected_scanner_vets_its_stderr_without_judging_another_area(
    build_repo, served_scanners, elsewhere, capsys,
    words, exit_of_actionlint, stderr_line, vet_answer, code, shown, rows, row,
):
    """VERBOSE-3, VERBOSE-8, VERBOSE-12, VERBOSE-20, VERBOSE-21, CHECK-6: a named area's scanner shows its cleared
    stderr, counts an uncleared one, starts one vet per finished scan and refusal, and no other area runs."""
    calls = elsewhere / "calls"
    fake_gitleaks(
        served_scanners,
        on_scan=vetting(f"echo {scanners.GITLEAKS} stdin >> {calls}; {vet_answer}", f'echo {scanners.GITLEAKS} git >> {calls}; echo "[]" > "$report"'),
    )
    fake_scanner(served_scanners, scanners.ACTIONLINT, f"echo '[]'\necho '{stderr_line}' >&2\nexit {exit_of_actionlint}")
    keywords = {"locate_zizmor": fake_zizmor(elsewhere / "zizmor", "echo '[]'")}
    done, out, err = run_check_of(scanned_repo(build_repo), capsys, *words, **keywords)
    assert (done, rows_of(out), calls_of(calls), shown in err, FAKE_KEY in out + err) == (
        code,
        [Area.WORKFLOWS],
        [f"{scanners.GITLEAKS} stdin"],
        False,
        False,
    )
    glyph = area_row(out, Area.WORKFLOWS).glyph
    assert (scanner_rows(out, Area.WORKFLOWS), glyph, shown in area_fact(out, Area.WORKFLOWS)) == (rows, *row)


@pytest.mark.parametrize(
    ("area", "started"),
    [
        pytest.param(Area.SECRETS, [scanners.GITLEAKS], id="secrets"),
        pytest.param(Area.WORKFLOWS, [scanners.ACTIONLINT, scanners.ZIZMOR], id="workflows"),
    ],
)
def test_under_v_a_named_area_shows_only_its_scanners(build_repo, served_scanners, elsewhere, capsys, area, started):
    """VERBOSE-1, CHECK-6: under -v a named area shows only the rows of its own scanners."""
    keywords, _ = recording_scanners(served_scanners, elsewhere)
    _, out, _ = run_check_of(scanned_repo(build_repo), capsys, area, "-v", **keywords)
    shown = [line.location for line in area_block(out, area) if isinstance(line, NoteLine) and line.row.event is NoteEvent.EXITED]
    assert (rows_of(out), shown) == ([area], started)


@pytest.mark.integration
def test_a_named_area_outside_a_repository_is_the_start_failure(elsewhere, capsys):
    named = run_check_of(elsewhere, capsys, "secrets", judging=pin.judge_here)
    whole = run_check_of(elsewhere, capsys, judging=pin.judge_here)
    assert (named[:2], named[2] == whole[2], "not inside a git work tree" in named[2]) == ((2, ""), True, True)
