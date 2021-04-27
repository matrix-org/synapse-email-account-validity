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
```

The syntax for durations is the same as in the rest of Synapse's configuration file.

If they are not already there, copy the [templates](/email_account_validity/templates)
into Synapse's templates directory.

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