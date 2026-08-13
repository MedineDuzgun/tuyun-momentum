import psycopg2
from psycopg2.extras import execute_batch
from datetime import datetime
import pandas as pd
import streamlit as st

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

def veritabanini_sifirla() -> None:
    conn = get_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE arama_notlari, deneme_kayitlari, ogrenciler RESTART IDENTITY CASCADE;")
            conn.commit()
        conn.close()
        st.cache_data.clear()
