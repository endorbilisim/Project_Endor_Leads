# Endor Lead Tool — v1 Spesifikasyonu

## Neden bu araç var (bağlam)

Endor, İstanbul merkezli bir yazılım/dijital ajans. Hedef: **DACH ve Nordik bölgesindeki ajanslara white-label geliştirme hizmeti satmak** — o ajanslar müşteriyi bulup yönetir, Endor arkada geliştirmeyi yapar. Gelir euro, maliyet lira.

Bu yüzden hedef müşteri **son müşteri değil, ajansın kendisi.** Ve bu, skorlama mantığının temelini oluşturur:
- Yerel merkezli, saatlik ücreti yüksek ajans = **müşteri adayı** (dışarı verirse kâr eder)
- Offshore merkezli, saatlik ücreti düşük geliştirme şirketi = **rakip**, müşteri değil
- Tasarım/strateji ağırlıklı ajans = geliştirmeyi dışarı verme ihtimali yüksek = daha iyi hedef

## Bu ne değil

**Bu bir CRM değil.** Endor'un kendi CRM altyapısı zaten var. Bu araç huninin sadece en baş kısmı: ajans bul → ele → iletişim bilgisini çıkar → mail at → işaretle. Bir lead cevap verdiği anda aracın işi biter, kayıt Endor CRM'ine aktarılır ve burada `aktarıldı` olarak işaretlenir.

Bu yüzden **yok:** pipeline, deal aşamaları, teklif takibi, gelir raporu, kullanıcı yönetimi, şifre, dashboard.

## Temel kısıtlar

- Tek kullanıcı, tamamen lokal (`127.0.0.1`), kimlik doğrulama yok
- SQLite tek dosya (`leads.db`), harici servis yok
- Mail gönderimi YOK — kullanıcı Gmail'den elle atar, araçta sadece işaretler
- Skorlama kural tabanlı (AI kullanmaz). LLM sadece iletişim bilgisi çıkarımında, fallback olarak kullanılır.

---

## Teknoloji

- Python 3.11+, Flask (port 5000), SQLite, BeautifulSoup4 + lxml, requests
- Arka plan işleri için `threading` (Celery/Redis gereksiz)
- Kurulum: `pip install -r requirements.txt && python app.py`

### Klasör yapısı — modülerlik şart

```
endor-lead-tool/
├── app.py                 # Flask app, route'lar
├── db.py                  # SQLite şema + yardımcılar
├── scoring.py             # skorlama mantığı (tek yerde)
├── worker.py              # arka plan iş kuyruğu
├── connectors/
│   ├── __init__.py        # connector kayıt mekanizması
│   ├── base.py            # ortak arayüz (her connector bunu uygular)
│   ├── manual.py          # elle domain girişi
│   ├── clutch.py          # Clutch listing + detay parser
│   └── impressum.py       # ajans sitesinden iletişim bilgisi çıkarma
├── extractors/
│   ├── heuristic.py       # regex + kalıp tabanlı çıkarım (ücretsiz, önce bu)
│   └── llm.py             # LLM fallback (provider-agnostik)
├── templates/
├── static/
└── leads.db
```

**Kural:** Her yeni kaynak (Sortlist, DesignRush, agenturmatching, BVDW…) `connectors/` altına yeni bir dosya olarak eklenir. Mevcut hiçbir dosya değişmez. Böylece kaynaklar tek tek, yavaşça, test edilerek devreye alınır.

`base.py` içindeki ortak arayüz:

```python
class Connector:
    name: str
    def parse_listing(self, html: str) -> list[dict]: ...
    def parse_detail(self, html: str) -> dict: ...
    def fetch(self, url: str) -> str: ...   # rate limit connector içinde
```

---

## Modüller ve sıra

Her aşama ayrı ayrı, test edilerek devreye alınacak. Sıradaki aşamaya bir öncekinin çıktısı doğrulanmadan geçilmeyecek.

### v1.0 — İskelet + Manuel giriş (ÖNCE BU)
- SQLite şeması, Flask arayüzü, tablo, satır içi düzenleme
- Elle domain ekleme: tek tek veya çok satırlı toplu yapıştırma
- CSV import/export
- **Bu aşama tek başına kullanılabilir olmalı.** Clutch olmadan da elle domain girip çalışabilmeliyim.

### v1.1 — Impressum connector + hibrit çıkarım

**Girdi:** ajansın kendi web sitesi (Clutch değil — orada Cloudflare yok, 3–5 sn bekleme yeterli)

**Adım 1 — Doğru sayfayı bul.** Ana sayfayı çek, footer linklerini tara, şu kalıpları ara (öncelik sırasıyla):
- DACH: `impressum`, `imprint`, `kontakt`
- Hollanda: `contact`, `over-ons`
- Nordik: `contact`, `about`, `om-oss`, `om-os`, `kontakt`
- Fallback: `/contact`, `/about`, `/team`, `/legal`

**Adım 2 — Sayfayı metne indirge.** Ham HTML'i asla doğrudan kullanma. `script`, `style`, `nav`, `svg` etiketlerini at, `get_text("\n", strip=True)` ile düz metne çevir. Bu hem regex'i hem LLM'i kolaylaştırır ve token maliyetini 5-10 kat düşürür.

**Adım 3 — Önce heuristic (`extractors/heuristic.py`, ücretsiz).**
- **İsim:** şu etiketlerin sağındaki metni al: `Geschäftsführer`, `Vertreten durch`, `Inhaber`, `Verantwortlich`, `Managing Director`, `Owner`, `Eier`, `Daglig leder`
- **E-posta:** `mailto:` linkleri + metin içi regex. `info@`/`office@`/`hello@` genel adres olarak işaretlensin.
- **Telefon:** uluslararası formatlı regex

**Adım 4 — Heuristic tutmazsa LLM (`extractors/llm.py`).**
Tetiklenme koşulu: isim bulunamadı VEYA sadece genel e-posta bulundu.

- Metni ~6000 karaktere kırp (e-posta/isim geçen bölümün etrafını önceliklendir)
- Sadece JSON döndürmesini iste, başka hiçbir şey yazmasın:
  ```json
  {"name": null, "role": null, "email": null, "phone": null, "confidence": 0.0}
  ```
- Bulamadığı alanı `null` bıraksın; **uydurmasın** — prompt'ta bu açıkça belirtilsin
- Ucuz model yeterli (gpt-4o-mini sınıfı). Bu iş sınıflandırma değil, düz bilgi çıkarımı.

**Adım 5 — Doğrulama (zorunlu).**
LLM çıktısı asla doğrudan DB'ye yazılmaz:
- E-posta regex'ten geçmeli, aksi halde `null`
- E-postanın domain'i ajansın domain'iyle uyuşmalı (uyuşmuyorsa işaretle, ama sil me)
- İsim 2–60 karakter arası olmalı, içinde `@` veya `http` geçmemeli
- `confidence < 0.6` ise `impressum_status = 'manual'` yapılsın, kullanıcı elle baksın

> **Not:** Kazınan sayfalar güvenilmez girdidir. Sayfa metni LLM'e *veri* olarak gider, talimat olarak değil — prompt'ta bu ayrım net kurulsun ve çıktı yukarıdaki gibi mutlaka doğrulansın.

**Adım 6 — Bonus sinyal.** `/karriere`, `/jobs`, `/career` sayfasında `entwickler`, `developer`, `frontend`, `backend` geçiyor mu? Geçiyorsa kapasitesi yetmiyor demektir → skor +10 (bu tamamen keyword araması, LLM'e gerek yok)

**Sonuç:** `impressum_status` = `done` | `manual` | `error`. Off-canvas panelde ham metin + hangi yöntemin (heuristic/LLM) kullanıldığı görünsün ki hatalı çıkarımı fark edip elle düzeltebilesin.

### API key yönetimi

- Key **koda gömülmez**, `.env` dosyasından okunur (`python-dotenv`)
- `.env` mutlaka `.gitignore`'da olsun
- `extractors/llm.py` provider-agnostik yazılsın: OpenAI ile başla, ama base URL ve model adı config'ten gelsin. Böylece ileride başka bir sağlayıcıya geçmek tek satırlık değişiklik olur.
- Key yoksa uygulama çökmesin — LLM adımını atlayıp `manual` işaretlesin
- **Maliyet takibi:** her çağrının token sayısı loglansın. 500 ajans için toplam maliyet birkaç dolar seviyesinde kalmalı; bunun çok üstüne çıkıyorsa metin kırpma çalışmıyordur.

### v1.2 — Clutch connector
- Listing crawl: sayfa sayfa, **sonuncu sayfaya kadar, limit yok**
- **Sayfalar arası bekleme: 60–90 saniye rastgele.** Pazarlık konusu değil.
- Detay crawl: ayrı aşama, Clutch profil sayfalarına tek tek girer, aynı bekleme
- Detaydan çıkarılacak: tam adres, **servis dağılımı yüzdeleri**, kuruluş yılı, son yorum tarihi, portföy başlıkları
- **Kaydedilmiş HTML yükleme modu ZORUNLU fallback:** Cloudflare 403 verirse kullanıcı sayfayı tarayıcıda açar, `Ctrl+S` → "HTML Only" kaydeder, dosyayı yükler. Aynı parser çalışır, ban riski sıfır.

### v1.3+ — Diğer kaynaklar (tek tek, aceleye getirmeden)
Sortlist → DesignRush → GoodFirms → agenturmatching.de → BVDW/dmvö üye listeleri

---

## Veri modeli

```sql
CREATE TABLE leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- kimlik
    name            TEXT NOT NULL,
    website         TEXT,
    source          TEXT,          -- 'manual' | 'clutch' | 'sortlist' ...
    source_url      TEXT,          -- Clutch profil linki vb.
    -- konum
    location        TEXT,
    country         TEXT,          -- merkez ülke (hizmet verdiği ülke DEĞİL)
    -- ajans profili
    hourly_rate     TEXT,
    rate_low        INTEGER,
    team_size       TEXT,
    size_low        INTEGER,
    min_project     TEXT,
    founded_year    INTEGER,
    services_json   TEXT,          -- servis yüzdeleri
    last_review     TEXT,
    tagline         TEXT,
    -- iletişim
    contact_name    TEXT,
    contact_email   TEXT,
    email_is_generic INTEGER DEFAULT 0,   -- info@ ise 1
    phone           TEXT,
    linkedin        TEXT,
    impressum_url   TEXT,
    impressum_raw   TEXT,
    hiring_devs     INTEGER DEFAULT 0,
    -- skor
    score           INTEGER DEFAULT 0,
    score_breakdown TEXT,          -- JSON: hangi sinyalden kaç puan
    verdict         TEXT,          -- SICAK | ORTA | ZAYIF | ELE
    -- takip
    status          TEXT DEFAULT 'yeni',
    emailed         INTEGER DEFAULT 0,
    emailed_at      TEXT,
    followup_at     TEXT,
    -- iş akışı durumu
    detail_status   TEXT DEFAULT 'pending',
    impressum_status TEXT DEFAULT 'pending',
    created_at      TEXT,
    UNIQUE(name, location)
);
```

### Notlar — ayrı tablo

Tek bir not alanı yerine, her leade **birden çok not** eklenebilir. Notlar zaman damgalı bir akış (timeline) olarak görünür, sayfa yenilenmeden eklenir (fetch/AJAX).

```sql
CREATE TABLE notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_notes_lead ON notes(lead_id);
```

Endpoint'ler:
- `GET  /api/lead/<id>/notes` → notları en yeniden eskiye döner
- `POST /api/lead/<id>/notes` → `{body}` ile yeni not ekler, eklenen notu döner
- `DELETE /api/note/<id>` → notu siler

Otomatik not: durum her değiştiğinde sisteme "Durum değişti: mail gitti" şeklinde bir not otomatik düşsün (`system` tipi). Böylece leadin geçmişi tek akışta görünür.

### Durum akışı

Durumlar dört evreye ayrılır. Arayüzde bu gruplamayla gösterilsin:

| Evre | Durum | Anlamı |
|---|---|---|
| **1. İletişim öncesi** | `yeni` | Listeye yeni düştü, hiçbir şey yapılmadı |
| | `araştırıldı` | Impressum çekildi, kurucu/mail bulundu, mail atmaya hazır |
| | `elendi` | Uygun değil (offshore, çok büyük, alakasız) — listede kalır ama gizlenir |
| **2. İletişim kuruldu** | `mail gitti` | İlk mail gönderildi (`emailed_at` dolar) |
| | `takip edildi` | Takip maili gönderildi (`followup_at` dolar) |
| **3. Cevap geldi** | `cevap var` | Olumlu/nötr cevap geldi |
| | `red` | Olumsuz cevap — kapandı |
| **4. Devir** | `aktarıldı` | Endor CRM'ine taşındı, bu araçtaki işi bitti |

Kurallar:
- `mail gitti` seçilince `emailed = 1` ve `emailed_at` otomatik dolsun; checkbox ile durum birbirine bağlı çalışsın (birini işaretleyince diğeri güncellensin)
- `takip edildi` seçilince `followup_at` otomatik dolsun
- `elendi`, `red` ve `aktarıldı` olanlar varsayılan listede gizlensin, filtreyle görünsün

---

## Skorlama (100 puan)

Tamamı `scoring.py` içinde, tek fonksiyon. **Puanlar ve ülke listeleri koda gömülmez** — `settings` tablosundan okunur, arayüzden değiştirilebilir (bkz. Ayarlar paneli). `scoring.py` sadece varsayılan değerleri tanımlar.

| Sinyal | Puan |
|---|---|
| DACH/Nordik **merkezli** | 25 |
| Saatlik ücret $100+ | 20 |
| Ekip 10–49 kişi | 15 (2–9 → 7, 50–99 → 5, 100+ → 0) |
| Design/strateji ağırlıklı, dev %50 altı | 15 |
| Kariyer sayfasında developer ilanı | 10 |
| Impressum'da kişisel mail bulundu | 10 (`info@` ise 5) |
| Aktif (son yorum < 12 ay) | 5 |

**Hedef ülkeler (merkez ülkesi bunlardan biriyse +25):**
Almanya, Avusturya, İsviçre, Hollanda, Belçika, İsveç, Danimarka, Norveç, Finlandiya

Ülke tespiti şehir adlarından da yapılabilmeli (Berlin, München, Wien, Zürich, Amsterdam, Stockholm, København, Oslo, Helsinki vb.). Şehir→ülke eşleme tablosu kodda dursun; ülke listesinin kendisi ayarlardan yönetilir.

**Otomatik ELE (negatif filtre):**
- Merkez ülke offshore: Ukrayna, Hindistan, Pakistan, Bangladeş, Belarus, Polonya, Romanya, Bulgaristan, Sırbistan, Vietnam, Filipinler, Mısır, Arjantin, Kolombiya → **bunlar müşteri değil, rakip**
- Saatlik ücret $50 altı → aynı sebep
- Ekip 250+ → kurumsal satın alma süreci, cold mail işlemez

**Kritik:** Ülke tespiti ajansın **merkezine** bakar, "serves Germany" ifadesine değil. Kyiv merkezli "serves Germany" ajansı ELE olmalı.

**Sınıflandırma:** 75+ SICAK · 55–74 ORTA · 35–54 ZAYIF · <35 ELE

---

## Ayarlar paneli

Skorlama ve ülke listeleri koda gömülmez, arayüzden yönetilir. `settings` tablosunda saklanır:

```sql
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,   -- JSON
    updated_at TEXT
);
```

Uygulama ilk açılışta varsayılan değerlerle doldurur (seed).

### Global ayarlar (kaynaktan bağımsız)

Bunlar **iş kararıdır**, hangi platformdan geldiğine bakmaz. Tek yerde tutulur.

- **Hedef ülkeler** — etiket (chip) seçimi. Tıklayarak ekle/çıkar. Varsayılan: Almanya, Avusturya, İsviçre, Hollanda, Belçika, İsveç, Danimarka, Norveç, Finlandiya
- **Elenecek ülkeler** — aynı etiket arayüzü. Varsayılan offshore listesi.
- **Skor ağırlıkları** — her sinyalin puanı sayı girişiyle değiştirilebilsin (ülke 25, ücret 20, ekip 15 …)
- **Eşikler** — SICAK / ORTA / ZAYIF sınırları (varsayılan 75 / 55 / 35)
- **Negatif filtre sınırları** — min saatlik ücret (varsayılan $50), max ekip büyüklüğü (varsayılan 250)

### Kaynak (connector) ayarları

Bunlar **teknik ayardır**, her kaynak için ayrı tutulur:

| Ayar | Clutch | Sortlist | Impressum |
|---|---|---|---|
| Aktif mi | ✓ | ✓ | ✓ |
| Bekleme (min–max sn) | 60–90 | 60–90 | 3–5 |
| Filtre URL şablonu | ✓ | ✓ | — |
| User-Agent | ✓ | ✓ | ✓ |

Yeni bir connector eklendiğinde ayarları otomatik olarak bu listede belirsin (connector kendi varsayılanlarını `base.py` üzerinden bildirsin).

### "Yeniden skorla" butonu — önemli

Ayarlar değiştiğinde mevcut kayıtların skoru **kendiliğinden güncellenmez.** Ayarlar panelinde bir "Tüm kayıtları yeniden skorla" butonu olmalı; basınca tüm leadler yeni ağırlıklarla baştan puanlanır ve `score_breakdown` yeniden yazılır.

Bu olmadan ayar paneli işe yaramaz: ağırlığı değiştirirsin, tablo aynı kalır, neden olmadığını anlamazsın.

Tek sayfa. Koyu tema. Excel benzeri tablo.

**Üst şerit**
- Sayaçlar: toplam · SICAK · mail atılan · cevap gelen
- Butonlar: Domain ekle · Impressum çek · CSV indir

**Ana tablo**
- Sütunlar: ☑ mail atıldı · Ajans · Konum · Saatlik · Ekip · **Skor (renkli etiket)** · Kurucu · E-posta · Durum · ⋯
- Varsayılan sıralama: skora göre azalan
- Filtreler: Hepsi / SICAK / SICAK+ORTA / ülke / mail atılmayanlar
- Satır içi düzenlenebilir: kurucu adı, e-posta, notlar
- **"Mail atıldı" checkbox'ı** — işaretlenince `emailed_at` otomatik dolar, durum `mail gitti` olur
- Arama kutusu (ajans adı / domain)

**Off-canvas panel** (satıra tıklayınca sağdan açılır)
- Skor kırılımı: hangi sinyalden kaç puan, neden
- Clutch detay verisi: servis yüzdeleri, portföy, yorumlar
- Impressum ham çıktısı (yanlış parse edilmişse görüp düzeltebilmek için)
- **Not akışı:** üstte tek satırlık ekleme kutusu (Enter ile kaydeder, sayfa yenilenmez), altında notlar tarihiyle birlikte en yeniden eskiye listelenir. Durum değişiklikleri de bu akışta soluk renkte görünür. Her notta silme butonu.
- Linkler: web sitesi · Impressum · kaynak profil
- Buton: **"CRM'e aktarıldı olarak işaretle"**

---

## Dayanıklılık kuralları

- **Elle girilen veri asla ezilmesin.** Kullanıcı bir alanı elle düzelttiyse (`contact_name`, `contact_email`, `phone`), o kayıt tekrar zenginleştirildiğinde crawler/LLM bu alanların üstüne yazmasın. Her alan için `*_manual` bayrağı tutulsun veya düzenlenen alanlar ayrı bir tabloda işaretlensin. Bu kural önemli: kullanıcı yanlış çekilen bir maili düzeltip sonra yeniden çalıştırdığında emeği kaybolmamalı.
- **Aynı lead tekrar işlenirse:** temel bilgiler (ücret, ekip büyüklüğü, servis dağılımı) güncellensin; iletişim bilgileri yukarıdaki kurala tabi; notlar ve durum asla değişmesin.
- **Parser esnek olsun.** Clutch DOM'unu sık değiştirir. En az iki strateji: JSON-LD varsa oradan, yoksa CSS selector.
- **Crawl kesilirse kaldığı yerden devam etsin.** Her kayıt işlendiği anda DB'ye yazılsın, sonda toplu değil.
- **Hata crawl'ı durdurmasın.** İlgili kaydı `error` işaretle, devam et.
- **Mükerrer kayıt olmasın** (name + location UNIQUE).
- Bekleme süreleri connector içinde tanımlı olsun, tek yerden ayarlanabilsin.

---

## Yapılmayacaklar

- **LinkedIn scraping.** Hesap kalıcı kapanır, GDPR riski var, üstelik satış yapılacak kitle bu konuda en hassas kitle. LinkedIn elle kullanılacak: mail gittikten 3–4 gün sonra bağlantı isteği + kısa not.
- **Cloudflare bypass.** Proxy rotasyonu, headless browser, CAPTCHA çözücü yok. Yavaş git, takılırsan kaydedilmiş HTML yükle.
- **Otomatik mail gönderimi.** Mailler Gmail'den elle, kişiselleştirilerek gidecek.
- **AI skorlama.** Skorlama kural tabanlı kalacak — LLM sadece iletişim bilgisi çıkarımında kullanılıyor, hedef eleme kararında değil.

---

## Burak'tan beklenenler

**Kod yazılmadan önce:**
- [ ] Clutch listing sayfası HTML'i (Ctrl+S → "HTML Only")
- [ ] Clutch ajans detay sayfası HTML'i (bir örnek yeterli)
- [ ] OpenAI API key (`.env` dosyasına konacak) — Impressum çıkarımının LLM fallback'i için

**Her aşama sonunda:**
- [ ] Çıktıyı gözle doğrula, bir sonrakine geçmeden önce onayla

**Paralel, kodu beklemeden:**
- [ ] İlk 10 maili elle gönder (Clutch'tan 10 ajans, Impressum'dan mail, şablonu kişiselleştir)
- [ ] Portföy PDF'inin ajans versiyonu (health tourism/SEO/ad slaytlarını çıkar, "how we work with agencies" ekle, CTA'yı değiştir)

---

## Başarı kriteri

Araç değil, sonuç ölçülür: **30 gün içinde en az 1 tanışma görüşmesi ve 1 küçük deneme projesi.**

30 maile 2–4 cevap normaldir. En büyük risk aracı mükemmelleştirip hiç mail atmamaktır.
