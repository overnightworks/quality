import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
TOOL_DIRECTORY = ".gate-tool"
UNSATISFIABLE_UV = ">=99"
# setup-uv exports these in CI; they choose uv's cache and Python, never the project or its configuration
KEPT_UV_VARIABLES = frozenset({"UV_CACHE_DIR", "UV_PYTHON", "UV_PYTHON_INSTALL_DIR"})
CALLER_FILES = {
    "a workspace listing the tool": (
        "pyproject.toml",
        f'[project]\nname = "caller"\nversion = "0"\n[tool.uv.workspace]\nmembers = ["{TOOL_DIRECTORY}"]\n',
    ),
    "a uv.toml": ("uv.toml", f'required-version = "{UNSATISFIABLE_UV}"\n'),
    "a pyproject.toml tool.uv table": ("pyproject.toml", f'[tool.uv]\nrequired-version = "{UNSATISFIABLE_UV}"\n'),
}


@pytest.mark.integration
@pytest.mark.parametrize("planted", CALLER_FILES)
def test_a_caller_checkout_above_the_tool_cannot_take_it_in_or_configure_it(planted, tmp_path):
    """CALLER-8."""
    tool = tmp_path / TOOL_DIRECTORY
    tool.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy(ROOT / name, tool / name)
    name, text = CALLER_FILES[planted]
    (tmp_path / name).write_text(text, encoding="utf-8")
    environment = {
        **{name: value for name, value in os.environ.items() if not name.startswith("UV_") or name in KEPT_UV_VARIABLES},
        "UV_OFFLINE": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }

    run = subprocess.run(
        ["uv", "run", "--no-sync", "--project", TOOL_DIRECTORY, "python", "-c", "import sys; print(sys.prefix)"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert run.returncode == 0, run.stderr
    assert Path(run.stdout.strip()).is_relative_to(tool.resolve())
