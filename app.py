"""
tuyun_momentum_app.py
-----------------------
Tuyun Akademi - Tuyun Momentum Sistemi (Sabit SHA-256 ID & Dinamik Deneme Sayısı Sürümü)
"""

import psycopg2
from datetime import datetime
import pandas as pd
import streamlit as st
import hashlib

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

# Ocak'tan başlayıp Aralık'a kadar 12 ayın tamamı sıralı
AYLAR = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"
]

ARAMA_SONUCU_SECENEKLERI = [
    "Ulaşıldı - Olumlu / Devam Ediyor",
    "Ulaşıldı - Engel Var (görüşme planlandı)",
    "Ulaşılamadı - Tekrar Denenecek",
    "Aranmadı",
]

# ===================================================================
# 2) DİNAMİK EXCEL TEMİZLEME VE MÜKERRER KAYIT ENGELLEME
# ===================================================================
def normalize_excel(uploaded_file) -> pd.DataFrame:
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

    # İsmi standartlaştırarak Türkçe karakter uyuşmazlığından mükerrer kişi oluşmasını engelliyoruz
    df["Ad_Soyad"] = df["Ad_Soyad"].astype(str).str.replace("i̇", "i").str.replace("I", "ı").str.strip().str.title()

    def parse_id(row):
        val = str(row["Ogrenci_ID"]).split(".")[0].strip()
        # DEĞİŞİKLİK 1: Python'ın değişken hash() fonksiyonu yerine Sabit/Kararlı SHA-256 ID üretimi
        if val in ["0", "nan", "None", "", "null"]:
            name_clean = str(row["Ad_Soyad"]).strip().lower()
            hash_val = hashlib.sha256(name_clean.encode('utf-8')).hexdigest()
            return int(hash_val[:8], 16) % 1000000
        try:
            return int(val)
        except:
            name_clean = str(row["Ad_Soyad"]).strip().lower()
            hash_val = hashlib.sha256(name_clean.encode('utf-8')).hexdigest()
            return int(hash_val[:8], 16) % 1000000

    df["Ogrenci_ID"] = df.apply(parse_id, axis=1)

    if "Telefon" not in df.columns:
        df["Telefon"] = ""

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
                        kayit_tarihi TEXT
                    );
                    
                    CREATE TABLE IF NOT EXISTS deneme_kayitlari (
                        id SERIAL PRIMARY KEY,
                        ogrenci_id BIGINT NOT NULL REFERENCES ogrenciler(ogrenci_id) ON DELETE CASCADE,
                        ay_adi TEXT NOT NULL,
                        deneme_no INT NOT NULL,
                        hafta_index INT NOT NULL,
                        durum TEXT NOT NULL,
                        yukleme_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(ogrenci_id, ay_adi, deneme_no)
                    );
                    
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
                
                # Tablo sütun güncellemeleri
                cur.execute("ALTER TABLE arama_notlari ADD COLUMN IF NOT EXISTS arayan TEXT;")
                cur.execute("ALTER TABLE arama_notlari ADD COLUMN IF NOT EXISTS hafta_index INT DEFAULT 1;")
                cur.execute("ALTER TABLE deneme_kayitlari ADD COLUMN IF NOT EXISTS ay_adi TEXT DEFAULT 'Ocak';")
                cur.execute("ALTER TABLE deneme_kayitlari ADD COLUMN IF NOT EXISTS deneme_no INT DEFAULT 1;")
                
                # Postgres kısıtlama esnetmeleri (NotNullViolation engeli için)
                cur.execute("ALTER TABLE arama_notlari ALTER COLUMN arama_sonucu DROP NOT NULL;")
                cur.execute("ALTER TABLE arama_notlari ALTER COLUMN hafta_index DROP NOT NULL;")
                cur.execute("ALTER TABLE arama_notlari ALTER COLUMN not_metni DROP NOT NULL;")
                cur.execute("ALTER TABLE arama_notlari ALTER COLUMN arayan DROP NOT NULL;")
                
                conn.commit()
        except Exception as e:
            conn.rollback()
            st.error(f"Tablo kontrol hatası: {e}")
        finally:
            conn.close()

def ogrencileri_kaydet(conn, df_ogrenci: pd.DataFrame) -> None:
    with conn.cursor() as cur:
        # DEĞİŞİKLİK 2A: Veritabanında ismi olan öğrencinin ID'sini koru
        cur.execute("SELECT ad_soyad, ogrenci_id FROM ogrenciler;")
        mevcut_ogrenciler = {row[0].strip().lower(): row[1] for row in cur.fetchall()}

        for _, r in df_ogrenci.iterrows():
            ad = str(r["Ad_Soyad"]).strip()
            ad_key = ad.lower()
            
            if ad_key in mevcut_ogrenciler:
                o_id = mevcut_ogrenciler[ad_key]
            else:
                o_id = int(r["Ogrenci_ID"])

            cur.execute(
                """INSERT INTO ogrenciler (ogrenci_id, ad_soyad, telefon, kayit_tarihi)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT(ogrenci_id) DO UPDATE SET
                       ad_soyad=EXCLUDED.ad_soyad,
                       telefon=EXCLUDED.telefon,
                       kayit_tarihi=EXCLUDED.kayit_tarihi""",
                (
                    o_id,
                    ad,
                    str(r.get("Telefon", "")),
                    datetime.now().strftime("%Y-%m-%d"),
                ),
            )

def deneme_kayit_ekle(conn, ogrenci_id: int, ay_adi: str, deneme_no: int, hafta_index: int, durum: str) -> None:
    durum_val = str(durum).strip() if durum else "Muaf"
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO deneme_kayitlari (ogrenci_id, ay_adi, deneme_no, hafta_index, durum, yukleme_zamani)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT(ogrenci_id, ay_adi, deneme_no) DO UPDATE SET
                   durum=EXCLUDED.durum,
                   hafta_index=EXCLUDED.hafta_index,
                   yukleme_zamani=EXCLUDED.yukleme_zamani""",
            (
                int(ogrenci_id),
                ay_adi,
                int(deneme_no),
                int(hafta_index),
                durum_val,
                datetime.now(),
            ),
        )

def arama_notu_ekle(ogrenci_id: int, hafta_index: int, sonuc: str, not_metni: str, arayan: str) -> None:
    conn = get_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                safe_id = int(ogrenci_id) if ogrenci_id is not None else 0
                
                try:
                    safe_h_idx = int(hafta_index) if hafta_index is not None else 1
                except (ValueError, TypeError):
                    safe_h_idx = 1
                
                safe_sonuc = str(sonuc).strip() if (sonuc and str(sonuc).strip()) else "Aranmadı"
                safe_not = str(not_metni).strip() if (not_metni and str(not_metni).strip()) else "Not girilmedi"
                safe_arayan = str(arayan).strip() if (arayan and str(arayan).strip()) else "Sistem / Belirtilmedi"

                cur.execute(
                    """INSERT INTO arama_notlari (ogrenci_id, hafta_index, arama_sonucu, not_metni, arayan, kayit_zamani)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (safe_id, safe_h_idx, safe_sonuc, safe_not, safe_arayan, datetime.now()),
                )
                conn.commit()
        except Exception as e:
            conn.rollback()
            st.error(f"Arama notu kaydedilirken hata oluştu: {e}")
        finally:
            conn.close()

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

def cift_excel_islem_ve_yukle(file_cozenler, file_cozmeyenler, ay_adi: str, deneme_no: int) -> int:
    conn = get_conn()
    if not conn:
        return 0

    df_cozenler = normalize_excel(file_cozenler)
    if not df_cozenler.empty:
        df_cozenler["Durum"] = "Vaktinde Çözdü"

    df_cozmeyenler = normalize_excel(file_cozmeyenler)
    if not df_cozmeyenler.empty:
        df_cozmeyenler["Durum"] = "Çözmedi"

    df_birlesik = pd.concat([df_cozenler, df_cozmeyenler], ignore_index=True)

    if df_birlesik.empty:
        conn.close()
        return 0

    ogrencileri_kaydet(conn, df_birlesik)
    h_idx = sonraki_hafta_index()

    # DEĞİŞİKLİK 2B: Deneme kaydı yaparken DB'deki gerçek/sabitleşmiş ID'yi çekip kaydet
    for _, r in df_birlesik.iterrows():
        cur = conn.cursor()
        cur.execute("SELECT ogrenci_id FROM ogrenciler WHERE LOWER(ad_soyad) = %s", (str(r["Ad_Soyad"]).strip().lower(),))
        fetched = cur.fetchone()
        real_id = fetched[0] if fetched else int(r["Ogrenci_ID"])
        cur.close()

        deneme_kayit_ekle(conn, real_id, ay_adi, deneme_no, h_idx, r["Durum"])

    conn.commit()
    conn.close()
    return len(df_birlesik)

def veritabanini_sifirla() -> None:
    conn = get_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE arama_notlari, deneme_kayitlari, ogrenciler RESTART IDENTITY CASCADE;")
            conn.commit()
        conn.close()

# ===================================================================
# 4) İŞ MANTIĞI HESAPLAMALARI
# ===================================================================
def ogrenci_metriklerini_hesapla(df_kayitlar: pd.DataFrame) -> pd.DataFrame:
    if df_kayitlar.empty:
        return pd.DataFrame(columns=["ogrenci_id", "temel_puan", "momentum_bonusu", "toplam_puan"])

    df = df_kayitlar.copy()
    df["puan"] = df["durum"].map(PUAN_TABLOSU).fillna(0.0)
    temel = df.groupby("ogrenci_id")["puan"].sum().rename("temel_puan")

    # Aylık girilen tüm denemeleri (en az 4 tane olmak şartıyla) Vaktinde Çözene Momentum Bonusu (+0.5)
    momentum_kayitlari = []
    for ogrenci_id, grup in df.groupby("ogrenci_id"):
        toplam_bonus = 0.0
        for ay, ay_grubu in grup.groupby("ay_adi"):
            if len(ay_grubu) >= 4 and all(ay_grubu["durum"] == "Vaktinde Çözdü"):
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

    # Her öğrenci için zamandizisel analiz yapıyoruz
    for o_id, grup in df_kayitlar.groupby("ogrenci_id"):
        grup_sirali = grup.sort_values("hafta_index")
        haftalar = grup_sirali["hafta_index"].tolist()
        durumlar = grup_sirali["durum"].tolist()
        
        # 1. En son yapılan arama haftasını tespit et
        son_arama_hafta = 0
        if not df_aramalar.empty:
            col_check = "hafta_index" if "hafta_index" in df_aramalar.columns else "hafta_no"
            ogrenci_aramalari = df_aramalar[
                (df_aramalar["ogrenci_id"] == o_id) & 
                (df_aramalar["arama_sonucu"] != "Aranmadı")
            ]
            if not ogrenci_aramalari.empty:
                son_arama_hafta = int(ogrenci_aramalari[col_check].max())

        # 2. Son aramadan SONRA en az 2 deneme daha üst üste "Çözmedi" durumunda mı?
        for i in range(len(durumlar) - 1):
            h1, h2 = haftalar[i], haftalar[i+1]
            d1, d2 = durumlar[i], durumlar[i+1]
            
            if h1 > son_arama_hafta and h2 > son_arama_hafta:
                if d1 == "Çözmedi" and d2 == "Çözmedi":
                    riskli_ogrenciler.append(o_id)
                    break
            elif son_arama_hafta == 0:  # Hiç aranmamışsa ve geçmişte 2 hafta çözemediyse
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
# 5) STREAMLIT ARAYÜZÜ (TAM 5 TAB)
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
    
    # Sınırsız / Esnek Deneme Sayısı Girişi
    deneme_no = st.number_input("Ayın Kaçıncı Denemesi?", min_value=1, max_value=20, value=1, step=1)

    if st.button("✅ İki Listeyi İşle ve Kaydet", type="primary"):
        if file_cozenler is None and file_cozmeyenler is None:
            st.error("Lütfen en az bir Excel dosyası yükleyin.")
        else:
            toplam_kayit = cift_excel_islem_ve_yukle(file_cozenler, file_cozmeyenler, secilen_ay, int(deneme_no))
            st.cache_data.clear()
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
            st.cache_data.clear()
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

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📞 Arama Listesi", "🏆 Scoreboard", "🤖 AI Analist Raporu", "📈 Öğrenci Geçmişi", "🗂️ Arama Geçmişi"])

with tab1:
    st.subheader("Operasyonel Arama Listesi (2 Hafta Üst Üste Çözmeyenler)")
    if arama_listesi.empty:
        st.success("Risk altında öğrenci yok. ✅")
    else:
        goster = arama_listesi[["ogrenci_id", "ad_soyad", "telefon", "onceki_durum", "son_durum"]]
        st.dataframe(goster, use_container_width=True, hide_index=True)

        for _, r in arama_listesi.iterrows():
            with st.expander(f"{r['ad_soyad']} (ID {r['ogrenci_id']})"):
                st.text_area("WhatsApp Mesajı", value=whatsapp_mesaji_olustur(r["ad_soyad"]), height=100, key=f"msg_{r['ogrenci_id']}")
                with st.form(key=f"form_{r['ogrenci_id']}"):
                    sonuc = st.selectbox("Sonuç", ARAMA_SONUCU_SECENEKLERI, key=f"res_{r['ogrenci_id']}")
                    not_metni = st.text_area("Not", key=f"note_{r['ogrenci_id']}")
                    arayan = st.text_input("Arayan", key=f"who_{r['ogrenci_id']}")
                    
                    if st.form_submit_button("Kaydet"):
                        try:
                            safe_ogrenci_id = int(r["ogrenci_id"])
                            target_h_idx = int(son_h_idx) if son_h_idx is not None else 1
                            arama_notu_ekle(safe_ogrenci_id, target_h_idx, sonuc, not_metni, arayan)
                            st.cache_data.clear()
                            st.success("Not başarıyla eklendi!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Form kaydı hatası: {e}")

with tab2:
    st.subheader("Scoreboard (Kümülatif)")
    siralanmis = metrikler.sort_values("toplam_puan", ascending=False).reset_index(drop=True)
    st.dataframe(siralanmis[["ogrenci_id", "ad_soyad", "temel_puan", "momentum_bonusu", "toplam_puan"]], use_container_width=True, hide_index=True)

with tab3:
    st.subheader("🤖 AI Analist Raporu")
    avg_puan = metrikler["toplam_puan"].mean() if not metrikler.empty else 0
    st.write(genel_yorum_uret(avg_puan))

with tab4:
    st.subheader("📈 Öğrenci Geçmişi")
    secim_listesi = df_ogrenciler.apply(lambda r: f"{r['ad_soyad']} (ID {r['ogrenci_id']})", axis=1).tolist()
    if secim_listesi:
        secilen = st.selectbox("Öğrenci Seçin", secim_listesi)
        secilen_id = int(secilen.split("ID ")[1].rstrip(")"))
        ogrenci_kayitlari = df_kayitlar[df_kayitlar["ogrenci_id"] == secilen_id].sort_values("hafta_index")
        st.dataframe(ogrenci_kayitlari[["ay_adi", "deneme_no", "durum"]], use_container_width=True, hide_index=True)

with tab5:
    st.subheader("🗂️ Tüm Arama Geçmişi")
    if not df_aramalar.empty:
        birlesik = df_aramalar.merge(df_ogrenciler, on="ogrenci_id", how="left")
        
        # 1. Kayıt Zamanını Formatlama (GG.AA.YYYY SS:DK)
        birlesik["kayit_zamani"] = pd.to_datetime(birlesik["kayit_zamani"]).dt.strftime("%d.%m.%Y %H:%M")
        
        # 2. Gösterilecek Sütunların Düzenli ve Türkçe İsmi
        sutun_haritasi = {
            "kayit_zamani": "Kayıt Tarihi",
            "hafta_index": "Hafta",
            "ogrenci_id": "Öğrenci ID",
            "ad_soyad": "Ad Soyad",
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
