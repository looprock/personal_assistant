"""
Renders the digest data dict into an HTML email string using Jinja2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)


def render_email(data: dict[str, Any]) -> str:
    template = _env.get_template("digest_email.html")
    return template.render(**data)
