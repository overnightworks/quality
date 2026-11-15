"""The HTML attributes whose value a person reads, which `literal-text` and `locator-text` both judge."""

READ_ATTRIBUTES = frozenset(
    {"title", "placeholder", "aria-label", "alt", "label", "aria-description", "aria-roledescription", "aria-valuetext"}
)
