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

import asyncio
import re
import time

# From Python 3.8 onwards, aiounittest.AsyncTestCase can be replaced by
# unittest.IsolatedAsyncioTestCase, so we'll be able to get rid of this dependency when
# we stop supporting Python < 3.8 in Synapse.
import aiounittest

from synapse.module_api.errors import SynapseError

from email_account_validity._utils import UNAUTHENTICATED_TOKEN_REGEX
from tests import create_account_validity_module


class AccountValidityHooksTestCase(aiounittest.AsyncTestCase):
    async def test_user_expired(self):
        user_id = "@izzy:test"
        module = await create_account_validity_module()

        now_ms = int(time.time() * 1000)
        one_hour_ahead = now_ms + 3600000
        one_hour_ago = now_ms - 3600000

        # Test that, if the user isn't known, the module says it can't determine whether
        # they've expired.
        expired, success = await module.user_expired(user_id=user_id)

        self.assertFalse(expired)
        self.assertFalse(success)

        # Test that, if the user has an expiration timestamp that's ahead of now, the
        # module says it can determine that they haven't expired.
        await module.renew_account_for_user(
            user_id=user_id,
            expiration_ts=one_hour_ahead,
        )

        expired, success = await module.user_expired(user_id=user_id)
        self.assertFalse(expired)
        self.assertTrue(success)

        # Test that, if the user has an expiration timestamp that's passed, the module
        # says it can determine that they have expired.
        await module.renew_account_for_user(
            user_id=user_id,
            expiration_ts=one_hour_ago,
        )

        expired, success = await module.user_expired(user_id=user_id)
        self.assertTrue(expired)
        self.assertTrue(success)

    async def test_on_user_registration(self):
        user_id = "@izzy:test"
        module = await create_account_validity_module()

        # Test that the user doesn't have an expiration date in the database. This acts
        # as a safeguard against old databases, and also adds an entry to the cache for
        # get_expiration_ts_for_user so we're sure later in the test that we've correctly
        # invalidated it.
        expiration_ts = await module._store.get_expiration_ts_for_user(user_id)

        self.assertIsNone(expiration_ts)

        # Call the registration hook and test that the user now has an expiration
        # timestamp that's ahead of now.
        await module.on_user_registration(user_id)

        expiration_ts = await module._store.get_expiration_ts_for_user(user_id)
        now_ms = int(time.time() * 1000)

        self.assertIsInstance(expiration_ts, int)
        self.assertGreater(expiration_ts, now_ms)


class AccountValidityEmailTestCase(aiounittest.AsyncTestCase):
    async def test_send_email(self):
        user_id = "@izzy:test"
        module = await create_account_validity_module()

        # Set the side effect of get_threepids_for_user so that it returns a threepid on
        # the first call and an empty list on the second call.

        threepids = [{
            "medium": "email",
            "address": "izzy@test",
        }]

        async def get_threepids(user_id):
            return threepids

        module._api.get_threepids_for_user.side_effect = get_threepids

        # Test that trying to send an email to an unknown user doesn't result in an email
        # being sent.
        try:
            await module.send_renewal_email_to_user(user_id)
        except SynapseError:
            pass

        self.assertEqual(module._api.send_mail.call_count, 0)

        await module._store.set_expiration_date_for_user(user_id)

        # Test that trying to send an email to a known user that has an email address
        # attached to their account results in an email being sent
        await module.send_renewal_email_to_user(user_id)
        self.assertEqual(module._api.send_mail.call_count, 1)

        # Test that the email content contains a link; we haven't set send_links in the
        # module's config so its value should be the default (which is True).
        _, kwargs = module._api.send_mail.call_args
        path = "_synapse/client/email_account_validity/renew"
        self.assertNotEqual(kwargs["html"].find(path), -1)
        self.assertNotEqual(kwargs["text"].find(path), -1)

        # Test that trying to send an email to a known use that has no email address
        # attached to their account results in no email being sent.
        threepids = []
        await module.send_renewal_email_to_user(user_id)
        self.assertEqual(module._api.send_mail.call_count, 1)

    async def test_renewal_token(self):
        user_id = "@izzy:test"
        module = await create_account_validity_module()

        # Insert a row with an expiration timestamp and a renewal token for this user.
        await module._store.set_expiration_date_for_user(user_id)
        await module.generate_renewal_token(user_id)

        # Retrieve the expiration timestamp and renewal token and check that they're in
        # the right format.
        old_expiration_ts = await module._store.get_expiration_ts_for_user(user_id)
        self.assertIsInstance(old_expiration_ts, int)

        renewal_token = await module._store.get_renewal_token_for_user(user_id)
        self.assertIsInstance(renewal_token, str)
        self.assertGreater(len(renewal_token), 0)
        self.assertTrue(UNAUTHENTICATED_TOKEN_REGEX.match(renewal_token))

        # Sleep a bit so the new expiration timestamp isn't likely to be equal to the
        # previous one.
        await asyncio.sleep(0.5)

        # Renew the account once with the token and test that the token is marked as
        # valid and the expiration timestamp has been updated.
        (
            token_valid,
            token_stale,
            new_expiration_ts,
        ) = await module.renew_account(renewal_token)

        self.assertTrue(token_valid)
        self.assertFalse(token_stale)
        self.assertGreater(new_expiration_ts, old_expiration_ts)

        # Renew the account a second time with the same token and test that, this time,
        # the token is marked as stale, and the expiration timestamp hasn't changed.
        (
            token_valid,
            token_stale,
            new_new_expiration_ts,
        ) = await module.renew_account(renewal_token)

        self.assertFalse(token_valid)
        self.assertTrue(token_stale)
        self.assertEqual(new_expiration_ts, new_new_expiration_ts)

        # Test that a fake token is marked as neither valid nor stale.
        (
            token_valid,
            token_stale,
            expiration_ts,
        ) = await module.renew_account("fake_token")

        self.assertFalse(token_valid)
        self.assertFalse(token_stale)
        self.assertEqual(expiration_ts, 0)

    async def test_duplicate_token(self):
        user_id_1 = "@izzy1:test"
        user_id_2 = "@izzy2:test"
        token = "sometoken"

        module = await create_account_validity_module()

        # Insert both users in the table.
        await module._store.set_expiration_date_for_user(user_id_1)
        await module._store.set_expiration_date_for_user(user_id_2)

        # Set the renewal token.
        await module._store.set_renewal_token_for_user(user_id_1, token, True)

        # Try to set the same renewal token for another user.
        exception = None
        try:
            await module._store.set_renewal_token_for_user(user_id_2, token, True)
        except SynapseError as e:
            exception = e

        # Check that an exception was raised and that it's the one we're expecting.
        self.assertIsInstance(exception, SynapseError)
        self.assertEqual(exception.code, 409)

    async def test_send_link_false(self):
        user_id = "@izzy:test"
        # Create a module with a configuration forbidding it to send links via email.
        module = await create_account_validity_module({"send_links": False})

        async def get_threepids(user_id):
            return [{
                "medium": "email",
                "address": "izzy@test",
            }]
        module._api.get_threepids_for_user.side_effect = get_threepids
        await module._store.set_expiration_date_for_user(user_id)

        # Test that, when an email is sent, it doesn't include a link. We do this by
        # searching the email's content for the path for renewal requests.
        await module.send_renewal_email_to_user(user_id)
        self.assertEqual(module._api.send_mail.call_count, 1)

        _, kwargs = module._api.send_mail.call_args
        path = "_synapse/client/email_account_validity/renew"
        self.assertEqual(kwargs["html"].find(path), -1)
        self.assertEqual(kwargs["text"].find(path), -1)

        # Check that the renewal token is in the right format. It should be a 8 digit
        # long string.
        token = await module._store.get_renewal_token_for_user(user_id)
        self.assertTrue(isinstance(token, str))
        self.assertTrue(re.compile("^[0-9]{8}$").match(token))
