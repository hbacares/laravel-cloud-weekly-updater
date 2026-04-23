"""Tests for retry mechanism."""

import pytest

from websiteupdater.retry import retry_on_exception


def test_retry_succeeds_on_first_try():
    """Test that function succeeds on first attempt."""
    call_count = 0

    @retry_on_exception(max_attempts=3)
    def succeeds():
        nonlocal call_count
        call_count += 1
        return "success"

    result = succeeds()
    assert result == "success"
    assert call_count == 1


def test_retry_succeeds_after_failures():
    """Test that function succeeds after initial failures."""
    call_count = 0

    @retry_on_exception(exceptions=(ValueError,), max_attempts=3, delay=0.1)
    def fails_twice():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("not yet")
        return "success"

    result = fails_twice()
    assert result == "success"
    assert call_count == 3


def test_retry_exhausts_attempts():
    """Test that retry gives up after max attempts."""
    call_count = 0

    @retry_on_exception(exceptions=(ValueError,), max_attempts=3, delay=0.1)
    def always_fails():
        nonlocal call_count
        call_count += 1
        raise ValueError("always fails")

    with pytest.raises(ValueError, match="always fails"):
        always_fails()

    assert call_count == 3


def test_retry_only_catches_specified_exceptions():
    """Test that retry only catches specified exception types."""
    @retry_on_exception(exceptions=(ValueError,), max_attempts=3)
    def raises_type_error():
        raise TypeError("wrong type")

    with pytest.raises(TypeError, match="wrong type"):
        raises_type_error()


def test_retry_backoff():
    """Test that retry delays with backoff."""
    import time
    call_times = []

    @retry_on_exception(exceptions=(ValueError,), max_attempts=3, delay=0.1, backoff=2.0)
    def fails_with_timing():
        call_times.append(time.monotonic())
        if len(call_times) < 3:
            raise ValueError("retry")
        return "done"

    fails_with_timing()

    # Check that delays increase (roughly 0.1s, then 0.2s)
    assert len(call_times) == 3
    # First retry should be ~0.1s after first call
    assert call_times[1] - call_times[0] >= 0.09
    # Second retry should be ~0.2s after second call (backoff)
    assert call_times[2] - call_times[1] >= 0.18
