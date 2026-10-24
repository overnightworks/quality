import pytest

from quality.pip_requirements import Requirement, RequirementsError, normalised_name, read

HASH = "--hash=sha256:" + "ab" * 32
WEBAUTH_WHEEL = "overnightworks-webauth @ https://github.com/o/w/releases/download/v0.20.1/overnightworks_webauth-0.20.1-py3-none-any.whl"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("pyyaml==6.0.2\n", [Requirement("pyyaml", "6.0.2", 1, is_wheel=False)], id="a plain pin"),
        pytest.param("PyYAML_x.y == 6.0.2", [Requirement("pyyaml-x-y", "6.0.2", 1, is_wheel=False)], id="a name is normalised"),
        pytest.param("# via httpx\n\n   \npyyaml==6.0.2  # why\n", [Requirement("pyyaml", "6.0.2", 4, is_wheel=False)], id="comments and blank lines"),
        pytest.param(
            f'httpx[http2, brotli]==0.27.0 ; python_version >= "3.9" \\\n    {HASH} \\\n    {HASH}\n',
            [Requirement("httpx", "0.27.0", 1, is_wheel=False)], id="extras, a marker and continued hashes",
        ),
        pytest.param(f"{WEBAUTH_WHEEL} {HASH}", [Requirement("overnightworks-webauth", "0.20.1", 1, is_wheel=True)], id="a wheel with a hash"),
        pytest.param(
            f"x==1\nreq-a @ https://h/p/Req_A-2.0+local-1-cp312-cp312-manylinux_2_28_x86_64.whl ; sys_platform == 'linux' \\\n {HASH}\n",
            [Requirement("x", "1", 1, is_wheel=False), Requirement("req-a", "2.0+local", 2, is_wheel=True)], id="a wheel with a marker and a build tag",
        ),
        pytest.param("", [], id="an empty file"),
        pytest.param("# only a comment\n", [], id="only a comment"),
    ],
)
def test_the_two_accepted_forms_are_read_with_their_line(text, expected):
    """#176 C2, S5, W1."""
    assert list(read(text)) == expected


@pytest.mark.parametrize(
    ("text", "line"),
    [
        pytest.param("a==1\nb==2\n-e git+https://example.org/x.git#egg=x\n", 3, id="-e"),
        pytest.param("-egit+https://example.org/x.git\n", 1, id="-e attached"),
        pytest.param("a==1\npyyaml>=5.3\n", 2, id="a minimum version"),
        pytest.param("pyyaml~=5.3\n", 1, id="a compatible release"),
        pytest.param("pyyaml<6,>=5\n", 1, id="a range"),
        pytest.param("pyyaml==5.*\n", 1, id="a wildcard"),
        pytest.param("pyyaml===5.3\n", 1, id="an arbitrary equality"),
        pytest.param("pyyaml\n", 1, id="a bare name"),
        pytest.param("\n\n-r other.txt\n", 3, id="-r"),
        pytest.param("-c constraints.txt\n", 1, id="-c"),
        pytest.param("--index-url https://example.org/simple\n", 1, id="--index-url"),
        pytest.param("--editab\n", 1, id="a misspelt option"),
        pytest.param("https://example.org/p-1.0-py3-none-any.whl\n", 1, id="a bare URL"),
        pytest.param("./local/dir\n", 1, id="a local path"),
        pytest.param(f"a==1 \\\n{HASH}\n-r x.txt\n", 3, id="a line after a continued one"),
        pytest.param(f"a==1 \\\n  b==2 {HASH}\n", 1, id="two pins joined by a continuation"),
        pytest.param("pkg @ https://h/pkg-1.0-py3-none-any.whl\n", 1, id="a wheel without a hash"),
        pytest.param(f"pkg @ https://h/pkg-1.0.tar.gz {HASH}\n", 1, id="an sdist URL"),
        pytest.param(f"pkg @ git+https://h/pkg.git {HASH}\n", 1, id="a git URL"),
        pytest.param(f"pkg @ http://h/pkg-1.0-py3-none-any.whl {HASH}\n", 1, id="a URL that is not https"),
        pytest.param(f"pkg @ https://h/other-1.0-py3-none-any.whl {HASH}\n", 1, id="a wheel named for another package"),
        pytest.param(f"pkg @ https://h/pkg-1.0-py3-none-any.whl?sig=1 {HASH}\n", 1, id="a wheel URL with a query"),
        pytest.param("pyyaml==6.0.2 --no-binary\n", 1, id="a trailing option"),
        pytest.param("pyyaml==6.0.2 ;\n", 1, id="an empty marker"),
    ],
)
def test_any_other_line_is_refused_with_its_number(text, line):
    """#176 S5, S6, W1, W2: an allowlist, so what is not one of the two forms is refused."""
    with pytest.raises(RequirementsError, match=f"^line {line} is neither"):
        read(text)


@pytest.mark.parametrize("name", ["Overnightworks_Webauth", "overnightworks.webauth", "OVERNIGHTWORKS--webauth"])
def test_names_compare_by_pep_503(name):
    assert normalised_name(name) == "overnightworks-webauth"
