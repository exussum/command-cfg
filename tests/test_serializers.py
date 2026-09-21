import re
from collections import defaultdict
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from command_cfg import ConfigError, array, each, group, load, raw, scalar

GRAMMAR = """
setting <key> <value>
round <name> <player> <result>
game define <name> <start> <duration>
game append <name> <player> <sets>
"""

FIXTURE = Path(__file__).parent / "fixture"


def _int(value, objects):
    return int(value)


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


def _game(starts, objects, row):
    groups: dict[str, list[Game]] = objects["game"]
    if row.define:
        starts[row.name] = (row.start, row.duration)
        groups[row.name] = []
    elif row.append:
        if row.name not in starts:
            raise ValueError(f"unknown game group {row.name!r} — add 'game define {row.name} ...' on an earlier line")
        start, duration = starts[row.name]
        groups[row.name].append(Game(start, duration, row.player, row.sets))


def _parse(name):
    objects = load(
        (FIXTURE / f"{name}.ccfg").read_text(),
        GRAMMAR,
        serializers={"setting": scalar(Settings), "round": group(Round), "game": each(partial(_game, {}), default=dict)},
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
        load("", GRAMMAR, serializers={"umpire": array(dict)})


def test_parse_coerces_scalars_to_dict_factory():
    assert load("setting surface clay", GRAMMAR, serializers={"setting": scalar(dict)}) == {"setting": {"surface": "clay"}}


def test_parse_yields_none_for_scalar_command_when_absent():
    assert load("", GRAMMAR, serializers={"setting": scalar(Settings)}) == {"setting": None}


def test_parse_rejects_duplicate_scalar_key():
    with pytest.raises(ConfigError, match=re.escape("line 2: duplicate setting 'surface' — delete this line or change its key")):
        load("setting surface clay\nsetting surface grass", GRAMMAR, serializers={"setting": scalar(dict)})


def test_scalar_types_coerce_by_key():
    objects = load(
        "setting surface clay\nsetting best_of 5",
        GRAMMAR,
        serializers={"setting": scalar(dict, types={"best_of": _int})},
    )
    assert objects["setting"] == {"surface": "clay", "best_of": 5}


def test_scalar_types_default_to_str_for_unlisted_keys():
    objects = load("setting surface clay", GRAMMAR, serializers={"setting": scalar(dict, types={"best_of": _int})})
    assert objects["setting"] == {"surface": "clay"}


def test_group_types_coerce_by_field_name():
    objects = load("round q Alcaraz 6", GRAMMAR, serializers={"round": group(dict, types={"result": int})})
    assert objects["round"] == {"q": [{"player": "Alcaraz", "result": 6}]}


def test_array_types_coerce_by_field_name():
    objects = load("game append Final Alcaraz 3", GRAMMAR, serializers={"game": array(dict, types={"sets": int})})
    assert objects["game"] == [{"name": "Final", "player": "Alcaraz", "sets": 3, "append": True, "define": False}]


def test_each_types_coerce_before_the_handler_runs():
    seen = []
    objects = load(
        "round q Alcaraz 6",
        GRAMMAR,
        serializers={"round": each(lambda objects, row: seen.append((row.result, type(row.result))), types={"result": int})},
    )
    assert seen == [(6, int)]
    assert objects == {}


def test_raw_types_coerce_the_rows_it_collates():
    objects = load(
        "round q Alcaraz 6",
        GRAMMAR,
        serializers={"round": raw(lambda rows, objects: [vars(r) for r in rows], types={"result": int})},
    )
    assert objects["round"] == [{"name": "q", "player": "Alcaraz", "result": 6}]


def test_parse_rejects_wrong_scalar_field_count():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "scalar command 'round' takes exactly 2 fields, grammar has ['name', 'player', 'result'] — grammar must be "
            "'round <key> <value>'; more fields needs group or array"
        ),
    ):
        load("", GRAMMAR, serializers={"round": scalar(dict)})


def test_parse_rejects_too_few_grouped_fields():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "group command 'solo' takes at least 2 fields, grammar has ['name'] — add row fields after the group key: "
            "'solo <name> <field>...'"
        ),
    ):
        load("", "solo <name>", serializers={"solo": group(dict)})


@pytest.mark.parametrize("grammar", ["pick <set> [--set=<v>]", "pick <set> [--set=<set>]"])
def test_parse_rejects_grammar_names_that_normalize_identically(grammar):
    with pytest.raises(
        ValueError,
        match=re.escape("pick grammar: ['--set', '<set>'] normalize to the same key 'set' — rename the option or the positional"),
    ):
        load("", grammar, serializers={"pick": array(dict)})


@pytest.mark.parametrize("grammar", ["pick [--set=<set>]", "pick <n> [--set=<v>]"])
def test_parse_allows_option_argument_placeholder(grammar):
    load("", grammar, serializers={"pick": array(dict)})


def test_untyped_placeholder_defaults_to_str():
    objects = load("match Alcaraz 3", "match <winner> <sets>", serializers={"match": array(dict)})
    assert objects["match"] == [{"winner": "Alcaraz", "sets": "3"}]


def test_placeholder_accepts_an_explicitly_empty_value():
    objects = load("match Alcaraz ''", "match <winner> <sets>", serializers={"match": array(dict)})
    assert objects["match"] == [{"winner": "Alcaraz", "sets": ""}]


def test_repeated_placeholder_collects_a_list():
    objects = load("pick Alice red green blue", "pick <name> <tag>...", serializers={"pick": array(dict)})
    assert objects["pick"] == [{"name": "Alice", "tag": ["red", "green", "blue"]}]


def test_repeated_placeholder_absent_defaults_to_empty_list():
    objects = load("pick Alice", "pick <name> [<tag>...]", serializers={"pick": array(dict)})
    assert objects["pick"] == [{"name": "Alice", "tag": []}]


def test_repeated_placeholder_default_does_not_leak_across_lines():
    objects = load(
        "pick Alice red green\npick Bob",
        "pick <name> [<tag>...]",
        serializers={"pick": array(dict)},
    )
    assert objects["pick"] == [{"name": "Alice", "tag": ["red", "green"]}, {"name": "Bob", "tag": []}]


@pytest.mark.parametrize(
    "grammar",
    [
        "pick single <name> <tag>\npick multi <name> <tag>...",
        "pick multi <name> <tag>...\npick single <name> <tag>",
    ],
)
def test_repeated_placeholder_marking_is_order_independent_across_lines(grammar):
    objects = load("pick single Alice red", grammar, serializers={"pick": array(dict)})
    assert objects["pick"] == [{"name": "Alice", "tag": ["red"], "single": True, "multi": False}]


@pytest.mark.parametrize(
    "grammar",
    [
        "x single <a> [--room=<r>]\nx multi <a> [--room=<r>]...",
        "x multi <a> [--room=<r>]...\nx single <a> [--room=<r>]",
    ],
)
def test_repeated_option_marking_is_order_independent_across_lines(grammar):
    objects = load("x single val --room Bedroom", grammar, serializers={"x": array(dict)})
    assert objects["x"] == [{"a": "val", "room": ["Bedroom"], "single": True, "multi": False}]


def test_duplicate_non_repeatable_option_raises():
    with pytest.raises(ConfigError, match=re.escape("'--room' given more than once")):
        load("x val --room Bedroom --room Kitchen", "x <a> [--room=<r>]", serializers={"x": array(dict)})


def test_repeated_option_collects_a_list():
    objects = load("x val --room Bedroom --room Kitchen", "x <a> [--room=<r>]...", serializers={"x": array(dict)})
    assert objects["x"] == [{"a": "val", "room": ["Bedroom", "Kitchen"]}]


def test_repeated_option_given_once_is_still_a_list():
    objects = load("x val --room Bedroom", "x <a> [--room=<r>]...", serializers={"x": array(dict)})
    assert objects["x"] == [{"a": "val", "room": ["Bedroom"]}]


def test_repeated_option_absent_defaults_to_empty_list():
    objects = load("x val", "x <a> [--room=<r>]...", serializers={"x": array(dict)})
    assert objects["x"] == [{"a": "val", "room": []}]


def test_repeated_option_default_does_not_leak_across_lines():
    objects = load(
        "x val1 --room Bedroom\nx val2",
        "x <a> [--room=<r>]...",
        serializers={"x": array(dict)},
    )
    assert objects["x"] == [{"a": "val1", "room": ["Bedroom"]}, {"a": "val2", "room": []}]


def test_option_argument_coerces_via_serializer_types():
    objects = load("pick --set=3", "pick [--set=<v>]", serializers={"pick": array(dict, types={"set": int})})
    assert objects["pick"] == [{"set": 3}]


def test_repeated_placeholder_elements_coerce_via_serializer_types():
    objects = load("pick Alice 1 2 3", "pick <name> <tag>...", serializers={"pick": array(dict, types={"tag": int})})
    assert objects["pick"] == [{"name": "Alice", "tag": [1, 2, 3]}]


def test_parse_rejects_bare_callable():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "serializers must be scalar/group/array/raw/each: ['setting'] are unwrapped — write scalar(Settings), not Settings"
        ),
    ):
        load("", GRAMMAR, serializers={"setting": dict})


def test_grouped_two_fields_accumulates():
    @dataclass
    class Entrant:
        player: str

    objects = load(
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

    objects = load(
        "round quarterfinal Alcaraz",
        "round <name> <player>",
        serializers={"round": group(Entrant, include_key=True)},
    )
    assert objects["round"] == {"quarterfinal": [Entrant("quarterfinal", "Alcaraz")]}


def test_array_accumulates_rows_in_file_order():
    @dataclass
    class Match:
        winner: str
        sets: str

    objects = load(
        "match Alcaraz 3\nmatch Alcaraz 3",
        "match <winner> <sets>",
        serializers={"match": array(Match)},
    )
    assert objects["match"] == [Match("Alcaraz", "3"), Match("Alcaraz", "3")]


def test_array_rejects_zero_fields():
    with pytest.raises(
        ValueError, match=re.escape("array command 'retire' takes at least 1 field, grammar has none — add '<field>'s to its grammar line")
    ):
        load("", "retire", serializers={"retire": array(dict)})


def test_absent_group_and_array_commands_yield_empty_containers():
    assert load("", "round <name> <player>\nmatch <winner>", serializers={"round": group(dict), "match": array(dict)}) == {
        "round": {},
        "match": [],
    }


def test_raw_serializer_collates_its_rows():
    objects = load(
        "round quarterfinal Alcaraz",
        "round <name> <player>",
        serializers={"round": raw(lambda rows, objects: [values.player for values in rows])},
    )
    assert objects == {"round": ["Alcaraz"]}


def test_raw_serializer_receives_objects_built_so_far():
    objects = load(
        "round quarterfinal Alcaraz\nchampion Alcaraz",
        "round <name> <player>\nchampion <player>",
        serializers={"round": group(dict), "champion": raw(lambda rows, objects: (rows[0].player, sorted(objects)))},
    )
    assert objects["champion"] == ("Alcaraz", ["round"])


def test_each_runs_handler_per_line():
    players = defaultdict(list)

    def collect(objects, row):
        players["players"].append(row.player)

    load(
        "round r Alcaraz w\nround r Djokovic l",
        "round <name> <player> <result>",
        serializers={"round": each(collect)},
    )
    assert players == {"players": ["Alcaraz", "Djokovic"]}


def test_each_sees_earlier_objects_and_locates_handler_error():
    wins = defaultdict(list)

    def register(objects, row):
        objects.setdefault("known", set()).add(row.player)

    def check(objects, row):
        if row.winner not in objects["known"]:
            raise ValueError(f"unknown player {row.winner!r}")
        wins["wins"].append(row.winner)

    with pytest.raises(ConfigError, match=re.escape("line 3: unknown player 'Sinner'")):
        load(
            "round r Alcaraz\nmatch Alcaraz\nmatch Sinner",
            "round <name> <player>\nmatch <winner>",
            serializers={"round": each(register), "match": each(check)},
        )


def test_each_default_seeds_objects_under_command_name():
    def collect(objects, row):
        objects["round"].append(row.player)

    objects = load(
        "round r Alcaraz\nround r Djokovic",
        "round <name> <player>",
        serializers={"round": each(collect, default=list)},
    )
    assert objects == {"round": ["Alcaraz", "Djokovic"]}


def test_each_default_is_present_and_fresh_when_command_absent():
    serializer = {"round": each(lambda objects, row: None, default=list)}
    assert load("", "round <name> <player>", serializers=serializer) == {"round": []}
    assert load("", "round <name> <player>", serializers=serializer) == {"round": []}  # not shared across parses


def test_each_default_must_be_keyword():
    with pytest.raises(TypeError):
        each(lambda objects, row: None, list)


def test_parse_rejects_command_with_hyphen():
    with pytest.raises(ValueError, match=re.escape("commands must not contain '-': ['ad-hoc']")):
        load("", "ad-hoc <name> <player>", serializers={"ad-hoc": each(lambda objects, row: None)})


def test_factory_type_errors_carry_line_numbers():
    with pytest.raises(ConfigError, match="^line 1: "):
        load("round quarterfinal Alcaraz", "round <name> <player>", serializers={"round": group(Round)})


@pytest.mark.parametrize(
    "name,message",
    [
        ("orphan_append", "line 1: unknown game group 'Nope' — add 'game define Nope ...' on an earlier line"),
    ],
)
def test_serializer_errors(name, message):
    with pytest.raises(ConfigError, match="^" + re.escape(message) + "$"):
        _parse(name)
