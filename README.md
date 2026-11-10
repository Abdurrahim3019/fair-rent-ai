# 🏠 Ədalətli Kirayə

Bakıda ev, ofis və kommersiya obyektlərinin kirayə qiymətinin bazara uyğun olub-olmadığını yoxlayan tətbiq.

## Rejimlər
1. **Elanı yoxla** — bina.az linki → bazar aralığı, hökm, danışıq mesajı.
2. **Bölgə üzrə bax** — əmlak növü + rayonlar + sahə + büdcə → rayon müqayisəsi, ən sərfəli bölgə.

## Lokal quraşdırma
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # açarları doldur
.venv/bin/pytest -q
```

## Status
- [x] Mərhələ 1: skelet, modellər, statistika + testlər
- [x] Mərhələ 2: scraper (requests+BS4 elan səhifəsi, Selenium axtarış, keş, rate limit)
- [x] Mərhələ 3: seed data (707 elan: 108 mənzil, 28 həyət evi, 272 ofis, 298 obyekt)
- [x] Mərhələ 4: minimal UI demo (link → oxşar elanlar → statistika → hökm)
- [x] Mərhələ 5: tools + ReAct agent + RunnableParallel + LangSmith (canlı sınaqdan keçdi: ~7–9 s, ~$0.001/analiz)
- [x] Mərhələ 6: LangGraph HITL (interrupt → data_editor → Command(resume)), danışıq mesajı
- [x] Mərhələ 7: bazar aralığı qrafiki (Altair), cədvəl görünüşü, UI cilalama
- [x] Mərhələ 8: "Bölgə üzrə bax" tabı (paralel rayon müqayisəsi, qrafik, xülasə, "Bu elanı yoxla")
- [x] Mərhələ 9: Docker (lokal build + smoke test keçdi), deploy təlimatı

## Scraping qeydləri
- **Elan səhifəsi** (`/items/<id>`) serverdə render olunur → `requests` + BS4 ilə oxunur (JSON-LD + `data-cy` atributları).
- **Axtarış səhifəsi** JS ilə yüklənir → headless Chrome (Selenium) ilə render olunur, sonsuz scroll (son karta scroll → ~32 yeni kart) = "səhifə". Diqqət: səhifədəki "Daha çox" linki VIP blokuna aiddir, pagination deyil.
- URL formatı: `https://bina.az/{rayon-slug}/kiraye/{menziller|heyet-evleri|ofisler|obyektler}?room_ids[]=2`
- Günlük kirayələr, VIP bloklar və absurd qiymətlər (1 m² > 150 AZN/ay) çıxarılır.

```bash
.venv/bin/python -m scripts.fetch_listing https://bina.az/items/6603033
```

## Seed data
```bash
.venv/bin/python -m scripts.seed_data            # ~7 dəq, 200-ə yaxın elan
.venv/bin/python -m scripts.seed_data --stats    # DB statistikası
.venv/bin/python -m scripts.import_csv data/listings.csv            # CSV idxal (ehtiyat)
.venv/bin/python -m scripts.import_csv --export data/listings.csv   # DB → CSV
```

## Lokal işə salma
```bash
.venv/bin/streamlit run app.py
```

## Agent arxitekturası
```
URL ─► RunnableParallel ─┬─ listing            (bina.az, requests)
                         ├─ district_snapshot  (DB, rayon medianları)
                         └─ comparables_seed   (DB keşi)
        └─► ReAct agent (create_agent + ToolStrategy(CandidateSelection))
               tools: search_comparables · widen_search · compute_market_stats
        └─► stats.py (IQR, median, p25/p75) ─► deterministik hökm
```
- Agent yalnız **id seçir**; qiymətlər həmişə DB-dən götürülür. Uydurma id, başqa əmlak növü və hədəfin özü atılır.
- API açarı yoxdursa və ya agent xəta verirsə → deterministik `find_comparables()` (tətbiq oflayn da işləyir).
- LangSmith metadata: `listing_id`, `district`, `property_type`, `thread_id`; tag-lar: `edaletli-kiraye`, əmlak növü.
- Xərc: `UsageMetadataCallbackHandler` → `config.MODEL_PRICES` cədvəli ilə $ hesablanır, UI-nin altında göstərilir.

### Ardıcıl vs paralel (ölçmə, 05.11.2026)
| | Ardıcıl | Paralel |
|---|---|---|
| Ümumi | ~2.05 s | ~1.98 s |

Komponentlər: elan sorğusu 0.31 s (+ rate-limit gözləməsi ~1.7 s), rayon statistikası 0.011 s, keşdən oxşar elanlar 0.003 s.
**Nəticə:** DB addımları çox sürətli olduğundan paralel icra hazırda ~0.1 s qazandırır; vaxtın əsas hissəsi nəzakətli scraping gözləməsidir. Paralellik `comparables_seed` canlı Selenium axtarışı etdikdə (~5 s) real qazanc verəcək.

> **LangSmith EU hesabı:** `.env`-də `LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com` olmalıdır, əks halda 403 Forbidden.

## LangGraph axını (`src/graph.py`)
```
START → parallel_gather → research_agent → human_review (interrupt) → compute_verdict → write_message → END
```
- `human_review`: `interrupt({"listing", "candidates", "remove_reasons"})` → UI `st.data_editor` → `Command(resume={"approved_ids": [...], "removed_reasons": {...}})`.
- Checkpointer: `MemorySaver`; `thread_id` = hər analiz üçün yeni UUID (`st.session_state`).
- 5-dən az təsdiqlənmiş elan → "məlumat azdır".
- `write_message`: LLM yalnız mətn yazır; qarşı təklif deterministikdir (yalnız "baha" halında, median 50-yə aşağı yuvarlaqlaşdırılır, elan qiymətindən heç vaxt yuxarı deyil). LLM yoxdursa → şablon mesaj.

## "Bölgə üzrə bax" (`src/area.py`)
```
AreaQuery → [istəyə bağlı: ardıcıl canlı yeniləmə, 1 brauzer] → RunnableParallel (hər rayon bir qol, DB)
          → stats.compare_districts (təmiz Python) → LLM xülasəsi (+ uyğunluq yoxlaması, əks halda şablon)
```
- Ən sərfəli bölgə = kifayət qədər elanı (≥5) və büdcəyə uyğun ən az 1 elanı olan, 1 m² medianı ən aşağı rayon.
- Qrafik **median aylıq qiyməti** göstərir (büdcə ilə eyni vahid — AZN), 1 m² qiyməti isə yazı və cədvəldədir; iki fərqli vahidi bir oxda qarışdırmamaq üçün.
- LLM xülasəsi cədvəllə ziddiyyət təşkil edərsə (məs. kifayət məlumatı olan rayona "məlumat azdır" deyərsə) avtomatik şablon xülasəyə keçilir.

## Deploy (DigitalOcean Droplet)

> Server IP-si bina.az tərəfindən bloklana bilər, ona görə tətbiq **öz kompüterində yığılmış** `data/app.db` ilə işləyir. Canlı scraping alınmasa belə, "Nümunə elanla sına" və "Bölgə üzrə bax" işləyir.

**1. Lokal hazırlıq (öz kompüterində)**
```bash
.venv/bin/python -m scripts.seed_data --stats      # DB-də elan olduğunu yoxla
.venv/bin/pytest -q                                  # testlər keçməlidir
```

**2. Droplet yarat:** Ubuntu 24.04, ən azı 1 GB RAM (Chromium ilə 2 GB), SSH açarı əlavə et.

**3. Serverə qoşul və Docker quraşdır**
```bash
ssh root@<DROPLET_IP>
curl -fsSL https://get.docker.com | sh
```

**4. Kodu köçür**
```bash
git clone <repo-url> edaletli-kiraye && cd edaletli-kiraye
# və ya lokal kompüterdən: rsync -av --exclude .venv --exclude data ./ root@<DROPLET_IP>:~/edaletli-kiraye/
```

**5. `.env` faylını yarat** (`.env.example`-dan; açarları serverdə yaz, git-ə əlavə etmə)
```bash
cp .env.example .env && nano .env
```

**6. Seed DB-ni serverə köçür** (lokal kompüterdən)
```bash
scp data/app.db root@<DROPLET_IP>:~/edaletli-kiraye/data/app.db
```

**7. İşə sal**
```bash
mkdir -p data && chown -R 1000:1000 data       # konteyner istifadəçisi uid 1000
docker compose up -d --build
docker compose ps                              # STATUS: healthy
```
Tətbiq: `http://<DROPLET_IP>:8501` (firewall-da 8501 portu açıq olmalıdır: `ufw allow 8501`).

**8. Domen varsa — HTTPS (Caddy, avtomatik sertifikat)**
```bash
# DNS: A record → <DROPLET_IP>
APP_PORT=127.0.0.1:8501 DOMAIN=kiraye.example.com docker compose --profile https up -d
ufw allow 80 && ufw allow 443
```

**9. Smoke test:** brauzerdə **"Nümunə elanla sına"** → "Təsdiqlə və hesabla" → hökm, qrafik və mesaj görünməlidir.

**Canlı scraping konteynerdə (istəyə bağlı):** `WITH_CHROMIUM=1 docker compose up -d --build` (image ~400 MB böyüyür).

**Yeniləmə:** lokal `scripts.seed_data` → `scp data/app.db ...` → `docker compose restart app`.
