import re
import time
import random
import hashlib
import base64
from functools import reduce

import requests
from bs4 import BeautifulSoup

ONDEMAND_URL = "https://abs.twimg.com/responsive-web/client-web/ondemand.s.{hash}a.js"
KEYWORD = "obfiowerehiring"
EPOCH_MS = 1682924400000

_INDICES_RE = re.compile(r"(\(\w{1}\[(\d{1,2})\],\s*16\))+")


class _Cubic:
    def __init__(self, curves):
        self.curves = curves

    def get_value(self, time):
        start_gradient = 0.0
        end_gradient = 0.0
        start = 0.0
        mid = 0.0
        end = 1.0

        if time <= 0.0:
            if self.curves[0] > 0.0:
                start_gradient = self.curves[1] / self.curves[0]
            elif self.curves[1] == 0.0 and self.curves[2] > 0.0:
                start_gradient = self.curves[3] / self.curves[2]
            return start_gradient * time

        if time >= 1.0:
            if self.curves[2] < 1.0:
                end_gradient = (self.curves[3] - 1.0) / (self.curves[2] - 1.0)
            elif self.curves[2] == 1.0 and self.curves[0] < 1.0:
                end_gradient = (self.curves[1] - 1.0) / (self.curves[0] - 1.0)
            return 1.0 + end_gradient * (time - 1.0)

        while start < end:
            mid = (start + end) / 2
            x_est = self._calculate(self.curves[0], self.curves[2], mid)
            if abs(time - x_est) < 0.00001:
                return self._calculate(self.curves[1], self.curves[3], mid)
            if x_est < time:
                start = mid
            else:
                end = mid
        return self._calculate(self.curves[1], self.curves[3], mid)

    @staticmethod
    def _calculate(a, b, m):
        return 3.0 * a * (1 - m) * (1 - m) * m + 3.0 * b * (1 - m) * m * m + m * m * m


class _MathUtils:
    @staticmethod
    def is_odd(num):
        return -1.0 if num % 2 else 0.0

    @staticmethod
    def interpolate_num(from_val, to_val, f):
        if isinstance(from_val, bool) and isinstance(to_val, bool):
            return from_val if f < 0.5 else to_val
        return from_val * (1 - f) + to_val * f

    @staticmethod
    def interpolate(from_list, to_list, f):
        return [_MathUtils.interpolate_num(from_list[i], to_list[i], f)
                for i in range(min(len(from_list), len(to_list)))]

    @staticmethod
    def convert_rotation_to_matrix(rotation):
        import math
        rad = math.radians(rotation)
        return [math.cos(rad), -math.sin(rad), math.sin(rad), math.cos(rad)]

    @staticmethod
    def float_to_hex(x):
        result = []
        quotient = int(x)
        fraction = x - quotient
        while quotient > 0:
            quotient = int(x / 16)
            remainder = int(x - (float(quotient) * 16))
            if remainder > 9:
                result.insert(0, chr(remainder + 55))
            else:
                result.insert(0, str(remainder))
            x = float(quotient)
        if fraction == 0:
            return "".join(result)
        result.append(".")
        while fraction > 0:
            fraction *= 16
            integer = int(fraction)
            fraction -= float(integer)
            if integer > 9:
                result.append(chr(integer + 55))
            else:
                result.append(str(integer))
        return "".join(result)

    @staticmethod
    def round(num):
        import math
        x = math.floor(num)
        if (num - x) >= 0.5:
            x = math.ceil(num)
        return math.copysign(x, num)


class XTransactionIdGenerator:
    def __init__(self, session=None, ondemand_content=None, home_page=None, fetch_session=None):
        self.session = session or requests.Session()
        self.fetch_session = fetch_session or self.session
        self._cache_home = home_page
        self._cache_ondemand = ondemand_content
        self.row_index = None
        self.key_bytes_indices = None
        self.key_bytes = None
        self.animation_key = None

    def _get_home_page(self):
        if self._cache_home is not None:
            return self._cache_home
        r = self.fetch_session.get("https://x.com/home")
        r.raise_for_status()
        self._cache_home = r.text
        return self._cache_home

    def _get_ondemand(self, home_html):
        if self._cache_ondemand is not None:
            return self._cache_ondemand
        m = re.search(r'(\d+):"ondemand\.s"', home_html)
        if m:
            chunk_id = m.group(1)
            m2 = re.search(re.escape(chunk_id) + r':"([a-f0-9]+)"', home_html)
            if not m2:
                raise RuntimeError("Couldn't find ondemand.s hash in home page")
            hsh = m2.group(1)
        else:
            m = re.search(r"ondemand\.s\.([a-f0-9]+a)\.js", home_html)
            if not m:
                raise RuntimeError("Couldn't find ondemand.s.js reference in home page")
            hsh = m.group(1)[:-1]
        r = self.fetch_session.get(ONDEMAND_URL.format(hash=hsh))
        r.raise_for_status()
        self._cache_ondemand = r.text
        return self._cache_ondemand

    def _init(self):
        if self.key_bytes is not None:
            return
        home_html = self._get_home_page()
        ondemand = self._get_ondemand(home_html)

        indices = [int(m.group(2)) for m in _INDICES_RE.finditer(ondemand)]
        if not indices:
            raise RuntimeError("Couldn't get KEY_BYTE indices")
        self.row_index, self.key_bytes_indices = indices[0], indices[1:]

        soup = BeautifulSoup(home_html, "html.parser")
        element = soup.select_one("meta[name='twitter-site-verification']")
        if not element:
            raise RuntimeError("Couldn't get twitter-site-verification key")
        key = element.get("content")
        self.key_bytes = list(base64.b64decode(key.encode()))
        self.animation_key = self._get_animation_key(soup)

    def generate(self, method, path):
        self._init()
        time_now = int((time.time() * 1000 - EPOCH_MS) / 1000)
        time_now_bytes = [(time_now >> (i * 8)) & 0xFF for i in range(4)]
        hash_val = hashlib.sha256(
            f"{method}!{path}!{time_now}{KEYWORD}{self.animation_key}".encode()
        ).digest()
        random_num = random.randint(0, 255)
        bytes_arr = [*self.key_bytes, *time_now_bytes, *list(hash_val)[:16], 3]
        out = bytearray([random_num, *[b ^ random_num for b in bytes_arr]])
        return base64.b64encode(out).decode().rstrip("=")

    def _get_animation_key(self, soup):
        row_index = self.key_bytes[self.row_index] % 16
        frame_time = reduce(
            lambda x, y: x * y,
            [self.key_bytes[i] % 16 for i in self.key_bytes_indices],
        )
        frame_time = _MathUtils.round(frame_time / 10) * 10

        frames = soup.select("[id^='loading-x-anim']")
        if not frames:
            raise RuntimeError("Couldn't find loading-x-anim frames")
        path_data = list(list(frames[self.key_bytes[5] % 4].children)[0].children)[1].get("d")[9:]
        arr = [
            [int(x) for x in re.sub(r"[^\d]+", " ", item).strip().split()]
            for item in path_data.split("C")
        ]
        frame_row = arr[row_index]
        target_time = float(frame_time) / 4096
        return self._animate(frame_row, target_time)

    def _animate(self, frames, target_time):
        import math

        def solve(value, min_val, max_val, rounding):
            result = value * (max_val - min_val) / 255 + min_val
            return math.floor(result) if rounding else round(result, 2)

        from_color = [float(x) for x in [*frames[:3], 1]]
        to_color = [float(x) for x in [*frames[3:6], 1]]
        to_rotation = [solve(float(frames[6]), 60.0, 360.0, True)]

        curves = [
            solve(float(item), _MathUtils.is_odd(i), 1.0, False)
            for i, item in enumerate(frames[7:])
        ]

        val = _Cubic(curves).get_value(target_time)
        color = [max(0, min(255, v)) for v in _MathUtils.interpolate(from_color, to_color, val)]
        rotation = _MathUtils.interpolate([0.0], to_rotation, val)
        matrix = _MathUtils.convert_rotation_to_matrix(rotation[0])

        str_arr = [format(round(value), "x") for value in color[:-1]]
        for value in matrix:
            rounded = abs(round(value, 2))
            hex_value = _MathUtils.float_to_hex(rounded)
            str_arr.append(f"0{hex_value}".lower() if hex_value.startswith(".") else hex_value or "0")
        str_arr.extend(["0", "0"])

        return re.sub(r"[.-]", "", "".join(str_arr))
