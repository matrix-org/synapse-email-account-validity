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

from collections import namedtuple
import sqlite3
import pkg_resources
import time
from unittest import mock

import jinja2
from synapse.module_api import ModuleApi

from email_account_validity import EmailAccountValidity


class SQLiteStore:
    """In-memory SQLite store. We can't just use a run_db_interaction function that opens
    its own connection, since we need to use the same connection for all queries in a
    test.
    """
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")

    async def run_db_interaction(self, desc, f, *args, **kwargs):
        cur = CursorWrapper(self.conn.cursor())
        try:
            res = f(cur, *args, **kwargs)
            self.conn.commit()
            return res
        except Exception:
            self.conn.rollback()
            raise


class CursorWrapper:
    """Wrapper around a SQLite cursor that also provides a call_after method."""
    def __init__(self, cursor: sqlite3.Cursor):
        self.cur = cursor

    def execute(self, sql, args):
        self.cur.execute(sql, args)

    @property
    def rowcount(self):
        return self.cur.rowcount

    def fetchone(self):
        return self.cur.fetchone()

    def call_after(self, f, args):
        f(args)

    def __iter__(self):
        return self.cur.__iter__()

    def __next__(self):
        return self.cur.__next__()


def read_templates(filenames):
    """Reads Jinja templates from the templates directory. This function is mostly copied
    from Synapse.
    """
    loader = jinja2.FileSystemLoader(
        pkg_resources.resource_filename("email_account_validity", "templates")
    )
    env = jinja2.Environment(
        loader=loader,
        autoescape=jinja2.select_autoescape(),
    )

    def _format_ts_filter(value: int, format: str):
        return time.strftime(format, time.localtime(value / 1000))

    env.filters.update(
        {
            "format_ts": _format_ts_filter,
        }
    )

    return [env.get_template(filename) for filename in filenames]


def current_time_ms():
    """Returns the current time in milliseconds.
    """
    return int(time.time() * 1000)


async def get_profile_for_user(user_id):
    ProfileInfo = namedtuple("ProfileInfo", ("avatar_url", "display_name"))
    return ProfileInfo(None, "Izzy")


async def send_mail(recipient, subject, html, text):
    return None


async def create_account_validity_module() -> EmailAccountValidity:
    """Starts an EmailAccountValidity module with a basic config and a mock of the
    ModuleApi.
    """
    config = {
        "period": 3628800000,  # 6w
        "renew_at": 604800000,  # 1w
    }

    store = SQLiteStore()

    # Create a mock based on the ModuleApi spec, but override some mocked functions
    # because some capabilities (interacting with the database, getting the current time,
    # etc.) are needed for running the tests.
    module_api = mock.Mock(spec=ModuleApi)
    module_api.run_db_interaction.side_effect = store.run_db_interaction
    module_api.read_templates.side_effect = read_templates
    module_api.current_time_ms.side_effect = current_time_ms
    module_api.get_profile_for_user.side_effect = get_profile_for_user
    module_api.send_mail.side_effect = send_mail

    # Make sure the table is created. Don't try to populate with users since we don't
    # have tables to populate from.
    module = EmailAccountValidity(config, module_api, populate_users=False)
    await module._store.create_and_populate_table(populate_users=False)

    return module
