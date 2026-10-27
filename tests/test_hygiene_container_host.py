"""#188: a host-networked compose service is a compose-port finding unless `[[compose.allow]]` declares it."""

from pathlib import Path

import pytest
from conftest import hygiene_output
from test_emergency import ONE_JOB

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


@pytest.mark.parametrize(
    "config",
    [
        declaring() + declaring(reason="again"),
        declaring(reason="two\\nlines"),
        declaring(reason=" "),
        declaring(file="compose/"),
        declaring(file="*.yaml"),
        declaring(file="../compose.yaml"),
        declaring(file="/compose.yaml"),
        declaring(file="./compose.yaml"),
        declaring(file="deploy//compose.yaml"),
        declaring(service=""),
        declaring() + "extra = 1\n",
        '[[compose.allow]]\nfile = "compose.yaml"\nservice = "proxy"\n',
        '[[compose.allow]]\nfile = "compose.yaml"\nreason = "r"\n',
        '[[compose.allow]]\nfile = 3\nservice = "proxy"\nreason = "r"\n',
        "[compose]\nignore = []\n",
        '[compose]\nallow = "x"\n',
    ],
    ids=[
        "duplicate", "multi-line reason", "blank reason", "folder", "pattern", "parent path", "absolute path", "dot component", "empty component",
        "blank service", "extra key", "missing service", "missing reason", "file not text", "unknown table key",
        "allow not a list",
    ],
)
def test_a_malformed_compose_table_cannot_be_judged(build_repo, capsys, config):
    """#188 sentence 3: each malformed entry is exit 2 and names `[[compose.allow]]` with an example."""
    code, output = hygiene_output(capsys, build_repo({"compose.yaml": service_file("host"), CONFIG: ONE_JOB + config}))
    assert code == 2
    assert "compose-port cannot judge" in output
    assert "[[compose.allow]]" in output
    assert "like:" in output


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
