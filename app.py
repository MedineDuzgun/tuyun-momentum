import io
import urllib.parse
import pandas as pd
import streamlit as st

from db_operations import (
    arama_listesi_hesapla,
    arama_notu_ekle,
    cift_excel_islem_ve_yukle,
    deneme_kaydi_sil,
    init_db,
    ogrenci_metriklerini_hesapla,
    son_yuklemeyi_sil,
    tum_veriyi_oku,
    veritabanini_sifirla,
)
from utils import (
    ARAMA_SONUCU_SECENEKLERI,
    AYLAR,
    SINIF_SEVIYELERI,
    genel_yorum_uret,
    whatsapp_mesaji_olustur,
)

# ===================================================================
# STREAMLIT ARAYÜZÜ
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

    # --- YANLIŞ YÜKLEME DÜZELTME OPERASYONU ---
    if not df_kayitlar_ozet.empty:
        max_h = df_kayitlar_ozet["hafta_index"].max()
        son_deneme_row = df_kayitlar_ozet[df_kayitlar_ozet["hafta_index"] == max_h].iloc[0]
        son_deneme_tanim = f"{son_deneme_row['ay_adi']} - Deneme {son_deneme_row['deneme_no']} (Index: {max_h})"
        
        st.markdown(f"**Son Yüklenen:** {son_deneme_tanim}")
        if st.button("↩️ Son Yüklenen Denemeyi Sil (Geri Al)", type="secondary", help="En son yüklediğiniz deneme kaydını tamamen veritabanından siler."):
            ok, msg = son_yuklemeyi_sil()
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
        st.caption("Yanlış dosya veya ay seçimiyle yükleme yaptığınızda yukarıdaki butonla geri alabilirsiniz.")
        st.divider()

    onay = st.checkbox("Tüm verileri silmeyi onaylıyorum")
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
