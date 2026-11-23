"""Words one import-linter contract's result as findings and notes: a broken contract with the lines import-linter renders
for it, an unmatched `ignore_imports` entry, and the warnings a contract gives."""

import re

from importlinter.application import output
from importlinter.domain.contract import Contract, ContractCheck
from importlinter.domain.helpers import MissingImport

from quality.report import Finding, FindingEvent, Note, NoteEvent, RuleResult
from quality.rules import Rule

UNMATCHED_IGNORE = re.compile(r"No matches for ignored import (?P<expression>.+)\.")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BROKEN_FIX = "change the import or the contract"
STALE_IGNORE_FIX = "delete it from the contract's ignore_imports"


def unmatched_ignores(contract_name: str, config_name: str, stale: MissingImport) -> RuleResult:
    """One `unused` finding for each ignore of the contract that matches no import."""
    return RuleResult([
        Finding(Rule.IMPORT_CONTRACT, config_name, None, _ignore_fact(contract_name, line, _ignored_expression(line)), STALE_IGNORE_FIX, event=FindingEvent.UNUSED)
        for line in str(stale).splitlines()
    ], 1, False)


def judged(contract: Contract, config_name: str, check: ContractCheck) -> RuleResult:
    """The result of the contract that was checked: its warnings as notes, and a broken contract as one finding."""
    notes = tuple(_warning_note(contract.name, config_name, warning) for warning in check.warnings)
    if check.kept:
        return RuleResult([], 1, False, notes)
    broken = Finding(
        Rule.IMPORT_CONTRACT, config_name, None, f"contract {contract.name}", BROKEN_FIX,
        detail=_rendered(contract, check), event=FindingEvent.BROKEN,
    )
    return RuleResult([broken], 1, False, notes)


def _ignored_expression(message: str) -> str | None:
    """The expression an unmatched-ignore message names, or None for any other message."""
    match = UNMATCHED_IGNORE.fullmatch(message)
    return None if match is None else match["expression"]


def _ignore_fact(contract_name: str, message: str, expression: str | None) -> str:
    """What a message says of a contract: the expression it ignores, else import-linter's own words."""
    return f"contract {contract_name} {message}" if expression is None else f"contract {contract_name} ignores {expression}"


def _warning_note(contract_name: str, config_name: str, message: str) -> Note:
    """An unmatched ignore at `warn` is an `unused` note, any other warning of the contract a `warning` note."""
    expression = _ignored_expression(message)
    event = NoteEvent.WARNING if expression is None else NoteEvent.UNUSED
    return Note(config_name, event, _ignore_fact(contract_name, message, expression))


def _rendered(contract: Contract, check: ContractCheck) -> tuple[str, ...]:
    """The broken contract as import-linter renders it, as plain text, one entry per line. Rich's soft_wrap mode is on,
    so rich never cuts a line at the console width; a line feed in repository text still splits a print, and the door
    prints every line behind its marker, so repository text never starts a printed line."""
    soft_wrap_before = output.console.soft_wrap
    output.console.soft_wrap = True
    try:
        with output.console.capture() as captured:
            contract.render_broken_contract(check)
    finally:
        output.console.soft_wrap = soft_wrap_before
    return tuple(ANSI_ESCAPE.sub("", captured.get()).strip().split("\n"))
