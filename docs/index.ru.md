# YandexDevices

Плагин для устройств Yandex Quasar IoT и **локального управления колонками Glagol** (LAN WebSocket, порт 1961).

Документация:

- [Руководство пользователя](USER_GUIDE.ru.md) — админка, авторизация, привязки, LAN
- [Техническая документация](TECHNICAL_REFERENCE.ru.md) — модель данных, маршруты, опрос, Glagol
- [Команды](Commands.md) — `glagol_command` из методов объектов через `callPluginFunction`
- [MCP](mcp.ru.md) — коллекции, операции invoke, привязки для агентов

## Что открывать

Если нужно авторизоваться в Yandex, обновить станции/устройства и настроить привязки через админку, начните с [руководства пользователя](USER_GUIDE.ru.md).

Если нужны детали реализации, модель данных, поведение маршрутов, логика опроса и устройство TTS-сценариев, откройте [техническую документацию](TECHNICAL_REFERENCE.ru.md).

## Быстрые ссылки

- [Чек-лист запуска](USER_GUIDE.ru.md#чек-лист-быстрого-запуска)
- [LAN Glagol](USER_GUIDE.ru.md#lan-glagol-локальное-управление)
- [Команды Glagol из объектов](Commands.md)
- [Привязки capability](USER_GUIDE.ru.md#как-работают-привязки-capability)
- [Маршруты и HTTP](TECHNICAL_REFERENCE.ru.md#админ-операции-и-http-маршруты)
- [Фоновый Glagol и WebSocket](TECHNICAL_REFERENCE.ru.md#lan-glagol-keepalive)
