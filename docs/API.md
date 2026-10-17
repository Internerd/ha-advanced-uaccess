# UniFi Access – intercom directory (internal API)

These notes come from recordings of the Access web UI and from its source code.
Recorded on 2026-10-17: UNVR, Access app **v3.14.4556**, UniFi OS UI 4.3.12, Intercom G2 firmware v1.11.23.0.

This is the **internal, undocumented** API used by the web UI. It can change with any update.
Each item is marked by how it is known:

- ✅ recorded
- 🧩 taken from the UI source code
- ❔ assumption

## Transport and authentication

The integration sends HTTPS requests directly to the console:

1. `POST https://<console>/api/auth/login` with `{"username", "password", "rememberMe": true}`.
   The response sets the `TOKEN` cookie and the `X-CSRF-Token` header (UniFi OS standard).
2. Every following request sends the cookie and the `X-CSRF-Token` header, which is mandatory for POST, PUT and DELETE.
   If a response carries `X-Updated-CSRF-Token`, that token replaces the old one.
3. On HTTP 401 or 403 the integration logs in again once.
4. Use a local account without MFA. A cloud (UI.com) account cannot log in locally.

Over unifi.ui.com the same paths are tunnelled through a WebRTC data channel. That transport does not matter for a local client.

**Envelope.** Responses look like this:

```json
{"code": 1, "codeS": "SUCCESS", "msg": "success", "error": "", "data": …}
```

The HTTP status is 200 even for errors, so the client checks `code == 1`.
Example error: `170014 CODE_ACCESS_POLICY_SCHEDULE_NOT_FOUND`.
The `ulp-go` user API has no `codeS` field.

**Base path:** `/proxy/access/api/v2`

## Receiver ids

| Receiver | Id in `receiver_groups` | Name | Source |
|---|---|---|---|
| Viewer | `unique_id`, e.g. `0cea1417bf7d` | `alias` | `GET /devices/topology4` → `data[].device_groups[][]`, where `device_type` is `UA-Int-Viewer` ✅ |
| Chime | MAC without colons, upper case, e.g. `E43883358449`. Not `uniqueId`. | `name` | `GET /protect_devices?include_adopted_by_access=true` → `data.chimes[]` ✅ |
| User ("admin") | `unique_id` | `full_name` | `GET /proxy/access/ulp-go/api/v2/users/search?page_num=1&page_size=…&status=ACTIVE` ✅ |
| Phone number | `unique_id`, not `id` | `area_code` + `phone_number` | `GET /callers/phone_number` ✅ |
| Third-party SIP / viewer | `extra.unique_id` | – | `GET /third_party_sips/extensions`, `GET /callers/{caller_id}/third_party_sips` 🧩 |

## Reading

| Purpose | Request |
|---|---|
| All intercoms | `GET /callers`. In the response array, `unique_id` is the caller id. ✅ |
| Caller config | `GET /callers/{caller_id}` → `rooms[]`, `receivers` (doorbell direct call), `receiver_groups`, `delivery_mode` ✅ |
| Rooms only | `GET /callers/{caller_id}/rooms` ✅ |
| **Full state** | `GET /callers/{caller_id}/topology?flow=true&support_delivery=true` ✅ |
| Schedule | `GET /schedule/{schedule_id}` (response format not recorded) ❔ |

### Topology

The topology is a graph with `vertices[]` and `edges[]`. Each edge points from `uplink_id` to `downlink_id`.

```json
{"node_id": "c0f8…", "type": "caller",
 "extra": {"device_id": "28704e31e640", "disable_directory": false, "name": "UA Intercom e640", "type": "UA-Intercom"}}
{"node_id": "907e…", "type": "directory",
 "extra": {"name": "1 Room A", "number_check": true, "room": "255", "room_id": "566126a3-…"}}
{"node_id": "bcdb…", "type": "viewer", "name": "UA Intercom Viewer bf7d",
 "extra": {"device_id": "0cea1417bf7d", "display_name": "…", "schedule_id": "296f…",
           "schedule_is_private": true, "schedule_name": "Custom", "avatar": ""}}
{"node_id": "ce1a…", "type": "admin",
 "extra": {"id": "cd4991c9-…", "name": "…", "email": "…", "schedule_id": "", "schedule_is_private": false, "schedule_name": ""}}
```

- A `directory` node is a room. Its receivers hang below it.
- Receivers that hang **directly below the `caller` node** belong to the **doorbell direct call**.
- The same device appears once per place where it is assigned, each time with its own `node_id`.
- Values of `vertex.type`:
  - in a real installation ✅: `caller`, `directory`, `viewer`, `chime`, `admin`
  - from the UI source 🧩: `phone_number`, `third_party_sip`, `third_party_viewer`, `delivery`, `template`
- The receiver id is:
  - `extra.device_id` for viewer and chime ✅
  - `extra.id` for admin ✅
  - `extra.unique_id` for the other types 🧩

## Writing

The key inside a `receiver_groups` element depends on the vertex type:

| Vertex type | Key |
|---|---|
| `viewer` | `viewers` |
| `admin` | `admins` |
| `chime` | `chimes` |
| `phone_number` | `phone_numbers` |
| `third_party_sip` | `third_party_sips` |
| `third_party_viewer` | `third_party_viewers` |

### Create a room ✅

```
POST /callers/{caller_id}/rooms/receivers
{"disable_directory": false, "name": "ZZ API Test", "room": "999", "number_check": true,
 "receiver_groups": [{"viewers": [], "admins": [], "chimes": [], "phone_numbers": [],
                      "third_party_sips": [], "third_party_viewers": []}]}
→ data: {"id": "<room_id>", …}
```

### Set the receivers of a room ✅

The request replaces the whole list. It is not a patch.

```
POST /callers/{caller_id}/rooms/{room_id}
{"room": "255", "number_check": true, "receiver_groups": [{…full list…}]}
```

- Receivers that stay in the list keep their schedules ✅.
- A receiver that is removed loses its **private schedule** ✅. When it is added again, its schedule is "Always".
- `receiver_groups` is an array. "Add call group" in the UI probably adds a second element, an escalation tier ❔.

### Set the receivers of the doorbell direct call ✅

The caller id comes **after** `/rooms/` in the path.

```
PUT /callers/rooms/{caller_id}
{"receiver_groups": [{"viewers": ["0cea1417bf7d"], "admins": [], "chimes": ["E43883358449"],
                      "phone_numbers": [], "third_party_sips": [], "third_party_viewers": []}]}
```

The request replaces the whole list, like for rooms. The schedule of the remaining viewer was kept ✅.
In delivery-call mode the UI sends more fields 🧩.

### Remove a single receiver

```
DELETE /callers/receivers/{receiver_id}?caller_id={caller_id}&room_id={room_id}&receiver_id={receiver_id}&receiver_type={type}
```

| vertex.type | receiver_type | Status |
|---|---|---|
| `viewer` | `viewer_device` | 🧩 |
| `admin` | `admin` | ✅ |
| `chime` | `chime` | ✅ |
| `phone_number` | `phone_number` | 🧩 |
| `third_party_sip` / `third_party_viewer` | same as the vertex type | 🧩 |

For the doorbell direct call, omit `room_id` ✅. Example:
`DELETE /callers/receivers/E43883358449?caller_id=28704e31e640&receiver_id=E43883358449&receiver_type=chime`.

If this request fails, the integration falls back to replacing the whole list.

There is no granular **add** endpoint.

### Delete a room ✅

```
DELETE /callers/{caller_id}/rooms/{room_id}
```

### Set a custom schedule ✅ (room) / ❔ (direct call)

```
PUT /callers/{caller_id}/schedules
{"target_type": "viewer_device", "receiver_id": "0cea1417bf7d", "room_id": "<room_id>",
 "schedule": {"name": "visitor-<epoch_ms>", "schedule_type": "access", "is_private": true,
   "holiday_timezone": {"day_schedule": [], "name": "holiday <epoch_ms>"},
   "user_timezone": {"name": "user <epoch_ms>",
     "week_schedule": {"0": [], "1": [{"start_time": "09:00:00", "end_time": "17:00:59"}], …, "6": []}},
   "holiday_group_id": ""}}
```

- In `week_schedule`, `0` is Sunday and `6` is Saturday.
- `target_type` follows the same rule as `receiver_type`: `viewer` → `viewer_device`, all other types unchanged.
- The UI adds the query `?custom_delivery_direct_receiver=undefined`. The integration leaves it out.
- For the direct call, the integration leaves out `room_id` ❔.

### Reset a schedule to "Always" ✅

```
DELETE /callers/{caller_id}/receivers/{receiver_id}/schedules?target_type=viewer_device&room_id={room_id}
```

## Ring history and live events

Recorded on 2026-10-17 on the same console.

### Ring history ✅

```
POST /system_log/search?page_size=50&page_num=1
{"topic": "rings", "since": "2026-09-17T00:00:00Z", "until": "2026-10-17T23:59:59Z"}
→ data: {versions, events[], buckets[]}
```

- `since` and `until` must be RFC 3339 strings. A number is rejected with `CODE_PARAMS_INVALID`.
- Other topics: `access_denial`, `unlocks`, `devices`, `emergency`, `system`, `admin`.

An event has these fields:

- `id`, `log_key`, `event_type`, `message`, `published` (epoch ms), `result`
- `metadata.directory`: `id` is the directory `room_id`, `display_name` the room name. It is missing for the doorbell direct call.
- `metadata.actor`: the user or viewer that accepted or rejected the call.
- `metadata.viewer`: the viewer, if one was involved.
- `metadata.device_ua_pro.id`: the caller id (intercom).
- `metadata.reader_capture`:
  - `id` is shared by "Incoming Call" and the result entry of the same call.
  - `thumbnail_url` and `video_url` point to Protect.
  - The "accepted" entry has no `reader_capture`.

Recorded log keys:

| log_key | event_type | Meaning |
|---|---|---|
| `access.visitor.made.doorbell.to.viewer` | `access.remotecall.request` | incoming call to a room |
| `access.doorbell.visitor.rang.remotecall` | `access.remotecall.request` | incoming doorbell direct call |
| `access.user.accept.doorbell.door_to_viewer` | `access.remotecall.accepted` | accepted by a user |
| `access.user.reject.doorbell.from_door_to_viewer` | `access.door.unlock` | rejected, or an accepted call was hung up |
| `access.doorbell.reject.use.viewer` | `access.door.unlock` | direct call rejected at a viewer |
| `access.doorbell.missed.from_door_to_viewer` | `access.door.unlock` | room call not answered |
| `access.doorbell.from_door.missed` | `access.door.unlock` | direct call not answered |
| `access.doorbell.no_receivers.missed` | `access.door.unlock` | direct call without an active receiver, about 60 s after the ring |
| `access.doorbell.canceled.from_door_to_viewer` | `access.door.unlock` | visitor canceled |

Further keys from the UI source, not recorded yet 🧩:

- `access.user.accept.doorbell.from_door`
- `access.viewer.accept.doorbell`
- `access.doorbell.phone.accept`
- `access.doorbell.third_party_sip.accept`
- `access.doorbell.reject.use.third_party_sip`
- `access.doorbell.visitor.canceled`
- `access.doorbell.visitor.rang`
- `access.user.reject.doorbell.from_door`

Incremental cache endpoints exist, but their entry format has not been checked, so the integration does not use them:

- `GET /system_log/cache?topic=rings`
- `GET /system_log/cache_latest_data?topic=rings&max_id=<id>`

### Notification WebSocket ✅

`wss://<console>/proxy/access/api/v2/ws/notification`

- Authentication: the session cookie.
- Messages are plain JSON: `{"event", "receiver_id", "event_object_id", "save_to_history", "data"}`. The connection also sends `"Hello"` keep-alives.
- `access.remote_view` is an incoming call to app receivers. It contains `request_id` and `door_guard_ids` (the users who are called).
  - Its `room_id` is a WebRTC session id (`WR-…`), **not** the directory room.
  - Access sends no `access.remote_view` for a doorbell call that only rings viewers.
- `access.remote_view.change` is the end of a call: `{"reason_code", "remote_call_request_id"}`.

| reason_code | Meaning |
|---|---|
| `400` | accepted ✅ |
| `106` | rejected ✅ |
| `105`, `108` | probably canceled or timeout 🧩 |

### Protect ring event

`wss://<console>/proxy/protect/ws/updates` sends an event of `type: "ring"` for every ring. It is binary and contains no room. The integration does not use it.

## How the integration uses the API

| Action | Requests |
|---|---|
| poll | `GET /callers`, then `GET /topology` per caller. The receiver lists are reloaded every 5 minutes. |
| add | `GET /topology` to get a fresh state, then `POST /rooms/{room}` or `PUT /callers/rooms/{caller}` with the full list. If a schedule was saved, it is restored with `PUT /schedules`. |
| remove | `GET /topology`, then the schedule is saved with `GET /schedule/{id}` if it is private, then `DELETE /callers/receivers/{id}`. If that fails, the full list is replaced instead. |
| create / delete room | `POST /rooms/receivers` / `DELETE /rooms/{room}` |
| schedule | `PUT /schedules` / `DELETE /receivers/{id}/schedules` |
| calls | WebSocket event → `POST /system_log/search` (topic `rings`, last 5 min), and the same request every 15 s |

All write operations of one config entry are serialized with a lock.
