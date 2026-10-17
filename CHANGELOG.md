# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-17

### Added

- First release.
- Local connection to the UniFi OS console with a local account, plus reauth and reconfigure.
- Switch per room × receiver (viewer, chime, user, phone number) and for the doorbell direct call.
- Sensors for the number of receivers per room and the number of rooms per intercom.
- Services `add_receiver`, `remove_receiver`, `create_room`, `delete_room`, `set_schedule` and `clear_schedule`.
- Backup and restore of private receiver schedules.
- Options for the polling interval, schedule restore and the users offered as receivers.
- Diagnostics with redaction.
- English and German translations.
