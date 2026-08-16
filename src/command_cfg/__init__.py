"""Line-oriented config files parsed against a docopt grammar, serialized into caller-owned objects.

A config file is a sequence of command lines with shell-style quoting, `#` comments,
and a `.` token that repeats the token in the same position on the line above. The
grammar is one docopt usage pattern per line, its first word the command name; each
config line is matched against its command's patterns and dispatched to a serializer.
A malformed line raises ConfigError carrying the offending line number.

Every entry in `serializers` wraps a callable in the command's kind. `scalar(factory)`:
each line is a key/value pair, and the factory is called once after parsing with the
accumulated pairs as kwargs — one object per command, duplicate keys error. A command
with no lines yields `None` and the factory is not called (whereas `group`/`array`
yield an empty container); config tokens are always strings, so `None` unambiguously
means the command was absent.
`group(factory)`: a row factory called once per line, its rows collected in dicts
of lists keyed by the line's first field — the group key, also passed to the factory
when `include_key=True`. `array(factory)`: a row factory whose rows collect in a
flat list, in file order. `raw(serializer)`: the escape hatch, called once per
command with the list of its lines' parsed values and the objects built so far,
whatever it returns stored under the command name. `each(handler, default=factory)`:
called once per line in file order as `handler(objects, row)`, where `row` is that
line's fields already run through `cast`. With `default`, a fresh `default()` is stored
under the command name before the lines run, so the handler mutates `objects[command]`
without a `setdefault` dance; without it, `each` claims no key and the handler writes
into `objects` wherever it wants. Command names key the result, so they may not contain
`-` (use `_`); only field names normalize `-` to `_`.

Serializers run in their dict order, each seeing the objects earlier commands
produced: the `cast(key, value, objects)` callable coerces every field value
before it reaches a factory and can resolve names against those objects. Below,
`known` rejects any winner or champion who never entered a round — a typo errors
out instead of silently naming a new player.

from collections import namedtuple

from command_cfg import array, group, parse, raw, scalar

CONFIG = '''
setting surface grass

round quarterfinal Alcaraz
round quarterfinal Djokovic

match Alcaraz 3
champion Alcaraz
'''

GRAMMAR = '''
setting <key> <value>
round <name> <entrant>
match <winner> <sets>
champion <player>
'''

Settings = namedtuple("Settings", "surface")
Round = namedtuple("Round", "name entrant")
Match = namedtuple("Match", "winner sets")


def known(key, value, objects):
    if key in ("winner", "player") and not any(value == row.entrant for rows in objects["round"].values() for row in rows):
        raise ValueError(f"unknown player {value!r}")
    return value


def champion(rows, objects):
    [row] = rows
    return known("player", row.player, objects)


objects = parse(
    CONFIG,
    GRAMMAR,
    {
        "setting": scalar(Settings),
        "round": group(Round, include_key=True),
        "match": array(Match),
        "champion": raw(champion),
    },
    cast=known,
)
assert objects == {
    "setting": Settings(surface="grass"),
    "round": {"quarterfinal": [Round("quarterfinal", "Alcaraz"), Round("quarterfinal", "Djokovic")]},
    "match": [Match(winner="Alcaraz", sets="3")],
    "champion": "Alcaraz",
}
"""

import re
import shlex
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from docopt import DocoptExit, docopt

from command_cfg import en

Cast = Callable[[str, Any, Mapping[str, Any]], Any]
Serializer = Callable[[list[SimpleNamespace], Mapping[str, Any]], Any]
Lines = list[tuple[int, dict[str, Any]]]


class ConfigError(Exception):
    pass


@contextmanager
def _located(number: int) -> Iterator[None]:
    try:
        yield
    except (TypeError, ValueError) as exc:
        raise ConfigError(en.LINE_ERROR.format(number=number, error=exc)) from None


@dataclass(frozen=True)
class scalar:
    factory: Callable[..., Any]


@dataclass(frozen=True)
class group:
    factory: Callable[..., Any]
    include_key: bool = False


@dataclass(frozen=True)
class array:
    factory: Callable[..., Any]


@dataclass(frozen=True)
class raw:
    serializer: Serializer


@dataclass(frozen=True)
class each:
    handler: Callable[[dict[str, Any], SimpleNamespace], None]
    default: Callable[[], Any] | None = field(default=None, kw_only=True)


def parse(
    text: str,
    grammar: str,
    serializers: Mapping[str, scalar | group | array | raw | each],
    *,
    cast: Cast = lambda key, value, objects: value,
) -> dict[str, Any]:
    fields = _fields(grammar, serializers)
    sub_grammars = _sub_grammars(grammar)
    parsed_lines: dict[str, Lines] = {}
    previous: list[str] = []

    for number, line in enumerate(text.splitlines(), 1):
        with _located(number):
            if parsed := _parse(line, sub_grammars, previous):
                if parsed.command not in serializers:
                    raise ValueError(en.NO_SERIALIZER.format(command=parsed.command))
                parsed_lines.setdefault(parsed.command, []).append((number, parsed.values))
                previous = parsed.tokens

    objects: dict[str, Any] = {}
    for command, kind in serializers.items():
        lines = parsed_lines.get(command, [])
        match kind:
            case raw():
                objects[command] = _custom(kind, command, lines, objects)
            case each():
                _apply(kind, command, cast, lines, objects)
            case scalar():
                objects[command] = _merge(kind, fields[command], cast, command, lines, objects) if lines else None
            case group():
                objects[command] = _group(kind, fields[command][0], cast, command, lines, objects)
            case array():
                objects[command] = _collect(kind, cast, lines, objects)

    return objects


def _fields(grammar: str, serializers: Mapping[str, scalar | group | array | raw | each]) -> dict[str, list[str]]:
    if bare := sorted(command for command, kind in serializers.items() if not isinstance(kind, (scalar, group, array, raw, each))):
        raise ValueError(en.UNWRAPPED.format(commands=bare))
    kinds = {command: kind for command, kind in serializers.items() if not isinstance(kind, (raw, each))}
    fields = _grammar_fields(grammar, tuple(kinds))

    for command, kind in kinds.items():
        match kind:
            case scalar() if len(fields[command]) != 2:
                raise ValueError(en.SCALAR_FIELDS.format(command=command, fields=fields[command]))
            case group() if len(fields[command]) < 2:
                raise ValueError(en.GROUP_FIELDS.format(command=command, fields=fields[command]))
            case group() if reserved := {"define", "append"} & set(fields[command]):
                raise ValueError(en.RESERVED_FIELDS.format(command=command, reserved=sorted(reserved)))
            case array() if not fields[command]:
                raise ValueError(en.ARRAY_FIELDS.format(command=command))

    return fields


def _merge(kind: scalar, fields: Sequence[str], cast: Cast, command: str, lines: Lines, objects: Mapping[str, Any]) -> Any:
    key, value = fields
    pairs: dict[Any, Any] = {}
    for number, values in lines:
        with _located(number):
            if (key_value := cast(key, values[key], objects)) in pairs:
                raise ValueError(en.DUPLICATE_KEY.format(command=command, key=key_value))
            pairs[key_value] = cast(value, values[value], objects)
    try:
        return kind.factory(**pairs)
    except (TypeError, ValueError) as exc:
        raise ConfigError(en.COMMAND_ERROR.format(command=command, error=exc)) from None


def _group(kind: group, key_field: str, cast: Cast, command: str, lines: Lines, objects: Mapping[str, Any]) -> dict[Any, list[Any]]:
    groups: dict[Any, list[Any]] = {}
    params: dict[Any, dict[str, Any]] = {}
    for number, values in lines:
        with _located(number):
            vals = {key: cast(key, value, objects) for key, value in values.items()}
            define, append = vals.pop("define", False), vals.pop("append", False)
            name = vals.pop(key_field)
            present = {key: value for key, value in vals.items() if value is not None}
            row_kwargs = {key_field: name, **present} if kind.include_key else present
            if define:
                params[name] = present
                groups[name] = []
            elif append:
                if (defined := params.get(name)) is None:
                    raise ValueError(en.UNKNOWN_GROUP.format(command=command, name=name))
                groups[name].append(kind.factory(**defined, **row_kwargs))
            else:
                groups.setdefault(name, []).append(kind.factory(**row_kwargs))
    return groups


def _collect(kind: array, cast: Cast, lines: Lines, objects: Mapping[str, Any]) -> list[Any]:
    rows: list[Any] = []
    for number, values in lines:
        with _located(number):
            vals = {key: cast(key, value, objects) for key, value in values.items()}
            rows.append(kind.factory(**{key: value for key, value in vals.items() if value is not None}))
    return rows


def _custom(kind: raw, command: str, lines: Lines, objects: Mapping[str, Any]) -> Any:
    try:
        return kind.serializer([SimpleNamespace(**values) for _, values in lines], objects)
    except (TypeError, ValueError) as exc:
        raise ConfigError(en.COMMAND_ERROR.format(command=command, error=exc)) from None


def _apply(kind: each, command: str, cast: Cast, lines: Lines, objects: dict[str, Any]) -> None:
    if kind.default is not None:
        objects[command] = kind.default()  # keyed by the command name; a fresh instance per parse
    for number, values in lines:
        with _located(number):
            row = SimpleNamespace(**{key: cast(key, value, objects) for key, value in values.items()})
            kind.handler(objects, row)


def _grammar_fields(grammar: str, commands: Sequence[str]) -> dict[str, list[str]]:
    sub_grammars = _sub_grammars(grammar)
    if unknown := set(commands) - sub_grammars.keys():
        raise ValueError(en.NOT_IN_GRAMMAR.format(commands=sorted(unknown)))
    return {command: [f.replace("-", "_") for f in dict.fromkeys(re.findall(r"<([\w-]+)>", sub_grammars[command]))] for command in commands}


def _sub_grammars(grammar: str) -> dict[str, str]:
    patterns: dict[str, list[str]] = {}
    for pattern in filter(None, (line.strip() for line in grammar.splitlines())):
        patterns.setdefault(pattern.split()[0], []).append(pattern)
    if hyphenated := sorted(command for command in patterns if "-" in command):
        raise ValueError(en.COMMAND_HYPHEN.format(commands=hyphenated))
    sub_grammars = {command: "Usage: " + "\n".join(lines) for command, lines in patterns.items()}
    for command, sub_grammar in sub_grammars.items():
        spellings: dict[str, set[str]] = {}
        for token in re.findall(r"--[\w-]+(?:=<[\w-]+>)?|<[\w-]+>", sub_grammar):
            spelling = token.split("=")[0]  # an option's =<arg> placeholder names the option, not a positional
            spellings.setdefault(spelling.strip("<>-").replace("-", "_"), set()).add(spelling)
        for name, tokens in spellings.items():
            if len(tokens) > 1:
                raise ValueError(en.KEY_COLLISION.format(command=command, tokens=sorted(tokens), key=name))
    return sub_grammars


def _parse(line: str, sub_grammars: Mapping[str, str], previous: Sequence[str] = ()) -> SimpleNamespace | None:
    tokens = shlex.split(line, comments=True)

    if not tokens:
        return None
    if any(token == "." and i >= len(previous) for i, token in enumerate(tokens) if i):
        raise ValueError(en.DITTO_NOTHING_ABOVE)
    tokens = [previous[i] if token == "." and i else token for i, token in enumerate(tokens)]

    if (sub_grammar := sub_grammars.get(tokens[0])) is None:
        raise ValueError(en.UNKNOWN_COMMAND.format(command=tokens[0], commands=sorted(sub_grammars)))
    try:
        parsed = docopt("\n  ".join(sub_grammar.splitlines()), argv=tokens[1:])
    except DocoptExit:
        raise ValueError(en.NO_MATCH.format(line=line.strip(), sub_grammar=sub_grammar.strip())) from None
    values = {key.strip("<>-").replace("-", "_"): value for key, value in parsed.items()}
    return SimpleNamespace(command=tokens[0], tokens=tokens, values=values)
