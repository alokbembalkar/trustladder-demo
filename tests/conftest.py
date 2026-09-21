"""
Test setup: every test run builds its own private demo world, so the suite never
touches the data the live demo uses (and the live demo never affects the tests).

The environment variable is set here, before any test module imports the
package, because the package reads it once at import time.
"""

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="trustladder_test_"))
os.environ["TRUSTLADDER_DATA"] = str(_TMP / "data")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from trustladder.cases import DATA_DIR  # noqa: E402
from trustladder.seed import build_and_snapshot  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def demo_world():
    """Build the full demo world once per test session."""
    assert str(DATA_DIR).startswith(str(_TMP)), "tests must never use the live demo data"
    build_and_snapshot(DATA_DIR)
    yield DATA_DIR
