import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cmd_config import ConfigError, parse, serializers

GRAMMAR = """
setting <key> <value>
round <name> <player> <result>
game define <name> <start> <duration>
game append <name> <player> <sets>
"""

FIXTURE = Path(__file__).parent / "fixture"


def _parse(name):
    styles = serializers(GRAMMAR, scalars=("setting",), grouped=("round", "game"))
    objects = parse((FIXTURE / f"{name}.ccfg").read_text(), GRAMMAR, styles)
    return NS(**objects)


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
def test_serializers(name, expected):
    assert _parse(name) == expected


def test_serializers_rejects_unknown_command():
    with pytest.raises(ValueError, match=re.escape("commands not in grammar: ['umpire']")):
        serializers(GRAMMAR, scalars=("umpire",))


def test_serializers_rejects_wrong_scalar_field_count():
    with pytest.raises(ValueError, match=re.escape("scalar command 'round' must have exactly 2 fields")):
        serializers(GRAMMAR, scalars=("round",))


def test_serializers_rejects_too_few_grouped_fields():
    with pytest.raises(ValueError, match=re.escape("grouped command 'solo' must have at least 2 fields")):
        serializers("solo <name>", grouped=("solo",))


def test_serializers_rejects_command_in_both_styles():
    with pytest.raises(ValueError, match=re.escape("commands in both scalars and grouped: ['setting']")):
        serializers(GRAMMAR, scalars=("setting",), grouped=("setting",))


def test_grouped_two_fields_accumulates():
    styles = serializers("round <name> <player>", grouped=("round",))
    objects = parse("round quarterfinal Alcaraz\nround quarterfinal Djokovic", "round <name> <player>", styles)
    assert objects["round"].quarterfinal == [NS(player="Alcaraz"), NS(player="Djokovic")]


@pytest.mark.parametrize(
    "name,message",
    [
        ("orphan_append", "line 1: unknown game group 'Nope': define it first"),
    ],
)
def test_serializer_errors(name, message):
    with pytest.raises(ConfigError, match="^" + re.escape(message) + "$"):
        _parse(name)
