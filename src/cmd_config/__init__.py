"""Line-oriented config files parsed against a docopt grammar, serialized into caller-owned objects.

A config file is a sequence of command lines with shell-style quoting, `#` comments,
and a `.` token that repeats the token in the same position on the line above. The
grammar is one docopt usage pattern per line, its first word the command name; each
config line is matched against its command's patterns and dispatched to a serializer.
A malformed line raises ConfigError carrying the offending line number.

Every object in the result comes from `serializers`. A command listed in `grouped`
maps to a row factory, called once per line with the line's fields as kwargs; its
rows are collected in dicts of lists keyed by the line's first field. A command in
`scalars` also maps to a factory, called once after parsing with the accumulated
key/value pairs as kwargs — one object per command, duplicate keys are an error.
Any other command maps to a function `(values, objects) -> None` that writes
wherever it wants in the `objects` dict parse returns.

from collections import namedtuple

from cmd_config import parse

CONFIG = '''
setting surface grass

round quarterfinal Alcaraz
round quarterfinal Djokovic

match Alcaraz 3
'''

GRAMMAR = '''
setting <key> <value>
round <name> <player>
match <winner> <sets>
'''

Settings = namedtuple("Settings", "surface")
Round = namedtuple("Round", "player")
Match = namedtuple("Match", "winner sets")


def match(values, objects):
    objects.setdefault("matches", []).append(Match(values.winner, int(values.sets)))


objects = parse(
    CONFIG,
    GRAMMAR,
    serializers={"setting": Settings, "round": Round, "match": match},
    scalars=("setting",),
    grouped=("round",),
)
assert objects == {
    "setting": Settings(surface="grass"),
    "round": {"quarterfinal": [Round(player="Alcaraz"), Round(player="Djokovic")]},
    "matches": [Match(winner="Alcaraz", sets=3)],
}
"""

import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from types import SimpleNamespace
from typing import Any

from docopt import DocoptExit, docopt


class ConfigError(Exception):
    pass


def parse(
    text: str,
    grammar: str,
    serializers: Mapping[str, Callable[..., Any]] | None = None,
    *,
    scalars: Sequence[str] = (),
    grouped: Sequence[str] = (),
    cast: Callable[[str, Any], Any] = lambda key, value: value,
) -> dict[str, Any]:
    given = dict(serializers or {})
    combined = {**given, **_serializers(grammar, scalars, grouped, cast, given)}
    usages = _to_usages(grammar)
    objects: dict[str, Any] = {}
    previous: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        try:
            if parsed := _parse_line(raw, usages, previous):
                if (serializer := combined.get(parsed.command)) is None:
                    raise ValueError(f"no serializer for {parsed.command!r}")
                serializer(parsed.values, objects)
                previous = parsed.tokens
        except ValueError as exc:
            raise ConfigError(f"line {number}: {exc}") from None
    for command in scalars:
        try:
            objects[command] = given[command](**objects.get(command, {}))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{command}: {exc}") from None
    return objects


def _serializers(
    grammar: str,
    scalars: Sequence[str],
    grouped: Sequence[str],
    cast: Callable[[str, Any], Any],
    factories: Mapping[str, Callable[..., Any]],
) -> dict[str, Callable[[SimpleNamespace, dict[str, Any]], None]]:
    fields = _grammar_fields(grammar, (*scalars, *grouped))
    if both := set(scalars) & set(grouped):
        raise ValueError(f"commands in both scalars and grouped: {sorted(both)}")
    for command in scalars:
        if len(fields[command]) != 2:
            raise ValueError(f"scalar command {command!r} must have exactly 2 fields, has {fields[command]}")
    for command in grouped:
        if len(fields[command]) < 2:
            raise ValueError(f"grouped command {command!r} must have at least 2 fields, has {fields[command]}")
    if missing := {*scalars, *grouped} - factories.keys():
        raise ValueError(f"commands need a factory in serializers: {sorted(missing)}")
    group_params: dict[tuple[str, str], dict[str, Any]] = {}

    def scalar(command: str, values: SimpleNamespace, objects: dict[str, Any]) -> None:
        key, value = fields[command]
        pairs = objects.setdefault(command, {})
        if (key_value := cast(key, getattr(values, key))) in pairs:
            raise ValueError(f"duplicate {command} {key_value!r}")
        pairs[key_value] = cast(value, getattr(values, value))

    def group(command: str, values: SimpleNamespace, objects: dict[str, Any]) -> None:
        vals = {key: cast(key, value) for key, value in vars(values).items()}
        define, append = vals.pop("define", False), vals.pop("append", False)
        name = vals.pop(fields[command][0])
        present = {key: value for key, value in vals.items() if value is not None}
        groups = objects.setdefault(command, {})
        if define:
            group_params[(command, name)] = present
            groups[name] = []
        elif append:
            if (rows := groups.get(name)) is None:
                raise ValueError(f"unknown {command} group {name!r}: define it first")
            rows.append(factories[command](**group_params[(command, name)], **present))
        else:
            groups.setdefault(name, []).append(factories[command](**present))

    return {**{c: partial(scalar, c) for c in scalars}, **{c: partial(group, c) for c in grouped}}


def _grammar_fields(grammar: str, commands: Sequence[str]) -> dict[str, list[str]]:
    usages = _to_usages(grammar)
    if unknown := set(commands) - usages.keys():
        raise ValueError(f"commands not in grammar: {sorted(unknown)}")
    return {command: [f.replace("-", "_") for f in dict.fromkeys(re.findall(r"<([\w-]+)>", usages[command]))] for command in commands}


def _to_usages(grammar: str) -> dict[str, str]:
    patterns: dict[str, list[str]] = {}
    for pattern in filter(None, (line.strip() for line in grammar.splitlines())):
        patterns.setdefault(pattern.split()[0], []).append(pattern)
    return {command: "Usage: " + "\n".join(lines) for command, lines in patterns.items()}


def _parse_line(raw: str, usages: Mapping[str, str], previous: Sequence[str] = ()) -> SimpleNamespace | None:
    tokens = shlex.split(raw, comments=True)
    if not tokens:
        return None
    if any(token == "." and i >= len(previous) for i, token in enumerate(tokens) if i):
        raise ValueError("'.' has nothing above it to repeat")
    tokens = [previous[i] if token == "." and i else token for i, token in enumerate(tokens)]
    if (usage := usages.get(tokens[0])) is None:
        raise ValueError(f"unknown command {tokens[0]!r}")
    try:
        # docopt requires continuation patterns indented under the Usage: header
        parsed = docopt("\n  ".join(usage.splitlines()), argv=tokens[1:])
    except DocoptExit:
        raise ValueError(f"{raw.strip()!r} does not match {usage.strip()!r}") from None
    values = {key.strip("<>-").replace("-", "_"): value for key, value in parsed.items()}
    return SimpleNamespace(command=tokens[0], tokens=tokens, values=SimpleNamespace(**values))
