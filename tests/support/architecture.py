import json
from pathlib import Path

from quality import architecture
from quality.report import RuleResult

SHOP = {"src/shop/__init__.py": "", "src/shop/web.py": "", "src/shop/api.py": "", "src/shop/db.py": "x = 1\n"}


SHOP_DB_IMPORTS_WEB = {**SHOP, "src/shop/db.py": "import shop.web\n"}


FORBIDDEN = {"type": "forbidden", "source_modules": ["shop.db"], "forbidden_modules": ["shop.web"]}


def toml(contract: dict, roots: tuple[str, ...] = ("shop",)) -> str:
    """A `pyproject.toml` with one contract named `guarded`; JSON strings, lists and booleans are TOML too."""
    entries = "".join(f"{key} = {json.dumps(value)}\n" for key, value in {"name": "guarded", **contract}.items())
    return f"[tool.importlinter]\nroot_packages = {json.dumps(list(roots))}\n\n[[tool.importlinter.contracts]]\n{entries}"


def ini_section(header: str, options: dict[str, str | list[str]]) -> str:
    lines = [f"[{header}]"]
    for key, value in options.items():
        lines.append(f"{key} = {value}" if isinstance(value, str) else f"{key} =" + "".join(f"\n    {item}" for item in value))
    return "\n".join(lines) + "\n"


def contracts_ini(contracts: dict[str, dict], roots: tuple[str, ...] = ("shop",), **session: str | list[str]) -> str:
    """An import-linter INI file with one numbered contract section per name, each carrying the contract's id."""
    text = ini_section("importlinter", {"root_packages": list(roots), **session})
    for number, (name, options) in enumerate(contracts.items(), 1):
        written = {key: str(value) if isinstance(value, bool) else value for key, value in options.items()}
        text += ini_section(f"importlinter:contract:{number}", {"id": f"c{number}", "name": name, **written})
    return text


def contracts_toml(contracts: dict[str, dict], roots: tuple[str, ...] = ("shop",), **session) -> str:
    head = {"root_packages": list(roots), **session}
    text = "[tool.importlinter]\n" + "".join(f"{key} = {json.dumps(value)}\n" for key, value in head.items())
    for name, options in contracts.items():
        entries = {"name": name, **options}
        text += "\n[[tool.importlinter.contracts]]\n" + "".join(f"{key} = {json.dumps(value)}\n" for key, value in entries.items())
    return text


KEPT_CONTRACT = {**SHOP, ".importlinter": contracts_ini({"guarded": FORBIDDEN})}


def loosened(base: dict[str, str], head: dict[str, str], tree: architecture.PullTree | None = None) -> list[str]:
    return architecture.contract_loosenings(architecture.contracts_of(base), architecture.contracts_of(head), tree)


def one(options: dict) -> dict[str, dict]:
    return {"guarded": options}


GUARDED_CHANGES = 'import contract "guarded" changes '


CORE_MODULE = "src/skat/table/core.py"
CALLING_AND_MEMBER = ["skat.table.** -> agents.calling", "skat.table.** -> agents.member"]
CORE_KEEPS_TO_TWO_OF_AGENTS = {
    "type": "forbidden", "source_modules": ["skat.table"], "forbidden_modules": ["agents"], "ignore_imports": CALLING_AND_MEMBER,
}
SKAT = {
    "src/skat/__init__.py": "", "src/skat/table/__init__.py": "", CORE_MODULE: "", "src/skat/web.py": "",
    "src/skat/helper.py": "",
}


def judged_in(root: Path, files: dict[str, str], contracts: dict[str, dict], untracked: tuple[str, ...] = (), **session) -> RuleResult:
    """`check_contracts` over `files` written under `root` and tracked but for `untracked`, with `skat` the one root
    package and agents external."""
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content, encoding="utf-8")
    configs = {"pyproject.toml": contracts_toml(contracts, ("skat",), include_external_packages=True, **session)}
    return architecture.check_contracts(root, [name for name in files if name not in untracked], configs)


def judged_core(root: Path, core: str, contract: dict | None = None, **session) -> RuleResult:
    """The `skat` repository whose `skat.table.core` holds `core`, judged against `contract` (by default, the core
    keeps to `agents.calling` and `agents.member`)."""
    return judged_in(root, {**SKAT, CORE_MODULE: core}, one(contract or CORE_KEEPS_TO_TWO_OF_AGENTS), **session)
