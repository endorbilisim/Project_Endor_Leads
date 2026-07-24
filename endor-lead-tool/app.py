"""Endor Lead Tool — Flask app (127.0.0.1, tek kullanıcı, auth yok).

Çalıştırma: pip install -r requirements.txt && python app.py
"""
from __future__ import annotations
import csv
import io
import json

from flask import (Flask, render_template, request, jsonify, Response, abort, redirect)
from dotenv import load_dotenv

import db
import scoring
import worker
from connectors.manual import ManualConnector
from connectors.clutch import ClutchBlocked
from connectors import all_default_settings, get_connector, directory_sources

load_dotenv()
app = Flask(__name__)

# Editlenebilir alanlar ve elle-koruma bayrağı olanlar
EDITABLE = {
    "contact_name", "contact_email", "phone", "name", "website", "location",
    "country", "hourly_rate", "rate_low", "team_size", "size_low",
    "founded_year", "linkedin", "tagline", "min_project", "last_review",
    "hiring_devs", "email_is_generic",
}
MANUAL_FLAGGED = {
    "contact_name": "contact_name_manual",
    "contact_email": "contact_email_manual",
    "phone": "phone_manual",
}
INT_FIELDS = {"rate_low", "size_low", "founded_year", "hiring_devs", "email_is_generic"}

# Durum -> evre eşlemesi ve gizlenen durumlar
HIDDEN_STATUSES = {"elendi", "firma reddetti", "kazanıldı"}
STATUS_PHASES = {
    "yeni": 1, "araştırıldı": 1, "elendi": 1,
    "mail gitti": 2, "takip edildi": 2,
    "cevap var": 3, "firma reddetti": 3,
    "kazanıldı": 4,
}


def rescore(conn, lead_id):
    settings = db.get_all_settings(conn)
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        return
    lead = dict(row)
    score, breakdown, verdict = scoring.score_lead(lead, settings)
    conn.execute(
        "UPDATE leads SET score=?, score_breakdown=?, verdict=? WHERE id=?",
        (score, json.dumps(breakdown, ensure_ascii=False), verdict, lead_id),
    )
    conn.commit()


def enrich_impressum(lead_id):
    """Bir lead'i Impressum'dan zenginleştir. Ayrı thread'de çalışır.

    Dayanıklılık (S8): elle düzenlenen contact alanları ezilmez; notlar ve durum
    değişmez; her kayıt işlendiği anda DB'ye yazılır.
    """
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row or not row["website"]:
        conn.close()
        return
    lead = dict(row)
    settings = db.get_all_settings(conn)
    conf = (settings.get("connectors", {}) or {}).get("impressum", {})
    connector = get_connector("impressum", conf)

    try:
        result = connector.enrich(lead["website"])
    except Exception as e:
        conn.execute("UPDATE leads SET impressum_status='error', impressum_raw=? WHERE id=?",
                     (f"[Hata: {e}]", lead_id))
        conn.commit()
        conn.close()
        return

    sets, params = [], []
    # temel/impressum alanları her zaman güncellenir
    for f in ("impressum_status", "impressum_url", "impressum_raw",
              "impressum_method", "hiring_devs"):
        sets.append(f"{f}=?")
        params.append(result.get(f))
    # iletişim alanları: elle-koruma kuralına tabi (manuel bayrağı 1 ise dokunma)
    manual_map = {"contact_name": "contact_name_manual",
                  "contact_email": "contact_email_manual", "phone": "phone_manual"}
    for f, flag in manual_map.items():
        if lead.get(flag) and lead.get(f):
            continue  # kullanıcı elle değer doldurmuşsa ezme (boş ise doldurulabilir)
        if result.get(f):
            sets.append(f"{f}=?")
            params.append(result[f])
            if f == "contact_email":
                sets.append("email_is_generic=?")
                params.append(result.get("email_is_generic", 0))

    # Impressum çekildiğinde eğer durumu halen 'yeni' ise otomatik 'araştırıldı' yap
    if lead.get("status") == "yeni" and result.get("impressum_status") in ("done", "manual"):
        sets.append("status=?")
        params.append("araştırıldı")
        db.add_note(conn, lead_id, "Durum değişti: yeni → araştırıldı", "system")

    params.append(lead_id)
    conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()
    rescore(conn, lead_id)  # yeni sinyaller (mail, hiring_devs) skora yansısın
    # otomatik not
    db.add_note(conn, lead_id,
                f"Impressum çekildi: {result.get('impressum_status')} ({result.get('impressum_method') or '—'})",
                "system")

    # Ayarlarda auto_ai_score açık ise Impressum sonrası otomatik AI değerlendirme kuyruğuna al
    if settings.get("auto_ai_score") and lead.get("ai_status") != "done":
        worker.enqueue(evaluate_lead_ai_worker, lead_id, job_name="AI Değerlendirmesi")

    conn.close()


def evaluate_lead_ai_worker(lead_id):
    """Bir lead'in AI skor ve yorumunu hesaplar. Ayrı thread / worker'da çalışır."""
    import extractors.ai_score as ai_score_mod
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        conn.close()
        return
    lead = dict(row)
    result = ai_score_mod.evaluate_lead_ai(lead)

    conn.execute(
        "UPDATE leads SET ai_score=?, ai_label=?, ai_comment=?, ai_angle=?, "
        "ai_confidence=?, ai_flags=?, ai_model=?, ai_at=?, ai_status=? WHERE id=?",
        (
            result.get("ai_score"),
            result.get("ai_label"),
            result.get("ai_comment"),
            result.get("ai_angle"),
            result.get("ai_confidence"),
            result.get("ai_flags"),
            result.get("ai_model"),
            result.get("ai_at"),
            result.get("ai_status", "error"),
            lead_id,
        )
    )
    conn.commit()
    if result.get("ai_status") == "done":
        db.add_note(
            conn, lead_id,
            f"🤖 AI Değerlendirmesi: {result.get('ai_label')} (Skor: {result.get('ai_score', '—')})",
            "system"
        )
    conn.close()


def generate_lead_ai_email_worker(lead_id):
    """Bir lead için AI Outreach Kiti (E-posta, LinkedIn, Follow-up) üretir."""
    import extractors.ai_email as ai_email_mod
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        conn.close()
        return
    lead = dict(row)
    result = ai_email_mod.generate_lead_email(lead)

    conn.execute(
        "UPDATE leads SET ai_email_subject=?, ai_email_body=?, ai_linkedin_note=?, "
        "ai_followup_body=?, ai_email_variant=?, ai_email_at=?, ai_email_status=? WHERE id=?",
        (
            result.get("ai_email_subject"),
            result.get("ai_email_body"),
            result.get("ai_linkedin_note"),
            result.get("ai_followup_body"),
            result.get("ai_email_variant"),
            result.get("ai_email_at"),
            result.get("ai_email_status", "error"),
            lead_id,
        )
    )
    conn.commit()
    if result.get("ai_email_status") == "done":
        db.add_note(
            conn, lead_id,
            f"✉️ AI Outreach Kiti üretildi (Varyant {result.get('ai_email_variant')})",
            "system"
        )
    conn.close()




def resolve_clutch(detail, settings):
    """Detay verisinden merkez ülke, hedef-ofis kuralı, rakip sinyali ve notlar.

    Kural (kullanıcı kararı): firmanın hedef ülkelerimizden birinde ofisi varsa
    listeye dahil et (o ülkeyi merkez say), ama merkez başka yerdeyse not düş.
    Rakip sinyali: çok offshore ofis + çok yorum + yüksek dev ağırlığı.
    """
    norm = scoring.normalize_country
    targets = set(settings.get("target_countries", []))
    offshore = set(settings.get("offshore_countries", []))
    comp = settings.get("competitor", {})

    locs = [c for c in (norm(x) for x in (detail.get("locations") or [])) if c]
    hq = norm(detail.get("hq_country"))
    target_offices = [c for c in locs if c in targets]
    offshore_offices = [c for c in locs if c in offshore]
    primary = target_offices[0] if target_offices else hq

    dev_share = scoring._dev_share(detail.get("services_json")) or 0
    is_comp = (len(offshore_offices) >= comp.get("min_offshore_locations", 2)
               and (detail.get("review_count") or 0) >= comp.get("min_reviews", 30)
               and dev_share >= comp.get("min_dev_share", 50))

    notes = []
    if target_offices and hq and primary != hq:
        notes.append(f"Merkez {hq}, ama {primary}'da ofisi var. İlk temas yerel ofisle; "
                     f"anlaşma için merkezle ({hq}) ayrıca görüşmek gerekebilir.")
    if is_comp:
        notes.append(f"⚠ Rakip sinyali: {len(offshore_offices)} offshore ofis "
                     f"({', '.join(offshore_offices)}), {detail.get('review_count')} yorum, "
                     f"dev ağırlığı ~%{int(dev_share)} — muhtemelen geliştirme evi (rakip).")
    return primary, hq, is_comp, locs, notes


def _upsert_clutch_card(conn, card):
    """Listing kartını ekle (mükerrer değilse). source_url ile dedup."""
    if card.get("source_url"):
        dup = conn.execute("SELECT id FROM leads WHERE source_url=?", (card["source_url"],)).fetchone()
        if dup:
            return None
    cols = {k: v for k, v in card.items() if k in {
        "name", "website", "source", "source_url", "location", "country",
        "hourly_rate", "rate_low", "team_size", "size_low"}}
    cols["status"] = "yeni"
    cols["created_at"] = db.now_iso()
    keys = list(cols)
    try:
        cur = conn.execute(
            f"INSERT INTO leads({','.join(keys)}) VALUES ({','.join('?'*len(keys))})",
            [cols[k] for k in keys])
        rescore(conn, cur.lastrowid)
        return cur.lastrowid
    except Exception:
        return None


def crawl_clutch_listing(filter_url):
    """Sayfa sayfa listing crawl — son sayfaya kadar, güvenli durma."""
    conn = db.get_conn()
    settings = db.get_all_settings(conn)
    conf = (settings.get("connectors", {}) or {}).get("clutch", {})
    connector = get_connector("clutch", conf)
    try:
        first = connector.fetch(filter_url)
    except ClutchBlocked as e:
        print("Clutch listing engellendi:", e)
        conn.close()
        return
    last = connector.detect_last_page(first)
    for page in range(1, last + 1):
        try:
            html = first if page == 1 else connector.fetch(connector.page_url(filter_url, page))
        except ClutchBlocked as e:
            print("Clutch sayfa engellendi:", e)
            break
        cards = connector.parse_listing(html)
        if not cards:          # son sayfa / boş -> güvenle dur (hata alma)
            break
        for card in cards:
            _upsert_clutch_card(conn, card)  # her kart işlenince yazılır
    conn.close()


def enrich_clutch_detail(lead_id, html=None):
    """Bir Clutch lead'inin profil detayını çek/işle. Dayanıklılık kurallarına tabi."""
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        conn.close()
        return
    lead = dict(row)
    settings = db.get_all_settings(conn)
    conf = (settings.get("connectors", {}) or {}).get("clutch", {})
    connector = get_connector("clutch", conf)

    if html is None:
        if not lead.get("source_url"):
            conn.close()
            return
        try:
            html = connector.fetch(lead["source_url"])
        except ClutchBlocked as e:
            conn.execute("UPDATE leads SET detail_status='blocked' WHERE id=?", (lead_id,))
            conn.commit()
            db.add_note(conn, lead_id, "⛔ Cloudflare engeli — profili tarayıcıda açıp "
                        "kaydet (Ctrl+S, HTML Only) ve panelden yükle.", "system")
            conn.close()
            return

    detail = connector.parse_detail(html)
    primary, hq, is_comp, locs, notes = resolve_clutch(detail, settings)

    # temel bilgiler güncellenir; iletişim alanları manuel-korumaya tabi (S8)
    updates = {
        "tagline": detail.get("tagline"), "hourly_rate": detail.get("hourly_rate"),
        "rate_low": detail.get("rate_low"), "team_size": detail.get("team_size"),
        "size_low": detail.get("size_low"), "founded_year": detail.get("founded_year"),
        "services_json": detail.get("services_json"), "last_review": detail.get("last_review"),
        "review_count": detail.get("review_count"), "rating": detail.get("rating"),
        "location": detail.get("location"), "country": primary, "hq_country": hq,
        "locations_json": json.dumps(locs, ensure_ascii=False),
        "is_competitor": 1 if is_comp else 0, "detail_status": "done",
    }
    if detail.get("website") and not lead.get("website"):
        updates["website"] = detail["website"]
    if not lead.get("name"):
        updates["name"] = detail.get("name")
    if detail.get("phone") and not lead.get("phone_manual"):
        updates["phone"] = detail["phone"]

    updates = {k: v for k, v in updates.items() if v is not None}
    if updates:
        sets = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE leads SET {sets} WHERE id=?", list(updates.values()) + [lead_id])
        conn.commit()
    rescore(conn, lead_id)
    for n in notes:
        db.add_note(conn, lead_id, n, "system")
    db.add_note(conn, lead_id, "Clutch detay çekildi.", "system")
    conn.close()


# --- Sayfalar ---------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/settings")
def settings_page():
    return render_template("settings.html")


# --- Lead listesi & sayaçlar -----------------------------------------------

def _parse_multi_arg(key):
    vals = request.args.getlist(key)
    res = set()
    for v in vals:
        for item in v.split(","):
            item = item.strip()
            if item:
                res.add(item)
    return res


@app.route("/api/leads")
def api_leads():
    conn = db.get_conn()
    show_hidden = request.args.get("show_hidden") == "1"
    verdict_set = _parse_multi_arg("verdict")
    country_set = _parse_multi_arg("country")
    impressum_set = _parse_multi_arg("impressum")
    detail_set = _parse_multi_arg("detail")
    status_set = _parse_multi_arg("status")
    ai_status_set = _parse_multi_arg("ai_status")
    data_filter_set = _parse_multi_arg("data_filter")
    not_emailed = request.args.get("not_emailed") == "1"
    q = (request.args.get("q") or "").strip().lower()

    rows = [dict(r) for r in conn.execute("SELECT * FROM leads ORDER BY score DESC, id DESC")]
    conn.close()

    out = []
    for r in rows:
        if status_set:
            if r.get("status") not in status_set:
                continue
        elif not show_hidden and r["status"] in HIDDEN_STATUSES:
            continue

        if verdict_set and r.get("verdict") not in verdict_set:
            continue
        if country_set and (r.get("country") or "") not in country_set:
            continue
        if impressum_set and (r.get("impressum_status") or "pending") not in impressum_set:
            continue
        if detail_set and (r.get("detail_status") or "pending") not in detail_set:
            continue
        if ai_status_set and (r.get("ai_status") or "pending") not in ai_status_set:
            continue
        if data_filter_set:
            if "has_email" in data_filter_set and not (r.get("contact_email") and r["contact_email"].strip()):
                continue
            if "has_contact" in data_filter_set and not (r.get("contact_name") and r["contact_name"].strip()):
                continue
            if "has_phone" in data_filter_set and not (r.get("phone") and r["phone"].strip()):
                continue
            if "not_emailed" in data_filter_set and r.get("emailed"):
                continue
            if "personal_email" in data_filter_set and not (r.get("contact_email") and not r.get("email_is_generic")):
                continue
        if not_emailed and r["emailed"]:
            continue
        if q and q not in (r.get("name") or "").lower() and q not in (r.get("website") or "").lower():
            continue
        r["score_breakdown"] = json.loads(r["score_breakdown"]) if r.get("score_breakdown") else {}
        out.append(r)
    return jsonify(out)


@app.route("/api/counters")
def api_counters():
    conn = db.get_conn()
    total = conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
    sicak = conn.execute("SELECT COUNT(*) c FROM leads WHERE verdict='SICAK'").fetchone()["c"]
    emailed = conn.execute("SELECT COUNT(*) c FROM leads WHERE emailed=1").fetchone()["c"]
    replied = conn.execute("SELECT COUNT(*) c FROM leads WHERE status='cevap var'").fetchone()["c"]
    conn.close()
    return jsonify({"total": total, "sicak": sicak, "emailed": emailed, "replied": replied})


# --- Domain ekleme (tek / toplu) -------------------------------------------

@app.route("/api/leads/add", methods=["POST"])
def api_add():
    payload = request.get_json(force=True)
    raw = payload.get("domains", "")
    lines = [l.strip() for l in raw.replace(",", "\n").splitlines() if l.strip()]
    conn = db.get_conn()
    added, skipped = 0, 0
    for line in lines:
        info = ManualConnector.normalize_domain(line)
        if not info:
            skipped += 1
            continue
        # Manuel girişte location NULL olur; UNIQUE(name,location) NULL'ları
        # ayrı sayar, bu yüzden website üzerinden mükerrer kontrolü yapılır.
        dup = conn.execute(
            "SELECT 1 FROM leads WHERE website=? OR name=?", (info["website"], info["name"])
        ).fetchone()
        if dup:
            skipped += 1
            continue
        try:
            cur = conn.execute(
                "INSERT INTO leads(name, website, source, status, created_at) VALUES (?,?,?,?,?)",
                (info["name"], info["website"], "manual", "yeni", db.now_iso()),
            )
            rescore(conn, cur.lastrowid)
            added += 1
        except Exception:
            skipped += 1
    conn.commit()
    conn.close()
    return jsonify({"added": added, "skipped": skipped})


# --- Satır içi düzenleme ----------------------------------------------------

@app.route("/api/lead/<int:lead_id>", methods=["PATCH"])
def api_edit(lead_id):
    payload = request.get_json(force=True)
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        conn.close()
        abort(404)

    sets, params = [], []
    for field, value in payload.items():
        if field not in EDITABLE:
            continue
        if field in INT_FIELDS:
            value = scoring._to_int(value)
        sets.append(f"{field}=?")
        params.append(value)
        # elle düzenleme koruması: manuel bayrağı işaretle (S8)
        if field in MANUAL_FLAGGED:
            sets.append(f"{MANUAL_FLAGGED[field]}=1")

    if sets:
        params.append(lead_id)
        conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
        rescore(conn, lead_id)
    updated = dict(conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone())
    updated["score_breakdown"] = json.loads(updated["score_breakdown"]) if updated.get("score_breakdown") else {}
    conn.close()
    return jsonify(updated)


# --- Durum değişimi (otomatik alanlar + sistem notu) -----------------------

@app.route("/api/lead/<int:lead_id>/status", methods=["POST"])
def api_status(lead_id):
    payload = request.get_json(force=True)
    new_status = payload.get("status")
    if new_status not in STATUS_PHASES:
        abort(400)
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    old = row["status"]

    sets = ["status=?"]
    params = [new_status]
    if new_status == "mail gitti":
        sets += ["emailed=1"]
        if not row["emailed_at"]:
            sets += ["emailed_at=?"]
            params.append(db.now_iso())
    if new_status == "takip edildi" and not row["followup_at"]:
        sets += ["followup_at=?"]
        params.append(db.now_iso())
    params.append(lead_id)
    conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()

    if old != new_status:
        db.add_note(conn, lead_id, f"Durum değişti: {old} → {new_status}", note_type="system")
    conn.close()
    return jsonify({"ok": True, "status": new_status})


@app.route("/api/lead/<int:lead_id>/emailed", methods=["POST"])
def api_emailed(lead_id):
    """'Mail atıldı' checkbox — durumla çift yönlü bağlı."""
    payload = request.get_json(force=True)
    checked = bool(payload.get("checked"))
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    if checked:
        emailed_at = row["emailed_at"] or db.now_iso()
        conn.execute("UPDATE leads SET emailed=1, emailed_at=?, status=? WHERE id=?",
                     (emailed_at, "mail gitti", lead_id))
        if row["status"] != "mail gitti":
            db.add_note(conn, lead_id, f"Durum değişti: {row['status']} → mail gitti", "system")
    else:
        conn.execute("UPDATE leads SET emailed=0 WHERE id=?", (lead_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# --- Notlar -----------------------------------------------------------------

@app.route("/api/lead/<int:lead_id>/notes", methods=["GET"])
def api_notes_list(lead_id):
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM notes WHERE lead_id=? ORDER BY created_at DESC, id DESC", (lead_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/lead/<int:lead_id>/notes", methods=["POST"])
def api_notes_add(lead_id):
    payload = request.get_json(force=True)
    body = (payload.get("body") or "").strip()
    if not body:
        abort(400)
    conn = db.get_conn()
    note_id = db.add_note(conn, lead_id, body, "user")
    row = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    conn.close()
    return jsonify(dict(row))


@app.route("/api/note/<int:note_id>", methods=["DELETE"])
def api_notes_delete(note_id):
    conn = db.get_conn()
    conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# --- CRM'e aktar ------------------------------------------------------------

@app.route("/api/lead/<int:lead_id>/transfer", methods=["POST"])
def api_transfer(lead_id):
    conn = db.get_conn()
    row = conn.execute("SELECT status FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute("UPDATE leads SET status='kazanıldı' WHERE id=?", (lead_id,))
    conn.commit()
    db.add_note(conn, lead_id, f"Durum değişti: {row['status']} → kazanıldı", "system")
    conn.close()
    return jsonify({"ok": True})


# --- Impressum çekme (S5, worker kuyruğu) -----------------------------------

@app.route("/api/lead/<int:lead_id>/impressum", methods=["POST"])
def api_impressum_one(lead_id):
    worker.enqueue(enrich_impressum, lead_id, job_name="Impressum Çekme")
    return jsonify({"ok": True, "queued": 1})


@app.route("/api/impressum/run", methods=["POST"])
def api_impressum_run():
    """Toplu: seçili değilse impressum'u 'pending' olan tüm lead'leri kuyruğa al."""
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids")
    include_eliminated = bool(payload.get("include_eliminated", False))
    conn = db.get_conn()
    if ids:
        rows = [r["id"] for r in conn.execute(
            f"SELECT id FROM leads WHERE id IN ({','.join('?'*len(ids))}) ORDER BY score DESC", ids)]
    else:
        where_clause = "website IS NOT NULL AND impressum_status='pending'"
        if not include_eliminated:
            where_clause += " AND status NOT IN ('elendi', 'firma reddetti') AND (verdict IS NULL OR verdict != 'ELE')"
        rows = [r["id"] for r in conn.execute(
            f"SELECT id FROM leads WHERE {where_clause} ORDER BY score DESC")]
    conn.close()
    for lead_id in rows:
        worker.enqueue(enrich_impressum, lead_id, job_name="Impressum Çekme")
    return jsonify({"ok": True, "queued": len(rows)})


@app.route("/api/worker")
def api_worker():
    return jsonify(worker.status())


@app.route("/api/worker/stop", methods=["POST"])
def api_worker_stop():
    worker.cancel()
    return jsonify({"ok": True})


def _prepare_lead_dict(row):
    if not row:
        return None
    r = dict(row)
    if r.get("score_breakdown"):
        try:
            r["score_breakdown"] = json.loads(r["score_breakdown"])
        except Exception:
            r["score_breakdown"] = {}
    else:
        r["score_breakdown"] = {}
    return r


# --- AI Skoru & Yorumu (v1.3) ------------------------------------------------

@app.route("/api/lead/<int:lead_id>/ai-evaluate", methods=["POST"])
def api_ai_evaluate_one(lead_id):
    """Tekil lead için anlık AI değerlendirmesi."""
    evaluate_lead_ai_worker(lead_id)
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    return jsonify({"ok": True, "lead": _prepare_lead_dict(row)})


@app.route("/api/lead/<int:lead_id>/ai-email", methods=["POST"])
def api_ai_email_one(lead_id):
    """Tekil lead için anlık AI Outreach Kiti üretimi."""
    generate_lead_ai_email_worker(lead_id)
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    return jsonify({"ok": True, "lead": _prepare_lead_dict(row)})


# --- Gmail OAuth2 Taslak API (v1.4) ------------------------------------------

@app.route("/api/gmail/status")
def api_gmail_status():
    import connectors.gmail_oauth as gm
    return jsonify({
        "configured": gm.is_configured(),
        "authenticated": gm.is_authenticated()
    })


@app.route("/api/gmail/auth")
def api_gmail_auth():
    import connectors.gmail_oauth as gm
    if not gm.is_configured():
        return "client_secret.json dosyası proje dizininde bulunamadı. Lütfen önce Google Cloud Console'dan istemci dosyasını yükleyin.", 400
    auth_url = gm.get_auth_url()
    return redirect(auth_url)


@app.route("/api/gmail/callback")
def api_gmail_callback():
    import connectors.gmail_oauth as gm
    code = request.args.get("code")
    error = request.args.get("error")
    if error or not code:
        return f"Gmail OAuth2 Doğrulama Hatası: {error or 'Kod alınamadı'}", 400

    try:
        gm.handle_auth_code(code)
        return """
        <div style="font-family:-apple-system,sans-serif;padding:40px;text-align:center">
          <h1 style="color:#2fbf71">✓ Gmail Bağlantısı Başarılı!</h1>
          <p><b>burak@endor.agency</b> GSuite hesabınız başarıyla bağlandı.</p>
          <p style="color:#888">Artık sekme açılmadan tek tıkla doğrudan GSuite Taslaklar klasörünüze mail atabilirsiniz.</p>
          <p><a href="/" style="color:#6366f1;text-decoration:none">← Ana Panele Dön</a></p>
        </div>
        """
    except Exception as e:
        return f"OAuth2 Token Hatası: {e}", 500


@app.route("/api/lead/<int:lead_id>/gmail-draft", methods=["POST"])
def api_gmail_draft_one(lead_id):
    """Lead için üretilen AI mailini doğrudan burak@endor.agency GSuite Taslaklarına kaydeder."""
    import connectors.gmail_oauth as gm
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    lead = dict(row)

    if not lead.get("contact_email") or not lead["contact_email"].strip():
        conn.close()
        return jsonify({"ok": False, "error": "Ajansın e-posta adresi bulunmamaktadır."}), 400

    if not lead.get("ai_email_subject") or not lead.get("ai_email_body"):
        generate_lead_ai_email_worker(lead_id)
        row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        lead = dict(row)

    try:
        draft_res = gm.create_gmail_draft(
            to_email=lead["contact_email"].strip(),
            subject=lead.get("ai_email_subject", ""),
            body=lead.get("ai_email_body", ""),
            cc="abdullah@endor.agency"
        )
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute("UPDATE leads SET gmail_draft_at=? WHERE id=?", (now_iso, lead_id))
        conn.commit()
        db.add_note(conn, lead_id, f"📥 GSuite (burak@endor.agency) Taslaklar klasörüne aktarıldı (Draft ID: {draft_res.get('id')})", "system")
        conn.close()
        return jsonify({"ok": True, "draft_id": draft_res.get("id"), "gmail_draft_at": now_iso})
    except Exception as e:
        conn.close()
        return jsonify({"ok": False, "error": str(e)}), 500



@app.route("/api/leads/ai-evaluate-batch", methods=["POST"])
def api_ai_evaluate_batch():
    """Toplu AI değerlendirmesi (Batch).
    
    `ids` dizisi verilirse o kayıtlar; verilmezse varsayılan olarak `verdict IN ('SICAK', 'ORTA')`
    ve henüz AI değerlendirmesi yapılmamış kayıtlar kuyruğa alınır.
    """
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids")
    include_all = bool(payload.get("include_all", False))

    conn = db.get_conn()
    if ids:
        rows = [r["id"] for r in conn.execute(
            f"SELECT id FROM leads WHERE id IN ({','.join('?'*len(ids))}) ORDER BY score DESC", ids)]
    else:
        where_clause = "status NOT IN ('elendi', 'firma reddetti')"
        if not include_all:
            where_clause += " AND verdict IN ('SICAK', 'ORTA') AND (ai_status IS NULL OR ai_status != 'done')"
        rows = [r["id"] for r in conn.execute(
            f"SELECT id FROM leads WHERE {where_clause} ORDER BY score DESC")]
    conn.close()

    for lead_id in rows:
        worker.enqueue(evaluate_lead_ai_worker, lead_id, job_name="AI Değerlendirmesi")
    return jsonify({"ok": True, "queued": len(rows)})



@app.route("/api/sources")
def api_sources():
    """'Kaynaklar' menüsü — dizin crawl kaynakları (Clutch, ileride Sortlist…)."""
    return jsonify(directory_sources())


# --- Clutch (S7, v1.2) ------------------------------------------------------

@app.route("/api/clutch/listing", methods=["POST"])
def api_clutch_listing():
    payload = request.get_json(force=True)
    url = (payload.get("url") or "").strip()
    if not url:
        abort(400)
    worker.enqueue(crawl_clutch_listing, url, job_name="Clutch Listing Taraması")
    return jsonify({"ok": True})


@app.route("/api/clutch/details", methods=["POST"])
def api_clutch_details():
    """detail_status IN ('pending','blocked') olan tüm Clutch lead'lerini tek tek işle."""
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids")
    include_eliminated = bool(payload.get("include_eliminated", False))
    conn = db.get_conn()
    if ids:
        rows = [r["id"] for r in conn.execute(
            f"SELECT id FROM leads WHERE id IN ({','.join('?'*len(ids))}) ORDER BY score DESC", ids)]
    else:
        where_clause = "source='clutch' AND source_url IS NOT NULL AND detail_status IN ('pending','blocked')"
        if not include_eliminated:
            where_clause += " AND status NOT IN ('elendi', 'firma reddetti') AND (verdict IS NULL OR verdict != 'ELE')"
        rows = [r["id"] for r in conn.execute(
            f"SELECT id FROM leads WHERE {where_clause} ORDER BY score DESC")]
    conn.close()
    for lead_id in rows:
        worker.enqueue(enrich_clutch_detail, lead_id, job_name="Clutch Detay Çekme")
    return jsonify({"ok": True, "queued": len(rows)})


@app.route("/api/leads/batch-status", methods=["POST"])
def api_batch_status():
    """Seçili id'lerin durumunu toplu olarak güncelle."""
    payload = request.get_json(force=True)
    ids = payload.get("ids", [])
    new_status = payload.get("status")
    if not ids or not new_status:
        return jsonify({"ok": False, "error": "id ve status gerekli"}), 400
    conn = db.get_conn()
    placeholders = ",".join("?" for _ in ids)
    params = [new_status]
    sets = ["status=?"]
    if new_status == "mail gitti":
        sets.append("emailed=1")
    params.extend(ids)
    conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id IN ({placeholders})", params)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "count": len(ids)})


@app.route("/api/lead/<int:lead_id>/clutch-detail", methods=["POST"])
def api_clutch_detail_one(lead_id):
    worker.enqueue(enrich_clutch_detail, lead_id, job_name="Clutch Detay Çekme")
    return jsonify({"ok": True, "queued": 1})


@app.route("/api/clutch/upload", methods=["POST"])
def api_clutch_upload():
    """Kaydedilmiş HTML yükleme (Cloudflare fallback). type=listing|detail."""
    f = request.files.get("file")
    kind = request.form.get("type", "listing")
    lead_id = request.form.get("lead_id")
    if not f:
        abort(400)
    html = f.read().decode("utf-8", errors="ignore")
    connector = get_connector("clutch")
    conn = db.get_conn()
    if kind == "listing":
        cards = connector.parse_listing(html)
        added = sum(1 for c in cards if _upsert_clutch_card(conn, c))
        conn.close()
        return jsonify({"ok": True, "added": added, "found": len(cards)})
    # detail
    detail = connector.parse_detail(html)
    if lead_id:
        target_lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        conn.close()
        if target_lead:
            target_url = (target_lead["source_url"] or "").rstrip("/").lower()
            html_url = (detail.get("source_url") or "").rstrip("/").lower()
            target_name = (target_lead["name"] or "").strip().lower()
            html_name = (detail.get("name") or "").strip().lower()

            # Firma uyuşmazlığı kontrolü (Yanlış HTML yüklemesini engelle)
            if html_url and target_url and html_url != target_url and target_name not in html_name and html_name not in target_name:
                return jsonify({
                    "ok": False,
                    "error": f"Firma Uyuşmazlığı! Yüklediğiniz HTML '{detail.get('name') or html_url}' firmasına ait, ancak siz '{target_lead['name']}' panelindesiniz!"
                }), 400

        enrich_clutch_detail(int(lead_id), html=html)
        return jsonify({"ok": True, "lead_id": int(lead_id)})
    # lead_id yoksa: profilden yeni kayıt oluştur
    detail = connector.parse_detail(html)
    conn = db.get_conn()
    card = {"name": detail.get("name"), "source": "clutch",
            "source_url": detail.get("source_url"), "website": detail.get("website")}
    new_id = _upsert_clutch_card(conn, card)
    conn.close()
    if new_id:
        enrich_clutch_detail(new_id, html=html)
    return jsonify({"ok": True, "lead_id": new_id})


# --- CSV import / export ----------------------------------------------------

CSV_COLUMNS = [
    "name", "website", "location", "country", "hourly_rate", "rate_low",
    "team_size", "size_low", "founded_year", "contact_name", "contact_email",
    "phone", "linkedin", "tagline", "score", "verdict", "status",
]


@app.route("/api/export.csv")
def api_export():
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM leads ORDER BY score DESC").fetchall()
    conn.close()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: r[c] for c in CSV_COLUMNS})
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=leads.csv"})


@app.route("/api/import.csv", methods=["POST"])
def api_import():
    f = request.files.get("file")
    if not f:
        abort(400)
    text = f.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    conn = db.get_conn()
    added, skipped = 0, 0
    for row in reader:
        name = (row.get("name") or "").strip()
        if not name:
            skipped += 1
            continue
        cols = {c: (row.get(c) or None) for c in CSV_COLUMNS if c in row}
        cols["name"] = name
        cols.setdefault("source", "manual")
        cols["created_at"] = db.now_iso()
        keys = list(cols.keys())
        placeholders = ",".join("?" for _ in keys)
        try:
            cur = conn.execute(
                f"INSERT INTO leads({','.join(keys)}) VALUES ({placeholders})",
                [cols[k] for k in keys],
            )
            rescore(conn, cur.lastrowid)
            added += 1
        except Exception:
            skipped += 1
    conn.commit()
    conn.close()
    return jsonify({"added": added, "skipped": skipped})


# --- Ayarlar (S3) -----------------------------------------------------------

@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    conn = db.get_conn()
    data = db.get_all_settings(conn)
    conn.close()
    data["_connector_defaults"] = all_default_settings()
    return jsonify(data)


@app.route("/api/settings", methods=["POST"])
def api_settings_save():
    payload = request.get_json(force=True)
    conn = db.get_conn()
    for key, value in payload.items():
        if key.startswith("_"):
            continue
        db.set_setting(conn, key, value)
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/rescore-all", methods=["POST"])
def api_rescore_all():
    conn = db.get_conn()
    ids = [r["id"] for r in conn.execute("SELECT id FROM leads")]
    for lead_id in ids:
        rescore(conn, lead_id)
    conn.close()
    return jsonify({"ok": True, "count": len(ids)})


if __name__ == "__main__":
    db.init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
