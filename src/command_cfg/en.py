"""English templates for every error message; str.format fills them in."""

LINE_ERROR = "line {number}: {error}"
COMMAND_ERROR = "{command}: {error}"

UNWRAPPED = "serializers must be scalar/group/array/raw/each: {commands} are unwrapped — write scalar(Settings), not Settings"
COMMAND_HYPHEN = "commands must not contain '-': {commands} — a command name keys the result directly, so use '_' (e.g. 'ad_hoc'); '-' only normalizes in fields"
SCALAR_FIELDS = "scalar command {command!r} takes exactly 2 fields, grammar has {fields} — grammar must be '{command} <key> <value>'; more fields needs group or array"
GROUP_FIELDS = "group command {command!r} takes at least 2 fields, grammar has {fields} — add row fields after the group key: '{command} <name> <field>...'"
RESERVED_FIELDS = (
    "group command {command!r} uses reserved fields {reserved} — rename them in the grammar; define/append are grouping keywords"
)
ARRAY_FIELDS = "array command {command!r} takes at least 1 field, grammar has none — add '<field>'s to its grammar line"
NOT_IN_GRAMMAR = "commands not in grammar: {commands} — add a grammar line starting with each or remove them from serializers"
KEY_COLLISION = "{command} grammar: {tokens} normalize to the same key {key!r} — rename the option or the positional"

NO_SERIALIZER = "no serializer for {command!r} — add a {command!r} entry to serializers or delete the line"
DITTO_NOTHING_ABOVE = "'.' repeats the token in this position from the previous line, which has none — type the token out"
UNKNOWN_COMMAND = "unknown command {command!r} — no grammar line starts with it; grammar has {commands}"
NO_MATCH = "{line!r} does not match {sub_grammar!r}"

DUPLICATE_KEY = "duplicate {command} {key!r} — delete this line or change its key"
UNKNOWN_GROUP = "unknown {command} group {name!r} — add '{command} define {name} ...' on an earlier line"
