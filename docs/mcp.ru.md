# MCP — YandexDevices

Плагин синхронизирует станции и IoT-устройства Yandex Quasar, опрашивает capabilities и привязывает их к свойствам объектов osysHome. Локальное управление колонками — через Glagol (LAN).

## Plugin notes

- Авторизация (cookie) выполняется в админке; статус — `get_connection_status`.
- Не создавайте станции/устройства/capabilities вручную: `sync_*` / `refresh_device`.
- Станции: LAN `ip`, `tts` (0/1/2), `min_level`, `glagol_linked_object`.
- `device_token` выдаётся только через `generate_device_token` (в list/get — `has_device_token`).
- Устройства: writable только `update_period`.
- Capabilities: upsert для `read_only` + привязок; `read_only=false` включает reverse link.
- `say` говорит на станциях с TTS при `level >= min_level`.
- LAN: `glagol_command` (`station_id` / `object` / `station` + `text` или `action`).

## Collections

| ID | binding_mode | writable | writable_fields | list_filters |
|----|--------------|----------|-----------------|--------------|
| `stations` | `object` | yes (no create) | `title`, `ip`, `tts`, `min_level`, `glagol_linked_object` | `query`, `online`, `glagol_linked_object`, `has_glagol_link` |
| `devices` | `none` | yes (no create) | `update_period` | `query`, `room`, `device_type` |
| `capabilities` | `property` | yes (no create) | `read_only`, `linked_object`, `linked_property`, `linked_method` | `query`, `device_id`, `linked_object`, `has_binding`, `read_only` |

### Поля entity (stations)

| поле | writable | описание |
|------|----------|----------|
| `title` | да | Имя станции |
| `ip` | да | LAN IP / host:port для Glagol |
| `tts` | да | `0` off, `1` local Glagol, `2` cloud |
| `min_level` | да | Порог для `say` (число или `Object.property`) |
| `glagol_linked_object` | да | Объект osysHome для статуса плеера |
| `has_device_token` | no | Есть ли сохранённый Glagol-токен |
| `platform`, `station_id`, `iot_id`, `online` | no | Из sync |

### Поля entity (capabilities)

| поле | writable | описание |
|------|----------|----------|
| `title` | no | Ключ capability/property из Quasar |
| `value` | no | Последнее значение |
| `read_only` | да | inbound-only vs reverse link |
| `linked_object` / `linked_property` / `linked_method` | да | Привязки |
| `device_id` | no | Parent device id |

## Операции (invoke)

| operation | Описание |
|-----------|----------|
| `sync_devices` | Список IoT-устройств из Quasar |
| `sync_stations` | Станции + sync Glagol registry |
| `say` | TTS: `message`, опционально `level`, `args.station` |
| `get_connection_status` | cookie / IoT / Glagol / счётчики |
| `refresh_device` | Опрос capabilities одного устройства |
| `generate_device_token` | Обновить Glagol `device_token` |
| `get_capability_value` | Кэш capability (`capability_id` или `device_id`+`title`) |
| `glagol_command` | LAN команда (`text` или `action`) |

## Промпты

| name | Назначение |
|------|------------|
| `osys_yandexdevices_entity_authoring` | Собрать payload по схеме |
| `osys_yandexdevices_binding` | Привязать `object.property` к capability |

## Примеры

### Статус и sync

```json
{
  "plugin": "YandexDevices",
  "action": "invoke",
  "args": { "operation": "get_connection_status", "params": {} }
}
```

```json
{
  "plugin": "YandexDevices",
  "action": "invoke",
  "args": { "operation": "sync_stations", "params": {} }
}
```

### Настроить станцию (Glagol)

```json
{
  "plugin": "YandexDevices",
  "action": "upsert_entity",
  "args": {
    "collection": "stations",
    "entity_id": 1,
    "payload": {
      "ip": "192.168.1.50",
      "tts": 1,
      "min_level": "0",
      "glagol_linked_object": "Station.Kitchen"
    }
  }
}
```

```json
{
  "plugin": "YandexDevices",
  "action": "invoke",
  "args": {
    "operation": "generate_device_token",
    "params": { "station_id": 1 }
  }
}
```

### Привязать capability

```json
{
  "plugin": "YandexDevices",
  "action": "list_entities",
  "args": { "collection": "capabilities", "device_id": 3, "limit": 100 }
}
```

```json
{
  "plugin": "YandexDevices",
  "action": "upsert_entity",
  "args": {
    "collection": "capabilities",
    "entity_id": 42,
    "payload": {
      "linked_object": "Lamp.Living",
      "linked_property": "status",
      "read_only": false
    }
  }
}
```

### SAY и Glagol

```json
{
  "plugin": "YandexDevices",
  "action": "invoke",
  "args": {
    "operation": "say",
    "params": { "message": "Привет", "level": 1 }
  }
}
```

```json
{
  "plugin": "YandexDevices",
  "action": "invoke",
  "args": {
    "operation": "glagol_command",
    "params": {
      "object": "Station.Kitchen",
      "text": "Включи свет на кухне"
    }
  }
}
```

## Config schema

| ключ | тип | описание |
|------|-----|----------|
| `get_device_data` | bool | Циклический опрос устройств |
| `update_period` | int | Период опроса по умолчанию (сек) |
| `update_linked` | bool | Опрашивать только устройства с привязками |
