import streamlit as st
import pandas as pd
import psycopg2
from datetime import datetime

# Sayfa Ayarları
st.set_page_config(page_title="Tuyun Momentum Sistemi", page_icon="🎯", layout="wide")

# Veritabanı Bağlantısı (Supabase PostgreSQL)
def get_connection():
    try:
        conn = psycopg2.connect(st.secrets["postgres"]["url"])
        return conn
    except Exception as e:
        st.error(f"Veritabanı bağlantı hatası: {e}")
        return None

# Tabloları Oluşturma
def init_db():
    conn = get_connection()
    if conn:
        with conn.cursor() as cur:
            # Ogrenciler Tablosu
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ogrenciler (
                    ogrenci_id SERIAL PRIMARY KEY,
                    isim TEXT UNIQUE NOT NULL
                );
            """)
            # Haftalik Veriler Tablosu
            cur.execute("""
                CREATE TABLE IF NOT EXISTS haftalik_veriler (
                    veri_id SERIAL PRIMARY KEY,
                    ogrenci_id INT REFERENCES ogrenciler(ogrenci_id) ON DELETE CASCADE,
                    hafta INT NOT NULL,
                    cozdu_mu BOOLEAN NOT NULL,
                    CONSTRAINT unique_ogrenci_hafta UNIQUE(ogrenci_id, hafta)
                );
            """)
            # Aramalar Tablosu
            cur.execute("""
                CREATE TABLE IF NOT EXISTS aramalar (
                    arama_id SERIAL PRIMARY KEY,
                    ogrenci_id INT REFERENCES ogrenciler(ogrenci_id) ON DELETE CASCADE,
                    hafta INT NOT NULL,
                    durum TEXT DEFAULT 'Aranmadı',
                    notlar TEXT DEFAULT '',
                    arama_tarihi TIMESTAMP,
                    CONSTRAINT unique_arama_hafta UNIQUE(ogrenci_id, hafta)
                );
            """)
            conn.commit()
        conn.close()

init_db()

# Başlık
st.title("🎯 Tuyun Momentum Sistemi")
st.caption("Veritabanı: Supabase Cloud (Kalıcı Bulut Veritabanı)")

# Sol Panel (Sidebar)
st.sidebar.header("📁 Haftalık Veri Yükleme")

cozenler_file = st.sidebar.file_uploader("1. Çözenler Listesi (.xlsx)", type=["xlsx", "xls"], key="cozenler")
cozmeyenler_file = st.sidebar.file_uploader("2. Çözmeyenler Listesi (.xlsx)", type=["xlsx", "xls"], key="cozmeyenler")

hafta_numarasi = st.sidebar.number_input("Bu veriler hangi hafta için?", min_value=1, max_value=52, value=1, step=1)

def verileri_isle(cozen_df, cozmeyen_df, hafta):
    conn = get_connection()
    if not conn:
        return
    
    with conn.cursor() as cur:
        # Çözenleri Ekle/Güncelle
        if cozen_df is not None:
            for _, row in cozen_df.iterrows():
                isim = str(row.iloc[0]).strip()
                if isim and isim != "nan":
                    cur.execute("INSERT INTO ogrenciler (isim) VALUES (%s) ON CONFLICT (isim) DO NOTHING;", (isim,))
                    cur.execute("SELECT ogrenci_id FROM ogrenciler WHERE isim = %s;", (isim,))
                    ogrenci_id = cur.fetchone()[0]
                    
                    cur.execute("""
                        INSERT INTO haftalik_veriler (ogrenci_id, hafta, cozdu_mu) 
                        VALUES (%s, %s, TRUE)
                        ON CONFLICT (ogrenci_id, hafta) DO UPDATE SET cozdu_mu = TRUE;
                    """, (ogrenci_id, hafta))
        
        # Çözmeyenleri Ekle/Güncelle
        if cozmeyen_df is not None:
            for _, row in cozmeyen_df.iterrows():
                isim = str(row.iloc[0]).strip()
                if isim and isim != "nan":
                    cur.execute("INSERT INTO ogrenciler (isim) VALUES (%s) ON CONFLICT (isim) DO NOTHING;", (isim,))
                    cur.execute("SELECT ogrenci_id FROM ogrenciler WHERE isim = %s;", (isim,))
                    ogrenci_id = cur.fetchone()[0]
                    
                    cur.execute("""
                        INSERT INTO haftalik_veriler (ogrenci_id, hafta, cozdu_mu) 
                        VALUES (%s, %s, FALSE)
                        ON CONFLICT (ogrenci_id, hafta) DO UPDATE SET cozdu_mu = FALSE;
                    """, (ogrenci_id, hafta))
                    
                    cur.execute("""
                        INSERT INTO aramalar (ogrenci_id, hafta, durum) 
                        VALUES (%s, %s, 'Aranmadı')
                        ON CONFLICT (ogrenci_id, hafta) DO NOTHING;
                    """, (ogrenci_id, hafta))
                    
        conn.commit()
    conn.close()

if st.sidebar.button("✅ İki Listeyi İşle ve Kaydet", type="primary"):
    if cozenler_file is None and cozmeyenler_file is None:
        st.sidebar.error("Lütfen en az bir dosya yükleyin!")
    else:
        try:
            cozen_df = pd.read_excel(cozenler_file) if cozenler_file else None
            cozmeyen_df = pd.read_excel(cozmeyenler_file) if cozmeyenler_file else None
            verileri_isle(cozen_df, cozmeyen_df, hafta_numarasi)
            st.sidebar.success(f"{hafta_numarasi}. Hafta verileri Supabase'e kalıcı olarak kaydedildi!")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Hata oluştu: {e}")

# İstatistikler (Sidebar)
conn = get_connection()
if conn:
    ogrenci_sayisi = pd.read_sql("SELECT COUNT(*) FROM ogrenciler;", conn).iloc[0, 0]
    hafta_sayisi = pd.read_sql("SELECT COUNT(DISTINCT hafta) FROM haftalik_veriler;", conn).iloc[0, 0]
    conn.close()
else:
    ogrenci_sayisi, hafta_sayisi = 0, 0

st.sidebar.markdown("---")
st.sidebar.metric("Kayıtlı Öğrenci", ogrenci_sayisi)
st.sidebar.metric("İşlenmiş Hafta Sayısı", hafta_sayisi)

# Sistem Yönetimi
st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ Sistem Yönetimi")
silme_onayi = st.sidebar.checkbox("Verileri silmeyi onaylıyorum")

if st.sidebar.button("🗑️ Veritabanını Sıfırla"):
    if silme_onayi:
        conn = get_connection()
        if conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE aramalar, haftalik_veriler, ogrenciler RESTART IDENTITY CASCADE;")
                conn.commit()
            conn.close()
            st.sidebar.success("Tüm veriler kalıcı olarak sıfırlandı!")
            st.rerun()
    else:
        st.sidebar.warning("Lütfen önce onay kutucuğunu işaretleyin!")

# Ana Ekran Sekmeleri
tab1, tab2 = st.tabs(["📊 Genel Takip Paneli", "📞 Arama Listesi (Çözmeyenler)"])

with tab1:
    st.subheader("Öğrenci Momentum ve Çözüm Durumu")
    conn = get_connection()
    if conn and ogrenci_sayisi > 0:
        query = """
            SELECT o.isim AS "Öğrenci Adı", hv.hafta, hv.cozdu_mu
            FROM ogrenciler o
            JOIN haftalik_veriler hv ON o.ogrenci_id = hv.ogrenci_id
            ORDER BY o.isim, hv.hafta;
        """
        df_raw = pd.read_sql(query, conn)
        conn.close()

        if not df_raw.empty:
            df_pivot = df_raw.pivot(index="Öğrenci Adı", columns="hafta", values="cozdu_mu")
            df_pivot.columns = [f"{c}. Hafta" for c in df_pivot.columns]
            
            # Görselleştirme (✅ / ❌)
            df_display = df_pivot.applymap(lambda x: "✅ Çözdü" if x == True else ("❌ Çözmedi" if x == False else "-"))
            st.dataframe(df_display, use_container_width=True)
        else:
            st.info("Henüz işlenmiş hafta verisi bulunmuyor.")
    else:
        if conn: conn.close()
        st.info("Henüz veri yüklenmedi. Başlamak için soldaki panelden Excel dosyalarını yükleyin.")

with tab2:
    st.subheader("Aranacak Öğrenciler Yönetimi")
    conn = get_connection()
    if conn and ogrenci_sayisi > 0:
        secili_hafta = st.selectbox("Arama listesi için hafta seçin:", list(range(1, 53)), index=hafta_numarasi-1)
        
        query = f"""
            SELECT a.arama_id, o.isim AS "Öğrenci Adı", a.durum AS "Arama Durumu", a.notlar AS "Notlar"
            FROM aramalar a
            JOIN ogrenciler o ON a.ogrenci_id = o.ogrenci_id
            WHERE a.hafta = {secili_hafta};
        """
        df_arama = pd.read_sql(query, conn)
        conn.close()

        if not df_arama.empty:
            edited_df = st.data_editor(
                df_arama[["Öğrenci Adı", "Arama Durumu", "Notlar"]],
                column_config={
                    "Arama Durumu": st.column_config.SelectboxColumn(
                        "Arama Durumu",
                        options=["Aranmadı", "Arandı - Ulaşıldı", "Arandı - Ulaşılamadı", "Mazeretli"],
                        required=True
                    )
                },
                use_container_width=True,
                key="arama_editor"
            )
            
            if st.button("💾 Arama Notlarını Kaydet"):
                conn = get_connection()
                if conn:
                    with conn.cursor() as cur:
                        for idx, row in edited_df.iterrows():
                            arama_id = int(df_arama.loc[idx, "arama_id"])
                            cur.execute("""
                                UPDATE aramalar 
                                SET durum = %s, notlar = %s, arama_tarihi = %s
                                WHERE arama_id = %s;
                            """, (row["Arama Durumu"], row["Notlar"], datetime.now(), arama_id))
                        conn.commit()
                    conn.close()
                    st.success("Arama kayıtları başarıyla Supabase'e güncellendi!")
        else:
            st.info(f"{secili_hafta}. Hafta için aranacak öğrenci kaydı bulunmuyor.")
    else:
        if conn: conn.close()
        st.info("Arama listesini görmek için önce haftalık veri yükleyin.")
