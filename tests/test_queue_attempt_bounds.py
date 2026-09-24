import pytest

from app.queue import (
    MAX_JOB_ATTEMPTS,
    MIN_JOB_ATTEMPTS,
    _validate_max_attempts,
    _validate_reclaim_timeout,
)


def test_accepts_supported_attempt_bounds() -> None:
    assert _validate_max_attempts(MIN_JOB_ATTEMPTS) == 1
    assert _validate_max_attempts(3) == 3
    assert _validate_max_attempts(MAX_JOB_ATTEMPTS) == 100


@pytest.mark.parametrize("value", [0, -1, 101, 1000])
def test_rejects_attempt_count_outside_supported_range(value: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        _validate_max_attempts(value)


@pytest.mark.parametrize("value", [True, 1.5, "3"])
def test_rejects_non_integer_attempt_count(value) -> None:
    with pytest.raises(ValueError, match="must be an integer"):
        _validate_max_attempts(value)


@pytest.mark.parametrize("value", [1, 30, 3600])
def test_accepts_positive_integer_reclaim_timeout(value: int) -> None:
    assert _validate_reclaim_timeout(value) == value


@pytest.mark.parametrize("value", [0, -1, -30])
def test_rejects_non_positive_reclaim_timeout(value: int) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        _validate_reclaim_timeout(value)


@pytest.mark.parametrize("value", [True, 1.5, "30", None])
def test_rejects_non_integer_reclaim_timeout(value) -> None:
    with pytest.raises(ValueError, match="must be an integer"):
        _validate_reclaim_timeout(value)
