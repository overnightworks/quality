import datetime
import re
import unicodedata
from collections.abc import Callable, Hashable, Iterator
from dataclasses import dataclass

from quality import repository

CONFIG_PATH = "quality/config.toml"
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
ONE_LINE_REASON = "a non-empty one-line reason"
# ASCII only, so a lookalike name (a Cyrillic letter in it) is no repository.
REPOSITORY_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ITEM_REFERENCE = re.compile(rf"(?:{REPOSITORY_NAME.pattern})?#[1-9][0-9]*")
ITEM_REFERENCE_EXPECTED = "item as #<number> or <owner>/<repo>#<number>"
RULING_KEYS = {"item", "date"}
PATH_PATTERN_CHARACTERS = "*?["
type ExampleLines = tuple[str, *tuple[str, ...]]


class ConfigError(Exception):
    """The quality config cannot be read: `fact` says why, `example` (TOML lines to copy) how to write it, where there is one.

    `str` is the one-line form for the doors that print a single line; the structured fields are the contract."""

    def __init__(self, fact: str, example: ExampleLines | None = None) -> None:
        super().__init__(fact)
        self.fact = fact
        self.example = example

    def __str__(self) -> str:
        return self.fact if self.example is None else f"{self.fact}, like:\n" + "\n".join(self.example)

def toml_string(value: str) -> str:
    """`value` as a TOML basic string: `\\` and `"` escaped, every control character (category Cc) as `\\uNNNN`, every
    other character as itself, so what a person copies reads back as `value`; `report.printable` escapes the rest
    when the line is printed."""
    return f'"{"".join(_escaped_in_string(character) for character in value)}"'

def _escaped_in_string(character: str) -> str:
    if character in '\\"':
        return f"\\{character}"
    return f"\\u{ord(character):04x}" if unicodedata.category(character) == "Cc" else character

def _key_name(key: str) -> str:
    """A key as TOML writes it: bare when it can be, else a basic string."""
    return key if BARE_KEY.fullmatch(key) else toml_string(key)

def unknown_keys(unknown: set[str]) -> str:
    return f"unknown {'key' if len(unknown) == 1 else 'keys'} {', '.join(_key_name(key) for key in sorted(unknown))}"

def parse(text: str, example: ExampleLines) -> dict:
    try:
        return repository.parse_toml(text)
    except repository.TomlError as error:
        raise ConfigError(f"{CONFIG_PATH} is not valid TOML ({error})", example) from error

def table_named(document: dict, name: str, example: ExampleLines) -> dict:
    table = document.get(name, {})
    if not isinstance(table, dict):
        raise ConfigError(f"{CONFIG_PATH} has [{name}] as a value, not a table", example)
    return table

def numbered_entries(table: dict, key: str, heading: str, example: ExampleLines, shape: str = "an array of tables") -> Iterator[tuple[int, object]]:
    """The entries of a list in `table` with their 1-based positions, counted once for every reader."""
    entries = table.get(key, [])
    if not isinstance(entries, list):
        raise ConfigError(f"{CONFIG_PATH} needs {heading} as {shape}", example)
    return enumerate(entries, start=1)

def unique_entries[Entry](
    table: dict,
    key: str,
    heading: str,
    example: ExampleLines,
    read: Callable[[object, int], Entry],
    identity: Callable[[Entry], Hashable],
    repeated: Callable[[Entry], str],
    example_on_repeat: bool = True,
) -> tuple[Entry, ...]:
    """The entries of a list in `table`, each read by `read`; an entry whose `identity` came before is refused as
    `{CONFIG_PATH} {heading} {repeated(entry)}`."""
    entries: dict[Hashable, Entry] = {}
    for position, raw in numbered_entries(table, key, heading, example):
        entry = read(raw, position)
        if identity(entry) in entries:
            raise ConfigError(f"{CONFIG_PATH} {heading} {repeated(entry)}", example if example_on_repeat else None)
        entries[identity(entry)] = entry
    return tuple(entries.values())

def declared_entries[Entry](
    table: dict, key: str, heading: str, example: ExampleLines, read: Callable[[object, int], Entry]
) -> tuple[Entry, ...]:
    """The entries of a list in `table`, each read by `read`, a repeated one kept as declared: a reading by identity
    alone has no say on how often an entry is declared, and the base's entries are only looked up."""
    return tuple(read(raw, position) for position, raw in numbered_entries(table, key, heading, example))

def entry_name(heading: str, position: int) -> str:
    """How a message names one entry of a list: its heading and 1-based position."""
    return f"{heading} entry {position}"

def delete_entry_fix(heading: str) -> str:
    """The fix a finding gives for an entry that no longer has anything to allow."""
    return f"delete its {heading} entry from {CONFIG_PATH}"

def entry_refusal(heading: str, position: int, example: ExampleLines) -> Callable[[str], ConfigError]:
    def refuse(need: str) -> ConfigError:
        return ConfigError(f"{CONFIG_PATH} {entry_name(heading, position)} needs {need}", example)

    return refuse

def spans_lines(reason: str) -> bool:
    return "\n" in reason or "\r" in reason

def is_one_line_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not spans_lines(value)

def is_date_without_time(value: object) -> bool:
    """A TOML date: tomllib reads a date with a time as a datetime, which is a date too."""
    return isinstance(value, datetime.date) and not isinstance(value, datetime.datetime)


@dataclass(frozen=True)
class Ruling:
    """The operator's ruling that let an entry in: the item holding it and its day."""

    item: str
    date: datetime.date


def refuse_unless_ruling(refuse: Callable[[str], ConfigError], item: object, date: object) -> None:
    """The item and date every declaration that carries its own ruling needs."""
    if not (isinstance(item, str) and ITEM_REFERENCE.fullmatch(item)):
        raise refuse(ITEM_REFERENCE_EXPECTED)
    if not is_date_without_time(date):
        raise refuse("date as a TOML date without a time")


def refuse_unless_ruled(refuse: Callable[[str], ConfigError], item: object, date: object, reason: object) -> None:
    """The item, date and reason every declaration that carries its own ruling needs."""
    refuse_unless_ruling(refuse, item, date)
    if not is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)


def optional_ruling(refuse: Callable[[str], ConfigError], entry: dict) -> Ruling | None:
    """The ruling an entry carries as `item` and `date` together, None when it carries neither; one of them alone is refused."""
    if RULING_KEYS.isdisjoint(entry):
        return None
    refuse_unless_ruling(refuse, entry.get("item"), entry.get("date"))
    return Ruling(entry["item"], entry["date"])


def is_exact_file_path(path: object) -> bool:
    """A relative path to one file: no folder (trailing `/`), no pattern character, no empty, `.` or `..` component, not absolute."""
    if not isinstance(path, str) or not path.strip() or path.startswith("/") or path.endswith("/"):
        return False
    components = path.split("/")
    return not any(character in path for character in PATH_PATTERN_CHARACTERS) and all(
        component not in ("", ".", "..") for component in components
    )
