import json
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from quality.config import CONFIG_PATH, GateSettings, LegacyMode, load_gate_settings
from quality.hygiene import CannotJudge

JOBS_URL = "https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs?filter=all&per_page=100"
MAX_PAGES = 10
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')
SUCCESS = "success"

PageFetcher = Callable[[str], tuple[str, str | None]]


NO_BASE_SETTINGS = GateSettings((), LegacyMode.REPORT)


@dataclass(frozen=True)
class Job:
    name: str
    run_attempt: int
    status: str
    conclusion: str | None

    @property
    def state(self) -> str:
        return self.conclusion if self.status == "completed" and self.conclusion else self.status


@dataclass(frozen=True)
class Verdict:
    problems: tuple[str, ...]

    @property
    def green(self) -> bool:
        return not self.problems


def judge(
    settings: GateSettings,
    base_settings: GateSettings,
    jobs: list[Job],
    gate_results: Mapping[str, str],
    current_attempt: int,
) -> Verdict:
    problems = [f'gate job "{name}" result {result}' for name, result in gate_results.items() if result != SUCCESS]
    problems += [
        f'loosened: mandatory job "{name}" removed'
        for name in base_settings.mandatory_jobs
        if name not in settings.mandatory_jobs
    ]
    if base_settings.legacy is LegacyMode.FAIL and settings.legacy is not LegacyMode.FAIL:
        problems.append(f"loosened: legacy {base_settings.legacy.value} → {settings.legacy.value}")
    for name in dict.fromkeys((*base_settings.mandatory_jobs, *settings.mandatory_jobs)):
        problems += _problems_of_job(name, jobs, current_attempt)
    return Verdict(tuple(problems))


def _problems_of_job(name: str, jobs: list[Job], current_attempt: int) -> list[str]:
    candidates = [job for job in jobs if job.name == name and job.run_attempt <= current_attempt]
    if not candidates:
        return [f'mandatory job "{name}" is missing from the run']
    attempt = max(job.run_attempt for job in candidates)
    effective = [job for job in candidates if job.run_attempt == attempt]
    failing = [job for job in effective if job.state != SUCCESS]
    if len(effective) > 1 and failing:
        return [f'mandatory job "{name}" (attempt {attempt}) has {len(effective)} entries, not all success']
    return [f'mandatory job "{name}" (attempt {attempt}) concluded {job.state}' for job in failing]


def read_jobs(repo: str, run_id: str, fetch: PageFetcher) -> list[Job]:
    url: str | None = JOBS_URL.format(repo=repo, run_id=run_id)
    jobs: list[Job] = []
    total = None
    for _ in range(MAX_PAGES):
        body, link = fetch(url)
        try:
            page = json.loads(body)
            total = page["total_count"]
            jobs += [Job(j["name"], j["run_attempt"], j["status"], j["conclusion"]) for j in page["jobs"]]
        except (ValueError, KeyError, TypeError) as error:
            raise CannotJudge(f"{url} did not return the expected jobs page: {error!r}") from error
        match = NEXT_LINK.search(link or "")
        if not match:
            if len(jobs) != total:
                raise CannotJudge(f"read {len(jobs)} jobs but the run has {total}")
            return jobs
        url = match.group(1)
    raise CannotJudge(f"the run has more than {MAX_PAGES} pages of jobs")


def github_fetcher(token: str) -> PageFetcher:
    def fetch(url: str) -> tuple[str, str | None]:
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8"), response.headers.get("Link")
        except (urllib.error.URLError, TimeoutError) as error:
            raise CannotJudge(f"cannot read {url}: {error} (does the caller grant actions: read?)") from error

    return fetch


FORBIDDEN_REF_CHARACTERS = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def _is_git_ref_name(ref: str) -> bool:
    """git-check-ref-format's rules, plus no leading '-' so a ref can never read as an option."""
    broken_name = (
        ref.startswith("-") or ref == "@" or ref.endswith(".")
        or ".." in ref or "@{" in ref or FORBIDDEN_REF_CHARACTERS.search(ref)
    )
    broken_component = any(
        part == "" or part.startswith(".") or part.endswith(".lock") for part in ref.split("/")
    )
    return not (broken_name or broken_component)


def _require_safe_ref(ref: str) -> None:
    if not _is_git_ref_name(ref):
        raise CannotJudge(f"{ref!r} is not a ref name git accepts")


def read_base_settings(repo: Path, base_sha: str) -> GateSettings:
    _require_safe_ref(base_sha)
    git = ["git", "-C", str(repo.resolve())]
    listed = subprocess.run([*git, "ls-tree", base_sha, "--", CONFIG_PATH], capture_output=True, text=True, check=False)
    if listed.returncode != 0:
        raise CannotJudge(f"cannot read the base commit {base_sha}: {listed.stderr.strip()}")
    if not listed.stdout.strip():
        return NO_BASE_SETTINGS
    shown = subprocess.run([*git, "show", f"{base_sha}:{CONFIG_PATH}"], capture_output=True, text=True, check=False)
    if shown.returncode != 0:
        raise CannotJudge(f"cannot read {CONFIG_PATH} at the base commit {base_sha}: {shown.stderr.strip()}")
    return load_gate_settings(shown.stdout)
