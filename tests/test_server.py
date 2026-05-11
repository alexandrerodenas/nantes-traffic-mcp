import pytest
from naolib_mcp.server import app

def test_app_exists():
    assert app is not None

# We can add more tests as needed, for example testing the search_stop function
def test_search_stop_returns_dict():
    # This is a simple test; we might want to mock the HTTP call in the future
    from naolib_mcp.server import search_stop
    result = search_stop("Gare Nord")
    assert isinstance(result, dict)
    assert "output" in result