import pytest
from naolib_mcp.server import app, search_stop


def test_app_exists():
    """FastMCP app should be importable (exposed for external tooling)."""
    assert app is not None


def test_search_stop_returns_string():
    """search_stop returns a human-readable string (not a dict)."""
    result = search_stop("Gare Nord")
    # Returns a string (either "No stops found..." or "Best matches: ...")
    assert isinstance(result, str)
    assert len(result) > 0


def test_search_stop_unknown_returns_not_found():
    """Unknown stop names return a 'not found' message."""
    result = search_stop("xyzabcdefghijklmnop")
    assert "No stops found" in result
