"""Shared pytest fixtures.

Warms up the structured logger once per session. The first ``log_debug`` call
lazily imports the UI module (``src.ui.components.debug_logs``), which costs
~0.5s. In timing-sensitive async tests (e.g. live log-tailing with a short
``asyncio.wait_for`` timeout) that one-time cost runs synchronously inside the
code under test and starves the event loop, preventing sibling tasks from
running within the test window. Triggering it up front makes those tests
deterministic.
"""

import pytest

from src.core import security
from src.core.log_parser import LapData, LogParser, SessionData
from src.utils.structured_logger import Component, log_debug

TEST_APP_SECRET = "0000000000000000000000000000000000000000000000000000000000000000"


@pytest.fixture(autouse=True)
def _clear_app_secret(monkeypatch):
    """Keep tests offline unless they explicitly request signing credentials."""
    monkeypatch.delenv("APP_SECRET", raising=False)
    monkeypatch.setattr(security, "APP_SECRET", None)


@pytest.fixture
def configured_app_secret(monkeypatch):
    """Configure the deterministic test secret for signing-specific tests."""
    monkeypatch.setenv("APP_SECRET", TEST_APP_SECRET)
    monkeypatch.setattr(security, "APP_SECRET", TEST_APP_SECRET)
    return TEST_APP_SECRET


def make_parser(car_id: str, with_completed_lap: bool = False) -> LogParser:
    """Create a LogParser initialized with the given car UUID for testing."""
    parser = LogParser()
    parser.context.car_uuid = car_id
    parser.context.player_car_uuids.add(car_id)
    parser.current_session = SessionData(car_uuid=car_id)
    if with_completed_lap:
        parser.current_session.laps.append(
            LapData(
                lap_number=1,
                physics_lap_number=1,
                lap_time_ms=100000,
                lap_time_str="1:40.000",
            )
        )
    return parser


@pytest.fixture(scope="session", autouse=True)
def _warm_up_structured_logger():
    """Pay the one-time lazy-import cost of the logger before tests run."""
    log_debug(Component.LOG_PARSER, "[conftest] logger warm-up")
