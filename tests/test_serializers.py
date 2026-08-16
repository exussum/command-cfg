import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from command_cfg import ConfigError, array, each, group, parse, raw, scalar

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
        serializers={"setting": scalar(Settings), "round": group(Round), "game": group(Game)},
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
    with pytest.raises(
        ValueError,
        match=re.escape("commands not in grammar: ['umpire'] — add a grammar line starting with each or remove them from serializers"),
    ):
        parse("", GRAMMAR, serializers={"umpire": array(dict)})


def test_parse_coerces_scalars_to_dict_factory():
    assert parse("setting surface clay", GRAMMAR, serializers={"setting": scalar(dict)}) == {"setting": {"surface": "clay"}}


def test_parse_yields_none_for_scalar_command_when_absent():
    assert parse("", GRAMMAR, serializers={"setting": scalar(Settings)}) == {"setting": None}


def test_parse_rejects_duplicate_scalar_key():
    with pytest.raises(ConfigError, match=re.escape("line 2: duplicate setting 'surface' — delete this line or change its key")):
        parse("setting surface clay\nsetting surface grass", GRAMMAR, serializers={"setting": scalar(dict)})


def test_parse_rejects_wrong_scalar_field_count():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "scalar command 'round' takes exactly 2 fields, grammar has ['name', 'player', 'result'] — grammar must be 'round <key> <value>'; more fields needs group or array"
        ),
    ):
        parse("", GRAMMAR, serializers={"round": scalar(dict)})


def test_parse_rejects_too_few_grouped_fields():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "group command 'solo' takes at least 2 fields, grammar has ['name'] — add row fields after the group key: 'solo <name> <field>...'"
        ),
    ):
        parse("", "solo <name>", serializers={"solo": group(dict)})


def test_parse_rejects_reserved_grouped_fields():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "group command 'step' uses reserved fields ['append'] — rename them in the grammar; define/append are grouping keywords"
        ),
    ):
        parse("", "step <name> <append>", serializers={"step": group(dict)})


@pytest.mark.parametrize("grammar", ["pick <set> [--set=<v>]", "pick <set> [--set=<set>]"])
def test_parse_rejects_grammar_names_that_normalize_identically(grammar):
    with pytest.raises(
        ValueError,
        match=re.escape("pick grammar: ['--set', '<set>'] normalize to the same key 'set' — rename the option or the positional"),
    ):
        parse("", grammar, serializers={"pick": array(dict)})


@pytest.mark.parametrize("grammar", ["pick [--set=<set>]", "pick <n> [--set=<v>]"])
def test_parse_allows_option_argument_placeholder(grammar):
    parse("", grammar, serializers={"pick": array(dict)})


def test_parse_rejects_bare_callable():
    with pytest.raises(
        ValueError,
        match=re.escape("serializers must be scalar/group/array/raw: ['setting'] are unwrapped — write scalar(Settings), not Settings"),
    ):
        parse("", GRAMMAR, serializers={"setting": dict})


def test_grouped_two_fields_accumulates():
    @dataclass
    class Entrant:
        player: str

    objects = parse(
        "round quarterfinal Alcaraz\nround quarterfinal Djokovic",
        "round <name> <player>",
        serializers={"round": group(Entrant)},
    )
    assert objects["round"]["quarterfinal"] == [Entrant("Alcaraz"), Entrant("Djokovic")]


def test_grouped_include_key_passes_group_key_to_rows():
    @dataclass
    class Entrant:
        name: str
        player: str

    objects = parse(
        "round quarterfinal Alcaraz",
        "round <name> <player>",
        serializers={"round": group(Entrant, include_key=True)},
    )
    assert objects["round"] == {"quarterfinal": [Entrant("quarterfinal", "Alcaraz")]}


def test_grouped_include_key_appends_with_defined_params():
    objects = parse(
        "game define Final 14:00\ngame append Final Alcaraz",
        "game define <id> <start>\ngame append <id> <player>",
        serializers={"game": group(dict, include_key=True)},
    )
    assert objects["game"] == {"Final": [{"id": "Final", "start": "14:00", "player": "Alcaraz"}]}


def test_group_append_to_undefined_group_errors_even_if_rows_exist():
    with pytest.raises(ConfigError, match=re.escape("line 2: unknown game group 'Final' — add 'game define Final ...' on an earlier line")):
        parse(
            "game Final Alcaraz\ngame append Final Djokovic",
            "game <name> <player>\ngame append <name> <player>",
            serializers={"game": group(dict)},
        )


def test_array_accumulates_rows_in_file_order():
    @dataclass
    class Match:
        winner: str
        sets: str

    objects = parse(
        "match Alcaraz 3\nmatch Alcaraz 3",
        "match <winner> <sets>",
        serializers={"match": array(Match)},
    )
    assert objects["match"] == [Match("Alcaraz", "3"), Match("Alcaraz", "3")]


def test_array_rejects_zero_fields():
    with pytest.raises(
        ValueError, match=re.escape("array command 'retire' takes at least 1 field, grammar has none — add '<field>'s to its grammar line")
    ):
        parse("", "retire", serializers={"retire": array(dict)})


def test_absent_group_and_array_commands_yield_empty_containers():
    assert parse("", "round <name> <player>\nmatch <winner>", serializers={"round": group(dict), "match": array(dict)}) == {
        "round": {},
        "match": [],
    }


def test_raw_serializer_collates_its_rows():
    objects = parse(
        "round quarterfinal Alcaraz",
        "round <name> <player>",
        serializers={"round": raw(lambda rows, objects: [values.player for values in rows])},
    )
    assert objects == {"round": ["Alcaraz"]}


def test_cast_resolves_names_against_earlier_objects():
    def cast(key, value, objects):
        if key == "winner" and not any(value == row["player"] for rows in objects["round"].values() for row in rows):
            raise ValueError(f"unknown player {value!r}")
        return value

    objects = parse(
        "round quarterfinal Alcaraz\nmatch Alcaraz",
        "round <name> <player>\nmatch <winner>",
        serializers={"round": group(dict), "match": array(dict)},
        cast=cast,
    )
    assert objects["match"] == [{"winner": "Alcaraz"}]


def test_cast_lookup_failure_carries_line_number():
    def cast(key, value, objects):
        if key == "winner" and not any(value == row["player"] for rows in objects["round"].values() for row in rows):
            raise ValueError(f"unknown player {value!r}")
        return value

    with pytest.raises(ConfigError, match=re.escape("line 2: unknown player 'Zverev'")):
        parse(
            "round quarterfinal Alcaraz\nmatch Zverev",
            "round <name> <player>\nmatch <winner>",
            serializers={"round": group(dict), "match": array(dict)},
            cast=cast,
        )


def test_serializers_run_in_dict_order_and_cast_sees_prior_objects():
    seen = []

    def cast(key, value, objects):
        seen.append((key, sorted(objects)))
        return value

    parse(
        "setting surface grass\nround quarterfinal Alcaraz\nmatch Alcaraz",
        "setting <key> <value>\nround <name> <player>\nmatch <winner>",
        serializers={"setting": scalar(dict), "round": group(dict), "match": array(dict)},
        cast=cast,
    )
    assert sorted(seen) == [("key", []), ("name", ["setting"]), ("player", ["setting"]), ("value", []), ("winner", ["round", "setting"])]


def test_raw_serializer_receives_objects_built_so_far():
    objects = parse(
        "round quarterfinal Alcaraz\nchampion Alcaraz",
        "round <name> <player>\nchampion <player>",
        serializers={"round": group(dict), "champion": raw(lambda rows, objects: (rows[0].player, sorted(objects)))},
    )
    assert objects["champion"] == ("Alcaraz", ["round"])


def test_each_runs_handler_per_line_with_cast_fields():
    def upper(key, value, objects):
        return value.upper() if key == "player" else value

    def collect(objects, row):
        objects.setdefault("players", []).append(row.player)

    objects = parse(
        "round r Alcaraz w\nround r Djokovic l",
        "round <name> <player> <result>",
        serializers={"round": each(collect)},
        cast=upper,
    )
    assert objects == {"players": ["ALCARAZ", "DJOKOVIC"]}


def test_each_sees_earlier_objects_and_locates_handler_error():
    def register(objects, row):
        objects.setdefault("known", set()).add(row.player)

    def check(objects, row):
        if row.winner not in objects["known"]:
            raise ValueError(f"unknown player {row.winner!r}")
        objects.setdefault("wins", []).append(row.winner)

    with pytest.raises(ConfigError, match=re.escape("line 3: unknown player 'Sinner'")):
        parse(
            "round r Alcaraz\nmatch Alcaraz\nmatch Sinner",
            "round <name> <player>\nmatch <winner>",
            serializers={"round": each(register), "match": each(check)},
        )


def test_factory_type_errors_carry_line_numbers():
    with pytest.raises(ConfigError, match="^line 1: "):
        parse("round quarterfinal Alcaraz", "round <name> <player>", serializers={"round": group(Round)})


@pytest.mark.parametrize(
    "name,message",
    [
        ("orphan_append", "line 1: unknown game group 'Nope' — add 'game define Nope ...' on an earlier line"),
    ],
)
def test_serializer_errors(name, message):
    with pytest.raises(ConfigError, match="^" + re.escape(message) + "$"):
        _parse(name)
