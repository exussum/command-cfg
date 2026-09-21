# command-cfg

Line-oriented config files parsed against a docopt-style grammar, serialized
into caller-owned objects.

A config file is a sequence of command lines with shell-style quoting, `#`
comments, and a `.` token that repeats the token in the same position on the
line above. The grammar is one docopt-style usage pattern per line, its first word
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
  `include_key=True`.
- `array(factory)` — a row factory whose rows collect in a flat list, in
  file order.
- `raw(serializer)` — the escape hatch: called once per command with the
  list of its lines' parsed values and the objects built so far; whatever it
  returns is stored under the command name.
- `each(handler, default=factory)` — called once per line as
  `handler(objects, row)`. `default`, if given, seeds `objects[command]`
  with a fresh `default()` before the lines run so the handler can mutate
  it without a `setdefault`; without it, `each` claims no key and the
  handler writes into `objects` wherever it wants.

## Example

Every kind and file feature in one config — scalar pairs, a quoted token, a
`#` comment, the `.` ditto token, a group, a self-rolled `define`/`append`
group built on `each`, array rows, and a raw command that resolves the
champion's name against the rounds so a typo errors out instead of silently
naming a new player:

```python
from collections import namedtuple

from command_cfg import array, each, group, load, raw, scalar

CONFIG = """
setting surface grass
setting best_of 5          # scalar pairs, comments allowed

round quarterfinal Alcaraz
round .            Djokovic

game define Final '14:00 BST'
game append Final Alcaraz  3
game append .     Djokovic 1

match Alcaraz 3

champion Alcaraz
"""

GRAMMAR = """
setting <key> <value>
round <name> <entrant>
game define <id> <start>
game append <id> <player> <sets>
match <winner> <sets>
champion <player>
"""

Settings = namedtuple("Settings", "surface best_of")
Round = namedtuple("Round", "name entrant")
Game = namedtuple("Game", "start player sets")
Match = namedtuple("Match", "winner sets")



def champion(rows, objects):
    # There's nothing to report _but_ the winner.  No frills result.
    [row] = rows
    if not any(row.player == entrant.entrant for rounds in objects["round"].values() for entrant in rounds):
        raise ValueError(f"unknown player {row.player!r}")
    return row.player


game_starts: dict[str, str] = {}


def game(objects, row):
    groups = objects["game"]
    if row.define:
        # start here is the <start> variable, we can keep track that a game has
        # started and everything rolls up into it.  So all finals can be returned
        # all at once, keyed by their types
        game_starts[row.id] = row.start
        groups[row.id] = []
    elif row.append:
        groups[row.id].append(Game(game_starts[row.id], row.player, row.sets))


objects = load(
    CONFIG,
    GRAMMAR,
    {
        "setting": scalar(Settings), # Settings and Match are easily constructed by passing in the data
        "match": array(Match),
        "round": group(Round, include_key=True), # Grouping will prevent repeats off of the first entry.  including that key returns it so Round can get <name> back
        "game": each(game, default=dict),
        "champion": raw(champion),
    },
)
assert objects == {
    "setting": Settings(surface="grass", best_of="5"),
    "round": {"quarterfinal": [Round("quarterfinal", "Alcaraz"), Round("quarterfinal", "Djokovic")]},
    "game": {"Final": [Game("14:00 BST", "Alcaraz", "3"), Game("14:00 BST", "Djokovic", "1")]},
    "match": [Match(winner="Alcaraz", sets="3")],
    "champion": "Alcaraz",
}
```

Serializers run in their dict order, each seeing the objects earlier commands
produced. Each kind's own `types` mapping coerces values by field name before
they reach its factory (e.g. turning `<start>` into a `datetime.time`); `raw`
and `each` also get `objects` directly, so their own code can do lookups
against earlier commands' objects, as `known` does above.

## Development

```sh
uv run pytest
uv run ruff format
uv run ruff check
uv run mypy
```

`pre-commit run --all-files` runs all of the above (plus `uv lock`/pylock/pip-audit regeneration) in one pass — the same checks CI runs.

## Publishing

`uv publish` reads the registry and credentials from the environment — copy
`scripts/deploy.env.example` to `scripts/deploy.env` (gitignored) and set your
registry, then:

```sh
. scripts/deploy.env
uv build
uv publish
```
