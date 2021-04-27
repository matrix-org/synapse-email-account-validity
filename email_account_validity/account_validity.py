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

import logging
from typing import Any, Tuple

from synapse.config._base import Config, ConfigError
from synapse.module_api import ModuleApi, run_in_background

from email_account_validity._base import EmailAccountValidityBase
from email_account_validity._store import EmailAccountValidityStore

logger = logging.getLogger(__name__)


class EmailAccountValidity(EmailAccountValidityBase):
    def __init__(self, config: Any, api: ModuleApi):
        self._store = EmailAccountValidityStore(config, api)
        self._api = api

        super().__init__(config, self._api, self._store)

        run_in_background(self._store.create_and_populate_table)
        self._api.looping_background_call_async(
            self._send_renewal_emails, 30 * 60 * 1000
        )

        if not api.public_baseurl:
            raise ConfigError("Can't send renewal emails without 'public_baseurl'")

    @staticmethod
    def parse_config(config: dict):
        if "period" not in config:
            raise ConfigError("'period' is required when using email account validity")

        if "renew_at" not in config:
            raise ConfigError(
                "'renew_at' is required when using email account validity"
            )

        config["period"] = Config.parse_duration(config.get("period") or 0)
        config["renew_at"] = Config.parse_duration(config.get("renew_at") or 0)
        return config

    async def on_legacy_renew(self, renewal_token):
        return await self.renew_account(renewal_token)

    async def on_legacy_send_mail(self, user_id):
        await self.send_renewal_email_to_user(user_id)

    async def on_legacy_admin_request(self, request):
        return await self.set_account_validity_from_request(request)

    async def user_expired(self, user_id: str) -> Tuple[bool, bool]:
        expiration_ts = await self._store.get_expiration_ts_for_user(user_id)
        if expiration_ts is None:
            return False, False

        now_ts = self._api.current_time_ms()
        return now_ts >= expiration_ts, True

    async def on_user_registration(self, user_id: str):
        await self._store.set_expiration_date_for_user(user_id)

    async def _send_renewal_emails(self):
        """Gets the list of users whose account is expiring in the amount of time
        configured in the ``renew_at`` parameter from the ``account_validity``
        configuration, and sends renewal emails to all of these users as long as they
        have an email 3PID attached to their account.
        """
        expiring_users = await self._store.get_users_expiring_soon()

        if expiring_users:
            for user in expiring_users:
                if user["expiration_ts_ms"] is None:
                    logger.warning(
                        "User %s has no expiration ts, ignoring" % user["user_id"],
                    )
                    continue

                await self.send_renewal_email(
                    user_id=user["user_id"], expiration_ts=user["expiration_ts_ms"]
                )
