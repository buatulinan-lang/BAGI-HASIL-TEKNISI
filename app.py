"""
Dashboard Bagi Hasil Teknisi (aplikasi berdiri sendiri)
=======================================================
Menghitung omzet jasa per teknisi beserta bagi hasilnya, dengan:
  - tarif per kata kunci pada NAMA BARANG yang bisa diubah manual
  - periode penggajian memakai cutoff tanggal 24 s/d 23
  - perbandingan terhadap skema flat (seluruh omzet jasa x satu tarif)

Jalankan:
    pip install -r requirements.txt
    streamlit run app.py
"""
import io
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Bagi Hasil Teknisi", layout="wide", page_icon="🧰")

DEFAULT_SALES_PATH = Path(__file__).parent / "data" / "penjualan.csv.gz"

BULAN_NAMES = ['', 'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli',
               'Agustus', 'September', 'Oktober', 'November', 'Desember']

PALETTE = ['#1f3864', '#2e9bd6', '#16a34a', '#e0921f', '#c9392f',
           '#7c3aed', '#0f8a82', '#a855f7', '#3f8ac9', '#d1478d']

# --- aturan bagi hasil (nilai awal; bisa diubah dari dashboard) --------------
KATA_KUNCI_TARIF = ['INTERFACE', 'NORMAL', 'MATI TOTAL', 'PROMO']
TARIF_AWAL = {'Interface': 20.0, 'Normal': 30.0, 'Mati Total': 32.0, 'Promo': 60.0}
TARIF_DEFAULT_AWAL = 30.0
TARIF_PEMBANDING_AWAL = 30.0
LABEL_LAINNYA = 'Lainnya'

st.markdown("""
<style>
  .kpi-wrap{display:grid;grid-template-columns:repeat(6,1fr);gap:14px;margin-bottom:6px;}
  @media(max-width:1200px){.kpi-wrap{grid-template-columns:repeat(3,1fr);}}
  .kpi{border-radius:16px;padding:16px 16px 18px;color:#fff;min-height:112px;
       box-shadow:0 8px 20px rgba(30,20,60,.14);}
  .kpi .label{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;opacity:.92;}
  .kpi .value{font-size:21px;font-weight:800;margin-top:10px;line-height:1.15;}
  .kpi .foot{font-size:11px;margin-top:6px;opacity:.9;}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Utilitas
# ---------------------------------------------------------------------------
def rp(v, singkat=True):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    neg, v = v < 0, abs(float(v))
    if singkat:
        if v >= 1_000_000_000:
            s = f"Rp {v/1_000_000_000:,.2f} M"
        elif v >= 1_000_000:
            s = f"Rp {v/1_000_000:,.1f} jt"
        elif v >= 1_000:
            s = f"Rp {v/1_000:,.0f} rb"
        else:
            s = f"Rp {v:,.0f}"
    else:
        s = f"Rp {v:,.0f}"
    s = s.replace(",", "#").replace(".", ",").replace("#", ".")
    return ("-" + s) if neg else s


def kpi_html(cards):
    cells = []
    for c in cards:
        cells.append(f"""
        <div class="kpi" style="background:{c['grad']}">
          <div class="label">{c['label']}</div>
          <div class="value">{c['value']}</div>
          <div class="foot">{c.get('sub','&nbsp;')}</div>
        </div>""")
    return f'<div class="kpi-wrap">{"".join(cells)}</div>'


def cocok_kata_kunci(nama_barang):
    s = str(nama_barang).upper()
    return [k for k in KATA_KUNCI_TARIF if k in s]


def pilih_label_tarif(kw_str, urutan):
    if not kw_str:
        return LABEL_LAINNYA
    cocok = kw_str.split('|')
    for k in urutan:
        if k in cocok:
            return k.title()
    return cocok[0].title()


def periode_gaji(bulan_gaji: int, tahun_gaji: int):
    """Gaji bulan M dihitung dari 24 bulan (M-2) s/d 23 bulan (M-1).

    Contoh: gaji Mei 2026 -> 24 Maret 2026 s/d 23 April 2026.
    """
    m_akhir, th_akhir = bulan_gaji - 1, tahun_gaji
    if m_akhir < 1:
        m_akhir += 12
        th_akhir -= 1
    m_awal, th_awal = m_akhir - 1, th_akhir
    if m_awal < 1:
        m_awal += 12
        th_awal -= 1
    return pd.Timestamp(th_awal, m_awal, 24), pd.Timestamp(th_akhir, m_akhir, 23)


def label_periode(bulan_gaji, tahun_gaji):
    a, b = periode_gaji(bulan_gaji, tahun_gaji)
    return (f"Gaji {BULAN_NAMES[bulan_gaji]} {tahun_gaji}  "
            f"({a.day} {BULAN_NAMES[a.month]} – {b.day} {BULAN_NAMES[b.month]} {b.year})")


def daftar_periode_gaji(tgl_min, tgl_max):
    hasil = []
    if pd.isna(tgl_min) or pd.isna(tgl_max):
        return hasil
    y, m = tgl_min.year, tgl_min.month
    for _ in range(120):
        a, b = periode_gaji(m, y)
        if a > tgl_max:
            break
        if b >= tgl_min:
            hasil.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return hasil


SALES_REQUIRED = ['TGL FAKTUR', 'NO FAKTUR', 'KATEGORI BARANG', 'NAMA BARANG',
                  'QTY', 'TOTAL HARGA', 'CABANG']


@st.cache_data(show_spinner="Membaca data penjualan...")
def load_sales(file_bytes: bytes, source_kind: str) -> pd.DataFrame:
    if source_kind == 'csv_gz':
        df = pd.read_csv(io.BytesIO(file_bytes), compression='gzip')
    elif source_kind == 'csv':
        df = pd.read_csv(io.BytesIO(file_bytes))
    else:
        xls = pd.ExcelFile(io.BytesIO(file_bytes), engine='openpyxl')
        frames = []
        for sheet in xls.sheet_names:
            d = xls.parse(sheet)
            if d.empty:
                continue
            d['CABANG'] = sheet
            frames.append(d)
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True, sort=False)

    if df.empty:
        return df
    missing = [c for c in SALES_REQUIRED if c not in df.columns]
    if missing:
        raise ValueError("Kolom tidak ditemukan: " + ", ".join(missing))

    for c in ['QTY', 'TOTAL HARGA']:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    df['TGL'] = pd.to_datetime(df['TGL FAKTUR'], errors='coerce')
    df['KATEGORI'] = df['KATEGORI BARANG'].astype(str).str.strip().str.upper()
    df['BARANG'] = df['NAMA BARANG'].astype(str).str.strip()

    fin = df['NAMA TEKNISI (FINAL)'] if 'NAMA TEKNISI (FINAL)' in df.columns \
        else pd.Series(index=df.index, dtype=object)
    asli = df['NAMA TEKNISI'] if 'NAMA TEKNISI' in df.columns \
        else pd.Series(index=df.index, dtype=object)
    tek = fin.fillna(asli).astype(str).str.strip().str.upper()
    tek = tek.replace({'NAN': '', 'NONE': ''})
    df['TEKNISI'] = tek
    df.loc[df['TEKNISI'] == '', 'TEKNISI'] = 'TIDAK ADA TEKNISI'

    df = df[df['KATEGORI'] == 'JASA'].copy()
    df['KW_MATCH'] = df['BARANG'].map(lambda s: '|'.join(cocok_kata_kunci(s)))
    return df


# ---------------------------------------------------------------------------
# Sidebar: sumber data
# ---------------------------------------------------------------------------
st.sidebar.title("📁 Sumber Data")
up = st.sidebar.file_uploader(
    "Upload data penjualan (opsional)", type=['xlsx', 'gz', 'csv'],
    help="Kalau kosong, dipakai file bawaan data/penjualan.csv.gz.")

jasa_all = pd.DataFrame()
try:
    if up is not None:
        kind = ('csv_gz' if up.name.endswith('.gz')
                else 'csv' if up.name.endswith('.csv') else 'xlsx')
        jasa_all = load_sales(up.getvalue(), kind)
        st.sidebar.success("Memakai file yang diupload.")
    elif DEFAULT_SALES_PATH.exists():
        jasa_all = load_sales(DEFAULT_SALES_PATH.read_bytes(), 'csv_gz')
        st.sidebar.info("Memakai data bawaan repo.")
except Exception as e:  # noqa: BLE001
    st.sidebar.error(f"Data tidak terbaca: {e}")

st.title("🧰 Bagi Hasil Teknisi")

if jasa_all.empty:
    st.info(
        "Data belum tersedia. Letakkan file **data/penjualan.csv.gz** di folder aplikasi, "
        "atau upload file penjualan lewat panel kiri.\n\n"
        "Format yang dibutuhkan: data faktur penjualan dengan kolom TGL FAKTUR, NO FAKTUR, "
        "KATEGORI BARANG, NAMA BARANG, NAMA TEKNISI (FINAL), QTY, TOTAL HARGA, dan CABANG "
        "(atau satu sheet per cabang bila berupa .xlsx)."
    )
    st.stop()

st.caption(
    f"{len(jasa_all):,} baris jasa · {jasa_all['CABANG'].nunique()} cabang · "
    f"{jasa_all.loc[jasa_all['TEKNISI'] != 'TIDAK ADA TEKNISI', 'TEKNISI'].nunique()} teknisi · "
    f"data {jasa_all['TGL'].min():%d %b %Y} – {jasa_all['TGL'].max():%d %b %Y}"
)

# ---------------------------------------------------------------------------
# Pengaturan tarif
# ---------------------------------------------------------------------------
with st.expander("⚙️ Pengaturan Tarif Bagi Hasil — klik untuk mengubah", expanded=False):
    st.caption("Ubah angka sesuai kebijakan; seluruh perhitungan langsung menyesuaikan.")
    c1, c2, c3, c4 = st.columns(4)
    tarif_input = {}
    with c1:
        tarif_input['Interface'] = st.number_input(
            "Interface (%)", 0.0, 100.0, TARIF_AWAL['Interface'], 1.0, key='t_int')
    with c2:
        tarif_input['Normal'] = st.number_input(
            "Normal (%)", 0.0, 100.0, TARIF_AWAL['Normal'], 1.0, key='t_nor')
    with c3:
        tarif_input['Mati Total'] = st.number_input(
            "Mati Total (%)", 0.0, 100.0, TARIF_AWAL['Mati Total'], 1.0, key='t_mat')
    with c4:
        tarif_input['Promo'] = st.number_input(
            "Promo (%)", 0.0, 100.0, TARIF_AWAL['Promo'], 1.0, key='t_pro')

    c5, c6, c7 = st.columns([1, 1, 1.6])
    with c5:
        tarif_lain = st.number_input(
            "Tanpa kata kunci (%)", 0.0, 100.0, TARIF_DEFAULT_AWAL, 1.0, key='t_lain',
            help="Untuk item seperti JASA REPAIR / JASA BATERAI yang tidak mengandung kata kunci.")
    with c6:
        tarif_flat = st.number_input(
            "Tarif pembanding (%)", 0.0, 100.0, TARIF_PEMBANDING_AWAL, 1.0, key='t_flat',
            help="Skema pembanding: seluruh omzet jasa dikali tarif ini.")
    with c7:
        prioritas = st.selectbox(
            "Kalau satu nama mengandung 2 kata kunci, yang menang:",
            ['Normal', 'Promo', 'Mati Total', 'Interface'], index=0, key='t_prio')

    if st.button("↩️ Kembalikan ke tarif awal", key='t_reset'):
        for k, v in [('t_int', 'Interface'), ('t_nor', 'Normal'),
                     ('t_mat', 'Mati Total'), ('t_pro', 'Promo')]:
            st.session_state[k] = TARIF_AWAL[v]
        st.session_state['t_lain'] = TARIF_DEFAULT_AWAL
        st.session_state['t_flat'] = TARIF_PEMBANDING_AWAL
        st.session_state['t_prio'] = 'Normal'
        st.rerun()

urutan = [prioritas.upper()] + [k for k in KATA_KUNCI_TARIF if k != prioritas.upper()]
peta_tarif = {k: v / 100.0 for k, v in tarif_input.items()}
peta_tarif[LABEL_LAINNYA] = tarif_lain / 100.0

jasa_all = jasa_all.copy()
jasa_all['TARIF_LABEL'] = jasa_all['KW_MATCH'].map(lambda s: pilih_label_tarif(s, urutan))
jasa_all['TARIF'] = jasa_all['TARIF_LABEL'].map(peta_tarif).fillna(0.0)
jasa_all['BAGI_HASIL'] = jasa_all['TOTAL HARGA'] * jasa_all['TARIF']
jasa_all['FLAT'] = jasa_all['TOTAL HARGA'] * (tarif_flat / 100.0)

st.caption(
    "**Tarif aktif:** " +
    " · ".join(f"{k} {v:.0f}%" for k, v in tarif_input.items()) +
    f" · Lainnya {tarif_lain:.0f}% · pembanding flat {tarif_flat:.0f}%"
    f" · prioritas bentrok: {prioritas}"
)

# ---------------------------------------------------------------------------
# Filter periode & cabang
# ---------------------------------------------------------------------------
fa, fb, fc = st.columns([2.2, 1.4, 1])
periode_list = daftar_periode_gaji(jasa_all['TGL'].min(), jasa_all['TGL'].max())
opsi = ['Semua Periode'] + periode_list
with fa:
    pilih = st.selectbox(
        "Periode penggajian (cutoff tanggal 24 s/d 23)", opsi,
        index=len(opsi) - 1 if len(opsi) > 1 else 0,
        format_func=lambda x: ("Semua Periode (tanpa cutoff)" if isinstance(x, str)
                               else label_periode(x[1], x[0])),
        key='f_periode')
with fb:
    cab_opts = ['Semua Cabang'] + sorted(jasa_all['CABANG'].dropna().unique().tolist())
    f_cabang = st.selectbox("Cabang", cab_opts, key='f_cabang')
with fc:
    sembunyikan = st.checkbox("Sembunyikan baris tanpa nama teknisi", value=False,
                              key='f_hide')

jasa = jasa_all
if f_cabang != 'Semua Cabang':
    jasa = jasa[jasa['CABANG'] == f_cabang]
if isinstance(pilih, str):
    periode_txt = "Seluruh periode data (tanpa cutoff)"
    tag_file = "semua-periode"
else:
    a, b = periode_gaji(pilih[1], pilih[0])
    jasa = jasa[(jasa['TGL'] >= a) & (jasa['TGL'] <= b)]
    periode_txt = (f"{a.day} {BULAN_NAMES[a.month]} {a.year} – "
                   f"{b.day} {BULAN_NAMES[b.month]} {b.year}")
    tag_file = f"gaji-{pilih[0]}-{pilih[1]:02d}"

jasa_tampil = jasa[jasa['TEKNISI'] != 'TIDAK ADA TEKNISI'] if sembunyikan else jasa

st.markdown(f"**Rentang dihitung:** {periode_txt}"
            + (f" · cabang **{f_cabang}**" if f_cabang != 'Semua Cabang' else ""))

if jasa.empty:
    st.warning("Tidak ada transaksi jasa pada periode/cabang tersebut.")
    st.stop()

# ---------------------------------------------------------------------------
# KPI
# ---------------------------------------------------------------------------
omzet = jasa['TOTAL HARGA'].sum()
bh = jasa['BAGI_HASIL'].sum()
fl = jasa['FLAT'].sum()
selisih = bh - fl
n_tek = jasa.loc[jasa['TEKNISI'] != 'TIDAK ADA TEKNISI', 'TEKNISI'].nunique()
tanpa_nama = jasa.loc[jasa['TEKNISI'] == 'TIDAK ADA TEKNISI', 'TOTAL HARGA'].sum()

n_kw = (jasa['TARIF_LABEL'] != LABEL_LAINNYA).sum()
if n_kw == 0:
    sama = abs(tarif_lain - tarif_flat) < 1e-9
    st.warning(
        "Pada periode ini **tidak ada item jasa yang mengandung kata kunci** "
        "(Interface / Normal / Mati Total / Promo) — semuanya memakai penamaan lama "
        f"seperti `JASA REPAIR`, sehingga kena tarif {tarif_lain:.0f}%"
        + (f", dan karena pembanding juga {tarif_flat:.0f}% kedua skema jadi **sama persis**."
           if sama else ".")
        + " Penamaan berkata kunci baru mulai dipakai sekitar Juli 2026.")
elif n_kw < len(jasa) * 0.5:
    st.info(f"Baru **{n_kw:,} dari {len(jasa):,} baris** ({n_kw/len(jasa)*100:.0f}%) "
            "memakai penamaan berkata kunci; sisanya kena tarif tanpa-kata-kunci.")

st.markdown(kpi_html([
    {'label': 'Omzet Jasa', 'value': rp(omzet), 'sub': f"{len(jasa):,} baris",
     'grad': 'linear-gradient(135deg,#1f3864,#2e5394)'},
    {'label': 'Bagi Hasil (Aturan)', 'value': rp(bh),
     'sub': f"{(bh/omzet*100 if omzet else 0):.1f}% dari omzet jasa",
     'grad': 'linear-gradient(135deg,#16a34a,#22c55e)'},
    {'label': f'Pembanding Flat {tarif_flat:.0f}%', 'value': rp(fl),
     'sub': f'omzet jasa × {tarif_flat:.0f}%',
     'grad': 'linear-gradient(135deg,#7c3aed,#a855f7)'},
    {'label': 'Selisih', 'value': rp(selisih),
     'sub': ('aturan lebih besar' if selisih > 0
             else 'flat lebih besar' if selisih < 0 else 'sama'),
     'grad': ('linear-gradient(135deg,#e0921f,#e2b21a)' if selisih >= 0
              else 'linear-gradient(135deg,#c9392f,#e0475a)')},
    {'label': 'Jumlah Teknisi', 'value': f"{n_tek:,}",
     'sub': f"rata-rata {rp(bh/n_tek if n_tek else 0)}/teknisi",
     'grad': 'linear-gradient(135deg,#0f8a82,#17a3a3)'},
    {'label': 'Omzet Tanpa Nama Teknisi', 'value': rp(tanpa_nama),
     'sub': f"{(jasa['TEKNISI'] == 'TIDAK ADA TEKNISI').sum():,} baris",
     'grad': 'linear-gradient(135deg,#64748b,#94a3b8)'},
]), unsafe_allow_html=True)
st.write("")

lbl_flat = f'Pembanding {tarif_flat:.0f}%'

# ---------------------------------------------------------------------------
# Rekap utama: per Teknisi x Cabang
# ---------------------------------------------------------------------------
st.markdown("### Rekap Bagi Hasil per Teknisi & Cabang")
st.caption(
    "Dipecah per cabang karena sebagian teknisi bekerja di lebih dari satu cabang, "
    "sehingga bagi hasilnya bisa dibebankan ke cabang yang tepat."
)

rek = (jasa_tampil.groupby(['TEKNISI', 'CABANG'], as_index=False)
       .agg(Baris=('TOTAL HARGA', 'size'),
            Omzet_Jasa=('TOTAL HARGA', 'sum'),
            Bagi_Hasil=('BAGI_HASIL', 'sum'),
            Flat=('FLAT', 'sum')))
rek['Selisih'] = rek['Bagi_Hasil'] - rek['Flat']
rek['Efektif %'] = (rek['Bagi_Hasil'] / rek['Omzet_Jasa'] * 100).round(1)
rek = rek.sort_values('Bagi_Hasil', ascending=False)

rek_show = rek.rename(columns={
    'TEKNISI': 'Nama Teknisi', 'CABANG': 'Cabang',
    'Omzet_Jasa': 'Omzet Jasa', 'Bagi_Hasil': 'Bagi Hasil (Aturan)', 'Flat': lbl_flat})

cari = st.text_input("Cari nama teknisi / cabang", key='cari_rekap')
rek_view = rek_show
if cari:
    m = rek_show.apply(lambda r: cari.upper() in
                       f"{r['Nama Teknisi']} {r['Cabang']}".upper(), axis=1)
    rek_view = rek_show[m]

st.dataframe(
    rek_view.style.format({
        'Baris': '{:,.0f}', 'Omzet Jasa': 'Rp {:,.0f}',
        'Bagi Hasil (Aturan)': 'Rp {:,.0f}', lbl_flat: 'Rp {:,.0f}',
        'Selisih': 'Rp {:,.0f}'}),
    use_container_width=True, height=460, hide_index=True, key='tabel_rekap')

# --- unduhan: wajib memuat Nama Teknisi, Cabang, Bagi Hasil (Aturan) ---
unduh = rek_show[['Nama Teknisi', 'Cabang', 'Bagi Hasil (Aturan)',
                  'Omzet Jasa', lbl_flat, 'Selisih', 'Baris', 'Efektif %']].copy()
for c in ['Bagi Hasil (Aturan)', 'Omzet Jasa', lbl_flat, 'Selisih']:
    unduh[c] = unduh[c].round(0).astype('int64')

u1, u2 = st.columns(2)
with u1:
    st.download_button(
        "⬇️ Unduh rekap per Teknisi & Cabang (CSV)",
        data=unduh.to_csv(index=False).encode('utf-8-sig'),
        file_name=f"bagi_hasil_teknisi_cabang_{tag_file}.csv",
        mime="text/csv", use_container_width=True, key='unduh_rekap')
with u2:
    gab = (jasa_tampil.groupby('TEKNISI', as_index=False)
           .agg(Omzet_Jasa=('TOTAL HARGA', 'sum'),
                Bagi_Hasil=('BAGI_HASIL', 'sum'),
                Flat=('FLAT', 'sum')))
    gab['Cabang'] = gab['TEKNISI'].map(
        jasa_tampil.groupby('TEKNISI')['CABANG']
        .apply(lambda s: ', '.join(sorted(s.unique()))))
    gab['Selisih'] = gab['Bagi_Hasil'] - gab['Flat']
    gab = gab.rename(columns={'TEKNISI': 'Nama Teknisi', 'Omzet_Jasa': 'Omzet Jasa',
                              'Bagi_Hasil': 'Bagi Hasil (Aturan)', 'Flat': lbl_flat})
    gab = gab[['Nama Teknisi', 'Cabang', 'Bagi Hasil (Aturan)', 'Omzet Jasa',
               lbl_flat, 'Selisih']].sort_values('Bagi Hasil (Aturan)', ascending=False)
    for c in ['Bagi Hasil (Aturan)', 'Omzet Jasa', lbl_flat, 'Selisih']:
        gab[c] = gab[c].round(0).astype('int64')
    st.download_button(
        "⬇️ Unduh rekap per Teknisi (digabung semua cabang)",
        data=gab.to_csv(index=False).encode('utf-8-sig'),
        file_name=f"bagi_hasil_teknisi_{tag_file}.csv",
        mime="text/csv", use_container_width=True, key='unduh_gab')

st.caption("Kedua berkas memuat kolom **Nama Teknisi**, **Cabang**, dan "
           "**Bagi Hasil (Aturan)**, ditambah omzet, pembanding, dan selisihnya.")

# ---------------------------------------------------------------------------
# Grafik & rekap pendukung
# ---------------------------------------------------------------------------
g1, g2 = st.columns([1.15, 1])
with g1:
    st.markdown("#### 15 Teratas — Aturan vs Pembanding")
    top = rek[rek['TEKNISI'] != 'TIDAK ADA TEKNISI'].head(15).copy()
    top['NAMA'] = top['TEKNISI'].str.slice(0, 22) + " — " + top['CABANG'].str.slice(0, 10)
    top = top.sort_values('Bagi_Hasil')
    fig = go.Figure()
    fig.add_bar(y=top['NAMA'], x=top['Bagi_Hasil'], orientation='h',
                name='Aturan', marker_color='#16a34a')
    fig.add_bar(y=top['NAMA'], x=top['Flat'], orientation='h',
                name=f'Flat {tarif_flat:.0f}%', marker_color='#a855f7')
    fig.update_layout(barmode='group', height=560, margin=dict(l=10, r=10, t=10, b=10),
                      legend=dict(orientation='h', y=1.04), xaxis_title='Rupiah')
    st.plotly_chart(fig, use_container_width=True, key='fig_top')

with g2:
    st.markdown("#### Komposisi Omzet Jasa per Tarif")
    gtar = jasa.groupby('TARIF_LABEL').agg(
        Baris=('TOTAL HARGA', 'size'), Omzet=('TOTAL HARGA', 'sum'),
        Bagi_Hasil=('BAGI_HASIL', 'sum'))
    gtar['Tarif'] = gtar.index.map(lambda k: peta_tarif.get(k, 0.0) * 100)
    gtar['Tarif'] = gtar['Tarif'].round(1).astype(str) + '%'
    gtar = gtar.sort_values('Omzet', ascending=False)
    st.dataframe(
        gtar[['Tarif', 'Baris', 'Omzet', 'Bagi_Hasil']]
        .rename(columns={'Bagi_Hasil': 'Bagi Hasil'})
        .style.format({'Baris': '{:,.0f}', 'Omzet': 'Rp {:,.0f}',
                       'Bagi Hasil': 'Rp {:,.0f}'}),
        use_container_width=True, key='tabel_tarif')
    figp = px.pie(names=gtar.index, values=gtar['Omzet'], hole=0.55,
                  color_discrete_sequence=PALETTE)
    figp.update_layout(height=300, margin=dict(l=5, r=5, t=5, b=5),
                       legend=dict(font=dict(size=9)))
    st.plotly_chart(figp, use_container_width=True, key='fig_tarif')

st.markdown("#### Rekap per Cabang")
gcb = jasa.groupby('CABANG', as_index=False).agg(
    Teknisi=('TEKNISI', 'nunique'), Baris=('TOTAL HARGA', 'size'),
    Omzet_Jasa=('TOTAL HARGA', 'sum'), Bagi_Hasil=('BAGI_HASIL', 'sum'),
    Flat=('FLAT', 'sum'))
gcb['Selisih'] = gcb['Bagi_Hasil'] - gcb['Flat']
gcb['Efektif %'] = (gcb['Bagi_Hasil'] / gcb['Omzet_Jasa'] * 100).round(1)
gcb = gcb.sort_values('Bagi_Hasil', ascending=False).rename(columns={
    'CABANG': 'Cabang', 'Omzet_Jasa': 'Omzet Jasa',
    'Bagi_Hasil': 'Bagi Hasil (Aturan)', 'Flat': lbl_flat})
st.dataframe(
    gcb.style.format({'Teknisi': '{:,.0f}', 'Baris': '{:,.0f}',
                      'Omzet Jasa': 'Rp {:,.0f}', 'Bagi Hasil (Aturan)': 'Rp {:,.0f}',
                      lbl_flat: 'Rp {:,.0f}', 'Selisih': 'Rp {:,.0f}'}),
    use_container_width=True, height=380, hide_index=True, key='tabel_cabang')
st.download_button(
    "⬇️ Unduh rekap per Cabang (CSV)",
    data=gcb.to_csv(index=False).encode('utf-8-sig'),
    file_name=f"bagi_hasil_cabang_{tag_file}.csv", mime="text/csv", key='unduh_cab')

st.markdown("#### Detail Transaksi Jasa")
q = st.text_input("Cari teknisi / cabang / barang / faktur", key='cari_detail')
kol = ['TGL FAKTUR', 'NO FAKTUR', 'CABANG', 'TEKNISI', 'NAMA BARANG',
       'TARIF_LABEL', 'TARIF', 'TOTAL HARGA', 'BAGI_HASIL', 'FLAT']
kol = [c for c in kol if c in jasa.columns]
det = jasa[kol].rename(columns={
    'CABANG': 'Cabang', 'TEKNISI': 'Nama Teknisi', 'TARIF_LABEL': 'Kategori Tarif',
    'TARIF': 'Tarif', 'BAGI_HASIL': 'Bagi Hasil', 'FLAT': lbl_flat})
if q:
    m = det.apply(lambda r: q.upper() in ' '.join(str(v) for v in r.values).upper(), axis=1)
    det = det[m]
st.caption(f"{len(det):,} baris (ditampilkan maksimal 1.000).")
st.dataframe(det.head(1000), use_container_width=True, height=360,
             hide_index=True, key='tabel_detail')

with st.expander("ℹ️ Cara perhitungan & catatan"):
    st.write(
        "**Tarif bagi hasil** ditentukan dari kata kunci pada kolom NAMA BARANG, "
        "mengikuti isian pada panel Pengaturan Tarif di atas:\n"
        f"- mengandung **Interface** → {tarif_input['Interface']:.0f}%\n"
        f"- mengandung **Normal** → {tarif_input['Normal']:.0f}%\n"
        f"- mengandung **Mati Total** → {tarif_input['Mati Total']:.0f}%\n"
        f"- mengandung **Promo** → {tarif_input['Promo']:.0f}%\n"
        f"- tanpa kata kunci mana pun → **{tarif_lain:.0f}%** (mencakup item berpola "
        "`JASA ...` seperti JASA REPAIR, JASA BATERAI, JASA LCD 50%)\n\n"
        "Bila satu nama mengandung dua kata kunci sekaligus (mis. "
        f"`JS PROMO LCD 250K - NORMAL`), dipakai **{prioritas} "
        f"{tarif_input[prioritas]:.0f}%** sesuai pilihan prioritas.\n\n"
        "**Periode penggajian** memakai cutoff tanggal 24 s/d 23: gaji bulan M dihitung "
        "dari 24 bulan (M−2) sampai 23 bulan (M−1). Contoh gaji Mei 2026 = 24 Maret 2026 "
        "s/d 23 April 2026. Tanggal acuan: **TGL FAKTUR**.\n\n"
        f"**Pembanding Flat {tarif_flat:.0f}%** = seluruh omzet jasa × {tarif_flat:.0f}%, "
        "tanpa membedakan jenis pekerjaan.\n\n"
        "Nama teknisi diambil dari kolom **NAMA TEKNISI (FINAL)**; bila kosong dipakai "
        "kolom NAMA TEKNISI. Baris yang keduanya kosong masuk kelompok "
        "*TIDAK ADA TEKNISI* — tetap ditampilkan agar terlihat, dan bisa disembunyikan "
        "lewat centang di atas.\n\n"
        "Perhitungan memakai **omzet jasa (TOTAL HARGA)**, belum dikurangi biaya apa pun. "
        "Hanya baris berkategori **JASA** yang dihitung."
    )
