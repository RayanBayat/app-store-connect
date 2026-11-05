"""Every endpoint the code can call is listed in ENDPOINTS_USED, and none is stale."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app_store_connect.spec import ENDPOINTS_USED

TOOLS_DIR = Path(__file__).parent.parent / "src" / "app_store_connect" / "tools"
PATH_PATTERN = re.compile(r"/v[12]/\S*")


def paths_in_code() -> set[str]:
    """Every string literal that looks like an Apple API path, in all tool modules."""
    found: set[str] = set()
    for file in TOOLS_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and PATH_PATTERN.fullmatch(node.value)
            ):
                found.add(node.value)
    return found


def localization_templates(file: Path) -> set[tuple[str, str]]:
    """`update_localization(..., template="/v1/x/{id}")` does a GET then a PATCH on it."""
    found: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "template" and isinstance(keyword.value, ast.Constant):
                    found |= {
                        ("GET", str(keyword.value.value)),
                        ("PATCH", str(keyword.value.value)),
                    }
    return found


def calls_in_code() -> set[tuple[str, str]]:
    """(method, path) of every `client.request("METHOD", "/v1/...")` call, found statically."""
    found: set[tuple[str, str]] = set()
    for file in TOOLS_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            args = [a.value for a in node.args if isinstance(a, ast.Constant)]
            if node.func.attr == "request" and len(args) == 2:
                found.add((str(args[0]), str(args[1])))
            if node.func.attr == "paginate" and len(args) == 1:
                found.add(("GET", str(args[0])))
        found |= localization_templates(file)
    return found


def test_every_client_call_in_the_code_is_in_endpoints_used() -> None:
    calls = calls_in_code()
    assert calls, "the scan found no calls; the test is broken"
    assert calls <= ENDPOINTS_USED, sorted(calls - ENDPOINTS_USED)


def test_every_path_literal_in_tools_is_a_used_endpoint_path() -> None:
    used_paths = {path for _, path in ENDPOINTS_USED}
    assert paths_in_code() <= used_paths


def test_no_stale_entries_in_endpoints_used() -> None:
    assert calls_in_code() >= ENDPOINTS_USED, sorted(ENDPOINTS_USED - calls_in_code())


def test_only_known_write_methods_are_used() -> None:
    writers = {m for m, _ in calls_in_code() if m != "GET"}
    assert writers <= {"POST", "PATCH", "DELETE"}
