# -*- coding: utf-8 -*-
# Copyright 2021 The Matrix.org Foundation C.I.C.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
import secrets
import string
from typing import Union


UNAUTHENTICATED_TOKEN_REGEX = re.compile('^[a-zA-Z]{32}$')


def random_digit_string(length):
    return "".join(secrets.choice(string.digits) for _ in range(length))


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
