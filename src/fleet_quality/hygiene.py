import os
import subprocess
from pathlib import Path, PurePosixPath

import yaml

from fleet_quality.report import Finding

ENV_FILE = "env-file"
COMPOSE_PORT = "compose-port"
RULE_IDS = (ENV_FILE, COMPOSE_PORT)

LOOPBACK = "127.0.0.1"
ENV_TEMPLATE_SUFFIX = ".example"
COMPOSE_PATTERNS = ("compose*.yml", "compose*.yaml", "docker-compose*.yml", "docker-compose*.yaml")


class CannotJudge(Exception):
    """The repository cannot be examined; this is never a clean result."""


def tracked_files(root: Path) -> list[str]:
    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise CannotJudge(f"{root} is not a readable git repository: {error}") from error
    return [os.fsdecode(name) for name in listing.stdout.split(b"\0") if name]


def check_env_files(tracked: list[str]) -> list[Finding]:
    return [
        Finding(
            ENV_FILE, name, None,
            "a tracked env file publishes configuration and often secrets.",
            f"git rm --cached {name} and add it to .gitignore.",
        )
        for name in tracked
        if _is_env_file(PurePosixPath(name).name)
    ]


def _is_env_file(name: str) -> bool:
    if name.endswith(ENV_TEMPLATE_SUFFIX):
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def check_compose_ports(root: Path, tracked: list[str]) -> list[Finding]:
    findings = []
    for name in tracked:
        if any(PurePosixPath(name).match(pattern) for pattern in COMPOSE_PATTERNS):
            findings.extend(_compose_file_findings(root, name))
    return findings


def _compose_file_findings(root: Path, name: str) -> list[Finding]:
    try:
        document = yaml.safe_load((root / name).read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        return [Finding(COMPOSE_PORT, name, None, f"the compose file is unreadable ({error.__class__.__name__}).",
                        "make it valid UTF-8 YAML so its ports can be checked.")]
    except OSError as error:
        raise CannotJudge(f"cannot read {name}: {error}") from error
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict):
        return []
    findings = []
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        ports = service.get("ports") or []
        if not isinstance(ports, list):
            findings.append(Finding(COMPOSE_PORT, name, None,
                                    f"service {service_name} has ports that are not a list (unreadable).",
                                    "write ports as a list so each one can be checked."))
            continue
        findings.extend(
            Finding(COMPOSE_PORT, name, None,
                    f"service {service_name} publishes port {_describe(port)} beyond loopback.",
                    f"bind it explicitly, for example \"{LOOPBACK}:HOST:CONTAINER\".")
            for port in ports
            if not _binds_loopback(port)
        )
    return findings


def _binds_loopback(port: object) -> bool:
    if isinstance(port, dict):
        return port.get("host_ip") == LOOPBACK
    parts = str(port).split(":")
    return len(parts) == 3 and parts[0] == LOOPBACK


def _describe(port: object) -> str:
    return str(port) if not isinstance(port, dict) else str(port.get("published", port.get("target", port)))


def run_hygiene(root: Path) -> list[Finding]:
    tracked = tracked_files(root)
    return check_env_files(tracked) + check_compose_ports(root, tracked)
