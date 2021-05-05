# Synapse email account validity

A Synapse plugin module to manage account validity using validation emails.

This module requires:

* Synapse >= 1.34.0
* sqlite3 >= 3.24.0 (if using SQLite with Synapse)

## Installation

Thi plugin can be installed via PyPI:

```
pip install synapse-email-account-validity
```

## Config

Add the following in your Synapse config under `account_validity`:

```yaml
  - module: email_account_validity.EmailAccountValidity
    config:
      # The maximum amount of time an account can stay valid for without being renewed.
      period: 6w
      # How long before an account expires should Synapse send it a renewal email.
      renew_at: 1w
      # Whether to include a link to click in the emails sent to users. If false, only a
      # renewal token is sent, in which case it is generated so it's simpler, and the
      # user will need to copy it into a compatible client that will send an
      # authenticated request to the server.
      # Defaults to true.
      send_links: true
```

Also under the HTTP client `listener`, configure an `additional_resource` as per below:

```yaml
    additional_resources:
      "/_synapse/client/email_account_validity":
        module: email_account_validity.EmailAccountValidityServlet
        config:
          # The maximum amount of time an account can stay valid for without being
          # renewed.
          period: 6w
          # Whether to include a link to click in the emails sent to users. If false,
          # only a renewal token is sent, in which case it is generated so it's simpler,
          # and the user will need to copy it into a compatible client that will send an
          # authenticated request to the server.
          # Defaults to true.
          send_links: true
```

The syntax for durations is the same as in the rest of Synapse's configuration file.

Configuration parameters with matching names that appear both in `account_validity` and
`listeners` __must__ have the same value in both places, otherwise the module will not
behave correctly.

## Templates

If they are not already there, copy the [templates](/email_account_validity/templates)
into Synapse's templates directory (or replace them with your own). The templates the
module will use are:

* `notice_expiry.(html|txt)`: The content of the renewal email. It gets passed the
  following variables:
    * `display_name`: The display name of the user needing renewal.
    * `expiration_ts`: A timestamp in milliseconds representing when the account will
      expire. Templates can use the `format_ts` (with a date format as the function's
      parameter) to format this timestamp into a human-readable date.
    * `url`: The URL the user is supposed to click on to renew their account. If
      `send_links` is set to `false` in the module's configuration, the value of this
      variable will be the token the user must copy into their client.
    * `renewal_token`: The token to use in order to renew the user's account. If
      `send_links` is set to `false`, templates should prefer this variable to `url`.
* `account_renewed.html`: The HTML to display to a user when they successfully renew
  their account. It gets passed the following vaiables:
    * `expiration_ts`: A timestamp in milliseconds representing when the account will
      expire. Templates can use the `format_ts` (with a date format as the function's
      parameter) to format this timestamp into a human-readable date.
* `account_previously_renewed.html`: The HTML to display to a user when they try to renew
  their account with a token that's valid but previously used. It gets passed the same
  variables as `account_renewed.html`.
* `invalid_token.html`: The HTML to display to a user when they try to renew their account
  with the wrong token. It doesn't get passed any variable.

Note that the templates directory contains two files that aren't templates (`mail.css`
and `mail-expiry.css`), but are used by email templates to apply visual adjustments.

## Routes

This plugin exposes three HTTP routes to manage account validity:

* `POST /_synapse/client/email_account_validity/send_mail`, which any registered user can
  hit with an access token to request a renewal email to be sent to their email addresses.
* `GET /_synapse/client/email_account_validity/renew`, which requires a `token` query
  parameter containing the latest token sent via email to the user, which renews the
  account associated with the token.
* `POST /_synapse/client/email_account_validity/admin`, which any server admin can use to
  manage the account validity of any registered user. It takes a JSON body with the
  following keys:
    * `user_id` (string, required): The Matrix ID of the user to update.
    * `expiration_ts` (integer, optional): The new expiration timestamp for this user, in
      milliseconds. If no token is provided, a value corresponding to `now + period` is
      used.
    * `enable_renewal_emails` (boolean, optional): Whether to allow renewal emails to be
      sent to this user.

The two first routes need to be reachable by the end users for this feature to work as
intended.

## Development and Testing

This repository uses `tox` to run tests.

### Tests

This repository uses `unittest` to run the tests located in the `tests`
directory. They can be ran with `tox -e tests`.

### Making a release

```
git tag vX.Y
python3 setup.py sdist
twine upload dist/synapse-email-account-validity-X.Y.tar.gz
git push origin vX.Y
```