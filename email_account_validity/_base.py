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
from typing import Any, Optional, Tuple

from twisted.web.server import Request

from synapse.api.errors import StoreError, SynapseError
from synapse.http.servlet import parse_json_object_from_request
from synapse.module_api import ModuleApi
from synapse.types import UserID
from synapse.util import stringutils

from email_account_validity._store import EmailAccountValidityStore

logger = logging.getLogger(__name__)


class EmailAccountValidityBase:
    def __init__(self, config: Any, api: ModuleApi, store: EmailAccountValidityStore):
        self._api = api
        self._store = store

        self._period = config.get("period")

        (self._template_html, self._template_text,) = api.read_templates(
            ["notice_expiry.html", "notice_expiry.txt"],
        )

        if "renew_email_subject" in config:
            renew_email_subject = config["renew_email_subject"]
        else:
            renew_email_subject = "Renew your %(app)s account"

        try:
            app_name = self._api.email_app_name
            self._renew_email_subject = renew_email_subject % {"app": app_name}
        except Exception:
            # If substitution failed, fall back to the bare strings.
            self._renew_email_subject = renew_email_subject

    async def send_renewal_email_to_user(self, user_id: str) -> None:
        """
        Send a renewal email for a specific user.

        Args:
            user_id: The user ID to send a renewal email for.

        Raises:
            SynapseError if the user is not set to renew.
        """
        expiration_ts = await self._store.get_expiration_ts_for_user(user_id)

        # If this user isn't set to be expired, raise an error.
        if expiration_ts is None:
            raise SynapseError(400, "User has no expiration time: %s" % (user_id,))

        await self.send_renewal_email(user_id, expiration_ts)

    async def send_renewal_email(self, user_id: str, expiration_ts: int):
        """Sends out a renewal email to every email address attached to the given user
        with a unique link allowing them to renew their account.

        Args:
            user_id: ID of the user to send email(s) to.
            expiration_ts: Timestamp in milliseconds for the expiration date of
                this user's account (used in the email templates).
        """
        threepids = await self._api.get_threepids_for_user(user_id)

        addresses = []
        for threepid in threepids:
            if threepid["medium"] == "email":
                addresses.append(threepid["address"])

        # Stop right here if the user doesn't have at least one email address.
        # In this case, they will have to ask their server admin to renew their account
        # manually.
        # We don't need to do a specific check to make sure the account isn't
        # deactivated, as a deactivated account isn't supposed to have any email address
        # attached to it.
        if not addresses:
            return

        try:
            profile = await self._api.get_profile_for_user(
                UserID.from_string(user_id).localpart
            )
            display_name = profile.display_name
            if display_name is None:
                display_name = user_id
        except StoreError:
            display_name = user_id

        renewal_token = await self.generate_renewal_token(user_id)

        url = "%s_synapse/client/email_account_validity/renew?token=%s" % (
            self._api.public_baseurl,
            renewal_token,
        )

        template_vars = {
            "display_name": display_name,
            "expiration_ts": expiration_ts,
            "url": url,
        }

        html_text = self._template_html.render(**template_vars)
        plain_text = self._template_text.render(**template_vars)

        for address in addresses:
            await self._api.send_mail(
                address,
                self._renew_email_subject,
                html_text,
                plain_text,
            )

        await self._store.set_renewal_mail_status(user_id=user_id, email_sent=True)

    async def generate_renewal_token(self, user_id: str) -> str:
        """Generates a 32-byte long random string that will be inserted into the
        user's renewal email's unique link, then saves it into the database.

        Args:
            user_id: ID of the user to generate a string for.

        Returns:
            The generated string.

        Raises:
            StoreError(500): Couldn't generate a unique string after 5 attempts.
        """
        attempts = 0
        while attempts < 5:
            try:
                renewal_token = stringutils.random_string(32)
                await self._store.set_renewal_token_for_user(user_id, renewal_token)
                return renewal_token
            except StoreError:
                attempts += 1
        raise StoreError(500, "Couldn't generate a unique string as refresh string.")

    async def renew_account(self, renewal_token: str) -> Tuple[bool, bool, int]:
        """Renews the account attached to a given renewal token by pushing back the
        expiration date by the current validity period in the server's configuration.

        If it turns out that the token is valid but has already been used, then the
        token is considered stale. A token is stale if the 'token_used_ts_ms' db column
        is non-null.

        Args:
            renewal_token: Token sent with the renewal request.
        Returns:
            A tuple containing:
              * A bool representing whether the token is valid and unused.
              * A bool which is `True` if the token is valid, but stale.
              * An int representing the user's expiry timestamp as milliseconds since the
                epoch, or 0 if the token was invalid.
        """
        if self._period is None:
            # If a period hasn't been provided in the config, then it means this function
            # was called from a place it shouldn't have been, e.g. the /send_mail servlet.
            raise SynapseError(500, "Tried to renew account in unexpected place")

        try:
            (
                user_id,
                current_expiration_ts,
                token_used_ts,
            ) = await self._store.get_user_from_renewal_token(renewal_token)
        except StoreError:
            return False, False, 0

        # Check whether this token has already been used.
        if token_used_ts:
            logger.info(
                "User '%s' attempted to use previously used token '%s' to renew account",
                user_id,
                renewal_token,
            )
            return False, True, current_expiration_ts

        logger.debug("Renewing an account for user %s", user_id)

        # Renew the account. Pass the renewal_token here so that it is not cleared.
        # We want to keep the token around in case the user attempts to renew their
        # account with the same token twice (clicking the email link twice).
        #
        # In that case, the token will be accepted, but the account's expiration ts
        # will remain unchanged.
        new_expiration_ts = await self.renew_account_for_user(
            user_id, renewal_token=renewal_token
        )

        return True, False, new_expiration_ts

    async def renew_account_for_user(
        self,
        user_id: str,
        expiration_ts: Optional[int] = None,
        email_sent: bool = False,
        renewal_token: Optional[str] = None,
    ) -> int:
        """Renews the account attached to a given user by pushing back the
        expiration date by the current validity period in the server's
        configuration.

        Args:
            user_id: The ID of the user to renew.
            expiration_ts: New expiration date. Defaults to now + validity period.
            email_sent: Whether an email has been sent for this validity period.
            renewal_token: Token sent with the renewal request. The user's token
                will be cleared if this is None.

        Returns:
            New expiration date for this account, as a timestamp in
            milliseconds since epoch.
        """
        now = self._api.current_time_ms()
        if expiration_ts is None:
            expiration_ts = now + self._period

        await self._store.set_account_validity_for_user(
            user_id=user_id,
            expiration_ts=expiration_ts,
            email_sent=email_sent,
            renewal_token=renewal_token,
            token_used_ts=now,
        )

        return expiration_ts

    async def set_account_validity_from_request(self, request: Request) -> int:
        """Set the account validity state of a user from a request's body. The body is
        expected to match the format for admin requests.

        Args:
            request: The request to extract data from.

        Returns:
            The new expiration timestamp for the updated user.
        """
        body = parse_json_object_from_request(request)

        if "user_id" not in body:
            raise SynapseError(400, "Missing property 'user_id' in the request body")

        return await self.renew_account_for_user(
            body["user_id"],
            body.get("expiration_ts"),
            not body.get("enable_renewal_emails", True),
        )
