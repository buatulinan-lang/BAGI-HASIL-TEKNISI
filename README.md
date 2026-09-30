# Bagi Hasil Teknisi — V2

Versi pengembangan. **V1** adalah versi produksi yang sudah dipakai dan tidak
diubah lagi: repo [BAGI-HASIL-TEKNISI](https://github.com/buatulinan-lang/BAGI-HASIL-TEKNISI),
app <https://bagi-hasil-teknisi.streamlit.app>.

| | V1 | V2 |
|---|---|---|
| Status | produksi, beku | pengembangan |
| Repo | BAGI-HASIL-TEKNISI | BAGI-HASIL-TEKNISI-V2 |
| Perubahan baru | tidak ada | semua di sini |

Judul app dan sidebar menampilkan penanda **V2** supaya tidak tertukar saat
keduanya dibuka bersamaan di browser.

## Jalankan lokal

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Isi repo

| Berkas | Keterangan |
|---|---|
| `app.py` | seluruh aplikasi (satu berkas) |
| `assets/` | logo Madinah Group & MFlash untuk slip PDF |
| `data/penjualan.csv.gz` | data bawaan (gabungan 18 cabang, Jan–Agu 2026); bisa diganti lewat uploader di sidebar |

## Yang sudah ada (diwarisi dari V1)

- Upload banyak berkas sekaligus, satu kiriman per cabang; nama cabang dideteksi dari
  kolom CABANG → nama sheet → nama berkas, dengan isian manual sebagai cadangan.
- Pembuangan kiriman ulang (duplikat antar berkas), aman terhadap nomor faktur yang
  dipakai ulang di cabang berbeda.
- Periode penggajian cutoff 24 s/d 23: gaji bulan M = 24 bulan (M−1) s/d 23 bulan M.
- Tarif bagi hasil per kualifikasi (Interface / Normal / Mati Total / Promo / Lainnya)
  plus tabel tarif khusus per teknisi.
- Pengecualian jasa tertentu (bawaan: oper gadget).
- Acuan KERUSAKAN UTAMA untuk cabang yang penamaan barangnya belum berkata kunci.
- Unduhan Excel multi-sheet (rekap + satu sheet per cabang, lengkap kolom penggajian).
- Unduhan slip gaji PDF: satu berkas per teknisi, dikumpulkan per cabang dalam ZIP.

## Tambahan V2

### Tab Insentif Non-Teknisi

Tab terpisah dari dashboard bagi hasil — pilih di sidebar, bagian **🗂️ Tab**.
Periode memakai **bulan kalender** (tanggal 1 s/d akhir bulan), berbeda dari bagi
hasil teknisi yang memakai cutoff 24–23.

| Peran | Dasar | Bawaan |
|---|---|---|
| Store Leader | omzet jasa *Mati Total* | 3% |
| Supervisor | omzet jasa *Mati Total* | 1% |
| Team | seluruh omzet jasa cabang | 2% |
| Front Liner — aksesoris | channel *Penjualan Aksesoris* | 5% |
| Front Liner — laptop & HP | dua skema pilihan | 3%/2% atau Rp 50rb/Rp 30rb per unit |
| **Sales Retail** | tier dari omzet sebulan per orang | lihat di bawah |

#### Sales Retail

Dikelompokkan per nama pada kolom **YANG MENYERAHKAN/MENJUAL**. Omzet yang
menentukan tier = Penjualan Laptop + Aksesoris + Handphone dalam satu bulan,
lalu **seluruh omzet itu** dikali persen tier (sesuai kolom SIMULASI INSENTIF
pada surat penawaran).

| Omzet minimal | Persen |
|---|---|
| 300.000.000 | 11,0% |
| 250.000.000 | 11,0% |
| 200.000.000 | 10,5% |
| 150.000.000 | 8,5% |
| 100.000.000 | 8,0% |
| 75.000.000 | 7,5% |
| 50.000.000 | 7,0% |
| 25.000.000 | 5,0% |

Tabel tier bisa diubah, ditambah, atau dikurangi dari dalam tab. Di bawah tier
terendah tidak dapat insentif tier.

Bonus terpisah (diatur di sidebar): **handphone Rp 40.000/unit** dan **laptop
gaming Rp 350.000/unit** — laptop gaming dikenali dari kata `GAMING` pada NAMA
BARANG di channel Penjualan Laptop.

Pada tabel per cabang, insentif tiap sales dibagi ke cabang sebanding omzetnya di
cabang tersebut, karena sebagian sales menjual di lebih dari satu cabang.

**Gross Profit** = omzet seluruh kategori − HPP − bagi hasil teknisi.
HPP diambil dari kolom `HARGA BELI`, yang pada data ini **sudah berupa total per
baris** (bukan harga satuan) sehingga tidak dikalikan QTY lagi. Angka ini belum
dikurangi biaya operasional lain, jadi bukan laba bersih.

### Rencana berikutnya

_(belum ada)_
