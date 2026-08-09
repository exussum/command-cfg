import re
from pathlib import Path

import pytest

from cmd_config import ConfigError, parse

GRAMMAR = """
seed <player> <rank>
match define <id> <name>
match append <id> <player> <score> [--set=<set>]
umpire <name>
"""

FIXTURE = Path(__file__).parent / "fixture"


def _parse(name):
    rows = []

    def record(values):
        rank = getattr(values, "rank", None)
        if rank is not None and not rank.isdigit():
            raise ValueError(f"not a number: {rank!r}")
        rows.append(vars(values))

    parse((FIXTURE / f"{name}.ccfg").read_text(), GRAMMAR, {"seed": record, "match": record})
    return rows


@pytest.mark.parametrize(
    "name,expected",
    [
        (
            "seeds",
            [
                {"player": "Alcaraz", "rank": "1"},
                {"player": "Djokovic", "rank": "2"},
            ],
        ),
        (
            "ditto",
            [
                {
                    "define": True,
                    "append": False,
                    "id": "WIMBLEDON_FINAL",
                    "name": "Wimbledon Final",
                    "player": None,
                    "score": None,
                    "set": None,
                },
                {
                    "define": False,
                    "append": True,
                    "id": "WIMBLEDON_FINAL",
                    "name": None,
                    "player": "Alcaraz",
                    "score": "6",
                    "set": "1",
                },
                {
                    "define": False,
                    "append": True,
                    "id": "WIMBLEDON_FINAL",
                    "name": None,
                    "player": "Djokovic",
                    "score": "4",
                    "set": None,
                },
            ],
        ),
    ],
)
def test_parse(name, expected):
    assert _parse(name) == expected


@pytest.mark.parametrize(
    "name,message",
    [
        ("unknown_command", "line 2: unknown command 'rank'"),
        ("usage_mismatch", "line 1: 'seed Alcaraz' does not match 'seed <player> <rank>'"),
        ("ditto_no_previous", "line 1: '.' has nothing above it to repeat"),
        ("ditto_command", "line 2: unknown command '.'"),
        ("no_serializer", "line 1: no serializer for 'umpire'"),
        ("serializer_error", "line 2: not a number: 'best'"),
    ],
)
def test_parse_errors(name, message):
    with pytest.raises(ConfigError, match="^" + re.escape(message) + "$"):
        _parse(name)
