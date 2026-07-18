# MassiVibe

Historisation périodique des données OHLCV multi-instruments via l'API REST de [Massive.com](https://massive.com).

MassiVibe supporte les **5 types d'instruments** de Massive : **futures**, **stocks**, **forex**, **indices** et **options**. À ce jour, **futures**, **stocks**, **forex** et **indices** sont pleinement implémentés ; **options** est scaffoldé (`NotImplementedError`).

## Fonctionnalités

- **Multi-type** : futures (rollover + contrats), stocks (splits/dividends), forex, indices, options — dispatch automatique par type d'instrument.
- Récupération et historisation **chaque semaine** des chandeliers OHLCV 1 minute.
- Stockage en **fichiers Parquet** via **Polars** (types `Categorical` optimisés), layout par type : `data/{raw,aggregate}/{type}/{symbol}/`.
- **Ajustement split** pour stocks : stockage en prix **bruts** (`adjusted=false`) + ajustement à la query (toggle `--no-split`, splits ON par défaut via le cache `/stocks/v1/splits`).
- Mise en cache intelligente : contrats futures (`/futures/v1/contracts`) et corporate actions stocks (`/stocks/v1/splits`), TTL commun configurable.
- Gestion automatique du **rollover** des contrats futures (switch J-7 avant expiration) via la `RolloverChain`.
- **Cascade automatique** des dépendances (type-aware) : `query` déclenche `aggregate` → `fetch` → `contracts`/`splits` si nécessaire.
- Normalisation des prix en **multiples entiers de tick size** (`Int32`) via `--normalize-tick-size` (futures).
- Test de qualité des données via `--check-ticksize-accuracy` (bilan par ticker, futures).
- Sidecar `.meta.json` systématique sur tous les fichiers Parquet (métadonnées, traçabilité).
- Retry automatique (Tenacity) sur 429/5xx avec `Retry-After` et exponential backoff.
- Logging DEBUG détaillé (appels API, skips cache, extraits pagination).

## Prérequis

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) (recommandé) ou pip
- [pipx](https://pipx.pypa.io/) pour une installation globale du binaire (optionnel)

## Installation

### Développement (contribuer / tester)

```bash
# Cloner le dépôt
git clone https://github.com/TheoBainee/MassiVibe.git
cd MassiVibe

# 1. Créer l'environnement virtuel
uv venv .venv

# 2. L'activer
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate          # Windows (PowerShell)

# 3. Installer le projet en mode editable (avec les dépendances de dev)
uv pip install -e ".[dev]"
```

> **Sans uv** : remplacez l'étape 1 par `python -m venv .venv` et l'étape 3 par `pip install -e ".[dev]"`.

### Usage quotidien (binaire global, sans activation de venv)

Pour utiliser `massivibe` sans avoir à activer un venv à chaque fois, installez-le
globalement avec [pipx](https://pipx.pypa.io/) depuis le dossier du dépôt :

```bash
pipx install --editable .
```

Le binaire `massivibe` est alors disponible partout (dans `~/.local/bin`).

Vérifiez que l'installation est fonctionnelle :

```bash
massivibe --help
```

## Configuration rapide

La config suit le [XDG Base Directory](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html) :

| Fichier | Emplacement principal | Fallback dev |
|---|---|---|
| Secrets | `~/.config/massivibe/.env` | `./.env` |
| Config métier | `~/.config/massivibe/config.toml` | `./config.toml` |
| Données / cache / logs | `~/.local/share/massivibe/{data,cache,logs}` | configurable |

### 1. Installer la config métier

```bash
mkdir -p ~/.config/massivibe
cp config.toml.example ~/.config/massivibe/config.toml
# Éditer instruments, chemins storage, etc. selon vos besoins
```

### 2. Configurer la clé API

```bash
massivibe setup-key
# Entrez votre clé API Massive.com (masquée)
```

Cela crée `~/.config/massivibe/.env` (jamais committé).

### 3. Vérifier la configuration

```bash
massivibe config
massivibe config --paths   # chemins résolus
```

Voir `docs/TECHNICAL_DESIGN.md` et `docs/MULTI_TYPE.md` pour le détail de chaque paramètre.

## Usage

### Workflow complet

```bash
# 1. Configurer la clé API
massivibe setup-key

# 2. Vérifier la config
massivibe config

# 3. Dry-run pour valider les ranges (tous les instruments configurés)
massivibe fetch --dry-run

# 4. Backfill complet (tous les instruments configurés)
massivibe fetch

# 5. Vérifier le status (adaptatif au type : RolloverChain pour futures, cache splits pour stocks)
massivibe status

# 6. Interroger l'historique (futures)
massivibe query ES --start 2026-01-01 --end 2026-07-11 --output es_history.parquet

# 7. Interroger un stock (ajustement split appliqué par défaut)
massivibe query AAPL --start 2024-01-01 --output aapl.parquet
# Prix bruts (non ajustés splits)
massivibe query AAPL --no-split --output aapl_raw.parquet

# 8. Vérifier la qualité des données futures (tick size)
massivibe query ES --check-ticksize-accuracy

# 9. Normaliser les prix futures en Int32 (multiples de tick)
massivibe query ES --normalize-tick-size --output es_int.parquet

# 10. Rééchantillonner en candles k-min (ex: 7min) avec filtrage intraday
massivibe query NQ --timescale-unit min --timescale-nb 7 --intraday-begin 09:30 --intraday-end 16:00

# 11. Lister/rafraîchir le cache contrats futures
massivibe futures contracts --symbol ES --refresh

# 12. Référentiel tickers + recherche + ajout conf
massivibe tickers refresh                              # → tickers/stocks/active.parquet
massivibe tickers refresh --markets stocks fx --active all
massivibe search apple --markets stocks --limit 50
massivibe search --ticker MSFT --add                   # 1 match → config.toml
massivibe config add TSLA NVDA                         # lookup type via cache
```

### Commandes CLI

| Commande | Description |
|---|---|
| `massivibe setup-key` | Configure la clé API dans `~/.config/massivibe/.env` |
| `massivibe config` | Affiche la configuration résolue (clé masquée) + chemin du fichier |

| `massivibe status [--instrument ES] [--type futures]` | Affiche l'état de chaque instrument (adaptatif au type) |
| `massivibe fetch [--instrument ES] [--type futures] [--force] [--dry-run] [--no-cascade]` | Historise les chandeliers OHLCV (multi-type, cascade auto) |
| `massivibe aggregate [--instrument ES] [--type futures] [--no-cascade]` | Régénère le cache agrégé (générique) |
| `massivibe query <instrument> [--type] [--start] [--end] [--timescale-unit min\|hour] [--timescale-nb K] [--intraday-begin HH:MM] [--intraday-end HH:MM] [--adjust] [--no-split] [--normalize-tick-size] [--check-ticksize-accuracy] [--output] [--limit] [--no-cascade]` | Interroge l'historique continu |
| `massivibe chart [instrument] [--type] [--port] [--host] [--mdns] [--timescale-unit] [--timescale-nb] [--nb-candle] [--intraday-begin] [--intraday-end] [--normalize-tick-size] [--no-split] [--adjust] [--no-cascade]` | Serveur de visualisation interactive |
| `massivibe futures contracts [--symbol ES] [--refresh] [--active-only]` | Liste/rafraîchit le cache contrats futures |
| `massivibe options contracts` | Scaffold options (`NotImplementedError`) |
| `massivibe tickers refresh [--markets stocks fx] [--active true\|false\|all] [--force]` | Fetch/cache shards `tickers/{market}/{active\|inactive}.parquet` + types |
| `massivibe tickers types [--force]` | Liste/rafraîchit le cache des ticker types |
| `massivibe search [QUERY] [--markets] [--limit N] [--add] [--yes]` | Recherche locale ; `--limit` override `display_max_rows` ; `--add` → conf |
| `massivibe config add TICKER… [--type stocks]` | Ajoute des tickers à la conf (lookup type via cache) |

> **Référencement des instruments** : par **symbole nu** (`ES`, `AAPL`, `EURUSD`, `NDX`) — le type est résolu depuis la config. En cas d'ambiguïté (symbole présent dans plusieurs types), utiliser `--type`. On peut aussi passer la clé complète `type:symbol` (ex: `futures:ES`, `stocks:AAPL`).

### Cascade automatique (type-aware)

Les commandes `fetch`, `aggregate` et `query` vérifient leurs prérequis et les déclenchent en cascade si manquants. La chaîne dépend du type :

```
futures : contracts (/futures/v1/contracts) → fetch → aggregate → query
stocks  : splits (/stocks/v1/splits)        → fetch → aggregate → query
forex/indices :                              fetch → aggregate → query
options : NotImplemented
```

Utiliser `--no-cascade` pour désactiver l'auto-cascade (erreur explicite si prérequis manquant — utile pour cron/CI).

### Resampling et filtrage intraday (`query --timescale-unit` / `--timescale-nb` / `--intraday-begin` / `--intraday-end`)

La commande `query` supporte le **rééchantillonnage à la volée** des candles 1min en candles k-min, ainsi que le **filtrage par heure du jour** (intraday). Ces transformations sont faites à la lecture (aucun stockage) — l'agrégé reste en 1min.

**`--timescale-unit min|hour` + `--timescale-nb K`** : rééchantillonne les candles 1min en buckets de K unités (ex: `--timescale-unit min --timescale-nb 7` pour 7min, `--timescale-unit hour --timescale-nb 2` pour 2h). La grille est **ancrée au début de chaque session** pour garantir la cohérence entre jours : le bucket N démarre à `anchor + N * K`, identique pour chaque session. Les buckets partiels de fin de session sont supprimés. Une colonne `candle_count` indique le nombre de candles 1min agrégés dans chaque bucket (utile pour détecter les gaps intra-session).

**`--intraday-begin HH:MM` / `--intraday-end HH:MM`** : filtre les candles par heure du jour. Deux modes :
- **Normal** (`begin < end`, ex: `09:30`-`16:00`) : garde les candles dans `[begin, end]`.
- **Wrap-around** (`begin > end`, ex: `20:00`-`04:00`) : garde les candles `>= begin` OU `<= end` (utile pour les sessions overnight qui spannent minuit).

Les deux doivent être fournis ensemble et doivent être différents.

```bash
# Candles 7min, session RTH uniquement (09:30-16:00)
massivibe query NQ --timescale-unit min --timescale-nb 7 --intraday-begin 09:30 --intraday-end 16:00

# Candles 15min, session overnight (wrap-around 20:00-04:00)
massivibe query NQ --timescale-unit min --timescale-nb 15 --intraday-begin 20:00 --intraday-end 04:00

# Filtrage intraday sans resampling (candles 1min filtrés)
massivibe query NQ --intraday-begin 09:30 --intraday-end 16:00
```

> **Note sur les types** : les colonnes `volume` et `transactions` sont stockées en `Int32` dans le Parquet agrégé (et non `Int64` comme retourné par l'API). Ce cast est fait une fois au moment de l'agrégation (`massivibe aggregate`) et persisté dans le Parquet. Si vous avez un cache agrégé antérieur à cette version, relancez `massivibe aggregate --instrument <symbol>` pour bénéficier du cast.

### Visualisation interactive (`massivibe chart`)

La commande `massivibe chart` lance un serveur web FastAPI qui sert un graphique candlestick interactif basé sur [TradingView Lightweight Charts™](https://tradingview.github.io/lightweight-charts/) (HTML5 Canvas). Le graphique supporte le zoom/pan fluide sur des centaines de milliers de chandeliers.

```bash
# Lancer le serveur (ouvre http://127.0.0.1:8050/NQ par défaut)
massivibe chart NQ

# Avec timescale 7min et filtrage intraday
massivibe chart NQ --timescale-unit min --timescale-nb 7 --intraday-begin 09:30 --intraday-end 16:00

# Accessible sur le réseau local (mDNS)
massivibe chart --mdns --host 0.0.0.0
```

**Fonctionnalités** :
- **Candlestick + volume** : pane principal (candles) + pane secondaire (volume histogram).
- **Zoom/pan** : roulette de la souris = zoom axe temps, drag = pan horizontal. Cap de zoom configurable (`max_visible_candles` dans la config).
- **Buffer progressif** : chargement initial de `buffer_multiplier × max_visible_candles` candles, puis fetch progressif au fur et à mesure du pan vers la gauche (lazy loading horizontal via `before` param). Le fetch se déclenche uniquement quand moins de 250 candles restent avant le bord gauche de la vue ; un flag `noMoreData` coupe les requêtes quand l'historique est épuisé (évite les boucles sur buckets partiels).
- **Sélecteur d'UT** : dropdown dans la toolbar (1min, 7min, 15min, 30min, 60min, 1h, 2h, 4h).
- **Multi-instrument** : `localhost:8050/futures:ES`, `localhost:8050/stocks:AAPL`, etc. Un seul serveur sert tous les instruments configurés (indexés par clé `type:symbol`).
- **Format de transfert** : Arrow IPC (binaire, ~3x plus compact que JSON).
- **mDNS** : `--mdns` pour la découverte réseau local (accessible depuis tablette/autre poste).

**License TradingView** : Lightweight Charts est sous Apache-2.0 avec attribution requise. Le logo TradingView est affiché sur le chart (`attributionLogo: true`), ce qui satisfait l'obligation de licence.

**Améliorations futures** (documentées, non implémentées) :
- Récupérer les chandeliers journaliers de l'API Massive (cascade complète daily)
- Récupérer les chandeliers 1 seconde (plan payant)
- Page d'accueil à `/` (présentation type `status`) — actuellement redirect simple
- Import d'éléments externes : backtest / indicateurs / objets custom
- Backend alternatif FinPlot (desktop only)
- Streaming temps réel (websockets, plans payants)

## Structure du projet

```
MassiVibe/
├─ config.toml.example          # Modèle de config (à copier vers ~/.config/massivibe/)
├─ .env.example                 # Modèle secrets
├─ docs/TECHNICAL_DESIGN.md     # Documentation technique
├─ docs/MULTI_TYPE.md           # Architecture multi-type (5 types d'instruments)
├─ src/massivibe/
│  ├─ cli.py                    # CLI (argparse, multi-type + groupes futures/options)
│  ├─ config.py                 # pydantic-settings + tomllib (XDG + fallback repo)
│  ├─ instruments.py            # InstrumentType (StrEnum) + Instrument (type, symbol)
│  ├─ chains.py                 # InstrumentChain (Protocol) + SingleSymbolChain + OptionsChain
│  ├─ logging_setup.py          # rich + rotation fichier
│  ├─ api/                      # Client HTTP (httpx, tenacity, pagination)
│  │  ├─ aggs_futures.py        # /futures/v1/aggs/{ticker} (ns, champs longs)
│  │  ├─ aggs_v2.py             # /v2/aggs/ticker/{t}/range/... (ms, champs courts → canonique)
│  │  ├─ contracts.py           # /futures/v1/contracts (futures-only)
│  │  └─ corporate_actions.py   # /stocks/v1/splits (+ dividends scaffold)
│  ├─ contracts/                # Cache contrats futures + RolloverChain
│  ├─ corporate_actions/        # Cache splits/dividends stocks
│  ├─ storage/                  # Parquet + sidecar .meta.json (paths par type)
│  ├─ pipeline/                 # historian, aggregator, cascade (type-aware)
│  │  └─ fetchers/              # FuturesFetcher, StocksFetcher, OptionsFetcher (scaffold)
│  ├─ query/                    # reader, resampler, adjust (split)
│  ├─ chart/                    # FastAPI + Lightweight Charts
│  └─ py.typed
└─ tests/                       # pytest + respx
```

Config utilisateur (hors dépôt) :

```
~/.config/massivibe/config.toml
~/.config/massivibe/.env
~/.local/share/massivibe/{data,cache,logs}/
```

## Tests

```bash
python -m pytest tests/ -v
```

## Autocompletion (optionnel)

L'autocompletion des sous-commandes et options est supportée via
[argcomplete](https://kislyuk.github.io/argcomplete/) (inclus dans les
dépendances de dev). Une fois l'environnement activé :

```bash
# Bash — ajouter dans ~/.bashrc :
eval "$(register-python-argcomplete massivibe)"

# ZSH — ajouter dans ~/.zshrc :
autoload bashcompinit
bashcompinit
eval "$(register-python-argcomplete massivibe)"

# Fish shell :
register-python-argcomplete --shell fish massivibe | source
```

Après quoi `massivibe fe<Tab>` complète automatiquement en `massivibe fetch`.

## Documentation

- `docs/TECHNICAL_DESIGN.md` — documentation technique complète (architecture, configuration, API, rollover, cascade, etc.).
- `docs/MULTI_TYPE.md` — architecture multi-type (5 types d'instruments, endpoints par type, sémantique `--adjust`/`--no-split`, layout de stockage, statut d'implémentation).

## Confidentialité et sécurité

> **Rappel MassiVe Terms of Service** : le code source de ce projet est libre (MIT), mais les Market Data récupérées via l'API Massive.com sont soumises aux [Market Data Terms](https://massive.com/legal/market-data-terms-of-service) et ne peuvent être redistribuées. Ce dépôt ne sert qu'à partager l'outil de collecte, pas les données elles-mêmes.

## Licence

Le code de MassiVibe est sous licence **MIT** (voir [LICENSE](./LICENSE)).

La librairie [TradingView Lightweight Charts](https://www.tradingview.com/lightweight-charts/) utilisée par la commande `massivibe chart` est sous licence **Apache 2.0** (voir [src/massivibe/chart/NOTICE](./src/massivibe/chart/NOTICE) et [LICENSE-2.0.txt](./src/massivibe/chart/LICENSE-2.0.txt)).
