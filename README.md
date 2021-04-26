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
      "/_synapse/client/account_validity":
        module: email_account_validity.EmailAccountValidityServlet
        config:
          # The maximum amount of time an account can stay valid for without being
          # renewed.
          period: 6w
```

The syntax for durations is the same as in the rest of Synapse's configuration file.