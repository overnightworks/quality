import json
from pathlib import Path

from quality import architecture
from quality.report import CannotJudge, Finding, RuleResult
from quality.rules import Area, Rule
from tests.support.check import check_result

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


def skat_core_path() -> str:
    return "src/skat/table/core.py"


def calling_and_member() -> list[str]:
    return ["skat.table.** -> agents.calling", "skat.table.** -> agents.member"]


def core_keeps_to_two_of_agents(**options) -> dict:
    """A forbidden contract that keeps `skat.table` off `agents` but for `agents.calling` and `agents.member`, with `options` over it."""
    contract = {"type": "forbidden", "source_modules": ["skat.table"], "forbidden_modules": ["agents"], "ignore_imports": calling_and_member()}
    return {**contract, **options}


def skat_files(core: str = "", *, web: str = "", helper: str = "") -> dict[str, str]:
    """The `skat` package's files, with `skat.table.core`, `skat.web` and `skat.helper` holding the given source."""
    return {
        "src/skat/__init__.py": "", "src/skat/table/__init__.py": "", skat_core_path(): core, "src/skat/web.py": web, "src/skat/helper.py": helper,
    }


def judged_in(
    root: Path, files: dict[str, str], contracts: dict[str, dict], untracked: tuple[str, ...] = (), tracked_absent: tuple[str, ...] = (), **session
) -> RuleResult:
    """`read_contracts` and `check_contracts` over `files` written under `root`, tracked but for `untracked`, and over the
    names of `tracked_absent`, which git tracks and the tree does not hold, with `skat` the one root package and agents external."""
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content, encoding="utf-8")
    configs = {"pyproject.toml": contracts_toml(contracts, ("skat",), include_external_packages=True, **session)}
    read = architecture.read_contracts(root, [*(name for name in files if name not in untracked), *tracked_absent], configs)
    assert read is not None
    return architecture.check_contracts(read)


def judged_core(root: Path, core: str, contract: dict | None = None, **session) -> RuleResult:
    """The `skat` repository whose `skat.table.core` holds `core`, judged against `contract` (by default, the core
    keeps to `agents.calling` and `agents.member`)."""
    return judged_in(root, skat_files(core), one(contract or core_keeps_to_two_of_agents()), **session)


CORE_TEST = "tests/core/test_config.py"
ADAPTER_IMPORT = "import shop.adapters.db\n"


def adapter_split(source: tuple[str, ...] = ("shop.core",), forbidden: tuple[str, ...] = ("shop.adapters",), **options) -> dict:
    """A forbidden contract that keeps the core off the adapters and carries no id."""
    return {"type": "forbidden", "source_modules": list(source), "forbidden_modules": list(forbidden), **options}


def adapter_contract(**options) -> dict:
    """`adapter_split` carrying the id the adapter-import rule reads."""
    return {"id": architecture.ADAPTER_CONTRACT_ID, **adapter_split(**options)}


def pytest_markers(*markers: str) -> str:
    """The `[tool.pytest.ini_options]` table of a `pyproject.toml` that registers `markers`."""
    return f"\n[tool.pytest.ini_options]\nmarkers = {json.dumps(list(markers))}\n"


def shop_with_adapters(
    tests: dict[str, str], contract: dict | None = None, *, markers: tuple[str, ...] = (), **session
) -> dict[str, str]:
    """The tracked files of a `shop` repository: a core, adapters and a `shop.main` that composes them, `tests` beside
    them and a `pyproject.toml` that holds `contract` (by default, the core never reaches the adapters) and registers `markers`."""
    return {
        "src/shop/__init__.py": "", "src/shop/core/__init__.py": "", "src/shop/core/config.py": "",
        "src/shop/core/config_stores.py": "", "src/shop/adapters/__init__.py": "", "src/shop/adapters/db.py": "",
        "src/shop/main.py": ADAPTER_IMPORT,
        "pyproject.toml": contracts_toml({"guard": contract or adapter_contract()}, **session) + (pytest_markers(*markers) if markers else ""),
        **tests,
    }


def imports_verdict(repo: Path, rule: Rule):
    """The verdict of `rule` over the imports area of `repo`."""
    (verdict,) = (verdict for verdict in check_result(repo, named=[Area.IMPORTS]).verdicts if verdict.rule is rule)
    return verdict


def adapter_verdict(repo: Path):
    """The verdict of the adapter-import rule over the imports area of `repo`."""
    return imports_verdict(repo, Rule.ADAPTER_IMPORT)


def imports_findings(repo: Path, rule: Rule) -> list[Finding]:
    """The findings of `rule` over the imports area of `repo`; a rule that cannot judge fails the test."""
    verdict = imports_verdict(repo, rule)
    assert verdict.refusal is None, verdict.refusal
    return list(verdict.findings)


def adapter_findings(repo: Path) -> list[Finding]:
    """The findings of the adapter-import rule over `repo`; a rule that cannot judge fails the test."""
    return imports_findings(repo, Rule.ADAPTER_IMPORT)


def adapter_refusal(repo: Path) -> CannotJudge:
    verdict = adapter_verdict(repo)
    assert verdict.refusal is not None
    return verdict.refusal


LAYERS = {"type": "layers", "layers": ["shop.web", "shop.db"]}
