# Security Policy

## Supported versions

This project follows a rolling release model. Security fixes are applied to the
latest released version published on
[PyPI](https://pypi.org/project/bitbucket-mcp-server-datacenter/). Please make
sure you are running the most recent version before reporting an issue.

## Reporting a vulnerability

**Do not report security vulnerabilities through public GitHub issues.**

Instead, use GitHub's
[private vulnerability reporting](https://github.com/telekom-mms/bitbucket-mcp-server-datacenter/security/advisories/new)
to disclose the issue privately to the maintainers.

Please include:

- a description of the vulnerability and its impact,
- steps to reproduce or a proof of concept,
- affected version(s), and
- any suggested mitigation, if known.

We aim to acknowledge reports within a few business days and will keep you
informed about the progress towards a fix and disclosure.

## Handling secrets

The Bitbucket HTTP access token is the only long-lived secret used by this
server. It must be treated as a technical-account credential:

- **Never commit the token.** It is provided only via the `BITBUCKET_TOKEN`
  environment variable (or a masked prompt input in VS Code) and is kept out of
  logs, URLs, and shell history.
- **Transport is always over TLS.** TLS verification is enforced and cannot be
  disabled; to trust an internal CA, set `BITBUCKET_CA_BUNDLE` instead.
- **Least privilege.** Scope the token to match `ENABLE_TOOLS` (read-only by
  default).
- **Rotation and revocation.** Rotate the token regularly and immediately if it
  may have been exposed; revoke it as soon as it is no longer needed.

If a token ever appears in plain text (chat, logs, screen sharing), treat it as
compromised and rotate it right away.
