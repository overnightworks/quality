"""A spec file a test holds."""

SPEC = "docs/rules.md"


def spec_with(*lines: str) -> dict[str, str]:
    """The files of a repository whose one spec file holds `lines`."""
    return {SPEC: "\n".join(lines) + "\n"}
