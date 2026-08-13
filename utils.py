import hashlib
import pandas as pd

# ===================================================================
# SABİTLER
# ===================================================================
PUAN_TABLOSU = {
    "Vaktinde Çözdü": 1.0,
    "Geç Çözdü": 0.5,
    "Çözmedi": -1.0,
    "Muaf": 0.0,
}
MOMENTUM_BONUS = 0.5

AYLAR = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"
]

SINIF_SEVIYELERI = [
    "Tüm Sınıflar",
    "5. Sınıf", "6. Sınıf", "7. Sınıf", "8. Sınıf",
    "9. Sınıf", "10. Sınıf", "11. Sınıf", "12. Sınıf", "Mezun"
]

ARAMA_SONUCU_SECENEKLERI = [
    "Ulaşıldı - Olumlu / Devam Ediyor",
    "Ulaşıldı - Engel Var (görüşme planlandı)",
    "Ulaşılamadı - Tekrar Denenecek",
    "Aranmadı",
]

# ===================================================================
# YARDIMCI VE METİN FONKSİYONLARI
# ===================================================================
def normalize_excel(uploaded_file, secilen_sinif_manuel: str = "Belirtilmedi") -> pd.DataFrame:
    if uploaded_file is None:
        return pd.DataFrame()

    df_raw = pd.read_excel(uploaded_file, header=None)
    header_idx = 0

    # Başlık satırını bulma
    for i in range(min(10, len(df_raw))):
        row_str = " ".join([str(val).lower() for val in df_raw.iloc[i].values])
        if any(keyword in row_str for keyword in ["adi", "numarasi", "telefon", "soyad"]):
            header_idx = i
            break

    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file, header=header_idx)

    # Sütun isimlerini standartlaştırma
    col_map = {}
    for col in df.columns:
        col_clean = str(col).strip().lower()
        if "adi" in col_clean or "soyad" in col_clean:
            col_map[col] = "Ad_Soyad"
        elif "numara" in col_clean or "id" in col_clean:
            col_map[col] = "Ogrenci_ID"
        elif "telefon" in col_clean and "veli" not in col_clean:
            col_map[col] = "Telefon"

    df = df.rename(columns=col_map)

    if "Ad_Soyad" not in df.columns:
        return pd.DataFrame()

    if "Ogrenci_ID" not in df.columns:
        df["Ogrenci_ID"] = 0

    if "Telefon" not in df.columns:
        df["Telefon"] = ""

    # İsim Temizliği
    df["Ad_Soyad"] = df["Ad_Soyad"].astype(str).str.replace("i̇", "i").str.replace("I", "ı").str.strip().str.title()

    # AD + SOYAD + TELEFON HASH'LEME İLE BENZERSİZ ID ÜRETİMİ
    def generate_unique_id(row):
        val = str(row["Ogrenci_ID"]).split(".")[0].strip()
        name_clean = str(row["Ad_Soyad"]).strip().lower()
        phone_clean = str(row.get("Telefon", "")).replace(" ", "").replace("-", "").replace("(", "").replace(")", "").strip()

        if val in ["0", "nan", "None", "", "null"]:
            unique_str = f"{name_clean}_{phone_clean}"
            hash_val = hashlib.sha256(unique_str.encode('utf-8')).hexdigest()
            return int(hash_val[:8], 16) % 1000000
        try:
            return int(val)
        except:
            unique_str = f"{name_clean}_{phone_clean}"
            hash_val = hashlib.sha256(unique_str.encode('utf-8')).hexdigest()
            return int(hash_val[:8], 16) % 1000000

    df["Ogrenci_ID"] = df.apply(generate_unique_id, axis=1)
    df["Sinif_Seviyesi"] = secilen_sinif_manuel

    return df

def whatsapp_mesaji_olustur(ad_soyad: str) -> str:
    ilk_isim = str(ad_soyad).split()[0]
    return (
        f"Merhaba, {ilk_isim} için Tuyun Akademi'den yazıyoruz. 🌱\n"
        f"Son iki haftadır deneme çözme takibinde {ilk_isim}'i göremedik. "
        f"Bunun bir sebebi olabilir, merak ettik ve yardımcı olmak istedik. "
        f"Uygun olduğunuzda kısa bir görüşme yapabilir miyiz? "
        f"Amacımız baskı kurmak değil, birlikte tekrar düzenli bir ritme geçmek. 🙏"
    )

def genel_yorum_uret(ortalama_puan: float) -> str:
    if ortalama_puan >= 1.0:
        return "Sınıf geneli pozitif bir momentum sergiliyor. Süreç stabil devam ediyor."
    elif ortalama_puan >= 0:
        return "Sınıf geneli nötr-pozitif bantta. Riskli öğrencilere odaklanılabilir."
    else:
        return "⚠️ Sınıf ortalaması negatif. 'Çözmedi' sayısı yüksek."
