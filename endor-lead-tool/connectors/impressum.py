"""Impressum connector (v1.1).

Girdi: ajansın KENDİ web sitesi (Clutch değil — Cloudflare yok, 3-5 sn yeter).
Akış:
  1) Doğru sayfayı bul (footer linkleri + kalıp önceliği)
  2) Sayfayı düz metne indirge (script/style/nav/svg at)
  3) Heuristic çıkarım (ücretsiz)
  4) Tutmazsa LLM fallback (extractors/llm.py)
  5) Doğrulama (llm katmanında)
  6) Bonus sinyal: kariyer sayfasında developer ilanı -> hiring_devs

Sonuç: impressum_status = done | manual | error
"""
from __future__ import annotations
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .base import Connector
from extractors import heuristic, llm

# Sayfa bulma kalıpları — öncelik sırasıyla
PAGE_PATTERNS = [
    "impressum", "imprint", "kontakt",           # DACH
    "over-ons",                                   # Hollanda
    "om-oss", "om-os",                            # Nordik
    "contact", "about", "team", "legal",          # genel fallback
]
CAREER_PATTERNS = ["karriere", "jobs", "career", "careers", "stellen", "vacatures", "jobb"]
DEV_KEYWORDS = ["entwickler", "developer", "frontend", "backend", "full-stack",
                "fullstack", "software engineer", "programmierer", "utvikler", "udvikler"]


class ImpressumConnector(Connector):
    name = "impressum"
    default_settings = {
        "active": True,
        "delay_min": 3,
        "delay_max": 5,
        "filter_url": "",
        "user_agent": "Mozilla/5.0 (compatible; EndorLeadBot/1.0)",
    }

    # --- Ağ ---
    def fetch(self, url: str) -> str:
        self.sleep()  # rate limit connector içinde
        headers = {"User-Agent": self.settings.get("user_agent", "")}
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        return r.text

    # --- Adım 1: doğru sayfayı bul ---
    @staticmethod
    def find_contact_url(home_html: str, base_url: str) -> str | None:
        soup = BeautifulSoup(home_html, "lxml")
        candidates = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            text = (a.get_text() or "").lower()
            hay = (href + " " + text).lower()
            for i, pat in enumerate(PAGE_PATTERNS):
                if pat in hay:
                    candidates.append((i, urljoin(base_url, href)))
                    break
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])  # düşük index = yüksek öncelik
        return candidates[0][1]

    @staticmethod
    def find_career_url(home_html: str, base_url: str) -> str | None:
        soup = BeautifulSoup(home_html, "lxml")
        for a in soup.find_all("a", href=True):
            hay = (a["href"] + " " + (a.get_text() or "")).lower()
            if any(p in hay for p in CAREER_PATTERNS):
                return urljoin(base_url, a["href"])
        return None

    # --- Adım 2: metne indirge ---
    @staticmethod
    def html_to_text(html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "svg", "noscript", "header", "footer"]):
            tag.decompose()
        return soup.get_text("\n", strip=True)

    # --- Ana akış: bir domain'i işle -> dict ---
    def enrich(self, website: str) -> dict:
        """Website'ten iletişim bilgisi çıkar. app tarafı sonucu DB'ye yazarken
        manuel-koruma kuralını uygular. Ağı burada yönetiriz."""
        out = {
            "impressum_status": "error", "impressum_url": None, "impressum_raw": None,
            "impressum_method": None, "contact_name": None, "contact_email": None,
            "phone": None, "email_is_generic": 0, "hiring_devs": 0,
        }
        base = website if re.match(r"^https?://", website or "") else "http://" + (website or "")
        try:
            home = self.fetch(base)
        except Exception as e:
            out["impressum_raw"] = f"[Ana sayfa çekilemedi: {e}]"
            return out

        # Bonus sinyal: kariyer sayfası
        try:
            career = self.find_career_url(home, base)
            if career:
                ctext = self.html_to_text(self.fetch(career)).lower()
                if any(k in ctext for k in DEV_KEYWORDS):
                    out["hiring_devs"] = 1
        except Exception:
            pass  # bonus sinyal hatası akışı durdurmaz

        # İletişim sayfası
        contact_url = self.find_contact_url(home, base) or base
        out["impressum_url"] = contact_url
        try:
            html = self.fetch(contact_url) if contact_url != base else home
        except Exception as e:
            out["impressum_raw"] = f"[İletişim sayfası çekilemedi: {e}]"
            return out

        text = self.html_to_text(html)
        out["impressum_raw"] = text[:8000]

        # Adım 3: heuristic
        h = heuristic.extract(text)
        out["contact_name"] = h["name"]
        out["contact_email"] = h["email"]
        out["phone"] = h["phone"]
        out["email_is_generic"] = h["email_is_generic"]
        out["impressum_method"] = "heuristic"

        # Adım 4: LLM fallback tetiği — isim yok VEYA sadece genel mail
        need_llm = (not h["name"]) or (h["email"] and h["email_is_generic"])
        if need_llm:
            verified = llm.extract_and_verify(text, agency_domain=urlparse(base).netloc)
            if verified is None:
                # key yok / LLM başarısız -> heuristic sonucu kalır, ama eksikse manual
                out["impressum_status"] = "manual" if not h["name"] else "done"
                return out
            out["impressum_method"] = "llm"
            if verified.get("_manual"):
                out["impressum_status"] = "manual"
            # doğrulanmış alanları yaz (None değilse)
            for f in ("name", "email", "phone"):
                if verified.get(f):
                    key = "contact_name" if f == "name" else ("contact_email" if f == "email" else "phone")
                    out[key] = verified[f]
            if verified.get("email"):
                out["email_is_generic"] = 1 if heuristic._is_generic(verified["email"]) else 0

        if out["impressum_status"] != "manual":
            out["impressum_status"] = "done" if out["contact_name"] else "manual"
        return out

    # Base arayüzü (Impressum listing/detay kullanmaz)
    def parse_listing(self, html: str) -> list[dict]:
        return []

    def parse_detail(self, html: str) -> dict:
        return self.enrich(html)  # kullanılmaz
