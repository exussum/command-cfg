"""Turns grammar text into docopt usage strings plus field types, and a config line into its parsed values."""

import re
import shlex
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType, SimpleNamespace
from typing import Any

from docopt import DocoptExit, docopt

from command_cfg import en

_TOKEN = re.compile(r"--[\w-]+(?:=<[\w-]+(?::[\w-]+)?>)?|<[\w-]+(?::[\w-]+)?>")
_PLACEHOLDER = re.compile(r"<([\w-]+)(?::([\w-]+))?>")


def grammar_fields(docopt_grammars: Mapping[str, str], commands: Sequence[str]) -> dict[str, list[str]]:
    if unknown := set(commands) - docopt_grammars.keys():
        raise ValueError(en.NOT_IN_GRAMMAR.format(commands=sorted(unknown)))
    return {
        command: [f.replace("-", "_") for f in dict.fromkeys(re.findall(r"<([\w-]+)(?::[\w-]+)?>", docopt_grammars[command]))]
        for command in commands
    }


def docopt_grammars(
    grammar: str, types: Mapping[str, Callable[[str], Any]] = MappingProxyType({})
) -> tuple[dict[str, str], dict[str, dict[str, Callable[[str], Any]]]]:
    patterns: defaultdict[str, list[str]] = defaultdict(list)
    for pattern in filter(None, (line.strip() for line in grammar.splitlines())):
        patterns[pattern.split()[0]].append(pattern)
    if hyphenated := sorted(command for command in patterns if "-" in command):
        raise ValueError(en.COMMAND_HYPHEN.format(commands=hyphenated))
    docopt_grammars = {command: "Usage: " + "\n".join(lines) for command, lines in patterns.items()}
    field_types = {command: _field_types(command, docopt_grammar, types) for command, docopt_grammar in docopt_grammars.items()}
    return docopt_grammars, field_types


def _field_types(command: str, docopt_grammar: str, types: Mapping[str, Callable[[str], Any]]) -> dict[str, Callable[[str], Any]]:
    spellings: defaultdict[str, set[str]] = defaultdict(set)
    field_types: dict[str, Callable[[str], Any]] = {}
    for token in _TOKEN.findall(docopt_grammar):
        name, arg, type_name = _resolve_token(token)
        spellings[name].add(arg)
        if type_name is not None:
            if type_name not in types:
                raise ValueError(en.UNKNOWN_TYPE.format(command=command, name=name, type=type_name, types=sorted(types)))
            field_types[name] = types[type_name]
    for name, tokens in spellings.items():
        if len(tokens) > 1:
            raise ValueError(en.KEY_COLLISION.format(command=command, tokens=sorted(tokens), key=name))
    return field_types


def coerce(field_types: Mapping[str, Callable[..., Any]], values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: field_types.get(key, str)(value) if isinstance(value, str) else value for key, value in values.items()}


def _resolve_token(token: str) -> tuple[str, str, str | None]:
    if "=" in token:
        flag, _, placeholder = token.partition("=")  # an option's =<arg> placeholder names the option, not a positional
        name = _key_name(flag)
        match = _PLACEHOLDER.fullmatch(placeholder)
        assert match is not None
        return name, flag, match.group(2)
    else:
        name = _key_name(token)
        if not token.startswith("<"):
            return name, token, None
        match = _PLACEHOLDER.fullmatch(token)
        assert match is not None
        return name, token, match.group(2)


def parse_line(line: str, docopt_grammars: Mapping[str, str], previous: Sequence[str] = ()) -> SimpleNamespace | None:
    tokens = shlex.split(line, comments=True)

    if not tokens:
        return None
    if any(token == "." and i >= len(previous) for i, token in enumerate(tokens) if i):
        raise ValueError(en.DITTO_NOTHING_ABOVE)
    tokens = [previous[i] if token == "." and i else token for i, token in enumerate(tokens)]

    if (docopt_grammar := docopt_grammars.get(tokens[0])) is None:
        raise ValueError(en.UNKNOWN_COMMAND.format(command=tokens[0], commands=sorted(docopt_grammars)))
    try:
        parsed = docopt("\n  ".join(docopt_grammar.splitlines()), argv=tokens[1:])
    except DocoptExit:
        raise ValueError(en.NO_MATCH.format(line=line.strip(), docopt_grammar=docopt_grammar.strip())) from None
    values = {_key_name(key): value for key, value in parsed.items()}
    return SimpleNamespace(command=tokens[0], tokens=tokens, values=values)


def _key_name(key: str) -> str:
    if key.startswith("<"):
        match = _PLACEHOLDER.fullmatch(key)
        assert match is not None
        return match.group(1).replace("-", "_")
    return key.strip("<>-").replace("-", "_")
