"""Ortak connector arayüzü. Her kaynak (Clutch, Sortlist, Impressum...) bunu uygular.

Kural: Her yeni kaynak connectors/ altına yeni bir dosya olarak eklenir.
Mevcut hiçbir dosya değişmez. Connector kendi varsayılan ayarlarını bildirir;
böylece ayarlar panelinde otomatik belirir.
"""
from __future__ import annotations
import random
import time


class Connector:
    name: str = "base"
    label: str | None = None            # arayüzde görünecek ad (yoksa name.title())
    is_directory_source: bool = False   # "Kaynaklar" menüsünde crawl kaynağı olarak görünsün mü

    # Ayarlar panelinde otomatik görünecek varsayılanlar
    default_settings: dict = {
        "active": True,
        "delay_min": 3,
        "delay_max": 5,
        "filter_url": "",
        "user_agent": "Mozilla/5.0 (compatible; EndorLeadBot/1.0)",
    }

    def __init__(self, settings: dict | None = None):
        self.settings = {**self.default_settings, **(settings or {})}

    # --- Alt sınıfların uygulayacağı arayüz ---
    def parse_listing(self, html: str) -> list[dict]:
        raise NotImplementedError

    def parse_detail(self, html: str) -> dict:
        raise NotImplementedError

    def fetch(self, url: str) -> str:
        """URL'yi çek. Rate limit connector içinde uygulanır."""
        raise NotImplementedError

    # --- Ortak yardımcı: bekleme (rate limit tek yerden) ---
    def sleep(self):
        lo = float(self.settings.get("delay_min", 0))
        hi = float(self.settings.get("delay_max", lo))
        if hi > 0:
            time.sleep(random.uniform(lo, hi))
