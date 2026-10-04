import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import command_cfg
from command_cfg import ConfigError, load, raw

GRAMMAR = """
seed <player> <rank>
match define <id> <name>
match append <id> <player> <score> [--set=<v>]
umpire <name>
"""

FINAL = {"define": True, "append": False, "id": "WIMBLEDON_FINAL", "name": "Wimbledon Final", "player": None, "score": None, "set": None}
ALCARAZ = {"define": False, "append": True, "id": "WIMBLEDON_FINAL", "name": None, "player": "Alcaraz", "score": "6", "set": "1"}
ALCARAZ_NO_SET = ALCARAZ | {"set": None}
DJOKOVIC = {"define": False, "append": True, "id": "WIMBLEDON_FINAL", "name": None, "player": "Djokovic", "score": "4", "set": None}

FIXTURE = Path(__file__).parent / "fixture"


def _parse(name, variables=None):
    def record(rows, objects):
        for values in rows:
            rank = getattr(values, "rank", None)
            if rank is not None and not rank.isdigit():
                raise ValueError(f"not a number: {rank!r}")
        return [vars(values) for values in rows]

    objects = load((FIXTURE / f"{name}.ccfg").read_text(), GRAMMAR, {"seed": raw(record), "match": raw(record)}, variables=variables)
    return objects["seed"] + objects["match"]


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
        ("ditto", [FINAL, ALCARAZ, DJOKOVIC]),
    ],
)
def test_parse(name, expected):
    assert _parse(name) == expected


@pytest.mark.parametrize(
    "variables,players",
    [
        (None, ["A${P:-x}", "B${P-x}", "${CHAMP:-Big Cat}"]),
        ({}, ["Ax", "Bx", "Big Cat"]),
        ({"P": ""}, ["Ax", "B", "Big Cat"]),
        ({"P": "lcaraz", "CHAMP": "Sinner"}, ["Alcaraz", "Blcaraz", "Sinner"]),
    ],
)
def test_interpolate(variables, players):
    assert [row["player"] for row in _parse("interpolate", variables)] == players


def test_interpolate_unset():
    message = "line 1: ${WHO} has no value — add 'WHO' to variables or write a default: ${WHO:-default}"
    with pytest.raises(ConfigError, match="^" + re.escape(message) + "$"):
        _parse("interpolate_unset", {})


@pytest.mark.parametrize(
    "name,message",
    [
        ("unknown_command", "line 2: unknown command 'rank' — no grammar line starts with it; grammar has ['match', 'seed', 'umpire']"),
        ("usage_mismatch", "line 1: seed ['Alcaraz'] does not match — Unexpected end-of-input. Expected one of: \n\t* VALUE"),
        ("ditto_no_previous", "line 1: '.' repeats the token in this position from the previous line, which has none — type the token out"),
        ("ditto_command", "line 2: unknown command '.' — no grammar line starts with it; grammar has ['match', 'seed', 'umpire']"),
        ("no_serializer", "line 1: no serializer for 'umpire' — add a 'umpire' entry to serializers or delete the line"),
        ("serializer_error", "seed: not a number: 'best'"),
    ],
)
def test_parse_errors(name, message):
    with pytest.raises(ConfigError, match="^" + re.escape(message) + "$"):
        _parse(name)


def test_docstring_example_is_a_working_script(tmp_path):
    marker = "from collections import namedtuple"
    _, found, script = command_cfg.__doc__.partition(marker)
    assert found
    example = tmp_path / "example.py"
    example.write_text(textwrap.dedent(marker + script))
    result = subprocess.run([sys.executable, str(example)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


_UNKNOWN = "line {number}: unknown command {token!r} — no grammar line starts with it; grammar has ['match', 'seed', 'umpire']"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("continuation", [FINAL, ALCARAZ]),
        ("continuation_ditto", [FINAL, ALCARAZ, DJOKOVIC]),
        ("continuation_comment", [ALCARAZ_NO_SET]),
    ],
    ids=["folds-onto-one-line", "ditto-reads-the-previous-logical-line", "comments-end-their-own-line-only"],
)
def test_an_indented_line_continues_the_command_above_it(name, expected):
    assert _parse(name) == expected


@pytest.mark.parametrize(
    "name,message",
    [
        ("continuation_orphan", _UNKNOWN.format(number=1, token="Alcaraz")),
        ("continuation_blank", _UNKNOWN.format(number=3, token="Djokovic")),
    ],
    ids=["nothing-to-continue", "a-blank-line-closes-the-command"],
)
def test_an_indented_line_with_nothing_open_is_its_own_command(name, message):
    with pytest.raises(ConfigError, match="^" + re.escape(message) + "$"):
        _parse(name)


def test_an_error_in_a_block_names_every_line_it_spans():
    text = "seed Alcaraz 1\nmatch append WIMBLEDON_FINAL\n      Alcaraz\n      notanumber\n      spare\n"
    with pytest.raises(ConfigError, match="^lines 2-5: "):
        load(text, GRAMMAR, {"seed": raw(lambda rows, objects: []), "match": raw(lambda rows, objects: [])})


def test_commenting_out_a_header_folds_its_body_into_the_command_above():
    with pytest.raises(ConfigError, match=r"^lines 1-3: seed \['Alcaraz', '1', 'Djokovic', '6'\] does not match"):
        _parse("continuation_commented_header")


def test_only_a_space_or_tab_indents():
    # A non-breaking space is not whitespace to shlex, so it must not be indentation here
    # either -- folding the line would leave the character as a token nothing mentions.
    with pytest.raises(ConfigError, match="^line 2: unknown command"):
        _parse("continuation_nbsp")
