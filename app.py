"""
tuyun_momentum_app.py
-----------------------
Tuyun Akademi - Tuyun Momentum Sistemi (Manuel Sınıf Seçimi & Telefon Hash'li ID Sistemi)
"""

import psycopg2
from psycopg2.extras import execute_batch
from datetime import datetime
import pandas as pd
import streamlit as st
import hashlib
import io
import urllib.parse

# ===================================================================
# 1) SABİTLER
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
# 2) DİNAMİK EXCEL TEMİZLEME VE HASH TABANLI ID ÜRETİMİ
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

    # --- AD + SOYAD + TELEFON HASH'LEME İLE BENZERSİZ ID ÜRETİMİ ---
    def generate_unique_id(row):
        val = str(row["Ogrenci_ID"]).split(".")[0].strip()
        name_clean = str(row["Ad_Soyad"]).strip().lower()
        phone_clean = str(row.get("Telefon", "")).replace(" ", "").replace("-", "").replace("(", "").replace(")", "").strip()

        # Eğer okul numarası yoksa, 0 veya geçersizse: Ad + Soyad + Telefon hash'i al
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

    # Sınıf Seviyesi manuel panelden gelen değer olarak atanır
    df["Sinif_Seviyesi"] = secilen_sinif_manuel

    return df

# ===================================================================
# 3) VERİTABANI KATMANI (SUPABASE POSTGRESQL)
# ===================================================================
def get_conn():
    try:
        conn = psycopg2.connect(st.secrets["postgres"]["url"])
        return conn
    except Exception as e:
        st.error(f"Veritabanı bağlantı hatası: {e}")
        return None

def init_db() -> None:
    conn = get_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS ogrenciler (
                        ogrenci_id BIGINT PRIMARY KEY,
                        ad_soyad TEXT NOT NULL,
                        telefon TEXT,
                        sinif_seviyesi TEXT,
                        kayit_tarihi TEXT
                    );
                    
                    ALTER TABLE ogrenciler ADD COLUMN IF NOT EXISTS sinif_seviyesi TEXT;

                    CREATE TABLE IF NOT EXISTS deneme_kayitlari (
                        id SERIAL PRIMARY KEY,
                        ogrenci_id BIGINT NOT NULL REFERENCES ogrenciler(ogrenci_id) ON DELETE CASCADE,
                        ay_adi TEXT NOT NULL,
                        deneme_no INT NOT NULL,
                        hafta_index INT NOT NULL,
                        durum TEXT NOT NULL,
                        is_ay_sonu BOOLEAN DEFAULT FALSE,
                        yukleme_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(ogrenci_id, ay_adi, deneme_no)
                    );
                    
                    ALTER TABLE deneme_kayitlari ADD COLUMN IF NOT EXISTS is_ay_sonu BOOLEAN DEFAULT FALSE;

                    CREATE TABLE IF NOT EXISTS arama_notlari (
                        id SERIAL PRIMARY KEY,
                        ogrenci_id BIGINT NOT NULL REFERENCES ogrenciler(ogrenci_id) ON DELETE CASCADE,
                        hafta_index INT,
                        arama_sonucu TEXT,
                        not_metni TEXT,
                        arayan TEXT,
                        kayit_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.commit()
        except Exception as e:
            conn.rollback()
            st.error(f"Tablo kontrol hatası: {e}")
        finally:
            conn.close()

def ogrencileri_kaydet(conn, df_ogrenci: pd.DataFrame) -> None:
    with conn.cursor() as cur:
        # İsim ve Telefon ikilisine göre eşleştirme kontrolü
        cur.execute("SELECT LOWER(ad_soyad), REGEXP_REPLACE(telefon, '[^0-9]', '', 'g'), ogrenci_id FROM ogrenciler;")
        mevcut_ogrenciler = {(row[0].strip(), row[1].strip()): row[2] for row in cur.fetchall()}

        kayit_verileri = []
        bugun = datetime.now().strftime("%Y-%m-%d")

        for _, r in df_ogrenci.iterrows():
            ad = str(r["Ad_Soyad"]).strip()
            ad_key = ad.lower()
            tel_clean = str(r.get("Telefon", "")).replace(" ", "").replace("-", "").replace("(", "").replace(")", "").strip()
            
            key = (ad_key, tel_clean)
            o_id = mevcut_ogrenciler.get(key, int(r["Ogrenci_ID"]))
            sinif = str(r.get("Sinif_Seviyesi", "Belirtilmedi")).strip()
            
            kayit_verileri.append((o_id, ad, str(r.get("Telefon", "")), sinif, bugun))

        query = """
            INSERT INTO ogrenciler (ogrenci_id, ad_soyad, telefon, sinif_seviyesi, kayit_tarihi)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT(ogrenci_id) DO UPDATE SET
                ad_soyad=EXCLUDED.ad_soyad,
                telefon=EXCLUDED.telefon,
                sinif_seviyesi=EXCLUDED.sinif_seviyesi
        """
        execute_batch(cur, query, kayit_verileri)

def arama_notu_ekle(ogrenci_id: int, hafta_index: int, sonuc: str, not_metni: str, arayan: str) -> None:
    conn = get_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                safe_id = int(ogrenci_id) if ogrenci_id is not None else 0
                safe_h_idx = int(hafta_index) if hafta_index is not None else 1
                safe_sonuc = str(sonuc).strip() if (sonuc and str(sonuc).strip()) else "Aranmadı"
                safe_not = str(not_metni).strip() if (not_metni and str(not_metni).strip()) else "Not girilmedi"
                safe_arayan = str(arayan).strip() if (arayan and str(arayan).strip()) else "Sistem / Belirtilmedi"

                cur.execute(
                    """INSERT INTO arama_notlari (ogrenci_id, hafta_index, arama_sonucu, not_metni, arayan, kayit_zamani)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (safe_id, safe_h_idx, safe_sonuc, safe_not, safe_arayan, datetime.now()),
                )
                conn.commit()
                st.cache_data.clear()
        except Exception as e:
            conn.rollback()
            st.error(f"Arama notu kaydedilirken hata oluştu: {e}")
        finally:
            conn.close()

def deneme_kaydi_sil(deneme_id: int) -> None:
    conn = get_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM deneme_kayitlari WHERE id = %s", (deneme_id,))
                conn.commit()
                st.cache_data.clear()
        except Exception as e:
            conn.rollback()
            st.error(f"Deneme silinirken hata: {e}")
        finally:
            conn.close()

@st.cache_data(ttl=30, show_spinner=False)
def tum_veriyi_oku():
    conn = get_conn()
    if not conn:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    df_ogrenciler = pd.read_sql_query("SELECT * FROM ogrenciler", conn)
    df_kayitlar = pd.read_sql_query("SELECT * FROM deneme_kayitlari ORDER BY hafta_index ASC", conn)
    df_aramalar = pd.read_sql_query("SELECT * FROM arama_notlari ORDER BY kayit_zamani DESC", conn)
    conn.close()
    return df_ogrenciler, df_kayitlar, df_aramalar

def sonraki_hafta_index() -> int:
    conn = get_conn()
    if not conn:
        return 1
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(hafta_index) FROM deneme_kayitlari")
        sonuc = cur.fetchone()[0]
    conn.close()
    return int(sonuc) + 1 if sonuc is not None else 1

def cift_excel_islem_ve_yukle(file_cozenler, file_cozmeyenler, ay_adi: str, deneme_no: int, is_ay_sonu: bool, secilen_sinif: str) -> int:
    conn = get_conn()
    if not conn:
        return 0

    df_cozenler = normalize_excel(file_cozenler, secilen_sinif)
    if not df_cozenler.empty:
        df_cozenler["Durum"] = "Vaktinde Çözdü"

    df_cozmeyenler = normalize_excel(file_cozmeyenler, secilen_sinif)
    if not df_cozmeyenler.empty:
        df_cozmeyenler["Durum"] = "Çözmedi"

    df_birlesik = pd.concat([df_cozenler, df_cozmeyenler], ignore_index=True)

    if df_birlesik.empty:
        conn.close()
        return 0

    ogrencileri_kaydet(conn, df_birlesik)
    h_idx = sonraki_hafta_index()

    cur = conn.cursor()
    cur.execute("SELECT LOWER(ad_soyad), REGEXP_REPLACE(telefon, '[^0-9]', '', 'g'), ogrenci_id FROM ogrenciler")
    id_map = {(row[0].strip(), row[1].strip()): row[2] for row in cur.fetchall()}

    if is_ay_sonu:
        cur.execute("UPDATE deneme_kayitlari SET is_ay_sonu = TRUE WHERE ay_adi = %s", (ay_adi,))

    deneme_verileri = []
    zaman_simdi = datetime.now()

    for _, r in df_birlesik.iterrows():
        ad_clean = str(r["Ad_Soyad"]).strip().lower()
        tel_clean = str(r.get("Telefon", "")).replace(" ", "").replace("-", "").replace("(", "").replace(")", "").strip()
        key = (ad_clean, tel_clean)
        
        real_id = id_map.get(key, int(r["Ogrenci_ID"]))
        durum_val = str(r["Durum"]).strip() if r["Durum"] else "Muaf"
        deneme_verileri.append((real_id, ay_adi, int(deneme_no), int(h_idx), durum_val, is_ay_sonu, zaman_simdi))

    deneme_query = """
        INSERT INTO deneme_kayitlari (ogrenci_id, ay_adi, deneme_no, hafta_index, durum, is_ay_sonu, yukleme_zamani)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(ogrenci_id, ay_adi, deneme_no) DO UPDATE SET
            durum=EXCLUDED.durum,
            hafta_index=EXCLUDED.hafta_index,
            is_ay_sonu=EXCLUDED.is_ay_sonu,
            yukleme_zamani=EXCLUDED.yukleme_zamani
    """
    execute_batch(cur, deneme_query, deneme_verileri)
    cur.close()

    conn.commit()
    conn.close()
    st.cache_data.clear()
    return len(df_birlesik)

def veritabanini_sifirla() -> None:
    conn = get_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE arama_notlari, deneme_kayitlari, ogrenciler RESTART IDENTITY CASCADE;")
            conn.commit()
        conn.close()
        st.cache_data.clear()

# ===================================================================
# 4) İŞ MANTIĞI HESAPLAMALARI
# ===================================================================
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

# ===================================================================
# 5) STREAMLIT ARAYÜZÜ
# ===================================================================
st.set_page_config(page_title="Tuyun Momentum Sistemi", page_icon="🎯", layout="wide")

init_db()

st.title("🎯 Tuyun Momentum Sistemi")
st.caption("Veritabanı: Supabase Cloud (Kalıcı Bulut Veritabanı)")

with st.sidebar:
    st.header("📂 Aylık Veri Yükleme")

    file_cozenler = st.file_uploader("1. Çözenler Listesi (.xlsx)", type=["xlsx", "xls"], key="cozenler")
    file_cozmeyenler = st.file_uploader("2. Çözmeyenler Listesi (.xlsx)", type=["xlsx", "xls"], key="cozmeyenler")

    secilen_ay = st.selectbox("Hangi Ay?", options=AYLAR)
    deneme_no = st.number_input("Ayın Kaçıncı Denemesi?", min_value=1, max_value=20, value=1, step=1)
    
    secilen_sinif_yukleme = st.selectbox("Sınıf / Düzey Seçin", options=SINIF_SEVIYELERI[1:], help="Yüklenen listenin ait olduğu sınıf seviyesini belirleyin.")
    
    is_ay_sonu = st.checkbox("🏁 Bu deneme, bu ayın son denemesi mi?", help="İşaretlenirse bu ay tamamlanmış kabul edilir ve tam çözenlere +0.5 Momentum Bonusu eklenir.")

    if st.button("✅ İki Listeyi İşle ve Kaydet", type="primary"):
        if file_cozenler is None and file_cozmeyenler is None:
            st.error("Lütfen en az bir Excel dosyası yükleyin.")
        else:
            toplam_kayit = cift_excel_islem_ve_yukle(file_cozenler, file_cozmeyenler, secilen_ay, int(deneme_no), is_ay_sonu, secilen_sinif_yukleme)
            st.success(f"{secilen_ay} Ayı - Deneme {deneme_no}: Toplam {toplam_kayit} öğrenci verisi işlendi.")
            st.rerun()

    st.divider()
    df_ogrenciler_ozet, df_kayitlar_ozet, _ = tum_veriyi_oku()
    st.metric("Kayıtlı Öğrenci", len(df_ogrenciler_ozet))
    st.metric("İşlenmiş Deneme Sayısı", df_kayitlar_ozet["hafta_index"].nunique() if not df_kayitlar_ozet.empty else 0)

    st.divider()
    st.subheader("⚙️ Sistem Yönetimi")
    onay = st.checkbox("Verileri silmeyi onaylıyorum")
    if st.button("🗑️ Veritabanını Sıfırla", type="secondary"):
        if onay:
            veritabanini_sifirla()
            st.warning("Veritabanı tamamen sıfırlandı!")
            st.rerun()
        else:
            st.error("Lütfen önce yukarıdaki onay kutusunu işaretleyin.")

df_ogrenciler, df_kayitlar, df_aramalar = tum_veriyi_oku()

if df_kayitlar.empty:
    st.info("👈 Henüz veri yüklenmedi. Başlamak için soldaki panelden Excel dosyalarını yükleyin.")
    st.stop()

metrikler = ogrenci_metriklerini_hesapla(df_kayitlar)
metrikler = metrikler.merge(df_ogrenciler, on="ogrenci_id", how="left")

arama_listesi, son_h_idx, onceki_h_idx = arama_listesi_hesapla(df_kayitlar, df_aramalar)
if not arama_listesi.empty:
    arama_listesi = arama_listesi.merge(df_ogrenciler, on="ogrenci_id", how="left")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Toplam Öğrenci", len(df_ogrenciler))
col2.metric("Ortalama Puan", f"{metrikler['toplam_puan'].mean():.2f}" if not metrikler.empty else "—")
col3.metric("Son Yüklenen Index", son_h_idx if son_h_idx else "—")
col4.metric("Arama Listesi", len(arama_listesi), delta_color="inverse")

st.divider()

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📞 Arama Listesi", 
    "🏆 Scoreboard", 
    "🤖 AI Analist Raporu", 
    "📈 Öğrenci Profil & Geçmişi", 
    "🗂️ Tüm Arama Geçmişi"
])

# ===================================================================
# TAB 1: ARAMA LİSTESİ
# ===================================================================
with tab1:
    st.subheader("Operasyonel Arama Listesi (2 Hafta Üst Üste Çözmeyenler)")
    
    if arama_listesi.empty:
        st.success("Risk altında öğrenci yok. ✅")
    else:
        col_csv, col_excel = st.columns(2)
        with col_csv:
            csv_data = arama_listesi.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📄 CSV Olarak İndir",
                data=csv_data,
                file_name=f"arama_listesi_hafta_{son_h_idx}.csv",
                mime="text/csv",
                use_container_width=True
            )
        with col_excel:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                arama_listesi.to_excel(writer, index=False, sheet_name='Arama Listesi')
            st.download_button(
                label="📊 Excel (.xlsx) Olarak İndir",
                data=buffer.getvalue(),
                file_name=f"arama_listesi_hafta_{son_h_idx}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

        st.write("")
        cols_to_show = ["ogrenci_id", "ad_soyad", "telefon", "sinif_seviyesi", "onceki_durum", "son_durum"]
        cols_final = [c for c in cols_to_show if c in arama_listesi.columns]
        
        st.dataframe(arama_listesi[cols_final], use_container_width=True, hide_index=True)

        for _, r in arama_listesi.iterrows():
            sinif_bilgisi = f" - {r.get('sinif_seviyesi', '')}" if pd.notna(r.get('sinif_seviyesi')) else ""
            with st.expander(f"{r['ad_soyad']} (ID {r['ogrenci_id']}){sinif_bilgisi}"):
                msg_text = whatsapp_mesaji_olustur(r["ad_soyad"])
                st.text_area("WhatsApp Mesajı", value=msg_text, height=100, key=f"msg_{r['ogrenci_id']}")
                
                tel_clean = str(r.get("telefon", "")).replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
                if tel_clean:
                    if not tel_clean.startswith("90") and len(tel_clean) == 10:
                        tel_clean = "90" + tel_clean
                    wa_url = f"https://wa.me/{tel_clean}?text={urllib.parse.quote(msg_text)}"
                    st.link_button("💬 WhatsApp'tan Mesaj Gönder", wa_url)

                with st.form(key=f"form_{r['ogrenci_id']}"):
                    sonuc = st.selectbox("Sonuç", ARAMA_SONUCU_SECENEKLERI, key=f"res_{r['ogrenci_id']}")
                    not_metni = st.text_area("Not", key=f"note_{r['ogrenci_id']}")
                    arayan = st.text_input("Arayan", key=f"who_{r['ogrenci_id']}")
                    
                    if st.form_submit_button("Kaydet"):
                        try:
                            safe_ogrenci_id = int(r["ogrenci_id"])
                            target_h_idx = int(son_h_idx) if son_h_idx is not None else 1
                            arama_notu_ekle(safe_ogrenci_id, target_h_idx, sonuc, not_metni, arayan)
                            st.success("Not başarıyla eklendi!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Form kaydı hatası: {e}")

# ===================================================================
# TAB 2: SCOREBOARD
# ===================================================================
with tab2:
    st.subheader("Scoreboard (Kümülatif)")
    siralanmis = metrikler.sort_values("toplam_puan", ascending=False).reset_index(drop=True)
    
    col_filter_1, col_filter_2 = st.columns([2, 1])
    with col_filter_1:
        arama_kw = st.text_input("🔍 Öğrenci Ara (İsim veya ID)", placeholder="Örn: Yağmur veya 734")
    with col_filter_2:
        secilen_sinif_filtre = st.selectbox("🎓 Sınıf Filtresi", options=SINIF_SEVIYELERI)

    if arama_kw:
        siralanmis = siralanmis[
            siralanmis["ad_soyad"].str.contains(arama_kw, case=False, na=False) |
            siralanmis["ogrenci_id"].astype(str).str.contains(arama_kw, na=False)
        ]
        
    if secilen_sinif_filtre != "Tüm Sınıflar" and "sinif_seviyesi" in siralanmis.columns:
        siralanmis = siralanmis[siralanmis["sinif_seviyesi"] == secilen_sinif_filtre]

    sb_cols = ["ogrenci_id", "ad_soyad", "sinif_seviyesi", "temel_puan", "momentum_bonusu", "toplam_puan"]
    sb_cols_existing = [c for c in sb_cols if c in siralanmis.columns]

    st.dataframe(
        siralanmis[sb_cols_existing], 
        use_container_width=True, 
        hide_index=True
    )

    st.write("")
    col_sb_csv, col_sb_excel = st.columns(2)
    with col_sb_csv:
        csv_sb = siralanmis.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📄 Scoreboard'u CSV İndir",
            data=csv_sb,
            file_name="scoreboard_tum_liste.csv",
            mime="text/csv",
            use_container_width=True
        )
    with col_sb_excel:
        buffer_sb = io.BytesIO()
        with pd.ExcelWriter(buffer_sb, engine='openpyxl') as writer:
            siralanmis.to_excel(writer, index=False, sheet_name='Scoreboard')
        st.download_button(
            label="📊 Scoreboard'u Excel İndir",
            data=buffer_sb.getvalue(),
            file_name="scoreboard_tum_liste.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

# ===================================================================
# TAB 3: AI ANALİST RAPORU
# ===================================================================
with tab3:
    st.subheader("🤖 AI Analist Raporu")
    avg_puan = metrikler["toplam_puan"].mean() if not metrikler.empty else 0
    st.write(genel_yorum_uret(avg_puan))

# ===================================================================
# TAB 4: ÖĞRENCİ PROFİL VE GEÇMİŞİ
# ===================================================================
with tab4:
    st.subheader("👤 Öğrenci Profili ve Detaylı Geçmişi")
    
    def format_ogrenci_label(r):
        sinif = f" - {r['sinif_seviyesi']}" if pd.notna(r.get('sinif_seviyesi')) and r.get('sinif_seviyesi') else ""
        return f"{r['ad_soyad']}{sinif} (ID {r['ogrenci_id']})"

    secim_listesi = df_ogrenciler.apply(format_ogrenci_label, axis=1).tolist() if not df_ogrenciler.empty else []
    
    if secim_listesi:
        secilen = st.selectbox("Öğrenci Seçin", secim_listesi)
        secilen_id = int(secilen.split("ID ")[1].rstrip(")"))
        
        st.write("---")
        st.markdown("### 📝 Deneme Kayıtları")
        
        ogrenci_kayitlari = df_kayitlar[df_kayitlar["ogrenci_id"] == secilen_id].copy()
        
        if ogrenci_kayitlari.empty:
            st.info("Bu öğrenciye ait deneme kaydı bulunamadı.")
        else:
            mevcut_aylar = [ay for ay in AYLAR if ay in ogrenci_kayitlari["ay_adi"].unique()]
            ay_filtre_secenekleri = ["Tüm Aylar"] + mevcut_aylar
            
            secilen_ay_f = st.selectbox("📅 İncelemek İstediğiniz Ayı Seçin", ay_filtre_secenekleri)
            
            if secilen_ay_f != "Tüm Aylar":
                filtreli_kayitlar = ogrenci_kayitlari[ogrenci_kayitlari["ay_adi"] == secilen_ay_f].copy()
            else:
                filtreli_kayitlar = ogrenci_kayitlari.copy()
            
            filtreli_kayitlar["ay_sira"] = filtreli_kayitlar["ay_adi"].apply(lambda x: AYLAR.index(x) if x in AYLAR else 99)
            filtreli_kayitlar = filtreli_kayitlar.sort_values(by=["ay_sira", "deneme_no"])

            if filtreli_kayitlar.empty:
                st.warning(f"{secilen_ay_f} ayına ait deneme kaydı yok.")
            else:
                with st.container(height=350):
                    for idx, r in filtreli_kayitlar.iterrows():
                        col_ay, col_deneme, col_durum, col_sil = st.columns([2, 2, 3, 1], vertical_alignment="center")
                        
                        col_ay.markdown(f"**Ay:** {r['ay_adi']}")
                        col_deneme.markdown(f"**Deneme No:** {r['deneme_no']}")
                        col_durum.markdown(f"**Durum:** {r['durum']}")
                        
                        if col_sil.button("🗑️ Sil", key=f"del_deneme_{r['id']}"):
                            deneme_kaydi_sil(r['id'])
                            st.success("Deneme kaydı silindi.")
                            st.rerun()

        st.markdown("---")
        st.markdown("### 📞 Bu Öğrenciye Ait Arama Kayıtları (Tarihli)")
        if not df_aramalar.empty:
            o_aramalar = df_aramalar[df_aramalar["ogrenci_id"] == secilen_id].copy()
            if not o_aramalar.empty:
                o_aramalar["kayit_zamani"] = pd.to_datetime(o_aramalar["kayit_zamani"]).dt.strftime("%d.%m.%Y %H:%M")
                o_aramalar = o_aramalar.rename(columns={
                    "kayit_zamani": "Tarih & Saat",
                    "hafta_index": "Hafta",
                    "arayan": "Arayan Kişi",
                    "arama_sonucu": "Sonuç",
                    "not_metni": "Görüşme Notu"
                })
                st.dataframe(
                    o_aramalar[["Tarih & Saat", "Hafta", "Arayan Kişi", "Sonuç", "Görüşme Notu"]], 
                    use_container_width=True, 
                    hide_index=True,
                    height=250
                )
            else:
                st.info("Bu öğrenci için henüz yapılmış bir arama kaydı yok.")
        else:
            st.info("Sistemde henüz arama kaydı bulunmuyor.")

# ===================================================================
# TAB 5: TÜM ARAMA GEÇMİŞİ
# ===================================================================
with tab5:
    st.subheader("🗂️ Tüm Arama Geçmişi")
    if not df_aramalar.empty:
        birlesik = df_aramalar.merge(df_ogrenciler, on="ogrenci_id", how="left")
        birlesik["kayit_zamani"] = pd.to_datetime(birlesik["kayit_zamani"]).dt.strftime("%d.%m.%Y %H:%M")
        
        sutun_haritasi = {
            "kayit_zamani": "Kayıt Tarihi",
            "hafta_index": "Hafta",
            "ogrenci_id": "Öğrenci ID",
            "ad_soyad": "Ad Soyad",
            "sinif_seviyesi": "Sınıf Seviyesi",
            "telefon": "Telefon",
            "arayan": "Arayan",
            "arama_sonucu": "Arama Sonucu",
            "not_metni": "Arama Notu"
        }
        
        mevcut_sutunlar = [col for col in sutun_haritasi.keys() if col in birlesik.columns]
        gosterilecek_df = birlesik[mevcut_sutunlar].rename(columns=sutun_haritasi)
        
        st.dataframe(gosterilecek_df, use_container_width=True, hide_index=True)
    else:
        st.info("Arama kaydı bulunamadı.")
