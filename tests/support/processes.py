import time
from pathlib import Path


def process_is_gone(pid):
    """True once the process has exited (a zombie waiting for its reaper counts as exited)."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return True
        if state == "Z":
            return True
        time.sleep(0.05)
    return False
