# Endor — Outreach Mail Şablonu (kopyala-yapıştır)

> Sistem mail göndermez. Buradan kopyala, Gmail/Workspace'te `{{...}}` alanlarını doldur, gönder.
> İpucu: Bunu Gmail'de **Ayarlar → Şablonlar (canned responses)** olarak da kaydedebilirsin.

---

## Konu satırı (title) seçenekleri

En güvenli/yüksek açılma oranlıdan aşağıya:

```
White-label dev partner for {{Agency}}?
```
```
Dev capacity for {{Agency}} — without hiring?
```
```
Quick question, {{Name}}
```
```
{{Agency}} + white-label development
```
```
{{City}} agencies + a dev partner
```

Notlar: Kısa tut (~50 karakter altı), ajans adını koymak açılmayı artırır, soru işareti cevabı davet eder. "Free", "offer", "cheap" gibi spam tetikleyen kelimelerden kaçın.

---

## Ana mail — gövde

**Varyant A — tasarım/strateji ağırlıklı ajanslar** (servis dağılımında dev %50 altı olanlar):

```
Hi {{Name}},

Came across {{Agency}} among the {{City/Region}} agencies that lead with
design and strategy rather than heavy in-house dev — that's exactly who we
partner best with.

I run Endor, an Istanbul-based dev studio that works white-label for agencies
like yours — you own strategy, design and the client; we're the development
arm behind the scenes, and your name stays on everything.

Most partners use us to take on projects they'd otherwise turn down, or to add
dev capacity without hiring. Euro-billed but a fraction of a local hire, and
only 1–2h ahead of your timezone.

Worth a short call next week to see if we're a fit?

Best,
Burak
```

**Varyant B — genelci / dev de yapan ajanslar** (A'daki "design-led" kancası yanlış kaçacaksa):

```
Hi {{Name}},

Came across {{Agency}} while mapping strong {{City/Region}} agencies — reaching
out because you're exactly the profile we partner with.

I run Endor, an Istanbul-based dev studio that works white-label for agencies
like yours — you keep the client and stay the face of everything; we're the
development arm behind the scenes.

Most partners use us to take on projects they'd otherwise turn down, or to add
dev capacity without hiring. Euro-billed but a fraction of a local hire, and
only 1–2h ahead of your timezone.

Worth a short call next week to see if we're a fit?

Best,
Burak
```

---

## Doldurma rehberi

- `{{Name}}` — kişi adı (Impressum/kurucu alanından). Bulamazsan "Hi there," ile geç ama kişi adı cevabı ciddi artırır.
- `{{Agency}}` — ajans adı.
- `{{City/Region}}` — tablodaki konum (ör. "Zurich", "the Swiss", "DACH").
- **Hangi varyant?** Panelde servis dağılımına bak: dev %50 altıysa → **A**. Dev ağırlıklıysa ya da emin değilsen → **B** (daha nötr, yanlış kaçmaz).
- İlk maile **link/PDF ekleme.** Cevap gelince portföyün ajans versiyonunu gönder.
- Tek CTA, tek soru. Kısa kalsın.

---

## Takip dizisi (spec'teki ritim)

**1) LinkedIn notu — mailden 3–4 gün sonra, bağlantı isteğiyle:**

```
Hi {{Name}} — reached out by email last week about Endor (white-label dev for
agencies). No worries if the timing's off; just thought it'd be good to connect.
```

**2) Kısa hatırlatma maili — cevap yoksa ~4–5 gün sonra, aynı thread'e reply:**

```
Hi {{Name}}, floating this back up in case it slipped through. Even a quick "not
now" is helpful — happy to circle back later if the timing's better.

Best,
Burak
```

---

## İpuçları

- 30 maile 2–4 cevap normaldir; hacim + tutarlılık kazandırır.
- En büyük risk: şablonu mükemmelleştirip hiç göndermemek. İlk 10'u bugün gönder.
- Aynı gün çok sayıda birebir aynı mail atma; Workspace'te günlük makul bir tempo tut (deliverability için).
- Cevap gelince kaydı araçta "cevap var" yap, sonra "aktarıldı" ile Endor CRM'ine geçir.
