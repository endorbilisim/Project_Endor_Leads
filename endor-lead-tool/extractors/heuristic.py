"""Regex + kalıp tabanlı iletişim çıkarımı (ücretsiz, önce bu denenir).

Girdi düz metindir (impressum connector HTML'i metne indirger). Bulunamayan
alan None döner. LLM fallback yalnızca burada isim yoksa veya sadece genel mail
bulunduysa tetiklenir.
"""
from __future__ import annotations
import re

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# Uluslararası formatlı telefon: +49 30 1234567 / 0049... / +47 ...
PHONE_RE = re.compile(r"(?:\+|00)\d[\d\s().\-/]{6,}\d")
GENERIC_PREFIXES = ("info@", "office@", "hello@", "kontakt@", "mail@",
                    "contact@", "hallo@", "post@", "moin@", "hej@")

# İsim etiketleri (DACH + Nordik). Etiketin sağındaki metni alırız.
NAME_LABELS = [
    "Geschäftsführerin", "Geschäftsführer", "Vertreten durch", "Inhaberin",
    "Inhaber", "Verantwortlich für den Inhalt", "Verantwortlich",
    "Managing Director", "Founder", "Owner", "CEO", "Eier", "Daglig leder",
    "Kontaktperson",
]

# İsim gibi görünen: 2-4 Baş harfi büyük kelime (Unicode harfler dahil).
# Satır içi yatay boşluk kullanılır — yeni satıra taşmaz (aksi halde bir sonraki
# etiket 'Telefon' vb. isme eklenir).
NAME_TOKEN = r"[A-ZÄÖÜÅÆØ][\wäöüåæøÄÖÜÅÆØ.'\-]+"
NAME_PATTERN = re.compile(rf"{NAME_TOKEN}(?:[ \t]+{NAME_TOKEN}){{1,3}}")


def _is_generic(email: str) -> bool:
    return email.lower().startswith(GENERIC_PREFIXES)


def _valid_name(name: str) -> bool:
    name = name.strip(" .,:;-")
    if not (2 <= len(name) <= 60):
        return False
    if "@" in name or "http" in name.lower():
        return False
    # en az bir boşluk (ad soyad) beklenir ama tek kelimelik unvanları da ele
    banned = {"gmbh", "impressum", "kontakt", "adresse", "telefon", "email",
              "e-mail", "umsatzsteuer", "handelsregister", "geschäftsführer"}
    if name.lower() in banned:
        return False
    return True


def _find_emails(text: str) -> list[str]:
    emails = []
    for e in EMAIL_RE.findall(text):
        if e not in emails:
            emails.append(e)
    # Ters yazılmış e-postaları kontrol et (bot koruması: smetsys.cc@ofni -> info@cc.systems)
    for line in text.splitlines():
        if "@" in line:
            rev_line = line.strip()[::-1]
            for e in EMAIL_RE.findall(rev_line):
                if e not in emails:
                    emails.append(e)
    return emails


def extract(text: str) -> dict:
    """Düz metinden isim/e-posta/telefon çıkar."""
    text = text or ""
    result = {"name": None, "role": None, "email": None, "phone": None,
              "email_is_generic": 0, "method": "heuristic"}

    # --- E-posta ---
    emails = _find_emails(text)
    if emails:
        personal = [e for e in emails if not _is_generic(e)]
        chosen = personal[0] if personal else emails[0]
        result["email"] = chosen
        result["email_is_generic"] = 0 if not _is_generic(chosen) else 1

    # --- Telefon ---
    phones = PHONE_RE.findall(text)
    if phones:
        result["phone"] = re.sub(r"\s{2,}", " ", phones[0]).strip()

    # --- İsim: etiket tabanlı ---
    for label in NAME_LABELS:
        # "Geschäftsführer: Max Mustermann" veya "Geschäftsführer\nMax Mustermann"
        m = re.search(re.escape(label) + r"\s*[:\-]?\s*\n?\s*(" + NAME_PATTERN.pattern + r")", text)
        if m:
            cand = m.group(1).strip()
            if _valid_name(cand):
                result["name"] = cand
                result["role"] = label
                break

    return result
