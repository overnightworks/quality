import json

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
