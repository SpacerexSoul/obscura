"""CLI smoke tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from obscura import __version__
from obscura.cli import main

SLICE_PATH = (
    Path(__file__).parent.parent
    / "data"
    / "itch-samples"
    / "itch_slice_5mb.gz"
)


def test_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_exits_zero():
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0


def test_parse_missing_file(capsys):
    rc = main(["parse", "/no/such/file.gz"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_book_missing_file(capsys):
    rc = main(["book", "/no/such/file.gz", "--symbol", "AAPL"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err


@pytest.mark.skipif(not SLICE_PATH.exists(), reason="M1 spike slice not present")
def test_parse_against_slice(capsys):
    rc = main(["parse", str(SLICE_PATH), "--limit", "10000"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "parsed 10,000 events" in out


@pytest.mark.skipif(not SLICE_PATH.exists(), reason="M1 spike slice not present")
def test_book_against_slice(capsys):
    rc = main(["book", str(SLICE_PATH), "--symbol", "AAPL", "--limit", "200000"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "AAPL book" in out
