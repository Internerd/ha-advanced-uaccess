"""Constants for the Advanced UniFi Access Directory integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "advanced_uaccess"

CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_RESTORE_SCHEDULES: Final = "restore_schedules"
CONF_USER_RECEIVERS: Final = "user_receivers"
CONF_TRACK_CALLS: Final = "track_calls"

DEFAULT_VERIFY_SSL: Final = False
DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 3600
DEFAULT_RESTORE_SCHEDULES: Final = True
DEFAULT_TRACK_CALLS: Final = True

STORAGE_VERSION: Final = 1

# Pseudo room id used for the doorbell direct call ("Türklingelanruf").
DIRECT_CALL: Final = "direct"

# Receiver types as they appear in topology vertices (vertex.type).
TYPE_VIEWER: Final = "viewer"
TYPE_ADMIN: Final = "admin"
TYPE_CHIME: Final = "chime"
TYPE_PHONE: Final = "phone_number"
TYPE_THIRD_PARTY_SIP: Final = "third_party_sip"
TYPE_THIRD_PARTY_VIEWER: Final = "third_party_viewer"

RECEIVER_TYPES: Final = (
    TYPE_VIEWER,
    TYPE_ADMIN,
    TYPE_CHIME,
    TYPE_PHONE,
    TYPE_THIRD_PARTY_SIP,
    TYPE_THIRD_PARTY_VIEWER,
)

# Receiver types Home Assistant can offer as switches (we know where to list them).
MANAGEABLE_TYPES: Final = (TYPE_VIEWER, TYPE_ADMIN, TYPE_CHIME, TYPE_PHONE)

# vertex.type -> key inside a receiver_group payload.
GROUP_KEYS: Final = {
    TYPE_VIEWER: "viewers",
    TYPE_ADMIN: "admins",
    TYPE_CHIME: "chimes",
    TYPE_PHONE: "phone_numbers",
    TYPE_THIRD_PARTY_SIP: "third_party_sips",
    TYPE_THIRD_PARTY_VIEWER: "third_party_viewers",
}


def api_receiver_type(vertex_type: str) -> str:
    """Return the receiver_type/target_type the API expects.

    From the Access web UI (selectSendReceiverType): viewer -> viewer_device,
    everything else is sent as the vertex type itself.
    """
    return "viewer_device" if vertex_type == TYPE_VIEWER else vertex_type


ATTR_CALLER_ID: Final = "caller_id"
ATTR_ROOM_ID: Final = "room_id"
ATTR_RECEIVER_TYPE: Final = "receiver_type"
ATTR_RECEIVER_ID: Final = "receiver_id"
ATTR_NAME: Final = "name"
ATTR_NUMBER: Final = "number"
ATTR_NUMBER_CHECK: Final = "number_check"
ATTR_VIEWERS: Final = "viewers"
ATTR_ADMINS: Final = "admins"
ATTR_CHIMES: Final = "chimes"
ATTR_PHONE_NUMBERS: Final = "phone_numbers"
ATTR_SCHEDULE: Final = "schedule"

SERVICE_ADD_RECEIVER: Final = "add_receiver"
SERVICE_REMOVE_RECEIVER: Final = "remove_receiver"
SERVICE_CREATE_ROOM: Final = "create_room"
SERVICE_DELETE_ROOM: Final = "delete_room"
SERVICE_SET_SCHEDULE: Final = "set_schedule"
SERVICE_CLEAR_SCHEDULE: Final = "clear_schedule"

WEEKDAYS: Final = (
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
)
