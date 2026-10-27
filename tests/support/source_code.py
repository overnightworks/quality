
def long_file(lines: int) -> str:
    return "x = 1\n" * lines


def long_function(lines: int) -> str:
    return "def long_one():\n" + "    x = 1\n" * (lines - 1)


def branchy_function(ccn: int) -> str:
    branches = "".join(f"    if x == {value}:\n        return {value}\n" for value in range(ccn - 1))
    return f"def branchy(x):\n{branches}    return -1\n"


def typescript_file(lines: int) -> str:
    """Lines no Python parser accepts, so a measurement that tried to parse them would call the file unparsable."""
    return "export const answer: number = 42;\n" * lines


def typescript_function(lines: int) -> str:
    return "export function big(): number {\n" + "  const x = 1;\n" * (lines - 3) + "  return 1;\n}\n"


def minified_bundle(characters: int = 46_106) -> str:
    return "var a=function(){return 1};" * (characters // 27) + "\n"
