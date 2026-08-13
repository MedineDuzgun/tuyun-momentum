import pandas as pd
import hashlib

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

def normalize_excel(uploaded_file, secilen_sinif_manuel: str = "Belirtilmedi") -> pd.DataFrame:
    if uploaded_file is None:
        return pd.DataFrame()

    df_raw = pd.read_excel(uploaded_file, header=None)
    header_idx = 0

    for i in range(min(10, len(df_raw))):
        row_str = " ".join([str(val).lower() for val in df_raw.iloc[i].values])
        if any(keyword in row_str for keyword in ["adi", "numarasi", "telefon", "soyad"]):
            header_idx = i
            break

    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file, header=header_idx)

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

    df["Ad_Soyad"] = df["Ad_Soyad"].astype(str).str.replace("i̇", "i").str.replace("I", "ı").str.strip().str.title()

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

def ogrenci_metriklerini_hesapla(df_kayitlar: pd.DataFrame) -> pd.DataFrame:
    if df_kayitlar.empty:
        return pd.DataFrame(columns=["ogrenci_id", "temel_puan", "momentum_bonusu", "toplam_puan"])

    df = df_kayitlar.copy()
    df["puan"] = df["durum"].map(PUAN_TABLOSU).fillna(0.0)
    temel = df.groupby("ogrenci_id")["puan"].sum().rename("temel_puan")

    if "is_ay_sonu" in df.columns:
        kapanmis_aylar = df[df["is_ay_sonu"] == True]["ay_adi"].unique().tolist()
    else:
        kapanmis_aylar = []

    momentum_kayitlari = []

    for ogrenci_id, grup in df.groupby("ogrenci_id"):
        toplam_bonus = 0.0
        
        for ay in kapanmis_aylar:
            ay_grubu = grup[grup["ay_adi"] == ay]
            o_ayki_toplam_deneme = df[df["ay_adi"] == ay]["deneme_no"].nunique()
            cozdugu_sayi = len(ay_grubu[ay_grubu["durum"] == "Vaktinde Çözdü"])
            
            if o_ayki_toplam_deneme > 0 and cozdugu_sayi == o_ayki_toplam_deneme:
                toplam_bonus += MOMENTUM_BONUS

        momentum_kayitlari.append({"ogrenci_id": ogrenci_id, "momentum_bonusu": toplam_bonus})

    momentum_df = pd.DataFrame(momentum_kayitlari).set_index("ogrenci_id")["momentum_bonusu"]
    sonuc = pd.concat([temel, momentum_df], axis=1).fillna(0.0)
    sonuc["toplam_puan"] = sonuc["temel_puan"] + sonuc["momentum_bonusu"]
    return sonuc.reset_index()

def arama_listesi_hesapla(df_kayitlar: pd.DataFrame, df_aramalar: pd.DataFrame):
    if df_kayitlar.empty:
        return pd.DataFrame(columns=["ogrenci_id"]), None, None

    son_h_index = int(df_kayitlar["hafta_index"].max())
    onceki_h_index = son_h_index - 1 if son_h_index > 1 else 1

    riskli_ogrenciler = []

    for o_id, grup in df_kayitlar.groupby("ogrenci_id"):
        grup_sirali = grup.sort_values("hafta_index")
        haftalar = grup_sirali["hafta_index"].tolist()
        durumlar = grup_sirali["durum"].tolist()
        
        son_arama_hafta = 0
        if not df_aramalar.empty:
            col_check = "hafta_index" if "hafta_index" in df_aramalar.columns else "hafta_no"
            ogrenci_aramalari = df_aramalar[
                (df_aramalar["ogrenci_id"] == o_id) & 
                (df_aramalar["arama_sonucu"] != "Aranmadı")
            ]
            if not ogrenci_aramalari.empty:
                son_arama_hafta = int(ogrenci_aramalari[col_check].max())

        for i in range(len(durumlar) - 1):
            h1, h2 = haftalar[i], haftalar[i+1]
            d1, d2 = durumlar[i], durumlar[i+1]
            
            if h1 > son_arama_hafta and h2 > son_arama_hafta:
                if d1 == "Çözmedi" and d2 == "Çözmedi":
                    riskli_ogrenciler.append(o_id)
                    break
            elif son_arama_hafta == 0:
                if d1 == "Çözmedi" and d2 == "Çözmedi":
                    riskli_ogrenciler.append(o_id)
                    break

    if not riskli_ogrenciler:
        return pd.DataFrame(columns=["ogrenci_id"]), son_h_index, onceki_h_index

    risk_df = pd.DataFrame({"ogrenci_id": riskli_ogrenciler})

    son_durumlar = df_kayitlar[df_kayitlar["hafta_index"] == son_h_index][["ogrenci_id", "durum"]].rename(columns={"durum": "son_durum"})
    onceki_durumlar = df_kayitlar[df_kayitlar["hafta_index"] == onceki_h_index][["ogrenci_id", "durum"]].rename(columns={"durum": "onceki_durum"})

    risk_df = risk_df.merge(son_durumlar, on="ogrenci_id", how="left")
    risk_df = risk_df.merge(onceki_durumlar, on="ogrenci_id", how="left")

    risk_df["son_durum"] = risk_df["son_durum"].fillna("Çözmedi")
    risk_df["onceki_durum"] = risk_df["onceki_durum"].fillna("Çözmedi")

    return risk_df, son_h_index, onceki_h_index

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
