import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import command_cfg
from command_cfg import ConfigError, parse, raw

GRAMMAR = """
seed <player> <rank>
match define <id> <name>
match append <id> <player> <score> [--set=<v>]
umpire <name>
"""

FIXTURE = Path(__file__).parent / "fixture"


def _parse(name):
    def record(rows, objects):
        for values in rows:
            rank = getattr(values, "rank", None)
            if rank is not None and not rank.isdigit():
                raise ValueError(f"not a number: {rank!r}")
        return [vars(values) for values in rows]

    objects = parse((FIXTURE / f"{name}.ccfg").read_text(), GRAMMAR, {"seed": raw(record), "match": raw(record)})
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
        ("unknown_command", "line 2: unknown command 'rank' — no grammar line starts with it; grammar has ['match', 'seed', 'umpire']"),
        ("usage_mismatch", "line 1: 'seed Alcaraz' does not match 'Usage: seed <player> <rank>'"),
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
