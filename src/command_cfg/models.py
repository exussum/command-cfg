"""The serializer kinds (scalar/group/array/raw/each) that wrap a command's factory; see command_cfg for usage."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

Serializer = Callable[[list[SimpleNamespace], Mapping[str, Any]], Any]

_DEFAULT_TYPES: Mapping[str, Callable[..., Any]] = {"str": str, "int": int, "float": float}


@dataclass(frozen=True, kw_only=True)
class _Typed:
    # loose Callable[..., Any]: scalar's casters run as caster(value, objects), every
    # other kind's as caster(value) via coerce() — see command_cfg's own docstring.
    types: Mapping[str, Callable[..., Any]] = field(default_factory=lambda: dict(_DEFAULT_TYPES))

    def __post_init__(self) -> None:
        object.__setattr__(self, "types", {**_DEFAULT_TYPES, **self.types})


@dataclass(frozen=True)
class scalar(_Typed):
    factory: Callable[..., Any]


@dataclass(frozen=True)
class group(_Typed):
    factory: Callable[..., Any]
    include_key: bool = False


@dataclass(frozen=True)
class array(_Typed):
    factory: Callable[..., Any]


@dataclass(frozen=True)
class raw(_Typed):
    serializer: Serializer


@dataclass(frozen=True)
class each(_Typed):
    handler: Callable[[dict[str, Any], SimpleNamespace], None]
    default: Callable[[], Any] | None = field(default=None, kw_only=True)
