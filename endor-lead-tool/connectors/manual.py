"""Elle domain girişi connector'ı.

Kaynak crawl etmez; kullanıcının girdiği domain'i normalize eder ve isim/website
alanlarını üretir. Skorlama ve zenginleştirme diğer aşamalarda yapılır.
"""
from __future__ import annotations
import re
from urllib.parse import urlparse

from .base import Connector


class ManualConnector(Connector):
    name = "manual"
    default_settings = {
        "active": True,
        "delay_min": 0,
        "delay_max": 0,
        "filter_url": "",
        "user_agent": "",
    }

    @staticmethod
    def normalize_domain(raw: str) -> dict | None:
        """'https://www.foo.de/x' veya 'foo.de' -> {name, website}."""
        raw = (raw or "").strip()
        if not raw:
            return None
        if not re.match(r"^https?://", raw, re.I):
            raw = "http://" + raw
        host = urlparse(raw).netloc.lower()
        host = host.split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        if "." not in host:
            return None
        # ajans adı tahmini: kök alan adının ilk parçası
        label = host.split(".")[0]
        name = label.replace("-", " ").replace("_", " ").title()
        return {"name": name, "website": "https://" + host, "source": "manual"}

    def parse_listing(self, html: str) -> list[dict]:
        return []

    def parse_detail(self, html: str) -> dict:
        return {}

    def fetch(self, url: str) -> str:
        return ""
