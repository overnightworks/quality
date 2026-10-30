import datetime
import json
import tomllib
from dataclasses import dataclass
from typing import Literal

CONFIG = "quality/config.toml"
GATE = '[gate]\nmandatory_jobs = ["checks"]\n'
HASH = "a" * 64

type Loader = Literal["allow_lists", "gate", "staged"]
WHOLE: tuple[Loader, ...] = ("allow_lists", "gate")
WHOLE_AND_STAGED: tuple[Loader, ...] = ("allow_lists", "gate", "staged")
DIRECTORY = "a directory stands where the config file should"

ITEM_NEED = "item as #<number> or <owner>/<repo>#<number>"
DATE_NEED = "date as a TOML date without a time"
ONE_LINE_NEED = "a non-empty one-line reason"
FILE_PATH_NEED = "path as the exact tracked path of one file: no folder, pattern, .. or absolute path"
KNOWN_KEYS = (
    "only [gate] mandatory_jobs, [gate] legacy, [[gate.loosened]], [secrets] allow, [[advisories.allow]], "
    "[[root.allow]], [[layout.allow]], [[python.allow]], [[compose.allow]] and [[size.generated]] exist"
)


@dataclass(frozen=True)
class Refusal:
    """One way the config is refused: the text, who reads it, the whole fact and the name of the example constant."""

    id: str
    text: str | None
    fact: str
    example: str | None
    loaders: tuple[Loader, ...] = WHOLE

    @property
    def reaches_hygiene(self) -> bool:
        return "allow_lists" in self.loaders


def toml_value(value: object) -> str:
    if isinstance(value, datetime.date):
        return value.isoformat()
    return json.dumps(value)


def entry_text(table: str, fields: dict[str, object]) -> str:
    """One `[[table]]` entry; a field whose value is None is left out."""
    body = "".join(f"{key} = {toml_value(value)}\n" for key, value in fields.items() if value is not None)
    return f"[[{table}]]\n{body}"


@dataclass(frozen=True)
class ArrayTable:
    """An array of tables the config holds: its valid entry, the ways an entry fails and how a duplicate reads."""

    name: str
    example: str
    defaults: dict[str, object]
    keys_need: str
    needs: tuple[tuple[dict[str, object], str], ...]
    duplicate: str
    duplicate_example: bool = True

    @property
    def header(self) -> str:
        return f"[[{self.name}]]"

    def entry(self, **changes: object) -> str:
        return entry_text(self.name, self.defaults | changes)


DATE = datetime.date(2026, 10, 21)
ARRAY_TABLES = (
    ArrayTable(
        "gate.loosened", "LOOSENED_EXAMPLE",
        {"what": "mandatory job removed", "item": "#1", "date": DATE, "reason": "ruled"},
        "exactly a what, an item, a date and a reason",
        (({"what": "  "}, "a non-empty what"), ({"item": "siehe Chat"}, ITEM_NEED), ({"date": "x"}, DATE_NEED),
         ({"reason": ""}, ONE_LINE_NEED)),
        'declares what = "mandatory job removed" twice; one entry per loosening',
    ),
    ArrayTable(
        "advisories.allow", "ADVISORY_ALLOWANCE_EXAMPLE",
        {"id": "GHSA-aaaa-bbbb-cccc", "lock": "uv.lock", "until": DATE, "date": DATE, "item": "#1", "reason": "ruled"},
        "exactly an id, a lock, an until, a date, an item and a reason",
        (({"id": ""}, "a non-empty one-line id, lock and reason"), ({"item": "siehe Chat"}, ITEM_NEED),
         ({"until": "2027-01-31"}, "until and date as TOML dates without a time"),
         ({"reason": "two\nlines"}, "a non-empty one-line id, lock and reason")),
        'allows id = "GHSA-aaaa-bbbb-cccc" in lock = "uv.lock" twice; one entry per advisory and lock file',
        duplicate_example=False,
    ),
    ArrayTable(
        "root.allow", "ROOT_ALLOWANCE_EXAMPLE", {"entry": "plans/", "reason": "ruled"},
        "exactly an entry and a reason",
        (({"entry": "a/b"}, "entry as one name directly in the root, a directory with a trailing /, not a path"),
         ({"reason": ""}, ONE_LINE_NEED)),
        'declares entry = "plans/" twice; one entry per root entry',
    ),
    ArrayTable(
        "layout.allow", "LAYOUT_ALLOWANCE_EXAMPLE", {"path": "NOTES", "rule": "file-suffix", "reason": "ruled"},
        "exactly a path, a rule and a reason",
        (({"path": "dir/"}, FILE_PATH_NEED),
         ({"rule": "size"}, "rule as one of file-suffix, script-header, script-name, test-import, test-layout"),
         ({"rule": [1]}, "rule as one of file-suffix, script-header, script-name, test-import, test-layout"),
         ({"reason": ""}, ONE_LINE_NEED)),
        'declares path = "NOTES" for rule = "file-suffix" twice; one declaration per rule and path',
    ),
    ArrayTable(
        "python.allow", "PYTHON_ALLOWANCE_EXAMPLE", {"path": "speech/pyproject.toml", "reason": "ruled"},
        "exactly a path and a reason",
        (({"path": "speech/"}, "path as the exact tracked path of one pyproject.toml, no folder, pattern or .."),
         ({"reason": ""}, ONE_LINE_NEED)),
        'declares path = "speech/pyproject.toml" twice; one entry per path',
    ),
    ArrayTable(
        "compose.allow", "COMPOSE_ALLOWANCE_EXAMPLE", {"file": "compose.yaml", "service": "web", "reason": "ruled"},
        "exactly a file, a service and a reason",
        (({"file": "dir/"}, "file as the exact tracked path of one compose file: no folder, pattern, .. or absolute path"),
         ({"service": ""}, "service as the one-line name of a service in that file"), ({"reason": ""}, ONE_LINE_NEED)),
        'declares service = "web" of file = "compose.yaml" twice; one declaration per file and service',
    ),
    ArrayTable(
        "size.generated", "GENERATED_FILE_EXAMPLE",
        {"path": "types.ts", "job": "checks", "item": "#1", "date": DATE, "reason": "ruled"},
        "exactly a path, a job, an item, a date and a reason",
        (({"path": "dir/"}, FILE_PATH_NEED),
         ({"job": "other"}, "job as one of [gate] mandatory_jobs, the job whose generator check proves the file"),
         ({"item": "siehe Chat"}, ITEM_NEED), ({"date": "x"}, DATE_NEED), ({"reason": ""}, ONE_LINE_NEED)),
        'declares path = "types.ts" twice; one entry per file',
    ),
)
NOT_A_LIST_TEXT = {
    "gate.loosened": GATE + 'loosened = "x"\n', "advisories.allow": '[advisories]\nallow = "x"\n',
    "root.allow": '[root]\nallow = "x"\n', "layout.allow": '[layout]\nallow = "x"\n',
    "python.allow": '[python]\nallow = "x"\n', "compose.allow": '[compose]\nallow = "x"\n',
    "size.generated": '[size]\ngenerated = "x"\n',
}


def secret_entry(**changes: object) -> str:
    fields = {"rule": "aws-access-token", "file": "tests/fixture.py", "secret_sha256": HASH, "reason": "fixture value"}
    inline = ", ".join(f"{key} = {toml_value(value)}" for key, value in (fields | changes).items() if value is not None)
    return "{ " + inline + " }"


def secrets_text(*entries: str) -> str:
    return f"[secrets]\nallow = [{', '.join(entries)}]\n"


SECRET_NEEDS = (
    ({"extra": 1}, "exactly a rule, a file, a secret_sha256 and a reason (the old fingerprint form is gone)", "ALLOWANCE_EXAMPLE"),
    ({"rule": ""}, "a non-empty rule, file and reason", "ALLOWANCE_EXAMPLE"),
    ({"secret_sha256": "a" * 63}, "secret_sha256 as 64 lowercase hex digits", "ALLOWANCE_EXAMPLE"),
    ({"reason": "two\nlines"}, "a one-line reason", None),
)
TABLE_VALUES = (("gate", "EXAMPLE"), ("secrets", "ALLOWANCE_EXAMPLE"), ("advisories", "ADVISORY_ALLOWANCE_EXAMPLE"),
                       ("root", "ROOT_ALLOWANCE_EXAMPLE"), ("layout", "LAYOUT_ALLOWANCE_EXAMPLE"),
                       ("python", "PYTHON_ALLOWANCE_EXAMPLE"), ("compose", "COMPOSE_ALLOWANCE_EXAMPLE"),
                       ("size", "GENERATED_FILE_EXAMPLE"))


def parser_message(text: str) -> str:
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        return str(error)
    raise AssertionError("the text is valid TOML")


def _array_table_refusals(table: ArrayTable) -> list[Refusal]:
    where = f"{CONFIG} {table.header}"
    refusals = [
        Refusal(f"{table.name} not a list", NOT_A_LIST_TEXT[table.name], f"{CONFIG} needs {table.header} as an array of tables", table.example),
        Refusal(f"{table.name} keys", GATE + table.entry(extra=1), f"{where} entry 1 needs {table.keys_need}", table.example),
        Refusal(
            f"{table.name} duplicate", GATE + table.entry() * 2, f"{where} {table.duplicate}",
            table.example if table.duplicate_example else None,
        ),
    ]
    for changes, need in table.needs:
        refusals.append(Refusal(f"{table.name} {need}", GATE + table.entry(**changes), f"{where} entry 1 needs {need}", table.example))
    return refusals


def _secret_refusals() -> list[Refusal]:
    where = f"{CONFIG} [secrets] allow"
    refusals = [
        Refusal("secrets not a list", '[secrets]\nallow = "x"\n', f"{CONFIG} needs [secrets] allow as a list", "ALLOWANCE_EXAMPLE", WHOLE_AND_STAGED),
        Refusal(
            "secrets duplicate", secrets_text(secret_entry(), secret_entry(reason="again")),
            f'{where} lists rule = "aws-access-token" in file = "tests/fixture.py" twice for one value', None, WHOLE_AND_STAGED,
        ),
        Refusal("secrets bare string", f'[secrets]\nallow = ["{HASH}"]\n', f"{where} entry 1 needs {SECRET_NEEDS[0][1]}", "ALLOWANCE_EXAMPLE", WHOLE_AND_STAGED),
        Refusal(
            "secrets second entry", secrets_text(secret_entry(), secret_entry(rule="")),
            f"{where} entry 2 needs {SECRET_NEEDS[1][1]}", "ALLOWANCE_EXAMPLE", WHOLE_AND_STAGED,
        ),
    ]
    for changes, need, example in SECRET_NEEDS:
        refusals.append(Refusal(f"secrets {next(iter(changes))}", secrets_text(secret_entry(**changes)), f"{where} entry 1 needs {need}", example, WHOLE_AND_STAGED))
    return refusals


def _unknown_key_refusals() -> list[Refusal]:
    where = f"{CONFIG} has unknown key"
    refusals = [
        Refusal("secrets unknown key", "[secrets]\nignore = []\n", f"{where} ignore in [secrets] - only allow exists", None, WHOLE_AND_STAGED),
        Refusal("top-level unknown key", "nope = 1\n", f"{where} nope - {KNOWN_KEYS}", None),
        Refusal("gate unknown key", "[gate]\nwhatever = 1\n", f"{where} whatever - {KNOWN_KEYS}", None),
        Refusal("advisories unknown key", "[advisories]\nignore = []\n", f"{where} ignore - {KNOWN_KEYS}", None),
        Refusal("two unknown keys", '[root]\nextra = 1\n[size]\nignore = []\n', f"{where}s extra, ignore - {KNOWN_KEYS}", "GENERATED_FILE_EXAMPLE"),
        Refusal("odd unknown key", '[root]\n"a b" = 1\nc = 2\n', f'{where}s "a b", c - {KNOWN_KEYS}', "ROOT_ALLOWANCE_EXAMPLE"),
    ]
    for table, example in (("root", "ROOT_ALLOWANCE_EXAMPLE"), ("layout", "LAYOUT_ALLOWANCE_EXAMPLE"),
                           ("python", "PYTHON_ALLOWANCE_EXAMPLE"), ("compose", "COMPOSE_ALLOWANCE_EXAMPLE"),
                           ("size", "GENERATED_FILE_EXAMPLE")):
        refusals.append(Refusal(f"{table} unknown key", f"[{table}]\nignore = []\n", f"{where} ignore - {KNOWN_KEYS}", example))
    return refusals


def _document_refusals() -> list[Refusal]:
    broken = "x = [\n"
    return [
        Refusal("not valid TOML", broken, f"{CONFIG} is not valid TOML ({parser_message(broken)})", "EXAMPLE", ("allow_lists", "gate")),
        Refusal("not valid TOML, staged", broken, f"{CONFIG} is not valid TOML ({parser_message(broken)})", "ALLOWANCE_EXAMPLE", ("staged",)),
        Refusal("config missing", None, f"{CONFIG} is missing", "EXAMPLE", ("gate",)),
        Refusal("mandatory_jobs", "[gate]\nmandatory_jobs = 3\n", f"{CONFIG} needs [gate] mandatory_jobs as a non-empty list of job names", "EXAMPLE", ("gate",)),
        Refusal("legacy", GATE + 'legacy = "x"\n', f'{CONFIG} [gate] legacy must be "report" or "fail"', "LEGACY_EXAMPLE", ("gate",)),
        Refusal("config is a directory", DIRECTORY, f"{CONFIG} is not a file", None),
        *(Refusal(f"{table} as a value", f"{table} = 1\n", f"{CONFIG} has [{table}] as a value, not a table", example,
                  WHOLE_AND_STAGED if table == "secrets" else WHOLE) for table, example in TABLE_VALUES),
    ]


def second_entry_refusal(table: ArrayTable) -> Refusal:
    changes, need = table.needs[0]
    return Refusal(
        f"{table.name} second entry", GATE + table.entry() + table.entry(**changes),
        f"{CONFIG} {table.header} entry 2 needs {need}", table.example,
    )


def all_refusals() -> list[Refusal]:
    refusals = [*_document_refusals(), *_unknown_key_refusals(), *_secret_refusals()]
    for table in ARRAY_TABLES:
        refusals.extend(_array_table_refusals(table))
        refusals.append(second_entry_refusal(table))
    return refusals
