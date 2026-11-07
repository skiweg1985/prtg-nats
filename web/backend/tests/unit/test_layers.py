"""The lower layers do not import the services built on top of them.

The transport once imported the service that records management routes, and
with it the database session: a command-line call that only wanted SSH pulled
in the whole service layer. The composition roots - ``app.main``, the API
dependencies, ``app.ops`` - wire services into infrastructure instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
LOWER_LAYERS = ("core", "domain", "infrastructure", "persistence")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


@pytest.mark.parametrize("layer", LOWER_LAYERS)
def test_a_lower_layer_does_not_import_services(layer: str) -> None:
    offenders = {
        str(path.relative_to(APP)): sorted(
            module
            for module in imported_modules(path)
            if module == "app.services" or module.startswith("app.services.")
        )
        for path in (APP / layer).rglob("*.py")
    }
    assert {path: found for path, found in offenders.items() if found} == {}
