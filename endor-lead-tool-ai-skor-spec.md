# Endor Lead Tool — AI Skoru + Yorumu (Tasarım Notu)

> Durum: **taslak / uygulanmadı.** Bu doküman "kural skorunu ezmeyen, yanına eklenen ayrı bir AI değerlendirmesi" özelliğinin tasarımıdır. Onayladıktan sonra kodlanacak.

## Neden

Kural tabanlı skor (scoring.py) yapısal sinyalleri iyi ölçüyor: merkez ülke, saatlik ücret, ekip, dev dağılımı, rakip sinyali… Ama iki şeyi göremiyor:

1. **Niteliksel fit.** Ajansın Clutch açıklaması/tagline'ı/servis dağılımı "geliştirmeyi dışarı verir mi, yoksa kendi mi yapar" konusunda ipucu barındırır. Örn. "startup'lara marka + strateji sunan tasarım stüdyosu" (dışarı verir → iyi hedef) ile "tam kadro mühendislik evi" (kendi yapar → muhtemel rakip) arasındaki farkı kurallar tam yakalayamaz.
2. **Strateji uyumu.** Örnek: bir Yunan ajansı kural skorundan 90 (SICAK) alabiliyor, ama Yunanistan hedef bölgemiz değil. AI, "bu gerçekten DACH/Nordik white-label tezine uyuyor mu?" sorusunu sorabilir.

**Amaç:** Kararı değil, **ikinci bir görüşü** eklemek. Kural skoru gerçeğin kaynağı olarak kalır; AI skoru + yorumu yanında danışma amaçlı durur.

## Temel ilke (pazarlık konusu değil)

- **AI, kural skorunu ve verdict'i ASLA değiştirmez.** Ayrı alanlarda saklanır, ayrı gösterilir.
- **AI eleme kararı vermez.** `verdict` (SICAK/ORTA/ZAYIF/ELE), filtreler ve sıralama varsayılanı kural skoruna dayanır.
- AI çıktısı güvenilmez kabul edilir: doğrulamadan geçer, uydurma engellenir (bkz. Impressum LLM katmanındaki aynı yaklaşım).
- **Otomatik olarak 478 kaydın hepsine çalışmaz.** Tetikleme kontrollü (aşağıda).

## Ne zaman çalışır (tetikleme)

Maliyet ve gürültüyü düşük tutmak için üç mod:

1. **Tek tek (panelden).** Bir lead'in off-canvas panelinde "🤖 AI değerlendir" butonu — o kayıt için çalışır. Ana kullanım.
2. **Toplu, kısa listede.** "SICAK/ORTA olanları AI ile değerlendir" — sadece kısa listeye (ör. verdict ∈ {SICAK, ORTA}) çalışır, tüm tabloya değil. Nano ile maliyeti kuruşluk.
3. **(Opsiyonel) Impressum sonrası otomatik** — sadece verdict eşiğini geçenler için. Varsayılan kapalı; ayarlardan açılır.

Zaten AI değerlendirmesi olan kayıt tekrar işlenmez (yeniden çalıştırma tek tek panelden zorlanır) — Impressum akışındaki `pending` mantığının aynısı.

## Girdi (LLM'e ne veriyoruz)

Ham web sayfası değil, elimizdeki **yapılandırılmış gerçekler** + kısa açıklama, **veri olarak** (talimat olarak değil):

- Ajans adı, merkez ülke, tüm ofis ülkeleri
- Saatlik ücret, ekip büyüklüğü, kuruluş yılı
- Servis dağılımı (services_json), dev ağırlığı
- Yorum sayısı / puan, rakip sinyali (is_competitor)
- Tagline / açıklama (Clutch profilinden)
- **Kural skoru + kırılımı** (AI bunu görsün ki "kurallar şunu dedi, ben katılıyor muyum" diyebilsin)
- Bizim tezimiz (hedef ülkeler, white-label dışarı-verme mantığı) — sabit sistem bağlamı

## Çıktı (yapılandırılmış JSON, doğrulanır)

```json
{
  "ai_score": 0,
  "ai_label": "Güçlü fit | Olası fit | Zayıf fit | Rakip şüphesi | Bölge dışı",
  "ai_comment": "2-4 cümle: neden bu değerlendirme, kuralların gözden kaçırdığı nokta",
  "ai_angle": "1 cümle: outreach için önerilen açı",
  "ai_confidence": 0.0,
  "ai_flags": ["hedef ülke dışı", "muhtemel rakip", "dev ağırlıklı", ...]
}
```

Doğrulama (kural, Impressum LLM katmanıyla aynı ruh):
- `ai_score` 0–100'e kırpılır; sayı değilse değerlendirme `manual` bırakılır
- `ai_label` sabit kümeden biri değilse boş bırakılır
- `ai_comment` / `ai_angle` uzunluk sınırı, `http`/enjeksiyon kalıpları temizlenir
- `ai_confidence < 0.5` → panelde "düşük güven" rozetiyle gösterilir
- Model/anahtar yoksa uygulama çökmez, alan boş kalır (Impressum'daki gibi)

## Ayrışma sinyali (asıl değer burada)

AI skoru ile kural skoru **çok ayrışınca** işaretle — Yunanistan/Cleveroad tipi durumları burada yakalarız:

- `|ai_score − score| ≥ 25` ise panelde ve tabloda "⚠ AI ayrışması" rozeti
- Örn. kural 90 SICAK ama AI 40 "Bölge dışı" → gözden geçir
- Bu bir **uyarı**, otomatik aksiyon değil; sen karar verirsin

## Veri modeli (yeni kolonlar, migration ile)

`leads` tablosuna (mevcut `migrate()` mekanizmasıyla, şema bozulmadan):

| Kolon | Tip | Açıklama |
|---|---|---|
| `ai_score` | INTEGER | 0–100, danışma skoru |
| `ai_label` | TEXT | sabit etiket kümesi |
| `ai_comment` | TEXT | kısa gerekçe |
| `ai_angle` | TEXT | outreach açısı |
| `ai_confidence` | TEXT | 0–1 |
| `ai_flags` | TEXT | JSON liste |
| `ai_model` | TEXT | hangi model üretti |
| `ai_at` | TEXT | zaman damgası |
| `ai_status` | TEXT | pending / done / manual / error |

Kural skoru alanları (`score`, `verdict`, `score_breakdown`) **hiç değişmez.**

## Arayüz

- **Off-canvas panel:** "🤖 AI değerlendirmesi" bölümü — AI skoru (kural skorunun yanında, farklı renk), etiket, yorum, outreach açısı, flag'ler, güven. Üstte "AI değerlendir / yeniden değerlendir" butonu.
- **Tablo (opsiyonel):** "AI" sütunu (ayrı etiket). Kural skoru sütunu aynen kalır. İstenirse AI skoruna göre de sıralanabilir (mevcut istemci sıralamasına bir kolon daha).
- **Ayrışma rozeti:** ayrıştığında satırda küçük "⚠ AI≠kural" işareti.
- Filtreler kural skoruna dayalı kalır; ek olarak "AI ayrışanları göster" filtresi eklenebilir.

## Maliyet

Girdi kısa (yapılandırılmış gerçekler + tagline ≈ 400–800 token). gpt-4.1-nano ile lead başına ~$0.0001. 478 kaydın hepsi bile birkaç sente gelir; ama pratik kullanım kısa liste + tek tek olduğu için maliyet ihmal edilebilir. Her çağrının token'ı loglanır (Impressum'daki gibi).

## Kapsam dışı (yapılmayacaklar)

- AI skoru **eleme/verdict kararı vermez**, kural skorunu ezmez.
- Otomatik olarak tüm tabloya çalışmaz (varsayılan).
- Kişiselleştirilmiş mail taslağı **bu özelliğin parçası değil** — ayrı, ilişkili bir iş (bkz. "Sonraki adım").

## Sonraki adım (ilişkili, ayrı iş)

En yüksek ROI'li AI kullanımı muhtemelen **kişiye özel ilk mail taslağı** (ajans açıklaması + Endor pitch'inden). Bu ayrı bir tasarım notu olacak; AI skoru onaylanırsa aynı `extractors/llm.py` altyapısı yeniden kullanılır.

## Açık kararlar (senin onayın gerek)

1. **Kapsam:** Toplu değerlendirme sadece SICAK/ORTA'ya mı çalışsın, yoksa "o an ekranda filtrelenmiş ne varsa" ona mı?
2. **Otomatik mod:** Impressum sonrası otomatik AI değerlendirmesi varsayılan **kapalı** olsun (öneri bu) — onaylıyor musun?
3. **Tabloda görünürlük:** AI skoru tabloda ayrı sütun mu olsun, yoksa sadece panelde mi? (Sütun tabloyu şişirir ama hızlı tarama sağlar.)
4. **Ayrışma eşiği:** 25 puan farkı işaretlemek için makul mü, yoksa daha dar/geniş mi?
5. **Model:** gpt-4.1-nano yeterli mi, yoksa yorum kalitesi için bir üst kademe (ör. gpt-4.1-mini) sadece bu iş için mi kullanılsın?
