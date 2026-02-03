import os
import json
import requests
import re
import urllib.parse
import certifi

# Таймауты (connect, read) в секундах — без них запрос может висеть бесконечно при проблемах с сетью/сервером
DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_READ_TIMEOUT = 30
DEFAULT_TIMEOUT = (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT)

class QuazarApi():
    def __init__(self, cache_dir, logger):
        self.logger = logger
        self.cache_dir = cache_dir
        self.cookie_path = os.path.join(self.cache_dir,'cookie')
        self.csrf_token = self.get_token()
        self.music_token = None

    def api_request(self, url, method='GET', params=None, repeating=0, csrf_token=None, debug=0):
        # Initialize session and headers
        headers = {}
        if method != 'GET' and not csrf_token:
            csrf_token = self.get_token()  # Retrieve the CSRF token if not already set

        if method != 'GET':
            headers = {
                'Content-type': 'application/json',
                'x-csrf-token': csrf_token
            }

        # Initialize the session and load cookies from the specified path
        session = requests.Session()
        # Load cookies from the file using MozillaCookieJar
        if self.cookie_path is not None and os.path.exists(self.cookie_path):
            with open(self.cookie_path, 'r') as f:
                cookies = requests.utils.cookiejar_from_dict(json.load(f))
                session.cookies.update(cookies)

        # Prepare request and make the API call
        response = None
        try:
            if method == 'GET':
                response = session.get(url, headers=headers, cookies=session.cookies, timeout=DEFAULT_TIMEOUT, verify=certifi.where())
            else:
                if isinstance(params, dict):
                    params = json.dumps(params)
                if method == 'POST':
                    response = session.post(url, data=params, headers=headers, cookies=session.cookies, timeout=DEFAULT_TIMEOUT, verify=certifi.where())
                else:
                    response = session.request(method, url, data=params, headers=headers, cookies=session.cookies, timeout=DEFAULT_TIMEOUT, verify=certifi.where())
        except requests.RequestException as e:
            self.logger.error(f"Request error: {e}")
            return None

        # Process the response
        result_code = response.status_code
        if result_code == 401:
            self.logger.error("Unauthorized access")
            return None
        try:
            data = response.json()
        except ValueError:
            data = None

        # Handle repeating logic in case of error or unauthorized status
        if not repeating and (data is None or data.get('code') != 'BAD_REQUEST') and (data is None or result_code == 403 or data.get('status') == 'error'):
            if debug:
                self.logger.debug(f"REPEATING: {method} {url}")
            csrf_token = ''
            return self.api_request(url, method, params, repeating=1, csrf_token=csrf_token, debug=debug)

        return data
    
    def _get_session(self):
        # Initialize a session to handle cookies
        session = requests.Session()

        # Load cookies from the file using MozillaCookieJar
        if self.cookie_path is not None and os.path.exists(self.cookie_path):
            with open(self.cookie_path, 'r') as f:
                cookies = requests.utils.cookiejar_from_dict(json.load(f))
                session.cookies.update(cookies)

        return session
    
    def get_device_token(self, device_id, platform):
        self.logger.debug("Обновление токена устройства")

        session = self._get_session()

        if not self.music_token:
            self.music_token = self.get_music_token(self.csrf_token)

        # OAuth should be capitalize, or music will be 128 bitrate quality
        headers = {}
        headers["Authorization"] = f"OAuth {self.music_token}"

        payload = {
            "device_id": device_id,
            "platform": platform,
        }
        r = session.get("https://quasar.yandex.net/glagol/token", headers=headers, params=payload, cookies=session.cookies, timeout=DEFAULT_TIMEOUT)
        resp = json.loads(r.text)
        if resp["status"] == "ok":
            return resp["token"]
        
        return None
    
    def get_music_token(self, x_token: str):
        """Get music token using x-token. Usual you should'n call this method."""
        self.logger.debug("Get music token")

        payload = {
            # Thanks to https://github.com/MarshalX/yandex-music-api/
            "client_secret": "53bc75238f0c4d08a118e51fe9203300",
            "client_id": "23cabbbdc6cd418abb4b39c32c41195d",
            "grant_type": "x-token",
            "access_token": x_token,
        }
        session = self._get_session()
        r = session.post("https://oauth.mobile.yandex.net/1/token", data=payload, timeout=DEFAULT_TIMEOUT)
        resp = r.json()
        assert "access_token" in resp, resp
        return resp["access_token"]

    def get_token(self, url='https://yandex.ru/quasar/iot', error_monitor=False, error_monitor_type=1):
        # Create a session to maintain cookies
        session = requests.Session()
        # Load cookies from the file using MozillaCookieJar
        if self.cookie_path is not None and os.path.exists(self.cookie_path):
            with open(self.cookie_path, 'r') as f:
                cookies = requests.utils.cookiejar_from_dict(json.load(f))
                session.cookies.update(cookies)
        
        # Set the necessary headers and parameters for the request
        headers = {
            'Accept-Encoding': 'gzip',
        }

        # Perform the GET request
        try:
            response = session.get(url, headers=headers, cookies=session.cookies, timeout=DEFAULT_TIMEOUT, verify=certifi.where())
        except requests.RequestException as e:
            self.logger.error(f"Request error: {e}")
            return False

        # Check if the response contains the CSRF token
        match = re.search(r'"csrfToken2":"(.+?)"', response.text)
        if match:
            token = match.group(1)
            return token
        else:
            # Handle error if CSRF token is not found
            if error_monitor:
                if error_monitor_type == 1:
                    self.logger.error("Error: Failed to retrieve csrfToken2")
                elif error_monitor_type == 2:
                    self.logger.error("Error: Failed to retrieve csrfToken2 (Verbose)")
            return False

    def get_csrf_token(self, cookie_path):
        url = 'https://passport.yandex.ru/am?app_platform=android'
        
        # Initialize a session to handle cookies
        session = requests.Session()

        # Load cookies from the file using MozillaCookieJar
        if cookie_path is not None and os.path.exists(cookie_path):
            with open(cookie_path, 'r') as f:
                cookies = requests.utils.cookiejar_from_dict(json.load(f))
                session.cookies.update(cookies)
        
        # Perform a GET request
        response = session.get(url, allow_redirects=True, timeout=DEFAULT_TIMEOUT, verify=certifi.where())

        # Check if the CSRF token is in the response body
        match = re.search(r'"csrf_token" value="(.+?)"', response.text)

        with open(cookie_path, 'w') as f:
            json.dump(requests.utils.dict_from_cookiejar(session.cookies), f)
        
        if match:
            token = match.group(1)
            return token
        else:
            # Log an error if token is not found (adjust your error handling as needed)
            self.logger.error("Failed to get CSRF token:", response.text)
            return False
        
    def getQrCode(self):
        use_cookie_file = os.path.join(self.cache_dir,'cookie_qr')
        out = {}
        csrf_token = self.get_csrf_token(use_cookie_file)
        if csrf_token:
            post_data = {
                'csrf_token': csrf_token,
                'retpath': 'https://passport.yandex.ru/profile',
                'with_code': 1
            }

            postvars = urllib.parse.urlencode(post_data)

            session = requests.Session()
            with open(use_cookie_file, 'r') as f:
                cookies = requests.utils.cookiejar_from_dict(json.load(f))
                session.cookies.update(cookies)

            response = session.post('https://passport.yandex.ru/registration-validations/auth/password/submit', data=postvars, cookies=session.cookies, timeout=DEFAULT_TIMEOUT, verify=certifi.where())

            data = response.json()

            if data['status'] == 'ok':
                out['TRACK_ID'] = data['track_id']
                out['CSRF_TOKEN'] = data['csrf_token']
                out['QR_URL'] = f'https://passport.yandex.ru/auth/magic/code/?track_id={data["track_id"]}'
            else:
                out['ERR_MSG'] = 'Ошибка получения QR-кода'
        else:
            out['ERR_MSG'] = 'Ошибка получения CSRF-токена'
        out['AUTHORIZED'] = None
        return out
    
    def confirmQrCode(self, track_id, csrf_token):
        out = {}
        use_cookie_file = os.path.join(self.cache_dir,'cookie_qr')
        post_data = {
            'csrf_token': csrf_token,
            'track_id': track_id
        }

        # Encode the post data as application/x-www-form-urlencoded
        postvars = urllib.parse.urlencode(post_data)

        url = 'https://passport.yandex.ru/auth/new/magic/status/'

        # Set up a session for persistent cookies
        session = requests.Session()
        with open(use_cookie_file, 'r') as f:
            cookies = requests.utils.cookiejar_from_dict(json.load(f))
            session.cookies.update(cookies)

        # Perform POST request
        response = session.post(url, data=postvars, cookies=session.cookies, timeout=DEFAULT_TIMEOUT, verify=certifi.where())

        # Assuming the response is JSON
        data = response.json()

        if isinstance(data, dict) and (data.get('status','') == 'ok' or data.get('errors', [''])[0] == 'account.auth_passed'):
            # Rename the cookie file (overwrite the existing cookie file)
            with open(self.cookie_path, 'w') as f:
                json.dump(requests.utils.dict_from_cookiejar(session.cookies), f)

            # Check the cookie with an API request
            check_cookie = self.api_request('https://iot.quasar.yandex.ru/m/user/scenarios')

            if check_cookie['status'] != 'ok':
                os.remove(self.cookie_path)
                out['AUTHORIZED'] = False
                return out
            else:
                out['AUTHORIZED'] = True
                return out
        else:
            out['ERR_MSG'] = 'Авторизация не пройдена. Попробуйте ещё раз.'

        # Provide other relevant info in the output
        out['TRACK_ID'] = track_id
        out['QR_URL'] = f'https://passport.yandex.ru/auth/magic/code/?track_id={track_id}'
        out['CSRF_TOKEN'] = csrf_token
        out['AUTHORIZED'] = None
        return out