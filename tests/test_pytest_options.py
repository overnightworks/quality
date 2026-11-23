import argparse

import pytest
from _pytest.config import _prepareconfig

from quality import pytest_options

DECLARED_SPELLINGS = [
    pytest.param(pytest_options.WORKERS_NAMES, id="workers"),
    pytest.param(pytest_options.LISTING_NAMES, id="listing"),
    pytest.param(pytest_options.PLUGIN_NAMES, id="plugin"),
    pytest.param(pytest_options.VERBOSE_NAMES, id="verbose"),
    pytest.param(pytest_options.QUIET_NAMES, id="quiet"),
    pytest.param(pytest_options.VERBOSITY_NAMES, id="verbosity"),
    pytest.param(pytest_options.HEADER_NAMES, id="header"),
    pytest.param(pytest_options.SEED_NAMES, id="seed"),
    pytest.param(pytest_options.REORDER_NAMES, id="reorder"),
    pytest.param(pytest_options.AUTOLOAD_NAMES, id="autoload"),
]


@pytest.fixture(scope="module")
def parser() -> argparse.ArgumentParser:
    """The parser pytest builds at start-up with the plugins installed here."""
    return _prepareconfig([], None)._parser.optparser


@pytest.fixture(scope="module")
def actions(parser) -> dict[str, argparse.Action]:
    return {name: action for action in parser._actions for name in action.option_strings}


def takes_value(action: argparse.Action) -> bool:
    return action.nargs != 0


def test_the_short_value_letters_are_the_ones_the_parsers_declare(actions):
    letters = {name[1] for name, action in actions.items() if len(name) == 2 and takes_value(action)}
    assert letters == set(pytest_options.VALUE_LETTERS)


@pytest.mark.parametrize("names", DECLARED_SPELLINGS)
def test_each_spelling_of_an_option_the_rules_read_is_declared_with_its_value_taking(actions, names):
    action = actions[next(iter(names))]
    spellings = {name for name in action.option_strings}
    assert spellings == set(names)
    declared = {name for name in names if (name[1] in pytest_options.VALUE_LETTERS if len(name) == 2 else name in pytest_options.LONG_VALUE_OPTIONS)}
    assert declared == (set(names) if takes_value(action) else set())


def test_the_parser_refuses_an_abbreviated_long_option(parser):
    assert parser.allow_abbrev is False
