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
import time
from typing import Tuple, Optional

from twisted.web.server import Request

from synapse.module_api import ModuleApi, run_in_background
from synapse.module_api.errors import ConfigError

from email_account_validity._base import EmailAccountValidityBase
from email_account_validity._config import EmailAccountValidityConfig
from email_account_validity._servlets import (
    EmailAccountValidityAdminServlet,
    EmailAccountValidityRenewServlet,
    EmailAccountValiditySendMailServlet,
)
from email_account_validity._store import EmailAccountValidityStore
from email_account_validity._utils import parse_duration

logger = logging.getLogger(__name__)


class EmailAccountValidity(EmailAccountValidityBase):
    def __init__(
        self,
        config: EmailAccountValidityConfig,
        api: ModuleApi,
        populate_users: bool = True,
    ):
        self._store = EmailAccountValidityStore(config, api)
        self._api = api

        super().__init__(config, self._api, self._store)

        run_in_background(self._store.create_and_populate_table, populate_users)
        self._api.looping_background_call(
            self._send_renewal_emails, 30 * 60 * 1000
        )

        self._api.register_account_validity_callbacks(
            is_user_expired=self.is_user_expired,
            on_user_registration=self.on_user_registration,
            on_legacy_send_mail=self.on_legacy_send_mail,
            on_legacy_renew=self.on_legacy_renew,
            on_legacy_admin_request=self.on_legacy_admin_request,
        )

        self._api.register_web_resource(
            path="/_synapse/client/email_account_validity/renew",
            resource=EmailAccountValidityRenewServlet(config, self._api, self._store)
        )

        self._api.register_web_resource(
            path="/_synapse/client/email_account_validity/send_mail",
            resource=EmailAccountValiditySendMailServlet(config, self._api, self._store)
        )

        self._api.register_web_resource(
            path="/_synapse/client/email_account_validity/admin",
            resource=EmailAccountValidityAdminServlet(config, self._api, self._store)
        )

        if not api.public_baseurl:
            raise ConfigError("Can't send renewal emails without 'public_baseurl'")

    @staticmethod
    def parse_config(config: dict):
        """Check that the configuration includes the required keys and parse the values
        expressed as durations."""
        if "period" not in config:
            raise ConfigError("'period' is required when using email account validity")

        if "renew_at" not in config:
            raise ConfigError(
                "'renew_at' is required when using email account validity"
            )

        parsed_config = EmailAccountValidityConfig(
            period=parse_duration(config["period"]),
            renew_at=parse_duration(config["renew_at"]),
            renew_email_subject=config.get("renew_email_subject"),
        )
        return parsed_config

    async def on_legacy_renew(self, renewal_token: str) -> Tuple[bool, bool, int]:
        """Attempt to renew an account and return the results of this attempt to the
        deprecated /renew servlet.

        Args:
            renewal_token: Token sent with the renewal request.

        Returns:
            A tuple containing:
              * A bool representing whether the token is valid and unused.
              * A bool which is `True` if the token is valid, but stale.
              * An int representing the user's expiry timestamp as milliseconds since the
                epoch, or 0 if the token was invalid.
        """
        return await self.renew_account(renewal_token)

    async def on_legacy_send_mail(self, user_id: str):
        """Sends a renewal email to the addresses associated with the given Matrix user
        ID.

        Args:
            user_id: The user ID to send a renewal email for.
        """
        await self.send_renewal_email_to_user(user_id)

    async def on_legacy_admin_request(self, request: Request) -> int:
        """Update the account validity state of a user using the data from the given
        request.

        Args:
            request: The request to extract data from.

        Returns:
            The new expiration timestamp for the updated user.
        """
        return await self.set_account_validity_from_request(request)

    async def is_user_expired(self, user_id: str) -> Optional[bool]:
        """Checks whether a user is expired.

        Args:
            user_id: The user to check the expiration state for.

        Returns:
            A boolean indicating if the user has expired, or None if the module could not
            figure it out (i.e. if the user has no expiration timestamp).
        """
        expiration_ts = await self._store.get_expiration_ts_for_user(user_id)
        if expiration_ts is None:
            return None

        now_ts = int(time.time() * 1000)
        return now_ts >= expiration_ts

    async def on_user_registration(self, user_id: str):
        """Set the expiration timestamp for a newly registered user.

        Args:
            user_id: The ID of the newly registered user to set an expiration date for.
        """
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
