"""The `render-pass` rule: a covered repository (`catalog_rules.read_catalogs`) that tracks two or more catalogs names, in
`[languages] render_pass`, the job that renders each page once per language; that job must exist in a tracked workflow and be
one of the gate's mandatory jobs. The rule judges presence and gating, never what the job does."""

from collections.abc import Callable
from pathlib import Path, PurePosixPath

from quality import catalog_rules, workflow_rules
from quality.config import CONFIG_PATH, Declarations, RenderPass
from quality.report import Finding, FindingEvent, RuleResult
from quality.rules import Rule

CATALOG_SUFFIX = ".po"
MINIMUM_CATALOGS = 2
UNDECLARED_FIX = "name the job that renders each page once per language in [languages] render_pass"
NOT_A_JOB_FIX = "name a job a tracked workflow holds"


def _catalogs(tracked: list[str], directory: PurePosixPath) -> list[str]:
    """The tracked `.po` files that lie directly in the directory."""
    return [name for name in tracked if PurePosixPath(name).parent == directory and PurePosixPath(name).suffix == CATALOG_SUFFIX]


def _finding(fact: str, fix: str) -> Finding:
    return Finding(Rule.RENDER_PASS, CONFIG_PATH, None, fact, fix, event=FindingEvent.MISSING)


def _unmet(root: Path, tracked: list[str], render_pass: RenderPass, catalog_count: int) -> Finding | None:
    """The first of the three missing findings that applies, None when the declaration is held."""
    job = render_pass.job
    if job is None:
        return _finding(f"{catalog_count} catalogs and no render pass declared", UNDECLARED_FIX)
    if job not in workflow_rules.job_names(root, tracked):
        return _finding(f'render pass "{job}" is no job of a tracked workflow', NOT_A_JOB_FIX)
    if job not in render_pass.mandatory_jobs:
        return _finding(f'render pass "{job}" is not a mandatory job', f'add "{job}" to [gate] mandatory_jobs')
    return None


def check_render_pass(root: Path, tracked: list[str], declarations: Callable[[], Declarations]) -> RuleResult:
    """A covered repository with two or more catalogs and a declared render-pass job that a tracked workflow holds and the
    gate makes mandatory gets no row; else one `missing` finding at the config. A repository with fewer catalogs, or none
    covered, is not judged, its config is not read and no workflow is read for it."""
    catalogs = catalog_rules.read_catalogs(root, tracked)
    if catalogs is None:
        return RuleResult([], 0, False)
    count = len(_catalogs(tracked, catalogs.directory()))
    if count < MINIMUM_CATALOGS:
        return RuleResult([], 0, False)
    unmet = _unmet(root, tracked, declarations().render_pass, count)
    return RuleResult([] if unmet is None else [unmet], count, False)
