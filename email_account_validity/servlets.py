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

from twisted.web.resource import Resource

from synapse.config._base import Config, ConfigError
from synapse.http.server import (
    DirectServeHtmlResource,
    DirectServeJsonResource,
    respond_with_html,
)
from synapse.module_api import ModuleApi
from synapse.module_api.errors import SynapseError

from email_account_validity._base import EmailAccountValidityBase
from email_account_validity._store import EmailAccountValidityStore


class EmailAccountValidityServlet(Resource):
    def __init__(self, config: dict, api: ModuleApi):
        super().__init__()
        self.putChild(b'renew', EmailAccountValidityRenewServlet(config, api))
        self.putChild(b'send_mail', EmailAccountValiditySendMailServlet(config, api))
        self.putChild(b'admin', EmailAccountValidityAdminServlet(config, api))

    @staticmethod
    def parse_config(config: dict) -> dict:
        config["period"] = Config.parse_duration(config.get("period") or 0)
        return config


class EmailAccountValidityRenewServlet(
    EmailAccountValidityBase, DirectServeHtmlResource
):
    def __init__(self, config: dict, api: ModuleApi):
        self._api = api
        self._store = EmailAccountValidityStore(config, api)

        EmailAccountValidityBase.__init__(self, config, self._api, self._store)
        DirectServeHtmlResource.__init__(self)

        if "period" in config:
            self._period = Config.parse_duration(config["period"])
        else:
            raise ConfigError("'period' is required when using account validity")

        (
            self._account_renewed_template,
            self._account_previously_renewed_template,
            self._invalid_token_template,
        ) = api.read_templates(
            [
                "account_renewed.html",
                "account_previously_renewed.html",
                "invalid_token.html",
            ]
        )

    async def _async_render_GET(self, request):
        """On GET requests on /renew, retrieve the given renewal token from the request
        query parameters and, if it matches with an account, renew the account.
        """
        if b"token" not in request.args:
            raise SynapseError(400, "Missing renewal token")

        renewal_token = request.args[b"token"][0].decode("utf-8")

        (
            token_valid,
            token_stale,
            expiration_ts,
        ) = await self.renew_account(renewal_token)

        if token_valid:
            status_code = 200
            response = self._account_renewed_template.render(
                expiration_ts=expiration_ts
            )
        elif token_stale:
            status_code = 200
            response = self._account_previously_renewed_template.render(
                expiration_ts=expiration_ts
            )
        else:
            status_code = 404
            response = self._invalid_token_template.render()

        respond_with_html(request, status_code, response)


class EmailAccountValiditySendMailServlet(
    EmailAccountValidityBase,
    DirectServeJsonResource,
):
    def __init__(self, config: dict, api: ModuleApi):
        store = EmailAccountValidityStore(config, api)

        EmailAccountValidityBase.__init__(self, config, api, store)
        DirectServeJsonResource.__init__(self)

        if not api.public_baseurl:
            raise ConfigError("Can't send renewal emails without 'public_baseurl'")

    async def _async_render_POST(self, request):
        """On POST requests on /send_mail, send a renewal email to the account the access
        token authenticating the request belongs to.
        """
        requester = await self._api.get_user_by_req(request, allow_expired=True)
        user_id = requester.user.to_string()
        await self.send_renewal_email_to_user(user_id)

        return 200, {}


class EmailAccountValidityAdminServlet(
    EmailAccountValidityBase,
    DirectServeJsonResource,
):
    def __init__(self, config: dict, api: ModuleApi):
        store = EmailAccountValidityStore(config, api)

        EmailAccountValidityBase.__init__(self, config, api, store)
        DirectServeJsonResource.__init__(self)

    async def _async_render_POST(self, request):
        """On POST requests on /admin, update the given user with the given account
        validity state, if the requester is a server admin.
        """
        requester = await self._api.get_user_by_req(request)
        if not await self._api.is_user_admin(requester.user.to_string()):
            raise SynapseError(403, "You are not a server admin", "M_FORBIDDEN")

        expiration_ts = await self.set_account_validity_from_request(request)

        res = {"expiration_ts": expiration_ts}
        return 200, res
