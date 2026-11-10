# Təqdimat üçün terminal əmrləri

Bütün əmrləri layihə qovluğunda işlət:
```bash
cd ~/PycharmProjects/SynapseCode/4_month_project/Project_1
```

---

## 0. Təqdimatdan 10 dəqiqə əvvəl (hazırlıq)

```bash
source .venv/bin/activate          # virtual mühiti aktiv et (sonra sadəcə python, pytest yaz)
```
```bash
pytest -q                          # testlər keçirmi? → "65 passed" görməlisən
```
```bash
streamlit run app.py               # tətbiqi aç → http://localhost:8501
```
> Brauzerdə bir dəfə **"Nümunə elanla sına"** bas ki, hər şey isinsin. İnternet zəifdirsə də bu düymə işləyir (bazadan oxuyur).

---

## 1. Problem (20 san) — terminal lazım deyil
"Bakıda kirayə axtaranlar — ev və ya biznes üçün yer — qiymətin normal olub-olmadığını bilmir."

---

## 2. Canlı demo (70 san) — brauzerdə

**A. Elanı yoxla** (tab 1)
1. bina.az linkini yapışdır, təxminini yaz (məs. 500) → **Analiz et**
2. Agentin addımları canlı görünür → oxşar elanlar cədvəli
3. Təmirsiz / fərqli birini ✅-dən çıxar → **Təsdiqlə və hesabla**
4. Hökm, qrafik, danışıq mesajı

**B. Bölgə üzrə bax** (tab 2) — sahibkar ssenarisi
- **Obyekt** · Nəsimi, Yasamal, Nərimanov · 40–120 m² · büdcə 1500 → **Bazara bax**
- 🏆 ən sərfəli bölgə, cədvəl, qrafik → bir elanda **"Bu elanı yoxla"**

---

## 3. Texniki hissə (30 san) — terminalda göstər

**Scraper işləyir (real bina.az linki → struktur məlumat):**
```bash
python -m scripts.fetch_listing https://bina.az/items/6603033
```

**Bazada nə qədər məlumat var:**
```bash
python -m scripts.seed_data --stats
```

**Testlər (LLM və scraper mock edilib, HITL yoxlanılır):**
```bash
pytest -q
```
```bash
pytest tests/test_graph.py -v      # interrupt → resume → hökm dəyişir
```

**LangSmith:** brauzerdə https://smith.langchain.com → layihə `edaletli-kiraye` → son run →
`edaletli_kiraye_analysis` → `parallel_gather` (3 paralel addım) → `research_agent` → tool-lar.
Metadata-da `listing_id`, `district`, `n_comparables`, `cost_usd` görünür.

**Deyiləcək rəqəmlər:**
- Bir analiz ≈ **$0.001** (limit $0.01), HITL-ə qədər **7–9 saniyə** (limit 30 s)
- Bazada **707 real elan** (mənzil, həyət evi, ofis, obyekt)
- **65 test** keçir; hökm deterministikdir — eyni giriş, eyni nəticə
- Niyə HITL: elanların bir qismi əslində uyğun deyil (təmirsiz, fərqli rayon) — istifadəçi çıxarır, hökm dəqiqləşir

---

## 4. Docker (soruşsalar)
```bash
docker compose up -d               # → http://localhost:8502
```
```bash
docker compose ps                  # STATUS: healthy
```
```bash
docker compose down                # dayandır
```

---

## Problem olarsa

| Problem | Həll |
|---|---|
| `command not found: streamlit` / `pytest` | `source .venv/bin/activate` unudulub |
| `address already in use` (8501) | başqa streamlit açıqdır → həmin terminalda `Ctrl+C`, və ya `streamlit run app.py --server.port 8503` |
| bina.az linki açılmır / internet yoxdur | **"Nümunə elanla sına"** düyməsini istifadə et |
| LLM xətası / açar bitib | tətbiq avtomatik "sadə rejim"ə keçir, hökm yenə hesablanır |
| Tətbiqi dayandırmaq | terminalda `Ctrl+C` |
