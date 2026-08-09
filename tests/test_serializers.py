import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cmd_config import ConfigError, parse

GRAMMAR = """
setting <key> <value>
round <name> <player> <result>
game define <name> <start> <duration>
game append <name> <player> <sets>
"""

FIXTURE = Path(__file__).parent / "fixture"


@dataclass
class Settings:
    surface: str
    best_of: str


@dataclass
class Round:
    player: str
    result: str


@dataclass
class Game:
    start: str
    duration: str
    player: str
    sets: str


def _parse(name):
    objects = parse(
        (FIXTURE / f"{name}.ccfg").read_text(),
        GRAMMAR,
        serializers={"setting": Settings, "round": Round, "game": Game},
        scalars=("setting",),
        grouped=("round", "game"),
    )
    return NS(**objects)


@pytest.mark.parametrize(
    "name,expected",
    [
        (
            "tournament",
            NS(
                setting=Settings(surface="grass", best_of="5"),
                round={
                    "quarterfinal": [Round("Alcaraz", "win"), Round("Djokovic", "win")],
                    "semifinal": [Round("Alcaraz", "win")],
                },
                game={
                    "Final": [
                        Game("14:00", "3:10", "Alcaraz", "3"),
                        Game("14:00", "3:10", "Djokovic", "1"),
                    ],
                    "Exhibition": [],
                },
            ),
        ),
    ],
)
def test_serializers(name, expected):
    assert _parse(name) == expected


def test_parse_rejects_unknown_command():
    with pytest.raises(ValueError, match=re.escape("commands not in grammar: ['umpire']")):
        parse("", GRAMMAR, scalars=("umpire",))


def test_parse_coerces_scalars_to_dict_factory():
    assert parse("setting surface clay", GRAMMAR, serializers={"setting": dict}, scalars=("setting",)) == {"setting": {"surface": "clay"}}


def test_parse_calls_scalar_factory_when_command_absent():
    with pytest.raises(ConfigError, match="setting: .*missing.*surface"):
        parse("", GRAMMAR, serializers={"setting": Settings}, scalars=("setting",))


def test_parse_rejects_duplicate_scalar_key():
    with pytest.raises(ConfigError, match=re.escape("line 2: duplicate setting 'surface'")):
        parse("setting surface clay\nsetting surface grass", GRAMMAR, serializers={"setting": dict}, scalars=("setting",))


def test_parse_rejects_wrong_scalar_field_count():
    with pytest.raises(ValueError, match=re.escape("scalar command 'round' must have exactly 2 fields")):
        parse("", GRAMMAR, scalars=("round",))


def test_parse_rejects_too_few_grouped_fields():
    with pytest.raises(ValueError, match=re.escape("grouped command 'solo' must have at least 2 fields")):
        parse("", "solo <name>", serializers={"solo": dict}, grouped=("solo",))


def test_parse_rejects_command_in_both_styles():
    with pytest.raises(ValueError, match=re.escape("commands in both scalars and grouped: ['setting']")):
        parse("", GRAMMAR, serializers={"setting": dict}, scalars=("setting",), grouped=("setting",))


def test_grouped_two_fields_accumulates():
    @dataclass
    class Entrant:
        player: str

    objects = parse(
        "round quarterfinal Alcaraz\nround quarterfinal Djokovic",
        "round <name> <player>",
        serializers={"round": Entrant},
        grouped=("round",),
    )
    assert objects["round"]["quarterfinal"] == [Entrant("Alcaraz"), Entrant("Djokovic")]


@pytest.mark.parametrize(
    "name,message",
    [
        ("orphan_append", "line 1: unknown game group 'Nope': define it first"),
    ],
)
def test_serializer_errors(name, message):
    with pytest.raises(ConfigError, match="^" + re.escape(message) + "$"):
        _parse(name)


def test_parse_rejects_style_without_factory():
    with pytest.raises(ValueError, match=re.escape("commands need a factory in serializers: ['round', 'setting']")):
        parse("", GRAMMAR, scalars=("setting",), grouped=("round",))
