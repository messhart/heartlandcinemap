"""Polite HTTP fetching shared by all adapters.

One session per run: identifies the project + contact in the User-Agent,
enforces a minimum delay between requests, and refuses to fetch URLs that a
site's robots.txt disallows.
"""

from __future__ import annotations

import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

USER_AGENT = (
    "HeartlandCinemapBot/0.1 "
    "(+https://github.com/messhart/heartlandcinemap; contact: dyfttym@protonmail.ch)"
)

MIN_DELAY_S = 1.0
TIMEOUT_S = 30


class RobotsDisallowed(RuntimeError):
    pass


class PoliteSession:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request = 0.0

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser:
        origin = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if origin not in self._robots:
            rp = urllib.robotparser.RobotFileParser(origin + "/robots.txt")
            try:
                rp.read()
            except OSError:
                rp.allow_all = True  # unreachable robots.txt -> assume allowed
            self._robots[origin] = rp
        return self._robots[origin]

    def get(self, url: str) -> requests.Response:
        if not self._robots_for(url).can_fetch(USER_AGENT, url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        wait = MIN_DELAY_S - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        resp = self._session.get(url, timeout=TIMEOUT_S)
        resp.raise_for_status()
        return resp
