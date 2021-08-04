import secrets
import string
from typing import Union


def parse_duration(value: Union[str, int]) -> int:
    """Convert a duration as a string or integer to a number of milliseconds.

    If an integer is provided it is treated as milliseconds and is unchanged.

    String durations can have a suffix of 's', 'm', 'h', 'd', 'w', or 'y'.
    No suffix is treated as milliseconds.

    Args:
        value: The duration to parse.

    Returns:
        The number of milliseconds in the duration.
    """
    if isinstance(value, int):
        return value
    second = 1000
    minute = 60 * second
    hour = 60 * minute
    day = 24 * hour
    week = 7 * day
    year = 365 * day
    sizes = {"s": second, "m": minute, "h": hour, "d": day, "w": week, "y": year}
    size = 1
    suffix = value[-1]
    if suffix in sizes:
        value = value[:-1]
        size = sizes[suffix]
    return int(value) * size


def random_string(length: int) -> str:
    """Generate a cryptographically secure string of random letters.

    Drawn from the characters: `a-z` and `A-Z`
    """
    return "".join(secrets.choice(string.ascii_letters) for _ in range(length))
