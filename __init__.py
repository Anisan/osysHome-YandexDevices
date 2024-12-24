import datetime
import os
import re
from flask import redirect 
from plugins.YandexDevices.models.YaDevices import YaDevices
from plugins.YandexDevices.models.YaStation import YaStation
from plugins.YandexDevices.models.YaCapabilities import YaCapabilities
from app.core.main.BasePlugin import BasePlugin
from app.core.lib.object import setProperty, getProperty, callMethod
from app.core.lib.cache import deleteFromCache, getCacheDir

from app.database import session_scope
from plugins.YandexDevices.QuazarApi import QuazarApi

class YandexDevices(BasePlugin):

    def __init__(self,app):
        super().__init__(app,__name__)
        self.title = "Yandex Devices"
        self.description = """Yandex Devices Plugin"""
        self.actions = ["cycle","say"]
    
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
            self.update_devices()

        if op == "generate_dev_token":
            id = request.args.get('id', None)
            req = YaStation.get_by_id(id)
            self.get_device_token(req.station_id, req.platform)
            return redirect("?station=" + id + "&op=edit")

        if op == 'edit':
            if device:
                from plugins.YandexDevices.forms.DeviceForm import editDevice
                result = editDevice(request)
                return result
            
            if station:
                from plugins.YandexDevices.forms.StationForm import editStation
                result = editStation(request)
                return result

        if tab == 'devices':
            devices = YaDevices.query.all()
            content = {
                "devices": devices,
                "tab": tab,
            }
            return self.render('yandexdevices_devices.html', content)

        stations = YaStation.query.all()
        content = {
            'stations': stations,
            "tab": tab
        }
        return self.render('yandexdevices_stations.html', content)

    def cyclic_task(self):
        self.refresh_stations()
        self.refresh_devices_data()

        self.event.wait(30.0)
    
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
                        rec.icon = device['icon_url']
                        rec.updated = datetime.datetime.now()
                        session.commit()

                        # обновление станций
                        rec_station = session.query(YaStation).filter(YaStation.title == device['name']).one_or_none()
                            
                        if not rec_station and quasar_id:
                            rec_station = session.query(YaStation).filter(YaStation.station_id == quasar_id).one_or_none()
                            
                        if rec_station:
                            rec_station.iot_id = device['id']
                            rec_station.updated = datetime.datetime.now()
                            session.commit()
        
        except Exception as ex:
            self.logger.error(ex)

    def refresh_stations(self):
        data = self.quazar.api_request('https://quasar.yandex.ru/devices_online_stats')

        if isinstance(data.get('items'), list):
            items = data['items']
            with session_scope() as session:
                for item in items:
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
                        'triggers': [{'type': 'scenario.trigger.voice', 'value': name_encoded[5:]}],
                        'steps': [{
                            'type': 'scenarios.steps.actions',
                            'parameters': {
                                'requested_speaker_capabilities': [],
                                'launch_devices': [{
                                    'id': station_id,
                                    'capabilities': [{
                                        'type': 'devices.capabilities.quasar.server_action',
                                        'state': {
                                            'instance': 'phrase_action',
                                            'value': 'Сценарий для osysHome. НЕ УДАЛЯТЬ!'
                                        }
                                    }]
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

    def refresh_devices_data(self, dev_id=None):
        with session_scope() as session:
            # Получение списка устройств
            if dev_id is not None:
                devices = session.query(YaDevices).filter(YaDevices.id == dev_id)
            else:
                devices = session.query(YaDevices).all()

            for device in devices:
                # Узнаем IOT_ID
                iot_id = device.iot_id

                # Запрос информации об устройстве
                data = self.quazar.api_request(f'https://iot.quasar.yandex.ru/m/user/devices/{iot_id}')
                # self.logger.debug(data)
                if not isinstance(data, dict):
                    continue
                
                current_status = 0
                if 'state' in data:
                    current_status = 1 if data['state'] == 'online' else 0

                online_array = {
                    'type': 'devices',
                    'state': {'value': current_status},
                    'parameters': {'instance': 'online'}
                }
                if 'properties' not in data:
                    data["properties"] = []
                data["properties"].append(online_array)

                # Цикл по всем возможностям устройства
                if isinstance(data.get("capabilities"), list):
                    for capability in data["capabilities"]:
                        c_type = capability['type']

                        if capability['type'] == 'devices.capabilities.on_off':
                            c_type = capability['type']
                        elif capability.get('state', {}).get('instance'):
                            c_type += f'.{capability["state"]["instance"]}'
                        elif capability.get('parameters', {}).get('instance'):
                            c_type += f'.{capability["parameters"]["instance"]}'
                        else:
                            c_type += '.unknown'

                        req_skill = session.query(YaCapabilities).filter(YaCapabilities.title == c_type, YaCapabilities.device_id == device.id).one_or_none()
                        if not req_skill:
                            req_skill = YaCapabilities(title=c_type, device_id=device.id)
                            session.add(req_skill)
                            session.commit()

                        # Основные возможности, меняем значение
                        value = None
                        if isinstance(capability.get('state', {}).get('value'), bool):
                            value = int(capability['state']['value'])
                        elif capability.get('state', {}).get('instance') == 'color':
                            value = capability['state']['value']['id']
                        elif capability.get('state', {}).get('instance') == 'scene':  # xor2016: добавлена сцена для Я.лампочки
                            value = capability['state']['value']['id']
                        else:
                            value = capability.get('state', {}).get('value')
                            if value is None:
                                value = '?'

                        new_value = value
                        old_value = req_skill.value

                        if str(new_value) != str(old_value) and req_skill.linked_object and req_skill.linked_property:
                            linked_object_property = f"{req_skill.linked_object}.{req_skill.linked_property}"
                            if new_value != getProperty(linked_object_property):
                                setProperty(linked_object_property, new_value, self.name)

                        if new_value != old_value:
                            req_skill.value = str(new_value)
                            req_skill.updated = datetime.datetime.now()
                            session.commit()

                        if new_value != old_value and req_skill.linked_object and req_skill.linked_method:
                            method_params = {
                                'NEW_VALUE': new_value,
                                'OLD_VALUE': old_value,
                                'DEVICE_STATE': current_status,
                                'UPDATED': req_skill.updated,
                                'MODULE': self.name
                            }
                            callMethod(f"{req_skill.linked_object}.{req_skill.linked_method}", method_params, self.name)

                # Значения датчиков
                if isinstance(data.get("properties"), list):
                    for property in data["properties"]:
                        p_type = f"{property['type']}.{property['parameters']['instance']}"

                        req_prop = session.query(YaCapabilities).filter(YaCapabilities.title == p_type, YaCapabilities.device_id == device.id).one_or_none()
                        if not req_prop:
                            req_prop = YaCapabilities(title=p_type, device_id=device.id)
                            session.add(req_prop)
                            session.commit()

                        # Основные датчики
                        value = None
                        if property['state']:
                            value = property['state'].get('value')

                        new_value = value
                        old_value = req_prop.value

                        if new_value != old_value and req_prop.linked_object and req_prop.linked_property:
                            linked_object_property = f"{req_prop.linked_object}.{req_prop.linked_property}"
                            setProperty(linked_object_property, new_value, self.name)

                        if new_value != old_value:
                            req_prop.value = new_value
                            req_prop.updated = datetime.datetime.now()
                            session.commit()

                        if new_value != old_value and req_prop.linked_object and req_prop.linked_method:
                            method_params = {
                                'NEW_VALUE': new_value,
                                'OLD_VALUE': old_value,
                                'DEVICE_STATE': current_status,
                                'UPDATED': req_prop.updated,
                                'MODULE': self.name
                            }
                            callMethod(f"{req_prop.linked_object}.{req_prop.linked_method}", method_params, self.name)

    def say(self, message, level=0, args=None):
        with session_scope() as session:
            station = session.query(YaStation).all()
            for station in station:
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
                    self.send_cloud_TTS(station, message)

    def send_command_to_station(self, station, command):
        pass

    def send_cloud_TTS(self, station: YaStation, message: str, action='phrase_action'):
        
        # Cleaning up the phrase as per the PHP code logic
        message = message.replace('(', ' ').replace(')', ' ')
        message = re.sub(r'<.+?>', '', message)  # Removing HTML tags
        message = ' '.join(message.split())  # Replacing multiple spaces with a single space

        if len(message) >= 100:
            message = message[:99]

        # Debug logging if error monitoring is enabled
        self.logger.debug(f"Sending cloud '{action}: {message}' to {station.title}")

        if not station.tts_scenario:
            return False

        name_encode = self.yandex_encode(station.iot_id)

        payload = {
            'name': name_encode,
            'icon': 'home',
            'triggers': [{
                'type': 'scenario.trigger.voice',
                'value': name_encode,
            }],
            'steps': [{
                'type': 'scenarios.steps.actions',
                'parameters': {
                    'requested_speaker_capabilities': [],
                    'launch_devices': [{
                        'id': station.iot_id,
                        'capabilities': [{
                            'type': 'devices.capabilities.quasar.server_action',
                            'state': {
                                'instance': action,
                                'value': message
                            }
                        }]
                    }]
                }
            }]
        }

        scenario_id = station.tts_scenario
        result = self.quazar.api_request(f'https://iot.quasar.yandex.ru/m/user/scenarios/{scenario_id}', 'PUT', payload)

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