# 📋 Plan: Dashboard Iyan Routers

> **Project:** kimchi-server — API Router Proxy  
> **Domain:** `https://routers.iyantama.tech:4000/dashboard`  
> **Tanggal:** 18 Juni 2026  
> **Status:** 🟡 Menunggu Approval

---

## 1. Latar Belakang

`kimchi-server` adalah proxy FastAPI yang menerima request format Anthropic, menerjemahkannya ke format OpenAI, lalu meneruskannya ke `kimchi.dev`. Server ini sudah mendukung **multi-API-key rotation** — bila satu key terkena rate limit (HTTP `429` / `401`), otomatis berpindah ke key cadangan berikutnya.

Saat ini, untuk memantau status key dan menambah key baru, harus SSH ke VPS dan edit file `.env` secara manual. **Dashboard ini akan menyelesaikan masalah itu.**

---

## 2. Tujuan Dashboard

| # | Tujuan | Deskripsi |
|---|--------|-----------|
| 1 | **Monitoring Real-time** | Melihat status server, uptime, total request, dan jumlah failover secara live |
| 2 | **Status Limit API Key** | Menampilkan status setiap API key (`Active` / `Standby` / `Limited`) dengan visual badge |
| 3 | **Live Request Logs** | Menampilkan 20 request terakhir beserta model, latensi, status code, dan info rotasi |
| 4 | **Manajemen API Key dari Dashboard** | Menambah API key baru langsung dari UI tanpa SSH ke server |

---

## 3. Arsitektur Sistem

```
┌─────────────────────────────────────────────────────┐
│                    Browser (User)                    │
│                                                     │
│   https://routers.iyantama.tech:4000/dashboard      │
│         │                              ▲            │
│         │  GET /dashboard              │            │
│         │  (HTML Response)             │            │
│         ▼                              │            │
│   ┌─────────────┐    Polling setiap    │            │
│   │  Dashboard   │───────2 detik──────►│            │
│   │  (JS Fetch)  │  GET /api/status    │            │
│   │              │  POST /api/keys     │            │
│   └─────────────┘                      │            │
└────────────────────────────────────────┼────────────┘
                                         │
┌────────────────────────────────────────┼────────────┐
│              FastAPI (kimchi.py)        │            │
│                Port 4000 + SSL         │            │
│                                        │            │
│   Routes:                              │            │
│   ├── POST /v1/messages    ← Proxy utama (existing) │
│   ├── GET  /dashboard      ← Serve HTML (NEW)      │
│   ├── GET  /api/status     ← JSON metrics (NEW)    │
│   ├── POST /api/keys       ← Tambah key (NEW)      │
│   └── DELETE /api/keys     ← Hapus key (NEW)       │
│                                        │            │
│   State (in-memory, config.py):        │            │
│   ├── START_TIME                       │            │
│   ├── total_requests                   │            │
│   ├── failover_count                   │            │
│   ├── key_statuses {}                  │            │
│   └── recent_requests []               │            │
└─────────────────────────────────────────────────────┘
```

---

## 4. Scope Perubahan File

### 4.1 `config.py` — State & Key Management

**Status saat ini:** Sudah memiliki variabel `START_TIME`, `total_requests`, `failover_count`, `recent_requests`, `key_statuses`, fungsi `rotate_key()` dan `add_request_log()`.

**Perubahan yang diperlukan:**

- [ ] **Tambah fungsi `add_api_key(new_key: str)`**
  - Validasi: cek apakah key sudah ada (duplikat)
  - Tambahkan key ke list `API_KEYS`
  - Set status key baru sebagai `Standby`
  - Update file `.env` agar key baru persisten setelah restart
  - Return `True/False` + pesan

- [ ] **Tambah fungsi `remove_api_key(key_prefix: str)`**
  - Cari key berdasarkan prefix (15 karakter pertama)
  - Jangan izinkan hapus key yang sedang `Active` (kecuali ada key lain)
  - Hapus dari `API_KEYS` dan `key_statuses`
  - Update file `.env`
  - Return `True/False` + pesan

- [ ] **Tambah fungsi `reset_key_status(key_prefix: str)`**
  - Reset status key dari `Limited` kembali ke `Standby`
  - Berguna ketika rate limit sudah expired dan ingin menggunakan key itu lagi

- [ ] **Tambah fungsi `get_masked_keys()`**
  - Mengembalikan list key yang sudah di-mask untuk tampilan di dashboard
  - Format: `sk-1234567890abcde...` (15 karakter pertama + `...`)

---

### 4.2 `kimchi.py` — Endpoint Baru + Logging

**Perubahan yang diperlukan:**

#### A. Integrasi Request Logging (modifikasi handler existing)

- [ ] **Stream handler (`generate()`):** Tambahkan pengukuran latensi (`time.time()`) dan panggil `add_request_log()` setelah stream selesai atau error
- [ ] **Non-stream handler:** Sama — ukur latensi dan log ke `add_request_log()`
- [ ] Catat apakah terjadi rotasi key (`rotated=True/False`)

#### B. Endpoint: `GET /dashboard`

- [ ] Mengembalikan `HTMLResponse` berisi halaman dashboard lengkap (inline HTML + CSS + JS)
- [ ] Tidak membutuhkan file statis terpisah — semua dalam satu string Python

#### C. Endpoint: `GET /api/status`

- [ ] Return JSON:
  ```json
  {
    "status": "online",
    "uptime": "2h 15m 30s",
    "uptime_seconds": 8130,
    "total_requests": 142,
    "failover_count": 3,
    "active_key_index": 0,
    "total_keys": 3,
    "keys": [
      {
        "index": 0,
        "prefix": "sk-1234567890a...",
        "status": "Active"
      },
      {
        "index": 1,
        "prefix": "sk-abcdef12345...",
        "status": "Standby"
      },
      {
        "index": 2,
        "prefix": "sk-xyz99887766...",
        "status": "Limited"
      }
    ],
    "recent_requests": [
      {
        "timestamp": "23:45:12",
        "model": "kimi-k2.6",
        "status_code": 200,
        "key_used": "sk-1234567890a...",
        "rotated": false,
        "latency_ms": 1523
      }
    ]
  }
  ```

#### D. Endpoint: `POST /api/keys`

- [ ] Body: `{ "key": "sk-xxxxxxxx" }`
- [ ] Validasi: key tidak kosong, tidak duplikat
- [ ] Panggil `add_api_key()` dari `config.py`
- [ ] Return: `{ "success": true, "message": "Key added", "total_keys": 4 }`

#### E. Endpoint: `DELETE /api/keys`

- [ ] Body: `{ "key_prefix": "sk-1234567890a" }`
- [ ] Panggil `remove_api_key()` dari `config.py`
- [ ] Return: `{ "success": true, "message": "Key removed" }`

#### F. Endpoint: `POST /api/keys/reset`

- [ ] Body: `{ "key_prefix": "sk-xyz99887766" }`
- [ ] Reset status key dari `Limited` → `Standby`
- [ ] Return: `{ "success": true, "message": "Key status reset to Standby" }`

---

### 4.3 Dashboard Frontend (Inline HTML/CSS/JS)

Dashboard akan di-serve sebagai satu halaman HTML lengkap yang di-embed di dalam `kimchi.py` sebagai string Python.

#### Desain Visual

| Aspek | Spesifikasi |
|-------|-------------|
| **Theme** | Dark mode — Background `hsl(220, 15%, 8%)`, Cards `hsl(220, 15%, 12%)` |
| **Font** | Google Fonts `Inter` (400, 500, 600, 700) |
| **Accent Colors** | Neon Emerald `hsl(145, 80%, 45%)` untuk Active/Success, Crimson `hsl(0, 85%, 55%)` untuk Limited/Error, Blue `hsl(210, 80%, 55%)` untuk Standby, Amber `hsl(38, 92%, 55%)` untuk Warning |
| **Border Radius** | `2px` (sharp, technical feel — bukan rounded generik) |
| **Cards** | `backdrop-filter: blur(12px)`, border `1px solid hsl(220, 15%, 20%)`, subtle shadow |
| **Animasi** | Pulsing dot untuk status online, fade-in untuk data baru, hover scale pada cards |

#### Komponen UI

```
┌─────────────────────────────────────────────────────────────┐
│  ⚡ IYAN ROUTER                              🟢 Online     │
│  routers.iyantama.tech                                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ REQUESTS │  │  UPTIME  │  │FAILOVERS │  │ACTIVE KEY│   │
│  │   142    │  │ 2h 15m   │  │    3     │  │  #1/3    │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  🔑 API KEYS                          [+ Add Key]  │   │
│  │                                                     │   │
│  │  #1  sk-1234567890a...   🟢 Active      [Reset]    │   │
│  │  #2  sk-abcdef12345...   🔵 Standby     [Remove]   │   │
│  │  #3  sk-xyz99887766...   🔴 Limited     [Reset]    │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  📡 LIVE ACTIVITY (auto-refresh 2s)                 │   │
│  │                                                     │   │
│  │  TIME     MODEL        STATUS  KEY          LATENCY │   │
│  │  23:45    kimi-k2.6    200     sk-123...    1.5s    │   │
│  │  23:44    minimax-m2.7 200     sk-123...    0.8s    │   │
│  │  23:43    kimi-k2.6    429     sk-abc... ⚠ 0.2s    │   │
│  │  23:42    kimi-k2.6    200     sk-xyz...    2.1s    │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  ➕ ADD NEW API KEY                                 │   │
│  │  ┌─────────────────────────────┐  ┌───────────┐    │   │
│  │  │ Paste API key here...       │  │ ADD KEY   │    │   │
│  │  └─────────────────────────────┘  └───────────┘    │   │
│  │  Key akan langsung aktif sebagai Standby            │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

#### Fitur Interaktif JavaScript

- [ ] **Auto-polling:** `fetch("/api/status")` setiap 2 detik, update DOM tanpa reload
- [ ] **Add Key Form:** Input field + tombol submit, panggil `POST /api/keys`
- [ ] **Reset Key Button:** Klik untuk reset key `Limited` → `Standby` via `POST /api/keys/reset`
- [ ] **Remove Key Button:** Klik untuk hapus key via `DELETE /api/keys` (dengan konfirmasi)
- [ ] **Toast Notifications:** Feedback visual setelah aksi (key added, key removed, error)
- [ ] **Status Badge Animation:** Badge berubah warna secara smooth saat status key berubah
- [ ] **Row Highlight:** Request yang mengalami rotasi ditandai dengan background merah samar

---

## 5. Alur Kerja (Flow)

### 5.1 Flow Normal (Request Berhasil)
```
User Request → /v1/messages → Key #1 (Active) → 200 OK
                                    │
                                    ▼
                           add_request_log()
                                    │
                                    ▼
                        Dashboard auto-update via polling
```

### 5.2 Flow Rate Limit + Rotation
```
User Request → /v1/messages → Key #1 (Active) → 429 Rate Limited
                                    │
                                    ▼
                         rotate_key() → Key #1 = "Limited"
                                    │
                                    ▼
                              Key #2 (Standby → Active) → 200 OK
                                    │
                                    ▼
                           add_request_log(rotated=True)
                                    │
                                    ▼
                        Dashboard: Key #1 badge → 🔴
                                   Key #2 badge → 🟢
                                   Failover counter +1
```

### 5.3 Flow Tambah Key dari Dashboard
```
User klik [+ Add Key] → Input key → POST /api/keys
                                        │
                                        ▼
                              add_api_key(new_key)
                              ├── Validasi (duplikat? kosong?)
                              ├── Tambah ke API_KEYS[]
                              ├── Set status "Standby"
                              └── Update .env file
                                        │
                                        ▼
                              Dashboard refresh → key baru muncul 🔵
```

---

## 6. Keamanan

| Aspek | Implementasi |
|-------|-------------|
| **API Key Masking** | Key ditampilkan hanya 15 karakter pertama + `...` — tidak pernah full key |
| **HTTPS** | Seluruh komunikasi via SSL (sertifikat Let's Encrypt sudah terpasang) |
| **Tidak Ada Auth Dashboard** | Dashboard hanya bisa diakses oleh yang tahu URL-nya. Untuk MVP ini cukup. Bisa ditambah Basic Auth di kemudian hari |
| **Input Sanitization** | Key yang dimasukkan akan di-strip whitespace dan divalidasi format |
| **Env Persistence** | Key baru ditulis ke `.env` agar tetap ada setelah server restart |

> ⚠️ **Catatan:** Dashboard ini bersifat admin panel tanpa autentikasi. Pastikan URL tidak disebarkan ke publik. Jika perlu keamanan lebih, bisa ditambahkan Basic Auth atau token sederhana di iterasi berikutnya.

---

## 7. Urutan Implementasi (Step-by-Step)

| Step | File | Tugas | Estimasi |
|------|------|-------|----------|
| 1 | `config.py` | Tambah fungsi `add_api_key()`, `remove_api_key()`, `reset_key_status()`, `get_masked_keys()` | 15 menit |
| 2 | `kimchi.py` | Integrasikan `add_request_log()` ke handler stream & non-stream | 10 menit |
| 3 | `kimchi.py` | Buat endpoint `GET /api/status` | 10 menit |
| 4 | `kimchi.py` | Buat endpoint `POST /api/keys`, `DELETE /api/keys`, `POST /api/keys/reset` | 15 menit |
| 5 | `kimchi.py` | Buat endpoint `GET /dashboard` + seluruh HTML/CSS/JS inline | 45 menit |
| 6 | Lokal | Test dashboard di `http://localhost:4000/dashboard` | 10 menit |
| 7 | VPS | Upload `config.py` + `kimchi.py` ke VPS, restart service | 10 menit |
| 8 | VPS | Verifikasi `https://routers.iyantama.tech:4000/dashboard` | 5 menit |

**Total estimasi: ~2 jam**

---

## 8. Verifikasi

### 8.1 Test Lokal
- [ ] Jalankan server lokal → buka `http://localhost:4000/dashboard`
- [ ] Pastikan metrics card menampilkan data (uptime, requests, dll.)
- [ ] Kirim beberapa request via test script → pastikan log muncul di tabel live activity
- [ ] Tambah key baru dari form → pastikan muncul di list dengan badge `Standby`
- [ ] Reset key `Limited` → pastikan badge berubah ke `Standby`

### 8.2 Test VPS
- [ ] Buka `https://routers.iyantama.tech:4000/dashboard` → halaman muncul dengan benar
- [ ] Kirim request coding dari Claude Code → dashboard menampilkan log real-time
- [ ] Tambah key dari dashboard → pastikan persisten setelah restart service

---

## 9. Teknologi yang Digunakan

| Layer | Teknologi |
|-------|-----------|
| Backend | Python 3.10+, FastAPI, httpx, uvicorn |
| Frontend | Vanilla HTML5, CSS3 (custom properties, animations), JavaScript ES6+ (fetch API) |
| Font | Google Fonts — Inter |
| Server | VPS Ubuntu, SSL Let's Encrypt, systemd service |
| Styling | Pure CSS (dark theme, HSL colors, blur backdrop) — **tanpa Tailwind** |

---

## 10. Batasan & Catatan

1. **In-memory state:** Semua metrics (request count, key statuses) hilang saat server restart. Ini acceptable untuk MVP karena yang penting adalah monitoring real-time, bukan historical data.
2. **Single-process:** Dashboard dan proxy berjalan di proses yang sama. Tidak ada overhead tambahan.
3. **Polling vs WebSocket:** Menggunakan polling 2 detik (bukan WebSocket) untuk kesederhanaan. Bisa di-upgrade ke WebSocket jika diperlukan.
4. **Env file write:** Saat menambah/hapus key, file `.env` di-update langsung. Ini memastikan persistensi, tapi perlu hati-hati agar format `.env` tidak rusak.

---

## 11. Kemungkinan Pengembangan Selanjutnya (Opsional / Masa Depan)

- 🔐 Basic Auth / token untuk proteksi dashboard
- 📊 Grafik chart (request per menit, latensi rata-rata)
- 🔔 Notifikasi (webhook/Telegram) saat semua key terkena limit
- 📱 Responsive mobile layout
- 🌐 Nginx reverse proxy agar bisa akses tanpa port `:4000`
- 💾 SQLite untuk persistensi log history

---

> **Menunggu approval sebelum mulai implementasi. ✅ / ❌ ?**
