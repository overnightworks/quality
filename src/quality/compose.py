"""The `compose-port` rule: every port a tracked Compose file publishes binds loopback and no service is host-networked
unless declared. Compose files are read as YAML 1.2, as Compose reads them."""

import re
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

import yaml

from quality import checkout, repository
from quality.config import CONFIG_PATH, ComposeAllowance
from quality.report import CannotJudge, Finding

COMPOSE_PORT = "compose-port"

LOOPBACK = "127.0.0.1"
LOOPBACK_HOSTS = (LOOPBACK, "::1")
INTERPOLATION = re.compile(r"\$\{[^}]*\}")
COMPOSE_WORD = "compose"
HOST_NETWORK_MODE = "host"
NAME_SEPARATORS = re.compile(r"[._-]")
YAML_NUMBER_TAGS = ("tag:yaml.org,2002:int", "tag:yaml.org,2002:float")


class ComposeLoader(yaml.SafeLoader):
    """Reads Compose's own YAML tags (!override, !reset) as the plain value they wrap."""


def _construct_tagged(loader: ComposeLoader, node: yaml.Node) -> object:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    is_plain = node.style is None
    implicit_tag = loader.resolve(yaml.ScalarNode, node.value, (is_plain, not is_plain))
    return loader.construct_object(yaml.ScalarNode(implicit_tag, node.value, node.start_mark, node.end_mark))


for compose_tag in ("!override", "!reset"):
    ComposeLoader.add_constructor(compose_tag, _construct_tagged)


def _number_constructor(construct_number: Callable[[yaml.SafeLoader, yaml.Node], object]):
    """Compose reads YAML 1.2, where `22:22` is text; only YAML 1.1 makes it a base-60 number."""
    def construct(loader: ComposeLoader, node: yaml.Node) -> object:
        return node.value if ":" in node.value else construct_number(loader, node)
    return construct


for number_tag in YAML_NUMBER_TAGS:
    ComposeLoader.add_constructor(number_tag, _number_constructor(yaml.SafeLoader.yaml_constructors[number_tag]))


YAML_BOOLEAN_TAG = "tag:yaml.org,2002:bool"
YAML_BOOLEAN = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")


def _read_only_true_and_false_as_boolean() -> None:
    """Compose reads YAML 1.2, where `on`, `yes` and `no` are text, as service names; only YAML 1.1 makes them booleans."""
    resolvers = {
        first: [(tag, pattern) for tag, pattern in entries if tag != YAML_BOOLEAN_TAG]
        for first, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    ComposeLoader.yaml_implicit_resolvers = resolvers
    ComposeLoader.add_implicit_resolver(YAML_BOOLEAN_TAG, YAML_BOOLEAN, list("tTfF"))


_read_only_true_and_false_as_boolean()


def is_compose_file(name: str) -> bool:
    """A YAML file whose name holds the word `compose` between `.`, `-` or `_`: `docker-compose.ci.yml`, not `composer.yml`."""
    path = PurePosixPath(name)
    return path.suffix.lower() in checkout.YAML_SUFFIXES and COMPOSE_WORD in NAME_SEPARATORS.split(path.stem.casefold())


def check_compose_ports(root: Path, tracked: list[str], allowances: Sequence[ComposeAllowance]) -> list[Finding]:
    """Every published port binds loopback and no service is host-networked (or may be, through an interpolation)
    unless declared; a declaration that matches no such service is a finding too."""
    declared = {(allowance.file, allowance.service) for allowance in allowances}
    findings = []
    host_networked: set[tuple[str, str]] = set()
    for name in [name for name in tracked if is_compose_file(name)]:
        if checkout.judged(repository.is_on_disk, root, name) and (text := checkout.judged(repository.read_text, root, name)) is not None:
            file_findings, services = _compose_file_findings(name, text, {service for file, service in declared if file == name})
            findings.extend(file_findings)
            host_networked.update((name, service) for service in services)
    findings += [_stale_compose_declaration(*key) for key in sorted(declared - host_networked)]
    return findings


def _compose_file_findings(name: str, text: str, declared_services: set[str]) -> tuple[list[Finding], list[str]]:
    """The findings of one compose file and its services that are host-networked or may be, declared or not."""
    try:
        document = yaml.load(text, Loader=ComposeLoader)  # noqa: S506 - ComposeLoader subclasses SafeLoader
    except (yaml.YAMLError, RecursionError, ValueError, KeyError, IndexError) as error:
        raise CannotJudge(f"cannot read {name}: {_yaml_error_reason(error)}") from error
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict):
        return [], []
    findings = []
    host_networked = []
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        if (network_finding := _network_mode_finding(name, service_name, service.get("network_mode"))) is not None:
            host_networked.append(str(service_name))
            if str(service_name) not in declared_services:
                findings.append(network_finding)
        ports = service.get("ports") or []
        if not isinstance(ports, list):
            findings.append(Finding(COMPOSE_PORT, name, None,
                                    f"service {service_name} has ports that are not a list (unreadable).",
                                    "write ports as a list so each one can be checked."))
            continue
        findings.extend(_port_findings(name, service_name, ports))
    return findings, host_networked


def _network_mode_finding(name: str, service_name: object, network_mode: object) -> Finding | None:
    """A finding for `host`, and for a value an interpolation may resolve to `host`; None for any other mode."""
    fix = f"give it a network of its own, or declare it in {CONFIG_PATH} with [[compose.allow]], a file, a service and a reason."
    if network_mode == HOST_NETWORK_MODE:
        return Finding(COMPOSE_PORT, name, None,
                       f"service {service_name} has network_mode: {HOST_NETWORK_MODE}, which publishes whatever it binds beyond loopback.", fix)
    if isinstance(network_mode, str) and INTERPOLATION.search(network_mode):
        return Finding(COMPOSE_PORT, name, None,
                       f"service {service_name} has a network_mode that holds an interpolation, which may resolve to {HOST_NETWORK_MODE} (unverifiable).", fix)
    return None


def _stale_compose_declaration(file: str, service: str) -> Finding:
    return Finding(
        COMPOSE_PORT, file, None,
        f"service {service} is declared in {CONFIG_PATH} with [[compose.allow]], but no tracked compose file of that name "
        f"has such a service with network_mode: {HOST_NETWORK_MODE} or an interpolated network_mode.",
        f"delete its [[compose.allow]] entry from {CONFIG_PATH}.",
    )


def _yaml_error_reason(error: Exception) -> str:
    """One line: PyYAML's own message quotes the source and names `<unicode string>`."""
    if isinstance(error, RecursionError):
        return "nested too deeply"
    if isinstance(error, yaml.reader.ReaderError):
        return f"position {error.position}: {error.reason}"
    if isinstance(error, yaml.MarkedYAMLError) and error.problem_mark:
        mark = error.problem_mark
        said = ", ".join(part for part in (error.context, error.problem) if part)
        return f"line {mark.line + 1}, column {mark.column + 1}: {said}"
    if isinstance(error, (ValueError, KeyError, IndexError)):
        return "a value does not fit its tag"
    return "not valid YAML"


def _port_findings(name: str, service_name: object, ports: list) -> list[Finding]:
    findings = []
    for port in ports:
        description = _describe(port)
        if description is None:
            findings.append(Finding(COMPOSE_PORT, name, None,
                                    f"service {service_name} has a port that is not a string, a number or a mapping whose published (or, without one, target) is text or a number (unreadable).",
                                    "write each port as a string, a number or a mapping whose published (or, without one, target) is text or a number."))
        elif not _binds_loopback(port):
            findings.append(Finding(COMPOSE_PORT, name, None,
                                    f"service {service_name} publishes port {description} beyond loopback.",
                                    f"bind it explicitly, for example \"{LOOPBACK}:HOST:CONTAINER\"."))
    return findings


def _binds_loopback(port: object) -> bool:
    if isinstance(port, dict):
        return port.get("host_ip") in LOOPBACK_HOSTS
    spec = INTERPOLATION.sub("-", str(port))
    if spec.startswith("["):
        host, _, ports = spec[1:].partition("]")
        parts = [host, *ports.split(":")[1:]]
    else:
        parts = spec.split(":")
    return len(parts) == 3 and parts[0] in LOOPBACK_HOSTS


def _describe(port: object) -> str | None:
    """The port as written, or None when it is not text, a number or a mapping of one; a container is never printed."""
    if isinstance(port, dict):
        port = port.get("published", port.get("target"))
    return str(port) if isinstance(port, (str, int, float)) else None
