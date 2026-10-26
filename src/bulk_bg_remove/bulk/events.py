"""Progress events — one stream feeds the terminal, `--json` output and the UI.

Every event is a flat JSON object with a `type`. This is a public contract:
agents parse it, so only add fields, never rename or remove them.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeAlias

JSONValue: TypeAlias = "str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]"
JSONObject: TypeAlias = dict[str, JSONValue]


@dataclass(frozen=True, slots=True)
class Event:
    type: str
    data: JSONObject = field(default_factory=dict)
    at: float = field(default_factory=time.time)

    def to_dict(self) -> JSONObject:
        return {"type": self.type, "at": round(self.at, 3), **self.data}


EventSink = Callable[[Event], None]
