from __future__ import annotations

import re
from pathlib import Path


TOKEN = re.compile(r"\[\[\s*([A-Za-z0-9_]+)\s*\]\]")


def render_text(template: str, context: dict[str, object]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            raise KeyError(f"missing template value: {key}")
        return str(context[key])

    return TOKEN.sub(replace, template)


def render_file(src: Path, dst: Path, context: dict[str, object]) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(render_text(src.read_text(encoding="utf-8"), context), encoding="utf-8")
