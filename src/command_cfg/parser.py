"""Groups grammar text into per-command Lark grammars, and matches a config line's tokens against them."""

import re
import shlex
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from types import SimpleNamespace
from typing import Any

from command_cfg import en
from command_cfg.grammar import CommandGrammar, compile_command
from command_cfg.grammar import match as match_grammar


def grammar_fields(grammars: Mapping[str, CommandGrammar], commands: Sequence[str]) -> dict[str, list[str]]:
    if unknown_commands := set(commands) - grammars.keys():
        raise ValueError(en.NOT_IN_GRAMMAR.format(commands=sorted(unknown_commands)))
    return {command: list(grammars[command].fields) for command in commands}


def grammars_by_command(grammar: str) -> dict[str, CommandGrammar]:
    patterns: defaultdict[str, list[str]] = defaultdict(list)
    for pattern in filter(None, (line.strip() for line in grammar.splitlines())):
        patterns[pattern.split()[0]].append(pattern)
    return {command: compile_command(command, lines) for command, lines in patterns.items()}


def coerce(field_types: Mapping[str, Callable[..., Any]], values: Mapping[str, Any]) -> dict[str, Any]:
    def cast(key: str, value: Any) -> Any:
        match value:
            case str():
                return field_types.get(key, str)(value)
            case list():
                return [cast(key, v) for v in value]
            case _:
                return value

    return {key: cast(key, value) for key, value in values.items()}


_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?-)([^}]*))?\}")


def expand_variables(token: str, variables: Mapping[str, str]) -> str:
    def sub(match: re.Match[str]) -> str:
        name, operator, default = match.groups()
        value = variables.get(name)
        if operator is None:
            if value is None:
                raise ValueError(en.UNSET_VARIABLE.format(name=name))
            return value
        if value is None or (operator == ":-" and value == ""):
            return default
        return value

    return _VARIABLE.sub(sub, token)


def parse_line(
    line: str, grammars: Mapping[str, CommandGrammar], previous: Sequence[str] = (), variables: Mapping[str, str] | None = None
) -> SimpleNamespace | None:
    tokens = shlex.split(line, comments=True)
    if variables is not None:
        tokens = [expand_variables(token, variables) for token in tokens]

    if not tokens:
        return None
    if any(token == "." and i >= len(previous) for i, token in enumerate(tokens) if i):
        raise ValueError(en.DITTO_NOTHING_ABOVE)
    tokens = [previous[i] if token == "." and i else token for i, token in enumerate(tokens)]

    if (grammar := grammars.get(tokens[0])) is None:
        raise ValueError(en.UNKNOWN_COMMAND.format(command=tokens[0], commands=sorted(grammars)))
    values = match_grammar(grammar, tokens[0], tokens[1:])
    return SimpleNamespace(command=tokens[0], tokens=tokens, values=values)
