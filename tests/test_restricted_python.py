"""CI guard that Contents/Code/__init__.py stays inside Plex's sandbox.

Plex compiles plugin code with RestrictedPython.RCompile.RModule(...). The
compiler rejects identifiers starting with underscore, yield statements, and
augmented assignments to attributes or subscripts. The live deployment failed on
exactly those constructs, so this module parses the plugin with ``ast`` and
reports them before the bundle reaches a Plex server.
"""

import ast

import plex_stubs


def _names(node):
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.arg):
        return (node.arg,)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return (node.name,)
    if isinstance(node, ast.Attribute):
        return (node.attr,)
    if isinstance(node, ast.alias):
        return tuple(name for name in (node.name, node.asname) if name)
    return ()


def check_restricted_python_ast(source):
    """Return ``(line, message)`` for every RestrictedPython violation in source."""
    tree = ast.parse(source)
    violations = []
    for node in ast.walk(tree):
        for name in _names(node):
            if name.startswith("_"):
                violations.append((node.lineno, "underscore identifier %r" % name))
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            violations.append((node.lineno, "yield is not allowed"))
        if isinstance(node, ast.AugAssign) and isinstance(
            node.target, (ast.Attribute, ast.Subscript)
        ):
            violations.append((node.lineno, "augmented assignment to attribute or subscript"))
    return sorted(violations)


def test_plugin_passes_restricted_python_ast_rules():
    source = plex_stubs.AGENT_PATH.read_text(encoding="utf-8")

    assert check_restricted_python_ast(source) == []


def test_clean_source_has_no_violations():
    assert check_restricted_python_ast("value = 1\nprint(value)\n") == []


def test_underscore_names_are_reported():
    source = "_foo = 1\ndef _helper():\n  return _foo\nclass _Thing(object):\n  pass\n"

    assert check_restricted_python_ast(source) == [
        (1, "underscore identifier '_foo'"),
        (2, "underscore identifier '_helper'"),
        (3, "underscore identifier '_foo'"),
        (4, "underscore identifier '_Thing'"),
    ]


def test_underscore_attributes_and_aliases_are_reported():
    source = "import collections as _collections\nvalue = _collections.OrderedDict()\nvalue._hidden = 1\n"

    assert check_restricted_python_ast(source) == [
        (1, "underscore identifier '_collections'"),
        (2, "underscore identifier '_collections'"),
        (3, "underscore identifier '_hidden'"),
    ]


def test_yield_is_reported():
    source = "def helper():\n  yield 1\n"

    assert check_restricted_python_ast(source) == [(2, "yield is not allowed")]


def test_augmented_assignment_to_attribute_and_subscript_is_reported():
    source = "def helper(obj, mapping):\n  obj.count += 1\n  mapping['k'] += 1\n  total = 0\n  total += 1\n"

    assert check_restricted_python_ast(source) == [
        (2, "augmented assignment to attribute or subscript"),
        (3, "augmented assignment to attribute or subscript"),
    ]
