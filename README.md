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
