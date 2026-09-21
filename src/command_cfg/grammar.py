"""Compiles a docopt-style usage pattern (literals, <placeholders>, <placeholders>...,
[optional] groups, --flag/--flag=<value> options) into a Lark grammar, and matches a
config line's tokens against it — replacing docopt-ng with a real context-free
parser so [optional] groups match as a connected whole instead of docopt's
per-child independent matching (which let a bracketed argument absorb a stray
token even when its sibling literal anchor wasn't present). A placeholder marked
`...` collects one-or-more occurrences into a list instead of a scalar value; an
absent `[<name>...]` defaults to an empty list, not None. The same applies to an
option marked `...` (`[--flag=<value>]...` or bare `--flag=<value>...`): repeated
occurrences collect into a list, matching docopt-ng's behavior.

Two separate Lark grammars are involved. `_PATTERN` parses the *fixed* pattern text
itself (one specific token order, as the grammar author wrote it) into literals,
placeholders, options, and optional groups — that's a genuine context-free grammar,
so it's Lark all the way down. Matching *actual config lines* against the compiled
result is not: `--flag`s can appear in any order, interleaved anywhere among the
required tokens, which isn't expressible as a CFG without either enumerating every
ordering or inserting an optional gap between every pair of elements (which Earley
then reports as spurious ambiguity on perfectly unambiguous input). docopt itself
draws the same line internally — its own argv parser handles options in a separate
pass from its Required/Optional pattern tree. So options are extracted from a
config line's tokens by `match()` directly, before the remaining tokens ever reach
Lark.
"""

from dataclasses import dataclass
from functools import cache

from lark import Lark, Token, Transformer, Tree
from lark.exceptions import UnexpectedInput

from command_cfg import en

_VALUE_TERMINAL = "VALUE"
_TOKEN_SEP = "\n"
_EMPTY_TOKEN = "\0"  # stands in for a genuinely empty ('') token
_LITERAL_PREFIX = "lit__"
_PLACEHOLDER_PREFIX = "ph__"

_PATTERN = Lark(
    r"""
    line: LITERAL item*
    item: LITERAL -> literal
        | PLACEHOLDER -> placeholder
        | PLACEHOLDER "..." -> repeated_placeholder
        | OPTION -> option
        | OPTION "..." -> repeated_option
        | "[" item* "]" -> optional
        | "[" item* "]" "..." -> repeated_optional

    LITERAL: /[A-Za-z][\w-]*/
    PLACEHOLDER: /<[\w-]+>/
    OPTION: /--[\w-]+(=<[\w-]+>)?/

    %import common.WS
    %ignore WS
    """,
    start="line",
    parser="lalr",
)


class GrammarError(ValueError):
    pass


@dataclass
class Option:
    raw_name: str
    normalized_name: str
    takes_value: bool
    repeated: bool = False


@dataclass(frozen=True)
class CommandGrammar:
    parser: Lark
    options_by_raw_name: dict[str, Option]
    fields: tuple[str, ...]  # placeholders + normalized option names, for scalar/group/array field-count checks
    defaults: dict[str, object]
    repeated_fields: frozenset[str]  # normalized placeholder/option names marked ...


def _option(atom: str) -> tuple[str, str | None] | None:
    if not atom.startswith("--"):
        return None
    name, has_value, value = atom[2:].partition("=")
    return name, (value if has_value else None)


@cache
def _normalize(name: str) -> str:
    return name.replace("-", "_")


def _join_fragments(children: list[str | None]) -> str:
    return " ".join(c for c in children if c)


@cache
def _literal_rule(word: str) -> str:
    return _LITERAL_PREFIX + _normalize(word)


@cache
def _placeholder_rule(name: str) -> str:
    return _PLACEHOLDER_PREFIX + name


def _bracket_option_names(tree: Tree[Token]) -> set[str]:
    names: set[str] = set()
    for bracket in tree.find_data("repeated_optional"):
        for option_node in bracket.find_data("option"):
            declared = _option(str(option_node.children[0]))
            assert declared is not None
            names.add(_normalize(declared[0]))
    return names


class _PatternCompiler(Transformer[Token, str]):
    def __init__(self) -> None:
        super().__init__()
        self.placeholders: dict[str, object] = {}
        self.literals: dict[str, object] = {}
        self.options_by_raw_name: dict[str, Option] = {}
        self.repeated: set[str] = set()  # normalized option names known repeated so far; bracket-wrapped
        # repeats are discovered post-walk, so Option.repeated is finalized after the whole compile pass
        self.spellings: dict[str, set[str]] = {}

    def literal(self, children: list[Token]) -> str:
        word = str(children[0])
        self.literals[word] = False
        return _literal_rule(word)

    def placeholder(self, children: list[Token]) -> str:
        name = _normalize(str(children[0])[1:-1])
        self.placeholders.setdefault(name, None)  # a prior line's <name>... must not be downgraded
        self.spellings.setdefault(name, set()).add(f"<{name}>")
        return _placeholder_rule(name)

    def repeated_placeholder(self, children: list[Token]) -> str:
        name = _normalize(str(children[0])[1:-1])
        self.placeholders[name] = []
        self.spellings.setdefault(name, set()).add(f"<{name}>")
        self.repeated.add(name)
        return f"{_placeholder_rule(name)}+"

    def option(self, children: list[Token]) -> None:
        self._register_option(children)
        return None  # options never appear in the joined positional grammar text

    def repeated_option(self, children: list[Token]) -> None:
        self.repeated.add(self._register_option(children))

    def _register_option(self, children: list[Token]) -> str:
        declared = _option(str(children[0]))
        assert declared is not None  # the OPTION terminal only ever matches --flag-shaped text
        name, value = declared
        normalized = _normalize(name)
        self.options_by_raw_name[name] = Option(name, normalized, takes_value=value is not None)
        self.spellings.setdefault(normalized, set()).add(f"--{name}")
        return normalized

    def optional(self, children: list[str | None]) -> str | None:
        inner = _join_fragments(children)
        return f"({inner})?" if inner else None

    def repeated_optional(self, children: list[str | None]) -> str | None:
        inner = _join_fragments(children)
        return f"({inner})*" if inner else None

    def line(self, children: list[str | None]) -> str:
        return _join_fragments(children[1:])  # children[0] is the leading command word, not part of argv


def _grammar_source(compiler: _PatternCompiler, alternatives: list[str]) -> str:
    rules = [f"start: {' | '.join(f'({alt})' if alt else '' for alt in alternatives)}"]
    rules += [f"{_placeholder_rule(name)}: {_VALUE_TERMINAL}" for name in compiler.placeholders]
    rules += [f'{_literal_rule(word)}: "{word}"' for word in compiler.literals]
    rules.append(rf"{_VALUE_TERMINAL}: /[^\n]+/")
    rules.append(r"%ignore /\n+/")
    return "\n".join(rules)


def _default(has_value: bool, repeated: bool) -> object:
    if repeated:
        return []
    return None if has_value else False


def _defaults(compiler: _PatternCompiler) -> dict[str, object]:
    values: dict[str, object] = {**compiler.placeholders, **compiler.literals}
    values.update(
        {option.normalized_name: _default(option.takes_value, option.repeated) for option in compiler.options_by_raw_name.values()}
    )
    return values


def compile_command(command: str, lines: list[str]) -> CommandGrammar:
    if "-" in command:
        raise GrammarError(en.COMMAND_HYPHEN.format(commands=[command]))
    compiler = _PatternCompiler()
    alternatives: list[str] = []
    for line in lines:
        try:
            tree = _PATTERN.parse(line)
        except UnexpectedInput as exc:
            raise GrammarError(en.INVALID_GRAMMAR.format(command=command, error=exc)) from None
        compiler.repeated.update(_bracket_option_names(tree))
        alternatives.append(compiler.transform(tree))

    for option in compiler.options_by_raw_name.values():
        if option.normalized_name in compiler.repeated:
            option.repeated = True

    for key, tokens in compiler.spellings.items():
        if len(tokens) > 1:
            raise GrammarError(en.KEY_COLLISION.format(command=command, tokens=sorted(tokens), key=key))

    try:
        parser = Lark(
            _grammar_source(compiler, alternatives), parser="earley", lexer="dynamic", ambiguity="explicit", maybe_placeholders=False
        )
    except Exception as exc:
        raise GrammarError(en.INVALID_GRAMMAR.format(command=command, error=exc)) from None
    fields = tuple(compiler.placeholders) + tuple(option.normalized_name for option in compiler.options_by_raw_name.values())
    return CommandGrammar(parser, compiler.options_by_raw_name, fields, _defaults(compiler), frozenset(compiler.repeated))


def match(grammar: CommandGrammar, command: str, tokens: list[str]) -> dict[str, object]:
    list_values: dict[str, list] = {n: [] for n in grammar.repeated_fields}
    scalar_values: dict[str, object] = {k: v for k, v in grammar.defaults.items() if k not in list_values}

    positional: list[str] = []
    seen: set[str] = set()
    it = iter(tokens)

    for token in it:
        declared = _option(token)
        if declared is None:
            positional.append(token)
            continue
        name, value = declared

        option = grammar.options_by_raw_name.get(name)
        if option is None:
            raise ValueError(en.UNKNOWN_OPTION.format(command=command, tokens=tokens, option=name))
        elif name in seen and not option.repeated:
            raise ValueError(en.DUPLICATE_OPTION.format(command=command, tokens=tokens, option=name))
        elif not option.takes_value:
            if value is not None:
                raise ValueError(en.OPTION_NO_VALUE.format(command=command, tokens=tokens, option=name))
            resolved: object = True
        elif value is not None:
            resolved = value
        else:
            try:
                resolved = next(it)
            except StopIteration:
                raise ValueError(en.OPTION_NEEDS_VALUE.format(command=command, tokens=tokens, option=name)) from None

        seen.add(name)
        match option.repeated:
            case True:
                list_values[option.normalized_name].append(resolved)
            case False:
                scalar_values[option.normalized_name] = resolved

    try:
        tree = grammar.parser.parse(_TOKEN_SEP.join(t or _EMPTY_TOKEN for t in positional))
    except UnexpectedInput as exc:
        raise ValueError(en.NO_MATCH.format(command=command, tokens=tokens, detail=str(exc))) from None

    if _has_ambiguity(tree):
        raise ValueError(en.AMBIGUOUS_MATCH.format(command=command, tokens=tokens))

    return _bind_fields(tree, {**scalar_values, **list_values})


def _has_ambiguity(node: object) -> bool:
    if isinstance(node, Tree):
        if node.data == "_ambig":
            return True
        return any(_has_ambiguity(child) for child in node.children)
    return False


def _bind_fields(node: object, values: dict[str, object]) -> dict[str, object]:
    if isinstance(node, Tree):
        if node.data.startswith(_LITERAL_PREFIX):
            values[node.data[len(_LITERAL_PREFIX) :]] = True
        elif node.data.startswith(_PLACEHOLDER_PREFIX):
            token = node.children[0]
            if isinstance(token, Token):
                text = str(token)
                value = "" if text == _EMPTY_TOKEN else text
                name = node.data[len(_PLACEHOLDER_PREFIX) :]
                existing = values.get(name)
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    values[name] = value
        for child in node.children:
            _bind_fields(child, values)
    return values
