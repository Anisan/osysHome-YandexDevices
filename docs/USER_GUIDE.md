# YandexDevices - User Guide

![YandexDevices Icon](../static/YandexDevices.png "YandexDevices plugin")

## Purpose

`YandexDevices` integrates osysHome with Yandex Quasar IoT and Yandex Stations.

After setup, the module lets you:

- authorize Yandex account access via QR flow;
- import and refresh station/device lists from Yandex cloud;
- monitor Yandex device capabilities and sensor-like properties;
- link capability values to osysHome object properties and methods;
- send control commands from osysHome back to Yandex devices;
- run cloud TTS on selected Yandex Stations.

> [!IMPORTANT]
> The integration is bidirectional: Yandex -> osysHome value updates and osysHome -> Yandex control commands.

---

## Interface Overview

Main admin page:

```text
/admin/YandexDevices
```

Top actions:

1. `Update` - refreshes stations and devices from Yandex cloud.
2. `Authorization` - opens QR-based login status and flow.
3. `Settings` - polling and update behavior.

Tabs:

- `Stations` - station-level settings and TTS behavior.
- `Devices` - discovered IoT devices and capability linking.

---

## Quick Start Checklist

- [ ] Open `/admin/YandexDevices`.
- [ ] Go to `Authorization` and complete QR login.
- [ ] Click `Update` to import stations and IoT devices.
- [ ] Open `Stations` and configure TTS mode for needed stations.
- [ ] Open `Devices`, choose a device, and configure capability links.
- [ ] Enable module polling in `Settings` if periodic updates are required.

---

## Authorization (QR Flow)

Open:

```text
YandexDevices?op=auth
```

Then:

1. Click `QR code`.
2. Scan the QR code using Yandex app.
3. Click `Continue` to confirm.
4. Verify status becomes `Authorized`.

If token/cookie becomes invalid, use `Reset` and re-run QR login.

> [!WARNING]
> Without successful authorization, refresh and control calls to Yandex APIs will fail.

---

## Stations Tab

The stations list shows:

- station title and icon;
- minimum SAY level (`Min level say`);
- online state (`Online`/`Offline`);
- last update timestamp.

### Station edit form

Fields:

| Field | Meaning |
| --- | --- |
| `Title` | Station name in module DB |
| `Platform` | Yandex platform identifier |
| `IOT id` | Quasar IoT identifier |
| `IP` | Optional station IP |
| `Token` | Device token (generated in UI) |
| `TTS` | `No`, `Local (not work)`, `Cloud` |
| `Min level SAY` | Minimum message level required for `say()` |

If token is missing, use `Generate token` inside station edit page.

---

## Devices Tab

The devices list shows:

- title and icon;
- Yandex type;
- room;
- IoT ID;
- last update time.

Open a device to edit:

- `Update period` (per-device polling override);
- links for each discovered capability/property;
- read-only mode for link direction.

---

## How Capability Links Work

Each device has capabilities/properties like:

- `devices.capabilities.on_off`
- `devices.capabilities.range.temperature`
- `devices.properties.float.temperature`
- `devices.properties.event.open`

Each record may be linked to:

- `linked_object` + `linked_property`
- `linked_object` + `linked_method`

### Property link behavior

- incoming Yandex value is written to osysHome property;
- if link is writable (not read-only), reverse updates from osysHome can be sent back to Yandex device.

### Method link behavior

On value change, module calls linked object method with payload:

```json
{
  "NEW_VALUE": "...",
  "OLD_VALUE": "...",
  "DEVICE_STATE": 1,
  "UPDATED": "timestamp",
  "MODULE": "YandexDevices"
}
```

---

## SAY and TTS

Action:

```text
say(message, level=0, args=None)
```

Filtering logic:

- station must have `tts` enabled;
- station must have `min_level`;
- `level` must be >= `min_level`.

Modes:

- `tts = 1` (`Local`) - currently marked in UI as not working.
- `tts = 2` (`Cloud`) - uses Yandex scenario-based server action.

Long cloud messages are split into short sentences and sent sequentially.

---

## Settings

Global settings in modal:

| Setting | Meaning |
| --- | --- |
| `Enable get device data` | Enables periodic polling in cyclic task |
| `Default update period device data (seconds)` | Default interval for devices without per-device value |
| `Update only linked devices` | Poll only devices that have at least one linked capability |

---

## Widget

Module exposes `widget` action and template showing:

- number of stations;
- number of devices.

---

## Troubleshooting

### Authorization remains `Not authorized`

Check:

- QR flow was fully completed with `Continue`;
- cookie/token were not reset;
- server can reach Yandex endpoints.

### Devices are listed but values do not update

Check:

- `Enable get device data` is enabled;
- per-device `Update period` is valid;
- account still authorized.

### Property/method links do not react

Check:

- link points to existing object/property or object/method;
- device was polled after saving links;
- capability actually provides a changing value.

### Reverse control does not work

Check:

- capability is not marked `Readonly`;
- capability type supports outgoing action semantics in current implementation.

---

## See Also

- [Technical Reference](TECHNICAL_REFERENCE.md)
- [Module index](index.md)
