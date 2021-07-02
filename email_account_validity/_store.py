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
import random
import time
from typing import Dict, List, Optional, Tuple, Union

from synapse.module_api import DatabasePool, LoggingTransaction, ModuleApi, cached
from synapse.module_api.errors import SynapseError

logger = logging.getLogger(__name__)

_LONG_TOKEN_COLUMN_NAME = "long_renewal_token"
_SHORT_TOKEN_COLUMN_NAME = "short_renewal_token"


class EmailAccountValidityStore:
    def __init__(self, config: dict, api: ModuleApi):
        self._api = api
        self._period = config.get("period")
        self._renew_at = config.get("renew_at")
        self._expiration_ts_max_delta = self._period * 10.0 / 100.0
        self._rand = random.SystemRandom()

        use_long_tokens = config.get("send_links", True)
        self._token_column_name = (
            _LONG_TOKEN_COLUMN_NAME
            if use_long_tokens
            else _SHORT_TOKEN_COLUMN_NAME
        )

    async def create_and_populate_table(self, populate_users: bool = True):
        """Create the email_account_validity table and populate it from other tables from
        within Synapse. It populates users in it by batches of 100 in order not to clog up
        the database connection with big requests.
        """
        def create_table_txn(txn: LoggingTransaction):
            # Try to create a table for the module.
            txn.execute(
                """
                CREATE TABLE IF NOT EXISTS email_account_validity(
                    user_id TEXT PRIMARY KEY,
                    expiration_ts_ms BIGINT NOT NULL,
                    email_sent BOOLEAN NOT NULL,
                    long_renewal_token TEXT,
                    short_renewal_token TEXT,
                    token_used_ts_ms BIGINT
                )
                """,
                (),
            )

            txn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS long_renewal_token_idx
                    ON email_account_validity(long_renewal_token)
                """,
                (),
            )

            txn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS short_renewal_token_idx
                    ON email_account_validity(short_renewal_token, user_id)
                """,
                (),
            )

        def populate_table_txn(txn: LoggingTransaction, batch_size: int) -> int:
            # Populate the database with the users that are in the users table but not in
            # the email_account_validity one.
            txn.execute(
                """
                SELECT users.name FROM users
                LEFT JOIN email_account_validity
                    ON (users.name = email_account_validity.user_id)
                WHERE email_account_validity.user_id IS NULL
                LIMIT ?
                """,
                (batch_size,),
            )

            missing_users = DatabasePool.cursor_to_dict(txn)
            if not missing_users:
                return 0

            # Figure out the state of these users in the account_validity table.
            # Note that at some point we'll want to get rid of the account_validity table
            # and we'll need to get rid of this code as well.
            rows = DatabasePool.simple_select_many_txn(
                txn=txn,
                table="account_validity",
                column="user_id",
                iterable=tuple([user["name"] for user in missing_users]),
                keyvalues={},
                retcols=(
                    "user_id",
                    "expiration_ts_ms",
                    "email_sent",
                    "renewal_token",
                    "token_used_ts_ms",
                ),
            )

            # Turn the results into a dictionary so we can later merge it with the list
            # of registered users on the homeserver.
            users_to_insert = {}
            for row in rows:
                users_to_insert[row["user_id"]] = row

            # Look for users that are registered but don't have a state in the
            # account_validity table, and set a default state for them. This default
            # state includes an expiration timestamp close to now + validity period, but
            # is slightly randomised to avoid sending huge bursts of renewal emails at
            # once.
            now_ms = int(time.time() * 1000)
            default_expiration_ts = now_ms + self._period
            for user in missing_users:
                if users_to_insert.get(user["name"]) is None:
                    users_to_insert[user["name"]] = {
                        "user_id": user["name"],
                        "expiration_ts_ms": self._rand.randrange(
                            default_expiration_ts - self._expiration_ts_max_delta,
                            default_expiration_ts,
                        ),
                        "email_sent": False,
                        "renewal_token": None,
                        "token_used_ts_ms": None,
                    }

            # Insert the users in the table.
            DatabasePool.simple_insert_many_txn(
                txn=txn,
                table="email_account_validity",
                values=list(users_to_insert.values()),
            )

            return len(missing_users)

        await self._api.run_db_interaction(
            "account_validity_create_table",
            create_table_txn,
        )

        if populate_users:
            batch_size = 100
            processed_rows = 100
            while processed_rows == batch_size:
                processed_rows = await self._api.run_db_interaction(
                    "account_validity_populate_table",
                    populate_table_txn,
                    batch_size,
                )
                logger.info(
                    "Inserted %s users in the email account validity table",
                    processed_rows,
                )

    async def get_users_expiring_soon(self) -> List[Dict[str, Union[str, int]]]:
        """Selects users whose account will expire in the [now, now + renew_at] time
        window (see configuration for account_validity for information on what renew_at
        refers to).

        Returns:
            A list of dictionaries, each with a user ID and expiration time (in
            milliseconds).
        """
        def select_users_txn(txn, renew_at):
            now_ms = int(time.time() * 1000)

            txn.execute(
                """
                SELECT user_id, expiration_ts_ms FROM email_account_validity
                WHERE email_sent = ? AND (expiration_ts_ms - ?) <= ?
                """,
                (False, now_ms, renew_at),
            )
            return DatabasePool.cursor_to_dict(txn)

        return await self._api.run_db_interaction(
            "get_users_expiring_soon",
            select_users_txn,
            self._renew_at,
        )

    async def set_account_validity_for_user(
        self,
        user_id: str,
        expiration_ts: int,
        email_sent: bool,
        renewal_token: Optional[str] = None,
        token_used_ts: Optional[int] = None,
    ):
        """Updates the account validity properties of the given account, with the
        given values.

        Args:
            user_id: ID of the account to update properties for.
            expiration_ts: New expiration date, as a timestamp in milliseconds
                since epoch.
            email_sent: True means a renewal email has been sent for this account
                and there's no need to send another one for the current validity
                period.
            renewal_token: Renewal token the user can use to extend the validity
                of their account. Defaults to no token.
            token_used_ts: A timestamp of when the current token was used to renew
                the account.
        """

        def set_account_validity_for_user_txn(txn: LoggingTransaction):
            txn.execute(
                """
                INSERT INTO email_account_validity (
                    user_id,
                    expiration_ts_ms,
                    email_sent,
                    %(token_column_name)s,
                    token_used_ts_ms
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (user_id) DO UPDATE
                SET
                    expiration_ts_ms = EXCLUDED.expiration_ts_ms,
                    email_sent = EXCLUDED.email_sent,
                    %(token_column_name)s = EXCLUDED.%(token_column_name)s,
                    token_used_ts_ms = EXCLUDED.token_used_ts_ms
                """ % {"token_column_name": self._token_column_name},
                (user_id, expiration_ts, email_sent, renewal_token, token_used_ts)
            )

            txn.call_after(self.get_expiration_ts_for_user.invalidate, (user_id,))

        await self._api.run_db_interaction(
            "set_account_validity_for_user",
            set_account_validity_for_user_txn,
        )

    @cached()
    async def get_expiration_ts_for_user(self, user_id: str) -> int:
        """Get the expiration timestamp for the account bearing a given user ID.

        Args:
            user_id: The ID of the user.
        Returns:
            None, if the account has no expiration timestamp, otherwise int
            representation of the timestamp (as a number of milliseconds since epoch).
        """

        def get_expiration_ts_for_user_txn(txn: LoggingTransaction):
            return DatabasePool.simple_select_one_onecol_txn(
                txn=txn,
                table="email_account_validity",
                keyvalues={"user_id": user_id},
                retcol="expiration_ts_ms",
                allow_none=True,
            )

        res = await self._api.run_db_interaction(
            "get_expiration_ts_for_user",
            get_expiration_ts_for_user_txn,
        )
        return res

    async def set_renewal_token_for_user(
        self,
        user_id: str,
        renewal_token: str,
    ):
        """Store the given renewal token for the given user.

        Args:
            user_id: The user ID to store the renewal token for.
            renewal_token: The renewal token to store for the user.
        """
        def set_renewal_token_for_user_txn(txn: LoggingTransaction):
            # We don't need to check if the token is unique since we've got unique
            # indexes to check that.
            try:
                DatabasePool.simple_update_one_txn(
                    txn=txn,
                    table="email_account_validity",
                    keyvalues={"user_id": user_id},
                    updatevalues={
                        self._token_column_name: renewal_token,
                        "token_used_ts_ms": None,
                    },
                )
            except Exception:
                raise SynapseError(409, "Renewal token already in use")

        await self._api.run_db_interaction(
            "set_renewal_token_for_user",
            set_renewal_token_for_user_txn,
        )

    async def validate_renewal_token(
        self, renewal_token: str, user_id: Optional[str] = None,
    ) -> Tuple[str, int, Optional[int]]:
        """Check if the provided renewal token is associating with a user, optionally
        validating the user it belongs to as well, and return the account renewal status
        of the user it belongs to.

        Args:
            renewal_token: The renewal token to perform the lookup with.
            user_id: The Matrix ID of the user to renew, if the renewal request was
                authenticated.

        Returns:
            A tuple of containing the following values:
                * The ID of a user to which the token belongs.
                * An int representing the user's expiry timestamp as milliseconds since
                    the epoch, or 0 if the token was invalid.
                * An optional int representing the timestamp of when the user renewed
                    their account timestamp as milliseconds since the epoch. None if the
                    account has not been renewed using the current token yet.

        Raises:
            SynapseError(404): The token could not be found (or does not belong to the
                provided user, if any).
        """

        def get_user_from_renewal_token_txn(txn: LoggingTransaction):
            keyvalues = {self._token_column_name: renewal_token}
            if user_id is not None:
                keyvalues["user_id"] = user_id

            return DatabasePool.simple_select_one_txn(
                txn=txn,
                table="email_account_validity",
                keyvalues=keyvalues,
                retcols=["user_id", "expiration_ts_ms", "token_used_ts_ms"],
            )

        res = await self._api.run_db_interaction(
            "get_user_from_renewal_token",
            get_user_from_renewal_token_txn,
        )

        return res["user_id"], res["expiration_ts_ms"], res["token_used_ts_ms"]

    async def set_expiration_date_for_user(self, user_id: str):
        """Sets an expiration date to the account with the given user ID.

        Args:
             user_id: User ID to set an expiration date for.
        """
        await self._api.run_db_interaction(
            "set_expiration_date_for_user",
            self.set_expiration_date_for_user_txn,
            user_id,
        )

    def set_expiration_date_for_user_txn(
        self,
        txn: LoggingTransaction,
        user_id: str,
    ):
        """Sets an expiration date to the account with the given user ID.

        Args:
            user_id: User ID to set an expiration date for.
        """
        now_ms = int(time.time() * 1000)
        expiration_ts = now_ms + self._period

        sql = """
        INSERT INTO email_account_validity (user_id, expiration_ts_ms, email_sent)
        VALUES (?, ?, ?)
        ON CONFLICT (user_id) DO
            UPDATE SET
                expiration_ts_ms = EXCLUDED.expiration_ts_ms,
                email_sent = EXCLUDED.email_sent
        """

        txn.execute(sql, (user_id, expiration_ts, False))

        txn.call_after(self.get_expiration_ts_for_user.invalidate, (user_id,))

    async def set_renewal_mail_status(self, user_id: str, email_sent: bool) -> None:
        """Sets or unsets the flag that indicates whether a renewal email has been sent
        to the user (and the user hasn't renewed their account yet).

        Args:
            user_id: ID of the user to set/unset the flag for.
            email_sent: Flag which indicates whether a renewal email has been sent
                to this user.
        """

        def set_renewal_mail_status_txn(txn: LoggingTransaction):
            DatabasePool.simple_update_one_txn(
                txn=txn,
                table="email_account_validity",
                keyvalues={"user_id": user_id},
                updatevalues={"email_sent": email_sent},
            )

        await self._api.run_db_interaction(
            "set_renewal_mail_status",
            set_renewal_mail_status_txn,
        )

    async def get_renewal_token_for_user(self, user_id: str) -> str:
        """Retrieve the renewal token for the given user.

        Args:
            user_id: Matrix ID of the user to retrieve the renewal token of.

        Returns:
            The renewal token for the user.
        """

        def get_renewal_token_txn(txn: LoggingTransaction):
            return DatabasePool.simple_select_one_onecol_txn(
                txn=txn,
                table="email_account_validity",
                keyvalues={"user_id": user_id},
                retcol=self._token_column_name,
            )

        return await self._api.run_db_interaction(
            "get_renewal_token_for_user",
            get_renewal_token_txn,
        )
