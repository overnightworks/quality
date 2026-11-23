from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from quality import baseline_file, checkout, clones, process, ratchet, repository, spec_citations, workflow_rules
from quality.config import AllowLists
from quality.output import SEPARATOR, counted
from quality.report import CannotJudge, Instruction, Note, NoteEvent

PLANNED_EVENT = {
    NoteEvent.LOWERED: NoteEvent.LOWER,
    NoteEvent.DROPPED: NoteEvent.DROP,
    NoteEvent.MOVED: NoteEvent.MOVE,
    NoteEvent.RECORDED: NoteEvent.RECORD,
}


def _require_whole_history(root: Path) -> None:
    if checkout.is_shallow(root):
        raise CannotJudge("this clone is shallow, so git cannot show the ceiling is new", (checkout.UNSHALLOW_STEP,))


def _require_no_ceiling_in_git(root: Path, tracked: list[str]) -> None:
    if baseline_file.BASELINE_PATH in tracked or checkout.blobs_of(root, baseline_file.BASELINE_PATH):
        raise CannotJudge(f"{baseline_file.BASELINE_PATH} is gone from the tree but git holds it", (ratchet.RESTORE_STEP,))
    _require_whole_history(root)


def _require_no_table_in_git(root: Path, tracked: list[str], table: str) -> None:
    name = baseline_file.BASELINE_PATH
    revisions = [f":{name}"] if name in tracked else []
    revisions += checkout.blobs_of(root, name)
    try:
        held = any(baseline_file.holds_table(checkout.blob(root, revision), table) for revision in revisions)
    except baseline_file.SizeError as error:
        raise CannotJudge(str(error), (ratchet.RESTORE_STEP,)) from error
    if held:
        raise CannotJudge(f"{name} lost its [{table}] table, which git holds", (ratchet.RESTORE_STEP,))
    _require_whole_history(root)


def written(root: Path, fix: ratchet.CeilingFix, write: bool) -> ratchet.CeilingFix:
    """The ceiling rendered, so one no file can hold refuses a dry run too; written, when it changed or its text
    is not the file's, only where `write` is set."""
    try:
        text = baseline_file.render_baseline(fix.ceiling)
        if not fix.changed:
            fix = replace(fix, text_differs=repository.read_text(root, baseline_file.BASELINE_PATH) != text)
        if fix.changed and write:
            repository.write_text(root, baseline_file.BASELINE_PATH, text)
    except (repository.RepositoryFileError, baseline_file.SizeError) as error:
        raise CannotJudge(str(error)) from error
    return fix


def _with_what_they_held(gone: dict[baseline_file.Key, str], held: baseline_file.SizeEntries) -> dict[baseline_file.Key, str]:
    """Each gone entry's reason, then the value the ceiling held."""
    return {key: SEPARATOR.join((why, f"was {ratchet.describe_value(key.metric, held[key])}")) for key, why in gone.items()}


def _ceiling_lineage(root: Path, rerun_command: str) -> tuple[baseline_file.Baseline, ratchet.Renames] | None:
    """The ceiling at its last commit and what git renamed from there to the index; None where no commit holds it."""
    change = checkout.last_change(root, baseline_file.BASELINE_PATH)
    if change is None:
        return None
    if not change.has_parent and checkout.is_shallow(root):
        fact = f"this clone is shallow, so git cannot show where {baseline_file.BASELINE_PATH} last changed"
        raise CannotJudge(fact, (checkout.UNSHALLOW_STEP,))
    where = f"{baseline_file.BASELINE_PATH} at its last commit {change.commit}"
    try:
        text = process.read_blob(root, change.commit, baseline_file.BASELINE_PATH, f"cannot read {where}", f"cannot read {where}")
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error
    if text is None:
        return None
    try:
        committed = baseline_file.load_baseline(text)
    except baseline_file.SizeError as error:
        repair = Instruction(f"commit the repaired {baseline_file.BASELINE_PATH}, then run {rerun_command} again")
        raise CannotJudge(f"the ceiling at its last commit {change.commit}: {error}", (repair,)) from error
    return committed, ratchet.renames_of(checkout.renamed_since(root, change.commit))


def _followed(root: Path, tracked: list[str], rerun_command: str, held: baseline_file.Baseline) -> ratchet.CeilingFix:
    lineage = _ceiling_lineage(root, rerun_command)
    return ratchet.CeilingFix(held, ()) if lineage is None else ratchet.follow_renames(held, *lineage, frozenset(tracked))


def _recording(
    root: Path, tracked: list[str], made: ratchet.CeilingFix, table: str, fact: str, ceiling: baseline_file.Baseline
) -> ratchet.CeilingFix:
    """The fix with a held table recorded in `ceiling`, once git is known never to have held it; a first recording
    carries the table in the ceiling alone, which its row counts, so only a later one is noted."""
    _require_no_table_in_git(root, tracked, table)
    recorded = () if made.first else (Note(baseline_file.BASELINE_PATH, NoteEvent.RECORDED, fact),)
    return replace(made, ceiling=ceiling, changes=(*made.changes, *recorded))


def _with_recorded_jobs(root: Path, tracked: list[str], made: ratchet.CeilingFix) -> ratchet.CeilingFix:
    """The fix with the job table recorded where the ceiling has none, git never held one and a judged job is debt;
    a first recording carries the table in the ceiling alone, which its row counts."""
    if made.ceiling.job_minutes is not None:
        return made
    limits = workflow_rules.recordable_job_limits(root, tracked)
    if not limits:
        return made
    return _recording(root, tracked, made, baseline_file.JOB_MINUTES, counted(len(limits), baseline_file.JOB_LIMIT), replace(made.ceiling, job_minutes=limits))


def _with_spec_citations(
    root: Path, tracked: list[str], sources: dict[str, bytes], allow_lists: AllowLists, made: ratchet.CeilingFix
) -> ratchet.CeilingFix:
    """The fix with the spec table made: a held table keeps only the IDs that are still defined and uncited, and never
    gains one; a ceiling without it records the table where the tree is clean and git never held one, which a first
    recording carries in the ceiling alone, as its row counts."""
    tree = spec_citations.read_tree(root, tracked, sources, allow_lists)
    held = made.ceiling.spec_citations
    if held is not None:
        spec_citations.require_read(tree)
        dropped = {identifier: reason for identifier in sorted(held) if (reason := tree.fall_reason(identifier))}
        changes = tuple(Note(baseline_file.BASELINE_PATH, NoteEvent.DROPPED, reason) for reason in dropped.values())
        kept = held.difference(dropped)
        return replace(made, ceiling=replace(made.ceiling, spec_citations=kept), changes=(*made.changes, *changes))
    uncited = tree.recordable()
    if uncited is None:
        return made
    fact = counted(len(uncited), baseline_file.UNCITED_SPEC_ID)
    return _recording(root, tracked, made, baseline_file.SPEC_CITATIONS, fact, replace(made.ceiling, spec_citations=uncited))


def fix_ceiling(
    root: Path,
    tracked: list[str],
    sources: dict[str, bytes],
    held: baseline_file.Baseline | None,
    allow_lists: Callable[[], AllowLists],
    dry_run: bool,
    rerun_command: str,
) -> ratchet.CeilingFix:
    """The ceiling lowered, or recorded where git never held one; the caller writes it."""
    tree = ratchet.measure(sources)
    ratchet.complete(tree)
    declared = allow_lists()  # read after the tree is judged complete, so that refusal comes first
    generated = declared.generated
    measurements = ratchet.without_generated(tree, generated).measurements
    pairs = clones.find_pairs(sources).pairs
    if held is None:
        _require_no_ceiling_in_git(root, tracked)
        first = ratchet.CeilingFix(baseline_file.Baseline(ratchet.debt(measurements), pairs), (), first=True)
        return _with_spec_citations(root, tracked, sources, declared, _with_recorded_jobs(root, tracked, first))
    if held.clones is None:
        _require_no_table_in_git(root, tracked, baseline_file.CLONES)
    followed = _followed(root, tracked, rerun_command, held)  # renames first, so a renamed file's entries move instead of falling as gone
    gone = ratchet.gone_entries(followed.ceiling.size, measurements, frozenset(tracked), generated)
    if dry_run:
        gone = _with_what_they_held(gone, followed.ceiling.size)
    lowered = ratchet.lower_ceiling(followed.ceiling, measurements, pairs, gone)
    jobs = workflow_rules.lower_job_entries(root, tracked, lowered.ceiling)
    changes = (*followed.changes, *lowered.changes, *jobs.changes)
    recorded_jobs = _with_recorded_jobs(root, tracked, replace(jobs, changes=changes))
    return _with_spec_citations(root, tracked, sources, declared, recorded_jobs)


def reported(fix: ratchet.CeilingFix, planned: bool) -> ratchet.CeilingFix:
    """The fix whose changes the debt rows list: done, or under a dry run planned."""
    if not planned:
        return fix
    return replace(fix, changes=tuple(replace(change, event=PLANNED_EVENT[change.event]) for change in fix.changes))
