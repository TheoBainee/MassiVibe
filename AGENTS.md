Tu es un expert Python senior. Maintiens et développe MassiVibe, outil professionnel d'historisation périodique des données OHLCV multi-instruments via l'API REST de Massive.com.

### Objectifs principaux
- Récupérer et historiser les chandeliers OHLCV (1min par défaut) pour les 4 types principaux : futures, stocks, forex, indices (options = scaffold).
- Utiliser **Polars** en priorité (Pandas uniquement si vraiment nécessaire).
- Tout le stockage se fait en **fichiers Parquet** (layout multi-type data/{raw,aggregate}/{type}/{symbol}/...).
- Caches intelligents et TTL pour /futures/v1/contracts et /stocks/v1/splits (corporate actions).
- Cascade automatique type-aware (contracts/splits → fetch → aggregate → query).

### Configuration
- Système clair : pydantic-settings + tomllib (XDG ~/.config/massivibe/ prioritaire, fallback repo).
- Fichiers :
  - ~/.config/massivibe/.env (API key, jamais commité)
  - ~/.config/massivibe/config.toml (instruments par type, fetch, storage, futures/stocks, logging, chart...)
- Paramètres clés configurables :
  - Instruments par type (futures = ["NQ", "ES", ...], stocks, forex, indices)
  - timeframe = "1min"
  - overlap_buffer_days
  - history_months par type (défaut 24, 60 pour indices)
  - days_before_expiry (futures rollover)
  - logging level (DEBUG par défaut)
  - data_dir, cache_dir, etc.

### Logique d'historisation
1. **Premier run** : récupérer depuis (today - history_months.<type>).
2. **Runs suivants** : depuis (dernière date agrégée - overlap_buffer_days).
3. Extension arrière automatique si history_months est augmenté.
4. À chaque exécution :
   - Sauvegarder un **dump pseudo-brut** (1 fichier par ticker + run_ts).
   - Mettre à jour l'agrégé.
5. **Définition "dump pseudo-brut"** :
   - Ce ne sont **pas** les réponses JSON brutes de l'API.
   - Ce sont les données API après normalisation minimale au format interne canonique (conversion timestamps ns/ms → Datetime[ns], normalisation champs, ajout colonnes d'identité symbol/instrument_type/product_code/run_id, casts volume→Int32 etc.).
   - Choix volontaire pour praticité et performance.
   - **Contrainte absolue (même en alpha)** : il doit toujours être possible de reconstruire l'agrégat complet à partir des dumps existants (read_all_runs + concat + dédup sur (window_start, ticker) + casts).

### Dumps & Stockage
- Layout : data/raw/{type}/{symbol}/{ticker}/{run_ts}.parquet (+ .meta.json sidecar)
- Pour futures : ticker = contrat (ESM5 etc.)
- Pour stocks/forex/indices : ticker = symbole
- data/aggregate/{type}/{symbol}.parquet (unique par instrument)
- Agrégation générique (pas de logique rollover dedans) : concat dumps, dédup keep=last, Categorical + Int32 casts, régénérée après chaque fetch.
- Sidecar .meta.json systématique sur tous les Parquet.

### Gestion des contrats et rollovers (futures)
- Cache /futures/v1/contracts intelligent (TTL, snapshots échelonnés pour contrats expirés).
- Rollover : days_before_expiry (défaut 7) → rollover_date = last_trade_date - N jours.
- Ex : contrat expire vendredi 19 → dernier jour conservé = vendredi 12.
- RolloverChain + RolloverSegment pour active_contract, continuous_segments, tick_size.
- Pour query : gaps naturels conservés par défaut.

### Corporate actions (stocks)
- Cache /stocks/v1/splits (et dividends scaffold).
- Ajustement split appliqué **à la query** (stockage en prix bruts avec adjusted=false).
- --no-split pour prix bruts.

### Pipeline & Architecture
- Fetchers multi-type (FuturesFetcher, StocksFetcher, V2SingleSymbolFetcher, OptionsFetcher scaffold).
- Cascade type-aware dans pipeline/cascade.py.
- Agrégateur générique (polars unique + casts).
- Query : reader + resampler + adjust (split).
- CLI complète + chart serveur.

### Logging & Observabilité
- DEBUG par défaut :
  - Tous les appels API (endpoint + params, clé masquée).
  - Skips grâce au cache.
  - Pagination : extrait des résultats (avec window_start) à chaque page.
- Retry Tenacity (429 avec Retry-After, 5xx backoff).

### Tests & Qualité
- Tests pytest + respx (mocks API).
- Commentaires clairs et explicatifs.
- Structure propre (src/ layout, type hints stricts, ruff + mypy).
- **Avant tout gros backfill (2 ans+)** : tester obligatoirement la récupération fonctionnelle de l'historique complet d'un contrat entier (utiliser/améliorer scripts/test_single_contract.py) pour valider workflow, perf, pagination, etc.

### Contraintes techniques
- uv recommandé pour deps + env.
- Python >= 3.11.
- Polars + pyarrow prioritaires.
- Pas de pandas.
- Code maintenable, prêt pour review.

### Documentation
- https://massive.com/docs/llms.txt
- README.md, docs/TECHNICAL_DESIGN.md, docs/MULTI_TYPE.md
- Maintenir AGENTS.md à jour (ce fichier est la source de vérité pour les consignes de dev).

### Notes alpha
- Version 0.1 alpha.
- Pas de garantie de rétrocompatibilité des formats de stockage ou layouts.
- La seule contrainte forte : pouvoir reconstruire les agrégats depuis les dumps pseudo-bruts existants.
- Pas de dump JSON brut supplémentaire (les dumps Parquet normalisés suffisent).

Commence/maintiens par : arborescence propre, pyproject.toml (uv/hatch), config (pydantic+toml), implémentation pipeline + fetchers + storage, tests.
