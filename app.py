"""
Dashboard Bagi Hasil Teknisi — V2 (aplikasi berdiri sendiri)
===========================================================
Versi pengembangan. Versi produksi (V1) ada di repo BAGI-HASIL-TEKNISI dan
tidak boleh diubah dari sini.

Menghitung omzet jasa per teknisi beserta bagi hasilnya, dengan:
  - tarif per kata kunci pada NAMA BARANG yang bisa diubah manual
  - periode penggajian memakai cutoff tanggal 24 s/d 23
  - perbandingan terhadap skema flat (seluruh omzet jasa x satu tarif)

Jalankan:
    pip install -r requirements.txt
    streamlit run app.py
"""
import io
import re
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

VERSI_APP = "V2"
# Dinaikkan setiap kali struktur kolom hasil pemuatan berubah, supaya cache
# Streamlit dari versi lama tidak ikut terpakai.
VERSI_DATA = 3

st.set_page_config(page_title=f"Bagi Hasil Teknisi {VERSI_APP}", layout="wide",
                   page_icon="🧰")

DEFAULT_SALES_PATH = Path(__file__).parent / "data" / "penjualan.csv.gz"

BULAN_NAMES = ['', 'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli',
               'Agustus', 'September', 'Oktober', 'November', 'Desember']

PALETTE = ['#1f3864', '#2e9bd6', '#16a34a', '#e0921f', '#c9392f',
           '#7c3aed', '#0f8a82', '#a855f7', '#3f8ac9', '#d1478d']

# --- aturan bagi hasil (nilai awal; bisa diubah dari dashboard) --------------
KATA_KUNCI_TARIF = ['INTERFACE', 'NORMAL', 'MATI TOTAL', 'PROMO']
KATEGORI_TARIF = ['Interface', 'Normal', 'Mati Total', 'Promo', 'Lainnya']
TARIF_AWAL = {'Interface': 20.0, 'Normal': 30.0, 'Mati Total': 32.0, 'Promo': 60.0}
TARIF_DEFAULT_AWAL = 30.0
TARIF_PEMBANDING_AWAL = 30.0
LABEL_LAINNYA = 'Lainnya'

# Perubahan tarif Mati Total yang berlaku mulai tanggal tertentu. Diterapkan
# per TGL FAKTUR sehingga periode gaji yang terbelah terhitung proporsional.
TGL_MT_BARU_AWAL = date(2026, 9, 1)
TARIF_MT_BARU_AWAL = 35.0

# Insentif non-teknisi, dihitung otomatis per cabang dengan periode kalender
# (tanggal 1 s/d akhir bulan). Dasar tiap peran:
#   Store Leader & Supervisor  -> omzet jasa berkualifikasi Mati Total
#   Front Liner (Admin & sales retail) -> penjualan aksesoris, laptop, handphone
#   Team                       -> seluruh omzet jasa cabang
KATEGORI_JUAL_AKSESORIS = ['PENJUALAN AKSESORIS']
KATEGORI_JUAL_LAPTOP = ['PENJUALAN LAPTOP']
KATEGORI_JUAL_HP = ['PENJUALAN HP', 'PENJUALAN HANDPHONE']

PERSEN_INSENTIF_AWAL = {
    'Store Leader': 3.0,       # % omzet jasa Mati Total
    'Supervisor': 1.0,         # % omzet jasa Mati Total
    'Front Liner Aksesoris': 5.0,   # % omzet penjualan aksesoris
    'Team': 2.0,               # % seluruh omzet jasa cabang
}
# skema A: persentase dari omzet ; skema B: nominal per unit terjual
SKEMA_A_AWAL = {'Laptop': 3.0, 'Handphone': 2.0}
SKEMA_B_AWAL = {'Laptop': 50_000.0, 'Handphone': 30_000.0}
NAMA_SKEMA_A = 'Kategori 1 — persentase omzet'
NAMA_SKEMA_B = 'Kategori 2 — nominal per unit'

KOL_INSENTIF = ['Insentif Store Leader', 'Insentif Supervisor',
                'Insentif Front Liner', 'Insentif Team']


def daftar_bulan(tgl_min, tgl_max):
    """Daftar (tahun, bulan) yang tercakup data, urut naik."""
    if pd.isna(tgl_min) or pd.isna(tgl_max):
        return []
    hasil, y, m = [], tgl_min.year, tgl_min.month
    for _ in range(240):
        hasil.append((y, m))
        if (y, m) == (tgl_max.year, tgl_max.month):
            break
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return hasil


def rentang_bulan(tahun, bulan):
    """Tanggal 1 s/d hari terakhir bulan tersebut."""
    awal = pd.Timestamp(tahun, bulan, 1)
    return awal, awal + pd.offsets.MonthEnd(0)


def label_bulan(tahun, bulan):
    _, akhir = rentang_bulan(tahun, bulan)
    return f"{BULAN_NAMES[bulan]} {tahun} (1–{akhir.day} {BULAN_NAMES[bulan]})"


def rekap_insentif(df_jasa_bulan, df_semua_bulan, persen, skema, tarif_skema):
    """Rekap insentif seluruh peran per cabang untuk satu bulan kalender.

    skema: NAMA_SKEMA_A (persen omzet) atau NAMA_SKEMA_B (nominal per unit).
    """
    mt = df_jasa_bulan[df_jasa_bulan['TARIF_LABEL'] == 'Mati Total']
    kj = df_semua_bulan['KAT_JUAL']
    aks = df_semua_bulan[kj.isin(KATEGORI_JUAL_AKSESORIS)]
    lap = df_semua_bulan[kj.isin(KATEGORI_JUAL_LAPTOP)]
    hpx = df_semua_bulan[kj.isin(KATEGORI_JUAL_HP)]

    cabang = sorted(set(df_semua_bulan['CABANG'].dropna())
                    | set(df_jasa_bulan['CABANG'].dropna()))
    t = pd.DataFrame({'Cabang': cabang})

    def _jumlah(sumber, nilai):
        """Total per cabang; kolom yang tidak ada dianggap nol."""
        if not len(sumber) or nilai not in sumber.columns:
            return pd.Series(dtype='float64')
        return sumber.groupby('CABANG')[nilai].sum()

    def isi(sumber, nama, pakai_qty=False):
        t['Omzet ' + nama] = t['Cabang'].map(
            _jumlah(sumber, 'TOTAL HARGA')).fillna(0.0)
        if pakai_qty:
            t['Unit ' + nama] = t['Cabang'].map(
                _jumlah(sumber, 'QTY')).fillna(0.0)

    isi(mt, 'Jasa Mati Total')
    isi(aks, 'Penjualan Aksesoris')
    isi(lap, 'Penjualan Laptop', pakai_qty=True)
    isi(hpx, 'Penjualan Handphone', pakai_qty=True)

    for kolom, sumber, nilai in [('Omzet Jasa', df_jasa_bulan, 'TOTAL HARGA'),
                                 ('Bagi Hasil Teknisi', df_jasa_bulan, 'BAGI_HASIL'),
                                 ('Omzet Total', df_semua_bulan, 'TOTAL HARGA'),
                                 ('HPP', df_semua_bulan, 'HARGA BELI')]:
        t[kolom] = t['Cabang'].map(_jumlah(sumber, nilai)).fillna(0.0)

    # --- insentif per peran ---
    t['Insentif Store Leader'] = (t['Omzet Jasa Mati Total']
                                  * persen['Store Leader'] / 100.0)
    t['Insentif Supervisor'] = (t['Omzet Jasa Mati Total']
                                * persen['Supervisor'] / 100.0)
    t['Insentif Team'] = t['Omzet Jasa'] * persen['Team'] / 100.0

    t['FL Aksesoris'] = (t['Omzet Penjualan Aksesoris']
                         * persen['Front Liner Aksesoris'] / 100.0)
    if skema == NAMA_SKEMA_B:
        t['FL Laptop'] = t['Unit Penjualan Laptop'] * tarif_skema['Laptop']
        t['FL Handphone'] = t['Unit Penjualan Handphone'] * tarif_skema['Handphone']
    else:
        t['FL Laptop'] = (t['Omzet Penjualan Laptop']
                          * tarif_skema['Laptop'] / 100.0)
        t['FL Handphone'] = (t['Omzet Penjualan Handphone']
                             * tarif_skema['Handphone'] / 100.0)
    t['Insentif Front Liner'] = t[['FL Aksesoris', 'FL Laptop',
                                   'FL Handphone']].sum(axis=1)

    t['Total Insentif'] = t[KOL_INSENTIF].sum(axis=1)
    t['Gross Profit Awal'] = t['Omzet Total'] - t['HPP'] - t['Bagi Hasil Teknisi']
    t['Gross Profit Setelah Insentif'] = t['Gross Profit Awal'] - t['Total Insentif']
    t['Penurunan GP %'] = (t['Total Insentif']
                           / t['Gross Profit Awal'].replace(0, pd.NA) * 100).round(2)
    return t.sort_values('Total Insentif', ascending=False).reset_index(drop=True)


KOL_TAMPIL_INSENTIF = [
    'Cabang',
    'Omzet Jasa Mati Total', 'Insentif Store Leader', 'Insentif Supervisor',
    'Omzet Penjualan Aksesoris', 'FL Aksesoris',
    'Omzet Penjualan Laptop', 'Unit Penjualan Laptop', 'FL Laptop',
    'Omzet Penjualan Handphone', 'Unit Penjualan Handphone', 'FL Handphone',
    'Insentif Front Liner',
    'Omzet Jasa', 'Insentif Team',
    'Total Insentif',
    'Omzet Total', 'HPP', 'Bagi Hasil Teknisi',
    'Gross Profit Awal', 'Gross Profit Setelah Insentif', 'Penurunan GP %',
]


def buat_excel_insentif(tabel, keterangan, judul_periode):
    """Workbook satu sheet: rincian insentif per cabang."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        tabel.to_excel(writer, sheet_name='Insentif', index=False, startrow=3)
        ws = writer.sheets['Insentif']
        ws.cell(row=1, column=1,
                value=f'Insentif Non-Teknisi per Cabang — {judul_periode}'
                ).font = Font(bold=True, size=13, color='1F3864')
        ws.cell(row=2, column=1, value=keterangan).font = Font(
            size=9, italic=True, color='555555')
        n, k = len(tabel), len(tabel.columns)
        thin = Side(style='thin', color='D9D9D9')
        for j, kol in enumerate(tabel.columns, start=1):
            c = ws.cell(row=4, column=j)
            c.fill = PatternFill('solid', fgColor='1F3864')
            c.font = Font(bold=True, color='FFFFFF', size=10)
            c.alignment = Alignment(horizontal='center', vertical='center',
                                    wrap_text=True)
            lebar = max(len(str(kol)) + 2,
                        (tabel[kol].astype(str).str.len().max() if n else 0) + 2)
            ws.column_dimensions[get_column_letter(j)].width = min(max(lebar, 11), 24)
        uang = [j for j, kol in enumerate(tabel.columns, start=1)
                if str(kol).startswith(('Omzet', 'Insentif', 'FL ', 'Total',
                                        'Bagi', 'Gross', 'HPP'))]
        cacah = [j for j, kol in enumerate(tabel.columns, start=1)
                 if str(kol).startswith('Unit')]
        for i in range(n):
            r = 5 + i
            if i % 2 == 1:
                for j in range(1, k + 1):
                    ws.cell(row=r, column=j).fill = PatternFill('solid',
                                                                fgColor='F4F7FB')
            for j in uang + cacah:
                ws.cell(row=r, column=j).number_format = '#,##0'
            for j in range(1, k + 1):
                ws.cell(row=r, column=j).border = Border(bottom=thin)
        if n:
            rt = 5 + n
            ws.cell(row=rt, column=1, value='TOTAL')
            for j in range(1, k + 1):
                c = ws.cell(row=rt, column=j)
                c.font = Font(bold=True)
                c.fill = PatternFill('solid', fgColor='DCE6F1')
                c.border = Border(top=Side(style='medium', color='1F3864'))
            for j in uang + cacah:
                L = get_column_letter(j)
                c = ws.cell(row=rt, column=j, value=f'=SUM({L}5:{L}{rt-1})')
                c.number_format = '#,##0'
                c.font = Font(bold=True)
            ws.auto_filter.ref = f'A4:{get_column_letter(k)}{4 + n}'
        ws.freeze_panes = ws.cell(row=5, column=2)
    buf.seek(0)
    return buf.getvalue()


# Teknisi dengan kesepakatan tarif berbeda dari tarif umum.
# Kolom kosong (None) berarti mengikuti tarif umum untuk kualifikasi itu.
# Kelompok pertama: Interface 20%, Normal 20%, Mati Total 22%, Lainnya 20%,
# Promo ikut tarif umum.
# Jasa yang tidak ikut dihitung bagi hasil (dicocokkan pada NAMA BARANG).
POLA_JASA_DIKECUALIKAN = ['OPER GADGET']

# Sebagian cabang datanya belum memakai penamaan barang berkata kunci, sehingga
# kualifikasi dibaca dari kolom KERUSAKAN UTAMA + KATEGORI PENJUALAN.
CABANG_ACUAN_KERUSAKAN = ['CONDET']
KERUSAKAN_INTERFACE = ['BATERAI', 'SSD', 'RAM']
KERUSAKAN_NORMAL = ['FLEXIBEL', 'FLEXIBLE', 'FLEKSIBEL', 'MIC', 'WIFI CARD', 'REPAIR']


def label_dari_kerusakan(kerusakan, kategori_jual):
    """Kualifikasi dari KERUSAKAN UTAMA; LCD & SOFTWARE bergantung kategori jual."""
    ku = str(kerusakan or '').upper()
    kp = str(kategori_jual or '').upper()
    laptop = 'LAPTOP' in kp
    if 'MATI TOTAL' in ku:
        return 'Mati Total'
    if 'LCD' in ku:                       # HP -> Interface, laptop -> Normal
        return 'Normal' if laptop else ('Interface' if 'HP' in kp else 'Normal')
    if 'SOFTWARE' in ku:                  # laptop -> Interface, HP -> Normal
        return 'Interface' if laptop else 'Normal'
    if any(k in ku for k in KERUSAKAN_INTERFACE):
        return 'Interface'
    return 'Normal'                       # termasuk daftar KERUSAKAN_NORMAL


NAMA_TARIF_TETAP_20 = [
    'M IBNU SIDIK', 'RAFI ALAMSYAH', 'MIFTAHUL MUTTAQIEN', 'BRYAN PUTRA',
    'HAMZAH MAULANA', 'DAVID SONDAKH', 'FATHUR ROHMAN SOBARNA', 'ALAI ARKAN',
    'ALFIN DAMARA', 'ADI FIRDAUS', 'M IQBAL PRADANA', 'IRSYAD PANCA GUNAWAN',
    'EGI SETYA RAMADHANI', 'ALVI SYAHLANI RAMADHAN', 'M NAUFAL', 'M HANIF FATIN',
    'SYAHDAN IBNU FAUZI', 'JUNARA', 'KARIM AGAKHAN', 'FARID HASBY ASH SHIDDIQ',
    'RIDWAN KURNIAWAN',
]
NAMA_TARIF_NORMAL_355 = ['EKO RISDIYANTORO JATIMULYA', 'IRVAN SYAHRONI']
KOL_TARIF_KHUSUS = ['Nama Teknisi', 'Interface', 'Normal', 'Mati Total', 'Promo',
                    'Lainnya']


def tarif_khusus_awal():
    baris = [{'Nama Teknisi': n, 'Interface': 20.0, 'Normal': 20.0,
              'Mati Total': 22.0, 'Promo': None, 'Lainnya': 20.0}
             for n in NAMA_TARIF_TETAP_20]
    baris += [{'Nama Teknisi': n, 'Interface': None, 'Normal': 35.5,
               'Mati Total': 37.5, 'Promo': None, 'Lainnya': None}
              for n in NAMA_TARIF_NORMAL_355]
    return pd.DataFrame(baris, columns=KOL_TARIF_KHUSUS)


def _nama_rapi(s):
    return re.sub(r'\s+', ' ', str(s).strip().upper())


def peta_tarif_khusus(tabel):
    """DataFrame editor -> {nama_rapi: {label_kualifikasi: pecahan tarif}}."""
    hasil = {}
    if tabel is None or len(tabel) == 0:
        return hasil
    for _, r in tabel.iterrows():
        nama = _nama_rapi(r.get('Nama Teknisi', ''))
        if not nama or nama == 'NAN':
            continue
        tar = {}
        for lbl in KATEGORI_TARIF:
            v = r.get(lbl)
            if v is not None and not pd.isna(v):
                tar[lbl] = float(v) / 100.0
        if tar:
            hasil[nama] = tar
    return hasil


def cocokkan_teknisi(nama_teknisi, kunci_khusus):
    """Nama di data sering berakhiran nama cabang ('IRVAN SYAHRONI CINERE').

    Dicocokkan sama persis atau lewat awalan; kunci terpanjang menang.
    """
    peta = {}
    kunci = sorted(kunci_khusus, key=len, reverse=True)
    for t in nama_teknisi:
        tn = _nama_rapi(t)
        for k in kunci:
            if tn == k or tn.startswith(k + ' '):
                peta[t] = k
                break
    return peta

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
    """Gaji bulan M dihitung dari 24 bulan (M-1) s/d 23 bulan M.

    Contoh: gaji Juli 2026 -> 24 Juni 2026 s/d 23 Juli 2026.
    """
    m_akhir, th_akhir = bulan_gaji, tahun_gaji
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
                  'QTY', 'TOTAL HARGA']          # CABANG boleh datang dari nama berkas
KOLOM_DIPAKAI = SALES_REQUIRED + ['CABANG', 'NAMA TEKNISI', 'NAMA TEKNISI (FINAL)',
                                 'KERUSAKAN UTAMA', 'KATEGORI PENJUALAN',
                                 'HARGA BELI']
# NO FAKTUR hanya unik DI DALAM satu cabang (nomor MF-FP.xxxx dipakai ulang di
# cabang lain), jadi kunci duplikat wajib menyertakan CABANG. Baris kembar di
# dalam satu berkas tetap dipertahankan — yang dibuang hanya kiriman ulang.
KUNCI_DUPLIKAT = ['CABANG', 'NO FAKTUR', 'NAMA BARANG', 'QTY', 'TOTAL HARGA',
                  'TGL FAKTUR']
LABEL_TANPA_CABANG = '(TANPA CABANG)'

# Daftar cabang resmi. Nama berkas kiriman cabang biasanya terpotong
# (mis. "001mflashklende") sehingga dicocokkan lewat awalan ke daftar ini.
CABANG_KANONIK = [
    'BINTARA', 'CEGER', 'CIBINONG', 'CIBUBUR', 'CIKAMPEK', 'CILANGKAP', 'CINERE',
    'CONDET', 'DRAMAGA', 'JATIBENING', 'JATIMULYA', 'JATIWARINGIN', 'KARAWANG',
    'KLENDER', 'PEJATEN', 'RADJIMAN', 'SAWANGAN', 'WARBONG',
]
# nama lain -> nama cabang resmi
ALIAS_CABANG_AWAL = {'TELUK JAMBE': 'KARAWANG', 'TELUKJAMBE': 'KARAWANG'}

# potongan kata yang dibuang saat menebak nama cabang
NOISE_NAMA = {
    'RINCIAN', 'PENJUALAN', 'PENJUALANAN', 'DATA', 'LAPORAN', 'LAP', 'REKAP', 'JASA',
    'SALES', 'FAKTUR', 'INVOICE', 'PERIODE', 'BULAN', 'TAHUN', 'CABANG', 'CAB',
    'BRANCH', 'TOKO', 'FIX', 'FINAL', 'REVISI', 'REV', 'UPDATE', 'BARU', 'NEW',
    'COPY', 'SALINAN', 'XLSX', 'XLS', 'CSV', 'GZ', 'FILE', 'BAGI', 'HASIL',
    'TEKNISI', 'OK', 'SHEET', 'LEMBAR', 'TABEL', 'TABLE',
    'JANUARI', 'FEBRUARI', 'MARET', 'APRIL', 'MEI', 'JUNI', 'JULI', 'AGUSTUS',
    'SEPTEMBER', 'OKTOBER', 'NOVEMBER', 'DESEMBER',
    'JAN', 'FEB', 'MAR', 'APR', 'JUN', 'JUL', 'AGU', 'AGS', 'SEP', 'OKT', 'NOV', 'DES',
}
AWALAN_BUANG = ('MFLASH', 'MFLSH', 'MFLAS')      # kode perusahaan di nama berkas


def _huruf(s) -> str:
    return re.sub(r'[^A-Z]', '', str(s).upper())


def token_cabang(nama: str) -> str:
    """Sisa nama berkas/sheet setelah angka, kode, dan kata umum dibuang.

    'rincian_faktur_penjualan_001mflashklende_260818094705.xlsx' -> 'KLENDE'
    'Rincian Faktur Penjualan' -> '' (semuanya kata umum)
    """
    s = re.sub(r'\.(xlsx|xlsm|xls|csv|gz|txt)$', '', str(nama), flags=re.I)
    s = re.sub(r'\.(csv|xlsx)$', '', s, flags=re.I)              # untuk nama .csv.gz
    s = re.sub(r'\b[0-9a-f]{8,}\b', ' ', s, flags=re.I)          # buang kode acak/uuid
    s = re.sub(r'[\_\-\.\(\)\[\]#]+', ' ', s)
    s = re.sub(r'\d+', ' ', s)
    tok = []
    for t in s.upper().split():
        for aw in AWALAN_BUANG:
            if t.startswith(aw) and len(t) > len(aw):
                t = t[len(aw):]
        if len(t) > 1 and t not in NOISE_NAMA:
            tok.append(t)
    return ' '.join(tok).strip()


def cocokkan_cabang(tok: str, kanonik, alias):
    """Cocokkan token ke daftar cabang resmi. -> (nama_cabang, keterangan)."""
    t = _huruf(tok)
    if not t:
        return '', ''
    for a, v in alias.items():
        A = _huruf(a)
        if A and (A.startswith(t) or t.startswith(A)):
            return v, f'alias {a}'
    kandidat = [c for c in kanonik
                if _huruf(c).startswith(t) or t.startswith(_huruf(c))]
    if len(kandidat) == 1:
        return kandidat[0], ''
    if len(kandidat) > 1:
        return tok.upper(), 'ambigu: ' + '/'.join(kandidat)
    return tok.upper(), 'di luar daftar'


def _cabang_dari_kolom(d: pd.DataFrame) -> str:
    """Kalau kolom CABANG terisi seragam, pakai itu. '' kalau kosong/beragam."""
    if 'CABANG' not in d.columns:
        return ''
    v = d['CABANG'].dropna().astype(str).str.strip()
    v = v[(v != '') & (~v.str.upper().isin(['NAN', 'NONE']))]
    if v.empty:
        return ''
    return '' if v.nunique() > 1 else v.iloc[0]     # beragam -> biarkan apa adanya


def _potongan_berkas(nama_berkas: str, isi: bytes):
    """Pecah satu berkas jadi (DataFrame, nama_bagian). Bagian = sheet untuk xlsx."""
    low = str(nama_berkas).lower()
    if low.endswith('.gz'):
        yield pd.read_csv(io.BytesIO(isi), compression='gzip'), ''
    elif low.endswith(('.csv', '.txt')):
        yield pd.read_csv(io.BytesIO(isi)), ''
    else:
        xls = None
        for mesin in ('calamine', 'openpyxl'):      # calamine jauh lebih cepat
            try:
                xls = pd.ExcelFile(io.BytesIO(isi), engine=mesin)
                break
            except Exception:                        # noqa: BLE001, PERF203
                continue
        if xls is None:
            xls = pd.ExcelFile(io.BytesIO(isi), engine='openpyxl')
        for sheet in xls.sheet_names:
            d = xls.parse(sheet)
            if not d.empty:
                yield d, sheet


@st.cache_data(show_spinner="Membaca berkas penjualan...")
def baca_mentah(items: tuple, kanonik: tuple, alias_items: tuple,
                versi_data: int = VERSI_DATA):
    """Gabung banyak berkas jadi satu tabel mentah + catatan asal tiap potongan.

    items: tuple of (nama_berkas, bytes). Nama cabang dicari berurutan:
    kolom CABANG -> nama sheet -> nama berkas -> kosong (diisi manual di sidebar).
    """
    alias = dict(alias_items)
    frames, catatan, gagal = [], [], []
    for nama_berkas, isi in items:
        try:
            potongan = list(_potongan_berkas(nama_berkas, isi))
        except Exception as e:                                   # noqa: BLE001
            gagal.append(f"{nama_berkas}: {e}")
            continue
        if not potongan:
            gagal.append(f"{nama_berkas}: tidak ada baris data")
            continue
        for d, bagian in potongan:
            kurang = [c for c in SALES_REQUIRED if c not in d.columns]
            if kurang:
                gagal.append(f"{nama_berkas}"
                             + (f" [{bagian}]" if bagian else "")
                             + ": kolom tidak ditemukan — " + ", ".join(kurang))
                continue
            d = d[[c for c in KOLOM_DIPAKAI if c in d.columns]].copy()   # hemat memori

            cab, asal, ket = _cabang_dari_kolom(d), 'kolom CABANG', ''
            if not cab and bagian:
                tok = token_cabang(bagian)
                if tok:
                    cab, ket = cocokkan_cabang(tok, kanonik, alias)
                    asal = 'nama sheet'
            if not cab:
                tok = token_cabang(nama_berkas)
                if tok:
                    cab, ket = cocokkan_cabang(tok, kanonik, alias)
                    asal = 'nama berkas'
            beragam = ('CABANG' in d.columns) and bool(d['CABANG'].notna().any())
            if not cab:
                asal, ket = ('kolom CABANG', 'beragam per baris') if beragam \
                    else ('—', 'perlu diisi manual')

            if cab:
                d['CABANG'] = cab
            elif not beragam:
                d['CABANG'] = pd.NA
            d['__BERKAS__'] = nama_berkas
            d['__BAGIAN__'] = bagian
            frames.append(d)
            catatan.append({
                'Berkas': nama_berkas, 'Bagian': bagian or '—',
                'Cabang': cab if cab else ('(beragam)' if beragam else LABEL_TANPA_CABANG),
                'Dari': asal, 'Catatan': ket, 'Baris': len(d)})
    if not frames:
        return pd.DataFrame(), pd.DataFrame(catatan), gagal
    return (pd.concat(frames, ignore_index=True, sort=False),
            pd.DataFrame(catatan), gagal)


# isian yang dianggap kosong pada kolom nama teknisi
NAMA_KOSONG = {'', '-', '--', 'NAN', 'NONE', 'NULL', '<NA>', 'N/A', 'NA', '#N/A',
               'N.A', 'N.A.', '#VALUE!', '#REF!', 'TIDAK ADA'}


def _nama_teknisi_bersih(kolom: pd.Series) -> pd.Series:
    s = (kolom.astype(str).str.replace(r'\s+', ' ', regex=True)
         .str.strip().str.upper().fillna(''))
    return s.where(~s.isin(NAMA_KOSONG), '')


def bersihkan(df: pd.DataFrame) -> pd.DataFrame:
    """Normalisasi kolom + saring baris kategori JASA (dipakai semua sumber data)."""
    if df.empty:
        return df
    df = df.copy()
    for c in ['QTY', 'TOTAL HARGA', 'HARGA BELI']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
        else:
            df[c] = 0.0
    df['TGL'] = pd.to_datetime(df['TGL FAKTUR'], errors='coerce')
    df['KATEGORI'] = df['KATEGORI BARANG'].astype(str).str.strip().str.upper()
    df['BARANG'] = df['NAMA BARANG'].astype(str).str.strip()
    df['CABANG'] = (df['CABANG'].astype(str).str.strip()
                    .replace({'': LABEL_TANPA_CABANG, 'nan': LABEL_TANPA_CABANG,
                              'NaN': LABEL_TANPA_CABANG, 'None': LABEL_TANPA_CABANG})
                    .fillna(LABEL_TANPA_CABANG))

    fin = df['NAMA TEKNISI (FINAL)'] if 'NAMA TEKNISI (FINAL)' in df.columns \
        else pd.Series(index=df.index, dtype=object)
    asli = df['NAMA TEKNISI'] if 'NAMA TEKNISI' in df.columns \
        else pd.Series(index=df.index, dtype=object)
    # acuan utama kolom FINAL; kalau kosong / N/A baru pakai NAMA TEKNISI
    tek = _nama_teknisi_bersih(fin)
    df['TEKNISI'] = tek.where(tek != '', _nama_teknisi_bersih(asli))
    df.loc[df['TEKNISI'] == '', 'TEKNISI'] = 'TIDAK ADA TEKNISI'

    df = df.copy()
    for asal, baru in [('KERUSAKAN UTAMA', 'KERUSAKAN'),
                       ('KATEGORI PENJUALAN', 'KAT_JUAL')]:
        df[baru] = (df[asal].astype(str).str.replace(r'\s+', ' ', regex=True)
                    .str.strip().str.upper().fillna('')
                    if asal in df.columns else '')
        df.loc[df[baru].isin(['NAN', 'NONE', '<NA>']), baru] = ''
    df['KW_MATCH'] = df['BARANG'].map(lambda s: '|'.join(cocok_kata_kunci(s)))
    return df                       # semua kategori; penyaringan JASA di pemanggil


def hanya_jasa(df: pd.DataFrame) -> pd.DataFrame:
    return df[df['KATEGORI'] == 'JASA'].copy() if len(df) else df


@st.cache_data(show_spinner="Membaca data penjualan...")
def load_sales(file_bytes: bytes, source_kind: str,
               versi_data: int = VERSI_DATA) -> pd.DataFrame:
    """Loader berkas tunggal (dipakai untuk data bawaan repo)."""
    nama = {'csv_gz': 'penjualan.csv.gz', 'csv': 'penjualan.csv'}.get(source_kind, 'penjualan.xlsx')
    mentah, _, gagal = baca_mentah(((nama, file_bytes),), tuple(CABANG_KANONIK),
                                   tuple(ALIAS_CABANG_AWAL.items()))
    if mentah.empty:
        raise ValueError(gagal[0] if gagal else "tidak ada baris data")
    return bersihkan(mentah)


# ---------------------------------------------------------------------------
# Sidebar: sumber data (mendukung banyak berkas — satu kiriman per cabang)
# ---------------------------------------------------------------------------
st.sidebar.caption(f"**{VERSI_APP}** — versi pengembangan")
st.sidebar.title("📁 Sumber Data")
ups = st.sidebar.file_uploader(
    "Upload data penjualan — bisa banyak berkas sekaligus",
    type=['xlsx', 'xlsm', 'gz', 'csv'], accept_multiple_files=True,
    key='uploader_cabang',
    help="Kirim satu berkas per cabang (boleh 25 sekaligus), atau satu berkas gabungan. "
         "Kalau kosong, dipakai berkas bawaan data/penjualan.csv.gz.")

buang_duplikat = st.sidebar.checkbox(
    "Buang kiriman ulang (duplikat antar berkas)", value=True, key='opsi_dedup',
    help="Kalau satu cabang mengirim berkas dua kali, baris yang sama persis "
         "(cabang, no faktur, barang, qty, total, tanggal) hanya dihitung sekali — "
         "yang dipakai berkas terakhir. Baris kembar di dalam satu berkas tetap utuh.")

with st.sidebar.expander("🏷️ Daftar & alias cabang", expanded=False):
    st.caption("Nama berkas kiriman cabang sering terpotong (mis. `001mflashklende`). "
               "Potongan itu dicocokkan ke daftar di bawah lewat awalan nama.")
    st.session_state.setdefault('teks_kanonik', "\n".join(CABANG_KANONIK))
    st.session_state.setdefault(
        'teks_alias', "\n".join(f"{k} = {v}" for k, v in ALIAS_CABANG_AWAL.items()))
    teks_kanonik = st.text_area(
        "Daftar cabang resmi (satu per baris)", height=150, key='teks_kanonik')
    teks_alias = st.text_area(
        "Alias — format `nama lain = CABANG RESMI`", height=100, key='teks_alias',
        help="Contoh: TELUK JAMBE = KARAWANG")

kanonik = tuple(dict.fromkeys(
    b.strip().upper() for b in teks_kanonik.splitlines() if b.strip()))
alias_items = tuple(
    (a.strip().upper(), b.strip().upper())
    for a, _, b in (baris.partition('=') for baris in teks_alias.splitlines())
    if a.strip() and b.strip())

jasa_all = pd.DataFrame()
data_all = pd.DataFrame()
catatan_berkas = pd.DataFrame()
try:
    if ups:
        items = tuple((u.name, u.getvalue()) for u in ups)
        mentah, catatan_berkas, gagal = baca_mentah(
            items, kanonik, alias_items, VERSI_DATA)
        mentah = mentah.copy()

        # --- koreksi manual untuk potongan yang cabangnya tak terdeteksi ---
        if not mentah.empty and not catatan_berkas.empty:
            perlu = catatan_berkas['Cabang'] == LABEL_TANPA_CABANG
            if perlu.any():
                st.sidebar.warning(f"{int(perlu.sum())} berkas belum ketahuan cabangnya — "
                                   "isi manual di bawah.")
                with st.sidebar.form('form_cabang'):
                    isian = {}
                    for _, r in catatan_berkas[perlu].iterrows():
                        label = r['Berkas'] + (f" [{r['Bagian']}]" if r['Bagian'] != '—' else '')
                        isian[(r['Berkas'], r['Bagian'])] = st.text_input(
                            f"Cabang untuk {label}", key=f"cab_{label}")
                    if st.form_submit_button("Terapkan nama cabang"):
                        st.session_state['peta_cabang'] = {
                            k: v.strip().upper() for k, v in isian.items() if v.strip()}
                        st.rerun()
            for (berkas, bagian), cab in st.session_state.get('peta_cabang', {}).items():
                m = (mentah['__BERKAS__'] == berkas) & \
                    (mentah['__BAGIAN__'] == ('' if bagian == '—' else bagian))
                mentah.loc[m, 'CABANG'] = cab
                mc = (catatan_berkas['Berkas'] == berkas) & (catatan_berkas['Bagian'] == bagian)
                catatan_berkas.loc[mc, 'Cabang'] = cab
                catatan_berkas.loc[mc, 'Dari'] = 'isian manual'
                catatan_berkas.loc[mc, 'Catatan'] = ''

        n_dup = 0
        if not mentah.empty and buang_duplikat and \
                all(c in mentah.columns for c in KUNCI_DUPLIKAT):
            sebelum = len(mentah)
            # nomor urut kemunculan di dalam satu berkas -> baris kembar yang memang
            # ada di berkas aslinya tidak ikut terbuang, hanya kiriman ulang
            mentah['__N__'] = mentah.groupby(KUNCI_DUPLIKAT + ['__BERKAS__'],
                                             dropna=False).cumcount()
            mentah = mentah.drop_duplicates(subset=KUNCI_DUPLIKAT + ['__N__'],
                                            keep='last').drop(columns='__N__')
            n_dup = sebelum - len(mentah)

        data_all = bersihkan(mentah)
        jasa_all = hanya_jasa(data_all)
        if gagal:
            st.sidebar.error("Berkas dilewati:\n\n- " + "\n- ".join(gagal))
        if not jasa_all.empty:
            st.sidebar.success(
                f"{len(ups)} berkas · {jasa_all['CABANG'].nunique()} cabang · "
                f"{len(jasa_all):,} baris jasa"
                + (f" · {n_dup:,} baris duplikat dibuang" if n_dup else ""))
    elif DEFAULT_SALES_PATH.exists():
        data_all = load_sales(DEFAULT_SALES_PATH.read_bytes(), 'csv_gz',
                              VERSI_DATA)
        jasa_all = hanya_jasa(data_all)
        st.sidebar.info("Memakai data bawaan repo.")
except Exception as e:  # noqa: BLE001
    st.sidebar.error(f"Data tidak terbaca: {e}")

if not catatan_berkas.empty:
    with st.sidebar.expander(f"📄 Rincian {len(catatan_berkas)} berkas terbaca", expanded=True):
        st.dataframe(catatan_berkas, hide_index=True, use_container_width=True)
        ragu = catatan_berkas[catatan_berkas['Catatan'].astype(str).str.startswith(
            ('ambigu', 'di luar daftar', 'perlu'))]
        if len(ragu):
            st.warning("Periksa berkas ini — nama cabangnya belum pasti: "
                       + ", ".join(ragu['Berkas'].astype(str)))
        ganda = (catatan_berkas[catatan_berkas['Cabang'] != LABEL_TANPA_CABANG]
                 .groupby('Cabang')['Berkas'].nunique())
        ganda = ganda[ganda > 1]
        if len(ganda):
            st.info("Cabang dengan lebih dari satu berkas: " + ", ".join(ganda.index))

st.title(f"🧰 Bagi Hasil Teknisi · {VERSI_APP}")

for _kol in ['QTY', 'TOTAL HARGA', 'HARGA BELI']:
    for _df in (data_all, jasa_all):
        if len(_df) and _kol not in _df.columns:
            _df[_kol] = 0.0

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

    st.divider()
    st.markdown("**Pengecualian & acuan kualifikasi**")
    k1, k2 = st.columns(2)
    with k1:
        teks_kecuali = st.text_area(
            "Jasa yang tidak dihitung (satu pola per baris)",
            value="\n".join(POLA_JASA_DIKECUALIKAN), height=90, key='teks_kecuali',
            help="Dicocokkan pada NAMA BARANG, tidak peduli huruf besar/kecil. "
                 "Baris jasa yang cocok dikeluarkan dari seluruh perhitungan.")
    with k2:
        cab_kerusakan = st.multiselect(
            "Cabang yang kualifikasinya dibaca dari KERUSAKAN UTAMA",
            options=sorted(jasa_all['CABANG'].dropna().unique().tolist()),
            default=[c for c in CABANG_ACUAN_KERUSAKAN
                     if c in set(jasa_all['CABANG'].dropna())],
            key='cab_kerusakan',
            help="Untuk cabang yang penamaan barangnya belum memakai kata kunci. "
                 "Interface: LCD (service HP), baterai, SSD, RAM, software (service "
                 "laptop). Mati Total: kerusakan 'mati total'. Sisanya Normal.")

    st.divider()
    st.markdown("**Perubahan tarif Mati Total berjangka**")
    st.caption(
        "Mulai tanggal yang dipilih, tarif Mati Total memakai angka baru. "
        "Penerapannya per **TGL FAKTUR**, jadi satu periode gaji yang terbelah "
        "tanggal berlakunya terhitung proporsional dengan sendirinya. Selisih "
        "poinnya juga ditambahkan ke teknisi bertarif khusus.")
    m1, m2, m3 = st.columns([1, 1.1, 1.6])
    with m1:
        pakai_mt_baru = st.checkbox("Aktifkan", value=True, key='mt_aktif')
    with m2:
        tgl_mt_baru = st.date_input(
            "Berlaku sejak", value=TGL_MT_BARU_AWAL, key='mt_tgl',
            format="DD/MM/YYYY")
    with m3:
        tarif_mt_baru = st.number_input(
            "Mati Total sejak tanggal itu (%)", 0.0, 100.0, TARIF_MT_BARU_AWAL,
            0.5, key='mt_tarif')

    if st.button("↩️ Kembalikan ke tarif awal", key='t_reset'):
        for k, v in [('t_int', 'Interface'), ('t_nor', 'Normal'),
                     ('t_mat', 'Mati Total'), ('t_pro', 'Promo')]:
            st.session_state[k] = TARIF_AWAL[v]
        st.session_state['t_lain'] = TARIF_DEFAULT_AWAL
        st.session_state['t_flat'] = TARIF_PEMBANDING_AWAL
        st.session_state['t_prio'] = 'Normal'
        st.session_state['mt_aktif'] = True
        st.session_state['mt_tgl'] = TGL_MT_BARU_AWAL
        st.session_state['mt_tarif'] = TARIF_MT_BARU_AWAL
        st.session_state['tabel_khusus'] = tarif_khusus_awal()
        st.session_state.pop('ed_khusus', None)
        st.rerun()

with st.expander("👥 Tarif Khusus per Teknisi — tambah, ubah, atau hapus di sini",
                 expanded=False):
    st.caption(
        "Teknisi di tabel ini memakai persentase sendiri; kolom yang dikosongkan "
        "ikut tarif umum. Nama dicocokkan sama persis atau lewat awalan, jadi "
        "`IRVAN SYAHRONI` juga kena untuk `IRVAN SYAHRONI CINERE`.")
    st.caption(
        "**Menambah:** ketik di baris kosong paling bawah. "
        "**Menghapus:** klik nomor baris di kiri untuk memilihnya, lalu tekan "
        "tombol 🗑️ yang muncul di kanan atas tabel (atau tombol Delete di keyboard).")

    if 'tabel_khusus' not in st.session_state:
        st.session_state['tabel_khusus'] = tarif_khusus_awal()

    tabel_khusus = st.data_editor(
        st.session_state['tabel_khusus'], key='ed_khusus', num_rows='dynamic',
        use_container_width=True, hide_index=False, height=420,
        column_config={
            'Nama Teknisi': st.column_config.TextColumn(
                width='medium', required=False),
            **{lbl: st.column_config.NumberColumn(
                f'{lbl} (%)', min_value=0.0, max_value=100.0, step=0.5,
                format='%.1f', help="Kosongkan untuk mengikuti tarif umum.")
               for lbl in KATEGORI_TARIF}})

    n_terisi = int(tabel_khusus['Nama Teknisi'].astype(str).str.strip().ne('').sum()) \
        if len(tabel_khusus) else 0
    st.caption(f"{n_terisi} teknisi terdaftar di daftar tarif khusus.")

    b1, b2, b3 = st.columns([1.1, 1.4, 1])
    with b1:
        st.download_button(
            "⬇️ Unduh daftar (CSV)",
            data=tabel_khusus.to_csv(index=False).encode('utf-8-sig'),
            file_name="tarif_khusus_teknisi.csv", mime="text/csv",
            use_container_width=True, key='unduh_khusus')
    with b2:
        naik_khusus = st.file_uploader(
            "Ganti daftar dari CSV", type=['csv'], key='up_khusus',
            label_visibility='collapsed',
            help="Berkas CSV dengan kolom Nama Teknisi, Interface, Normal, "
                 "Mati Total, Promo, Lainnya.")
        if naik_khusus is not None and st.button("📥 Terapkan CSV",
                                                 use_container_width=True,
                                                 key='terap_khusus'):
            try:
                baru = pd.read_csv(naik_khusus)
                kurang = [k for k in KOL_TARIF_KHUSUS if k not in baru.columns]
                if kurang:
                    st.error("Kolom tidak ada: " + ", ".join(kurang))
                else:
                    st.session_state['tabel_khusus'] = baru[KOL_TARIF_KHUSUS]
                    st.session_state.pop('ed_khusus', None)
                    st.rerun()
            except Exception as e:                              # noqa: BLE001
                st.error(f"CSV tidak terbaca: {e}")
    with b3:
        if st.button("↩️ Daftar awal", use_container_width=True,
                     key='reset_khusus'):
            st.session_state['tabel_khusus'] = tarif_khusus_awal()
            st.session_state.pop('ed_khusus', None)
            st.rerun()


urutan = [prioritas.upper()] + [k for k in KATA_KUNCI_TARIF if k != prioritas.upper()]
peta_tarif = {k: v / 100.0 for k, v in tarif_input.items()}
peta_tarif[LABEL_LAINNYA] = tarif_lain / 100.0

jasa_all = jasa_all.copy()

pola_kecuali = [p.strip().upper() for p in teks_kecuali.splitlines() if p.strip()]
n_kecuali, omzet_kecuali = 0, 0.0
if pola_kecuali:
    b = jasa_all['BARANG'].astype(str).str.upper()
    buang = pd.Series(False, index=jasa_all.index)
    for p in pola_kecuali:
        buang |= b.str.contains(re.escape(p), regex=True, na=False)
    n_kecuali = int(buang.sum())
    omzet_kecuali = float(jasa_all.loc[buang, 'TOTAL HARGA'].sum())
    jasa_all = jasa_all[~buang].copy()

jasa_all['TARIF_LABEL'] = jasa_all['KW_MATCH'].map(lambda s: pilih_label_tarif(s, urutan))
if cab_kerusakan and 'KERUSAKAN' in jasa_all.columns:
    m_ker = jasa_all['CABANG'].isin(cab_kerusakan)
    if m_ker.any():
        jasa_all.loc[m_ker, 'TARIF_LABEL'] = [
            label_dari_kerusakan(k, p) for k, p in
            zip(jasa_all.loc[m_ker, 'KERUSAKAN'], jasa_all.loc[m_ker, 'KAT_JUAL'])]
jasa_all['TARIF'] = jasa_all['TARIF_LABEL'].map(peta_tarif).fillna(0.0)

khusus = peta_tarif_khusus(tabel_khusus)
peta_nama = cocokkan_teknisi(jasa_all['TEKNISI'].unique(), khusus.keys())
jasa_all['TARIF_KHUSUS'] = jasa_all['TEKNISI'].map(peta_nama)
n_khusus = 0
for kunci, tar in khusus.items():
    for lbl, frac in tar.items():
        m = (jasa_all['TARIF_KHUSUS'] == kunci) & (jasa_all['TARIF_LABEL'] == lbl)
        jasa_all.loc[m, 'TARIF'] = frac
        n_khusus += int(m.sum())
tanpa_padanan = sorted(set(khusus) - set(peta_nama.values()))

# --- perubahan tarif Mati Total sejak tanggal tertentu -----------------------
# Ditambahkan sebagai SELISIH POIN supaya teknisi bertarif khusus ikut naik
# dengan besaran yang sama (mis. 22% -> 25%, 37,5% -> 40,5% saat 32% -> 35%).
delta_mt = 0.0
n_mt_baru = 0
if pakai_mt_baru:
    delta_mt = (tarif_mt_baru - tarif_input['Mati Total']) / 100.0
    batas_mt = pd.Timestamp(tgl_mt_baru)
    m_mt = ((jasa_all['TARIF_LABEL'] == 'Mati Total')
            & (jasa_all['TGL'] >= batas_mt))
    n_mt_baru = int(m_mt.sum())
    if n_mt_baru and abs(delta_mt) > 1e-12:
        jasa_all.loc[m_mt, 'TARIF'] = jasa_all.loc[m_mt, 'TARIF'] + delta_mt

jasa_all['BAGI_HASIL'] = jasa_all['TOTAL HARGA'] * jasa_all['TARIF']
jasa_all['FLAT'] = jasa_all['TOTAL HARGA'] * (tarif_flat / 100.0)

st.caption(
    "**Tarif aktif:** " +
    " · ".join(f"{k} {v:.0f}%" for k, v in tarif_input.items()) +
    f" · Lainnya {tarif_lain:.0f}% · pembanding flat {tarif_flat:.0f}%"
    f" · prioritas bentrok: {prioritas}"
)
if pakai_mt_baru and abs(delta_mt) > 1e-12:
    st.caption(
        f"**Tarif Mati Total sejak {pd.Timestamp(tgl_mt_baru):%d %B %Y}:** "
        f"{tarif_input['Mati Total']:.1f}% → **{tarif_mt_baru:.1f}%** "
        f"({delta_mt*100:+.1f} poin, ikut menaikkan tarif khusus) · "
        f"mempengaruhi {n_mt_baru:,} baris Mati Total. Faktur sebelum tanggal itu "
        "tetap memakai tarif lama.")
if n_kecuali:
    st.caption(f"**Dikecualikan:** {n_kecuali:,} baris jasa "
               f"({', '.join(pola_kecuali)}) senilai {rp(omzet_kecuali)} "
               "tidak ikut dihitung.")
if cab_kerusakan:
    st.caption("**Acuan KERUSAKAN UTAMA** dipakai untuk cabang: "
               + ", ".join(cab_kerusakan) + ".")
if khusus:
    st.caption(
        f"**Tarif khusus:** {len(khusus)} teknisi terdaftar, "
        f"{len(set(peta_nama.values()))} ketemu di data dan mempengaruhi "
        f"{n_khusus:,} baris jasa."
        + (f" Belum ada padanannya di data: {', '.join(tanpa_padanan)}."
           if tanpa_padanan else ""))

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


# --- pemilih tab & pengaturan insentif non-teknisi ---
tab_aktif = st.sidebar.radio(
    "🗂️ Tab", ['Bagi Hasil Teknisi', 'Insentif Store Leader, SPV & Front Liner'],
    key='tab_aktif')

with st.sidebar.expander("💼 Pengaturan insentif", expanded=False):
    st.caption("Dipakai di tab insentif.")
    persen_insentif = {}
    persen_insentif['Store Leader'] = st.number_input(
        "Store Leader (%)", 0.0, 100.0, PERSEN_INSENTIF_AWAL['Store Leader'], 0.5,
        key='ins_sl', help="Dari omzet jasa berkualifikasi Mati Total.")
    persen_insentif['Supervisor'] = st.number_input(
        "Supervisor (%)", 0.0, 100.0, PERSEN_INSENTIF_AWAL['Supervisor'], 0.5,
        key='ins_spv', help="Dari omzet jasa berkualifikasi Mati Total.")
    persen_insentif['Team'] = st.number_input(
        "Team (%)", 0.0, 100.0, PERSEN_INSENTIF_AWAL['Team'], 0.5,
        key='ins_team', help="Dari seluruh omzet jasa cabang.")

    st.divider()
    st.markdown("**Front Liner** (Admin & sales retail)")
    persen_insentif['Front Liner Aksesoris'] = st.number_input(
        "Penjualan aksesoris (%)", 0.0, 100.0,
        PERSEN_INSENTIF_AWAL['Front Liner Aksesoris'], 0.5, key='ins_fl_aks')
    skema_fl = st.radio(
        "Skema laptop & handphone", [NAMA_SKEMA_A, NAMA_SKEMA_B], key='skema_fl',
        help="Kategori 1 memakai persentase omzet, Kategori 2 memakai nominal "
             "tetap per unit terjual.")
    tarif_skema = {}
    if skema_fl == NAMA_SKEMA_A:
        tarif_skema['Laptop'] = st.number_input(
            "Penjualan laptop (%)", 0.0, 100.0, SKEMA_A_AWAL['Laptop'], 0.5,
            key='ins_fl_lap_p')
        tarif_skema['Handphone'] = st.number_input(
            "Penjualan handphone (%)", 0.0, 100.0, SKEMA_A_AWAL['Handphone'], 0.5,
            key='ins_fl_hp_p')
    else:
        tarif_skema['Laptop'] = st.number_input(
            "Penjualan laptop (Rp / unit)", 0.0, 10_000_000.0,
            SKEMA_B_AWAL['Laptop'], 5_000.0, key='ins_fl_lap_n')
        tarif_skema['Handphone'] = st.number_input(
            "Penjualan handphone (Rp / unit)", 0.0, 10_000_000.0,
            SKEMA_B_AWAL['Handphone'], 5_000.0, key='ins_fl_hp_n')

if skema_fl == NAMA_SKEMA_A:
    ket_skema = (f"laptop {tarif_skema['Laptop']:g}% · handphone "
                 f"{tarif_skema['Handphone']:g}%")
else:
    ket_skema = (f"laptop {rp(tarif_skema['Laptop'], False)}/unit · handphone "
                 f"{rp(tarif_skema['Handphone'], False)}/unit")
ket_insentif = (f"Store Leader {persen_insentif['Store Leader']:g}% · Supervisor "
                f"{persen_insentif['Supervisor']:g}% · Team "
                f"{persen_insentif['Team']:g}% · Front Liner: aksesoris "
                f"{persen_insentif['Front Liner Aksesoris']:g}%, {ket_skema}")


if tab_aktif == 'Bagi Hasil Teknisi':
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
    # Unduhan Excel (multi-sheet: rekap + satu sheet per cabang)
    # ---------------------------------------------------------------------------
    KATEGORI_ORDER = ['Interface', 'Normal', 'Mati Total', 'Promo', LABEL_LAINNYA]

    # Kolom penggajian pada sheet per cabang. Sembilan kolom potongan dikosongkan
    # untuk diisi finance; sisanya berisi rumus Excel yang ikut menyesuaikan.
    KOLOM_POTONGAN = ['Potongan Refund', 'Potongan AR', 'Potongan Kasbon', 'Keterlambatan',
                      'Potongan Minus Audit', 'Potongan Audit Compliance',
                      'Biaya Pendaftaran Koperasi', 'Simpanan Pokok', 'Simpanan Wajib']
    KOLOM_CADANGAN = ['Cadangan 7 Tahun / bulan', 'Cadangan 7 Tahun']
    KOLOM_RUMUS = ['Total Potongan', 'Gaji Teknisi', 'Nett Bagi hasil',
                   'Total Cadangan 7 Tahun']
    KOLOM_GAJI = (KOLOM_POTONGAN + ['Total Potongan', 'Gaji Teknisi', 'Nett Bagi hasil']
                  + KOLOM_CADANGAN + ['Total Cadangan 7 Tahun'])


    def _sheet_name(nama, terpakai):
        """Nama sheet Excel yang aman: <=31 karakter, tanpa karakter terlarang, unik."""
        s = str(nama)
        for ch in '[]:*?/\\':
            s = s.replace(ch, '-')
        s = s.strip() or 'Cabang'
        s = s[:31]
        dasar, n = s, 2
        while s.lower() in terpakai:
            akhiran = f"_{n}"
            s = dasar[:31 - len(akhiran)] + akhiran
            n += 1
        terpakai.add(s.lower())
        return s


    def rekap_kualifikasi(df, keys):
        """Rekap omzet & bagi hasil, dipecah per kualifikasi (Interface/Normal/Mati Total/...)."""
        base = (df.groupby(keys, as_index=False)
                  .agg(Baris=('TOTAL HARGA', 'size'),
                       Omzet=('TOTAL HARGA', 'sum'),
                       BH=('BAGI_HASIL', 'sum'),
                       Flat=('FLAT', 'sum')))

        def _pivot(nilai, prefix):
            p = df.pivot_table(index=keys, columns='TARIF_LABEL', values=nilai,
                               aggfunc='sum', fill_value=0.0)
            for k in KATEGORI_ORDER:
                if k not in p.columns:
                    p[k] = 0.0
            p = p[KATEGORI_ORDER]
            p.columns = [f"{prefix} {k}" for k in KATEGORI_ORDER]
            return p.reset_index()

        out = (base.merge(_pivot('TOTAL HARGA', 'Omzet'), on=keys, how='left')
                   .merge(_pivot('BAGI_HASIL', 'Bagi Hasil'), on=keys, how='left'))
        out['Selisih'] = out['BH'] - out['Flat']
        out['Efektif %'] = (out['BH'] / out['Omzet'].replace(0, pd.NA) * 100).round(1)

        urut = list(keys) + ['Baris'] \
            + [f"Omzet {k}" for k in KATEGORI_ORDER] + ['Omzet'] \
            + [f"Bagi Hasil {k}" for k in KATEGORI_ORDER] + ['BH', 'Flat', 'Selisih', 'Efektif %']
        out = out[urut].rename(columns={
            'TEKNISI': 'Nama Teknisi', 'CABANG': 'Cabang',
            'Omzet': 'Omzet Jasa (Total)', 'BH': 'Bagi Hasil (Aturan)', 'Flat': lbl_flat})
        return out.sort_values('Bagi Hasil (Aturan)', ascending=False).reset_index(drop=True)


    def _rumus_gaji(df, r):
        """Rumus Excel kolom penggajian untuk baris ke-r (1-indexed di worksheet)."""
        from openpyxl.utils import get_column_letter as L

        def kol(nama):
            return L(df.columns.get_loc(nama) + 1)

        return {
            'Total Potongan':
                f"=SUM({kol(KOLOM_POTONGAN[0])}{r}:{kol(KOLOM_POTONGAN[-1])}{r})",
            'Gaji Teknisi':
                f"={kol('Bagi Hasil (Aturan)')}{r}-{kol('Total Potongan')}{r}",
            'Nett Bagi hasil':
                f"={kol('Gaji Teknisi')}{r}-{kol('Cadangan 7 Tahun / bulan')}{r}",
            'Total Cadangan 7 Tahun':
                f"={kol('Cadangan 7 Tahun / bulan')}{r}+{kol('Cadangan 7 Tahun')}{r}",
        }


    def _tulis_sheet(writer, df, nama_sheet, judul, kolom_gaji=False):
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter

        df.to_excel(writer, sheet_name=nama_sheet, index=False, startrow=3)
        ws = writer.sheets[nama_sheet]

        ws.cell(row=1, column=1, value=judul).font = Font(bold=True, size=13, color='1F3864')
        ws.cell(row=2, column=1,
                value=f"Periode: {periode_txt} · Tarif: "
                      + ", ".join(f"{k} {v:.0f}%" for k, v in tarif_input.items())
                      + f", Lainnya {tarif_lain:.0f}%, pembanding flat {tarif_flat:.0f}%"
                ).font = Font(size=9, italic=True, color='555555')

        n_baris, n_kol = len(df), len(df.columns)
        head_fill = PatternFill('solid', fgColor='1F3864')
        head_font = Font(bold=True, color='FFFFFF', size=10)
        thin = Side(style='thin', color='D9D9D9')

        isian_fill = PatternFill('solid', fgColor='B45309')      # coklat: diisi manual
        rumus_fill = PatternFill('solid', fgColor='166534')       # hijau: rumus otomatis
        for j, kol in enumerate(df.columns, start=1):
            c = ws.cell(row=4, column=j)
            c.font = head_font
            c.fill = (isian_fill if kol in (KOLOM_POTONGAN + KOLOM_CADANGAN)
                      else rumus_fill if kol in KOLOM_RUMUS else head_fill)
            c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            c.border = Border(bottom=Side(style='medium', color='1F3864'))
            lebar = max(len(str(kol)) + 2,
                        (df[kol].astype(str).str.len().max() if n_baris else 0) + 2)
            ws.column_dimensions[get_column_letter(j)].width = min(max(lebar, 10), 34)

        kol_rp = [c for c in df.columns
                  if c.startswith(('Omzet', 'Bagi Hasil')) or c in (lbl_flat, 'Selisih')
                  or c in KOLOM_GAJI
                  or c in ('Omzet Jasa Mati Total', 'Insentif Store Leader',
                       'Bagi Hasil Teknisi', 'Sisa Sebelum', 'Sisa Sesudah')]
        idx_rp = [df.columns.get_loc(c) + 1 for c in kol_rp]
        idx_baris = (df.columns.get_loc('Baris') + 1) if 'Baris' in df.columns else None
        idx_pct = (df.columns.get_loc('Efektif %') + 1) if 'Efektif %' in df.columns else None

        for i in range(n_baris):
            r = 5 + i
            if i % 2 == 1:
                for j in range(1, n_kol + 1):
                    ws.cell(row=r, column=j).fill = PatternFill('solid', fgColor='F4F7FB')
            for j in idx_rp:
                ws.cell(row=r, column=j).number_format = '#,##0'
            if idx_baris:
                ws.cell(row=r, column=idx_baris).number_format = '#,##0'
            if idx_pct:
                ws.cell(row=r, column=idx_pct).number_format = '0.0'
            for j in range(1, n_kol + 1):
                ws.cell(row=r, column=j).border = Border(bottom=thin)
            if kolom_gaji:
                for kol in KOLOM_POTONGAN + KOLOM_CADANGAN:
                    ws.cell(row=r, column=df.columns.get_loc(kol) + 1).fill = \
                        PatternFill('solid', fgColor='FFF8E1')
                for kol, rumus in _rumus_gaji(df, r).items():
                    sel = ws.cell(row=r, column=df.columns.get_loc(kol) + 1, value=rumus)
                    sel.number_format = '#,##0'

        # baris TOTAL
        if n_baris:
            rt = 5 + n_baris
            ws.cell(row=rt, column=1, value='TOTAL')
            for j in range(1, n_kol + 1):
                c = ws.cell(row=rt, column=j)
                c.font = Font(bold=True)
                c.fill = PatternFill('solid', fgColor='DCE6F1')
                c.border = Border(top=Side(style='medium', color='1F3864'))
            for j in idx_rp + ([idx_baris] if idx_baris else []):
                L = get_column_letter(j)
                c = ws.cell(row=rt, column=j, value=f"=SUM({L}5:{L}{rt-1})")
                c.number_format = '#,##0'
                c.font = Font(bold=True)
            if idx_pct and 'Bagi Hasil (Aturan)' in df.columns:
                Lb = get_column_letter(df.columns.get_loc('Bagi Hasil (Aturan)') + 1)
                Lo = get_column_letter(df.columns.get_loc('Omzet Jasa (Total)') + 1)
                c = ws.cell(row=rt, column=idx_pct,
                            value=f"=IF({Lo}{rt}=0,0,{Lb}{rt}/{Lo}{rt}*100)")
                c.number_format = '0.0'
                c.font = Font(bold=True)

        ws.freeze_panes = ws.cell(row=5, column=1)
        if n_baris:
            ws.auto_filter.ref = f"A4:{get_column_letter(n_kol)}{4 + n_baris}"


    def buat_excel(df_sumber, rekap_sl=None):
        """Workbook: rekap teknisi/cabang, insentif store leader, lalu sheet per cabang."""
        buf = io.BytesIO()

        # normalisasi kunci supaya tidak ada baris yang hilang saat groupby
        d = df_sumber.copy()
        d['TEKNISI'] = (d['TEKNISI'].fillna('TIDAK ADA TEKNISI').astype(str).str.strip()
                        .replace({'': 'TIDAK ADA TEKNISI', 'nan': 'TIDAK ADA TEKNISI',
                                  'NaN': 'TIDAK ADA TEKNISI', 'None': 'TIDAK ADA TEKNISI'}))
        d['CABANG'] = (d['CABANG'].fillna('(TANPA CABANG)').astype(str).str.strip()
                       .replace({'': '(TANPA CABANG)', 'nan': '(TANPA CABANG)'}))

        rek_all = rekap_kualifikasi(d, ['TEKNISI', 'CABANG'])
        rek_cab = rekap_kualifikasi(d, ['CABANG'])
        rek_tek = rekap_kualifikasi(d, ['TEKNISI'])

        with pd.ExcelWriter(buf, engine='openpyxl') as writer:
            _tulis_sheet(writer, rek_all, 'Rekap Teknisi & Cabang',
                         'Rekap Bagi Hasil per Teknisi & Cabang')
            _tulis_sheet(writer, rek_tek, 'Rekap Teknisi',
                         'Rekap Bagi Hasil per Teknisi (gabungan semua cabang)')
            _tulis_sheet(writer, rek_cab, 'Rekap Cabang', 'Rekap Bagi Hasil per Cabang')

            terpakai = {'rekap teknisi & cabang', 'rekap teknisi', 'rekap cabang'}
            if rekap_sl is not None and len(rekap_sl):
                _tulis_sheet(writer, rekap_sl.copy(), 'Insentif Store Leader',
                             'Insentif Store Leader — persen dari omzet jasa Mati Total')
                terpakai.add('insentif store leader')
            for cab in sorted(d['CABANG'].unique()):
                sub = d[d['CABANG'] == cab]
                if sub.empty:
                    continue
                dc = rekap_kualifikasi(sub, ['TEKNISI']).copy()
                dc.insert(1, 'Cabang', cab)
                for kol in KOLOM_GAJI:
                    dc[kol] = pd.NA
                _tulis_sheet(writer, dc, _sheet_name(cab, terpakai),
                             f'Bagi Hasil Teknisi — Cabang {cab}', kolom_gaji=True)
        buf.seek(0)
        return buf.getvalue()


    # ---------------------------------------------------------------------------
    # Slip gaji PDF per cabang
    # ---------------------------------------------------------------------------
    DIR_ASET = Path(__file__).parent / "assets"
    LOGO_MADINAH = DIR_ASET / "logo-madinah.png"
    LOGO_MFLASH = DIR_ASET / "logo-mflash.png"

    # baris potongan pada slip -> kolom Excel yang dijumlahkan
    PETA_POTONGAN_SLIP = [
        ('Potongan Kasbon', ['Potongan Kasbon']),
        ('Potongan Refund', ['Potongan Refund']),
        ('Potongan AR', ['Potongan AR']),
        ('Potongan Terlambat', ['Keterlambatan']),
        ('Potongan Minus Audit', ['Potongan Minus Audit']),
        ('Potongan Audit Compliance', ['Potongan Audit Compliance']),
        ('Potongan Koperasi', ['Biaya Pendaftaran Koperasi', 'Simpanan Pokok',
                               'Simpanan Wajib']),
    ]


    def rupiah(v) -> str:
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        s = f"{abs(v):,.0f}".replace(",", ".")
        return ("Rp (" + s + ")") if v < 0 else ("Rp " + s)


    @st.cache_data(show_spinner="Membaca berkas potongan...")
    def baca_potongan(isi: bytes):
        """Ambil nilai potongan dari Excel hasil unduhan yang sudah diisi finance.

        -> {(CABANG, NAMA TEKNISI): {nama_kolom: nilai}}
        """
        hasil, terbaca = {}, 0
        xls = pd.ExcelFile(io.BytesIO(isi), engine='openpyxl')
        for sheet in xls.sheet_names:
            try:
                d = xls.parse(sheet, header=3)
            except Exception:                                     # noqa: BLE001
                continue
            if 'Nama Teknisi' not in d.columns or 'Cabang' not in d.columns:
                continue
            if not any(k in d.columns for k in KOLOM_POTONGAN):
                continue
            d = d[d['Nama Teknisi'].notna() & (d['Nama Teknisi'].astype(str) != 'TOTAL')]
            for _, r in d.iterrows():
                kunci = (str(r['Cabang']).strip().upper(),
                         str(r['Nama Teknisi']).strip().upper())
                isi_baris = {}
                for kol in KOLOM_POTONGAN + KOLOM_CADANGAN:
                    v = r.get(kol)
                    isi_baris[kol] = 0.0 if v is None or pd.isna(v) else float(v)
                hasil[kunci] = isi_baris
                terbaca += 1
        return hasil, terbaca


    def _baris_slip(sub, potongan):
        """Susun angka satu slip dari transaksi seorang teknisi di satu cabang."""
        per_kual = []
        for lbl in KATEGORI_ORDER:
            s = sub[sub['TARIF_LABEL'] == lbl]
            if s.empty or s['TOTAL HARGA'].sum() == 0:
                continue
            omzet, bh = s['TOTAL HARGA'].sum(), s['BAGI_HASIL'].sum()
            per_kual.append((lbl, omzet, bh / omzet if omzet else 0.0, bh))
        bruto = sum(x[3] for x in per_kual)

        pot = []
        for label, kolom in PETA_POTONGAN_SLIP:
            pot.append((label, sum(float(potongan.get(k, 0) or 0) for k in kolom)))
        total_pot = sum(x[1] for x in pot)

        return per_kual, bruto, pot, total_pot, bruto - total_pot


    def _gambar_slip(c, lebar, tinggi, nama, cabang, periode, angka, catatan):
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader

        per_kual, bruto, pot, total_pot, nett = angka
        m = 18 * mm
        y = tinggi - 14 * mm

        if LOGO_MADINAH.exists():
            c.drawImage(ImageReader(str(LOGO_MADINAH)), m, y - 20 * mm, width=20 * mm,
                        height=20 * mm, mask='auto')
        if LOGO_MFLASH.exists():
            c.drawImage(ImageReader(str(LOGO_MFLASH)), lebar - m - 34 * mm, y - 20 * mm,
                        width=34 * mm, height=24 * mm, mask='auto',
                        preserveAspectRatio=True, anchor='ne')
        y -= 26 * mm

        c.setFillColorRGB(0.12, 0.22, 0.39)
        c.setFont('Helvetica-Bold', 13)
        c.drawCentredString(lebar / 2, y, 'SLIP BAGI HASIL TEKNISI MADINAH FLASH')
        y -= 4 * mm
        c.setLineWidth(1.2)
        c.line(m, y, lebar - m, y)
        y -= 9 * mm

        c.setFillColorRGB(0, 0, 0)
        c.setFont('Helvetica', 9.5)
        for label, isi in [('Nama', nama), ('Jabatan', 'Teknisi'),
                           ('Divisi', f'MFlash — {cabang}'), ('Periode', periode)]:
            c.setFont('Helvetica-Bold', 9.5)
            c.drawString(m, y, label)
            c.setFont('Helvetica', 9.5)
            c.drawString(m + 24 * mm, y, f': {isi}')
            y -= 5.4 * mm
        y -= 3 * mm

        def judul_tabel(teks, kolom_kanan=True):
            nonlocal y
            c.setFillColorRGB(0.12, 0.22, 0.39)
            c.rect(m, y - 5.6 * mm, lebar - 2 * m, 5.6 * mm, stroke=0, fill=1)
            c.setFillColorRGB(1, 1, 1)
            c.setFont('Helvetica-Bold', 8.5)
            c.drawString(m + 2 * mm, y - 4 * mm, teks)
            if kolom_kanan:
                c.drawRightString(lebar - m - 46 * mm, y - 4 * mm, 'OMZET')
                c.drawRightString(lebar - m - 30 * mm, y - 4 * mm, 'AKAD')
                c.drawRightString(lebar - m - 2 * mm, y - 4 * mm, 'BAGI HASIL')
            else:
                c.drawRightString(lebar - m - 2 * mm, y - 4 * mm, 'JUMLAH')
            c.setFillColorRGB(0, 0, 0)
            y -= 9 * mm

        judul_tabel('PENDAPATAN PER KUALIFIKASI')
        c.setFont('Helvetica', 9)
        if not per_kual:
            c.drawString(m + 2 * mm, y, '(tidak ada transaksi jasa pada periode ini)')
            y -= 5.4 * mm
        for lbl, omzet, akad, bh in per_kual:
            c.drawString(m + 2 * mm, y, lbl)
            c.drawRightString(lebar - m - 46 * mm, y, rupiah(omzet))
            c.drawRightString(lebar - m - 30 * mm, y,
                              f'{akad*100:.1f}'.replace('.', ',') + '%')
            c.drawRightString(lebar - m - 2 * mm, y, rupiah(bh))
            y -= 5.4 * mm

        y -= 1 * mm
        c.setLineWidth(0.6)
        c.line(lebar - m - 52 * mm, y + 1.5 * mm, lebar - m, y + 1.5 * mm)
        y -= 3 * mm
        c.setFont('Helvetica-Bold', 9.5)
        c.drawString(m + 2 * mm, y, 'Total Bruto Bagi Hasil')
        c.drawRightString(lebar - m - 2 * mm, y, rupiah(bruto))
        y -= 9 * mm

        judul_tabel('POTONGAN', kolom_kanan=False)
        c.setFont('Helvetica', 9)
        for label, nilai in pot:
            c.drawString(m + 2 * mm, y, label)
            c.drawRightString(lebar - m - 2 * mm, y, rupiah(nilai))
            y -= 5.4 * mm
        c.setLineWidth(0.6)
        c.line(lebar - m - 52 * mm, y + 1.5 * mm, lebar - m, y + 1.5 * mm)
        y -= 3 * mm
        c.setFont('Helvetica-Bold', 9.5)
        c.drawString(m + 2 * mm, y, 'Total Potongan')
        c.drawRightString(lebar - m - 2 * mm, y, rupiah(total_pot))
        y -= 9 * mm

        c.setFillColorRGB(0.86, 0.92, 0.84)
        c.rect(m, y - 3 * mm, lebar - 2 * m, 8 * mm, stroke=0, fill=1)
        c.setFillColorRGB(0.05, 0.35, 0.15)
        c.setFont('Helvetica-Bold', 11)
        c.drawString(m + 2 * mm, y, 'NETT BAGI HASIL')
        c.drawRightString(lebar - m - 2 * mm, y, rupiah(nett))
        c.setFillColorRGB(0, 0, 0)
        y -= 14 * mm

        c.setFont('Helvetica-Bold', 8.5)
        c.drawString(m, y, 'Catatan')
        y -= 3 * mm
        tinggi_kotak = 20 * mm
        c.setLineWidth(0.6)
        c.setStrokeColorRGB(0.7, 0.7, 0.7)
        c.rect(m, y - tinggi_kotak, lebar - 2 * m, tinggi_kotak, stroke=1, fill=0)
        c.setFont('Helvetica', 8.5)
        baris_catatan = str(catatan or '').splitlines()
        yy = y - 5 * mm
        for baris in baris_catatan[:5]:
            c.drawString(m + 2 * mm, yy, baris[:110])
            yy -= 4.2 * mm
        y -= tinggi_kotak + 12 * mm

        c.setStrokeColorRGB(0, 0, 0)
        c.setFont('Helvetica', 8.5)
        for x, teks in ((m + 8 * mm, 'Teknisi'),
                        (lebar / 2 - 12 * mm, 'Kepala Cabang'),
                        (lebar - m - 40 * mm, 'Finance')):
            c.line(x, y, x + 32 * mm, y)
            c.drawCentredString(x + 16 * mm, y - 4.5 * mm, teks)


    def _nama_berkas_aman(teks, cadangan='TANPA-NAMA'):
        aman = re.sub(r'[^A-Za-z0-9 _.-]', '-', str(teks)).strip(' .-')
        return (aman[:80] or cadangan)


    def buat_pdf_teknisi(sub, nama, cabang, potongan, catatan, periode):
        """Satu PDF berisi slip satu teknisi saja — siap dikirim ke orangnya."""
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas as rl_canvas

        buf = io.BytesIO()
        lebar, tinggi = A4
        c = rl_canvas.Canvas(buf, pagesize=A4)
        c.setTitle(f'Slip Bagi Hasil — {nama} ({cabang})')
        c.setAuthor('Madinah Flash')
        pot = potongan.get((str(cabang).strip().upper(), str(nama).strip().upper()), {})
        _gambar_slip(c, lebar, tinggi, nama, cabang, periode,
                     _baris_slip(sub, pot), catatan)
        c.showPage()
        c.save()
        buf.seek(0)
        return buf.getvalue()


    def buat_zip_slip(df_sumber, potongan, catatan, periode, zip_per_cabang=False):
        """Satu PDF per teknisi, dikumpulkan per cabang.

        zip_per_cabang=False -> satu ZIP berisi folder per cabang (default)
        zip_per_cabang=True  -> satu ZIP berisi berkas .zip terpisah tiap cabang
        """
        import zipfile

        d = df_sumber.copy()
        d['CABANG'] = d['CABANG'].astype(str).str.strip()
        buf = io.BytesIO()
        ringkas = []
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as luar:
            for cab in sorted(d['CABANG'].unique()):
                sub_cab = d[d['CABANG'] == cab]
                if sub_cab.empty:
                    continue
                folder = _nama_berkas_aman(cab, 'CABANG')
                berkas = []
                for nama in sorted(sub_cab['TEKNISI'].unique()):
                    isi = buat_pdf_teknisi(sub_cab[sub_cab['TEKNISI'] == nama], nama, cab,
                                           potongan, catatan, periode)
                    berkas.append((f'{folder} - {_nama_berkas_aman(nama)}.pdf', isi))
                if zip_per_cabang:
                    dalam = io.BytesIO()
                    with zipfile.ZipFile(dalam, 'w', zipfile.ZIP_DEFLATED) as z2:
                        for nm, isi in berkas:
                            z2.writestr(nm, isi)
                    luar.writestr(f'{folder}.zip', dalam.getvalue())
                else:
                    for nm, isi in berkas:
                        luar.writestr(f'{folder}/{nm}', isi)
                ringkas.append({'Cabang': cab, 'Slip': len(berkas)})
        buf.seek(0)
        return buf.getvalue(), pd.DataFrame(ringkas)


    with st.container():
        st.markdown("##### 📊 Unduh Excel (multi-sheet per cabang)")
        st.caption(
            "Berisi kolom **Nama Teknisi**, **Cabang**, **Omzet**, rincian kualifikasi "
            "**Interface / Normal / Mati Total / Promo / Lainnya** (omzet & bagi hasil "
            "masing-masing), serta **Bagi Hasil**. Sheet: rekap gabungan, rekap per teknisi, "
            "rekap per cabang, sheet **Insentif Store Leader**, lalu satu sheet untuk "
            "tiap cabang.\n\n"
            "Sheet per cabang ditambah kolom penggajian: sembilan kolom potongan "
            "(header **coklat** = diisi manual) plus Total Potongan, Gaji Teknisi, "
            "Nett Bagi Hasil, dan Total Cadangan 7 Tahun (header **hijau** = rumus Excel, "
            "ikut berubah begitu potongannya diisi)."
        )
        if st.button("🧾 Siapkan berkas Excel", key='siap_xlsx', use_container_width=True):
            with st.spinner("Menyusun workbook..."):
                st.session_state['xlsx_bytes'] = buat_excel(jasa_tampil)
                st.session_state['xlsx_tag'] = tag_file
        if st.session_state.get('xlsx_bytes') is not None:
            st.download_button(
                "⬇️ Unduh Excel (.xlsx)",
                data=st.session_state['xlsx_bytes'],
                file_name=f"bagi_hasil_teknisi_{st.session_state.get('xlsx_tag', tag_file)}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, key='unduh_xlsx')


    with st.container():
        st.markdown("##### 🧾 Unduh Slip Gaji PDF (satu PDF per teknisi)")
        st.caption(
            "Setiap teknisi dapat berkas PDF sendiri supaya gampang dikirim satu-satu, "
            "dikelompokkan per cabang. Isinya: pendapatan dirinci per kualifikasi lengkap "
            "dengan persen akad-nya, lalu potongan dan Nett Bagi Hasil. "
            "Nilai potongan diambil dari berkas Excel yang sudah Anda isi — unduh Excel di "
            "atas, isi kolom potongan di sheet tiap cabang, lalu upload kembali di sini."
        )

        up_pot = st.file_uploader(
            "Excel potongan yang sudah diisi (opsional)", type=['xlsx'],
            key='up_potongan',
            help="Berkas hasil tombol Unduh Excel di atas, setelah kolom potongan diisi. "
                 "Kalau dikosongkan, semua potongan dianggap nol.")

        bentuk = st.radio(
            "Pengelompokan berkas di dalam ZIP", ['Folder per cabang', 'ZIP per cabang'],
            horizontal=True, key='bentuk_zip',
            help="Folder per cabang: satu ZIP berisi folder KLENDER/, CEGER/, dst. "
                 "ZIP per cabang: satu ZIP berisi KLENDER.zip, CEGER.zip, dst.")

        catatan_slip = st.text_area(
            "Catatan yang dicetak di setiap slip", value="", key='catatan_slip',
            height=70, placeholder="mis. Slip ini sah tanpa tanda tangan basah.")

        potongan = {}
        if up_pot is not None:
            try:
                potongan, n_pot = baca_potongan(up_pot.getvalue())
                st.success(f"Potongan terbaca untuk {n_pot:,} baris teknisi.")
            except Exception as e:                                   # noqa: BLE001
                st.error(f"Berkas potongan tidak terbaca: {e}")
        else:
            st.info("Belum ada berkas potongan — semua potongan dicetak Rp 0.")

        if st.button("🧾 Siapkan slip gaji PDF", key='siap_pdf', use_container_width=True):
            with st.spinner("Menyusun slip per cabang..."):
                try:
                    isi, ringkas = buat_zip_slip(
                        jasa_tampil, potongan, catatan_slip, periode_txt,
                        zip_per_cabang=(bentuk == 'ZIP per cabang'))
                    st.session_state['zip_slip'] = isi
                    st.session_state['zip_tag'] = tag_file
                    st.session_state['ringkas_slip'] = ringkas
                except ModuleNotFoundError:
                    st.error("Paket **reportlab** belum terpasang. Tambahkan `reportlab` "
                             "ke requirements.txt lalu deploy ulang.")

        if st.session_state.get('zip_slip') is not None:
            st.download_button(
                "⬇️ Unduh slip gaji (.zip)",
                data=st.session_state['zip_slip'],
                file_name=f"slip_bagi_hasil_{st.session_state.get('zip_tag', tag_file)}.zip",
                mime="application/zip", use_container_width=True, key='unduh_zip')
            rs = st.session_state.get('ringkas_slip')
            if rs is not None and len(rs):
                st.caption(f"{int(rs['Slip'].sum()):,} slip di {len(rs)} cabang.")
                with st.expander("Rincian jumlah slip per cabang"):
                    st.dataframe(rs, hide_index=True, use_container_width=True)



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
            "dari 24 bulan (M−1) sampai 23 bulan M. Contoh gaji Juli 2026 = 24 Juni 2026 "
            "s/d 23 Juli 2026. Tanggal acuan: **TGL FAKTUR**.\n\n"
            ("**Tarif Mati Total berubah sejak "
         f"{pd.Timestamp(tgl_mt_baru):%d %B %Y}** menjadi {tarif_mt_baru:.1f}% "
         f"(dari {tarif_input['Mati Total']:.1f}%). Penerapannya dinilai per "
         "TGL FAKTUR tiap baris, bukan per periode gaji — jadi periode yang "
         "terbelah tanggal berlaku otomatis terhitung proporsional: faktur "
         "sebelum tanggal itu memakai tarif lama, sesudahnya tarif baru. "
         f"Selisih {delta_mt*100:+.1f} poin juga ditambahkan ke teknisi "
         "bertarif khusus.\n\n") if pakai_mt_baru and abs(delta_mt) > 1e-12 else ""
        +
        f"**Pembanding Flat {tarif_flat:.0f}%** = seluruh omzet jasa × {tarif_flat:.0f}%, "
            "tanpa membedakan jenis pekerjaan.\n\n"
            "**Tarif khusus per teknisi** (panel 👥 Tarif Khusus per Teknisi) menimpa tarif "
            "umum hanya untuk kualifikasi yang diisi; kualifikasi yang dikosongkan tetap "
            "ikut tarif umum. Pencocokan nama memakai awalan, sehingga nama di data yang "
            "berakhiran nama cabang tetap kena. Tarif pembanding flat tidak ikut "
            "ditimpa.\n\n"
            "Nama teknisi diambil dari kolom **NAMA TEKNISI (FINAL)**; bila kosong dipakai "
            "kolom NAMA TEKNISI. Baris yang keduanya kosong masuk kelompok "
            "*TIDAK ADA TEKNISI* — tetap ditampilkan agar terlihat, dan bisa disembunyikan "
            "lewat centang di atas.\n\n"
            "Perhitungan memakai **omzet jasa (TOTAL HARGA)**, belum dikurangi biaya apa pun. "
            "Hanya baris berkategori **JASA** yang dihitung, dan baris yang cocok dengan "
            "daftar pengecualian (bawaan: **oper gadget**) dikeluarkan lebih dulu.\n\n"
            "Untuk cabang yang dipilih pada **acuan KERUSAKAN UTAMA**, kualifikasi tidak "
            "dibaca dari nama barang melainkan dari kolom KERUSAKAN UTAMA bersama KATEGORI "
            "PENJUALAN: LCD pada service HP, baterai, SSD, RAM, dan software pada service "
            "laptop masuk **Interface**; LCD pada service laptop, flexibel, mic, wifi card, "
            "software pada service HP, dan repair masuk **Normal**; kerusakan `mati total` "
            "masuk **Mati Total**; kerusakan lain di luar daftar ikut **Normal**."
        )

else:
    # -----------------------------------------------------------------------
    # Tab khusus: Insentif Store Leader, Supervisor, Front Liner & Team
    # -----------------------------------------------------------------------
    st.markdown("### 💼 Insentif Store Leader, Supervisor, Front Liner & Team")
    st.caption(
        "Tab ini memakai **periode kalender** (tanggal 1 sampai akhir bulan), "
        "berbeda dengan bagi hasil teknisi yang memakai cutoff 24–23. "
        "Semua insentif dihitung otomatis per cabang; pengaturannya ada di "
        "sidebar, bagian **💼 Pengaturan insentif**."
    )

    bulan_opsi = daftar_bulan(data_all['TGL'].min(), data_all['TGL'].max())
    if not bulan_opsi:
        st.warning("Tanggal faktur tidak terbaca, periode tidak bisa dibentuk.")
        st.stop()
    pilih_bulan = st.selectbox(
        "Periode (bulan kalender)", bulan_opsi, index=len(bulan_opsi) - 1,
        format_func=lambda x: label_bulan(x[0], x[1]), key='ins_bulan')
    b_awal, b_akhir = rentang_bulan(pilih_bulan[0], pilih_bulan[1])

    sel_jasa = jasa_all[(jasa_all['TGL'] >= b_awal) & (jasa_all['TGL'] <= b_akhir)]
    sel_semua = data_all[(data_all['TGL'] >= b_awal) & (data_all['TGL'] <= b_akhir)]
    if f_cabang != 'Semua Cabang':
        sel_jasa = sel_jasa[sel_jasa['CABANG'] == f_cabang]
        sel_semua = sel_semua[sel_semua['CABANG'] == f_cabang]

    st.markdown(
        f"**Rentang dihitung:** {b_awal.day} {BULAN_NAMES[b_awal.month]} – "
        f"{b_akhir.day} {BULAN_NAMES[b_akhir.month]} {b_akhir.year}"
        + (f" · cabang **{f_cabang}**" if f_cabang != 'Semua Cabang' else ""))
    st.caption(f"**Skema aktif:** {skema_fl} · {ket_insentif}")

    if sel_semua.empty:
        st.warning("Tidak ada transaksi pada bulan tersebut.")
        st.stop()

    ins = rekap_insentif(sel_jasa, sel_semua, persen_insentif, skema_fl,
                         tarif_skema)
    tot = {k: float(ins[k].sum()) for k in KOL_INSENTIF}
    total_ins = float(ins['Total Insentif'].sum())
    gp_awal = float(ins['Gross Profit Awal'].sum())
    gp_akhir = gp_awal - total_ins
    turun = (total_ins / gp_awal * 100) if gp_awal else 0.0
    omzet_total = float(ins['Omzet Total'].sum())
    hpp = float(ins['HPP'].sum())
    bh_tek = float(ins['Bagi Hasil Teknisi'].sum())

    st.markdown(kpi_html([
        {'label': 'Gross Profit Awal', 'value': rp(gp_awal),
         'sub': 'omzet − HPP − bagi hasil teknisi',
         'grad': 'linear-gradient(135deg,#1f3864,#2e5394)'},
        {'label': 'Total Insentif', 'value': rp(total_ins),
         'sub': f"{len(ins)} cabang",
         'grad': 'linear-gradient(135deg,#0f8a82,#17a3a3)'},
        {'label': 'Gross Profit Setelah Insentif', 'value': rp(gp_akhir),
         'sub': f'turun {turun:.2f}%',
         'grad': ('linear-gradient(135deg,#16a34a,#22c55e)' if gp_akhir >= 0
                  else 'linear-gradient(135deg,#c9392f,#e0475a)')},
        {'label': 'Store Leader + Supervisor',
         'value': rp(tot['Insentif Store Leader'] + tot['Insentif Supervisor']),
         'sub': f"dari omzet Mati Total {rp(ins['Omzet Jasa Mati Total'].sum())}",
         'grad': 'linear-gradient(135deg,#2e9bd6,#3f8ac9)'},
        {'label': 'Front Liner', 'value': rp(tot['Insentif Front Liner']),
         'sub': 'aksesoris + laptop + handphone',
         'grad': 'linear-gradient(135deg,#e0921f,#e2b21a)'},
        {'label': 'Team', 'value': rp(tot['Insentif Team']),
         'sub': f"{persen_insentif['Team']:g}% dari omzet jasa "
                f"{rp(ins['Omzet Jasa'].sum())}",
         'grad': 'linear-gradient(135deg,#7c3aed,#a855f7)'},
    ]), unsafe_allow_html=True)
    st.write("")

    # --- ringkasan gross profit -------------------------------------------
    st.markdown("#### Dari Omzet ke Gross Profit")
    ringkas = pd.DataFrame([
        {'Pos': 'Omzet total (semua kategori)', 'Nilai': omzet_total},
        {'Pos': 'HPP (harga beli)', 'Nilai': -hpp},
        {'Pos': 'Bagi hasil teknisi', 'Nilai': -bh_tek},
        {'Pos': 'Gross Profit Awal', 'Nilai': gp_awal},
        {'Pos': 'Insentif Store Leader', 'Nilai': -tot['Insentif Store Leader']},
        {'Pos': 'Insentif Supervisor', 'Nilai': -tot['Insentif Supervisor']},
        {'Pos': 'Insentif Front Liner', 'Nilai': -tot['Insentif Front Liner']},
        {'Pos': 'Insentif Team', 'Nilai': -tot['Insentif Team']},
        {'Pos': 'Gross Profit Setelah Insentif', 'Nilai': gp_akhir},
    ])
    ringkas['% dari omzet'] = (ringkas['Nilai'].abs() / omzet_total * 100
                               if omzet_total else 0).round(2)
    r1, r2 = st.columns([1, 1.15])
    with r1:
        st.dataframe(
            ringkas.style.format({'Nilai': 'Rp {:,.0f}', '% dari omzet': '{:.2f}'}),
            use_container_width=True, hide_index=True, height=350,
            key='tabel_gp_ringkas')
    with r2:
        figk = px.pie(
            names=['Store Leader', 'Supervisor', 'Front Liner', 'Team'],
            values=[tot['Insentif Store Leader'], tot['Insentif Supervisor'],
                    tot['Insentif Front Liner'], tot['Insentif Team']],
            hole=0.55,
            color_discrete_sequence=['#2e9bd6', '#7c3aed', '#e0921f', '#12a89e'])
        figk.update_traces(textinfo='label+percent')
        figk.update_layout(height=350, margin=dict(l=5, r=5, t=25, b=5),
                           showlegend=False,
                           title=dict(text='Komposisi Total Insentif', font_size=13))
        st.plotly_chart(figk, use_container_width=True, key='fig_ins_pie')

    # --- grafik per cabang -------------------------------------------------
    st.markdown("#### Insentif per Cabang menurut Peran")
    c = ins.sort_values('Total Insentif')
    warna = {'Insentif Store Leader': '#2e9bd6', 'Insentif Supervisor': '#7c3aed',
             'Insentif Front Liner': '#e0921f', 'Insentif Team': '#12a89e'}
    fig = go.Figure()
    for kol in KOL_INSENTIF:
        fig.add_bar(y=c['Cabang'], x=c[kol], orientation='h',
                    name=kol.replace('Insentif ', ''), marker_color=warna[kol],
                    marker_line_width=0,
                    hovertemplate='%{y}<br>' + kol.replace('Insentif ', '')
                                  + ' %{x:,.0f}<extra></extra>')
    fig.update_layout(
        barmode='stack', height=max(340, 30 * len(c)),
        margin=dict(l=10, r=20, t=10, b=10), xaxis_title='Rupiah',
        legend=dict(orientation='h', y=1.04, x=0),
        plot_bgcolor='rgba(0,0,0,0)', bargap=0.35)
    fig.update_xaxes(gridcolor='#eceff3', zeroline=False)
    fig.update_yaxes(gridcolor='rgba(0,0,0,0)')
    st.plotly_chart(fig, use_container_width=True, key='fig_ins_peran')

    st.markdown("#### Gross Profit Sebelum vs Sesudah Insentif")
    st.caption(
        "Gross Profit = omzet seluruh kategori − HPP (kolom HARGA BELI) − bagi "
        "hasil teknisi, pada bulan kalender yang sama. Belum dikurangi biaya "
        "operasional lain seperti sewa, listrik, dan gaji pokok."
    )
    c_pf = ins.sort_values('Gross Profit Awal')
    fig_pf = go.Figure()
    fig_pf.add_bar(y=c_pf['Cabang'], x=c_pf['Gross Profit Awal'], orientation='h',
                   name='Sebelum insentif', marker_color='#2e9bd6',
                   marker_line_width=0,
                   hovertemplate='%{y}<br>Sebelum %{x:,.0f}<extra></extra>')
    fig_pf.add_bar(y=c_pf['Cabang'], x=c_pf['Gross Profit Setelah Insentif'],
                   orientation='h', name='Sesudah insentif', marker_color='#c9392f',
                   marker_line_width=0,
                   hovertemplate='%{y}<br>Sesudah %{x:,.0f}<extra></extra>')
    fig_pf.update_layout(
        barmode='group', height=max(360, 42 * len(c_pf)),
        margin=dict(l=10, r=20, t=10, b=10), xaxis_title='Rupiah',
        legend=dict(orientation='h', y=1.04, x=0),
        plot_bgcolor='rgba(0,0,0,0)', bargap=0.3, bargroupgap=0.08)
    fig_pf.update_xaxes(gridcolor='#eceff3', zeroline=False)
    fig_pf.update_yaxes(gridcolor='rgba(0,0,0,0)')
    st.plotly_chart(fig_pf, use_container_width=True, key='fig_ins_gp')

    st.markdown("#### Rincian per Cabang")
    tampil = ins[[k for k in KOL_TAMPIL_INSENTIF if k in ins.columns]]
    fmt = {k: 'Rp {:,.0f}' for k in tampil.columns
           if str(k).startswith(('Omzet', 'Insentif', 'FL ', 'Total', 'Bagi',
                                 'Gross', 'HPP'))}
    fmt.update({k: '{:,.0f}' for k in tampil.columns if str(k).startswith('Unit')})
    fmt['Penurunan GP %'] = '{:.2f}'
    st.dataframe(tampil.style.format(fmt), use_container_width=True,
                 hide_index=True, height=440, key='tabel_ins_cabang')

    tag_bulan = f"{pilih_bulan[0]}-{pilih_bulan[1]:02d}"
    d1, d2 = st.columns(2)
    with d1:
        st.download_button(
            "⬇️ Unduh rincian insentif (CSV)",
            data=tampil.to_csv(index=False).encode('utf-8-sig'),
            file_name=f"insentif_{tag_bulan}.csv", mime="text/csv",
            use_container_width=True, key='unduh_ins_csv')
    with d2:
        if st.button("🧾 Siapkan berkas Excel insentif", key='siap_ins_xlsx',
                     use_container_width=True):
            with st.spinner("Menyusun workbook..."):
                st.session_state['ins_xlsx'] = buat_excel_insentif(
                    tampil, f"{skema_fl} · {ket_insentif}",
                    label_bulan(pilih_bulan[0], pilih_bulan[1]))
                st.session_state['ins_tag'] = tag_bulan
        if st.session_state.get('ins_xlsx') is not None:
            st.download_button(
                "⬇️ Unduh Excel insentif (.xlsx)",
                data=st.session_state['ins_xlsx'],
                file_name=f"insentif_{st.session_state.get('ins_tag', tag_bulan)}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, key='unduh_ins_xlsx')

    with st.expander("ℹ️ Cara perhitungan tab ini"):
        st.write(
            f"**Periode** memakai bulan kalender penuh: {b_awal.day} "
            f"{BULAN_NAMES[b_awal.month]} s/d {b_akhir.day} "
            f"{BULAN_NAMES[b_akhir.month]} {b_akhir.year} — sengaja berbeda dari "
            "bagi hasil teknisi yang memakai cutoff 24–23.\n\n"
            "**Dasar tiap peran** (semua dihitung otomatis per cabang, tanpa perlu "
            "mengisi nama):\n"
            f"- **Store Leader** = omzet jasa *Mati Total* × "
            f"{persen_insentif['Store Leader']:g}%\n"
            f"- **Supervisor** = omzet jasa *Mati Total* × "
            f"{persen_insentif['Supervisor']:g}%\n"
            f"- **Team** = seluruh omzet jasa cabang × {persen_insentif['Team']:g}%\n"
            f"- **Front Liner** = aksesoris × "
            f"{persen_insentif['Front Liner Aksesoris']:g}%, ditambah laptop & "
            f"handphone menurut skema terpilih ({ket_skema})\n\n"
            "**Sumber baris penjualan** memakai kolom KATEGORI PENJUALAN: "
            "`Penjualan Aksesoris`, `Penjualan Laptop`, serta `Penjualan HP` dan "
            "`Penjualan Handphone` yang digabung sebagai handphone. Seluruh baris "
            "dalam faktur channel tersebut ikut dihitung, termasuk aksesoris "
            "pengiring.\n\n"
            "**Gross Profit Awal** = omzet seluruh kategori − HPP − bagi hasil "
            "teknisi. HPP diambil dari kolom HARGA BELI, yang pada data ini sudah "
            "berupa total per baris (bukan harga satuan), sehingga tidak dikalikan "
            "QTY lagi. Angka ini belum dikurangi biaya operasional lain, jadi bukan "
            "laba bersih.\n\n"
            "**Gross Profit Setelah Insentif** = Gross Profit Awal − total insentif "
            "keempat peran."
        )
