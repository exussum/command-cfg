import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from types import SimpleNamespace
from typing import Any

from docopt import DocoptExit, docopt


class ConfigError(Exception):
    pass


def parse(text: str, grammar: str, serializers: Mapping[str, Callable[[SimpleNamespace, dict[str, Any]], None]]) -> dict[str, Any]:
    usages = _to_usages(grammar)
    objects: dict[str, Any] = {}
    previous: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        try:
            if parsed := _parse_line(raw, usages, previous):
                if (serializer := serializers.get(parsed.command)) is None:
                    raise ValueError(f"no serializer for {parsed.command!r}")
                serializer(parsed.values, objects)
                previous = parsed.tokens
        except ValueError as exc:
            raise ConfigError(f"line {number}: {exc}") from None
    return objects


def serializers(
    grammar: str,
    scalars: Sequence[str] = (),
    grouped: Sequence[str] = (),
    cast: Callable[[str, Any], Any] = lambda key, value: value,
) -> dict[str, Callable[[SimpleNamespace, dict[str, Any]], None]]:
    if both := set(scalars) & set(grouped):
        raise ValueError(f"commands in both scalars and grouped: {sorted(both)}")
    fields = _grammar_fields(grammar, (*scalars, *grouped))
    for command in scalars:
        if len(fields[command]) != 2:
            raise ValueError(f"scalar command {command!r} must have exactly 2 fields, has {fields[command]}")
    for command in grouped:
        if len(fields[command]) < 2:
            raise ValueError(f"grouped command {command!r} must have at least 2 fields, has {fields[command]}")
    group_params: dict[tuple[str, str], dict[str, Any]] = {}

    def scalar(command: str, values: SimpleNamespace, objects: dict[str, Any]) -> None:
        key, value = fields[command]
        objects[cast(key, getattr(values, key))] = cast(value, getattr(values, value))

    def group(command: str, values: SimpleNamespace, objects: dict[str, Any]) -> None:
        vals = {key: cast(key, value) for key, value in vars(values).items()}
        define, append = vals.pop("define", False), vals.pop("append", False)
        name = vals.pop(fields[command][0])
        present = {key: value for key, value in vals.items() if value is not None}
        groups = objects.setdefault(command, SimpleNamespace())
        if define:
            group_params[(command, name)] = present
            setattr(groups, name, [])
        elif append:
            if (rows := getattr(groups, name, None)) is None:
                raise ValueError(f"unknown {command} group {name!r}: define it first")
            rows.append(SimpleNamespace(**group_params[(command, name)], **present))
        else:
            if (rows := getattr(groups, name, None)) is None:
                rows = []
                setattr(groups, name, rows)
            rows.append(SimpleNamespace(**present))

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
