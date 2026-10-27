
def function(name: str, parameter: str, local: str, assignments: int = 7, passes: int = 2) -> str:
    """A function of `3 + 5 * assignments + passes` tokens.

    The parameter is `arguments`, `arg`, `ID` (3 tokens), each `local = parameter` is `Assign`, `Name`, `ID`,
    `Name`, `ID` (5 tokens) and each `pass` is `Pass` (1 token). The defaults give 3 + 35 + 2 = 40.
    """
    body = [f"{local} = {parameter}"] * assignments + ["pass"] * passes
    return f"def {name}({parameter}):\n" + "".join(f"    {line}\n" for line in body)
