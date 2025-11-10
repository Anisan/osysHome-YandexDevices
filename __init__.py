import datetime
import os
import re
import threading
from flask import redirect, request, jsonify, render_template
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

class YandexDevices(BasePlugin):

    def __init__(self,app):
        super().__init__(app,__name__)
        self.title = "Yandex Devices"
        self.description = """Yandex Devices Plugin"""
        self.actions = ["cycle","say","widget"]
        self.category = "Devices"
        self.version = 0.2
        self.author = 'Eraser'

    def initialization(self):
        cache_dir = os.path.join(getCacheDir(), self.name)
        os.makedirs(cache_dir, exist_ok=True)
        self.quazar = QuazarApi(cache_dir, self.logger)
        pass

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
            id = request.args.get('id', None)
            req = YaStation.get_by_id(id)
            self.get_device_token(req.station_id, req.platform)
            return redirect("?station=" + id + "&op=edit")

        if op == 'edit':
            if device:
                return render_template("yandexdevices_device.html", id=device)
            if station:
                from plugins.YandexDevices.forms.StationForm import editStation
                result = editStation(request)
                return result
            
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
            devices = YaDevices.query.all()
            devices = [row2dict(device) for device in devices]
            content = {
                "devices": devices,
                "tab": tab,
                'form': settings,
            }
            return self.render('yandexdevices_devices.html', content)

        stations = YaStation.query.all()
        stations = [row2dict(station) for station in stations]
        content = {
            'stations': stations,
            "tab": tab,
            'form': settings,
        }
        return self.render('yandexdevices_stations.html', content)

    def route_index(self):
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

    def cyclic_task(self):
        # self.refresh_stations()
        if self.config.get("get_device_data", False):
            self.refresh_devices_data()

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
        with session_scope() as session:
            self.logger.debug("Begin get data devices")

            # Получение списка устройств
            update_linked = self.config.get('update_linked',True)
            if update_linked:
                # Подзапрос для выборки уникальных device_id из YaCapabilities
                subquery = (
                    select(distinct(YaCapabilities.device_id))
                    .where(
                        and_(
                            YaCapabilities.linked_object is not None,
                            YaCapabilities.linked_object != ""
                        )
                    )
                )
                # Основной запрос для выборки устройств из YaDevices
                devices = (
                    session.query(YaDevices)
                    .filter(YaDevices.id.in_(subquery))  # Фильтруем по результатам подзапроса
                    .all()
                )
            else:
                devices = session.query(YaDevices).all()  # Все устройства
            threads = []
            for device in devices:

                period = device.update_period
                if period is None:
                    period = self.config.get("update_period", 60)  # get default period from settings
                dt = device.updated + datetime.timedelta(seconds=period)
                if get_now_to_utc() < dt:
                    continue
                t = threading.Thread(name="YandexDevice_" + device.title,target=self.refresh_device_data, args=(device.id,))
                threads.append(t)
                t.start()

            # Ожидание завершения всех потоков
            for event in threads:
                event.join()  # Ожидаем, пока каждый закончит работу

            self.logger.debug("End get data devices")

    def refresh_device_data(self, id):
        with session_scope() as session:
            device = session.query(YaDevices).filter(YaDevices.id == id).one_or_none()
            if not device:
                return
            self.logger.info(f"Begin get data device - {device.title}({device.room})")
            # Узнаем IOT_ID
            iot_id = device.iot_id

            # Запрос информации об устройстве
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
                    c_type = capability["type"]

                    if capability["type"] == "devices.capabilities.on_off":
                        c_type = capability["type"]
                    elif capability.get("state", {}) is not None and capability.get("state", {}).get("instance"):
                        c_type += f'.{capability["state"]["instance"]}'
                    elif capability.get("parameters", {}) is not None and capability.get("parameters", {}).get("instance"):
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
                    if capability.get("state", {}) is not None and isinstance(capability.get("state", {}).get("value"), bool):
                        value = int(capability["state"]["value"])
                    elif capability.get("state", {}) is not None and capability.get("state", {}).get("instance") == "color":
                        value = capability["state"]["value"]["id"]
                    elif capability.get("state", {}) is not None and capability.get("state", {}).get("instance") == "scene":  # xor2016: добавлена сцена для Я.лампочки
                        value = capability["state"]["value"]["id"]
                    else:
                        if capability.get("state", {}) is not None:
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
                for property in data["properties"]:
                    p_type = f"{property['type']}.{property['parameters']['instance']}"

                    req_prop = (
                        session.query(YaCapabilities).filter(YaCapabilities.title == p_type,YaCapabilities.device_id == device.id).one_or_none()
                    )
                    if not req_prop:
                        req_prop = YaCapabilities(title=p_type, device_id=device.id)
                        session.add(req_prop)
                        session.commit()

                    # Основные датчики
                    value = None
                    if property["state"]:
                        value = property["state"].get("value")

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
        if property.title == "devices.capabilities.on_off":
            if value == 1:
                value = True
            else:
                value = False
        payload = {
            "actions": [
                {
                    "type": property.title,
                    "state": {
                        "instance": "on",
                        "value": value
                    }
                }
            ]
        }

        result = self.quazar.api_request('https://iot.quasar.yandex.ru/m/user/devices/' + device.iot_id + '/actions', 'POST', payload)
        self.logger.debug(result)

    def send_command_to_station(self, station, command):
        pass

    def send_command_to_stationCloud(self, station, command):
        with session_scope() as session:
            ystation = session.query(YaStation).filter(YaStation.title == station).one()
            if ystation and command:
                self.send_cloud_TTS(ystation,command,'text_action')

    def send_cloud_TTS(self, station: YaStation, message: str, action='phrase_action'):

        # Cleaning up the phrase as per the PHP code logic
        message = message.replace('(', ' ').replace(')', ' ')
        message = re.sub(r'<.+?>', '', message)  # Removing HTML tags
        message = ' '.join(message.split())  # Replacing multiple spaces with a single space

        if len(message) >= 100:
            message = message[:99]

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
