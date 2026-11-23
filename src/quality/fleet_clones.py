"""The fleet report's pairs between repositories: the `clone` rule's detector, thresholds and function names over the
functions of every measured repository, keeping the pairs whose two sides lie in different repositories."""

from dataclasses import dataclass

from quality import clones, output

COLUMNS = ("Repo", "Function", "Repo", "Function", "Kind")
PROD = "prod"
TEST = "test"


@dataclass(frozen=True, order=True)
class Side:
    repository: str
    path: str
    symbol: str

    @property
    def function(self) -> str:
        return f"{self.path} {self.symbol}"


@dataclass(frozen=True)
class RepositoryPair:
    """Two near-identical functions in two repositories, the sides in sorted order."""

    first: Side
    second: Side

    @property
    def kind(self) -> str:
        return TEST if clones.both_tests(self.first.path, self.second.path) else PROD

    @property
    def order(self) -> tuple[bool, Side, Side]:
        return self.kind == TEST, self.first, self.second

    @property
    def cells(self) -> tuple[str, ...]:
        return self.first.repository, self.first.function, self.second.repository, self.second.function, self.kind


def _side(unit: clones.Unit) -> Side:
    return Side(unit.repository, unit.place.path, unit.place.symbol)


def pairs_between(units: list[clones.Unit]) -> list[RepositoryPair]:
    """The pairs among units tagged by repository whose sides differ in repository, `prod` first, then by sides."""
    found = {RepositoryPair(*sorted((_side(unit), _side(other)))) for unit, other in clones.pairs_of(units, lambda unit: unit.repository)}
    return sorted(found, key=lambda pair: pair.order)


def heading(pairs: list[RepositoryPair]) -> str:
    return f"**Clone pairs between repositories{output.SEPARATOR}{output.counted(len(pairs), 'pair')}**"
