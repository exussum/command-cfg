import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from types import SimpleNamespace
from typing import Any

from docopt import DocoptExit, docopt


class ConfigError(ValueError):
    pass


def parse(text: str, grammar: str, serializers: Mapping[str, Callable[[SimpleNamespace], None]]) -> None:
    usages = _to_usages(grammar)
    previous: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        try:
            if parsed := _parse_line(raw, usages, previous):
                if (serializer := serializers.get(parsed.command)) is None:
                    raise ValueError(f"no serializer for {parsed.command!r}")
                serializer(parsed.values)
                previous = parsed.tokens
        except ValueError as exc:
            raise ConfigError(f"line {number}: {exc}") from None


def namespace(
    grammar: str, cast: Callable[[str, Any], Any] = lambda key, value: value
) -> tuple[dict[str, Callable[[SimpleNamespace], None]], Callable[[], SimpleNamespace]]:
    fields = {
        command: [f.replace("-", "_") for f in dict.fromkeys(re.findall(r"<([\w-]+)>", usage))]
        for command, usage in _to_usages(grammar).items()
    }
    attrs: dict[str, Any] = {}
    group_params: dict[tuple[str, str], dict[str, Any]] = {}

    def entry(command: str, values: SimpleNamespace) -> None:
        vals = {key: cast(key, value) for key, value in vars(values).items()}
        define, append = vals.pop("define", False), vals.pop("append", False)
        first = fields[command][0]
        if len(vals) == 2:
            attrs[vals[first]] = vals[fields[command][1]]
            return

        group = vals.pop(first)
        present = {key: value for key, value in vals.items() if value is not None}
        groups = attrs.setdefault(command, SimpleNamespace())
        if define:
            group_params[(command, group)] = present
            setattr(groups, group, [])
        elif append:
            if (rows := getattr(groups, group, None)) is None:
                raise ValueError(f"unknown {command} group {group!r}: define it first")
            rows.append(SimpleNamespace(**group_params[(command, group)], **present))
        else:
            if (rows := getattr(groups, group, None)) is None:
                rows = []
                setattr(groups, group, rows)
            rows.append(SimpleNamespace(**present))

    return {command: partial(entry, command) for command in fields}, lambda: SimpleNamespace(**attrs)


def _to_usages(grammar: str) -> dict[str, str]:
    patterns: dict[str, list[str]] = {}
    for pattern in filter(None, (line.strip() for line in grammar.splitlines())):
        patterns.setdefault(pattern.split()[0], []).append(pattern)
    return {command: "\n".join(lines) for command, lines in patterns.items()}


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
        # docopt requires a Usage: header and continuation patterns indented under it
        parsed = docopt("Usage: " + "\n  ".join(usage.splitlines()), argv=tokens[1:])
    except DocoptExit:
        raise ValueError(f"{raw.strip()!r} does not match {usage.strip()!r}") from None
    values = {key.strip("<>-").replace("-", "_"): value for key, value in parsed.items()}
    return SimpleNamespace(command=tokens[0], tokens=tokens, values=SimpleNamespace(**values))
