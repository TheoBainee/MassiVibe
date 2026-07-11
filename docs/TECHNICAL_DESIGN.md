# MassiVibe — Documentation Technique

> Historisation périodique des données OHLCV 1 minute des contrats futures via l'API REST de Massive.com.

---

## 1. Vision et Objectifs

MassiVibe est un projet Python d'historisation des chandeliers OHLCV 1 minute des contrats futures CME/CBOT/NYMEX/COMEX via l'API Massive.com.

Objectifs principaux :

- Récupérer et historiser **chaque semaine** les chandeliers OHLCV 1 minute.
- Stocker toutes les données en **fichiers Parquet** via **Polars**.
- Mettre en cache les contrats (`/futures/v1/contracts`), un cache **par `product_code`** avec TTL configurable (défaut 30 jours).
- Gérer le **rollover** automatique (switch J-7 avant `last_trade_date`).
- Fournir une CLI complète avec **cascade automatique** des dépendances fonctionnelles.
- Permettre la normalisation des prix en **multiples entiers de tick size** (OHLC + settlement → Int32), activable via la commande `query`.

---

## 2. Architecture du Projet

### 2.1 Arborescence

```
MassiVibe/
├─ AGENTS.md
├─ README.md
├─ .gitignore
├─ .env.example
├─ .env                           # non committé, créé par setup-key
├─ pyproject.toml
├─ config.toml
├─ docs/
│  └─ TECHNICAL_DESIGN.md         # cette documentation
├─ scripts/
│  └─ test_single_contract.py     # validation pré-backfill
├─ tests/
│  ├─ conftest.py
│  ├─ test_config.py
│  ├─ test_client.py
│  ├─ test_contracts_cache.py
│  ├─ test_aggregates_fetch.py
│  ├─ test_parquet_io.py
│  ├─ test_raw_dumps.py
│  ├─ test_aggregator.py
│  ├─ test_rollover.py
│  ├─ test_historian.py
│  ├─ test_reader.py
│  ├─ test_cascade.py
│  └─ test_cli.py
└─ src/massivibe/
   ├─ __init__.py
   ├─ __main__.py
   ├─ cli.py                      # CLI: setup-key, config, contracts, fetch, aggregate, query, status
   ├─ config.py                   # pydantic-settings: .env + config.toml
   ├─ logging_setup.py            # rich handler + fichier rotation, helpers DEBUG
   ├─ api/
   │  ├─ __init__.py
   │  ├─ client.py                # MassiveClient: httpx, Bearer, throttle, retry tenacity, pagination
   │  ├─ contracts.py             # fetch_contracts(product_code) paginé
   │  └─ aggregates.py            # fetch_aggs(ticker, resolution, gte, lte) paginé
   ├─ contracts/
   │  ├─ __init__.py
   │  ├─ cache.py                 # ContractsCache par product_code + sidecar .meta.json
   │  └─ rollover.py              # RolloverChain: segments, front-month, J-7, to_table()
   ├─ storage/
   │  ├─ __init__.py
   │  ├─ parquet_io.py            # read/write parquet via polars + sidecar .meta.json (toujours)
   │  ├─ raw_dumps.py             # data/raw/{product_code}/{ticker}/{run_ts}.parquet
   │  └─ aggregate_cache.py       # data/aggregate/{product_code}_continuous.parquet
   ├─ pipeline/
   │  ├─ __init__.py
   │  ├─ cascade.py               # ensure_contracts, ensure_raw_dumps, ensure_aggregate
   │  ├─ historian.py             # orchestration du fetch
   │  └─ aggregator.py            # merge + dedup (window_start, ticker) keep=last
   └─ query/
      ├─ __init__.py
      └─ reader.py                # query() + normalize_tick_size + check_ticksize_accuracy (bilan qualité)
```

### 2.2 Choix technologiques

| Domaine | Choix | Raison |
|---|---|---|
| Gestion dépendances | `uv` + `hatchling` | Rapide, moderne, `src/` layout |
| Dataframes | `polars` ≥ 1 | Performance, lazy eval, types `Categorical` optimisés |
| Stockage | `pyarrow` + Parquet | Columnar, compressé, schéma typé |
| HTTP | `httpx` | Sync, interceptors, retries propres |
| Config | `pydantic-settings` + `tomllib` | Secrets via `.env`, params via `config.toml` |
| Retry | `tenacity` | Retry avancé, `Retry-After`, exponential backoff |
| Logging | `rich` + fichier rotation | Logs colorés DEBUG, un seul levier `level` |
| Tests | `pytest` + `respx` | Mock httpx, fixtures JSON de la doc API |
| Qualité | `ruff` + `mypy` | Lint, format, types stricts |
| Python | ≥ 3.11 | `tomllib` natif, type hints modernes |

---

## 3. Configuration

### 3.1 Séparation des préoccupations

**`.env`** (secrets, jamais committé) — chargé via `pydantic-settings` avec `env_prefix = "MASSIVE_"` :

```
MASSIVE_API_KEY=...
MASSIVE_BASE_URL=https://api.massive.com
```

**`config.toml`** (paramètres métier, committé) — chargé via `tomllib` :

```toml
[instruments]
product_codes = ["NQ", "ES", "RTY", "YM"]   # codes directs, pas d'appel /products

[fetch]
timeframe = "1min"
overlap_buffer_days = 1                      # recouvrement entre runs (jours)
history_months = 24                           # historique ciblé (mois) ; si augmenté -> backfill arrière auto
requests_per_minute = 10                      # throttle self-imposé (0 = pas de throttle)
page_limit = 50000                            # limite par page pour /aggs (max API = 50000)
contracts_page_limit = 1000                   # limite par page pour /contracts (max API = 1000)
max_retries = 6                               # retry tenacity : 6 tentatives

[storage]
data_dir = "./data"
raw_dumps_subdir = "raw"
aggregate_subdir = "aggregate"
contracts_cache_dir = "data/cache/contracts"  # un fichier Parquet par product_code
log_dir = "./logs"                            # dossier des fichiers de log

[contracts_cache]
ttl_days = 30                                 # TTL du cache /contracts par product_code

[rollover]
days_before_expiry = 7                        # switch J-7 avant last_trade_date

[tests]
data_quality_trigger = 0.1                    # tolérance pour le test de qualité tick_size (cf §8.3)
                                              # si ABS((prix/tick) - round(prix/tick)) > trigger * tick -> mauvaise qualité

[logging]
level = "DEBUG"                               # DEBUG active appels API + skips cache + extrait pagination (window_start inclus)
```

### 3.2 Modèle `Settings`

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MASSIVE_", env_file=".env")

    # Secrets (.env)
    api_key: SecretStr
    base_url: str = "https://api.massive.com"

    # Paramètres (config.toml)
    product_codes: list[str] = ["NQ", "ES", "RTY", "YM"]
    timeframe: str = "1min"
    overlap_buffer_days: int = 1
    history_months: int = 24
    requests_per_minute: int = 10
    page_limit: int = 50000
    contracts_page_limit: int = 1000
    max_retries: int = 6
    data_dir: str = "./data"
    raw_dumps_subdir: str = "raw"
    aggregate_subdir: str = "aggregate"
    contracts_cache_dir: str = "data/cache/contracts"
    log_dir: str = "./logs"
    contracts_ttl_days: int = 30
    days_before_expiry: int = 7
    data_quality_trigger: float = 0.1
    log_level: str = "DEBUG"
```

Validations pydantic : `overlap_buffer_days >= 0`, `days_before_expiry >= 0`, `product_codes` non vide, `history_months >= 1`, `requests_per_minute >= 0`, `max_retries >= 1`, `page_limit` et `contracts_page_limit` dans les bornes API (1..50000 et 1..1000), `data_quality_trigger > 0`.

> **Note** : `normalize_tick_size` n'est pas un paramètre de configuration — c'est un **flag de la commande `query`** (`--normalize-tick-size`). La normalisation se fait à la lecture, pas au stockage, pour garder l'agrégat en `Float64` (données brutes) et permettre plusieurs formats de consommation. Voir §8.3 et §12.1.

---

## 4. Client API (`api/client.py`)

### 4.1 Authentification

Toutes les requêtes envoient le header `Authorization: Bearer <MASSIVE_API_KEY>`. La clé est lue depuis `.env` via `SecretStr` (pydantic) et jamais loggée — masquée `****` dans tous les logs.

### 4.2 Throttle self-imposé

Le client impose un délai minimum inter-requête calculé depuis `requests_per_minute` :

- `requests_per_minute = 10` → délai minimum = `60 / 10 = 6 secondes` entre chaque appel API.
- `requests_per_minute = 0` → pas de throttle (on s'appuie uniquement sur le retry 429).

Implémentation : on garde le timestamp de la dernière requête et on calcule le temps restant ; si positif, `time.sleep` avant d'envoyer la nouvelle requête. Ce throttle prévient la plupart des 429 en amont.

### 4.3 Retry via Tenacity

La méthode `get()` est décorée avec `@retry` (tenacity). Configuration :

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(max_retries),              # défaut = 6
    wait=wait_exponential(multiplier=1, min=1, max=60),# backoff 1s, 2s, 4s... plafonné à 60s
    retry=retry_if_exception_type((RateLimitError, ServerError)),
    reraise=True,
)
def get(self, path: str, **params) -> dict:
    ...
```

**Comportement détaillé** :

1. **429 Too Many Requests** : Le serveur renvoie 429 avec un header `Retry-After` (secondes). Si présent, le client lit ce header et attend **exactement** cette durée avant de réessayer (prioritaire sur le backoff). Si `Retry-After` est absent, il applique un **exponential backoff** : 1s, 2s, 4s, 8s, 16s, 32s (plafonné à 60s), jusqu'à 6 tentatives.

2. **5xx Server Error** : Exponential backoff, jusqu'à 6 tentatives.

3. **4xx (sauf 429)** : Erreur client non retryable (400, 403, 404...) — lève immédiatement une exception, pas de retry.

4. **Après 6 tentatives échouées** : `tenacity` lève `TenacityError` (propagée) avec un log ERROR.

5. **Logs de retry** : chaque retry logge `WARNING "Retry 2/6 après 429 (Retry-After=12s)"` ou `WARNING "Retry 3/6 après 500 (backoff=4s)"` pour observabilité.

### 4.4 Pagination

L'API Massive utilise `next_url` pour la pagination. Le client :

1. Effectue la requête initiale avec les params.
2. Si `next_url` présent dans la réponse → effectue une nouvelle requête vers cette URL.
3. À chaque page, log DEBUG d'un extrait des **5 premières lignes de `results`** avec conversion explicite de `window_start` (timestamp nanosecondes) → datetime lisible UTC. **La colonne `window_start` (date du chandelier) est systématiquement incluse** dans l'extrait pour repérer visuellement une boucle infinie.
4. Concatène tous les `results` via Polars.
5. S'arrête quand `next_url` est absent.

**Pas de `max_pages`** : on fait confiance à la pagination `next_url`. Le log d'extrait à chaque page permet de détecter manuellement une boucle.

Format de log : `DEBUG [page 3] 5 premières candles: window_start=2026-07-11T18:30:00Z ticker=ESM5 close=... | window_start=... | ...`.

### 4.5 Logs d'appel API

Chaque appel logge en DEBUG : `GET /futures/v1/contracts?product_code=ES&limit=1000` (endpoint + params, clé masquée).

---

## 5. Cache des Contrats (`contracts/cache.py`)

### 5.1 Structure du cache

Un cache **par `product_code`** (pas un cache global, pas un fichier par échéance) :

```
data/cache/contracts/
├─ ES.parquet        # contrats du produit ES
├─ ES.meta.json      # métadonnées (sidecar)
├─ NQ.parquet
├─ NQ.meta.json
├─ RTY.parquet
├─ RTY.meta.json
├─ YM.parquet
└─ YM.meta.json
```

### 5.2 Sidecar `.meta.json` — principe général

Un **sidecar** est un fichier annexe attaché à un fichier principal, portant le même nom de base avec une extension différente, qui stocke des métadonnées complémentaires. On les place côte à côte :

```
data/cache/contracts/ES.parquet       # le cache contrats (données tabulaires)
data/cache/contracts/ES.meta.json     # métadonnées de ce cache (sidecar)
```

**Pourquoi séparé du Parquet ?** Stocker `last_fetched_at` dans le Parquet polluerait les données métier avec de la plomberie. Un fichier JSON séparé est plus simple à lire/écrire (1 ligne), ne parasite pas les `read_parquet` métier, et reste lisible/éditable manuellement.

### 5.3 Généralisation du sidecar à TOUS les fichiers Parquet

**Le sidecar `.meta.json` est appliqué systématiquement à tous les fichiers Parquet écrits par MassiVibe**, via `storage/parquet_io.py`. La fonction `write_parquet(df, path, **extra_meta)` écrit automatiquement le fichier Parquet **et** le sidecar `.meta.json` à côté.

Le sidecar contient toujours :

```json
{
  "schema_version": "1.0",
  "created_at": "2026-07-11T18:30:00Z",
  "row_count": 12345,
  "columns": ["window_start", "ticker", "open", ...],
  "dtypes": {"window_start": "Datetime[ns]", "ticker": "Categorical", ...},
  "file_size_bytes": 1234567
}
```

Plus des champs spécifiques par type de fichier :

| Fichier Parquet | Champs additionnels du sidecar |
|---|---|
| `contracts/{product_code}.parquet` | `product_code`, `source_url` (`/futures/v1/contracts?product_code=ES`), `last_fetched_at`, `fetch_duration_ms` |
| `raw/{product_code}/{ticker}/{run_ts}.parquet` | `product_code`, `ticker`, `run_ts`, `source_url` (`/futures/v1/aggs/{ticker}?resolution=1min&...`), `window_start_min`, `window_start_max`, `page_count` |
| `aggregate/{product_code}_continuous.parquet` | `product_code`, `aggregated_at`, `source_dump_count` (nb dumps fusionnés), `dedup_removed_count`, `window_start_min`, `window_start_max` |

L'utilitaire `storage/parquet_io.py` expose `write_parquet(df, path, **extra_meta)` et `read_meta(parquet_path)` qui lit le sidecar associé. Toutes les fonctions de stockage (`raw_dumps`, `aggregate_cache`, `contracts_cache`) utilisent cet utilitaire — aucun Parquet n'est écrit sans son sidecar.

### 5.4 Logique de cache des contrats

```python
class ContractsCache:
    def __init__(self, product_code: str, settings: Settings):
        self.product_code = product_code
        self.parquet_path = f"{settings.contracts_cache_dir}/{product_code}.parquet"
        self.meta_path = f"{settings.contracts_cache_dir}/{product_code}.meta.json"

    def get(self, client: MassiveClient, force_refresh: bool = False) -> pl.DataFrame:
        if not force_refresh and self._is_fresh():
            log.debug(f"Cache skip: contrats {self.product_code} frais (last_fetched=...)")
            return self._read_parquet()
        log.info(f"Cache miss/périmé: fetch /contracts pour {self.product_code}")
        df = fetch_contracts(client, self.product_code, page_limit=settings.contracts_page_limit)
        self._write(df)  # écrit Parquet + sidecar .meta.json
        return df

    def _is_fresh(self) -> bool:
        meta = read_meta(self.parquet_path)
        if not meta: return False
        age = now - meta["last_fetched_at"]
        return age < timedelta(days=settings.contracts_ttl_days)
```

TTL par défaut : 30 jours (`contracts_ttl_days = 30`).

---

## 6. Rollover (`contracts/rollover.py`)

### 6.1 Règle de rollover

On passe au contrat suivant **1 semaine avant l'expiration**. Exemple : contrat expirant le vendredi 19 → dernier jour conservé = vendredi 12. Les chandeliers à partir du lundi suivant appartiennent au nouveau contrat.

En pratique, le `rollover_date` d'un contrat = `last_trade_date - days_before_expiry` (défaut 7 jours). Tous les chandeliers dont `window_start < rollover_date` appartiennent à ce contrat ; à partir de `rollover_date`, on bascule sur le contrat suivant (front-month suivant).

### 6.2 L'objet `RolloverChain`

`RolloverChain` est l'objet central qui modélise la chaîne continue des contrats d'un produit. Il est construit à partir du `DataFrame` Polars des contrats (issu du cache `/contracts`) et de `days_before_expiry`.

**Attributs** :

```python
class RolloverChain:
    product_code: str                       # ex: "ES"
    contracts: pl.DataFrame                 # tous les contrats du produit (triés par first_trade_date)
    days_before_expiry: int                 # ex: 7
    segments: list[RolloverSegment]         # liste ordonnée des segments actifs
```

**`RolloverSegment`** (dataclass) — un segment représente une période pendant laquelle un contrat donné est le contrat actif (front-month) :

```python
@dataclass
class RolloverSegment:
    ticker: str              # ex: "ESM5"
    first_trade_date: date   # ex: 2025-03-17  — premier jour de trading du contrat
    last_trade_date: date    # ex: 2025-06-13  — dernier jour de trading (expiration)
    settlement_date: date    # ex: 2025-06-13
    rollover_date: date      # ex: 2025-06-06  = last_trade_date - days_before_expiry
    active_from: date        # ex: 2025-03-17  — date à partir de laquelle ce contrat devient le front-month
    active_until: date       # ex: 2025-06-06  — date (exclusive) où on bascule au contrat suivant
    trade_tick_size: float   # ex: 0.25  — taille du tick pour la normalisation des prix
    product_code: str        # ex: "ES"
    name: str                # ex: "E-mini S&P 500 Jun 2025"
```

**Méthodes** :

| Méthode | Signature | Description |
|---|---|---|
| `active_contract(date)` | `(date) -> str` | Retourne le ticker du contrat actif à cette date = le segment dont `active_from <= date < active_until`. |
| `segment_for_ticker(ticker)` | `(str) -> RolloverSegment \| None` | Retourne le segment correspondant à un ticker. |
| `continuous_segments(start, end)` | `(date, date) -> list[RolloverSegment]` | Retourne la liste des segments couvrant la période `[start, end]`. |
| `tick_size_for_ticker(ticker)` | `(str) -> float` | Retourne le `trade_tick_size` du contrat (depuis la colonne `/contracts`). |
| `to_table()` | `() -> pl.DataFrame` | Retourne un DataFrame Polars plat des segments (pour `status`). |
| `__repr__()` | — | Représentation textuelle des segments (un par ligne). |

**Construction** :

1. Trier `contracts` par `first_trade_date` ascendant.
2. Ne garder que les contrats de type `single` (ignorer les `combo`).
3. Pour chaque contrat : calculer `rollover_date = last_trade_date - days_before_expiry`.
4. Déterminer `active_from` et `active_until` en chaînant les contrats : `active_from` du segment N+1 = `rollover_date` du segment N ; `active_until` du segment N = `rollover_date` du segment N (exclusive).
5. Stocker `trade_tick_size` pour chaque segment (utile à `aggregator` pour la normalisation).

### 6.3 Affichage via `status`

La commande `massivibe status` affiche, pour chaque produit, la `RolloverChain` sous forme de tableau (via `to_table()`) :

```
== ES — RolloverChain ==
ticker   first_trade  last_trade   rollover_date  active_from  active_until  tick_size
ESH5     2024-12-16   2025-03-14   2025-03-07     2024-12-16   2025-03-07    0.25
ESM5     2025-03-17   2025-06-13   2025-06-06     2025-03-07   2025-06-06    0.25
ESU5     2025-06-16   2025-09-12   2025-09-05     2025-06-06   2025-09-05    0.25
ESZ5     2025-09-15   2025-12-12   2025-12-05     2025-09-05   2025-12-05    0.25
```

Le `status` affiche aussi l'information "contrat actuellement actif" (front-month = `active_contract(date.today())`).

### 6.4 Ajustement de rollover

- `adjust_rollover = False` (défaut) : on conserve les gaps naturels entre contrats. Chaque chandelier provient du contrat actif à sa date.
- `adjust_rollover = True` : **stub** — lève `NotImplementedError` avec WARNING log ("ajustement non défini — à venir"). Réservé à une implémentation future (méthode Panama ou back-adjusted à définir).

⚠️ **Incompatibilité** : `adjust_rollover=True` et `normalize_tick_size=True` sont **mutuellement exclusifs** (cf §8.3). Le CLI rejette la combinaison avec une erreur explicite.

---

## 7. Historisation (`pipeline/historian.py`)

### 7.1 `run_ts` — identifiant d'exécution

`run_ts` = **horodatage du lancement d'exécution** au format `YYYYMMDDTHHMMSS` (ex: `20260711T183000`). Il identifie de manière unique chaque exécution du pipeline :

- Le dump brut d'un contrat récupéré pendant ce run est sauvegardé dans `data/raw/{product_code}/{ticker}/{run_ts}.parquet`.
- Permet de conserver l'historique des exécutions (un fichier par run, jamais écrasé).
- **Détection "déjà historisé aujourd'hui"** : en inspectant la partie date (`YYYYMMDD`) des `run_ts` existants pour un produit donné.

### 7.2 Logique de détection "déjà fait aujourd'hui"

Avant de lancer un produit, le `historian` vérifie s'il existe un dump avec `run_ts` daté d'aujourd'hui pour ce `product_code` :

- **Si oui** : log `WARNING "Historisation déjà effectuée aujourd'hui (run_ts=20260711T...) — skip. Utilisez --force pour relancer."` et passe au produit suivant.
- **Si `--force`** : relance quand même, crée un nouveau dump avec un `run_ts` plus précis (inclut l'heure pour garantir l'unicité).

### 7.3 Détermination du range à fetcher

Pour chaque produit et chaque contrat de la chaîne de rollover :

1. **Premier run** : range = `(today - history_months)` → `today`. Par défaut 24 mois (plan Basic, 2 ans).

2. **Runs suivants (incrémental)** : range = `(last_date_in_aggregate + 1ns - overlap_buffer_days)` → `today`. Le buffer de recouvrement (= 1 jour) garantit la continuité même en cas de candles manquants.

3. **Extension d'historique** : si `history_months` est augmenté (ex: 24 → 60 après upgrade Developer), le `historian` détecte que la date la plus ancienne en Parquet est plus récente que `today - 60 mois` → lance un **backfill arrière** pour combler (`oldest_existing_date` → `today - 60 mois`). Pas de re-téléchargement de ce qui existe déjà.

### 7.4 Pipeline d'exécution (`fetch`)

```
Pour chaque product_code (NQ, ES, RTY, YM):
    1. Vérifier "déjà fait aujourd'hui" -> WARNING + skip si oui (sauf --force)
    2. ContractsCache.get() -> contrats du produit (auto-refresh si périmé/absent via cascade)
    3. Construire RolloverChain à partir des contrats
    4. Pour chaque contrat actif sur la période cible:
        a. Déterminer le range (premier run vs incrémental vs backfill extension)
        b. fetch_aggs(ticker, resolution=1min, gte, lte) -> DataFrame Polars
        c. Sauver dump brut: data/raw/{product_code}/{ticker}/{run_ts}.parquet (+ sidecar .meta.json)
    5. aggregate(product_code) -> régénérer le cache agrégé (+ sidecar .meta.json)
    6. Log résumé DEBUG (nb contrats, nb candles, durée, cache hits/miss)
```

---

## 8. Stockage (`storage/`)

### 8.1 Schéma Parquet canonique

Tous les DataFrames Polars suivent le même schéma. Les colonnes string répétées (`run_id`, `ticker`, `product_code`) utilisent le type **`Categorical`** de Polars — plus optimisé en mémoire et en schema encoding que `Utf8` pour des colonnes à faible cardinalité (ex: un produit a ~10-20 tickers distincts sur 2 ans de données 1m).

| Colonne | Type Polars | Description |
|---|---|---|
| `window_start` | `Datetime[ns]` | Timestamp du début du chandelier (converti depuis ns API → datetime UTC) |
| `ticker` | `Categorical` | Ticker du contrat (ex: `ESM5`) |
| `open` | `Float64` (ou `Int32` si normalisation) | Prix d'ouverture |
| `high` | `Float64` (ou `Int32` si normalisation) | Prix haut |
| `low` | `Float64` (ou `Int32` si normalisation) | Prix bas |
| `close` | `Float64` (ou `Int32` si normalisation) | Prix de clôture |
| `settlement_price` | `Float64` (ou `Int32` si normalisation) | Prix de settlement |
| `volume` | `Int64` | Volume (nb contrats) |
| `dollar_volume` | `Float64` | Volume en dollars |
| `transactions` | `Int64` | Nombre de transactions |
| `session_end_date` | `Date` | Date de fin de session |
| `product_code` | `Categorical` | Code produit (ex: `ES`) |
| `run_id` | `Categorical` | Identifiant du run (run_ts) |

> **Note sur les types prix** : les colonnes OHLC et `settlement_price` sont stockées en `Float64` dans l'agrégat (données brutes). La conversion en `Int32` (multiples de tick size) se fait **à la lecture** via le flag `--normalize-tick-size` de la commande `query`, pas au stockage. Cela permet de garder un agrégat universel et de servir plusieurs formats de consommation.

### 8.2 Normalisation en multiples de tick size

L'endpoint `/futures/v1/contracts` renvoie une colonne `trade_tick_size` pour chaque contrat. Cette valeur signifie que **toutes les valeurs OHLC et `settlement_price` sont des multiples de ce tick size** (ex: `0.25` pour ES signifie tous les prix sont des quarts de point).

La normalisation est **un flag de la commande `query`** (`--normalize-tick-size`), pas un paramètre de configuration. Elle se fait à la lecture :

```
open_int  = (open  / trade_tick_size).round().cast(Int32)
high_int  = (high  / trade_tick_size).round().cast(Int32)
low_int   = (low   / trade_tick_size).round().cast(Int32)
close_int = (close / trade_tick_size).round().cast(Int32)
settlement_price_int = (settlement_price / trade_tick_size).round().cast(Int32)
```

**Logique** :

1. La `RolloverChain` expose `tick_size_for_ticker(ticker)` — chaque contrat a son propre `trade_tick_size` (depuis la colonne `/contracts`).
2. Le `reader.py` (lors du `query` avec `--normalize-tick-size`) regarde le `ticker` de chaque ligne, récupère le `trade_tick_size` correspondant via la `RolloverChain`, et divise les 5 colonnes (`open`, `high`, `low`, `close`, `settlement_price`) par cette valeur.
3. Les colonnes normalisées remplacent les originales (mêmes noms, `Int32`).

**Pourquoi `Int32` ?** Les prix futures sont des multiples entiers de tick size et tiennent largement dans un `Int32` (ex: ES à 5000 pts / 0.25 = 20000, largement sous 2^31). `Int32` est 2x plus compact que `Float64` et permet de comparer des prix exactement (pas de flottants).

### 8.3 Test de qualité des données (tick size)

Le test de qualité n'est **pas** déclenché automatiquement lors de `--normalize-tick-size`. C'est un flag CLI dédié `--check-ticksize-accuracy` sur la commande `query` qui analyse les données et **affiche un bilan** détaillé, sans modifier les données.

**Formule du test** : pour chaque prix `p` (open, high, low, close, settlement_price) et son `trade_tick_size` `t` :

```
si ABS((p / t) - round(p / t)) > data_quality_trigger * t  ->  donnée non conforme
```

- `data_quality_trigger` (config `[tests]`, défaut `0.1`) = tolérance relative au tick size.
- Avec `0.1` : on accepte une déviation jusqu'à 10% d'un tick. Au-delà, la donnée est considérée non conforme.

**Flag CLI** : `massivibe query <product> --check-ticksize-accuracy`

- N'effectue **aucune conversion** — les données retournées restent en `Float64`.
- Analyse l'ensemble des prix OHLC + `settlement_price` pour chaque ticker (via son `trade_tick_size`).
- Affiche un **bilan** sur stdout :

```
== ES — Bilan qualité tick size ==
ticker   tick_size   total_candles   non_conformes   ratio    statut
ESM5     0.25        145000           3               0.002%   OK
ESU5     0.25        142000           0               0.000%   OK
ESZ5     0.25        138000           15              0.011%   OK
TOTAL    —           425000           18              0.004%   OK
```

- `statut` = `OK` si ratio < 1%, `ATTENTION` si ratio ≥ 1% et < 5%, `ERREUR` si ratio ≥ 5%.
- Le bilan est aussi loggué (INFO pour OK, WARNING pour ATTENTION, ERROR pour ERREUR).
- Le code de sortie est 0 si OK, 0 si ATTENTION (warning seulement), 1 si ERREUR (pour scripting/CI).

**Seuils** (constantes dans `reader.py`) :

```python
DATA_QUALITY_WARNING_THRESHOLD = 0.01   # ≥1% -> statut ATTENTION (WARNING log)
DATA_QUALITY_ERROR_THRESHOLD   = 0.05   # ≥5% -> statut ERREUR (ERROR log, exit code 1)
```

**Implémentation** (`query/reader.py`) :

```python
def check_ticksize_accuracy(
    df: pl.DataFrame, chain: RolloverChain, trigger: float
) -> pl.DataFrame:
    """Analyse la conformité des prix au tick size et retourne un bilan par ticker.
    Affiche le bilan sur stdout et le log, retourne aussi un DataFrame récapitulatif.
    Ne modifie pas les données d'entrée."""
    price_cols = ["open", "high", "low", "close", "settlement_price"]
    rows = []
    for ticker in df["ticker"].unique():
        tick = chain.tick_size_for_ticker(ticker)
        subset = df.filter(pl.col("ticker") == ticker)
        total = subset.height
        nb_bad = 0
        for col in price_cols:
            # ABS((p / tick) - round(p / tick)) > trigger * tick
            bad_mask = (pl.col(col) / tick - (pl.col(col) / tick).round()).abs() > trigger * tick
            nb_bad += subset.filter(bad_mask).height
        ratio = nb_bad / total if total > 0 else 0.0
        rows.append({"ticker": ticker, "tick_size": tick, "total_candles": total,
                     "non_conformes": nb_bad, "ratio": ratio})
    bilan = pl.DataFrame(rows)
    # Afficher le bilan, logger, déterminer le statut global
    return bilan
```

⚠️ **Incompatibilité** : `--normalize-tick-size` et `--adjust` sont **mutuellement exclusifs**. Si les deux sont passés simultanément, le CLI lève une erreur explicite : `ValueError: normalize_tick_size et adjust_rollover sont incompatibles`. L'ajustement de rollover futur devra calculer en prix réels (Float64) ou en unités de tick (Int32), mais pas les deux simultanément — les calculs seraient incohérents.

### 8.4 Dumps bruts

```
data/raw/
├─ ES/
│  ├─ ESM5/
│  │  ├─ 20260704T180000.parquet
│  │  ├─ 20260704T180000.meta.json
│  │  ├─ 20260711T183000.parquet
│  │  └─ 20260711T183000.meta.json
│  ├─ ESU5/
│  │  └─ ...
│  ...
├─ NQ/
│  ...
```

Un fichier Parquet par contrat et par run, jamais écrasé. Permet l'audit et la re-agrégation. Chaque fichier a son sidecar `.meta.json` (voir §5.3).

### 8.5 Cache agrégé

```
data/aggregate/
├─ ES_continuous.parquet
├─ ES_continuous.meta.json
├─ NQ_continuous.parquet
├─ NQ_continuous.meta.json
├─ RTY_continuous.parquet
├─ RTY_continuous.meta.json
└─ YM_continuous.parquet
└─ YM_continuous.meta.json
```

### 8.6 Agrégation (`pipeline/aggregator.py`)

```python
def aggregate(product_code: str, settings: Settings, chain: RolloverChain) -> pl.DataFrame:
    # 1. Lire tous les dumps bruts du produit (tous run_ts confondus) via raw_dumps.read_all_runs()
    # 2. Concaténer en un seul DataFrame
    # 3. Caster run_id / ticker / product_code en Categorical (optimisation mémoire)
    # 4. Dédupliquer: unique(subset=["window_start", "ticker"], keep="last")
    #    -> en cas de doublon (même chandelier re-téléchargé), garde la version du run le plus récent
    # 5. Trier par window_start
    # 6. Écrire data/aggregate/{product_code}_continuous.parquet (+ sidecar .meta.json)
    # 7. Log nb lignes avant/après déduplication, nb dumps fusionnés
    #
    # Note: la normalisation tick_size n'est PAS faite ici — elle se fait à la lecture via query --normalize-tick-size
```

deduplication en Polars : `pl.col("*").unique(subset=["window_start", "ticker"], keep="last")` ou `df.unique(subset=..., keep="last")`. On garde `keep="last"` car les dumps sont lus par ordre chronologique des `run_ts`, donc le dernier contient les données les plus récentes.

---

## 9. Requêtes (`query/reader.py`)

La fonction `query` accepte 3 flags mutuellement liés :

- `adjust_rollover` (`--adjust`) : ajustement de rollover (stub `NotImplementedError`).
- `normalize_tick_size` (`--normalize-tick-size`) : conversion prix → multiples entiers de tick size (`Int32`).
- `check_ticksize_accuracy` (`--check-ticksize-accuracy`) : analyse la conformité des prix au tick size et **affiche un bilan** (cf §8.3), sans modifier les données.

**Incompatibilités** :
- `adjust_rollover` × `normalize_tick_size` : `ValueError` (les calculs d'ajustement futur seraient incohérents en Int32).
- `check_ticksize_accuracy` peut être combiné avec `normalize_tick_size` (le bilan s'affiche **avant** la conversion) ou utilisé seul (read-only, retourne les données en `Float64` + bilan).

```python
def query(
    product_code: str,
    start: datetime | None = None,
    end: datetime | None = None,
    adjust_rollover: bool = False,
    normalize_tick_size: bool = False,
    check_ticksize_accuracy: bool = False,
    settings: Settings,
    chain: RolloverChain,
) -> pl.DataFrame:
    # --- Incompatibilité mutuelle ---
    if adjust_rollover and normalize_tick_size:
        raise ValueError(
            "normalize_tick_size et adjust_rollover sont incompatibles"
        )

    df = read_parquet(f"data/aggregate/{product_code}_continuous.parquet")
    if start: df = df.filter(pl.col("window_start") >= start)
    if end:   df = df.filter(pl.col("window_start") <= end)

    # --- Ajustement de rollover (stub) ---
    if adjust_rollover:
        raise NotImplementedError(
            "adjust_rollover=True non implémenté — méthode d'ajustement à définir"
        )

    # --- Bilan qualité tick size (read-only, affiche un bilan) ---
    if check_ticksize_accuracy:
        bilan = check_ticksize_accuracy_fn(df, chain, settings.data_quality_trigger)
        # Affiche le bilan sur stdout (table rich) + log INFO/WARNING/ERROR selon seuils
        # cf §8.3 pour le format du bilan
        # Ne lève pas d'exception — affiche seulement. Le code de sortie CLI gère l'exit code.

    # --- Normalisation tick size (à la lecture) ---
    if normalize_tick_size:
        # Conversion: pour chaque ticker, diviser OHLC + settlement_price par trade_tick_size -> round -> Int32
        price_cols = ["open", "high", "low", "close", "settlement_price"]
        for ticker in df["ticker"].unique():
            tick = chain.tick_size_for_ticker(ticker)
            for col in price_cols:
                df = df.with_columns(
                    pl.when(pl.col("ticker") == ticker)
                    .then((pl.col(col) / tick).round().cast(pl.Int32))
                    .otherwise(pl.col(col))
                    .alias(col)
                )

    # adjust_rollover=False: retourne la chaîne continue avec gaps naturels
    return df
```

---

## 10. Cascade automatique (`pipeline/cascade.py`)

### 10.1 Chaine de dépendances

```
contracts (cache /contracts) --> fetch (OHLCV) --> aggregate (fusion+dedup) --> query (lecture)
```

### 10.2 Helpers `ensure_*`

```python
def ensure_contracts(product_code: str, client: MassiveClient, settings: Settings):
    """Vérifie le cache contrats. Si absent/périmé -> WARNING + auto-refresh."""
    cache = ContractsCache(product_code, settings)
    if not cache._is_fresh():
        log.warning(f"[cascade] Cache contrats {'absent' if not cache.exists else 'périmé'} "
                    f"pour {product_code} — rafraîchissement automatique…")
        cache.get(client, force_refresh=True)
    else:
        log.debug(f"[cascade] Cache contrats frais pour {product_code} — OK")

def ensure_raw_dumps(product_code: str, client: MassiveClient, settings: Settings):
    """Vérifie l'existence de dumps bruts. Si aucun -> WARNING + auto-fetch."""
    if not raw_dumps_exist(product_code):
        log.warning(f"[cascade] Aucun dump trouvé pour {product_code} — lancement fetch…")
        ensure_contracts(product_code, client, settings)  # cascade en amont
        run_fetch(product_code, client, settings)
    else:
        log.debug(f"[cascade] Dumps bruts présents pour {product_code} — OK")

def ensure_aggregate(product_code: str, client: MassiveClient, settings: Settings, chain: RolloverChain):
    """Vérifie l'existence du cache agrégé. Si absent -> WARNING + auto-aggregate."""
    if not aggregate_exists(product_code):
        log.warning(f"[cascade] Agrégé absent pour {product_code} — lancement aggregate…")
        ensure_raw_dumps(product_code, client, settings)  # cascade en amont
        aggregate(product_code, settings, chain)
    else:
        log.debug(f"[cascade] Agrégé présent pour {product_code} — OK")
```

### 10.3 Comportement par commande

| Commande | Prérequis vérifiés | Cascade si manquant |
|---|---|---|
| `contracts` | Aucun | — |
| `fetch` | Cache contrats frais | `ensure_contracts` (WARNING si déclenché) |
| `aggregate` | Dumps bruts existants | `ensure_raw_dumps` → `ensure_contracts` (WARNING si déclenché) |
| `query` | Cache agrégé existant | `ensure_aggregate` → `ensure_raw_dumps` → `ensure_contracts` (WARNING à chaque niveau) |

### 10.4 Flag `--no-cascade`

Toutes les commandes avec dépendances acceptent `--no-cascade` :
- Si prérequis manquant → **erreur explicite** (pas d'auto-cascade).
- Usage : cron/CI où on veut un échec clair plutôt qu'un backfill silencieux de 2 ans.

### 10.5 `massivibe status` avant cascade

En cas de déclenchement en cascade (ex: `query` sur repo vide), on logge d'abord un snapshot `status` (état de chaque étape pour chaque produit impliqué) avant de dérouler la cascade :

```
INFO    [status] == Avant cascade ==
INFO    [status] ES: contracts=absent, dumps=absent, aggregate=absent
INFO    [status] NQ: contracts=frais(2026-07-10), dumps=12 fichiers, aggregate=OK
WARNING  [cascade] Agrégé absent pour ES — lancement aggregate…
WARNING  [cascade] Aucun dump trouvé pour ES — lancement fetch…
WARNING  [cascade] Cache contrats absent pour ES — rafraîchissement automatique…
INFO    [contracts] Fetch /futures/v1/contracts?product_code=ES …
INFO    [contracts] 42 contrats mis en cache
INFO    [cascade] Prérequis OK — retour à 'fetch'
DEBUG   [fetch] GET /futures/v1/aggs/ESM5?resolution=1min…
```

---

## 11. Logging et Observabilité (`logging_setup.py`)

### 11.1 Configuration

- Handler console : `rich.logging.RichHandler` (couleurs, formatage lisible)
- Handler fichier : `{log_dir}/massivibe.log` avec rotation (10 MB, 5 fichiers) — `log_dir` configurable (défaut `./logs`)
- **Un seul levier** : `level = "DEBUG"` (défaut). DEBUG active tout (appels API, skips cache, extrait pagination).

### 11.2 Helpers de log

```python
def log_api_call(method: str, path: str, **params):
    if logger.isEnabledFor(logging.DEBUG):
        # Masque la clé API, log endpoint + params
        logger.debug(f"API {method} {path} params={params}")

def log_cache_skip(cache_name: str, product_code: str, last_fetched: str):
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"Cache skip: {cache_name} pour {product_code} (last_fetched={last_fetched})")

def log_pagination_excerpt(page_num: int, results: list[dict]):
    if logger.isEnabledFor(logging.DEBUG):
        excerpt = results[:5]
        # Convertir window_start (ns) -> datetime lisible UTC pour chaque ligne
        for r in excerpt:
            r["window_start"] = ns_to_iso(r["window_start"])
        logger.debug(f"[page {page_num}] 5 premières candles: {excerpt}")
```

Les 3 helpers se déclenchent uniquement si `level >= DEBUG` (via `isEnabledFor`). Pas de booléens individuels : tout est piloté par `level`.

### 11.3 Convention de préfixes de log

| Préfixe | Usage |
|---|---|
| `[cascade]` | WARNING de déclenchement automatique d'une dépendance |
| `[contracts]`, `[fetch]`, `[aggregate]`, `[query]` | Logs métier par étape |
| `[status]` | Snapshot avant cascade |
| `[page N]` | Extrait pagination |

---

## 12. CLI détaillé (`cli.py`)

### 12.1 Commandes

| Commande | Description | Flags |
|---|---|---|
| `massivibe setup-key` | Demande la clé API (prompt masqué), crée `.env` si absent. Refuse d'écraser une clé existante sans confirmation. | `--base-url` |
| `massivibe config` | Affiche la config résolue (clé masquée `****`). | `--paths` (affiche chemins fichiers) |
| `massivibe contracts` | Liste/rafraîchit le cache contrats par `product_code`. | `--product ES`, `--refresh`, `--active-only` |
| `massivibe fetch` | Historise les OHLCV 1min. Skip si déjà fait aujourd'hui (WARNING) sauf `--force`. Cache contrats auto-rafraîchi si périmé (WARNING cascade). | `--product ES`, `--force`, `--dry-run`, `--no-cascade` |
| `massivibe aggregate` | Régénère le cache agrégé depuis dumps bruts. Auto-déclenche `fetch` si dumps manquants (WARNING cascade). | `--product ES`, `--no-cascade` |
| `massivibe query <product>` | Interroge l'historique continu. Auto-déclenche `aggregate` → `fetch` → `contracts` si manquant (WARNING cascade). | `--start`, `--end`, `--adjust` (rollover ajusté — stub), `--normalize-tick-size` (prix → Int32, **incompatible avec `--adjust`**), `--check-ticksize-accuracy` (analyse la conformité au tick size et affiche un bilan), `--output`, `--limit`, `--no-cascade` |
| `massivibe status` | Snapshot par produit : dernier `run_ts`, plage historisée, nb candles, tailles raw/aggregate, fraîcheur cache contrats, **et la `RolloverChain` du produit** (tableau des segments). | `--product ES` |

### 12.2 Comportements notables

- `fetch --dry-run` : calcule et affiche pour chaque produit/contrat : plage à fetcher, nb pages estimées, cache hit/miss. N'appelle pas l'API ni n'écrit de fichiers. Idéal pour valider avant un backfill 2 ans.
- `fetch` sans `--force` : si un dump daté d'aujourd'hui existe pour le produit → `WARNING` + skip (passe au suivant).
- `query` sans `--start/--end` : retourne tout l'historique disponible.
- `--no-cascade` : erreur explicite si prérequis manquant (pour automatisation/cron).
- `status` affiche la `RolloverChain` (tableau `ticker / dates / rollover_date / tick_size`) pour chaque produit — voir §6.3.
- `query --normalize-tick-size` : convertit OHLC + settlement_price en multiples entiers de tick size (`Int32`).
- `query --check-ticksize-accuracy` : analyse la conformité des prix au tick size et affiche un bilan (tableau par ticker + total). Peut être combiné avec `--normalize-tick-size` (bilan affiché avant la conversion). Read-only sinon. Code de sortie : 0 si OK, 0 si ATTENTION (warning), 1 si ERREUR (≥5% non conforme).
- `query --normalize-tick-size` et `query --adjust` sont **mutuellement exclusifs** — erreur explicite si les deux sont passés.

### 12.3 Workflow de validation (AGENTS.md)

```bash
# 1. Configurer la clé API
massivibe setup-key

# 2. Vérifier la config
massivibe config

# 3. Tester un seul contrat entier (validation pré-backfill)
python scripts/test_single_contract.py ES

# 4. Dry-run pour valider les ranges
massivibe fetch --dry-run

# 5. Backfill complet 2 ans tous produits
massivibe fetch

# 6. Vérifier le status (incluant RolloverChain)
massivibe status

# 7. Interroger l'historique
massivibe query ES --start 2026-01-01 --end 2026-07-11 --output es_history.parquet

# 8. Interroger avec normalisation tick size (prix en multiples entiers de tick -> Int32)
massivibe query ES --start 2026-01-01 --end 2026-07-11 --normalize-tick-size --output es_int.parquet

# 9. Vérifier la qualité des données par rapport au tick size (bilan read-only)
massivibe query ES --check-ticksize-accuracy

# 10. Normaliser après avoir vérifié la qualité (bilan affiché avant conversion)
massivibe query ES --check-ticksize-accuracy --normalize-tick-size --output es_int.parquet
```

---

## 13. Tests (`tests/`)

### 13.1 Couverture

| Fichier | Tests |
|---|---|
| `test_config.py` | Chargement `.env` + `config.toml`, validations pydantic, valeurs par défaut, `contracts_page_limit=1000` |
| `test_client.py` | Bearer envoyé, retry 429 avec `Retry-After`, exponential backoff sans `Retry-After`, échec après 6 retries, pagination `next_url`, extrait loggé/page avec `window_start` |
| `test_contracts_cache.py` | Cache par `product_code`, skip si frais, refresh si `force`/TTL dépassé, sidecar `.meta.json` |
| `test_aggregates_fetch.py` | Pagination complète, conversion `window_start` ns → datetime, concat Polars |
| `test_parquet_io.py` | Write/read round-trip, schéma canonique respecté, sidecar `.meta.json` écrit systématiquement avec champs attendus |
| `test_raw_dumps.py` | Sauvegarde par `{product_code}/{ticker}/{run_ts}`, listage, lecture, sidecar |
| `test_aggregator.py` | Fusion 2 dumps chevauchants, dédup `(window_start, ticker)` keep=last, tri, cast Categorical |
| `test_rollover.py` | Expiration vendredi 19 → dernier jour conservé vendredi 12 ; lundi suivant = nouveau contrat ; `continuous_segments` correct ; `tick_size_for_ticker` ; `to_table()` |
| `test_historian.py` | Premier run range 2 ans ; incrémental = last + buffer (1 jour) ; extension `history_months` ; skip si déjà fait aujourd'hui ; `--force` |
| `test_reader.py` | `adjust_rollover=False` retourne chaîne ; `True` lève `NotImplementedError` ; filtres `start`/`end` ; `normalize_tick_size` conversion Int32 ; `check_ticksize_accuracy` bilan read-only (format tableau, 0 non conforme = OK/exit 0, <1% = ATTENTION/exit 0, ≥5% = ERREUR/exit 1) ; incompatibilité `normalize_tick_size` × `adjust_rollover` ; `check_ticksize_accuracy` + `normalize_tick_size` combinables |
| `test_cascade.py` | Cascade `query` → `aggregate` → `fetch` → `contracts` ; `--no-cascade` erreur ; logs WARNING ; status avant cascade |
| `test_cli.py` | Toutes commandes, flags, format output, `status` affiche `RolloverChain` ; `query --normalize-tick-size` ; `query --adjust` ; `query --check-ticksize-accuracy` (bilan + exit code) ; incompatibilité `--normalize-tick-size` × `--adjust` |

### 13.2 Fixtures

- `conftest.py` : tmp dirs, mock client via `respx` (intercepte httpx), fixtures JSON de `/contracts` et `/aggs` depuis les samples de la doc API (avec `trade_tick_size` pour tester la normalisation). Fixtures spécifiques pour `--check-ticksize-accuracy` : données "propres" (100% conformes au tick), données "bruitées" (<1% non conformes → bilan OK/ATTENTION), données "corrompues" (≥5% non conformes → bilan ERREUR, exit code 1).

---

## 14. Sécurité et Qualité

### 14.1 Sécurité

- `.gitignore` : `.env`, `data/`, `.venv/`, `__pycache__/`, `*.parquet`, `logs/`
- `.env.example` committé sans valeur réelle ; `setup-key` crée `.env`
- Clé API jamais loggée (masquée `****`)
- `SecretStr` pydantic pour stockage en mémoire

### 14.2 Qualité

- `ruff check` + `ruff format` (lint + format)
- `mypy --strict` sur `src/massivibe/`
- `pytest --cov=massivibe --cov-report=term-missing`
- Type hints partout, docstrings sur modules publics
- **Commentaires explicatifs** dans le code : les sections non triviales (rollover, normalisation tick_size, cascade, retry tenacity, sidecar) seront commentées pour faciliter la relecture et la maintenance. Le code doit être instructif pour un relecteur.

---

## 15. Endpoints API Massive utilisés

| Endpoint | Méthode | Usage | Paramètres |
|---|---|---|---|
| `/futures/v1/contracts` | GET | Cache des contrats par `product_code` | `product_code`, `active`, `date`, `limit` (max 1000), `sort` ; pagination `next_url` |
| `/futures/v1/aggs/{ticker}` | GET | OHLCV 1 minute | `resolution=1min`, `window_start.gte`, `window_start.lte`, `limit` (max 50000) ; pagination `next_url` |

**Supprimé** : `/futures/v1/products` (codes directs `NQ, ES, RTY, YM` — pas de résolution de nom nécessaire).

**Champs utilisés de `/contracts`** : `ticker`, `first_trade_date`, `last_trade_date`, `settlement_date`, `product_code`, `name`, `trade_tick_size` (pour la normalisation), `type` (filtre `single`).

**Champs utilisés de `/aggs`** : `window_start` (ns), `open`, `high`, `low`, `close`, `volume`, `dollar_volume`, `transactions`, `session_end_date`, `settlement_price`, `ticker`.

---

## 16. Plan d'implémentation (ordre de codage)

1. `.gitignore`, `.env.example`, `pyproject.toml`, `config.toml`
2. `src/massivibe/config.py` + `logging_setup.py`
3. `src/massivibe/api/client.py` (httpx, Bearer, throttle, retry tenacity, pagination)
4. `src/massivibe/api/contracts.py` + `api/aggregates.py`
5. `src/massivibe/storage/parquet_io.py` (avec sidecar `.meta.json` systématique) + `raw_dumps.py` + `aggregate_cache.py`
6. `src/massivibe/contracts/cache.py` (sidecar `.meta.json`)
7. `src/massivibe/contracts/rollover.py` (RolloverChain, RolloverSegment, to_table)
8. `src/massivibe/pipeline/aggregator.py` (dedup + cast Categorical)
9. `src/massivibe/pipeline/historian.py` (run_ts, skip today, ranges)
10. `src/massivibe/pipeline/cascade.py` (ensure_*, status avant cascade)
11. `src/massivibe/query/reader.py` (query + normalize_tick_size + check_ticksize_accuracy (bilan) + incompatibilité adjust × normalize)
12 `src/massivibe/cli.py` (toutes commandes + flags, status affiche RolloverChain)
13. `scripts/test_single_contract.py`
14. Tests complets (`tests/`)
15. `README.md` (usage)