"""SQLite şema + yardımcılar. Tek dosya (leads.db), harici servis yok."""
import sqlite3
import json
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leads.db")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# --- Şema -------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- kimlik
    name            TEXT NOT NULL,
    website         TEXT,
    source          TEXT,
    source_url      TEXT,
    -- konum
    location        TEXT,
    country         TEXT,
    -- ajans profili
    hourly_rate     TEXT,
    rate_low        INTEGER,
    team_size       TEXT,
    size_low        INTEGER,
    min_project     TEXT,
    founded_year    INTEGER,
    services_json   TEXT,
    last_review     TEXT,
    tagline         TEXT,
    -- iletişim
    contact_name    TEXT,
    contact_email   TEXT,
    email_is_generic INTEGER DEFAULT 0,
    phone           TEXT,
    linkedin        TEXT,
    impressum_url   TEXT,
    impressum_raw   TEXT,
    hiring_devs     INTEGER DEFAULT 0,
    -- elle düzenleme koruması (S8): elle dokunulan alan yeniden zenginleştirmede ezilmez
    contact_name_manual  INTEGER DEFAULT 0,
    contact_email_manual INTEGER DEFAULT 0,
    phone_manual         INTEGER DEFAULT 0,
    -- skor
    score           INTEGER DEFAULT 0,
    score_breakdown TEXT,
    verdict         TEXT,
    -- takip
    status          TEXT DEFAULT 'yeni',
    emailed         INTEGER DEFAULT 0,
    emailed_at      TEXT,
    followup_at     TEXT,
    -- iş akışı durumu
    detail_status   TEXT DEFAULT 'pending',
    impressum_status TEXT DEFAULT 'pending',
    impressum_method TEXT,
    created_at      TEXT,
    UNIQUE(name, location)
);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    body       TEXT NOT NULL,
    type       TEXT NOT NULL DEFAULT 'user',   -- 'user' | 'system'
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_lead ON notes(lead_id);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT
);
"""


# --- Varsayılan ayarlar (seed) ---------------------------------------------

DEFAULT_SETTINGS = {
    "target_countries": [
        "Almanya", "Avusturya", "İsviçre", "Hollanda", "Belçika",
        "İsveç", "Danimarka", "Norveç", "Finlandiya",
    ],
    "offshore_countries": [
        "Ukrayna", "Hindistan", "Pakistan", "Bangladeş", "Belarus",
        "Polonya", "Romanya", "Bulgaristan", "Sırbistan", "Vietnam",
        "Filipinler", "Mısır", "Arjantin", "Kolombiya",
    ],
    "weights": {
        "target_country": 25,
        "rate_high": 20,       # saatlik $100+
        "team_ideal": 15,      # 10-49 kişi
        "team_small": 7,       # 2-9
        "team_large": 5,       # 50-99
        "team_xlarge": 0,      # 100+
        "design_heavy": 15,    # dev %50 altı
        "hiring_devs": 10,
        "personal_email": 10,
        "generic_email": 5,
        "active_recent": 5,    # son yorum < 12 ay
        "competitor_penalty": 40,  # rakip sinyali işaretlenirse skordan düşülür
    },
    "thresholds": {"sicak": 75, "orta": 55, "zayif": 35},
    "negative_filter": {"min_rate": 50, "max_team": 250},
    # Rakip sinyali: ABD/nötr cephe + offshore ofisli geliştirme evlerini yakalar.
    # Üç koşul birden sağlanırsa 'is_competitor' işaretlenir ve skora ceza yazılır.
    "competitor": {"min_offshore_locations": 2, "min_reviews": 30, "min_dev_share": 50},
}

DEFAULT_CONNECTOR_SETTINGS = {
    "connectors": {
        "manual":    {"active": True,  "delay_min": 0,  "delay_max": 0,  "filter_url": "", "user_agent": ""},
        "impressum": {"active": True,  "delay_min": 3,  "delay_max": 5,  "filter_url": "", "user_agent": "Mozilla/5.0 (compatible; EndorLeadBot/1.0)"},
        "clutch":    {"active": False, "delay_min": 60, "delay_max": 90, "filter_url": "", "user_agent": "Mozilla/5.0 (compatible; EndorLeadBot/1.0)"},
    }
}


# v1.2 (Clutch) ile eklenen kolonlar — mevcut DB'lere migration ile eklenir
MIGRATIONS = [
    ("hq_country", "TEXT"),          # gerçek merkez ülke (primary'den farklı olabilir)
    ("locations_json", "TEXT"),      # tüm ofis ülkeleri (JSON liste)
    ("is_competitor", "INTEGER DEFAULT 0"),
    ("review_count", "INTEGER"),
    ("rating", "TEXT"),
    # AI Skoru + Yorumu alanları (v1.3)
    ("ai_score", "INTEGER"),
    ("ai_label", "TEXT"),
    ("ai_comment", "TEXT"),
    ("ai_angle", "TEXT"),
    ("ai_confidence", "REAL"),
    ("ai_flags", "TEXT"),
    ("ai_model", "TEXT"),
    ("ai_at", "TEXT"),
    ("ai_status", "TEXT DEFAULT 'pending'"),
    # AI Outreach E-posta Kiti alanları (v1.4)
    ("ai_email_subject", "TEXT"),
    ("ai_email_body", "TEXT"),
    ("ai_linkedin_note", "TEXT"),
    ("ai_followup_body", "TEXT"),
    ("ai_email_variant", "TEXT"),
    ("ai_email_at", "TEXT"),
    ("ai_email_status", "TEXT DEFAULT 'pending'"),
    ("gmail_draft_at", "TEXT"),
]


def init_db():
    """Şemayı oluştur, eksik kolonları ekle ve varsayılan ayarları yükle."""
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    migrate(conn)
    seed_settings(conn)
    conn.close()


def migrate(conn):
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
    for col, decl in MIGRATIONS:
        if col not in existing:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {decl}")
    conn.execute("UPDATE leads SET status='kazanıldı' WHERE status='aktarıldı'")
    conn.execute("UPDATE leads SET status='firma reddetti' WHERE status='red'")
    conn.commit()


def seed_settings(conn):
    existing = {r["key"] for r in conn.execute("SELECT key FROM settings")}
    seed = dict(DEFAULT_SETTINGS)
    seed.update(DEFAULT_CONNECTOR_SETTINGS)
    for key, value in seed.items():
        if key not in existing:
            conn.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES (?,?,?)",
                (key, json.dumps(value, ensure_ascii=False), now_iso()),
            )
    conn.commit()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def get_all_settings(conn):
    return {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM settings")}


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value, ensure_ascii=False), now_iso()),
    )
    conn.commit()


# --- Not yardımcıları -------------------------------------------------------

def add_note(conn, lead_id, body, note_type="user"):
    cur = conn.execute(
        "INSERT INTO notes(lead_id, body, type, created_at) VALUES (?,?,?,?)",
        (lead_id, body, note_type, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


if __name__ == "__main__":
    init_db()
    print(f"DB hazır: {DB_PATH}")
