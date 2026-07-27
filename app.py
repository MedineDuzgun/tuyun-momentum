"""
tuyun_momentum_app.py
-----------------------
Tuyun Akademi - Tuyun Momentum Sistemi (Supabase Entegreli)
"""

import psycopg2
from datetime import datetime
import pandas as pd
import streamlit as st

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
MOMENTUM_PENCERE = 3

ARAMA_SONUCU_SECENEKLERI = [
    "Ulaşıldı - Olumlu / Devam Ediyor",
    "Ulaşıldı - Engel Var (görüşme planlandı)",
    "Ulaşılamadı - Tekrar Denenecek",
    "Aranmadı",
]


# ===================================================================
# 2) DİNAMİK EXCEL TEMİZLEME VE SÜTUN DÜZENLEME
# ===================================================================
def normalize_excel(uploaded_file) -> pd.DataFrame:
    """Hocanın Excel yapısındaki başlık offseti ve sütun ismi farklılıklarını düzeltir."""
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

    df["Ad_Soyad"] = df["Ad_Soyad"].astype(str).str.strip()

    def parse_id(row):
        val = str(row["Ogrenci_ID"]).split(".")[0].strip()
        if val in ["0", "nan", "None", "", "None"]:
            return abs(hash(row["Ad_Soyad"])) % 1000000
        try:
            return int(val)
        except:
            return abs(hash(row["Ad_Soyad"])) % 1000000

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
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ogrenciler (
                    ogrenci_id BIGINT PRIMARY KEY,
                    ad_soyad TEXT NOT NULL,
                    telefon TEXT,
                    kayit_tarihi TEXT
                );
            """)
            conn.commit()
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS haftalik_kayitlar (
                    id SERIAL PRIMARY KEY,
                    ogrenci_id BIGINT NOT NULL REFERENCES ogrenciler(ogrenci_id) ON DELETE CASCADE,
                    hafta_no INT NOT NULL,
                    durum TEXT NOT NULL,
                    yukleme_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT unique_ogrenci_hafta UNIQUE(ogrenci_id, hafta_no)
                );
            """)
            conn.commit()
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS arama_notlari (
                    id SERIAL PRIMARY KEY,
                    ogrenci_id BIGINT NOT NULL REFERENCES ogrenciler(ogrenci_id) ON DELETE CASCADE,
                    hafta_no INT NOT NULL,
                    arama_sonucu TEXT NOT NULL,
                    not_metni TEXT,
                    arayan TEXT,
                    kayit_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
        conn.close()


def ogrencileri_kaydet(conn, df_ogrenci: pd.DataFrame) -> None:
    with conn.cursor() as cur:
        for _, r in df_ogrenci.iterrows():
            cur.execute(
                """INSERT INTO ogrenciler (ogrenci_id, ad_soyad, telefon, kayit_tarihi)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT(ogrenci_id) DO UPDATE SET
                       ad_soyad=EXCLUDED.ad_soyad,
                       telefon=EXCLUDED.telefon,
                       kayit_tarihi=EXCLUDED.kayit_tarihi""",
                (
                    int(r["Ogrenci_ID"]),
                    str(r["Ad_Soyad"]),
                    str(r.get("Telefon", "")),
                    datetime.now().strftime("%Y-%m-%d"),
                ),
            )


def haftalik_kayit_ekle(conn, ogrenci_id: int, hafta_no: int, durum: str) -> None:
    durum_val = str(durum).strip() if durum else "Muaf"
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO haftalik_kayitlar (ogrenci_id, hafta_no, durum, yukleme_zamani)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT(ogrenci_id, hafta_no) DO UPDATE SET
                   durum=EXCLUDED.durum,
                   yukleme_zamani=EXCLUDED.yukleme_zamani""",
            (
                int(ogrenci_id),
                int(hafta_no),
                durum_val,
                datetime.now(),
            ),
        )


def arama_notu_ekle(ogrenci_id: int, hafta_no: int, sonuc: str, not_metni: str, arayan: str) -> None:
    conn = get_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO arama_notlari (ogrenci_id, hafta_no, arama_sonucu, not_metni, arayan, kayit_zamani)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (ogrenci_id, hafta_no, sonuc, not_metni, arayan, datetime.now()),
            )
            conn.commit()
        conn.close()


def tum_veriyi_oku():
    conn = get_conn()
    if not conn:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    df_ogrenciler = pd.read_sql_query("SELECT * FROM ogrenciler", conn)
    df_kayitlar = pd.read_sql_query("SELECT * FROM haftalik_kayitlar", conn)
    df_aramalar = pd.read_sql_query("SELECT * FROM arama_notlari ORDER BY kayit_zamani DESC", conn)
    conn.close()
    return df_ogrenciler, df_kayitlar, df_aramalar


def sonraki_hafta_no() -> int:
    conn = get_conn()
    if not conn:
        return 1
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(hafta_no) FROM haftalik_kayitlar")
        sonuc = cur.fetchone()[0]
    conn.close()
    return int(sonuc) + 1 if sonuc is not None else 1


def cift_excel_islem_ve_yukle(file_cozenler, file_cozmeyenler, hafta_no: int) -> int:
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

    for _, r in df_birlesik.iterrows():
        haftalik_kayit_ekle(conn, int(r["Ogrenci_ID"]), hafta_no, r["Durum"])

    conn.commit()
    conn.close()
    return len(df_birlesik)


def veritabanini_sifirla() -> None:
    """Tüm tabloları temizler."""
    conn = get_conn()
    if conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE arama_notlari, haftalik_kayitlar, ogrenciler RESTART IDENTITY CASCADE;")
            conn.commit()
        conn.close()


# ===================================================================
# 4) İŞ MANTIĞI HESAPLAMALARI
# ===================================================================
def ogrenci_metriklerini_hesapla(df_kayitlar: pd.DataFrame) -> pd.DataFrame:
    if df_kayitlar.empty:
        return pd.DataFrame(columns=["ogrenci_id", "temel_puan", "momentum_bonusu", "toplam_puan"])

    df = df_kayitlar.copy()

    # Pandas 2.1+ için map kullanımı (Eski 179. satır hatası giderildi)
    df["puan"] = df["durum"].map(PUAN_TABLOSU).fillna(0.0)
    temel = df.groupby("ogrenci_id")["puan"].sum().rename("temel_puan")

    momentum_kayitlari = []
    for ogrenci_id, grup in df.groupby("ogrenci_id"):
        grup_sirali = grup.sort_values("hafta_no", ascending=False)
        son_n = grup_sirali.head(MOMENTUM_PENCERE)
        bonus = 0.0
        if len(son_n) == MOMENTUM_PENCERE:
            haftalar = sorted(son_n["hafta_no"].tolist())
            ardisik = haftalar == list(range(haftalar[0], haftalar[0] + MOMENTUM_PENCERE))
            hepsi_vaktinde = all(son_n["durum"] == "Vaktinde Çözdü")
            if ardisik and hepsi_vaktinde:
                bonus = MOMENTUM_BONUS
        momentum_kayitlari.append({"ogrenci_id": ogrenci_id, "momentum_bonusu": bonus})

    momentum_df = pd.DataFrame(momentum_kayitlari).set_index("ogrenci_id")["momentum_bonusu"]
    sonuc = pd.concat([temel, momentum_df], axis=1).fillna(0.0)
    sonuc["toplam_puan"] = sonuc["temel_puan"] + sonuc["momentum_bonusu"]
    return sonuc.reset_index()


def arama_listesi_hesapla(df_kayitlar: pd.DataFrame):
    if df_kayitlar.empty:
        return pd.DataFrame(columns=["ogrenci_id"]), None, None

    son_hafta = int(df_kayitlar["hafta_no"].max())
    onceki_hafta = son_hafta - 1

    # Son 2 haftanın durumuna bak
    pivot = df_kayitlar[df_kayitlar["hafta_no"].isin([onceki_hafta, son_hafta])]
    if pivot.empty:
        return pd.DataFrame(columns=["ogrenci_id"]), son_hafta, onceki_hafta

    pivot = pivot.pivot_table(index="ogrenci_id", columns="hafta_no", values="durum", aggfunc="first")

    if son_hafta not in pivot.columns or onceki_hafta not in pivot.columns:
        return pd.DataFrame(columns=["ogrenci_id"]), son_hafta, onceki_hafta

    # 1. Kural: Son 2 hafta üst üste 'Çözmedi' olan riskli öğrenciler
    risk = pivot[(pivot[onceki_hafta] == "Çözmedi") & (pivot[son_hafta] == "Çözmedi")].reset_index()

    if risk.empty:
        return pd.DataFrame(columns=["ogrenci_id"]), son_hafta, onceki_hafta

    # 2. Kural (Cooldown/Dinlendirme): 
    # Son 2 hafta içinde (bu hafta ve bir önceki hafta) zaten aranmış olan öğrencileri getir
    conn = get_conn()
    if conn:
        arananlar_df = pd.read_sql_query(
            "SELECT DISTINCT ogrenci_id FROM arama_notlari WHERE hafta_no IN (%s, %s) AND arama_sonucu != 'Aranmadı'",
            conn,
            params=(son_hafta, onceki_hafta)
        )
        conn.close()
    else:
        arananlar_df = pd.DataFrame()

    # Eğer son 2 hafta içinde arandıysa arama listesinden çıkar (Dinlendirme kuralı)
    if not arananlar_df.empty:
        risk = risk[~risk["ogrenci_id"].isin(arananlar_df["ogrenci_id"])]

    return risk.rename(columns={onceki_hafta: "onceki_durum", son_hafta: "son_durum"}), son_hafta, onceki_hafta


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
    st.header("📂 Haftalık Veri Yükleme")

    file_cozenler = st.file_uploader("1. Çözenler Listesi (.xlsx)", type=["xlsx", "xls"], key="cozenler")
    file_cozmeyenler = st.file_uploader("2. Çözmeyenler Listesi (.xlsx)", type=["xlsx", "xls"], key="cozmeyenler")

    onerilen_hafta = sonraki_hafta_no()
    hafta_no = st.number_input("Bu veriler hangi hafta için?", min_value=1, value=onerilen_hafta, step=1)

    if st.button("✅ İki Listeyi İşle ve Kaydet", type="primary"):
        if file_cozenler is None and file_cozmeyenler is None:
            st.error("Lütfen en az bir Excel dosyası yükleyin.")
        else:
            toplam_kayit = cift_excel_islem_ve_yukle(file_cozenler, file_cozmeyenler, int(hafta_no))
            st.cache_data.clear()
            st.success(f"Hafta {hafta_no}: Toplam {toplam_kayit} öğrenci verisi başarıyla işlendi.")
            st.rerun()

    st.divider()
    df_ogrenciler_ozet, df_kayitlar_ozet, _ = tum_veriyi_oku()
    st.metric("Kayıtlı Öğrenci", len(df_ogrenciler_ozet))
    st.metric("İşlenmiş Hafta Sayısı", df_kayitlar_ozet["hafta_no"].nunique() if not df_kayitlar_ozet.empty else 0)

    # -------------------------------------------------------------
    # GÜVENLİ VERİTABANI SIFIRLAMA BÖLÜMÜ
    # -------------------------------------------------------------
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

arama_listesi, son_hafta, onceki_hafta = arama_listesi_hesapla(df_kayitlar)
if not arama_listesi.empty:
    arama_listesi = arama_listesi.merge(df_ogrenciler, on="ogrenci_id", how="left")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Toplam Öğrenci", len(df_ogrenciler))
col2.metric("Ortalama Puan", f"{metrikler['toplam_puan'].mean():.2f}" if not metrikler.empty else "—")
col3.metric("Aktif Hafta", son_hafta if son_hafta else "—")
col4.metric("Arama Listesi", len(arama_listesi), delta_color="inverse")

st.divider()

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📞 Arama Listesi", "🏆 Scoreboard", "🤖 AI Analist Raporu", "📈 Öğrenci Geçmişi", "🗂️ Arama Geçmişi"])

with tab1:
    st.subheader(f"Hafta {son_hafta} Operasyonel Arama Listesi" if son_hafta else "Arama Listesi")
    if arama_listesi.empty:
        st.success("Risk altında öğrenci yok. ✅")
    else:
        goster = arama_listesi[["ogrenci_id", "ad_soyad", "telefon", "onceki_durum", "son_durum"]]
        st.dataframe(goster, use_container_width=True, hide_index=True)

        for _, r in arama_listesi.iterrows():
            with st.expander(f"{r['ad_soyad']} (ID {r['ogrenci_id']})"):
                st.text_area("WhatsApp Mesajı", value=whatsapp_mesaji_olustur(r["ad_soyad"]), height=100, key=f"msg_{r['ogrenci_id']}")
                with st.form(key=f"form_{r['ogrenci_id']}"):
                    sonuc = st.selectbox("Sonuc", ARAMA_SONUCU_SECENEKLERI, key=f"res_{r['ogrenci_id']}")
                    not_metni = st.text_area("Not", key=f"note_{r['ogrenci_id']}")
                    arayan = st.text_input("Arayan", key=f"who_{r['ogrenci_id']}")
                    if st.form_submit_button("Kaydet"):
                        arama_notu_ekle(int(r["ogrenci_id"]), int(son_hafta), sonuc, not_metni, arayan)
                        st.cache_data.clear()
                        st.success("Not eklendi.")
                        st.rerun()

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
        ogrenci_kayitlari = df_kayitlar[df_kayitlar["ogrenci_id"] == secilen_id].sort_values("hafta_no")
        st.dataframe(ogrenci_kayitlari[["hafta_no", "durum"]], use_container_width=True, hide_index=True)

with tab5:
    st.subheader("🗂️ Tüm Arama Geçmişi")
    if not df_aramalar.empty:
        birlesik = df_aramalar.merge(df_ogrenciler, on="ogrenci_id", how="left")
        st.dataframe(birlesik[["kayit_zamani", "hafta_no", "ad_soyad", "arama_sonucu", "not_metni"]], use_container_width=True, hide_index=True)
    else:
        st.info("Arama kaydı bulunamadı.")
