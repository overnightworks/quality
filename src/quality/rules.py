from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Self

FIRST_RELEASE = "v0.1.0"
RATCHET_RELEASE = "v0.3.0"
CLONE_RELEASE = "v0.4.0"
CLONE_TABLE_RELEASE = "v0.5.0"


class Switch(Enum):
    """What decides whether a gate that knows a rule enforces it."""

    ALWAYS = "always"
    SIZE_BASELINE = "a size baseline"
    IMPORT_LINTER_CONFIG = "an import-linter config"
    CLONE_TABLE = "a clone table"
    JUDGED_PROJECT = "a judged project"
    REPORT_ONLY = "report only"


@dataclass(frozen=True)
class SwitchChange:
    """From `release` on (None: main only) `switch` decides what the gate does with the rule."""

    release: str | None
    switch: Switch


@dataclass(frozen=True)
class RuleRelease:
    """A rule of the door, the first gate release that judges it (None: main only), what switches it on and how that
    changes at a later release."""

    rule: str
    first_release: str | None
    switch: Switch
    switched: SwitchChange | None = None


class Engines(Enum):
    """Whether a rule is judged only with the engines (`--engines`)."""

    NOT_NEEDED = "not needed"
    NEEDED = "needed"


class Rule(StrEnum):
    """Every rule quality judges, in run order, with whether it needs the engines and its release facts."""

    engines: Engines
    release: RuleRelease

    ENV_FILE = "env-file", Engines.NOT_NEEDED, FIRST_RELEASE, Switch.ALWAYS
    COMPOSE_PORT = "compose-port", Engines.NOT_NEEDED, FIRST_RELEASE, Switch.ALWAYS
    SIZE = "size", Engines.NOT_NEEDED, RATCHET_RELEASE, Switch.SIZE_BASELINE
    CLONE = (
        "clone", Engines.NOT_NEEDED, CLONE_RELEASE, Switch.REPORT_ONLY, SwitchChange(CLONE_TABLE_RELEASE, Switch.CLONE_TABLE)
    )
    IMPORT_CONTRACT = "import-contract", Engines.NOT_NEEDED, RATCHET_RELEASE, Switch.IMPORT_LINTER_CONFIG
    CLAUDE_MD = "claude-md", Engines.NOT_NEEDED, RATCHET_RELEASE, Switch.ALWAYS
    ROOT_LAYOUT = "root-layout", Engines.NOT_NEEDED, None, Switch.ALWAYS
    FILE_SUFFIX = "file-suffix", Engines.NOT_NEEDED, None, Switch.ALWAYS
    SCRIPT_NAME = "script-name", Engines.NOT_NEEDED, None, Switch.ALWAYS
    SCRIPT_HEADER = "script-header", Engines.NOT_NEEDED, None, Switch.ALWAYS
    TEST_LAYOUT = "test-layout", Engines.NOT_NEEDED, None, Switch.JUDGED_PROJECT
    TEST_IMPORT = "test-import", Engines.NOT_NEEDED, None, Switch.ALWAYS
    PYTHON_FLOOR = "python-floor", Engines.NOT_NEEDED, None, Switch.ALWAYS
    PYTHON_SECURITY = "python-security", Engines.NOT_NEEDED, None, Switch.ALWAYS
    PYTHON_PRINT = "python-print", Engines.NOT_NEEDED, None, Switch.ALWAYS
    LEAK = "secret", Engines.NEEDED, FIRST_RELEASE, Switch.ALWAYS
    WORKFLOW_LINT = "workflow-lint", Engines.NEEDED, FIRST_RELEASE, Switch.ALWAYS
    WORKFLOW_SECURITY = "workflow-security", Engines.NEEDED, FIRST_RELEASE, Switch.ALWAYS
    ADVISORY = "advisory", Engines.NEEDED, RATCHET_RELEASE, Switch.ALWAYS

    def __new__(
        cls, rule_id: str, engines: Engines, first_release: str | None, switch: Switch, switched: SwitchChange | None = None
    ) -> Self:
        member = str.__new__(cls, rule_id)
        member._value_ = rule_id
        member.engines = engines
        member.release = RuleRelease(rule_id, first_release, switch, switched)
        return member
