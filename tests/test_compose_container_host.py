"""#188: a host-networked compose service is a compose-port finding unless `[[compose.allow]]` declares it."""

from pathlib import Path

import pytest

from quality.config import COMPOSE_ALLOWANCE_EXAMPLE, CONFIG_PATH
from tests.support.check import copy_block, hygiene_output, refused
from tests.support.config_refusals import KNOWN_KEYS, ONE_LINE_NEED
from tests.support.merge import ONE_JOB

CONFIG = "quality/config.toml"
EDGE_STACK = (Path(__file__).parent / "fixtures" / "edge_stack.yml").read_text(encoding="utf-8")


def service_file(network_mode: str, service: str = "proxy") -> str:
    return f"services:\n  {service}:\n    image: x\n    network_mode: {network_mode}\n"


def declaring(file: str = "compose.yaml", service: str = "proxy", reason: str = "it must see the host's interfaces") -> str:
    return f'[[compose.allow]]\nfile = "{file}"\nservice = "{service}"\nreason = "{reason}"\n'


def judged(capsys, build_repo, files: dict[str, str], declarations: str = "") -> tuple[int, list[str]]:
    """The exit code and the compose-port finding lines of `quality hygiene` over a fixture repository."""
    config = {CONFIG: ONE_JOB + declarations} if declarations else {}
    code, output = hygiene_output(capsys, build_repo({**files, **config}))
    return code, [line for line in output.splitlines() if line.startswith("compose-port ")]


def test_a_host_networked_service_is_red(build_repo, capsys):
    """#188 sentence 1: a service with `network_mode: host` is a compose-port finding."""
    code, lines = judged(capsys, build_repo, {"compose.yaml": service_file("host")})
    assert code == 1
    assert len(lines) == 1
    assert lines[0].startswith("compose-port compose.yaml - service proxy ")
    assert "network_mode: host" in lines[0]


@pytest.mark.parametrize("network_mode", ["none", "service:db", "bridge"])
def test_another_network_mode_is_clean(build_repo, capsys, network_mode):
    """#188 sentence 1: a `network_mode` other than `host` is green."""
    assert judged(capsys, build_repo, {"compose.yaml": service_file(network_mode)}) == (0, [])


@pytest.mark.parametrize("network_mode", ["${NETWORK_MODE}", "${NETWORK_MODE:-none}", "'ho${REST}'"])
def test_an_interpolated_network_mode_is_red(build_repo, capsys, network_mode):
    """#188 sentence 2: a `network_mode` holding an interpolation may resolve to host, so it is a finding."""
    code, lines = judged(capsys, build_repo, {"compose.yaml": service_file(network_mode)})
    assert code == 1
    assert len(lines) == 1
    assert "interpolation" in lines[0]


@pytest.mark.parametrize("network_mode", ["host", "${NETWORK_MODE}"])
def test_a_declared_service_passes_the_host_check(build_repo, capsys, network_mode):
    """#188 sentence 3: a declared service is clean, host-networked or interpolated."""
    files = {"compose.yaml": service_file(network_mode)}
    assert judged(capsys, build_repo, files, declaring()) == (0, [])


def test_a_declaration_covers_its_file_and_service_only(build_repo, capsys):
    """#188 sentence 3: another service, or the same service in another file, stays red."""
    files = {
        "compose.yaml": service_file("host") + "  other:\n    image: x\n    network_mode: host\n",
        "deploy/compose.prod.yaml": service_file("host"),
    }
    code, lines = judged(capsys, build_repo, files, declaring())
    assert code == 1
    assert [line.split(" - ")[0] for line in lines] == [
        "compose-port compose.yaml",
        "compose-port deploy/compose.prod.yaml",
    ]
    assert "service other " in lines[0]


def test_a_declared_service_still_has_its_ports_judged(build_repo, capsys):
    """#188 sentence 3: the declaration passes the host check only, not a port beyond loopback."""
    text = service_file("host") + '    ports:\n      - "8080:80"\n'
    code, lines = judged(capsys, build_repo, {"compose.yaml": text}, declaring())
    assert (code, len(lines)) == (1, 1)
    assert "publishes port" in lines[0]


def test_edge_stack_is_clean_once_its_traefik_is_declared(build_repo, capsys):
    """#188 sentence 3: edge's real file has one host-networked service; the others use `none`."""
    files = {"compose.yaml": EDGE_STACK}
    code, lines = judged(capsys, build_repo, files)
    assert (code, [line.split(" - ")[1][:15] for line in lines]) == (1, ["service traefik"])
    assert judged(capsys, build_repo, files, declaring(service="traefik")) == (0, [])


@pytest.mark.parametrize(
    ("files", "declaration"),
    [
        ({"compose.yaml": service_file("none")}, declaring()),
        ({"compose.yaml": service_file("host")}, declaring(service="missing")),
        ({"compose.yaml": service_file("host")}, declaring(file="deploy/compose.yaml")),
        ({"deploy/compose.yaml": service_file("host"), "notes.yaml": service_file("host")}, declaring(file="notes.yaml")),
        ({"compose.yaml": service_file("none")}, declaring(file="gone/compose.yaml")),
    ],
    ids=["not host-networked", "service missing", "file not tracked", "not a compose file", "tracked file absent"],
)
def test_a_declaration_that_matches_nothing_is_red(build_repo, capsys, files, declaration):
    """#188 sentence 4: a declaration matching no host-networked or interpolated service is a finding on its file."""
    code, lines = judged(capsys, build_repo, files, declaration)
    assert code == 1
    stale = [line for line in lines if "[[compose.allow]]" in line and "declared" in line]
    assert len(stale) == 1
    assert stale[0].startswith(f"compose-port {declaration.split('"')[1]} - ")


COMPOSE = f"{CONFIG_PATH} [[compose.allow]]"
COMPOSE_FILE_NEED = f"{COMPOSE} entry 1 needs file as the exact tracked path of one compose file: no folder, pattern, .. or absolute path"
COMPOSE_KEYS = f"{COMPOSE} entry 1 needs exactly a file, a service and a reason"


@pytest.mark.parametrize(
    ("config", "fact"),
    [
        (
            declaring() + declaring(reason="again"),
            f'{COMPOSE} declares service = "proxy" of file = "compose.yaml" twice; one declaration per file and service',
        ),
        (declaring(reason="two\\nlines"), f"{COMPOSE} entry 1 needs {ONE_LINE_NEED}"),
        (declaring(reason=" "), f"{COMPOSE} entry 1 needs {ONE_LINE_NEED}"),
        (declaring(file="compose/"), COMPOSE_FILE_NEED),
        (declaring(file="*.yaml"), COMPOSE_FILE_NEED),
        (declaring(file="../compose.yaml"), COMPOSE_FILE_NEED),
        (declaring(file="/compose.yaml"), COMPOSE_FILE_NEED),
        (declaring(file="./compose.yaml"), COMPOSE_FILE_NEED),
        (declaring(file="deploy//compose.yaml"), COMPOSE_FILE_NEED),
        (declaring(service=""), f"{COMPOSE} entry 1 needs service as the one-line name of a service in that file"),
        (declaring() + "extra = 1\n", COMPOSE_KEYS),
        ('[[compose.allow]]\nfile = "compose.yaml"\nservice = "proxy"\n', COMPOSE_KEYS),
        ('[[compose.allow]]\nfile = "compose.yaml"\nreason = "r"\n', COMPOSE_KEYS),
        ('[[compose.allow]]\nfile = 3\nservice = "proxy"\nreason = "r"\n', COMPOSE_FILE_NEED),
        ("[compose]\nignore = []\n", f"{CONFIG_PATH} has unknown key ignore - {KNOWN_KEYS}"),
        ('[compose]\nallow = "x"\n', f"{CONFIG_PATH} needs [[compose.allow]] as an array of tables"),
    ],
    ids=[
        "duplicate", "multi-line reason", "blank reason", "folder", "pattern", "parent path", "absolute path", "dot component", "empty component",
        "blank service", "extra key", "missing service", "missing reason", "file not text", "unknown table key",
        "allow not a list",
    ],
)
def test_a_malformed_compose_table_cannot_be_judged(build_repo, capsys, config, fact):
    """#188 sentence 3: each malformed entry is exit 2, names its fact on the files row and shows the example once."""
    code, output = hygiene_output(capsys, build_repo({"compose.yaml": service_file("host"), CONFIG: ONE_JOB + config}))
    assert code == 2
    assert refused(output, "files")
    assert fact in output
    assert output.count(copy_block(COMPOSE_ALLOWANCE_EXAMPLE)) == 1


def test_the_unknown_table_message_lists_the_compose_table(build_repo, capsys):
    """#188 sentence 3: an unknown table names `[[compose.allow]]` among the tables that exist."""
    code, output = hygiene_output(capsys, build_repo({CONFIG: ONE_JOB + "[nope]\nx = 1\n"}))
    assert code == 2
    assert "[[compose.allow]]" in output


@pytest.mark.parametrize("name", ["on", "yes", "off", "no"])
def test_a_service_named_like_a_yaml_one_one_boolean_is_matched_by_its_name(build_repo, capsys, name):
    """#188 sentences 3-4: the declared service passes by the name written in the file, and a declaration of its coerced spelling is stale."""
    files = {"compose.yaml": service_file("host", service=name)}
    assert judged(capsys, build_repo, files, declaring(service=name)) == (0, [])
    code, lines = judged(capsys, build_repo, files, declaring(service=name) + declaring(service="True"))
    assert code == 1
    assert [line for line in lines if "service True" in line and "declared" in line]
