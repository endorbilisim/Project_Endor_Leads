"""Clutch connector (v1.2).

Her kaynak kendi dosyasında — bu dosya yalnızca Clutch'a özgüdür, ortak mantık
base.py'de. Listing + detay ayrı aşamalar. Parser iki stratejili: önce JSON-LD
(LocalBusiness), yoksa CSS selector — Clutch DOM'unu sık değiştirdiği için.

Cloudflare bypass YOK: 403/challenge görülürse ClutchBlocked yükseltilir ve
kullanıcı kaydedilmiş HTML yükleme moduna yönlendirilir (ban riski sıfır).

Pagination: ilk sayfadaki pagination bileşeninden son sayfa numarası okunur;
ayrıca bir sayfa 0 kart dönerse crawl güvenle durur (son sayfada hata alınmaz).
"""
from __future__ import annotations
import json
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup

from .base import Connector

BASE = "https://clutch.co"
CF_MARKERS = ("just a moment", "cf-challenge", "cloudflare", "attention required",
              "checking your browser", "cf-browser-verification")

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


class ClutchBlocked(Exception):
    """Cloudflare engeli — kaydedilmiş HTML moduna geç."""


class ClutchConnector(Connector):
    name = "clutch"
    label = "Clutch"
    is_directory_source = True
    default_settings = {
        "active": False,
        "delay_min": 20,
        "delay_max": 30,
        "filter_url": "",
        "user_agent": "Mozilla/5.0 (compatible; EndorLeadBot/1.0)",
    }

    # --- Ağ ---
    def fetch(self, url: str) -> str:
        self.sleep()  # 60-90 sn (connector içinde)
        headers = {"User-Agent": self.settings.get("user_agent", ""),
                   "Accept-Language": "en-US,en;q=0.9"}
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 403 or self._looks_blocked(r.text):
            raise ClutchBlocked(f"Cloudflare engeli ({r.status_code}) — kaydedilmiş HTML yükle: {url}")
        r.raise_for_status()
        return r.text

    @staticmethod
    def _looks_blocked(html: str) -> bool:
        head = (html or "")[:3000].lower()
        return any(m in head for m in CF_MARKERS)

    # --- Pagination ---
    @staticmethod
    def page_url(filter_url: str, page: int) -> str:
        """1. sayfa = temel URL (page paramı yok); 2+ için &page=N."""
        if page <= 1:
            return filter_url
        parts = urlparse(filter_url)
        q = parse_qs(parts.query)
        q["page"] = [str(page)]
        # parse_qs listelerini düzleştir, çoklu değerleri koru
        flat = []
        for k, vs in q.items():
            for v in vs:
                flat.append((k, v))
        return urlunparse(parts._replace(query=urlencode(flat)))

    @staticmethod
    def detect_last_page(html: str) -> int:
        """Pagination bileşenindeki en büyük sayfa numarası. Bulunamazsa 1."""
        soup = BeautifulSoup(html, "lxml")
        pag = soup.select_one("[class*=pagination]")
        if not pag:
            return 1
        while pag.parent and "pagination" in " ".join(pag.parent.get("class") or []):
            pag = pag.parent
        nums = [1]
        for a in pag.find_all("a", href=True):
            m = re.search(r"[?&]page=(\d+)", a["href"])
            if m:
                nums.append(int(m.group(1)))
        return max(nums)

    # --- Listing ---
    def parse_listing(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        out = []
        for title in soup.find_all(class_="provider__title"):
            card = title.find_parent(class_=re.compile(r"provider(-row|-list-item|$)"))
            if not card:
                continue
            a = card.find("a", href=re.compile(r"/profile/"))
            profile = urljoin(BASE, a["href"].split("#")[0].strip()) if a else None
            loc = self._txt(card, "location")
            country, serves = self._card_country(loc)
            rate = self._txt(card, "hourly-rate")
            emp = self._txt(card, "employees-count")
            out.append({
                "name": title.get_text(strip=True),
                "source": "clutch",
                "source_url": profile,
                "location": None if serves else loc,   # "Serves X" merkez değildir
                "country": country,
                "hourly_rate": rate,
                "rate_low": _rate_low(rate),
                "team_size": emp,
                "size_low": _size_low(emp),
            })
        return out

    @staticmethod
    def _txt(card, cls):
        el = card.find(class_=cls)
        return el.get_text(" ", strip=True) if el else None

    @staticmethod
    def _card_country(loc: str | None):
        """('Hamburg, Germany' -> 'Germany', serves=False) / ('Serves Germany' -> None, True)."""
        if not loc:
            return None, False
        if loc.lower().startswith("serves "):
            return None, True
        m = re.search(r",\s*([A-Za-zÄÖÜäöüß .]+)$", loc)
        return (m.group(1).strip() if m else None), False

    # --- Detay ---
    def parse_detail(self, html: str) -> dict:
        soup = BeautifulSoup(html, "lxml")
        out = {"source": "clutch", "detail_status": "done"}

        # Strateji 1: JSON-LD LocalBusiness
        ld = self._json_ld(soup)
        if ld:
            out["name"] = ld.get("name")
            out["website"] = ld.get("sameAs")
            out["source_url"] = ld.get("url")
            out["tagline"] = (ld.get("description") or "")[:300] or None
            out["hourly_rate"] = ld.get("priceRange")
            out["rate_low"] = _rate_low(ld.get("priceRange"))
            fd = ld.get("foundingDate")
            out["founded_year"] = int(fd) if str(fd).isdigit() else None
            agg = ld.get("aggregateRating") or {}
            out["review_count"] = _int(agg.get("reviewCount"))
            out["rating"] = agg.get("ratingValue")
            addr = ld.get("address") or {}
            out["hq_country"] = addr.get("addressCountry")
            out["location"] = addr.get("addressLocality")
            tel = ld.get("telephone")
            out["phone"] = tel if tel and tel.strip("0") else None

        # Strateji 2 / tamamlayıcı: HQ bloğu (ekip büyüklüğü + merkez)
        hq = self._hq_block(soup)
        if hq:
            out.setdefault("location", hq.get("city"))
            if hq.get("country"):
                out["hq_country"] = hq["country"]
            if hq.get("team_size"):
                out["team_size"] = hq["team_size"]
                out["size_low"] = _size_low(hq["team_size"])

        # Tüm ofis ülkeleri
        out["locations"] = self._all_locations(soup, out.get("hq_country"))

        # Servis dağılımı (Service Lines sekmesi)
        services = self._services(soup)
        if services:
            out["services_json"] = json.dumps(services, ensure_ascii=False)

        # Son yorum tarihi
        out["last_review"] = self._last_review(soup)
        return out

    @staticmethod
    def _json_ld(soup):
        for s in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(s.string or "")
            except (ValueError, TypeError):
                continue
            if isinstance(d, dict) and d.get("@type") == "LocalBusiness":
                return d
        return None

    @staticmethod
    def _hq_block(soup):
        label = soup.find(string=re.compile(r"^\s*Headquarters\s*$"))
        if not label:
            return None
        block = label.find_parent().find_parent()
        txt = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
        out = {}
        # "... City , Country postalcode 10 - 15 +49..."
        mteam = re.search(r"(\d{1,4}\s*-\s*\d{1,4})", txt)
        if mteam:
            out["team_size"] = mteam.group(1).replace(" ", "")
        mgeo = re.search(r"([A-Za-zÄÖÜäöüß.\- ]+?)\s*,\s*([A-Za-zÄÖÜäöüß .]+?)\s+\d", txt)
        if mgeo:
            out["city"] = f"{mgeo.group(1).strip()}, {mgeo.group(2).strip()}"
            out["country"] = mgeo.group(2).strip()
        return out

    @staticmethod
    def _all_locations(soup, hq_country):
        """Profildeki tüm ofis ülkelerini topla (hedef-ofis kuralı için)."""
        countries = set()
        if hq_country:
            countries.add(hq_country)
        # 'location' sınıflı elemanlar + genel "City, Country" kalıpları.
        # Kısa büyük-harf eyalet kodları (NY, CA...) ülke değildir — atla.
        for el in soup.find_all(class_=re.compile(r"location", re.I)):
            t = el.get_text(" ", strip=True)
            m = re.search(r",\s*([A-Za-zÄÖÜäöüß .]{3,})$", t)
            if m:
                val = m.group(1).strip()
                if not (val.isupper() and len(val) <= 3):
                    countries.add(val)
        # Bilinen ülke adlarını tam metinden de yakala (locations widget'ı modalda olabilir)
        known = ("Germany|Austria|Switzerland|Netherlands|Belgium|Sweden|Denmark|"
                 "Norway|Finland|Ukraine|Poland|Estonia|Romania|Bulgaria|Serbia|"
                 "India|Pakistan|Bangladesh|Belarus|Vietnam|Philippines|United States|"
                 "United Kingdom|Spain|Portugal")
        for m in re.finditer(r",\s*(" + known + r")\b", soup.get_text(" ")):
            countries.add(m.group(1))
        return sorted(countries)

    @staticmethod
    def _services(soup):
        dl = soup.find("dl", class_="chart-legend__list")
        if not dl:
            return {}
        out = {}
        for item in dl.find_all("div", class_="chart-legend__item"):
            dt, dd = item.find("dt"), item.find("dd")
            if dt and dd:
                m = re.search(r"(\d{1,3})", dd.get_text())
                if m:
                    out[dt.get_text(strip=True)] = int(m.group(1))
        return out

    @staticmethod
    def _last_review(soup):
        best = None
        for d in re.findall(
                r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},?\s+20\d\d",
                soup.get_text(" ")):
            try:
                mon = MONTHS[d[:3]]
                nums = re.findall(r"\d+", d)
                day, year = int(nums[0]), int(nums[1])
                dt = datetime(year, mon, day)
                if best is None or dt > best:
                    best = dt
            except (KeyError, ValueError, IndexError):
                continue
        return best.date().isoformat() if best else None


# --- Ortak yardımcılar (modül düzeyi) ---

def _int(v):
    if v is None:
        return None
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else None


def _rate_low(rate: str | None):
    """'$100 - $149 / hr' -> 100 ; '< $25' -> 0 ; '$300+' -> 300."""
    if not rate:
        return None
    nums = re.findall(r"\d+", rate.replace(",", ""))
    if not nums:
        return None
    if rate.strip().startswith("<"):
        return 0
    return int(nums[0])


def _size_low(size: str | None):
    """'10 - 49' -> 10 ; '250 - 999' -> 250 ; '10000+' -> 10000."""
    if not size:
        return None
    nums = re.findall(r"\d+", size.replace(",", ""))
    return int(nums[0]) if nums else None
