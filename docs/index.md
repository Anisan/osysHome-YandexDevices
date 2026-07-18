# YandexDevices

Plugin for Yandex Quasar IoT devices and **local Glagol** control of Yandex Stations (LAN WebSocket, port 1961).

The documentation is split into:

- [User Guide](USER_GUIDE.md) — admin UI, authorization, links, LAN setup
- [Technical Reference](TECHNICAL_REFERENCE.md) — data model, routes, polling, Glagol internals
- [Commands](Commands.md) — `glagol_command` from object methods via `callPluginFunction`
- [MCP (RU)](mcp.ru.md) — collections, invoke operations, bindings for agents

## What to open

If you need to connect Yandex account access, sync stations/devices, and configure links from the admin UI, start with [User Guide](USER_GUIDE.md).

If you need implementation details, data model, endpoint behavior, polling logic, and TTS scenario internals, open [Technical Reference](TECHNICAL_REFERENCE.md).

## Quick links

- [Jump to quick start checklist](USER_GUIDE.md#quick-start-checklist)
- [Jump to LAN Glagol setup](USER_GUIDE.md#lan-glagol-local-control)
- [Jump to Glagol commands from objects](Commands.md)
- [Jump to capabilities and links](USER_GUIDE.md#how-capability-links-work)
- [Jump to admin and HTTP routes](TECHNICAL_REFERENCE.md#admin-and-http-routes)
- [Jump to Glagol keepalive and WebSocket](TECHNICAL_REFERENCE.md#lan-glagol-keepalive)
