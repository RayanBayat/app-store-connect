"""Tests for scripts/osv_check.py (no real network)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import osv_check
import pytest

GO_MOD = """\
module example.com/app

go 1.24.0

toolchain go1.24.3

require github.com/a/direct v1.2.3

require (
\tgithub.com/b/block v0.4.0
\tgithub.com/c/indirect v2.0.0+incompatible // indirect
)

replace github.com/a/direct => ../direct

replace (
\tgithub.com/x/y v1.0.0 => github.com/z/y v1.0.1
)

exclude github.com/bad/mod v9.9.9
retract v1.0.0
"""


def test_parse_go_mod_collects_requires_and_toolchain() -> None:
    assert osv_check.parse_go_mod(GO_MOD) == [
        ("github.com/a/direct", "1.2.3"),
        ("github.com/b/block", "0.4.0"),
        ("github.com/c/indirect", "2.0.0+incompatible"),
        ("stdlib", "1.24.3"),
    ]


def test_parse_go_mod_falls_back_to_go_line() -> None:
    assert osv_check.parse_go_mod("module m\ngo 1.23.1\n") == [("stdlib", "1.23.1")]


def test_parse_go_mod_skips_replace_exclude_retract_blocks() -> None:
    text = "exclude (\n\tgithub.com/p/q v1.0.0\n)\nretract (\n\tv1.0.0\n)\n"
    assert osv_check.parse_go_mod(text) == []


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def install_fake_osv(monkeypatch: pytest.MonkeyPatch, batch_replies: list[dict]) -> list:
    """Replace urlopen; batch POSTs pop replies in order, GETs serve a vuln record."""
    seen: list = []

    def fake_urlopen(request, timeout):
        assert timeout == 20
        seen.append(request)
        if request.data is not None:
            return FakeResponse(json.dumps(batch_replies.pop(0)).encode())
        events = [{"introduced": "0"}, {"fixed": "1.2.4"}]
        record = {
            "id": "GO-1",
            "summary": "Bad thing",
            "affected": [{"ranges": [{"events": events}]}],
        }
        return FakeResponse(json.dumps(record).encode())

    monkeypatch.setattr(osv_check.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_run_reports_vulnerability_and_exits_1(tmp_path: Path, monkeypatch, capsys) -> None:
    go_mod = tmp_path / "go.mod"
    go_mod.write_text("module m\nrequire github.com/a/direct v1.2.3\n", encoding="utf-8")
    seen = install_fake_osv(monkeypatch, [{"results": [{"vulns": [{"id": "GO-1"}]}]}])
    assert osv_check.main([str(go_mod)]) == 1
    out = capsys.readouterr().out
    assert "GO-1: Bad thing" in out
    assert "fixed in: 1.2.4" in out
    body = json.loads(seen[0].data)
    assert body["queries"][0] == {
        "package": {"name": "github.com/a/direct", "ecosystem": "Go"},
        "version": "1.2.3",
    }


def test_run_clean_exits_0(tmp_path: Path, monkeypatch) -> None:
    go_mod = tmp_path / "go.mod"
    go_mod.write_text("module m\nrequire github.com/a/direct v1.2.3\n", encoding="utf-8")
    install_fake_osv(monkeypatch, [{"results": [{}]}])
    assert osv_check.main([str(go_mod)]) == 0


def test_pagination_follows_next_page_token(monkeypatch) -> None:
    seen = install_fake_osv(
        monkeypatch,
        [
            {"results": [{"vulns": [{"id": "A"}], "next_page_token": "tok"}]},
            {"results": [{"vulns": [{"id": "B"}]}]},
        ],
    )
    found = osv_check.query_vuln_ids([("m", "1.0.0")])
    assert found[("m", "1.0.0")] == {"A", "B"}
    assert json.loads(seen[1].data)["queries"][0]["page_token"] == "tok"


def test_network_error_exits_2(tmp_path: Path, monkeypatch, capsys) -> None:
    go_mod = tmp_path / "go.mod"
    go_mod.write_text("module m\nrequire github.com/a/direct v1.2.3\n", encoding="utf-8")

    def broken(_request, timeout):
        raise osv_check.urllib.error.URLError("offline")

    monkeypatch.setattr(osv_check.urllib.request, "urlopen", broken)
    assert osv_check.main([str(go_mod)]) == 2
    assert "offline" in capsys.readouterr().err


def test_missing_file_exits_2(tmp_path: Path) -> None:
    assert osv_check.main([str(tmp_path / "nope")]) == 2
