"""Workflow text whose `run:` script stands at a known line, for the rules that read a step's script."""

import json

WORKFLOW = ".github/workflows/w.yml"
BLOCK_LINE = 10
QUOTED_LINE = 8


def steps_of(on: str = "push") -> str:
    """A workflow up to its `steps:` key (line 7); the next line is line 8."""
    return f"on: {on}\npermissions: {{}}\njobs:\n  j:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n"


def block_run(*lines: str, on: str = "push", style: str = "|") -> str:
    """One step whose `run:` block, in the scalar `style`, is line 9 and whose script starts on line 10."""
    return steps_of(on) + f"      - name: t\n        run: {style}\n" + "".join(f"          {line}\n" for line in lines)


def quoted_run(line: str, on: str = "push") -> str:
    """One step whose quoted `run:` value is line 8."""
    return steps_of(on) + f"      - run: {json.dumps(line)}\n"


def folded_run(*lines: str, on: str = "push") -> str:
    """One step whose `run: >` is line 9 and whose value starts on line 10."""
    return steps_of(on) + "      - name: t\n        run: >\n" + "".join(f"          {line}\n" for line in lines)
