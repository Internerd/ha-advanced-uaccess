<p align="center">
  <img src="brand/icon@2x.png" alt="Advanced UniFi Access Directory" width="128">
</p>

# Advanced UniFi Access Directory

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/internerd/ha-advanced-uaccess/actions/workflows/validate.yml/badge.svg)](https://github.com/internerd/ha-advanced-uaccess/actions/workflows/validate.yml)
[![Tests](https://github.com/internerd/ha-advanced-uaccess/actions/workflows/tests.yml/badge.svg)](https://github.com/internerd/ha-advanced-uaccess/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

🇩🇪 [Deutsche Anleitung](README.de.md)

Home Assistant custom integration that manages the **intercom directory of UniFi Access**:
who rings when a visitor calls a room (or presses the doorbell) on a UniFi intercom.

Add or remove viewers, chimes, users and phone numbers per room, create and delete rooms,
and set call schedules — from the Home Assistant UI, from automations or from scripts.

The integration talks **directly and locally** to your UniFi OS console (UDM, UNVR, UCG, …)
over HTTPS. No cloud, no UI.com account.

> [!WARNING]
> This integration uses the **internal, undocumented API** of the UniFi Access web UI.
> Ubiquiti can change it with any Access update without notice. Changes are written
> straight into your Access configuration. Use at your own risk — see [Disclaimer](#disclaimer).

---

## Features

| | |
|---|---|
| 🔀 **Switch per room × receiver** | `on` = the receiver rings for that room. Turning it on/off changes Access. |
| 🔔 **Doorbell direct call** | The receivers of the doorbell button (“Doorbell call”) are switches too. |
| 🏠 **Rooms** | Create and delete directory rooms with services. |
| 🗓️ **Schedules** | Set a custom weekly schedule per receiver, or reset it to “Always”. |
| ♻️ **Schedule restore** | Access deletes a receiver's private schedule when it is removed. The integration saves it and restores it when the receiver is added again. |
| 🔍 **Auto discovery** | All intercoms, rooms, viewers, chimes, users and phone numbers are discovered; new rooms appear automatically. |
| 🌍 **Translations** | English and German. |
| 🩺 **Diagnostics** | Download redacted diagnostics including the raw directory graph. |

Supported receiver types:

| Type | Source of the list | Id used by Access |
|---|---|---|
| Viewer (UA Intercom Viewer) | `/devices/topology4` | `unique_id` |
| Chime (UniFi Protect chime) | `/protect_devices` | MAC without colons, upper case |
| User (“Admin” receiver, UniFi Access app) | `ulp-go /users/search` | `unique_id` |
| Phone number | `/callers/phone_number` | `unique_id` |
| Third-party SIP / viewer | only if already assigned | `unique_id` |

## Requirements

- Home Assistant **2025.3** or newer
- UniFi OS console with **UniFi Access** and at least one intercom (tested: UNVR, Access 3.14.4556, UI 4.3.12, Intercom G2 firmware 1.11.23)
- A **local UniFi OS account** (not a UI.com cloud account) **without MFA** that can manage UniFi Access

### Why a local account and not an API key?

UniFi offers two kinds of API keys, and neither of them can change the intercom directory:

- The **Access developer API** (API token created in UniFi Access, port 12445) covers doors, users and visitors, but not the intercom directory.
- **UniFi OS API keys** only cover the official integration APIs.

The directory endpoints exist only in the internal web UI API (`/proxy/access/api/v2`). That API is authenticated with a UniFi OS login session, cookie plus CSRF token. The UniFi Protect integration in Home Assistant core uses a local account for the same reason.

Recommended setup:

1. In UniFi OS → *Admins & Users* create a **dedicated local admin** for Home Assistant, e.g. `homeassistant`.
2. Restrict the account to *Local access only* and give it permission to manage **UniFi Access**.
   Other apps can be set to *None* or *View only*.
3. Do **not** enable MFA for this account.

> Which minimal role is enough has not been tested yet. If an account with only Access
> permissions fails, please open an issue.

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/internerd/ha-advanced-uaccess`, category **Integration**.
2. Search for **Advanced UniFi Access Directory** and download it.
3. Restart Home Assistant.

[![Open your Home Assistant instance and open a repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=internerd&repository=ha-advanced-uaccess&category=integration)

### Manual

Copy `custom_components/advanced_uaccess` into the `custom_components` folder of your
Home Assistant configuration and restart.

## Configuration

*Settings → Devices & services → Add integration → Advanced UniFi Access Directory*

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=advanced_uaccess)

| Field | Description |
|---|---|
| Host | IP or hostname of the console, e.g. `192.168.1.1` |
| Username / Password | The local account |
| Verify SSL certificate | Off by default (consoles use a self-signed certificate) |

Re-authentication and *Reconfigure* are supported.

### Options

| Option | Default | Description |
|---|---|---|
| Polling interval | 30 s | How often the directory is read |
| Restore schedules | on | Save a private schedule on removal and restore it on re-add |
| Users offered as receivers | users that already receive calls | One switch per room is created for each selected user |

## Entities

For every intercom, the integration creates:

- a device for the **intercom**, with the sensor *Rooms*. Its attributes list every room with its `room_id`.
- a device for the **doorbell call**, i.e. the receivers that ring when the doorbell button is pressed.
- a device for every **room**.

The doorbell call device and every room device have:

- a **switch per receiver**: every viewer, chime and phone number, plus the selected users.
- a sensor **Receivers**: the number of receivers. Its attributes list them with their schedules.

Switch attributes: `caller_id`, `room_id` (`direct` for the doorbell call), `receiver_type`,
`receiver_id`, `schedule` (`always` or the schedule name) and `schedule_is_private`.

## Services

All receiver services accept **either** `entity_id` of a receiver switch **or**
`caller_id` + `room_id` + `receiver_type` + `receiver_id`.
`caller_id` can be omitted when only one intercom exists. `room_id: direct` targets the doorbell call.

| Service | Purpose |
|---|---|
| `advanced_uaccess.add_receiver` | Add a receiver to a room or the doorbell call |
| `advanced_uaccess.remove_receiver` | Remove a receiver |
| `advanced_uaccess.set_schedule` | Set a custom weekly schedule for an assigned receiver |
| `advanced_uaccess.clear_schedule` | Reset a receiver's schedule to “Always” |
| `advanced_uaccess.create_room` | Create a room (returns `room_id`) |
| `advanced_uaccess.delete_room` | Delete a room |

### Examples

Front-door viewer stops ringing for a room while nobody is home:

```yaml
automation:
  - alias: "Intercom: viewer off when away"
    triggers:
      - trigger: state
        entity_id: zone.home
        to: "0"
    actions:
      - action: switch.turn_off
        target:
          entity_id: switch.1_alice_front_door
```

Add a chime by MAC (colons are accepted):

```yaml
action: advanced_uaccess.add_receiver
data:
  room_id: 566126a3-2809-4993-9625-b1bfb6ebd6e0
  receiver_type: chime
  receiver_id: "E4:38:83:35:84:49"
```

Office hours for a viewer:

```yaml
action: advanced_uaccess.set_schedule
data:
  entity_id: switch.1_alice_front_door
  schedule:
    monday: ["08:00-12:00", "13:00-17:00"]
    tuesday: ["08:00-17:00"]
    friday: [{ start: "08:00", end: "12:00" }]
```

Days that are missing have no time slot. `sunday` … `saturday` are accepted. A slot ends at
`hh:mm:59`, like in the Access UI.

Create a guest room and keep the id:

```yaml
action: advanced_uaccess.create_room
data:
  name: "3 Guest"
  number: "999"
  viewers: ["0cea1417bf7d"]
  chimes: ["E43883358449"]
response_variable: room
```

## How it works

| Action | Request |
|---|---|
| Read state | `GET /callers/{caller}/topology?flow=true&support_delivery=true` |
| Add receiver to a room | fresh topology → full list + new id → `POST /callers/{caller}/rooms/{room}` |
| Add receiver to the doorbell call | fresh topology → full list + new id → `PUT /callers/rooms/{caller}` |
| Remove receiver | `DELETE /callers/receivers/{id}?caller_id&room_id&receiver_id&receiver_type`, with the list replace as fallback |
| Schedules | `PUT /callers/{caller}/schedules`, `DELETE /callers/{caller}/receivers/{id}/schedules` |
| Rooms | `POST /callers/{caller}/rooms/receivers`, `DELETE /callers/{caller}/rooms/{room}` |

The full API notes are in [docs/API.md](docs/API.md).

## Known limitations

- **Escalation tiers**: Access lets you add several call groups per room (“Add call group”). This integration reads and writes **one** group. Adding a receiver to a room that has several groups merges them into one group.
- **Delivery call mode** is not supported.
- **Third-party SIP and third-party viewers** get switches only while they are assigned. Once removed, they are not offered again.
- **Schedule restore**: the response format of `GET /schedule/{id}` has not been recorded yet. The integration searches the response for `week_schedule`. If it cannot find it, a warning is logged and the schedule is not restored.
- Schedules of the doorbell call are sent without `room_id`. This has not been tested yet.
- Room names and numbers cannot be changed yet.

Please report changes of the Access API with a [diagnostics download](#troubleshooting).

## Troubleshooting

- **invalid_auth**: the account is a cloud account, has MFA enabled, or the password is wrong.
- **no_access**: the login worked, but `/proxy/access` answered with an error. Is Access installed, and does the account have Access permissions?
- Debug logging:

  ```yaml
  logger:
    logs:
      custom_components.advanced_uaccess: debug
  ```

- *Settings → Devices & services → Advanced UniFi Access Directory → ⋮ → Download diagnostics*.
  Credentials, IPs, MACs, names, e-mail addresses and phone numbers are redacted.

## Privacy

All communication stays between Home Assistant and your console. The integration sends no
data to third parties and contains no telemetry. Saved schedules are stored locally in
`.storage/advanced_uaccess.<entry_id>`.

## Disclaimer

This project is **not affiliated with, endorsed by, or sponsored by Ubiquiti Inc.**
“UniFi”, “UniFi Access”, “UniFi Protect” and “Ubiquiti” are trademarks of Ubiquiti Inc.
They are used here only to describe compatibility.

The integration uses an undocumented interface that can change at any time. The software is
provided “as is”, without warranty of any kind (see [LICENSE](LICENSE)). You are responsible
for your access control and intercom configuration. Do not rely on this integration for
safety-critical functions. See [DISCLAIMER.md](DISCLAIMER.md).

## Contributing

Issues and pull requests are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md).
Security issues: see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © 2026 Internerd
