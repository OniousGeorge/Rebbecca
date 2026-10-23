import pytest

from core.single_instance import AlreadyRunning, hold_lock


def test_a_second_copy_cannot_take_the_lock(tmp_path):
    lock_file = tmp_path / "agent.lock"
    first = hold_lock(lock_file)
    with pytest.raises(AlreadyRunning):
        hold_lock(lock_file)
    first.close()


def test_the_lock_is_free_again_once_released(tmp_path):
    lock_file = tmp_path / "agent.lock"
    hold_lock(lock_file).close()
    second = hold_lock(lock_file)
    second.close()
