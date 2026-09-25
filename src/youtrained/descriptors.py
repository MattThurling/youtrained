"""Dataset descriptors: the human-facing text shown verbatim in reports, loaded from YAML."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, HttpUrl

from .models import KEY_TYPES

BANNED_PHRASES = ("proof", "stolen", "trained on this")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(_Strict):
    title: str
    url: HttpUrl
    date: date


class Licence(_Strict):
    name: str
    url: HttpUrl


class Citation(_Strict):
    model: str
    url: HttpUrl
    note: str


class Usage(_Strict):
    role: list[str]
    cited_by: list[Citation]


class LitigationContext(_Strict):
    text: str
    sources: list[Source]
    last_reviewed: date


class Action(_Strict):
    title: str
    text: str
    sources: list[Source]


class Descriptor(_Strict):
    name: str
    publisher: str
    year: int
    homepage: HttpUrl
    licence: Licence
    key_types: list[str]
    usage: Usage
    description: str
    litigation_context: LitigationContext
    what_you_can_do: list[Action]

    def text_fields(self) -> list[tuple[str, str]]:
        """Every user-facing prose field, as (label, text), for wording checks."""
        out = [
            ("description", self.description),
            ("litigation_context", self.litigation_context.text),
        ]
        out += [(f"cited_by[{c.model}]", c.note) for c in self.usage.cited_by]
        out += [(f"what_you_can_do[{a.title}]", a.text) for a in self.what_you_can_do]
        return out


def _dataset_dir() -> Path:
    return Path(str(resources.files("youtrained") / "datasets"))


def list_descriptors() -> list[str]:
    return sorted(p.stem for p in _dataset_dir().glob("*.yaml") if not p.stem.startswith("_"))


@lru_cache
def _common() -> dict:
    """Shared text (litigation context, what you can do) that every dataset reuses."""
    path = _dataset_dir() / "_common.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


@lru_cache
def load_descriptor(name: str) -> Descriptor:
    path = _dataset_dir() / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no descriptor for dataset {name!r} at {path}")
    data = {**_common(), **yaml.safe_load(path.read_text(encoding="utf-8"))}
    desc = Descriptor.model_validate(data)
    unknown = set(desc.key_types) - set(KEY_TYPES)
    if unknown:
        raise ValueError(f"{name}: unknown key_types {sorted(unknown)}")
    return desc
