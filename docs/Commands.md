# Команды Glagol из методов объектов

Обзор модуля и LAN — [index.ru.md](index.ru.md), [USER_GUIDE.ru.md](USER_GUIDE.ru.md#lan-glagol-локальное-управление), [TECHNICAL_REFERENCE.ru.md](TECHNICAL_REFERENCE.ru.md#lan-glagol-keepalive).

Команды в колонку по LAN из методов и сценариев osysHome:

```python
from app.core.lib.common import callPluginFunction

result = callPluginFunction("YandexDevices", "glagol_command", { ... })
```

`callPluginFunction` возвращает **словарь** от `glagol_command` или `None`, если плагин не загружен или вызов упал с исключением.

---

## Привязка станции к объекту

1. Админка **Yandex Devices** → станция.
2. **LAN IP**, **токен устройства**.
3. Поле **объект osysHome** (`glagol_linked_object`) — имя объекта, куда пишется снимок с колонки.

### Параметр `object` и `self.name`

`"object": self.name` — **имя объекта** osysHome (строка из дерева объектов), например `SmartHome.KitchenSpeaker`. Это **не** id записи в БД.

В карточке станции в `glagol_linked_object` должно быть **то же имя**. Иначе: `error: station not found`.

| Параметр | Что это |
|----------|---------|
| `object` / `object_name` | Имя объекта = `glagol_linked_object` станции |
| `station_id` | Числовой `id` станции в БД плагина |
| `station` / `station_title` | Название колонки (`title`, напр. «Кухня») |

```python
callPluginFunction("YandexDevices", "glagol_command", {
    "object": self.name,
    "text": "Включи свет",
})
```

---

## Метод `glagol_command`

Вызывается только через `callPluginFunction("YandexDevices", "glagol_command", kwargs)`.

### Параметры

| Параметр | Назначение |
|----------|------------|
| `text` | Текст для отправки. Если `tts` не задан или `false` — голосовая команда Алисе (`sendText`). Если `tts=True` — произносится голосом (`repeat_phrase`). |
| `tts` | `true` — использовать сценарий `repeat_phrase` (TTS с поддержкой SSML `<speaker voice="...">`). `false` / не указан — `sendText` (команда Алисе). |
| `action` | Управление плеером (таблица ниже). |
| `source` | Необязательно, для логов. |

### Что умеет `glagol_command` (кратко)

| Класс | Параметр | Glagol | Назначение |
|-------|----------|--------|------------|
| TTS (озвучить) | `text` + `tts: true` | `serverAction` → `repeat_phrase` | Произнести текст вслух, поддержка SSML `<speaker voice="...">` |
| Команда Алисе | `text` | `sendText` | Отправить фразу Алисе как голосовой запрос |
| Плеер | `action` + поля | см. [Управление плеером](#управление-плеером) | Транспорт, громкость, repeat/shuffle, запуск по id |

Админка станции (блок «Плеер») шлёт те же `action`, что и `glagol_command` — POST `/admin/.../station/<id>/glagol`. Разовый снимок плеера — GET на тот же URL.

Низкоуровневые поля `payload` — в `glagol_local.py`.

---

### `text` — команда Алисе (`sendText`)

Без `tts=true` текст отправляется как голосовая команда Алисе (через `sendText`).

| Возможность | Поддержка |
|-------------|-----------|
| Команды умного дома / сценарии Алисы | Да, фраза как в голосе |
| Музыка без id | Да: «включи джаз», «поставь мой плейлист «Дорога»» |
| Таймер / будильник | Да фразой: «таймер на 5 минут», «будильник в 7:00» |
| Разметка голоса SSML | **Нет** — для SSML используйте `tts: true` |
| Лимит | ~2000 символов |

### `text` + `tts: true` — TTS с SSML (`repeat_phrase`)

С `tts: true` текст произносится голосом Алисы через сценарий `repeat_phrase` (аналог AlexxIT/YandexStation). Поддерживает SSML-разметку Яндекса.

| Возможность | Поддержка |
|-------------|-----------|
| Дословное произнесение текста | Да |
| Смена голоса | Да: `<speaker voice="zahar">…`, `<speaker voice="oksana">…` |
| Эффекты | Да: `<speaker effect="megaphone">…` |
| Команды Алисе | **Нет** — для команд используйте `text` без `tts` |
| Лимит | ~2000 символов |

---

## Управление плеером

Параметр **`action`** — только команды **текущего медиаплеера** колонки (Яндекс.Музыка, радио, поток и т.д.). Отдельных `action` для TV-экранов или мультирума нет.

### Команды `action`

| `action` | Параметры | Что делает на колонке |
|----------|-----------|------------------------|
| `play` | — | Старт / продолжить воспроизведение **того, что уже в очереди** (после паузы) |
| `pause`, `stop` | — | Остановка (оба → Glagol `stop`) |
| `next`, `prev` | — | Следующий / предыдущий трек в очереди |
| `volume` | `volume` 0…1 | Абсолютная громкость (`setVolume`, шаг 0.1) |
| `seek` | `position` (сек) | Перемотка **текущего** трека (`rewind`) |
| `repeat` | `repeat_mode` | Режим повтора: `None`, `All`, `One` |
| `shuffle` | `shuffle` true/false | Перемешивание очереди |
| `play_music` | `music_id`, `music_type` | Запуск сущности каталога Яндекс.Музыки |

Неизвестный `action` → в `detail` будет `unknown action`.

### Что умеет **включать**

| Способ | Что запускается | Нужны id |
|--------|-----------------|----------|
| `play` | Продолжить после паузы / возобновить текущую очередь | Нет |
| `play_music` | Конкретный **трек**, **альбом** или **плейлист** каталога | Да: `music_id` + `music_type` |
| `text` (без `tts`) | Поиск Алисой: исполнитель, жанр, «моя волна», радио, название | Нет |
| `text` + `tts: true` | TTS с поддержкой SSML (смена голоса, эффекты) | Нет |
| `next` / `prev` | Переключение в уже играющей очереди | Нет |

`play` **сам по себе** новый альбом не откроет — только `play_music` или `text`.

#### `play_music`: типы и id

Glagol: `{"command": "playMusic", "id": "<строка>", "type": "<тип>"}`.

| `music_type` | Сущность | Пример `music_id` |
|--------------|----------|-------------------|
| `track` | Один трек | `36812345` |
| `album` | Альбом целиком | `60062` |
| `playlist` | Плейлист | id плейлиста в каталоге |

Оба поля обязательны. Id — **числовой идентификатор каталога Яндекс.Музыки** (строка), не URL и не название.

```python
callPluginFunction("YandexDevices", "glagol_command", {
    "object": self.name,
    "action": "play_music",
    "music_id": "60062",
    "music_type": "album",
})
```

### Как узнать `music_id` и `music_type`

**1. Снять с уже играющего трека (надёжнее всего)**

1. Включите нужный трек на колонке (голосом или приложением Яндекс).
2. Админка → станция → «Плеер» → **Обновить** (или дождитесь live-снимка).
3. GET `/admin/YandexDevices/station/<station_id>/glagol` возвращает JSON; в блоке `player`:

```json
"player": {
  "id": "36812345",
  "type": "track",
  "title": "…",
  "subtitle": "…",
  "playlist_id": "…",
  "playlist_type": "…"
}
```

Для `play_music` используйте **`player.id`** → `music_id` и **`player.type`** → `music_type` (обычно `track`).

`playlist_id` / `playlist_type` — контекст очереди (часто альбом или плейлист-родитель); для `play_music` подставляйте их только если хотите запустить **весь** альбом/плейлист, а не один трек.

**2. Из ссылки Яндекс.Музыки**

В URL веб-версии или приложения часто есть id:

- `…/album/60062` → `music_type`: `album`, `music_id`: `60062`
- `…/track/36812345` → `track` / `36812345`
- `…/playlists/1234` → `playlist` / `1234`

Формат ссылок может меняться; при сомнении — способ 1.

**3. Без id — через `text`**

```python
callPluginFunction("YandexDevices", "glagol_command", {
    "object": self.name,
    "text": "Включи альбом Кино «Группа крови»",
})
```

Алиса подберёт контент сама; id для сценария не нужен.

**4. В админке**

Блок «Запуск трека по id» на карточке станции — те же `music_id` / `music_type`, что уходит в `play_music`.

### Снимок плеера: что приходит и что пишется в объект

Полный JSON снимка (GET или push `glagol_snapshot`) содержит, в частности:

| Поле | Смысл |
|------|--------|
| `playing` | `true` / `false` — идёт ли воспроизведение |
| `volume`, `muted` | Громкость 0…1 |
| `alice_state` | Состояние Алисы (слушает, говорит, …) |
| `player.title`, `player.subtitle` | Название и исполнитель |
| `player.id`, `player.type` | Для `play_music` |
| `player.duration_sec`, `player.progress_sec` | Длительность и позиция |
| `player.cover_url` | Обложка |
| `player.repeat_mode`, `player.shuffled` | Repeat / shuffle (если колонка отдаёт) |
| `player.has_next`, `player.has_prev`, … | Доступность кнопок (в UI админки) |
| `raw_state` | Сырой `state` из Glagol (отладка) |

В объект osysHome (`glagol_linked_object`) плагин пишет **укороченный** набор: `state`, `volume`, `muted`, `alice_state`, `media_title`, `media_subtitle`, `media_duration`, `media_progress`, `media_cover_url`. **`media_id` / тип трека в объект не публикуются** — для автоматизации id берите из GET снимка или храните в своём сценарии после первого опроса.

### Типичные сценарии

```python
# Пауза / продолжить
callPluginFunction("YandexDevices", "glagol_command", {"object": self.name, "action": "pause"})
callPluginFunction("YandexDevices", "glagol_command", {"object": self.name, "action": "play"})

# Громкость 30%
callPluginFunction("YandexDevices", "glagol_command", {
    "object": self.name, "action": "volume", "volume": 0.3,
})

# Повтор альбома и shuffle
callPluginFunction("YandexDevices", "glagol_command", {
    "object": self.name, "action": "repeat", "repeat_mode": "All",
})
callPluginFunction("YandexDevices", "glagol_command", {
    "object": self.name, "action": "shuffle", "shuffle": True,
})

# Перемотка на 90-ю секунду текущего трека
callPluginFunction("YandexDevices", "glagol_command", {
    "object": self.name, "action": "seek", "position": 90,
})
```

Метод объекта: сохранить id с играющего трека и потом снова включить:

```python
from app.core.lib.common import callPluginFunction

def playFavoriteAlbum(self, params):
    # id альбома: params[0], админка «Запуск трека по id» или player.id из снимка
    album_id = (params[0] if params else "60062").strip()
    r = callPluginFunction("YandexDevices", "glagol_command", {
        "object": self.name,
        "action": "play_music",
        "music_id": album_id,
        "music_type": "album",
    })
    return "ok" if r and r.get("ok") else str(r)
```

---

### Ответ

```python
{
    "ok": True,
    "station_id": 8,
    "station_title": "ТВ Станция Бейсик",
    "action": "sendText",   # или repeat_phrase, play, volume, …
    "host": "192.168.0.145",
    "port": 1961,
    "response": { ... },    # если колонка прислала JSON
    "error": "...",         # при ok == False
    "detail": "..."         # пояснение, см. ниже
}
```

### Поле `ok` и sendText

Колонка **часто выполняет** `sendText`, но **не отвечает** кадром с `status: "ok"` и тем же `requestId`.

| Ситуация | `ok` | `detail` |
|----------|------|----------|
| Ответ `status: ok` | `true` | — |
| Таймаут ack, команда уже отправлена | `true` | `timeout waiting for response` |
| Явная ошибка в ответе | `false` | тело ответа / `error` |
| Нет связи с колонкой | `false` | `no response`, текст ошибки TCP |

**Важно:** `ok: true` с `detail: timeout waiting for response` — нормальная ситуация для команд Алисе; колонка уже получила фразу.

Проверка в методе объекта:

```python
r = callPluginFunction("YandexDevices", "glagol_command", {
    "object": self.name,
    "text": phrase,
})
if not r:
    return "вызов плагина не удался"
if not r.get("ok"):
    return f"ошибка: {r.get('error')} — {r.get('detail')}"
# ok True, даже если detail про timeout — команда считается отправленной
return "ok"
```

---

## Примеры

### TTS (произнести текст)

```python
callPluginFunction("YandexDevices", "glagol_command", {
    "object": self.name,
    "text": "Температура в комнате двадцать три градуса",
    "tts": True,
})
```

С SSML-разметкой (смена голоса, эффекты):

```python
callPluginFunction("YandexDevices", "glagol_command", {
    "object": self.name,
    "text": '<speaker voice="zahar">Внимание! Важное сообщение',
    "tts": True,
})

callPluginFunction("YandexDevices", "glagol_command", {
    "object": self.name,
    "text": '<speaker effect="megaphone">Пожарная тревога!',
    "tts": True,
})
```

### Выполнение команд станцией

Фраза уходит в Алису как **запрос** (не дословное TTS): «включи…», «какая погода».

```python
callPluginFunction("YandexDevices", "glagol_command", {
    "object": self.name,
    "text": "Включи мою любимую музыку вперемешку",
})

callPluginFunction("YandexDevices", "glagol_command", {
    "station": "Кухня",
    "text": "Какая погода на улице",
})
```

Метод объекта с фразой из `params`:

```python
from app.core.lib.common import callPluginFunction

def runAliceCommand(self, params):
    phrase = (params[0] if params else "").strip()
    if not phrase:
        return "укажите фразу в params[0]"
    r = callPluginFunction("YandexDevices", "glagol_command", {
        "object": self.name,
        "text": phrase,
    })
    if not r or not r.get("ok"):
        err = r.get("error") if r else "нет ответа"
        det = r.get("detail") if r else ""
        return f"не выполнено: {err}" + (f" ({det})" if det else "")
    return "ok"
```

### Плеер (кратко)

См. раздел [Управление плеером](#управление-плеером): транспорт, `play_music`, откуда брать id.

---

## `say` vs `glagol_command`

| | `say` (action плагина) | `glagol_command` |
|--|------------------------|------------------|
| Вызов | `say("текст", level, {"station": "…"})` | `callPluginFunction(...)` |
| Режим | По `tts` станции: облако или LAN | Только LAN (Glagol) |
| Префикс | Для локального TTS добавляет «повтори за мной» | Без `tts=true`: как есть. С `tts=true`: сценарий `repeat_phrase` |
| SSML `<speaker>` | Нет (теги вырезаются) | Да, с `tts: true` |
| Когда использовать | Озвучивание по level/min_level | Точные команды плеера, TTS с SSML, Алиса из методов объекта |

---

## Фоновый поток (без вызова из метода)

`glagol_keepalive` при IP + токене держит WebSocket и обновляет свойства объекта (`glagol_linked_object`).

Исходящие команды из методов — **отдельное** короткое соединение (как кнопки «Плеер» в админке).

Публикуемые свойства (основной набор): `state`, `volume`, `muted`, `alice_state`, `media_title`, `media_subtitle`, `media_duration`, `media_progress`, `media_cover_url`.

Id трека/альбома для `play_music` в объект **не** пишутся — только в полном снимке GET / push (поля `player.id`, `player.type`).

---

## Сводная таблица: что вызывать

| Задача | Вызов |
|--------|--------|
| Озвучить текст | `{"object": "…", "text": "…", "tts": true}` |
| Озвучить с SSML / смена голоса | `{"object": "…", "text": '<speaker voice="zahar">…', "tts": true}` |
| Команда Алисе / умный дом | `{"object": "…", "text": "включи свет"}` |
| Музыка без id | `{"object": "…", "text": "включи джаз"}` |
| Play / pause / next | `{"object": "…", "action": "play"}` … |
| Громкость 50% | `{"action": "volume", "volume": 0.5}` |
| Альбом / трек по id | `{"action": "play_music", "music_id": "…", "music_type": "album\|track\|playlist"}` |
| Узнать id играющего | GET снимка плеера → `player.id`, `player.type` |
| Таймер / будильник | `{"text": "поставь таймер на 10 минут"}` |

---

## Ошибки

| `error` | Причина |
|---------|---------|
| `station not found` | Нет станции с таким `object` / `station_id` / `title` |
| `no IP` | Не задан LAN-IP |
| `no device token` | Нет токена — сформировать в админке |
| `no response` | Колонка не ответила на `action` (сеть, порт 1961) |
| `sendText not acknowledged` | Ответ пришёл, но не успех (редко для sendText) |
| `repeat_phrase not acknowledged` | Ответ пришёл, но не успех (редко для repeat_phrase) |
| `command not acknowledged` | Ответ плеера без успешного status |
| `action or text required` | Не передан ни `text`, ни `action` |
| `unknown action` (в `detail`) | `action` не из списка плеера (см. таблицу `action`) |
| `volume required` / `position required` / … | Не передан обязательный параметр для `action` |
