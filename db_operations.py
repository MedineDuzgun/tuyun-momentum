from datetime import datetime
import pandas as pd
import psycopg2
from psycopg2.extras import execute_batch
import streamlit as st

from utils import MOMENTUM_BONUS, PUAN_TABLOSU, normalize_excel


# ===================================================================
# VERİTABANI BAĞLANTI VE CRUD İŞLEMLERİ
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

def son_yuklemeyi_sil() -> tuple[bool, str]:
    """
    Sistemdeki en son yüklenen hafta_index'e ait tüm deneme kayıtlarını siler.
    Eğer silinen deneme 'Ay Sonu Denemesi' olarak işaretlendiyse, o aya ait kalan
    kayıtların is_ay_sonu bayrağını da otomatik olarak FALSE yaparak bonusu geri alır.
    """
    conn = get_conn()
    if not conn:
        return False, "Veritabanı bağlantısı kurulamadı."
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(hafta_index) FROM deneme_kayitlari")
            max_h_idx = cur.fetchone()[0]

            if max_h_idx is None:
                return False, "Silinecek deneme kaydı bulunamadı."

            # Silinecek denemenin ay, deneme no ve ay sonu durumunu öğren
            cur.execute(
                "SELECT ay_adi, deneme_no, is_ay_sonu FROM deneme_kayitlari WHERE hafta_index = %s LIMIT 1", 
                (max_h_idx,)
            )
            row = cur.fetchone()
            
            ay_adi = row[0] if row else None
            deneme_no = row[1] if row else None
            was_ay_sonu = row[2] if row else False
            ay_bilgisi = f"{ay_adi} - Deneme {deneme_no}" if row else f"Hafta {max_h_idx}"

            # 1. En son yüklenen haftaya ait deneme kayıtlarını sil
            cur.execute("DELETE FROM deneme_kayitlari WHERE hafta_index = %s", (max_h_idx,))
            
            # 2. En son yüklenen haftaya yazılmış arama notları varsa temizle
            cur.execute("DELETE FROM arama_notlari WHERE hafta_index = %s", (max_h_idx,))

            # 3. Eğer silinen deneme bir 'Ay Sonu' denemesiyse, o ayın veritabanında kalan
            #    diğer kayıtlarındaki is_ay_sonu bayrağını da FALSE yap (Momentum bonusunu iptal et)
            if was_ay_sonu and ay_adi:
                cur.execute("UPDATE deneme_kayitlari SET is_ay_sonu = FALSE WHERE ay_adi = %s", (ay_adi,))

            conn.commit()
            st.cache_data.clear()
            return True, f"Son yüklenen '{ay_bilgisi}' (Index: {max_h_idx}) silindi ve ay kapanış bonusu (Momentum) otomatik geri alındı."
            
    except Exception as e:
        conn.rollback()
        return False, f"Silme işleminde hata oluştu: {e}"
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
# İŞ MANTIĞI HESAPLAMALARI
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
