import pytest

from tests.support.workflow_steps import run_step, step, step_environment, step_names, step_script, workflow

pytestmark = pytest.mark.integration

NAME_STEP = "Refuse a malformed image, version or build-args"
CHECKOUT_STEP = "Check out the dispatched commit"
BUILD_STEP = "Build the image"
ATTEST_STEP = "Attest the image's build provenance"
LOGIN_STEP = "Log in to GHCR"
PUSHED_STEP = "Name the pushed image"


@pytest.fixture(scope="module")
def image() -> dict:
    return workflow("image.yml")


def run_job_step(image: dict, name: str, directory, **scenario: str):
    tools = directory / "tools"
    tools.mkdir()
    return run_step(
        step_script(image, "image", name), directory, step_environment(image, "image", name, **scenario), tools
    )


def test_the_call_takes_exactly_the_image_interface(image):
    """IMAGE-2, IMAGE-4, IMAGE-5, IMAGE-6, IMAGE-11, IMAGE-12, IMAGE-13, IMAGE-16, IMAGE-18."""
    call = image[True]["workflow_call"]
    assert {
        name: (spec["type"], spec.get("required", False), spec.get("default")) for name, spec in call["inputs"].items()
    } == {
        "context": ("string", False, "."),
        "file": ("string", False, ""),
        "target": ("string", False, ""),
        "image": ("string", False, ""),
        "version": ("string", True, None),
        "build-args": ("string", False, ""),
        "push": ("boolean", True, None),
        "cache": ("boolean", False, False),
        "submodules": ("boolean", False, False),
        "reproducible-layers": ("boolean", False, False),
        "runner": ("string", False, "ubuntu-latest"),
    }
    assert {name: spec["required"] for name, spec in call["secrets"].items()} == {"build-secrets": False}
    assert {name: spec["value"] for name, spec in call["outputs"].items()} == {
        "digest": "${{ jobs.image.outputs.digest }}",
        "ref": "${{ jobs.image.outputs.ref }}",
    }
    assert image["jobs"]["image"]["outputs"] == {
        "digest": "${{ steps.pushed.outputs.digest }}",
        "ref": "${{ steps.pushed.outputs.ref }}",
    }
    assert step(image, "image", PUSHED_STEP)["id"] == "pushed"
    assert step(image, "image", NAME_STEP)["id"] == "name"


def test_the_job_runs_hosted_with_the_inputs_in_its_environment(image):
    """IMAGE-7, IMAGE-15, IMAGE-18."""
    job = image["jobs"]["image"]
    assert job["runs-on"] == "${{ inputs.runner }}"
    assert job["timeout-minutes"] == 60
    assert job["env"] == {
        "IMAGE": "${{ inputs.image }}",
        "VERSION": "${{ inputs.version }}",
        "BUILD_ARGS": "${{ inputs.build-args }}",
        "REPOSITORY": "${{ github.repository }}",
    }


def test_the_steps_run_in_the_order_that_lets_the_push_succeed(image):
    """IMAGE-3, IMAGE-4."""
    assert step_names(image, "image") == [
        NAME_STEP,
        CHECKOUT_STEP,
        "Set up Buildx",
        LOGIN_STEP,
        BUILD_STEP,
        ATTEST_STEP,
        PUSHED_STEP,
    ]


def test_each_step_uses_its_named_action(image):
    """IMAGE-9, IMAGE-10."""
    assert {
        name: step(image, "image", name)["uses"].split("@")[0]
        for name in (CHECKOUT_STEP, "Set up Buildx", LOGIN_STEP, BUILD_STEP, ATTEST_STEP)
    } == {
        CHECKOUT_STEP: "actions/checkout",
        "Set up Buildx": "docker/setup-buildx-action",
        LOGIN_STEP: "docker/login-action",
        BUILD_STEP: "docker/build-push-action",
        ATTEST_STEP: "actions/attest",
    }


def test_the_checkout_is_the_dispatched_commit_without_credentials(image):
    """IMAGE-8, IMAGE-12."""
    assert step(image, "image", CHECKOUT_STEP)["with"] == {
        "ref": "${{ github.sha }}",
        "persist-credentials": False,
        "submodules": "${{ inputs.submodules }}",
    }


def test_buildx_reads_no_cache_binary_and_login_uses_the_actor_token(image):
    """IMAGE-14."""
    assert step(image, "image", "Set up Buildx")["with"] == {"cache-binary": False}
    assert step(image, "image", LOGIN_STEP)["with"] == {
        "registry": "ghcr.io",
        "username": "${{ github.actor }}",
        "password": "${{ github.token }}",
    }


def test_the_naming_step_reads_the_name_and_the_built_digest_and_the_name_step_has_no_env(image):
    """IMAGE-13."""
    assert "env" not in step(image, "image", NAME_STEP)
    assert step(image, "image", PUSHED_STEP)["env"] == {
        "NAME": "${{ steps.name.outputs.name }}",
        "DIGEST": "${{ steps.build.outputs.digest }}",
    }


def test_only_the_image_job_holds_the_four_grants(image):
    """IMAGE-1."""
    assert image["permissions"] == {}
    assert list(image["jobs"]) == ["image"]
    assert image["jobs"]["image"]["permissions"] == {
        "attestations": "write",
        "contents": "read",
        "id-token": "write",
        "packages": "write",
    }


def test_the_build_is_amd64_with_max_provenance_and_an_sbom(image):
    """IMAGE-4, IMAGE-5, IMAGE-6, IMAGE-7, IMAGE-9, IMAGE-11, IMAGE-14, IMAGE-17.

    With reproducible-layers off, both expressions are empty. An empty outputs adds no --output argument, and an empty
    SOURCE_DATE_EPOCH is read as unset, as the pinned action and buildx read them, so the command is today's. The
    constant is the quoted string '0': an unquoted 0 is falsy in an expression and would turn the option off.
    """
    build = step(image, "image", BUILD_STEP)
    assert {option: value for option, value in build["with"].items() if option != "build-args"} == {
        "context": "${{ inputs.context }}",
        "file": "${{ inputs.file }}",
        "target": "${{ inputs.target }}",
        "secrets": "${{ secrets.build-secrets }}",
        "labels": "org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}",
        "platforms": "linux/amd64",
        "tags": "${{ steps.name.outputs.name }}:${{ inputs.version }}",
        "push": "${{ inputs.push }}",
        "provenance": "mode=max",
        "sbom": True,
        "cache-from": "${{ inputs.cache && 'type=gha' || '' }}",
        "cache-to": "${{ inputs.cache && 'type=gha,mode=max' || '' }}",
        "outputs": "${{ inputs.reproducible-layers && 'type=image,rewrite-timestamp=true' || '' }}",
    }
    assert build["env"] == {"SOURCE_DATE_EPOCH": "${{ inputs.reproducible-layers && '0' || '' }}"}


def test_the_attestation_is_pushed_provenance_of_the_built_digest(image):
    """IMAGE-10."""
    attest = step(image, "image", ATTEST_STEP)
    build = step(image, "image", BUILD_STEP)
    assert attest["if"] == "inputs.push"
    assert attest["with"] == {
        "subject-name": "${{ steps.name.outputs.name }}",
        "subject-digest": "${{ steps.build.outputs.digest }}",
        "push-to-registry": True,
        "create-storage-record": False,
    }
    assert build["id"] == "build"
    assert attest["with"]["subject-name"] in build["with"]["tags"]


def test_only_login_attest_and_naming_depend_on_push(image):
    """IMAGE-10, IMAGE-13."""
    steps = image["jobs"]["image"]["steps"]
    assert {candidate["name"]: candidate["if"] for candidate in steps if "if" in candidate} == {
        LOGIN_STEP: "inputs.push",
        ATTEST_STEP: "inputs.push",
        PUSHED_STEP: "inputs.push",
    }
    assert all("continue-on-error" not in candidate for candidate in steps)
    assert "continue-on-error" not in image["jobs"]["image"]


@pytest.mark.parametrize(
    ("image_name", "expected"),
    [
        ("", "ghcr.io/overnightworks/songmaker"),
        ("worker", "ghcr.io/overnightworks/songmaker/worker"),
        ("scoring/worker-gpu", "ghcr.io/overnightworks/songmaker/scoring/worker-gpu"),
    ],
    ids=["repository itself", "sub-name", "nested sub-name"],
)
def test_the_image_is_named_after_the_calling_repository(image, tmp_path, image_name, expected):
    """IMAGE-2."""
    result = run_job_step(
        image,
        NAME_STEP,
        tmp_path,
        REPOSITORY="OvernightWorks/SongMaker",
        IMAGE=image_name,
        VERSION="1.2.3",
        BUILD_ARGS="",
    )
    assert result.returncode == 0
    assert result.outputs.splitlines()[0] == f"name={expected}"


@pytest.mark.parametrize(
    ("image_name", "version", "build_args", "refusal"),
    [
        ("Worker", "1.2.3", "", "Refused: the image Worker is not lowercase path components."),
        ("a//b", "1.2.3", "", "Refused: the image a//b is not lowercase path components."),
        ("../x", "1.2.3", "", "Refused: the image ../x is not lowercase path components."),
        ("-x", "1.2.3", "", "Refused: the image -x is not lowercase path components."),
        ("", "", "", "Refused: the version  is not a valid Docker tag."),
        ("", "a b", "", "Refused: the version a b is not a valid Docker tag."),
        ("", "-x", "", "Refused: the version -x is not a valid Docker tag."),
        ("", "a:b", "", "Refused: the version a:b is not a valid Docker tag."),
        ("", "a" * 129, "", f"Refused: the version {'a' * 129} is not a valid Docker tag."),
        ("", "a b", "SERVICE_VERSION=0.0.1", "Refused: the version a b is not a valid Docker tag."),
    ],
    ids=[
        "uppercase",
        "double slash",
        "dot dot",
        "leading dash",
        "empty version",
        "space",
        "leading dash version",
        "colon",
        "129 characters",
        "version before build-args",
    ],
)
def test_a_malformed_image_or_version_is_refused(image, tmp_path, image_name, version, build_args, refusal):
    """IMAGE-3, IMAGE-4."""
    result = run_job_step(
        image, NAME_STEP, tmp_path, REPOSITORY="o/r", IMAGE=image_name, VERSION=version, BUILD_ARGS=build_args
    )
    assert result.returncode == 1
    assert result.stderr == f"{refusal}\n"
    assert result.outputs == ""


def parsed_outputs(outputs: str) -> dict[str, str]:
    """The step outputs, read as GitHub reads a GITHUB_OUTPUT file: `key=value` lines and `key<<delimiter` entries."""
    parsed: dict[str, str] = {}
    lines = iter(outputs.splitlines())
    for line in lines:
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            key, delimiter = line.split("<<", 1)
            body = []
            for entry in lines:
                if entry == delimiter:
                    break
                body.append(entry)
            else:
                pytest.fail(f"the entry {key} is never closed by {delimiter}")
            parsed[key] = "\n".join(body)
        else:
            key, _, value = line.partition("=")
            parsed[key] = value
    return parsed


def composed_build_args(outputs: str) -> list[str]:
    """The lines of the `build-args` step output."""
    return parsed_outputs(outputs)["build-args"].splitlines()


@pytest.mark.parametrize(
    ("caller_lines", "expected"),
    [
        ("", []),
        ("A=1\nB=two words", ["A=1", "B=two words"]),
        ("SERVICE_VERSION_X=1", ["SERVICE_VERSION_X=1"]),
        ("X_SERVICE_VERSION=1", ["X_SERVICE_VERSION=1"]),
        ("A=1\nEOF\nname=evil", ["A=1", "EOF", "name=evil"]),
        ("build-args<<EOF\nname=evil", ["build-args<<EOF", "name=evil"]),
    ],
    ids=[
        "empty",
        "caller lines",
        "name that starts with it",
        "name that ends with it",
        "output assignment after a delimiter-like line",
        "output entry header",
    ],
)
def test_the_build_arguments_hold_every_caller_line_then_the_version(image, tmp_path, caller_lines, expected):
    """IMAGE-6, IMAGE-19."""
    result = run_job_step(
        image, NAME_STEP, tmp_path, REPOSITORY="o/r", IMAGE="", VERSION="1.2.3", BUILD_ARGS=caller_lines
    )
    assert result.returncode == 0
    outputs = parsed_outputs(result.outputs)
    assert outputs["name"] == "ghcr.io/o/r"
    assert composed_build_args(result.outputs) == [*expected, "SERVICE_VERSION=1.2.3"]
    assert outputs.keys() == {"name", "build-args"}


@pytest.mark.parametrize(
    "caller_lines",
    [
        "SERVICE_VERSION=0.0.1",
        "A=1\nSERVICE_VERSION=",
        "  SERVICE_VERSION=2",
        "SERVICE_VERSION",
        '"SERVICE_VERSION=9"',
        '"SERVICE_VERSION',
    ],
    ids=["alone", "after another line", "indented", "without a value", "double-quoted", "double-quoted without a value"],
)
def test_a_caller_build_argument_named_service_version_is_refused(image, tmp_path, caller_lines):
    """IMAGE-19."""
    result = run_job_step(
        image, NAME_STEP, tmp_path, REPOSITORY="o/r", IMAGE="", VERSION="1.2.3", BUILD_ARGS=caller_lines
    )
    assert result.returncode == 1
    assert result.stderr == "Refused: build-args sets SERVICE_VERSION, which image.yml sets from version.\n"
    assert result.outputs == ""


def test_the_build_reads_the_composed_build_arguments(image):
    """IMAGE-19."""
    name_id = step(image, "image", NAME_STEP)["id"]
    assert step(image, "image", BUILD_STEP)["with"]["build-args"] == f"${{{{ steps.{name_id}.outputs.build-args }}}}"


@pytest.mark.parametrize("version", ["v2026.11.06.2", "1.2.3", "a" * 128], ids=["dated", "semantic", "128 characters"])
def test_a_dated_or_semantic_version_is_accepted(image, tmp_path, version):
    """IMAGE-4."""
    result = run_job_step(image, NAME_STEP, tmp_path, REPOSITORY="o/r", IMAGE="", VERSION=version, BUILD_ARGS="")
    assert result.returncode == 0


def test_the_pushed_image_is_referred_to_by_tag_and_digest(image, tmp_path):
    """IMAGE-13."""
    digest = f"sha256:{'ab' * 32}"
    result = run_job_step(
        image,
        PUSHED_STEP,
        tmp_path,
        REPOSITORY="o/r",
        IMAGE="",
        NAME="ghcr.io/o/r/worker",
        DIGEST=digest,
        VERSION="v2026.11.06.2",
        BUILD_ARGS="",
    )
    assert result.returncode == 0
    assert result.outputs == f"digest={digest}\nref=ghcr.io/o/r/worker:v2026.11.06.2@{digest}\n"


def test_no_run_value_interpolates_an_expression(image):
    """IMAGE-15."""
    assert all("${{" not in candidate["run"] for candidate in image["jobs"]["image"]["steps"] if "run" in candidate)
