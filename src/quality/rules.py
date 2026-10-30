from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Self


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


RELEASE_0_1 = "v0.1.0"
RELEASE_0_3 = "v0.3.0"
RELEASE_0_7 = "v0.7.0"


class Area(StrEnum):
    """What a run reports on, in the order its rows stand; every rule belongs to one."""

    FILES = "files"
    DEBT = "debt"
    IMPORTS = "imports"
    SECRETS = "secrets"
    WORKFLOWS = "workflows"
    DEPENDENCIES = "dependencies"
    CODE = "code"


class Rule(StrEnum):
    """Every rule quality judges, in run order, with its release facts."""

    area: Area
    release: RuleRelease

    ENV_FILE = "env-file", Area.FILES, RELEASE_0_1, Switch.ALWAYS
    COMPOSE_PORT = "compose-port", Area.FILES, RELEASE_0_1, Switch.ALWAYS
    SIZE = "size", Area.DEBT, RELEASE_0_3, Switch.SIZE_BASELINE
    CLONE = "clone", Area.DEBT, "v0.4.0", Switch.REPORT_ONLY, SwitchChange("v0.5.0", Switch.CLONE_TABLE)
    IMPORT_CONTRACT = "import-contract", Area.IMPORTS, RELEASE_0_3, Switch.IMPORT_LINTER_CONFIG
    CLAUDE_MD = "claude-md", Area.FILES, RELEASE_0_3, Switch.ALWAYS
    ROOT_LAYOUT = "root-layout", Area.FILES, "v0.6.0", Switch.ALWAYS
    FILE_SUFFIX = "file-suffix", Area.FILES, RELEASE_0_7, Switch.ALWAYS
    SCRIPT_NAME = "script-name", Area.FILES, RELEASE_0_7, Switch.ALWAYS
    SCRIPT_HEADER = "script-header", Area.FILES, RELEASE_0_7, Switch.ALWAYS
    TEST_LAYOUT = "test-layout", Area.FILES, RELEASE_0_7, Switch.JUDGED_PROJECT
    TEST_IMPORT = "test-import", Area.FILES, RELEASE_0_7, Switch.ALWAYS
    PYTHON_FLOOR = "python-floor", Area.FILES, "v0.8.0", Switch.ALWAYS
    PYTHON_SECURITY = "python-security", Area.CODE, "v0.9.0", Switch.ALWAYS
    PYTHON_PRINT = "python-print", Area.CODE, "v0.9.0", Switch.ALWAYS
    LEAK = "secret", Area.SECRETS, RELEASE_0_1, Switch.ALWAYS
    WORKFLOW_LINT = "workflow-lint", Area.WORKFLOWS, RELEASE_0_1, Switch.ALWAYS
    WORKFLOW_SECURITY = "workflow-security", Area.WORKFLOWS, RELEASE_0_1, Switch.ALWAYS
    ADVISORY = "advisory", Area.DEPENDENCIES, RELEASE_0_3, Switch.ALWAYS

    def __new__(
        cls, rule_id: str, area: Area, first_release: str | None, switch: Switch, switched: SwitchChange | None = None
    ) -> Self:
        member = str.__new__(cls, rule_id)
        member._value_ = rule_id
        member.area = area
        member.release = RuleRelease(rule_id, first_release, switch, switched)
        return member
