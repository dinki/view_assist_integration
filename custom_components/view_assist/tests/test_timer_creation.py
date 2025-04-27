import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from custom_components.view_assist.timers import VATimers, TimerClass, TimerInterval, TimerLanguage, TimerStatus, Timer


@pytest.fixture
def mock_hass():
    """Mock Home Assistant instance."""
    hass = MagicMock()
    hass.config.time_zone = "UTC"
    hass.bus.async_fire = MagicMock()
    return hass

@pytest.fixture
def mock_store():
    """Mock Home Assistant Store."""
    store = MagicMock()
    store.async_save = AsyncMock()
    store.async_load = AsyncMock(return_value=None)
    return store

@pytest.fixture
def va_timers(mock_hass, mock_store):
    """Initialize VATimers with mocked dependencies."""
    timers = VATimers(mock_hass, MagicMock())
    timers.store.store = mock_store
    return timers

@pytest.mark.asyncio
async def test_timer_creation_and_fetching(va_timers, mock_hass, mock_store):
    # Mock current time
    now = datetime.now()

    # Mock timer interval
    timer_interval = TimerInterval(minutes=5)

    # Add a timer
    timer_id, timer_output, encoded_time = await va_timers.add_timer(
        timer_class=TimerClass.TIMER,
        device_or_entity_id="test.entity",
        timer_info=timer_interval,
        name="Test Timer",
        pre_expire_warning=10,
        start=False,
        extra_info={},
        language=TimerLanguage.EN,
    )

    # Verify the timer was saved
    assert timer_id is not None
    assert timer_output["name"] == "Test Timer"
    assert timer_output["timer_class"] == TimerClass.TIMER
    assert timer_output["expiry"]["interval"]["minutes"] == 5

    # Verify the timer is in the store
    assert timer_id in va_timers.store.timers

    # Fetch the timer
    fetched_timers = va_timers.get_timers(timer_id=timer_id)
    assert len(fetched_timers) == 1
    assert fetched_timers[0]["name"] == "Test Timer"
    assert fetched_timers[0]["language"] == TimerLanguage.EN

    # Verify the expiry time is correctly formatted
    expected_time = fetched_timers[0]["expiry"]["time"]
    assert isinstance(expected_time, str)
    assert ":" in expected_time  # Ensure it looks like a time string (e.g., "12:34:56")

    # Verify that no events were fired since the timer was not started
    mock_hass.bus.async_fire.assert_not_called()
