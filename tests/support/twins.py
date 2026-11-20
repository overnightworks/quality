def twin_test(name: str, literal: object = 1, passes: int = 3, decorator: str = "") -> str:
    """A test of `9 + passes` tokens once names are kept and literals folded.

    `arguments` (1), `Assign` (1), `Name`, `ID` (2), `Call` (1), `Name`, `ID` (2), `Constant`, `S` or `N` (2), each `pass` 1.
    The default is 12, the floor of a test twin.
    """
    return f"{decorator}def {name}():\n    result = run({literal!r})\n" + "    pass\n" * passes


def helper(name: str, literal: object = 1, passes: int = 1) -> str:
    """A helper of `7 + passes` tokens: `arguments` (1), `Return` (1), `Call` (1), `Name`, `ID` (2), `Constant`, `S` or `N` (2),
    each `pass` 1. The default is 8, the floor of a copied helper."""
    return f"def {name}():\n" + "    pass\n" * passes + f"    return run({literal!r})\n"
