"""The `pr-workflows` rule: every workflow a pull request triggers is the gate's caller, CodeQL's, or declared with `[[workflow.allow]]`."""

from collections.abc import Sequence
from pathlib import Path

from quality import checkout, workflow_rules
from quality.config_allowances import WORKFLOW_ALLOWANCE_HEADING, WorkflowAllowance, workflow_allowance_entry
from quality.config_entries import CONFIG_PATH
from quality.report import Example, Finding, FindingEvent, RuleResult
from quality.rules import Rule

FIX_LABEL = "move its jobs behind the gate's caller, or declare it with a reason"


def check_pr_workflows(root: Path, tracked: list[str], allowances: Sequence[WorkflowAllowance]) -> RuleResult:
    """Every tracked workflow a pull request triggers, judged; the extent is how many there are. A declaration that
    names no such workflow, or one that is the gate's or CodeQL's caller, is a finding too."""
    texts = checkout.workflow_texts(root, tracked)
    roots = workflow_rules.pull_request_workflows(texts)
    calls = checkout.workflows_of(texts)
    callers = {call.workflow for call in (*calls.gate, *calls.codeql)}
    declared = {allowance.file for allowance in allowances}
    findings = [_undeclared(path) for path in roots if path not in callers | declared]
    findings += [_unused(file) for file in sorted(declared) if file not in roots or file in callers]
    return RuleResult(sorted(findings, key=lambda finding: finding.path), len(roots), False)


def _undeclared(path: str) -> Finding:
    return Finding(
        Rule.PR_WORKFLOWS, path, None,
        "a pull request triggers it, and it is neither the gate's caller nor CodeQL's",
        Example(FIX_LABEL, workflow_allowance_entry(path)),
        event=FindingEvent.UNLISTED,
    )


def _unused(file: str) -> Finding:
    return Finding(
        Rule.PR_WORKFLOWS, file, None,
        f"{WORKFLOW_ALLOWANCE_HEADING} names no workflow a pull request triggers beyond the gate's caller and CodeQL's",
        f"delete its {WORKFLOW_ALLOWANCE_HEADING} entry from {CONFIG_PATH}",
        event=FindingEvent.UNUSED,
    )
