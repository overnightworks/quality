import io


class Terminal(io.StringIO):
    """A stream that answers as a terminal does, so a test can read what the door paints."""

    def isatty(self) -> bool:
        return True
