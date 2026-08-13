%%writefile app.py
from datetime import datetime
import pandas as pd
import streamlit as st

# Modüllerden içe aktarmalar
from db_operations import (
    init_db,
    get_conn,
    ogrencileri_kaydet,
    arama_notu_ekle,
    tum_veriyi_oku,
    veritabanini_sifirla,
)
from utils import (
    PUAN_TABLOSU,
    AYLAR,
    SINIF_SEVIYELERI,
    ARAMA_SONUCU_SECENEKLERI,
    normalize_excel_pair,
    ogrenci_metriklerini_hesapla,
    arama_listesi_hesapla,
    whatsapp_mesaji_olustur,
)

# 1) UYGULAMA YAPILANDIRMASI
st.set_page_config(
    page_title="Tuyun Momentum Sistemi",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Veritabanını başlat
init_db()

# 2) SOL MENÜ VE FİLTRELER
st.sidebar.title("🌱 Tuyun Momentum")
st.sidebar.markdown("---")

secilen_sinif = st.sidebar.selectbox("Sınıf Seviyesi Filtresi", SINIF_SEVIYELERI)

# Veriyi oku
df_ogrenciler, df_kayitlar, df_aramalar = tum_veriyi_oku()

# Sınıf Filtresi Uygula
if secilen_sinif != "Tüm Sınıflar" and not df_ogrenciler.empty:
    df_ogrenciler = df_ogrenciler[df_ogrenciler["sinif_seviyesi"] == secilen_sinif]
    if not df_kayitlar.empty:
        df_kayitlar = df_kayitlar[df_kayitlar["ogrenci_id"].isin(df_ogrenciler["ogrenci_id"])]
    if not df_aramalar.empty:
        df_aramalar = df_aramalar[df_aramalar["ogrenci_id"].isin(df_ogrenciler["ogrenci_id"])]

# 3) ANA SAYFA SEKMELERİ
tab1, tab2, tab3, tab4 = st.tabs([
    "📥 Veri Yükleme", 
    "📊 Genel Tablo & Skorlar", 
    "📞 Arama / Müdahale Listesi", 
    "⚙️ Yönetim & Ayarlar"
])

# TAB 1: VERİ YÜKLEME (TAM KADRO PARALLEL TASARIM)
with tab1:
    st.header("📁 Haftalık Veri Yükleme")
    
    # 1 ve 2. Dosya Yükleme
    cozenler_file = st.file_uploader("1. Çözenler Listesi (.xlsx)", type=["xlsx", "xls"], key="cozenler")
    cozmeyenler_file = st.file_uploader("2. Çözmeyenler Listesi (.xlsx)", type=["xlsx", "xls"], key="cozmeyenler")
    
    st.markdown("---")
    
    # Tüm Parametre Alanları (Ay, Sınıf, Hafta No, Ay Sonu Onayı)
    col_p1, col_p2 = st.columns(2)
    
    with col_p1:
        secilen_ay = st.selectbox("Ait Olduğu Ay", AYLAR)
        hafta_no = st.number_input("Bu veriler hangi hafta / deneme için?", min_value=1, value=1, step=1)
        
    with col_p2:
        yukleme_sinifi = st.selectbox("Yüklenen Sınıf Seviyesi", [s for s in SINIF_SEVIYELERI if s != "Tüm Sınıflar"])
        is_ay_sonu = st.checkbox("Bu deneme ayın son denemesidir (Momentum Bonusu Hesaplansın)")

    st.markdown("<br>", unsafe_allow_html=True)

    if st.button("✅ İki Listeyi İşle ve Kaydet", type="primary"):
        if cozenler_file is None and cozmeyenler_file is None:
            st.error("Lütfen en az bir dosya (Çözenler veya Çözmeyenler) yükleyin.")
        else:
            df_norm = normalize_excel_pair(cozenler_file, cozmeyenler_file, sinif_seviyesi=yukleme_sinifi)
            if df_norm.empty:
                st.error("Excel dosyalarından geçerli öğrenci verisi okunamadı.")
            else:
                conn = get_conn()
                if conn:
                    try:
                        ogrencileri_kaydet(conn, df_norm)
                        
                        kayitlar = []
                        for _, row in df_norm.iterrows():
                            kayitlar.append((
                                int(row["Ogrenci_ID"]),
                                secilen_ay,          # Seçtiğin Ay
                                int(hafta_no),       # Seçtiğin Hafta
                                int(hafta_no),       # Hafta Index
                                str(row["Durum"]),
                                is_ay_sonu           # Seçtiğin Ay Sonu Onayı (True/False)
                            ))
                            
                        with conn.cursor() as cur:
                            from psycopg2.extras import execute_batch
                            query = """
                                INSERT INTO deneme_kayitlari (ogrenci_id, ay_adi, deneme_no, hafta_index, durum, is_ay_sonu)
                                VALUES (%s, %s, %s, %s, %s, %s)
                                ON CONFLICT (ogrenci_id, ay_adi, deneme_no) DO UPDATE SET
                                    durum = EXCLUDED.durum,
                                    hafta_index = EXCLUDED.hafta_index,
                                    is_ay_sonu = EXCLUDED.is_ay_sonu;
                            """
                            execute_batch(cur, query, kayitlar)
                            conn.commit()
                        
                        st.success(f"✅ {len(df_norm)} öğrencinin verisi {secilen_ay} ayı ({hafta_no}. hafta) için başarıyla kaydedildi!")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        conn.rollback()
                        st.error(f"Yükleme hatası: {e}")
                    finally:
                        conn.close()

    st.markdown("---")
    
    # Alt Metrikler
    m_col1, m_col2 = st.columns(2)
    toplam_ogrenci_sayisi = len(df_ogrenciler) if not df_ogrenciler.empty else 0
    toplam_hafta_sayisi = df_kayitlar["hafta_index"].nunique() if not df_kayitlar.empty else 0
    
    with m_col1:
        st.metric("Kayıtlı Öğrenci", toplam_ogrenci_sayisi)
    with m_col2:
        st.metric("İşlenmiş Hafta Sayısı", toplam_hafta_sayisi)

# TAB 2: GENEL TABLO & SKORLAR
with tab2:
    st.header("Öğrenci Momentum Skorları")
    
    if df_ogrenciler.empty or df_kayitlar.empty:
        st.info("Henüz veritabanında yüklenmiş deneme kaydı bulunmuyor.")
    else:
        df_metrikler = ogrenci_metriklerini_hesapla(df_kayitlar)
        df_ozet = df_ogrenciler.merge(df_metrikler, on="ogrenci_id", how="left").fillna(0.0)
        
        st.dataframe(
            df_ozet[["ogrenci_id", "ad_soyad", "sinif_seviyesi", "temel_puan", "momentum_bonusu", "toplam_puan"]]
            .sort_values(by="toplam_puan", ascending=False),
            use_container_width=True
        )

# TAB 3: ARAMA / MÜDAHALE LİSTESİ
with tab3:
    st.header("📞 Arama & Müdahale Listesi (Üst Üste 2 Hafta Çözmeyenler)")
    
    if df_kayitlar.empty:
        st.info("Kayıtlı deneme bulunmadığı için liste oluşturulamıyor.")
    else:
        risk_df, son_h, onceki_h = arama_listesi_hesapla(df_kayitlar, df_aramalar)
        
        if risk_df.empty:
            st.success("🎉 Harika! Üst üste 2 hafta çözmeyen ve aranması gereken öğrenci bulunmuyor.")
        else:
            risk_full = risk_df.merge(df_ogrenciler, on="ogrenci_id", how="inner")
            st.warning(f"⚠️ Toplam {len(risk_full)} öğrenci takibe takıldı.")
            
            for _, row in risk_full.iterrows():
                with st.expander(f"👤 {row['ad_soyad']} ({row.get('sinif_seviyesi', 'Belirtilmedi')})"):
                    st.write(f"**Telefon:** {row.get('telefon', 'Yok')}")
                    st.write(f"**Son Durumlar:** {onceki_h}. Hafta ({row['onceki_durum']}) | {son_h}. Hafta ({row['son_durum']})")
                    
                    wa_msg = whatsapp_mesaji_olustur(row['ad_soyad'])
                    st.text_area("WhatsApp Mesaj Taslağı", wa_msg, height=100, key=f"wa_{row['ogrenci_id']}")
                    
                    col_a1, col_a2 = st.columns(2)
                    with col_a1:
                        arama_sonucu = st.selectbox(
                            "Arama Sonucu", 
                            ARAMA_SONUCU_SECENEKLERI, 
                            key=f"sec_{row['ogrenci_id']}"
                        )
                    with col_a2:
                        arayan_kisi = st.text_input("Arayan Kişi", value="Rehberlik", key=f"arayan_{row['ogrenci_id']}")
                    
                    not_metni = st.text_input("Arama Notu", key=f"not_{row['ogrenci_id']}")
                    
                    if st.button("Aramayı Kaydet", key=f"btn_{row['ogrenci_id']}"):
                        arama_notu_ekle(
                            ogrenci_id=row['ogrenci_id'],
                            hafta_index=son_h,
                            sonuc=arama_sonucu,
                            not_metni=not_metni,
                            arayan=arayan_kisi
                        )
                        st.success("Arama notu başarıyla kaydedildi!")
                        st.rerun()

# TAB 4: YÖNETİM & AYARLAR
with tab4:
    st.header("⚙️ Sistem Yönetimi")
    st.warning("🚨 Bu alandaki işlemler geri alınamaz!")
    
    if st.button("🗑️ TÜM VERİTABANINI SIFIRLA", type="primary"):
        veritabanini_sifirla()
        st.success("Veritabanı sıfırlandı!")
        st.rerun()
