# Dashboard Bagi Hasil Teknisi

Aplikasi **berdiri sendiri** (terpisah dari dashboard servis/penjualan) untuk
menghitung omzet jasa per teknisi beserta bagi hasilnya.

## Isi dashboard

- **KPI**: omzet jasa, bagi hasil menurut aturan, pembanding flat, selisih,
  jumlah teknisi, dan omzet yang belum bernama teknisi
- **Rekap per Teknisi & Cabang** — tabel utama, bisa dicari, bisa diunduh
- **Rekap per Cabang**
- **Grafik 15 teratas**: aturan vs pembanding
- **Komposisi omzet per tarif**
- **Detail transaksi jasa**

## 1. Install (sekali saja)

```bash
pip install -r requirements.txt
```

## 2. Jalankan

```bash
streamlit run app.py
```

Browser terbuka otomatis ke `http://localhost:8501`.

## 3. Data

Aplikasi membaca `data/penjualan.csv.gz` secara otomatis. Kalau mau memakai
file lain, upload lewat panel kiri (mendukung `.xlsx` satu sheet per cabang,
`.csv`, atau `.csv.gz`).

Kolom yang dibutuhkan: `TGL FAKTUR`, `NO FAKTUR`, `KATEGORI BARANG`,
`NAMA BARANG`, `NAMA TEKNISI (FINAL)` (atau `NAMA TEKNISI`), `QTY`,
`TOTAL HARGA`, dan `CABANG`.

Hanya baris berkategori **JASA** yang dihitung.

## 4. Aturan bagi hasil

Tarif ditentukan dari kata kunci pada kolom **NAMA BARANG**:

| Kata kunci pada nama barang | Tarif awal |
|---|---|
| mengandung **Interface** | 20% |
| mengandung **Normal** | 30% |
| mengandung **Mati Total** | 32% |
| mengandung **Promo** | 60% |
| tidak mengandung kata kunci mana pun | 30% |

Semua angka di atas **bisa diubah manual** lewat panel
**⚙️ Pengaturan Tarif Bagi Hasil** di dashboard — termasuk tarif pembanding
dan penentuan mana yang menang bila satu nama mengandung dua kata kunci
(contoh: `JS PROMO LCD 250K - NORMAL`).

## 5. Periode penggajian (cutoff 24 → 23)

Gaji bulan M dihitung dari **24 bulan (M−2)** sampai **23 bulan (M−1)**.

| Bulan gaji | Periode dihitung |
|---|---|
| Mei 2026 | 24 Maret – 23 April 2026 |
| Juni 2026 | 24 April – 23 Mei 2026 |
| Juli 2026 | 24 Mei – 23 Juni 2026 |

Tanggal acuannya adalah **TGL FAKTUR**.

## 6. Berkas unduhan

Tersedia tiga tombol unduh CSV:

1. **Rekap per Teknisi & Cabang** — kolom: `Nama Teknisi`, `Cabang`,
   `Bagi Hasil (Aturan)`, `Omzet Jasa`, `Pembanding`, `Selisih`, `Baris`,
   `Efektif %`
2. **Rekap per Teknisi (digabung semua cabang)** — kolom: `Nama Teknisi`,
   `Cabang` (daftar cabang tempat ia bekerja), `Bagi Hasil (Aturan)`,
   `Omzet Jasa`, `Pembanding`, `Selisih`
3. **Rekap per Cabang**

Nama berkas otomatis memuat periode gajinya, misalnya
`bagi_hasil_teknisi_cabang_gaji-2026-09.csv`.

## Catatan penting

Sebagian teknisi (33 dari 170 pada data saat ini) bekerja di **lebih dari satu
cabang**. Karena itu rekap utama dipecah per teknisi **per cabang**, supaya
bagi hasilnya bisa dibebankan ke cabang yang tepat. Bila butuh angka gabungan
per orang, gunakan tombol unduh yang kedua.

Penamaan barang berkata kunci (`JS ... - INTERFACE`, dst.) baru mulai dipakai
sekitar **Juli 2026**. Untuk periode sebelumnya semua item memakai penamaan
lama (`JASA REPAIR`, `JASA BATERAI`, ...) sehingga seluruhnya kena tarif
tanpa-kata-kunci — dashboard akan menampilkan peringatan bila itu terjadi.

Angka yang dihitung adalah **omzet jasa (TOTAL HARGA)**, belum dikurangi biaya
apa pun.
