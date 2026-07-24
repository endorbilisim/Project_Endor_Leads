"""Kural tabanlı skorlama (AI kullanmaz). Tek fonksiyon: score_lead().

Puanlar, ülke listeleri ve eşikler koda gömülmez — settings'ten okunur.
scoring.py yalnızca şehir->ülke eşlemesini ve varsayılan mantığı tanımlar.

Kritik kural: ülke tespiti ajansın MERKEZİNE bakar, "serves Germany" ifadesine
değil. Kyiv merkezli "serves Germany" ajansı ELE olmalı.
"""
from __future__ import annotations
import json
import re

# Şehir -> ülke eşlemesi (kodda sabit; ülke listesinin kendisi ayarlardan yönetilir)
CITY_TO_COUNTRY = {
    # Almanya
    "berlin": "Almanya", "münchen": "Almanya", "munich": "Almanya",
    "hamburg": "Almanya", "köln": "Almanya", "cologne": "Almanya",
    "frankfurt": "Almanya", "stuttgart": "Almanya", "düsseldorf": "Almanya",
    "leipzig": "Almanya", "dresden": "Almanya", "hannover": "Almanya",
    "nürnberg": "Almanya", "bremen": "Almanya", "dortmund": "Almanya",
    # Avusturya
    "wien": "Avusturya", "vienna": "Avusturya", "graz": "Avusturya",
    "linz": "Avusturya", "salzburg": "Avusturya", "innsbruck": "Avusturya",
    # İsviçre
    "zürich": "İsviçre", "zurich": "İsviçre", "genf": "İsviçre",
    "geneva": "İsviçre", "genève": "İsviçre", "basel": "İsviçre",
    "bern": "İsviçre", "lausanne": "İsviçre", "luzern": "İsviçre",
    # Hollanda
    "amsterdam": "Hollanda", "rotterdam": "Hollanda", "utrecht": "Hollanda",
    "den haag": "Hollanda", "the hague": "Hollanda", "eindhoven": "Hollanda",
    "groningen": "Hollanda",
    # Belçika
    "brussels": "Belçika", "brüssel": "Belçika", "bruxelles": "Belçika",
    "antwerp": "Belçika", "antwerpen": "Belçika", "gent": "Belçika",
    "ghent": "Belçika", "leuven": "Belçika",
    # İsveç
    "stockholm": "İsveç", "gothenburg": "İsveç", "göteborg": "İsveç",
    "malmö": "İsveç", "malmo": "İsveç", "uppsala": "İsveç",
    # Danimarka
    "copenhagen": "Danimarka", "københavn": "Danimarka", "kobenhavn": "Danimarka",
    "aarhus": "Danimarka", "odense": "Danimarka", "aalborg": "Danimarka",
    # Norveç
    "oslo": "Norveç", "bergen": "Norveç", "trondheim": "Norveç",
    "stavanger": "Norveç",
    # Finlandiya
    "helsinki": "Finlandiya", "espoo": "Finlandiya", "tampere": "Finlandiya",
    "turku": "Finlandiya", "oulu": "Finlandiya",
    # Offshore örnek şehirler (ELE tespiti için yardımcı)
    "kyiv": "Ukrayna", "kiev": "Ukrayna", "lviv": "Ukrayna",
    "kharkiv": "Ukrayna", "warsaw": "Polonya", "warszawa": "Polonya",
    "krakow": "Polonya", "kraków": "Polonya", "wrocław": "Polonya",
    "bucharest": "Romanya", "bucurești": "Romanya", "cluj": "Romanya",
    "sofia": "Bulgaristan", "belgrade": "Sırbistan", "beograd": "Sırbistan",
    "bengaluru": "Hindistan", "bangalore": "Hindistan", "mumbai": "Hindistan",
    "delhi": "Hindistan", "hyderabad": "Hindistan", "pune": "Hindistan",
    "lahore": "Pakistan", "karachi": "Pakistan", "dhaka": "Bangladeş",
    "minsk": "Belarus", "hanoi": "Vietnam", "ho chi minh": "Vietnam",
    "manila": "Filipinler", "cairo": "Mısır", "buenos aires": "Arjantin",
    "bogota": "Kolombiya", "bogotá": "Kolombiya", "medellin": "Kolombiya",
}


# İngilizce/Almanca ülke adlarını ayarlarda kullanılan kanonik (Türkçe) adlara
# çevirir. Clutch İngilizce verir ("Germany"), ayarlar Türkçe tutar ("Almanya").
COUNTRY_ALIASES = {
    "germany": "Almanya", "deutschland": "Almanya",
    "austria": "Avusturya", "österreich": "Avusturya", "osterreich": "Avusturya",
    "switzerland": "İsviçre", "schweiz": "İsviçre",
    "netherlands": "Hollanda", "the netherlands": "Hollanda", "nederland": "Hollanda",
    "belgium": "Belçika", "belgië": "Belçika", "belgique": "Belçika",
    "sweden": "İsveç", "sverige": "İsveç",
    "denmark": "Danimarka", "danmark": "Danimarka",
    "norway": "Norveç", "norge": "Norveç",
    "finland": "Finlandiya", "suomi": "Finlandiya",
    "ukraine": "Ukrayna", "india": "Hindistan", "pakistan": "Pakistan",
    "bangladesh": "Bangladeş", "belarus": "Belarus", "poland": "Polonya",
    "polska": "Polonya", "romania": "Romanya", "bulgaria": "Bulgaristan",
    "serbia": "Sırbistan", "vietnam": "Vietnam", "philippines": "Filipinler",
    "egypt": "Mısır", "argentina": "Arjantin", "colombia": "Kolombiya",
    "united states": "ABD", "usa": "ABD", "united kingdom": "Birleşik Krallık",
    "estonia": "Estonya",
}


def normalize_country(raw: str | None) -> str | None:
    """Serbest ülke metnini kanonik ada çevir. Bilinmiyorsa olduğu gibi döndür."""
    if not raw:
        return None
    r = raw.strip()
    return COUNTRY_ALIASES.get(r.lower(), r)


def _to_int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def detect_country(lead: dict) -> tuple[str | None, bool]:
    """Ajansın MERKEZ ülkesini döndür. (ülke, kesin_mi).

    Önce açık country alanı, yoksa location/şehir adından çıkarım.
    """
    country = (lead.get("country") or "").strip()
    if country:
        return normalize_country(country), True

    loc = (lead.get("location") or "").lower()
    # "Serves Germany" gibi ifadeler MERKEZ değildir — atla
    if loc.startswith("serves "):
        return None, False
    # "Hamburg, Germany" -> önce ülke adından
    m = re.search(r",\s*([a-zäöüßé ]+)$", loc)
    if m:
        c = normalize_country(m.group(1).strip())
        if c:
            return c, True
    for city, ctry in CITY_TO_COUNTRY.items():
        if re.search(r"\b" + re.escape(city) + r"\b", loc):
            return ctry, True
    return None, False


def score_lead(lead: dict, settings: dict) -> tuple[int, dict, str]:
    """Tek skorlama fonksiyonu. -> (score, breakdown, verdict).

    settings: get_all_settings() çıktısı (target_countries, offshore_countries,
    weights, thresholds, negative_filter anahtarlarını içerir).
    """
    weights = settings.get("weights", {})
    targets = set(settings.get("target_countries", []))
    offshore = set(settings.get("offshore_countries", []))
    thresholds = settings.get("thresholds", {})
    neg = settings.get("negative_filter", {})

    breakdown: dict[str, int] = {}
    score = 0

    country, country_known = detect_country(lead)
    rate_low = _to_int(lead.get("rate_low")) if lead.get("rate_low") is not None else _to_int(lead.get("hourly_rate"))
    size_low = _to_int(lead.get("size_low")) if lead.get("size_low") is not None else _to_int(lead.get("team_size"))

    # --- Otomatik ELE (negatif filtre) ---
    min_rate = neg.get("min_rate", 50)
    max_team = neg.get("max_team", 250)
    ele_reasons = []
    if country and country in offshore:
        ele_reasons.append(f"merkez offshore ({country})")
    if rate_low is not None and rate_low < min_rate:
        ele_reasons.append(f"saatlik <${min_rate}")
    if size_low is not None and size_low >= max_team:
        ele_reasons.append(f"ekip {max_team}+")

    if ele_reasons:
        breakdown["ELE"] = 0
        breakdown["_ele_reasons"] = ele_reasons  # gösterim için
        return 0, breakdown, "ELE"

    # --- Pozitif sinyaller ---
    # Hedef ülke merkezli
    if country and country in targets:
        p = weights.get("target_country", 25)
        score += p
        breakdown["Hedef ülke merkezli"] = p
    elif not country_known:
        breakdown["_country_unknown"] = 1  # ülke belirsiz, +25 verilmedi

    # Saatlik ücret $100+
    if rate_low is not None and rate_low >= 100:
        p = weights.get("rate_high", 20)
        score += p
        breakdown["Saatlik $100+"] = p

    # Ekip büyüklüğü kademeli
    if size_low is not None:
        if 10 <= size_low <= 49:
            p = weights.get("team_ideal", 15)
            breakdown["Ekip 10-49"] = p
        elif 2 <= size_low <= 9:
            p = weights.get("team_small", 7)
            breakdown["Ekip 2-9"] = p
        elif 50 <= size_low <= 99:
            p = weights.get("team_large", 5)
            breakdown["Ekip 50-99"] = p
        else:  # 100-249 (250+ zaten ELE)
            p = weights.get("team_xlarge", 0)
            if p:
                breakdown["Ekip 100+"] = p
        score += p

    # Design/strateji ağırlıklı (dev %50 altı)
    dev_share = _dev_share(lead.get("services_json"))
    if dev_share is not None and dev_share < 50:
        p = weights.get("design_heavy", 15)
        score += p
        breakdown["Design/strateji ağırlıklı"] = p

    # Kariyer sayfasında developer ilanı
    if lead.get("hiring_devs"):
        p = weights.get("hiring_devs", 10)
        score += p
        breakdown["Developer ilanı"] = p

    # Impressum'da mail bulundu
    if lead.get("contact_email"):
        if lead.get("email_is_generic"):
            p = weights.get("generic_email", 5)
            breakdown["Genel mail (info@)"] = p
        else:
            p = weights.get("personal_email", 10)
            breakdown["Kişisel mail"] = p
        score += p

    # Aktif (son yorum < 12 ay)
    if _is_recent(lead.get("last_review")):
        p = weights.get("active_recent", 5)
        score += p
        breakdown["Aktif (<12 ay)"] = p

    # Rakip sinyali (ABD/nötr cephe + offshore ofisli geliştirme evi).
    # Hedef ofisi olduğu için ülkeden ELE olmadı ama muhtemelen rakip -> ceza.
    if lead.get("is_competitor"):
        p = weights.get("competitor_penalty", 40)
        score -= p
        breakdown["Rakip sinyali (-)"] = -p

    score = max(0, min(100, score))
    verdict = _classify(score, thresholds)
    return score, breakdown, verdict


def _dev_share(services_json) -> float | None:
    """services_json içinden development/programming yüzdesini bul."""
    if not services_json:
        return None
    try:
        data = json.loads(services_json) if isinstance(services_json, str) else services_json
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    total = 0.0
    found = False
    for k, v in data.items():
        kl = str(k).lower()
        if any(w in kl for w in ("develop", "programming", "software", "web dev", "custom software")):
            try:
                total += float(v)
                found = True
            except (ValueError, TypeError):
                continue
    return total if found else None


def _is_recent(last_review) -> bool:
    if not last_review:
        return False
    from datetime import datetime, timezone
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(str(last_review)[:19], fmt).replace(tzinfo=timezone.utc)
            months = (datetime.now(timezone.utc) - dt).days / 30.0
            return months < 12
        except ValueError:
            continue
    return False


def _classify(score: int, thresholds: dict) -> str:
    sicak = thresholds.get("sicak", 75)
    orta = thresholds.get("orta", 55)
    zayif = thresholds.get("zayif", 35)
    if score >= sicak:
        return "SICAK"
    if score >= orta:
        return "ORTA"
    if score >= zayif:
        return "ZAYIF"
    return "ELE"
