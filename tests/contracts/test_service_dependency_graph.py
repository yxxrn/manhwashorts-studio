"""Architecture guardrails for the application import graph."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _application_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = _module_name(path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names if alias.name.startswith("app"))
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            base = resolve_name("." * node.level + (node.module or ""), package)
        elif node.module:
            base = node.module
        else:
            continue
        if not base.startswith("app"):
            continue
        imports.add(base)
        imports.update(
            f"{base}.{alias.name}" for alias in node.names if alias.name != "*"
        )
    return imports


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[set[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[set[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in graph.get(node, set()):
            if neighbor not in indices:
                visit(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbor])
        if lowlinks[node] == indices[node]:
            component: set[str] = set()
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.add(member)
                if member == node:
                    break
            components.append(component)

    for node in graph:
        if node not in indices:
            visit(node)
    return components


def test_application_import_graph_has_no_cycles():
    modules = {_module_name(path): path for path in APP.rglob("*.py")}
    graph = {
        module: {name for name in _application_imports(path) if name in modules and name != module}
        for module, path in modules.items()
    }
    cycles = [component for component in _strongly_connected_components(graph) if len(component) > 1]
    assert cycles == [], "circular application imports: " + "; ".join(
        " -> ".join(sorted(component)) for component in cycles
    )
