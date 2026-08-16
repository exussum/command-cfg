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
is created by this library. Each entry wraps a callable in the command's
kind:

- `scalar(factory)` — each line is a key/value pair, and the factory is
  called once after parsing with the accumulated pairs as kwargs — one
  object per command, duplicate keys are an error.
- `group(factory)` — a row factory, called once per line with the line's
  fields as kwargs; its rows are collected in dicts of lists keyed by the
  line's first field — the group key, also passed to the factory when
  `include_key=True`. `define`/`append` grammar pairs hoist shared values:
  `define` names a group and carries its parameters, `append` adds a row,
  and every row carries the group's parameters merged in.
- `array(factory)` — a row factory whose rows collect in a flat list, in
  file order.
- `raw(serializer)` — the escape hatch: called once per command with the
  list of its lines' parsed values; whatever it returns is stored under the
  command name.

## Example

Every kind and file feature in one config — scalar pairs, a quoted token, a
`#` comment, the `.` ditto token, a `define`/`append` group, array rows, and
a raw command:

```python
from collections import namedtuple

from command_cfg import array, group, parse, raw, scalar

CONFIG = """
setting surface grass
setting best_of 5          # scalar pairs, comments allowed

round quarterfinal 'Carlos Alcaraz'
round .            Djokovic

game define Final 14:00
game append Final Alcaraz  3
game append .     Djokovic 1

match Alcaraz 3
match Sinner  3

champion Alcaraz
"""

GRAMMAR = """
setting <key> <value>
round <name> <player>
game define <id> <start>
game append <id> <player> <sets>
match <winner> <sets>
champion <player>
"""

Settings = namedtuple("Settings", "surface best_of")
Round = namedtuple("Round", "name player")
Game = namedtuple("Game", "start player sets")
Match = namedtuple("Match", "winner sets")


def champion(rows):
    [row] = rows
    return row.player


objects = parse(
    CONFIG,
    GRAMMAR,
    {
        "setting": scalar(Settings),
        "round": group(Round, include_key=True),
        "game": group(Game),
        "match": array(Match),
        "champion": raw(champion),
    },
)
assert objects == {
    "setting": Settings(surface="grass", best_of="5"),
    "round": {"quarterfinal": [Round("quarterfinal", "Carlos Alcaraz"), Round("quarterfinal", "Djokovic")]},
    "game": {"Final": [Game("14:00", "Alcaraz", "3"), Game("14:00", "Djokovic", "1")]},
    "match": [Match(winner="Alcaraz", sets="3"), Match(winner="Sinner", sets="3")],
    "champion": "Alcaraz",
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
