"""Checks on the shape of the source, rather than on what it does."""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = sorted(list((ROOT / "desktop_app").glob("*.py"))
                 + list((ROOT / "network_a2a").glob("*.py")))


def classes(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def accessor_companion(item):
    """Whether this definition is a property's setter, getter or deleter.

    Those are written as a second ``def`` with the same name, which is the
    idiom rather than a mistake: ``@http.setter def http(self, value)`` attaches
    to the property built by the first one. Only an undecorated repeat is a
    real redefinition.
    """
    for decorator in item.decorator_list:
        if isinstance(decorator, ast.Attribute) \
                and decorator.attr in ("setter", "getter", "deleter"):
            return True
    return False


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_class_defines_the_same_method_twice(path):
    """A repeated ``def`` in one class silently replaces the earlier one.

    Nothing complains and the file still imports, so the first definition
    simply stops being reachable. Both halves of this have happened here: a
    header button builder that was kept after its replacement landed beside
    it, and an HTTP handler whose name matched the method it called, which
    replaced it and made the model method uncallable. Both were found by
    reading the traceback, not by a failure.
    """
    clashes = []
    for node in classes(path):
        seen = {}
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name in seen and not accessor_companion(item):
                clashes.append(
                    f"{path.name}: {node.name}.{item.name} is defined at "
                    f"line {seen[item.name]} and again at line {item.lineno}, "
                    f"so the first one cannot be reached")
            seen[item.name] = item.lineno
    assert not clashes, "\n".join(clashes)


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_a_module_defines_no_function_twice_either(path):
    """The same trap at module level, where the later name also wins."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    seen = {}
    clashes = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in seen:
            clashes.append(
                f"{path.name}: {node.name} is defined at line {seen[node.name]} "
                f"and again at line {node.lineno}")
        seen[node.name] = node.lineno
    assert not clashes, "\n".join(clashes)
