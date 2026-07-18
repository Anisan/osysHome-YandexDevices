"""MCP integration helpers for YandexDevices plugin."""

from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import delete, or_

from app.core.lib.mcp_contract import (
    build_plugin_mcp_descriptors,
    revision_from_datetime,
    revision_from_dict,
    validate_entity_payload,
)
from app.core.lib.plugin_binding import (
    remove_property_link,
    sync_object_link,
    sync_property_link,
    validate_object_exists,
    validate_object_property_exists,
)
from app.core.main.ObjectsStorage import objects_storage
from app.database import row2dict, session_scope

from plugins.YandexDevices.models.YaCapabilities import YaCapabilities
from plugins.YandexDevices.models.YaDevices import YaDevices
from plugins.YandexDevices.models.YaStation import YaStation

STATIONS = "stations"
DEVICES = "devices"
CAPABILITIES = "capabilities"
PLUGIN_NAME = "YandexDevices"

_STATION_WRITABLE_FIELDS = (
    "title",
    "ip",
    "tts",
    "min_level",
    "glagol_linked_object",
)
_DEVICE_WRITABLE_FIELDS = ("update_period",)
_CAPABILITY_WRITABLE_FIELDS = (
    "read_only",
    "linked_object",
    "linked_property",
    "linked_method",
)

_STATION_READONLY_FIELDS = (
    "id",
    "platform",
    "station_id",
    "iot_id",
    "icon",
    "device_token",
    "has_device_token",
    "screen_capable",
    "screen_present",
    "online",
    "tts_scenario",
    "updated",
)
_DEVICE_READONLY_FIELDS = (
    "id",
    "title",
    "device_type",
    "room",
    "icon",
    "iot_id",
    "updated",
)
_CAPABILITY_READONLY_FIELDS = (
    "id",
    "device_id",
    "title",
    "value",
    "updated",
)

_PLUGIN_NOTES = [
    "YandexDevices syncs Quasar IoT devices/stations and binds capabilities to osysHome.",
    "Authorize once in the admin UI (QR / cookie); invoke get_connection_status to check session.",
    "Prefer sync_stations + sync_devices over inventing rows; collections are not creatable.",
    "Stations: configure LAN ip, tts (0=off, 1=Glagol local, 2=cloud), min_level, glagol_linked_object.",
    "glagol_linked_object receives Glagol properties: state, volume, muted, alice_state, media_*.",
    "device_token is write-only via generate_device_token; never returned in list/get (only has_device_token).",
    "Devices: only update_period is writable; titles/types come from Quasar sync.",
    "Capabilities are auto-created when polling device data; upsert only bindings and read_only.",
    "read_only=true = inbound only; read_only=false registers reverse property link for control.",
    "linked_method is called on value change with NEW_VALUE/OLD_VALUE/DEVICE_STATE/UPDATED/MODULE.",
    "say() speaks on stations with tts enabled when level >= min_level (object.property or int).",
    "LAN control: invoke glagol_command with station_id/object/station + text or action.",
    "Prefer validate_entity before upsert when linking properties.",
]

_BINDING_PROMPT = "osys_yandexdevices_binding"
_ENTITY_AUTHORING_PROMPT = "osys_yandexdevices_entity_authoring"

_GLAGOL_PROPERTIES = (
    "state",
    "volume",
    "muted",
    "alice_state",
    "media_title",
    "media_subtitle",
    "media_duration",
    "media_progress",
    "media_cover_url",
)


def _plugin_instance():
    try:
        from app.core.main.PluginsHelper import plugins
        return plugins.get(PLUGIN_NAME, {}).get("instance")
    except Exception:
        return None


def validate_object_method_exists(object_name: Optional[str], method_name: Optional[str]) -> bool:
    obj_name = str(object_name or "").strip()
    meth_name = str(method_name or "").strip()
    if not obj_name or not meth_name:
        return False
    obj = objects_storage.getObjectByName(obj_name)
    if obj is None:
        return False
    return meth_name in getattr(obj, "methods", {})


def mcp_capabilities() -> dict:
    return {
        "mcp_version": 1,
        "entities": True,
        "config_schema": True,
        "notes": list(_PLUGIN_NOTES),
        "collections": [
            {
                "id": STATIONS,
                "title": "Yandex Stations",
                "binding_mode": "object",
                "writable": True,
                "creatable": False,
                "deletable": True,
                "has_code": False,
                "list_filters": [
                    "query",
                    "online",
                    "glagol_linked_object",
                    "has_glagol_link",
                ],
                "default_sort": "title asc, id asc",
                "writable_fields": list(_STATION_WRITABLE_FIELDS),
                "description": (
                    "Yandex Stations / speakers synced from Quasar. "
                    "Configure LAN Glagol (ip, token via generate_device_token) and TTS."
                ),
            },
            {
                "id": DEVICES,
                "title": "Yandex IoT Devices",
                "binding_mode": "none",
                "writable": True,
                "creatable": False,
                "deletable": True,
                "has_code": False,
                "list_filters": ["query", "room", "device_type"],
                "default_sort": "title asc, id asc",
                "writable_fields": list(_DEVICE_WRITABLE_FIELDS),
                "description": (
                    "IoT devices from Yandex Quasar. Synced fields are read-only; "
                    "only update_period is writable. Capabilities appear after refresh_device."
                ),
            },
            {
                "id": CAPABILITIES,
                "title": "Device Capabilities",
                "binding_mode": "property",
                "writable": True,
                "creatable": False,
                "deletable": True,
                "has_code": False,
                "list_filters": [
                    "query",
                    "device_id",
                    "linked_object",
                    "has_binding",
                    "read_only",
                ],
                "default_sort": "title asc, id asc",
                "writable_fields": list(_CAPABILITY_WRITABLE_FIELDS),
                "description": (
                    "Capability/property keys auto-created from Quasar device status. "
                    "Configure linked_object/property/method and read_only."
                ),
            },
        ],
        "operations": [
            "sync_devices",
            "sync_stations",
            "say",
            "get_connection_status",
            "refresh_device",
            "generate_device_token",
            "get_capability_value",
            "glagol_command",
        ],
        "operation_schemas": {
            "sync_devices": {
                "description": "Refresh IoT device list from Quasar (rooms/devices)",
                "params": {"type": "object", "properties": {}},
            },
            "sync_stations": {
                "description": "Refresh stations from Quasar online stats and sync Glagol registry",
                "params": {"type": "object", "properties": {}},
            },
            "say": {
                "description": "Speak message on stations with TTS enabled (respects min_level)",
                "params": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "level": {"type": "integer", "default": 0},
                        "args": {
                            "type": "object",
                            "description": "Optional {station: <title>} to target one station",
                        },
                    },
                    "required": ["message"],
                },
            },
            "get_connection_status": {
                "description": "Report Quasar cookie/session readiness and Glagol registry status",
                "params": {"type": "object", "properties": {}},
            },
            "refresh_device": {
                "description": "Poll one IoT device capabilities/properties from Quasar now",
                "params": {
                    "type": "object",
                    "properties": {
                        "device_id": {"type": "integer", "description": "Internal YaDevices id"},
                    },
                    "required": ["device_id"],
                },
            },
            "generate_device_token": {
                "description": "Request/refresh Glagol device_token for a station (needs iot_id+platform)",
                "params": {
                    "type": "object",
                    "properties": {
                        "station_id": {"type": "integer"},
                    },
                    "required": ["station_id"],
                },
            },
            "get_capability_value": {
                "description": "Read cached capability value from database",
                "params": {
                    "type": "object",
                    "properties": {
                        "capability_id": {"type": "integer"},
                        "device_id": {"type": "integer"},
                        "title": {
                            "type": "string",
                            "description": "Capability title with device_id",
                        },
                    },
                },
            },
            "glagol_command": {
                "description": (
                    "Send LAN Glagol command (text or player action). "
                    "Resolve station via station_id, object (glagol_linked_object), or station title."
                ),
                "params": {
                    "type": "object",
                    "properties": {
                        "station_id": {"type": "integer"},
                        "object": {
                            "type": "string",
                            "description": "osysHome object name (= glagol_linked_object)",
                        },
                        "station": {"type": "string", "description": "Station title"},
                        "text": {
                            "type": "string",
                            "description": "sendText payload (TTS / Alice command)",
                        },
                        "action": {
                            "type": "string",
                            "description": "Player action (play, pause, volume, …)",
                        },
                    },
                },
            },
        },
    }


def mcp_config_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "get_device_data": {
                "type": "boolean",
                "default": False,
                "description": "Enable cyclic polling of IoT device capabilities",
            },
            "update_period": {
                "type": "integer",
                "default": 60,
                "description": "Default device poll period in seconds (overridable per device)",
            },
            "update_linked": {
                "type": "boolean",
                "default": True,
                "description": "When polling, only devices that have at least one capability link",
            },
        },
    }


def _collection_meta(collection: str) -> dict:
    for item in mcp_capabilities()["collections"]:
        if item["id"] == collection:
            return item
    raise ValueError(f"Unsupported collection: {collection}")


def _format_dt(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


def _parse_optional_bool(value) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _station_to_dict(row: YaStation) -> dict:
    data = row2dict(row)
    data.pop("device_token", None)
    data["has_device_token"] = bool(str(row.device_token or "").strip())
    data["online"] = bool(row.online) if row.online is not None else False
    data["tts"] = int(row.tts) if row.tts is not None else 0
    updated = _format_dt(row.updated)
    if updated:
        data["updated"] = updated
    return data


def _device_to_dict(row: YaDevices) -> dict:
    data = row2dict(row)
    updated = _format_dt(row.updated)
    if updated:
        data["updated"] = updated
    return data


def _capability_to_dict(row: YaCapabilities) -> dict:
    data = row2dict(row)
    data["read_only"] = bool(row.read_only)
    updated = _format_dt(row.updated)
    if updated:
        data["updated"] = updated
    return data


def _writable_fields(collection: str) -> tuple:
    if collection == STATIONS:
        return _STATION_WRITABLE_FIELDS
    if collection == DEVICES:
        return _DEVICE_WRITABLE_FIELDS
    if collection == CAPABILITIES:
        return _CAPABILITY_WRITABLE_FIELDS
    return ()


def _readonly_fields(collection: str) -> tuple:
    if collection == STATIONS:
        return _STATION_READONLY_FIELDS
    if collection == DEVICES:
        return _DEVICE_READONLY_FIELDS
    if collection == CAPABILITIES:
        return _CAPABILITY_READONLY_FIELDS
    return ("id",)


def _merge_payload(collection: str, payload: dict, entity_id=None) -> dict:
    merged = dict(payload or {})
    if entity_id in (None, ""):
        return merged
    try:
        current = mcp_get_entity(collection, entity_id)
    except ValueError:
        return merged
    for field in _writable_fields(collection):
        if field not in merged and field in current:
            merged[field] = current[field]
    return merged


def _sync_capability_link(row: YaCapabilities, *, old_object=None, old_property=None) -> None:
    """Register reverse property link only for writable (non read-only) bindings."""
    if row.read_only:
        if old_object and old_property:
            remove_property_link(PLUGIN_NAME, old_object, old_property)
        return
    ok, err = sync_property_link(
        PLUGIN_NAME,
        row.linked_object,
        row.linked_property,
        old_object=old_object,
        old_property=old_property,
    )
    if not ok:
        raise ValueError(err or "property link validation failed")


def _resolve_capability(
    session,
    capability_id=None,
    device_id=None,
    title: Optional[str] = None,
) -> YaCapabilities:
    if capability_id not in (None, ""):
        row = session.query(YaCapabilities).filter(YaCapabilities.id == int(capability_id)).one_or_none()
        if row is None:
            raise ValueError(f"Capability not found: {capability_id}")
        return row
    if device_id in (None, "") or not str(title or "").strip():
        raise ValueError("capability_id or (device_id + title) is required")
    row = (
        session.query(YaCapabilities)
        .filter(
            YaCapabilities.device_id == int(device_id),
            YaCapabilities.title == str(title).strip(),
        )
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"Capability not found: device_id={device_id} title={title!r}")
    return row


def mcp_entity_schema(collection: str) -> dict:
    _collection_meta(collection)
    if collection == STATIONS:
        return {
            "type": "object",
            "description": (
                "Yandex Station. Synced identity fields are read-only; "
                "configure LAN Glagol and TTS locally."
            ),
            "properties": {
                "id": {"type": "integer", "readOnly": True},
                "title": {"type": "string", "description": "Display name"},
                "platform": {"type": "string", "readOnly": True},
                "station_id": {"type": "string", "readOnly": True},
                "iot_id": {"type": "string", "readOnly": True},
                "ip": {
                    "type": "string",
                    "description": "LAN IP or host:port for Glagol (default port 1961)",
                },
                "online": {"type": "boolean", "readOnly": True},
                "tts": {
                    "type": "integer",
                    "description": "0=off, 1=local Glagol, 2=cloud TTS scenario",
                    "enum": [0, 1, 2],
                },
                "min_level": {
                    "type": "string",
                    "description": "Min say() level: integer or Object.property path",
                },
                "tts_scenario": {"type": "string", "readOnly": True},
                "glagol_linked_object": {
                    "type": "string",
                    "description": (
                        "osysHome object name for Glagol status. "
                        f"Expected properties: {', '.join(_GLAGOL_PROPERTIES)}"
                    ),
                },
                "has_device_token": {
                    "type": "boolean",
                    "readOnly": True,
                    "description": "True when Glagol device_token is stored",
                },
                "updated": {"type": "string", "readOnly": True},
            },
        }
    if collection == DEVICES:
        return {
            "type": "object",
            "description": "Yandex IoT device synced from Quasar (mostly read-only).",
            "properties": {
                "id": {"type": "integer", "readOnly": True},
                "title": {"type": "string", "readOnly": True},
                "device_type": {"type": "string", "readOnly": True},
                "room": {"type": "string", "readOnly": True},
                "iot_id": {"type": "string", "readOnly": True},
                "icon": {"type": "string", "readOnly": True},
                "update_period": {
                    "type": "integer",
                    "description": "Per-device poll period in seconds (null = use config default)",
                },
                "updated": {"type": "string", "readOnly": True},
            },
        }
    if collection == CAPABILITIES:
        return {
            "type": "object",
            "description": "Device capability/property with optional osysHome binding.",
            "properties": {
                "id": {"type": "integer", "readOnly": True},
                "device_id": {
                    "type": "integer",
                    "readOnly": True,
                    "description": "Parent YaDevices id",
                },
                "title": {
                    "type": "string",
                    "readOnly": True,
                    "description": (
                        "Capability key, e.g. devices.capabilities.on_off "
                        "or devices.properties.float.temperature"
                    ),
                },
                "value": {
                    "type": "string",
                    "readOnly": True,
                    "description": "Last reported value (string cache)",
                },
                "read_only": {
                    "type": "boolean",
                    "description": "true = inbound only; false registers reverse property link",
                },
                "linked_object": {
                    "type": "string",
                    "description": "Bound osysHome object name",
                },
                "linked_property": {
                    "type": "string",
                    "description": "Bound osysHome property for inbound updates",
                },
                "linked_method": {
                    "type": "string",
                    "description": "Optional method called on value change",
                },
                "updated": {"type": "string", "readOnly": True},
            },
        }
    raise ValueError(f"Unsupported collection: {collection}")


def mcp_list_entities(
    collection: str,
    query: str = None,
    limit: int = 100,
    device_id: Optional[int] = None,
    online: Optional[bool] = None,
    room: Optional[str] = None,
    device_type: Optional[str] = None,
    linked_object: Optional[str] = None,
    glagol_linked_object: Optional[str] = None,
    has_binding: Optional[bool] = None,
    has_glagol_link: Optional[bool] = None,
    read_only: Optional[bool] = None,
) -> List[dict]:
    limit = max(1, min(int(limit or 100), 5000))

    if collection == STATIONS:
        online_filter = _parse_optional_bool(online)
        link_filter = _parse_optional_bool(has_glagol_link)
        glagol_obj = str(glagol_linked_object or "").strip()
        with session_scope() as session:
            q = session.query(YaStation)
            if query:
                like = f"%{query}%"
                q = q.filter(
                    or_(
                        YaStation.title.ilike(like),
                        YaStation.platform.ilike(like),
                        YaStation.ip.ilike(like),
                        YaStation.glagol_linked_object.ilike(like),
                    )
                )
            if online_filter is not None:
                q = q.filter(YaStation.online == (1 if online_filter else 0))
            if glagol_obj:
                q = q.filter(YaStation.glagol_linked_object == glagol_obj)
            if link_filter is True:
                q = q.filter(
                    YaStation.glagol_linked_object.isnot(None),
                    YaStation.glagol_linked_object != "",
                )
            elif link_filter is False:
                q = q.filter(
                    or_(
                        YaStation.glagol_linked_object.is_(None),
                        YaStation.glagol_linked_object == "",
                    )
                )
            rows = q.order_by(YaStation.title, YaStation.id).limit(limit).all()
            return [_station_to_dict(row) for row in rows]

    if collection == DEVICES:
        room_filter = str(room or "").strip()
        type_filter = str(device_type or "").strip()
        with session_scope() as session:
            q = session.query(YaDevices)
            if query:
                like = f"%{query}%"
                q = q.filter(
                    or_(
                        YaDevices.title.ilike(like),
                        YaDevices.room.ilike(like),
                        YaDevices.device_type.ilike(like),
                        YaDevices.iot_id.ilike(like),
                    )
                )
            if room_filter:
                q = q.filter(YaDevices.room == room_filter)
            if type_filter:
                q = q.filter(YaDevices.device_type == type_filter)
            rows = q.order_by(YaDevices.title, YaDevices.id).limit(limit).all()
            return [_device_to_dict(row) for row in rows]

    if collection == CAPABILITIES:
        linked_obj = str(linked_object or "").strip()
        binding_filter = _parse_optional_bool(has_binding)
        read_only_filter = _parse_optional_bool(read_only)
        with session_scope() as session:
            q = session.query(YaCapabilities)
            if device_id not in (None, ""):
                q = q.filter(YaCapabilities.device_id == int(device_id))
            if query:
                like = f"%{query}%"
                q = q.filter(
                    or_(
                        YaCapabilities.title.ilike(like),
                        YaCapabilities.linked_object.ilike(like),
                        YaCapabilities.linked_property.ilike(like),
                        YaCapabilities.value.ilike(like),
                    )
                )
            if linked_obj:
                q = q.filter(YaCapabilities.linked_object == linked_obj)
            if binding_filter is True:
                q = q.filter(
                    YaCapabilities.linked_object.isnot(None),
                    YaCapabilities.linked_object != "",
                    YaCapabilities.linked_property.isnot(None),
                    YaCapabilities.linked_property != "",
                )
            elif binding_filter is False:
                q = q.filter(
                    or_(
                        YaCapabilities.linked_object.is_(None),
                        YaCapabilities.linked_object == "",
                        YaCapabilities.linked_property.is_(None),
                        YaCapabilities.linked_property == "",
                    )
                )
            if read_only_filter is True:
                q = q.filter(YaCapabilities.read_only == 1)
            elif read_only_filter is False:
                q = q.filter(or_(YaCapabilities.read_only == 0, YaCapabilities.read_only.is_(None)))
            rows = q.order_by(YaCapabilities.title, YaCapabilities.id).limit(limit).all()
            return [_capability_to_dict(row) for row in rows]

    raise ValueError(f"Unsupported collection: {collection}")


def mcp_get_entity(collection: str, entity_id) -> dict:
    with session_scope() as session:
        if collection == STATIONS:
            row = session.query(YaStation).filter(YaStation.id == int(entity_id)).one_or_none()
            if row is None:
                raise ValueError(f"Station not found: {entity_id}")
            return _station_to_dict(row)
        if collection == DEVICES:
            row = session.query(YaDevices).filter(YaDevices.id == int(entity_id)).one_or_none()
            if row is None:
                raise ValueError(f"Device not found: {entity_id}")
            return _device_to_dict(row)
        if collection == CAPABILITIES:
            row = session.query(YaCapabilities).filter(YaCapabilities.id == int(entity_id)).one_or_none()
            if row is None:
                raise ValueError(f"Capability not found: {entity_id}")
            return _capability_to_dict(row)
    raise ValueError(f"Unsupported collection: {collection}")


def mcp_upsert_entity(collection: str, payload: dict, entity_id=None) -> dict:
    meta = _collection_meta(collection)
    if not meta.get("writable"):
        raise ValueError(f"Collection '{collection}' is read-only")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if meta.get("creatable") is False and entity_id in (None, ""):
        raise ValueError(
            f"Collection '{collection}' does not allow manual creation; "
            "sync/refresh first, then upsert by entity_id"
        )

    clean_payload = dict(payload)
    for field in _readonly_fields(collection):
        clean_payload.pop(field, None)

    validation = mcp_validate_entity(collection, clean_payload, entity_id=entity_id)
    if not validation.get("ok"):
        raise ValueError(f"validation failed: {validation}")

    merged = _merge_payload(collection, clean_payload, entity_id=entity_id)

    if collection == STATIONS:
        with session_scope() as session:
            row = session.query(YaStation).filter(YaStation.id == int(entity_id)).one_or_none()
            if row is None:
                raise ValueError(f"Station not found: {entity_id}")
            if "title" in merged:
                row.title = str(merged.get("title") or "").strip() or row.title
            if "ip" in merged:
                row.ip = str(merged.get("ip") or "").strip() or None
            if "tts" in merged and merged["tts"] is not None:
                row.tts = int(merged["tts"])
            if "min_level" in merged:
                row.min_level = str(merged.get("min_level") or "").strip() or None
            if "glagol_linked_object" in merged:
                linked = str(merged.get("glagol_linked_object") or "").strip() or None
                if linked:
                    ok, err = sync_object_link(linked)
                    if not ok:
                        raise ValueError(err or "object link validation failed")
                row.glagol_linked_object = linked
            session.commit()
            session.refresh(row)
            instance = _plugin_instance()
            reg = getattr(instance, "_glagol_registry", None) if instance else None
            if reg is not None:
                try:
                    reg.sync_stations()
                except Exception:
                    pass
            return _station_to_dict(row)

    if collection == DEVICES:
        with session_scope() as session:
            row = session.query(YaDevices).filter(YaDevices.id == int(entity_id)).one_or_none()
            if row is None:
                raise ValueError(f"Device not found: {entity_id}")
            if "update_period" in merged:
                period = merged.get("update_period")
                row.update_period = int(period) if period not in (None, "") else None
            session.commit()
            session.refresh(row)
            return _device_to_dict(row)

    if collection == CAPABILITIES:
        with session_scope() as session:
            row = session.query(YaCapabilities).filter(YaCapabilities.id == int(entity_id)).one_or_none()
            if row is None:
                raise ValueError(f"Capability not found: {entity_id}")
            old_object = row.linked_object
            old_property = row.linked_property
            if "read_only" in merged:
                row.read_only = 1 if merged.get("read_only") else 0
            if "linked_object" in merged:
                row.linked_object = str(merged.get("linked_object") or "").strip() or None
            if "linked_property" in merged:
                row.linked_property = str(merged.get("linked_property") or "").strip() or None
            if "linked_method" in merged:
                row.linked_method = str(merged.get("linked_method") or "").strip() or None
            session.commit()
            session.refresh(row)
            _sync_capability_link(row, old_object=old_object, old_property=old_property)
            return _capability_to_dict(row)

    raise ValueError(f"Unsupported collection: {collection}")


def mcp_delete_entity(collection: str, entity_id) -> bool:
    meta = _collection_meta(collection)
    if meta.get("deletable") is False:
        raise ValueError(f"Collection '{collection}' does not allow deletion")

    with session_scope() as session:
        if collection == STATIONS:
            row = session.query(YaStation).filter(YaStation.id == int(entity_id)).one_or_none()
            if row is None:
                raise ValueError(f"Station not found: {entity_id}")
            session.execute(delete(YaStation).where(YaStation.id == int(entity_id)))
            session.commit()
            instance = _plugin_instance()
            reg = getattr(instance, "_glagol_registry", None) if instance else None
            if reg is not None:
                try:
                    reg.sync_stations()
                except Exception:
                    pass
            return True

        if collection == DEVICES:
            row = session.query(YaDevices).filter(YaDevices.id == int(entity_id)).one_or_none()
            if row is None:
                raise ValueError(f"Device not found: {entity_id}")
            caps = session.query(YaCapabilities).filter(YaCapabilities.device_id == int(entity_id)).all()
            for cap in caps:
                if cap.linked_object and cap.linked_property:
                    remove_property_link(PLUGIN_NAME, cap.linked_object, cap.linked_property)
            session.execute(delete(YaCapabilities).where(YaCapabilities.device_id == int(entity_id)))
            session.execute(delete(YaDevices).where(YaDevices.id == int(entity_id)))
            session.commit()
            return True

        if collection == CAPABILITIES:
            row = session.query(YaCapabilities).filter(YaCapabilities.id == int(entity_id)).one_or_none()
            if row is None:
                raise ValueError(f"Capability not found: {entity_id}")
            if row.linked_object and row.linked_property:
                remove_property_link(PLUGIN_NAME, row.linked_object, row.linked_property)
            session.execute(delete(YaCapabilities).where(YaCapabilities.id == int(entity_id)))
            session.commit()
            return True

    raise ValueError(f"Unsupported collection: {collection}")


def mcp_validate_entity_code(collection: str, code: str) -> dict:
    raise ValueError(f"Collection '{collection}' does not support code validation")


def mcp_run_entity_dry(collection: str, code: str, context: dict = None) -> dict:
    raise ValueError(f"Collection '{collection}' does not support dry-run code")


def _connection_status() -> dict:
    instance = _plugin_instance()
    if instance is None:
        raise ValueError("YandexDevices plugin not loaded")
    quazar = getattr(instance, "quazar", None)
    cookie_path = getattr(quazar, "cookie_path", None) if quazar else None
    cookie_present = bool(cookie_path and os.path.exists(cookie_path))
    iot_allowed = bool(quazar and getattr(quazar, "_iot_backend_allowed", lambda: False)())
    csrf_set = bool(str(getattr(quazar, "csrf_token", None) or "").strip()) if quazar else False
    blocked = bool(getattr(quazar, "_iot_unauthorized_blocked", False)) if quazar else False
    reg = getattr(instance, "_glagol_registry", None)
    with session_scope() as session:
        stations = session.query(YaStation).count()
        devices = session.query(YaDevices).count()
        capabilities = session.query(YaCapabilities).count()
    return {
        "plugin_loaded": True,
        "cookie_present": cookie_present,
        "iot_backend_allowed": iot_allowed,
        "csrf_token_set": csrf_set,
        "iot_unauthorized_blocked": blocked,
        "glagol_registry_ready": reg is not None,
        "stations_count": stations,
        "devices_count": devices,
        "capabilities_count": capabilities,
        "get_device_data": bool(instance.config.get("get_device_data", False)),
        "update_period": instance.config.get("update_period", 60),
        "update_linked": bool(instance.config.get("update_linked", True)),
    }


def mcp_invoke(operation: str, params: dict = None) -> dict:
    params = params or {}
    instance = _plugin_instance()

    if operation == "get_connection_status":
        return {"ok": True, "operation": operation, **_connection_status()}

    if operation == "get_capability_value":
        with session_scope() as session:
            cap = _resolve_capability(
                session,
                capability_id=params.get("capability_id"),
                device_id=params.get("device_id"),
                title=params.get("title"),
            )
            return {
                "ok": True,
                "operation": operation,
                "capability_id": cap.id,
                "device_id": cap.device_id,
                "title": cap.title,
                "value": cap.value,
                "read_only": bool(cap.read_only),
                "updated": _format_dt(cap.updated),
            }

    if instance is None:
        raise ValueError("YandexDevices plugin not loaded")

    if operation == "sync_devices":
        instance.update_devices()
        return {"ok": True, "operation": operation}

    if operation == "sync_stations":
        instance.refresh_stations()
        reg = getattr(instance, "_glagol_registry", None)
        if reg is not None:
            reg.sync_stations()
        return {"ok": True, "operation": operation}

    if operation == "say":
        message = str(params.get("message") or "").strip()
        if not message:
            raise ValueError("message is required")
        ok = instance.say(message, level=int(params.get("level") or 0), args=params.get("args"))
        return {"ok": bool(ok), "operation": operation}

    if operation == "refresh_device":
        device_id = params.get("device_id")
        if device_id in (None, ""):
            raise ValueError("device_id is required")
        instance.refresh_device_data(int(device_id))
        return {"ok": True, "operation": operation, "device_id": int(device_id)}

    if operation == "generate_device_token":
        station_id = params.get("station_id")
        if station_id in (None, ""):
            raise ValueError("station_id is required")
        with session_scope() as session:
            station = session.query(YaStation).filter(YaStation.id == int(station_id)).one_or_none()
            if station is None:
                raise ValueError(f"Station not found: {station_id}")
            if not (station.iot_id and station.platform):
                raise ValueError("Station needs iot_id and platform — run sync_stations / sync_devices first")
            token = instance.quazar.get_device_token(station.iot_id, station.platform)
            if not token:
                raise ValueError("Yandex did not return a device token — check Authorization")
            station.device_token = token
            session.commit()
        reg = getattr(instance, "_glagol_registry", None)
        if reg is not None:
            reg.sync_stations()
        return {
            "ok": True,
            "operation": operation,
            "station_id": int(station_id),
            "has_device_token": True,
        }

    if operation == "glagol_command":
        result = instance.glagol_command(**params)
        if not isinstance(result, dict):
            result = {"ok": bool(result), "result": result}
        return {"operation": operation, **result}

    raise ValueError(f"Unsupported operation: {operation}")


def mcp_descriptors() -> Tuple[list, list, list]:
    return build_plugin_mcp_descriptors(PLUGIN_NAME, mcp_capabilities())


def mcp_get_prompt(name: str, arguments: dict = None) -> dict:
    arguments = arguments or {}
    notes_block = "\n".join(f"- {note}" for note in _PLUGIN_NOTES)

    if name == _BINDING_PROMPT:
        object_name = str(arguments.get("object_name") or "").strip()
        property_name = str(arguments.get("property_name") or "").strip()
        device_id = arguments.get("device_id")
        capability_title = str(arguments.get("title") or arguments.get("capability") or "").strip()
        prompt_text = (
            "Bind a YandexDevices capability to an osysHome object property.\n"
            f"Plugin: {PLUGIN_NAME}\n"
            f"Object: {object_name or '-'}\n"
            f"Property: {property_name or '-'}\n"
            f"Device id: {device_id or '-'}\n"
            f"Capability title: {capability_title or '-'}\n\n"
            f"Plugin notes:\n{notes_block}\n\n"
            "Flow:\n"
            "1. invoke get_connection_status; authorize in admin if cookie missing\n"
            "2. invoke sync_devices then refresh_device for the device\n"
            "3. osys_plugin_list_entities collection=capabilities device_id=<id>\n"
            "4. osys_plugin_entity_schema collection=capabilities\n"
            "5. upsert capabilities with linked_object/linked_property "
            f"(example target={(object_name or 'Object') + '.' + (property_name or 'property')})\n"
            "6. Prefer read_only=true for sensors; read_only=false for bidirectional control\n"
            "7. For stations Glagol status use glagol_linked_object (object binding), not capabilities\n"
        )
        return {"messages": [{"role": "user", "content": {"type": "text", "text": prompt_text}}]}

    if name == _ENTITY_AUTHORING_PROMPT:
        task = str(arguments.get("task") or "").strip()
        collection = str(arguments.get("collection") or CAPABILITIES).strip()
        if not task:
            raise ValueError("task is required")
        prompt_text = (
            "Create or update YandexDevices plugin entity payload by schema.\n"
            f"Plugin: {PLUGIN_NAME}\nCollection: {collection}\nTask: {task}\n\n"
            f"Plugin notes:\n{notes_block}\n\n"
            "Flow: osys_plugin_entity_schema -> validate_entity -> upsert_entity.\n"
            "Stations writable: title, ip, tts, min_level, glagol_linked_object.\n"
            "Devices writable: update_period only.\n"
            "Capabilities writable: read_only, linked_object, linked_property, linked_method.\n"
            "Do not invent capability titles; list/refresh existing ones first.\n"
            "Use generate_device_token for Glagol token; glagol_command for LAN actions.\n"
        )
        return {"messages": [{"role": "user", "content": {"type": "text", "text": prompt_text}}]}

    raise ValueError(f"Unsupported prompt: {name}")


def mcp_entity_revision(collection: str, entity_id) -> str:
    entity = mcp_get_entity(collection, entity_id)
    updated = revision_from_datetime(entity.get("updated"))
    if updated:
        return updated
    keys_map = {
        STATIONS: [
            "id",
            "title",
            "station_id",
            "ip",
            "tts",
            "min_level",
            "glagol_linked_object",
            "has_device_token",
            "online",
        ],
        DEVICES: ["id", "title", "iot_id", "room", "update_period"],
        CAPABILITIES: [
            "id",
            "device_id",
            "title",
            "value",
            "read_only",
            "linked_object",
            "linked_property",
            "linked_method",
        ],
    }
    return revision_from_dict(entity, keys=keys_map.get(collection, ["id"]))


def mcp_validate_entity(collection: str, payload: dict, entity_id=None) -> dict:
    _collection_meta(collection)
    if not isinstance(payload, dict):
        return {"ok": False, "errors": [{"field": "_", "message": "payload must be an object"}]}

    if entity_id in (None, ""):
        return {
            "ok": False,
            "errors": [{
                "field": "id",
                "message": (
                    f"{collection} cannot be created manually; "
                    "sync/refresh then upsert by entity_id"
                ),
            }],
        }

    readonly = _readonly_fields(collection)
    disallowed = [key for key in payload if key in readonly]
    if disallowed:
        return {
            "ok": False,
            "errors": [{"field": disallowed[0], "message": "field is read-only"}],
        }

    merged = _merge_payload(collection, payload, entity_id=entity_id)
    schema = mcp_entity_schema(collection)
    result = validate_entity_payload(merged, schema)
    if not result.get("ok"):
        return result

    errors = list(result.get("errors") or [])
    warnings: List[dict] = []

    try:
        current = mcp_get_entity(collection, entity_id)
    except ValueError as ex:
        errors.append({"field": "id", "message": str(ex)})
        current = {}

    if collection == STATIONS:
        linked = str(merged.get("glagol_linked_object") or "").strip()
        if linked and not validate_object_exists(linked):
            errors.append({
                "field": "glagol_linked_object",
                "message": f"Object not found: {linked}",
            })
        if "tts" in payload and payload.get("tts") is not None:
            try:
                tts_val = int(payload["tts"])
            except (TypeError, ValueError):
                errors.append({"field": "tts", "message": "tts must be 0, 1, or 2"})
            else:
                if tts_val not in (0, 1, 2):
                    errors.append({"field": "tts", "message": "tts must be 0, 1, or 2"})

    if collection == DEVICES:
        if "update_period" in payload and payload.get("update_period") not in (None, ""):
            try:
                period = int(payload["update_period"])
            except (TypeError, ValueError):
                errors.append({"field": "update_period", "message": "must be an integer"})
            else:
                if period < 0:
                    errors.append({"field": "update_period", "message": "must be >= 0"})

    if collection == CAPABILITIES:
        linked_object = str(merged.get("linked_object") or "").strip()
        linked_property = str(merged.get("linked_property") or "").strip()
        linked_method = str(merged.get("linked_method") or "").strip()

        if linked_object or linked_property:
            if not linked_object or not linked_property:
                errors.append({
                    "field": "linked_property",
                    "message": "linked_object and linked_property must both be set",
                })
            else:
                if not validate_object_exists(linked_object):
                    errors.append({
                        "field": "linked_object",
                        "message": f"Object not found: {linked_object}",
                    })
                elif not validate_object_property_exists(linked_object, linked_property):
                    errors.append({
                        "field": "linked_property",
                        "message": f"Object property not found: {linked_object}.{linked_property}",
                    })

        if linked_method:
            obj_for_method = linked_object or str(current.get("linked_object") or "").strip()
            if not obj_for_method:
                errors.append({
                    "field": "linked_method",
                    "message": "linked_object is required when linked_method is set",
                })
            elif not validate_object_method_exists(obj_for_method, linked_method):
                errors.append({
                    "field": "linked_method",
                    "message": f"Object method not found: {obj_for_method}.{linked_method}",
                })

        if (
            not linked_object
            and not linked_property
            and not linked_method
            and "read_only" not in payload
        ):
            warnings.append({
                "field": "linked_object",
                "message": "no binding fields in payload; upsert will keep existing links",
            })

    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}

    response = {"ok": True, "errors": []}
    if warnings:
        response["warnings"] = warnings
    return response
