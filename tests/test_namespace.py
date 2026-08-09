import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cmd_config import ConfigError, namespace, parse

GRAMMAR = """
setting <key> <value>
round <name> <player> <result>
game define <name> <start> <duration>
game append <name> <player> <sets>
"""

FIXTURE = Path(__file__).parent / "fixture"


def _parse(name):
    serializers, result = namespace(GRAMMAR)
    parse((FIXTURE / f"{name}.ccfg").read_text(), GRAMMAR, serializers)
    return result()


@pytest.mark.parametrize(
    "name,expected",
    [
        (
            "tournament",
            NS(
                surface="grass",
                best_of="5",
                round=NS(
                    quarterfinal=[NS(player="Alcaraz", result="win"), NS(player="Djokovic", result="win")],
                    semifinal=[NS(player="Alcaraz", result="win")],
                ),
                game=NS(
                    Final=[
                        NS(start="14:00", duration="3:10", player="Alcaraz", sets="3"),
                        NS(start="14:00", duration="3:10", player="Djokovic", sets="1"),
                    ],
                    Exhibition=[],
                ),
            ),
        ),
    ],
)
def test_namespace(name, expected):
    assert _parse(name) == expected


@pytest.mark.parametrize(
    "name,message",
    [
        ("orphan_append", "line 1: unknown game group 'Nope': define it first"),
    ],
)
def test_namespace_errors(name, message):
    with pytest.raises(ConfigError, match="^" + re.escape(message) + "$"):
        _parse(name)
