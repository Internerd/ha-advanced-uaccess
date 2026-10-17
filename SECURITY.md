# Security Policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please **do not** open a public issue for security problems.
Use GitHub's private vulnerability reporting instead:
*Security → Report a vulnerability* in this repository.

Please include the version of the integration, of Home Assistant and of UniFi Access, and steps to reproduce.

## Notes for users

- Use a dedicated **local** UniFi OS account for Home Assistant with only the permissions it needs.
- Credentials are stored by Home Assistant in its config entry storage (`.storage/core.config_entries`).
  Protect your Home Assistant backups accordingly.
- Diagnostics downloads redact credentials, IPs, MACs, names, e-mail addresses and phone numbers.
  Still review them before posting them publicly.
