"""
Плагин YandexDevices: колонки и устройства Яндекс.Quasar, локальный Glagol (LAN).

Документация:
  - plugins/YandexDevices/docs/index.ru.md — оглавление документации
  - plugins/YandexDevices/docs/Commands.md — glagol_command из методов объектов
"""
import datetime
import os
import re
import threading
import time
from collections import defaultdict
from flask import redirect, request, jsonify, render_template, url_for
from app.authentication.handlers import handle_admin_required
from plugins.YandexDevices.models.YaDevices import YaDevices
from plugins.YandexDevices.models.YaStation import YaStation
from plugins.YandexDevices.models.YaCapabilities import YaCapabilities
from app.core.main.BasePlugin import BasePlugin
from app.core.lib.object import setProperty, getProperty, callMethod, setLinkToObject, removeLinkFromObject, updateProperty
from app.core.lib.cache import deleteFromCache, getCacheDir
from plugins.YandexDevices.forms.SettingForms import SettingsForm
from app.database import session_scope, row2dict, get_now_to_utc
from plugins.YandexDevices.QuazarApi import QuazarApi
from time import sleep
from sqlalchemy import and_, select, distinct
from typing import Any, Dict, Optional

class YandexDevices(BasePlugin):

    def __init__(self,app):
        super().__init__(app,__name__)
        self.name = "YandexDevices"
        self.title = "Yandex Devices"
        self.description = (
            "Колонки и устройства Яндекса (Quasar + LAN Glagol). "
            "Документация: plugins/YandexDevices/docs/index.ru.md"
        )
        self.actions = ["cycle","say","widget"]
        self.category = "Devices"
        self.version = 0.22
        self.author = 'Eraser'

    def initialization(self):
        cache_dir = os.path.join(getCacheDir(), self.name)
        os.makedirs(cache_dir, exist_ok=True)
        self.quazar = QuazarApi(cache_dir, self.logger, user_notify=self._quazar_user_notify)
        from plugins.YandexDevices.glagol_keepalive import GlagolWsRegistry

        self._glagol_registry = GlagolWsRegistry(self)
        self._glagol_registry.sync_stations()

    def stop_cycle(self):
        reg = getattr(self, "_glagol_registry", None)
        if reg is not None:
            reg.shutdown()
        super().stop_cycle()

    def _quazar_user_notify(self, title: str, description: str = "", level: str = "warning"):
        """Уведомление в интерфейсе (лентa уведомлений) при проблемах с Яндексом."""
        from app.core.lib.common import addNotify
        from app.core.lib.constants import CategoryNotify

        cat = CategoryNotify.Error if level == "error" else CategoryNotify.Warning
        addNotify(title, description, cat, source=self.name)

    def admin(self, request):
        op = request.args.get('op', '')
        tab = request.args.get('tab', '')
        station = request.args.get('station',None)
        device = request.args.get('device',None)

        if op == 'auth':
            auth = False
            type = request.args.get('type', '')
            track_id = request.args.get('track_id', '')
            csrf_token = request.args.get('csrf_token', '')
            if type == 'qr':
                if track_id:
                    out = self.quazar.confirmQrCode(track_id,csrf_token)
                    return self.render('yandexdevices_auth.html', out)

                else:
                    out = self.quazar.getQrCode()
                    return self.render('yandexdevices_auth.html', out)

            if type == 'reset':
                deleteFromCache("cookie",self.name)
            # check authorized
            data = self.quazar.api_request('https://iot.quasar.yandex.ru/m/user/devices')
            if data:
                auth = True
            content = {
                "AUTHORIZED": auth,
            }
            return self.render('yandexdevices_auth.html', content)

        if op == 'update':
            self.refresh_stations()
            self.update_devices()
            return redirect("YandexDevices")

        if op == "generate_dev_token":
            sid = request.args.get("id", None)
            if not sid:
                return redirect("YandexDevices")
            with session_scope() as session:
                station = session.query(YaStation).filter(YaStation.id == sid).one_or_none()
                if not station:
                    self.logger.warning("generate_dev_token: станция id=%s не найдена", sid)
                    return redirect("YandexDevices")
                if not (station.iot_id and station.platform):
                    self.logger.warning(
                        "generate_dev_token: у станции «%s» нет iot_id/platform — сначала Update в списке станций",
                        station.title,
                    )
                    return redirect(f"?station={sid}&op=edit")
                token = self.quazar.get_device_token(station.iot_id, station.platform)
                if token:
                    station.device_token = token
                    session.commit()
                    self.logger.info(
                        "Glagol: токен обновлён для станции id=%s «%s»",
                        sid,
                        station.title,
                    )
                    reg = getattr(self, "_glagol_registry", None)
                    if reg is not None:
                        reg.sync_stations()
                else:
                    self.logger.warning(
                        "generate_dev_token: Яндекс не выдал токен для «%s» — проверьте Authorization",
                        station.title,
                    )
            return redirect(f"?station={sid}&op=edit")

        if op == 'edit':
            if device:
                return render_template("yandexdevices_device.html", id=device)
            if station:
                from plugins.YandexDevices.forms.StationForm import editStation
                return editStation(request, self)
            
        if op == 'delete':
            if device:
                with session_scope() as session:
                    session.query(YaDevices).filter(YaDevices.id == device).delete(synchronize_session=False)
                    session.commit()
            if station:
                with session_scope() as session:
                    session.query(YaStation).filter(YaStation.id == station).delete(synchronize_session=False)
                    session.commit()

        settings = SettingsForm()
        if request.method == 'GET':
            settings.get_data.data = self.config.get('get_device_data',False)
            settings.update_period.data = self.config.get('update_period',60)
            settings.update_linked.data = self.config.get('update_linked',True)
        else:
            if settings.validate_on_submit():
                self.config["get_device_data"] = settings.get_data.data
                self.config["update_linked"] = settings.update_linked.data
                self.saveConfig()

        if tab == 'devices':
            def _format_capability_target(cap):
                lo = (cap.linked_object or "").strip()
                if not lo:
                    return None
                t = lo
                lp = (cap.linked_property or "").strip()
                if lp:
                    t = f"{t}.{lp}"
                lm = cap.linked_method or ""
                if lm:
                    t = f"{t}{lm}"
                return t

            with session_scope() as session:
                devices_rows = session.query(YaDevices).all()
                devices = [row2dict(device) for device in devices_rows]
                dev_ids = [d["id"] for d in devices]
                links_by_device = defaultdict(list)
                if dev_ids:
                    caps = (
                        session.query(YaCapabilities)
                        .filter(YaCapabilities.device_id.in_(dev_ids))
                        .order_by(YaCapabilities.device_id, YaCapabilities.title)
                    )
                    for cap in caps:
                        target = _format_capability_target(cap)
                        if target is None:
                            continue
                        links_by_device[cap.device_id].append(
                            {"title": cap.title or "", "target": target}
                        )
                for d in devices:
                    d["capability_links"] = links_by_device[d["id"]]
            content = {
                "devices": devices,
                "tab": tab,
                'form': settings,
            }
            return self.render('yandexdevices_devices.html', content)

        stations = YaStation.query.all()
        stations = [row2dict(station) for station in stations]
        glagol_lan_by_id: dict = {}
        glagol_ws_status_url = ""
        for ep in self._app.view_functions:
            if str(ep).endswith("yandexdevices_glagol_ws_status"):
                try:
                    glagol_ws_status_url = url_for(ep)
                except Exception:
                    glagol_ws_status_url = ""
                break
        reg = getattr(self, "_glagol_registry", None)
        if reg is not None:
            for s in stations:
                sid = s.get("id")
                if sid is not None:
                    try:
                        glagol_lan_by_id[int(sid)] = reg.get_station_status(int(sid))
                    except (TypeError, ValueError):
                        pass
        content = {
            'stations': stations,
            "tab": tab,
            'form': settings,
            "glagol_lan_by_id": glagol_lan_by_id,
            "glagol_ws_status_url": glagol_ws_status_url,
        }
        return self.render('yandexdevices_stations.html', content)

    def route_index(self):
        _admin = self.name.split(".")[-1]

        @self.blueprint.route('/YandexDevices/device', methods=['POST'])
        @self.blueprint.route('/YandexDevices/device/<device_id>', methods=['GET', 'POST'])
        @handle_admin_required
        def point_yandex_device(device_id=None):
            with session_scope() as session:
                if request.method == "GET":
                    dev = session.query(YaDevices).filter(YaDevices.id == device_id).one()
                    device = row2dict(dev)
                    device['props'] = []
                    props = session.query(YaCapabilities).filter(YaCapabilities.device_id == device_id).order_by(YaCapabilities.title)
                    for prop in props:
                        item = row2dict(prop)
                        item['read_only'] = item['read_only'] == 1
                        device['props'].append(item)
                    return jsonify(device)
                if request.method == "POST":
                    data = request.get_json()
                    if data['id']:
                        device = session.query(YaDevices).where(YaDevices.id == int(data['id'])).one()
                    else:
                        device = YaDevices()
                        session.add(device)
                        session.commit()

                    device.update_period = data['update_period']

                    for prop in data['props']:
                        prop_rec = session.query(YaCapabilities).filter(YaCapabilities.device_id == device.id,YaCapabilities.title == prop['title']).one()
                        if prop_rec.linked_object:
                            removeLinkFromObject(prop_rec.linked_object, prop_rec.linked_property, self.name)
                        prop_rec.linked_object = prop['linked_object']
                        prop_rec.linked_property = prop['linked_property']
                        prop_rec.linked_method = prop['linked_method']
                        prop_rec.read_only = 1 if prop['read_only'] else 0
                        if prop_rec.linked_object and prop_rec.read_only == 0:
                            setLinkToObject(prop_rec.linked_object, prop_rec.linked_property, self.name)

                    session.commit()

                    return 'Device updated successfully', 200

        @self.blueprint.route(
            "/admin/" + _admin + "/station/<int:station_id>/glagol",
            methods=["GET", "POST"],
        )
        @handle_admin_required
        def yandexdevices_station_glagol(station_id: int):
            from plugins.YandexDevices.glagol_local import (
                glagol_player_command,
                glagol_snapshot,
                parse_host_port,
            )

            with session_scope() as session:
                st = session.query(YaStation).filter(YaStation.id == station_id).one_or_none()
                if not st:
                    return jsonify({"ok": False, "error": "station not found"}), 404
                station_ip = st.ip or ""
                station_iot_id = st.iot_id
                station_platform = st.platform
                station_device_token = st.device_token
                station_db_id = int(st.id)

            host, port = parse_host_port(station_ip)
            if not host:
                return jsonify({"ok": False, "error": "no IP"}), 400

            token = self._ensure_device_token(
                station_db_id,
                station_iot_id,
                station_platform,
                station_device_token,
                refresh_if_expired=True,
            )
            if not token:
                return jsonify({"ok": False, "error": "no device token"}), 400

            if request.method == "GET":
                reg = getattr(self, "_glagol_registry", None)
                if reg is not None:
                    cached = reg.get_station_snapshot(station_db_id)
                    if cached:
                        return jsonify(cached)

                snap, conn_detail = glagol_snapshot(host, port, token, self.logger)
                if snap is None:
                    hint = ""
                    dlow = (conn_detail or "").lower()
                    if "10061" in (conn_detail or "") or "отверг" in (conn_detail or "") or "refused" in dlow:
                        hint = (
                            "На TCP-порту 1961 колонка не отвечает: проверьте IP в LAN, "
                            "что сервер и колонка в одной сети (без изоляции клиентов Wi‑Fi), "
                            "в приложении Яндекс разрешено локальное управление, брандмауэр."
                        )
                    return (
                        jsonify(
                            {
                                "ok": False,
                                "error": "glagol connection failed",
                                "detail": conn_detail,
                                "hint": hint or None,
                            }
                        ),
                        502,
                    )
                return jsonify({"ok": True, "source": "poll", **snap})

            body = request.get_json(silent=True) or {}
            text_raw = body.get("text")
            if text_raw is not None and str(text_raw).strip():
                from plugins.YandexDevices.glagol_local import glagol_send_text

                phrase = self._sanitize_tts_text(str(text_raw), 2000)
                send_fn = self._glagol_lan_send_fn(station_db_id, host, port, token)
                ok, resp, detail = glagol_send_text(
                    host, port, token, phrase, self.logger, send_fn=send_fn
                )
                out: Dict[str, Any] = {"ok": bool(ok), "action": "sendText"}
                if resp is not None:
                    out["response"] = resp
                if detail:
                    out["detail"] = detail
                if not ok and resp is not None:
                    out["error"] = "sendText not acknowledged"
                return jsonify(out), (200 if ok else 400)

            action = (body.get("action") or "").strip()
            if not action:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": "action or text required",
                            "detail": "Передайте text (sendText) или action (плеер).",
                        }
                    ),
                    400,
                )
            kw: dict = {}
            if "volume" in body and body.get("volume") is not None:
                kw["volume"] = float(body["volume"])
            if "position" in body and body.get("position") is not None:
                kw["position"] = float(body["position"])
            if body.get("repeat_mode") is not None:
                kw["repeat_mode"] = body.get("repeat_mode")
            if "shuffle" in body:
                kw["shuffle"] = bool(body.get("shuffle"))
            if body.get("music_id") is not None:
                kw["music_id"] = body.get("music_id")
            if body.get("music_type") is not None:
                kw["music_type"] = body.get("music_type")

            send_fn = self._glagol_lan_send_fn(station_db_id, host, port, token)
            resp, cmd_err = glagol_player_command(
                host, port, token, self.logger, action, send_fn=send_fn, **kw
            )
            if not resp:
                hint = ""
                d = cmd_err or ""
                dlow = d.lower()
                if "10061" in d or "отверг" in d or "refused" in dlow:
                    hint = (
                        "На TCP-порту 1961 колонка не отвечает: проверьте IP в LAN, "
                        "что сервер и колонка в одной сети (без изоляции клиентов Wi‑Fi), "
                        "в приложении Яндекс разрешено локальное управление, брандмауэр."
                    )
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": "no response",
                            "detail": cmd_err,
                            "hint": hint or None,
                        }
                    ),
                    502,
                )
            from plugins.YandexDevices.glagol_local import _glagol_response_ok

            ok = _glagol_response_ok(resp)
            out = {"ok": ok, "response": resp, "action": action}
            if not ok:
                out["error"] = "command not acknowledged"
                out["detail"] = str(resp.get("status") or resp)
            return jsonify(out), (200 if ok else 400)

        @self.blueprint.route(
            "/admin/" + _admin + "/glagol_ws_status",
            methods=["GET"],
        )
        @handle_admin_required
        def yandexdevices_glagol_ws_status():
            reg = getattr(self, "_glagol_registry", None)
            if not reg:
                return jsonify({})
            return jsonify(reg.get_all_status())

    def cyclic_task(self):
        # self.refresh_stations()
        if self.config.get("get_device_data", False):
            self.refresh_devices_data()

        now = time.monotonic()
        if now - getattr(self, "_last_glagol_registry_sync", 0.0) >= 5.0:
            self._last_glagol_registry_sync = now
            reg = getattr(self, "_glagol_registry", None)
            if reg is not None:
                reg.sync_stations()
        self.event.wait(1.0)

    def update_devices(self):
        try:
            data = self.quazar.api_request('https://iot.quasar.yandex.ru/m/user/devices')
            self.logger.debug(data)
            with session_scope() as session:
                for room in data["rooms"]:
                    for device in room["devices"]:
                        quasar_id = None
                        if 'quasar_info' in device:
                            quasar_id = device['quasar_info']['device_id']
                        rec = session.query(YaDevices).filter(YaDevices.iot_id == device['id']).one_or_none()
                        if not rec:
                            rec = YaDevices()
                            rec.iot_id = device['id']
                            session.add(rec)
                        rec.title = device['name']
                        rec.device_type = device['type']
                        rec.room = room['name']
                        rec.icon = device['icon_url']
                        rec.updated = get_now_to_utc()
                        session.commit()

                        # обновление станций
                        rec_station = session.query(YaStation).filter(YaStation.title == device['name']).one_or_none()

                        if not rec_station and quasar_id:
                            rec_station = session.query(YaStation).filter(YaStation.station_id == quasar_id).one_or_none()

                        if rec_station:
                            rec_station.iot_id = device['id']
                            rec_station.updated = get_now_to_utc()
                            session.commit()

        except Exception as ex:
            self.logger.error(ex)

    def refresh_stations(self):
        data = self.quazar.api_request('https://quasar.yandex.ru/devices_online_stats')

        if isinstance(data.get('items'), list):
            items = data['items']
            with session_scope() as session:
                for item in items:
                    if item['platform'] not in ['iot_app_android','iot_app_ios','alice_app_ios']: #remove unused platforms
                        station_id = item['id']
                        rec = session.query(YaStation).filter(YaStation.station_id == station_id).one_or_none()

                        if not rec:
                            rec = YaStation()
                            rec.station_id = item['id']
                            session.add(rec)
                            session.commit()

                        rec.title = item['name']
                        rec.icon = item['icon']
                        rec.platform = item['platform']
                        rec.screen_capable = int(item['screen_capable'])
                        rec.screen_present = int(item['screen_present'])
                        rec.online = int(item['online'])

                        session.commit()

            self.add_scenarios()

    def add_scenarios(self):
        data = self.quazar.api_request('https://iot.quasar.yandex.ru/m/user/scenarios')
        scenarios = {}

        if isinstance(data.get('scenarios'), list):
            for scenario in data['scenarios']:
                scenarios[self.yandex_decode(scenario['name'])] = scenario

        with session_scope() as session:
            stations = session.query(YaStation).all()
            for station in stations:
                station_id = station.iot_id
                if not station_id:
                    continue
                if station_id.lower() not in scenarios:
                    # Add scenario
                    name_encoded = self.yandex_encode(station_id)
                    payload = {
                        'name': name_encoded,
                        'icon': 'home',
                        'triggers': [{
                            'trigger': {
                                'type': 'scenario.trigger.voice',
                                'value': name_encoded[5:],  # Аналог mb_substr($nameEncode, 4)
                            }
                        }],
                        'steps': [{
                            'type': 'scenarios.steps.actions.v2',
                            'parameters': {
                                'items': [{
                                    'id': station_id,
                                    'type': 'step.action.item.device',
                                    'value': {
                                        'id': station_id,
                                        'item_type': 'device',
                                        'capabilities': [{
                                            'type': 'devices.capabilities.quasar',
                                            'state': {
                                                'instance': 'tts',
                                                'value': {
                                                    'text': 'Сценарий для osys. НЕ УДАЛЯТЬ!'
                                                }
                                            }
                                        }]
                                    }
                                }]
                            }
                        }]
                    }

                    result = self.quazar.api_request('https://iot.quasar.yandex.ru/m/user/scenarios/', 'POST', payload)
                    if result.get('status') == 'ok':
                        station.tts_scenario = result.get('scenario_id')
                        session.commit()
                else:
                    station.tts_scenario = scenarios[station_id.lower()]['id']
                    session.commit()

    def yandex_encode(self, in_str):
        in_str = in_str.lower()
        MASK_EN = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'a', 'b', 'c', 'd', 'e', 'f', '-']
        MASK_RU = ['о', 'е', 'а', 'и', 'н', 'т', 'с', 'р', 'в', 'л', 'к', 'м', 'д', 'п', 'у', 'я', 'ы']
        translation_table = str.maketrans(''.join(MASK_EN), ''.join(MASK_RU))
        return 'осис ' + in_str.translate(translation_table)

    def yandex_decode(self, in_str):
        in_str = in_str[5:]  # Removing the "oсис " prefix
        MASK_EN = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'a', 'b', 'c', 'd', 'e', 'f', '-']
        MASK_RU = ['о', 'е', 'а', 'и', 'н', 'т', 'с', 'р', 'в', 'л', 'к', 'м', 'д', 'п', 'у', 'я', 'ы']
        translation_table = str.maketrans(''.join(MASK_RU), ''.join(MASK_EN))
        return in_str.translate(translation_table)

    def refresh_devices_data(self):
        try:
            # Собираем список id устройств для опроса только внутри сессии, без удержания соединения во время I/O
            device_ids_to_poll = []
            default_period = self.config.get("update_period", 60)
            now = get_now_to_utc()

            with session_scope() as session:
                self.logger.debug("Begin get data devices")
                update_linked = self.config.get('update_linked', True)
                if update_linked:
                    subquery = (
                        select(distinct(YaCapabilities.device_id))
                        .where(
                            and_(
                                YaCapabilities.linked_object is not None,
                                YaCapabilities.linked_object != ""
                            )
                        )
                    )
                    devices = (
                        session.query(YaDevices)
                        .filter(YaDevices.id.in_(subquery))
                        .all()
                    )
                else:
                    devices = session.query(YaDevices).all()

                for device in devices:
                    period = device.update_period if device.update_period is not None else default_period
                    updated = device.updated
                    if updated is None:
                        # новое устройство — опрашиваем
                        device_ids_to_poll.append((device.id, device.title or f"device_{device.id}"))
                        continue
                    dt = updated + datetime.timedelta(seconds=period)
                    if now >= dt:
                        device_ids_to_poll.append((device.id, device.title or f"device_{device.id}"))

            # Запуск потоков и ожидание — без удержания сессии БД (избегаем блокировок/исчерпания пула)
            threads = []
            for device_id, device_title in device_ids_to_poll:
                t = threading.Thread(
                    name=f"YandexDevice_{device_title}",
                    target=self.refresh_device_data,
                    args=(device_id,),
                )
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            self.logger.debug("End get data devices")
        except Exception as ex:
            self.logger.error(f"refresh_devices_data error: {ex}", exc_info=True)

    def refresh_device_data(self, id):
        def _touch_device_updated(device_id):
            """Обновить время опроса устройства при ошибке, чтобы не опрашивать каждую секунду."""
            try:
                with session_scope() as session:
                    dev = session.query(YaDevices).filter(YaDevices.id == device_id).one_or_none()
                    if dev:
                        dev.updated = get_now_to_utc()
                        session.commit()
            except Exception as e:
                self.logger.warning(f"Could not touch device {device_id} updated: {e}")

        try:
            with session_scope() as session:
                device = session.query(YaDevices).filter(YaDevices.id == id).one_or_none()
                if not device:
                    return
                self.logger.info(f"Begin get data device - {device.title}({device.room})")
                iot_id = device.iot_id

                data = self.quazar.api_request(
                    f"https://iot.quasar.yandex.ru/m/user/devices/{iot_id}"
                )
                self.logger.debug(data)
                if not isinstance(data, dict):
                    device.updated = get_now_to_utc()
                    session.commit()
                    self.sendDataToWebsocket("updateDevice", row2dict(device))
                    self.logger.info(f"End get data device - {device.title}({device.room})")
                    return

                current_status = 0
                if "state" in data:
                    current_status = 1 if data["state"] == "online" else 0

                online_array = {
                    "type": "devices",
                    "state": {"value": current_status},
                    "parameters": {"instance": "online"},
                }
                if "properties" not in data:
                    data["properties"] = []
                data["properties"].append(online_array)

                # Цикл по всем возможностям устройства
                if isinstance(data.get("capabilities"), list):
                    for capability in data["capabilities"]:
                        if capability is None:
                            continue
                        c_type = capability.get("type") or "unknown"
                        if c_type == "devices.capabilities.on_off":
                            pass  # c_type уже задан
                        elif capability.get("state", {}) and capability.get("state", {}).get("instance"):
                            c_type += f'.{capability["state"]["instance"]}'
                        elif capability.get("parameters", {}) and capability.get("parameters", {}).get("instance"):
                            c_type += f'.{capability["parameters"]["instance"]}'
                        else:
                            c_type += ".unknown"

                        req_skill = (
                            session.query(YaCapabilities)
                            .filter(
                                YaCapabilities.title == c_type,
                                YaCapabilities.device_id == device.id,
                            )
                            .one_or_none()
                        )
                        if not req_skill:
                            req_skill = YaCapabilities(title=c_type, device_id=device.id)
                            session.add(req_skill)
                            session.commit()

                        # Основные возможности, меняем значение
                        value = None
                        if capability.get("state", {}) and isinstance(capability.get("state", {}).get("value"), bool):
                            value = int(capability["state"]["value"])
                        elif capability.get("state", {}) and capability.get("state", {}).get("instance") == "color":
                            value = capability["state"]["value"].get("id")
                        elif capability.get("state", {}) and capability.get("state", {}).get("instance") == "scene":
                            value = capability["state"]["value"].get("id") if isinstance(capability["state"]["value"], dict) else capability["state"]["value"]
                        else:
                            if capability.get("state", {}):
                                value = capability.get("state", {}).get("value")
                            else:
                                value = "?"

                        new_value = value
                        old_value = req_skill.value

                        if req_skill.linked_object and req_skill.linked_property:
                            linked_object_property = (
                                f"{req_skill.linked_object}.{req_skill.linked_property}"
                            )
                            updateProperty(linked_object_property, new_value, self.name)

                        if new_value != old_value:
                            req_skill.value = str(new_value)
                            req_skill.updated = get_now_to_utc()
                            session.commit()

                        if new_value != old_value and req_skill.linked_object and req_skill.linked_method:
                            method_params = {
                                "NEW_VALUE": new_value,
                                "OLD_VALUE": old_value,
                                "DEVICE_STATE": current_status,
                                "UPDATED": req_skill.updated,
                                "MODULE": self.name,
                            }
                            callMethod(
                                f"{req_skill.linked_object}.{req_skill.linked_method}",
                                method_params,
                                self.name,
                            )

                # Значения датчиков
                if isinstance(data.get("properties"), list):
                    for prop in data["properties"]:
                        p_type_raw = prop.get("type")
                        params = prop.get("parameters") or {}
                        instance = params.get("instance")
                        if not p_type_raw or instance is None:
                            continue
                        p_type = f"{p_type_raw}.{instance}"

                        req_prop = (
                            session.query(YaCapabilities).filter(YaCapabilities.title == p_type, YaCapabilities.device_id == device.id).one_or_none()
                        )
                        if not req_prop:
                            req_prop = YaCapabilities(title=p_type, device_id=device.id)
                            session.add(req_prop)
                            session.commit()

                        # Основные датчики
                        value = None
                        if prop.get("state"):
                            value = prop["state"].get("value")

                        new_value = value
                        old_value = req_prop.value

                        if req_prop.linked_object and req_prop.linked_property:
                            linked_object_property = (
                                f"{req_prop.linked_object}.{req_prop.linked_property}"
                            )
                            setProperty(linked_object_property, new_value, self.name)

                        if new_value != old_value:
                            req_prop.value = new_value
                            req_prop.updated = get_now_to_utc()
                            session.commit()

                        if new_value != old_value and req_prop.linked_object and req_prop.linked_method:
                            method_params = {
                                "NEW_VALUE": new_value,
                                "OLD_VALUE": old_value,
                                "DEVICE_STATE": current_status,
                                "UPDATED": req_prop.updated,
                                "MODULE": self.name,
                            }
                            callMethod(
                                f"{req_prop.linked_object}.{req_prop.linked_method}",
                                method_params,
                                self.name,
                            )

                device.updated = get_now_to_utc()
                session.commit()
                self.sendDataToWebsocket("updateDevice", row2dict(device))
                self.logger.info(f"End get data device - {device.title}({device.room})")
        except Exception as ex:
            self.logger.error(f"refresh_device_data error (device id={id}): {ex}", exc_info=True)
            _touch_device_updated(id)

    def changeLinkedProperty(self, obj, prop, val):
        with session_scope() as session:
            properties = session.query(YaCapabilities).filter(YaCapabilities.linked_object == obj, YaCapabilities.linked_property == prop).all()
            if len(properties) == 0:
                from app.core.lib.object import removeLinkFromObject
                removeLinkFromObject(obj, prop, self.name)
                return
            for property in properties:
                device = session.query(YaDevices).filter(YaDevices.id == property.device_id).one_or_none()
                if device:
                    self.setDataDevice(device, property, val)

    def say(self, message, level=0, args=None):
        with session_scope() as session:
            if args and 'station' in args:
                stations = session.query(YaStation).filter(YaStation.title == args['station'])
            else:
                stations = session.query(YaStation).all()

            for station in stations:
                if station.tts == 0 or station.tts is None:
                    continue
                minlevel = station.min_level
                if not minlevel or minlevel == '':
                    continue
                if "." in minlevel:
                    minlevel = getProperty(minlevel)
                minlevel = int(minlevel)
                if level < minlevel:
                    continue
                if station.tts == 1:  # local TTS
                    self.send_command_to_station(station, 'повтори за мной ' + message)
                elif station.tts == 2:  # cloud TTS
                    if len(message) >= 100:
                        sentences = re.split(r'\.\.\.|[.!?]\s*', message)
                        for sentence in sentences:
                            pause = int(len(sentence) / 8 + 1)  # экспериментально
                            self.send_cloud_TTS(station, sentence)
                            self.logger.info(sentence)
                            sleep(pause)
                    else:
                        self.send_cloud_TTS(station, message)

    def widget(self):
        with session_scope() as session:
            stations = session.query(YaStation).all()
            devices = session.query(YaDevices).all()
            content = {}
            content['stations'] = len(stations)
            content['devices'] = len(devices)
        return render_template("widget_yandexdevices.html",**content)

    def setDataDevice(self, device: YaDevices, property: YaCapabilities, value):
        p = property.title.split('.')
        cap = p[0] + '.' + p[1] + '.' + p[2]  # уберем instance
        inst = 'on' if p[2] == 'on_off' else p[3]
        if p[2] in  ["on_off", "toggle"]:
            if value == 1:
                value = True
            else:
                value = False
        payload = {
            "actions": [
                {
                    "type": cap,
                    "state": {
                        "instance": inst,
                        "value": value
                    }
                }
            ]
        }

        result = self.quazar.api_request('https://iot.quasar.yandex.ru/m/user/devices/' + device.iot_id + '/actions', 'POST', payload)
        self.logger.debug(result)

    def _glagol_lan_send_fn(
        self,
        station_id: int,
        host: str,
        port: int,
        token: str,
    ):
        """Отправка Glagol-команды через фоновый WS, иначе отдельное подключение."""
        from plugins.YandexDevices.glagol_local import glagol_request

        plugin = self

        def _send(payload: Dict[str, Any]):
            reg = getattr(plugin, "_glagol_registry", None)
            if reg is not None:
                resp, err, used = reg.send_payload(int(station_id), token, payload)
                if used:
                    return resp, err
            return glagol_request(host, port, token, payload, plugin.logger)

        return _send

    def _sanitize_tts_text(self, message: str, max_len: int) -> str:
        message = message.replace('(', ' ').replace(')', ' ')
        message = re.sub(r'<.+?>', '', message)
        message = ' '.join(message.split())
        if len(message) > max_len:
            message = message[: max_len - 1]
        return message

    def _ensure_device_token(
        self,
        station_id: int,
        iot_id: Optional[str],
        platform: Optional[str],
        existing_token: Optional[str],
        *,
        refresh_if_expired: bool = False,
    ) -> Optional[str]:
        """Возвращает ``device_token``; при необходимости запрашивает новый у Quasar."""
        from plugins.YandexDevices.glagol_local import glagol_token_expired, glagol_token_exp_unix

        token = (existing_token or "").strip() or None
        if refresh_if_expired and token and glagol_token_expired(token):
            exp = glagol_token_exp_unix(token)
            self.logger.warning(
                "Glagol: токен станции id=%s истёк (exp=%s) — запрос нового у Quasar",
                station_id,
                int(exp) if exp else "?",
            )
            token = None
        if not token and iot_id and platform:
            token = self.quazar.get_device_token(iot_id, platform)
            if token:
                try:
                    with session_scope() as session:
                        st = session.query(YaStation).filter(YaStation.id == station_id).one_or_none()
                        if st:
                            st.device_token = token
                            session.commit()
                except Exception as ex:
                    self.logger.warning("Glagol: не удалось сохранить device_token в БД — %s", ex)
        return token

    def _ensure_station_device_token(self, station: YaStation) -> Optional[str]:
        """Возвращает ``device_token``, при необходимости запрашивает через Quasar и сохраняет в БД."""
        try:
            sid = int(station.id)
        except (TypeError, ValueError):
            return None
        return self._ensure_device_token(
            sid,
            station.iot_id,
            station.platform,
            station.device_token,
        )

    def _find_station_for_glagol(self, session, kwargs: Dict[str, Any]) -> Optional[YaStation]:
        """Станция по ``station_id``, имени объекта ``glagol_linked_object`` или ``station``/``station_title``."""
        sid = kwargs.get("station_id")
        if sid is not None and str(sid).strip() != "":
            try:
                return session.query(YaStation).filter(YaStation.id == int(sid)).one_or_none()
            except (TypeError, ValueError):
                pass
        obj = (kwargs.get("object") or kwargs.get("object_name") or "").strip()
        if obj:
            st = session.query(YaStation).filter(YaStation.glagol_linked_object == obj).one_or_none()
            if st:
                return st
        title = (kwargs.get("station") or kwargs.get("station_title") or "").strip()
        if title:
            return session.query(YaStation).filter(YaStation.title == title).one_or_none()
        return None

    def glagol_command(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Универсальная отправка команды Glagol (LAN) в колонку.

        Предназначен для вызова из методов объектов osysHome через
        ``callPluginFunction("YandexDevices", "glagol_command", {...})``.
        См. ``plugins/YandexDevices/docs/Commands.md``.
        """
        from plugins.YandexDevices.glagol_local import (
            glagol_player_command,
            glagol_send_text,
            parse_host_port,
        )

        base: Dict[str, Any] = {"ok": False}
        with session_scope() as session:
            st = self._find_station_for_glagol(session, kwargs)
            if not st:
                return {
                    **base,
                    "error": "station not found",
                    "detail": "Укажите station_id, object (glagol_linked_object) или station (title).",
                }
            station_id = int(st.id)
            station_title = st.title or ""
            host, port = parse_host_port(st.ip or "")
            iot_id = st.iot_id
            platform = st.platform
            device_token = st.device_token

        base["station_id"] = station_id
        base["station_title"] = station_title

        if not host:
            return {**base, "error": "no IP", "detail": "Задайте LAN-IP в карточке станции."}

        token = self._ensure_device_token(
            station_id, iot_id, platform, device_token, refresh_if_expired=True
        )
        if not token:
            return {
                **base,
                "error": "no device token",
                "detail": "Сформируйте или обновите токен в карточке станции (кнопка «Обновить токен»).",
            }

        text = kwargs.get("text")
        if text is not None and str(text).strip():
            phrase = self._sanitize_tts_text(str(text), 2000)
            send_fn = self._glagol_lan_send_fn(station_id, host, port, token)
            ok, resp, detail = glagol_send_text(
                host, port, token, phrase, self.logger, send_fn=send_fn
            )
            out = {
                **base,
                "ok": bool(ok),
                "action": "sendText",
                "host": host,
                "port": port,
            }
            if resp is not None:
                out["response"] = resp
            if detail:
                out["detail"] = detail
            if not ok and resp is not None:
                out["error"] = "sendText not acknowledged"
            return out

        action = (kwargs.get("action") or "").strip()
        if not action:
            return {
                **base,
                "error": "action or text required",
                "detail": "Передайте action (play, pause, volume, …) или text (sendText).",
            }

        cmd_kw: Dict[str, Any] = {}
        if kwargs.get("volume") is not None:
            cmd_kw["volume"] = float(kwargs["volume"])
        if kwargs.get("position") is not None:
            cmd_kw["position"] = float(kwargs["position"])
        if kwargs.get("repeat_mode") is not None:
            cmd_kw["repeat_mode"] = kwargs.get("repeat_mode")
        if "shuffle" in kwargs and kwargs.get("shuffle") is not None:
            cmd_kw["shuffle"] = bool(kwargs.get("shuffle"))
        if kwargs.get("music_id") is not None:
            cmd_kw["music_id"] = kwargs.get("music_id")
        if kwargs.get("music_type") is not None:
            cmd_kw["music_type"] = kwargs.get("music_type")

        send_fn = self._glagol_lan_send_fn(station_id, host, port, token)
        resp, cmd_err = glagol_player_command(
            host, port, token, self.logger, action, send_fn=send_fn, **cmd_kw
        )
        if not resp:
            return {
                **base,
                "action": action,
                "error": "no response",
                "detail": cmd_err,
                "host": host,
                "port": port,
            }
        from plugins.YandexDevices.glagol_local import _glagol_response_ok

        ok = _glagol_response_ok(resp)
        out = {
            **base,
            "ok": ok,
            "action": action,
            "response": resp,
            "host": host,
            "port": port,
        }
        if not ok:
            out["error"] = "command not acknowledged"
            out["detail"] = str(resp.get("status") or resp)
        return out

    def send_command_to_station(self, station: YaStation, command: str):
        """Локальный TTS/команда по WebSocket Glagol (нужны IP, platform, iot_id, device_token)."""
        from plugins.YandexDevices.glagol_local import glagol_send_text, parse_host_port

        text = self._sanitize_tts_text(command or '', 2000)
        if not text.strip():
            self.logger.warning("Local TTS: пустая фраза для станции %s", getattr(station, "title", "?"))
            return False

        host, port = parse_host_port(station.ip or "")
        if not host:
            self.logger.warning(
                "Local TTS: у станции «%s» не задан IP. Укажите LAN-адрес колонки в карточке станции.",
                station.title,
            )
            return False

        token = self._ensure_station_device_token(station)

        if not token:
            self.logger.warning(
                "Local TTS: нет токена устройства для «%s». Нажмите «Сформировать токен» в редактировании станции.",
                station.title,
            )
            return False

        station_id = int(station.id) if station.id is not None else 0
        send_fn = self._glagol_lan_send_fn(station_id, host, port, token) if station_id else None
        ok, _resp, detail = glagol_send_text(
            host,
            port,
            token,
            text,
            self.logger,
            send_fn=send_fn,
        )
        if not ok:
            self.logger.warning(
                "Local TTS: отправка не удалась (%s:%s), станция «%s»%s",
                host,
                port,
                station.title,
                f" — {detail}" if detail else "",
            )
        return ok

    def send_command_to_stationCloud(self, station, command):
        with session_scope() as session:
            ystation = session.query(YaStation).filter(YaStation.title == station).one()
            if ystation and command:
                self.send_cloud_TTS(ystation,command,'text_action')

    def send_cloud_TTS(self, station: YaStation, message: str, action='phrase_action'):

        message = self._sanitize_tts_text(message or '', 99)

        # Debug logging if error monitoring is enabled
        self.logger.info(f"Sending cloud '{action}: {message}' to {station.title}")

        if not station.tts_scenario:
            return False

        name_encode = self.yandex_encode(station.iot_id)

        payload = {
            'name': name_encode,
            'icon': 'home',
            'triggers': [{
                'trigger': {
                    'type': 'scenario.trigger.voice',
                    'value': name_encode,
                }
            }],
            'steps': [{
                'type': 'scenarios.steps.actions.v2',
                'parameters': {
                    'items': [{
                        'id': station.iot_id,
                        'type': 'step.action.item.device',
                        'value': {
                            'id': station.iot_id,
                            'item_type': 'device',
                            'capabilities': [{
                                'type': 'devices.capabilities.quasar.server_action',
                                'state': {
                                    'instance': action,
                                    'value': message
                                }
                            }]
                        }
                    }]
                }
            }]
        }

        scenario_id = station.tts_scenario
        result = self.quazar.api_request(f'https://iot.quasar.yandex.ru/m/v4/user/scenarios/{scenario_id}', 'PUT', payload)

        if isinstance(result, dict) and result.get('status') == 'ok':
            payload = {}
            result = self.quazar.api_request(f'https://iot.quasar.yandex.ru/m/user/scenarios/{scenario_id}/actions', 'POST', payload)

            if isinstance(result, dict) and result.get('status') == 'ok':
                return True
            else:
                self.logger.error(result, 'Failed to run TTS scenario')
        else:
            self.logger.error(result, 'Failed to update TTS scenario')

        return False
