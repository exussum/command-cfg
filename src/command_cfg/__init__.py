"""Line-oriented config files parsed against a docopt-shaped grammar, serialized into caller-owned objects.

A config file is a sequence of command lines with shell-style quoting, `#` comments,
and a `.` token repeating the token in the same position on the line above. The
grammar is one docopt-style usage pattern per line, first word the command name;
each pattern is compiled into a Lark grammar (see `command_cfg.grammar`), and each
config line matches its command's patterns and dispatches to a serializer. A
malformed line raises ConfigError with the offending line number.

Every `serializers` entry wraps a callable in the command's kind. `scalar(factory)`:
each line is a key/value pair; the factory is called once after parsing with the
accumulated pairs as kwargs — duplicate keys error. A command with no lines yields
`None` without calling the factory (`group`/`array` instead yield an empty
container); a present field is never `None`, even one coerced to `int`/`float`, so
`None` unambiguously means the command was absent. `group(factory)`: a row factory
called once per line, rows collected in dicts of lists keyed by the line's first
field — the group key, also passed to the factory when `include_key=True`.
`array(factory)`: a row factory whose rows collect in one flat list, in file order.
`raw(serializer)`: the escape hatch, called once per command with the list of its
lines' parsed values plus the objects built so far, whatever it returns stored
under the command name. `each(handler, default=factory)`: called once per line as
`handler(objects, row)`, `row`'s fields already coerced per `types`. `default`, if
given, seeds `objects[command]` with a fresh `default()` before the lines run so
the handler can mutate it without a `setdefault`; without it, `each` claims no key
and the handler writes into `objects` wherever it wants. Command names key the
result, so they may not contain `-` (use `_`); only field names normalize `-` to
`_`.

Serializers run in dict order, each seeing the objects earlier commands produced:
`raw` and `each` get `objects` directly, so they can resolve or validate a value
against commands parsed earlier. Below, `known` rejects any champion who never
entered a round — a typo errors out instead of silently naming a new player.

Every kind takes its own `types` mapping as a per-field override — keyed straight
to a callable, not a name to look up — defaulting to `{"str": str, "int": int,
"float": float}` for every field that isn't overridden. For `group`/`array`/`each`/
`raw` it's keyed by field name, and each caster runs as plain `caster(value)`.
`scalar`'s `<key> <value>` line has one `<value>` placeholder shared by every row,
so its `types` is keyed by each row's `key` instead — `scalar(Settings,
types={"best_of": int})` types `best_of` as `int`, every other setting stays `str`
— and each caster runs as `caster(value, objects)`, so a scalar's own casters can
resolve or validate against commands parsed earlier too, the same access
`raw`/`each` get. The mapping lives only on that command's own
`scalar(...)`/`group(...)`/etc. instance — it's not global, so it can't affect any
other command.

Tokens may interpolate variables when a `variables` mapping is passed:
`${NAME}` becomes `variables["NAME"]` (an unknown name errors with its line
number), and `${NAME-default}` / `${NAME:-default}` fall back bash-style — when
`NAME` is missing, or missing-or-empty, respectively — so a config can carry its
own literal as the default: `setting jobs_db ${JOBS_DB:-sqlite:///var/jobs.sqlite}`.
Expansion runs per token after quoting, so `'${MOTTO:-hello world}'` stays one
token. Without `variables` (the default), `${...}` is ordinary text.

`Parser(grammar, serializers)` validates the grammar and every kind's `types`
once; `.load(text)` reuses that setup across repeated parses — useful when many
texts share one grammar. `load(text, grammar, serializers, variables=None)` is a
one-line convenience for a single parse, equivalent to `Parser(grammar,
serializers, variables).load(text)`.

from collections import namedtuple

from command_cfg import Parser, array, group, raw, scalar

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


def known(player, objects):
    if not any(player == row.entrant for rows in objects["round"].values() for row in rows):
        raise ValueError(f"unknown player {player!r}")
    return player


def champion(rows, objects):
    [row] = rows
    return known(row.player, objects)


objects = Parser(
    GRAMMAR,
    {
        "setting": scalar(Settings),
        "round": group(Round, include_key=True),
        "match": array(Match, types={"sets": int}),
        "champion": raw(champion),
    },
).load(CONFIG)
assert objects == {
    "setting": Settings(surface="grass"),
    "round": {"quarterfinal": [Round("quarterfinal", "Alcaraz"), Round("quarterfinal", "Djokovic")]},
    "match": [Match(winner="Alcaraz", sets=3)],
    "champion": "Alcaraz",
}
"""

from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from command_cfg import en
from command_cfg.grammar import CommandGrammar
from command_cfg.models import array, each, group, raw, scalar
from command_cfg.parser import coerce, grammar_fields, grammars_by_command, parse_line

Lines = list[tuple[int, dict[str, Any]]]


class ConfigError(Exception):
    pass


@contextmanager
def exc_handler(number: int) -> Iterator[None]:
    try:
        yield
    except (TypeError, ValueError) as exc:
        raise ConfigError(en.LINE_ERROR.format(number=number, error=exc)) from None


class Parser:
    def __init__(
        self,
        grammar: str,
        serializers: Mapping[str, scalar | group | array | raw | each],
        variables: Mapping[str, str] | None = None,
    ) -> None:
        self.serializers = serializers
        self.grammars: dict[str, CommandGrammar] = grammars_by_command(grammar)
        self.fields = _fields(self.grammars, serializers)
        self.variables = variables

    def load(self, text: str) -> dict[str, Any]:
        parsed_lines: defaultdict[str, Lines] = defaultdict(list)

        previous: list[str] = []
        for number, line in enumerate(text.splitlines(), 1):
            with exc_handler(number):
                if parsed := parse_line(line, self.grammars, previous, self.variables):
                    if parsed.command not in self.serializers:
                        raise ValueError(en.NO_SERIALIZER.format(command=parsed.command))
                    parsed_lines[parsed.command].append((number, parsed.values))
                    previous = parsed.tokens

        objects: dict[str, Any] = {}
        for command, kind in self.serializers.items():
            lines = parsed_lines.get(command, [])
            match kind:
                case raw():
                    objects[command] = _process_raw(kind, command, lines, objects)
                case each():
                    _process_each(kind, command, lines, objects)
                case scalar():
                    objects[command] = _process_scalar(kind, self.fields[command], command, lines, objects) if lines else None
                case group():
                    objects[command] = _process_group(kind, self.fields[command][0], lines)
                case array():
                    objects[command] = _process_array(kind, lines)

        return objects


def load(
    text: str,
    grammar: str,
    serializers: Mapping[str, scalar | group | array | raw | each],
    variables: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return Parser(grammar, serializers, variables).load(text)


def _fields(grammars: Mapping[str, CommandGrammar], serializers: Mapping[str, scalar | group | array | raw | each]) -> dict[str, list[str]]:
    if bare := sorted(command for command, kind in serializers.items() if not isinstance(kind, (scalar, group, array, raw, each))):
        raise ValueError(en.UNWRAPPED.format(commands=bare))

    kinds = {command: kind for command, kind in serializers.items() if not isinstance(kind, (raw, each))}
    fields = grammar_fields(grammars, tuple(kinds))

    for command, kind in kinds.items():
        match kind:
            case scalar() if len(fields[command]) != 2:
                raise ValueError(en.SCALAR_FIELDS.format(command=command, fields=fields[command]))
            case group() if len(fields[command]) < 2:
                raise ValueError(en.GROUP_FIELDS.format(command=command, fields=fields[command]))
            case array() if not fields[command]:
                raise ValueError(en.ARRAY_FIELDS.format(command=command))

    return fields


def _default_cast(value: str, objects: Mapping[str, Any]) -> str:
    return value


def _process_scalar(kind: scalar, fields: Sequence[str], command: str, lines: Lines, objects: Mapping[str, Any]) -> Any:
    key, value = fields
    pairs: dict[Any, Any] = {}
    for number, values in lines:
        with exc_handler(number):
            if (key_value := values[key]) in pairs:
                raise ValueError(en.DUPLICATE_KEY.format(command=command, key=key_value))
            pairs[key_value] = kind.types.get(key_value, _default_cast)(values[value], objects)
    try:
        return kind.factory(**pairs)
    except (TypeError, ValueError) as exc:
        raise ConfigError(en.COMMAND_ERROR.format(command=command, error=exc)) from None


def _process_group(kind: group, key_field: str, lines: Lines) -> dict[Any, list[Any]]:
    groups: defaultdict[Any, list[Any]] = defaultdict(list)
    for number, values in lines:
        with exc_handler(number):
            vals = dict(coerce(kind.types, values))
            name = vals.pop(key_field)
            present = {key: value for key, value in vals.items() if value is not None}
            row_kwargs = {key_field: name, **present} if kind.include_key else present
            groups[name].append(kind.factory(**row_kwargs))
    return dict(groups)


def _process_array(kind: array, lines: Lines) -> list[Any]:
    rows: list[Any] = []
    for number, values in lines:
        with exc_handler(number):
            vals = coerce(kind.types, values)
            rows.append(kind.factory(**{key: value for key, value in vals.items() if value is not None}))
    return rows


def _process_raw(kind: raw, command: str, lines: Lines, objects: Mapping[str, Any]) -> Any:
    try:
        return kind.serializer([SimpleNamespace(**coerce(kind.types, values)) for _, values in lines], objects)
    except (TypeError, ValueError) as exc:
        raise ConfigError(en.COMMAND_ERROR.format(command=command, error=exc)) from None


def _process_each(kind: each, command: str, lines: Lines, objects: dict[str, Any]) -> None:
    if kind.default is not None:
        objects[command] = kind.default()  # keyed by the command name; a fresh instance per parse
    for number, values in lines:
        with exc_handler(number):
            row = SimpleNamespace(**coerce(kind.types, values))
            kind.handler(objects, row)
