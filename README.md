# Etsy + n8n otomasyonlari

## 1) Gunluk Etsy satis raporu

Her sabah **09:05 (Istanbul)** GitHub Actions calisir, n8n uzerinden Etsy'den
satislari okur ve raporu **selinmetin13@gmail.com** adresine mail atar.

- **Salt okuma:** Etsy'ye sadece GET (okuma) istegi gider. Urun yayinlama,
  silme, fiyat degistirme, reklam acma YOK. Kod bunu her calismada kontrol eder
  (`assert_read_only`).
- **Alici bilgisi yok:** Isim, e-posta, adres, mesaj gibi bilgiler n8n icinde
  atilir; rapora ve GitHub'a hic gelmez.
- **Depo herkese acik:** Rapor icerigi loglara/dosyalara yazilmaz, sadece mail
  olarak gider.

### Gerekenler
| Nerede | Ne | Durum |
|---|---|---|
| GitHub secret | `N8N_API_KEY` | var |
| n8n Credentials | `Etsy OAuth2` | var |
| n8n Credentials | **Gmail OAuth2** (mail gondermek icin) | eklenmeli |
| GitHub secret (istege bagli) | `ETSY_API_KEY` = `keystring:shared_secret` | gerekmiyor (mevcut n8n is akislarindan otomatik bulunuyor) |

### Elle calistirma
GitHub → **Actions** → **Etsy gunluk satis raporu** → **Run workflow**.

### Dosyalar
- `scripts/etsy_report.py` – n8n is akisini kurar, veriyi alir, raporu hazirlar.
- `scripts/n8n_check.py` – n8n hesabini salt okuma olarak kontrol eder.
- `tests/` – sahte veriyle testler.

## 2) Kripto sanal portfoy (SAHTE para)

Her sabah **09:10 (Istanbul)** calisir. **Borsa hesabi yok, API anahtari yok,
gercek emir yok.** Sadece Binance'in herkese acik fiyat verisini okur.

- Baslangic: **200 USDT** (sahte), 10 coin: BTC ETH SOL BNB XRP ADA DOGE AVAX LINK DOT
- **3x kaldirac**, izole teminat, **long ve short**
- Kurallar: EMA20/EMA50 trendi + RSI ile sinyal; her pozisyona toplam degerin
  %10'u teminat (ayni anda en fazla 5 pozisyon, sinyal fazlaysa en guclu trendler); stop 2×ATR (%3–15), hedef stop mesafesinin 2 kati; trend
  bozulunca kapanir. Gun icinde stop/hedef/likidasyon saatlik fiyatlarla kontrol edilir.
- Ucret %0,05 (islem basina); fonlama 8 saatte %0,01 (tahmini).
- GitHub sunuculari Binance vadeli API'sine erisemedigi icin (ABD kisitlamasi)
  fiyatlar Binance **spot** verisinden alinir; fark cok kucuktur.

Her sabah "**hareket VAR / YOK**" raporu mail olarak gelir ve depoya da kaydedilir:
- Son rapor: [`raporlar/kripto/SON_RAPOR.md`](raporlar/kripto/SON_RAPOR.md)
- Gecmis raporlar: `raporlar/kripto/`
- Portfoy durumu: `data/kripto/portfoy.json`

Portfoyu sifirlamak icin `data/kripto/portfoy.json` dosyasini silmek yeterli.
Bu bir yatirim tavsiyesi degildir.
