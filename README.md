# command-cfg

Line-oriented config files parsed against a docopt grammar, serialized into
caller-owned objects.

A config file is a sequence of command lines with shell-style quoting, `#`
comments, and a `.` token that repeats the token in the same position on the
line above. The grammar is one docopt usage pattern per line, its first word
the command name; each config line is matched against its command's patterns
and dispatched to a serializer. A malformed line raises `ConfigError`
carrying the offending line number.

Every object in the result comes from `serializers` — nothing in the result
is created by this library:

- A command listed in `grouped` maps to a row factory, called once per line
  with the line's fields as kwargs; its rows are collected in dicts of lists
  keyed by the line's first field. `define`/`append` grammar pairs hoist
  shared values: `define` names a group and carries its parameters, `append`
  adds a row, and every row carries the group's parameters merged in.
- A command listed in `scalars` also maps to a factory, called once after
  parsing with the accumulated key/value pairs as kwargs — one object per
  command, duplicate keys are an error.
- Any other command maps to a function `(values, objects) -> None` that
  writes wherever it wants in the `objects` dict `parse` returns.

## Example

```python
from collections import namedtuple

from command_cfg import parse

CONFIG = """
setting surface grass

round quarterfinal Alcaraz
round quarterfinal Djokovic

match Alcaraz 3
"""

GRAMMAR = """
setting <key> <value>
round <name> <player>
match <winner> <sets>
"""

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
```

A `cast` callable can be passed to `parse` to coerce values by field name
before they reach any serializer (e.g. turning `<start>` into a
`datetime.time`).

## Development

```sh
uv run pytest
uv run black src tests
uv run mypy
```

## Publishing

`uv publish` reads the registry and credentials from the environment — copy
`scripts/deploy.env.example` to `scripts/deploy.env` (gitignored) and set your
registry, then:

```sh
. scripts/deploy.env
uv build
uv publish
```
