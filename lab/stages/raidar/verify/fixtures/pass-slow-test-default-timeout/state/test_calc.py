import time

from calc import add


def test_add() -> None:
    time.sleep(6)
    assert add(1, 2) == 3
