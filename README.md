# MassiVibe

Historisation périodique des données OHLCV 1 minute des contrats futures via l'API REST de [Massive.com](https://massive.com).

## Fonctionnalités

- Récupération et historisation **chaque semaine** des chandeliers OHLCV 1 minute.
- Stockage en **fichiers Parquet** via **Polars** (types `Categorical` optimisés).
- Mise en cache intelligente des contrats (`/futures/v1/contracts`), un cache par `product_code` avec TTL configurable (30 jours par défaut).
- Gestion automatique du **rollover** des contrats (switch J-7 avant expiration).
- **Cascade automatique** des dépendances : `query` déclenche `aggregate` → `fetch` → `contracts` si nécessaire.
- Normalisation des prix en **multiples entiers de tick size** (`Int32`) via `--normalize-tick-size`.
- Test de qualité des données via `--check-ticksize-accuracy` (bilan par ticker).
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
Les commandes cherchent `config.toml` et `.env` dans le **répertoire courant** —
exécutez-les depuis le dossier qui contient ces fichiers (typiquement le dépôt).

Vérifiez que l'installation est fonctionnelle :

```bash
massivibe --help
```

## Configuration rapide

### 1. Configurer la clé API

```bash
massivibe setup-key
# Entrez votre clé API Massive.com (masquée)
```

Cela crée un fichier `.env` (jamais committé) avec votre clé.

### 2. Vérifier la configuration

```bash
massivibe config
```

La configuration métier se trouve dans `config.toml` (committé). Voir `docs/TECHNICAL_DESIGN.md` pour le détail de chaque paramètre.

## Usage

### Workflow complet

```bash
# 1. Configurer la clé API
massivibe setup-key

# 2. Vérifier la config
massivibe config

# 3. Tester un seul contrat (validation pré-backfill)
python scripts/test_single_contract.py ES

# 4. Dry-run pour valider les ranges
massivibe fetch --dry-run

# 5. Backfill complet (2 ans, tous produits)
massivibe fetch

# 6. Vérifier le status (incluant la RolloverChain)
massivibe status

# 7. Interroger l'historique
massivibe query ES --start 2026-01-01 --end 2026-07-11 --output es_history.parquet

# 8. Vérifier la qualité des données (tick size)
massivibe query ES --check-ticksize-accuracy

# 9. Normaliser les prix en Int32 (multiples de tick)
massivibe query ES --normalize-tick-size --output es_int.parquet

# 10. Rééchantillonner en candles k-min (ex: 7min) avec filtrage intraday
massivibe query NQ --timescale-unit min --timescale-nb 7 --intraday-begin 09:30 --intraday-end 16:00

# 11. Filtrage intraday wrap-around (session overnight, ex: 20:00-04:00)
massivibe query NQ --timescale-unit min --timescale-nb 15 --intraday-begin 20:00 --intraday-end 04:00
```

### Commandes CLI

| Commande | Description |
|---|---|
| `massivibe setup-key` | Configure la clé API dans `.env` |
| `massivibe config` | Affiche la configuration résolue (clé masquée) |
| `massivibe contracts [--product ES] [--refresh]` | Liste/rafraîchit le cache contrats |
| `massivibe fetch [--product ES] [--force] [--dry-run] [--no-cascade]` | Historise les chandeliers OHLCV 1min |
| `massivibe aggregate [--product ES] [--no-cascade]` | Régénère le cache agrégé |
| `massivibe query <product> [--start] [--end] [--timescale-unit min\|hour] [--timescale-nb K] [--intraday-begin HH:MM] [--intraday-end HH:MM] [--adjust] [--normalize-tick-size] [--check-ticksize-accuracy] [--output] [--limit] [--no-cascade]` | Interroge l'historique continu (resampling + filtrage intraday à la volée) |
| `massivibe chart [product] [--port] [--host] [--mdns] [--timescale-unit] [--timescale-nb] [--nb-candle] [--intraday-begin] [--intraday-end] [--normalize-tick-size] [--adjust] [--no-cascade]` | Lance le serveur de visualisation interactive (candlestick, zoom/pan, lazy loading) |
| `massivibe status [--product ES]` | Affiche l'état de chaque produit (incluant la RolloverChain) |

### Cascade automatique

Les commandes `fetch`, `aggregate` et `query` vérifient automatiquement leurs prérequis et les déclenchent en cascade si manquants (avec WARNING) :

```
contracts → fetch → aggregate → query
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

> **Note sur les types** : les colonnes `volume` et `transactions` sont stockées en `Int32` dans le Parquet agrégé (et non `Int64` comme retourné par l'API). Ce cast est fait une fois au moment de l'agrégation (`massivibe aggregate`) et persisté dans le Parquet. Si vous avez un cache agrégé antérieur à cette version, relancez `massivibe aggregate --product <code>` pour bénéficier du cast.

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
- **Multi-product** : `localhost:8050/NQ`, `localhost:8050/ES`, etc. Un seul serveur sert tous les products configurés.
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
├─ config.toml                  # Configuration métier
├─ .env                         # Secrets (non committé)
├─ docs/TECHNICAL_DESIGN.md     # Documentation technique complète
├─ scripts/test_single_contract.py
├─ src/massivibe/
│  ├─ cli.py                    # CLI (argparse)
│  ├─ config.py                 # pydantic-settings + tomllib
│  ├─ logging_setup.py          # rich + rotation fichier
│  ├─ api/                      # Client HTTP (httpx, tenacity, pagination)
│  ├─ contracts/                # Cache contrats + RolloverChain
│  ├─ storage/                  # Parquet + sidecar .meta.json
│  ├─ pipeline/                 # Historian, aggregator, cascade
│  ├─ query/                    # Reader (query, normalize, check_ticksize), resampler (k-min, intraday)
│  ├─ chart/                    # Serveur de visualisation (FastAPI + Lightweight Charts)
│  │  ├─ server.py              # Endpoints API (candles Arrow IPC, meta, HTML)
│  │  ├─ mdns.py                # Découverte réseau local (zeroconf)
│  │  ├─ NOTICE                 # Attribution TradingView (license Apache-2.0)
│  │  └─ static/                # JS embarqués (lightweight-charts, apache-arrow) + template HTML
└─ tests/                       # 143 tests pytest + respx
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

Voir `docs/TECHNICAL_DESIGN.md` pour la documentation technique complète (architecture, configuration, API, rollover, cascade, etc.).

## Confidentialité et sécurité

> **Rappel MassiVe Terms of Service** : le code source de ce projet est libre (MIT), mais les Market Data récupérées via l'API Massive.com sont soumises aux [Market Data Terms](https://massive.com/legal/market-data-terms-of-service) et ne peuvent être redistribuées. Ce dépôt ne sert qu'à partager l'outil de collecte, pas les données elles-mêmes.

## Licence

Le code de MassiVibe est sous licence **MIT** (voir [LICENSE](./LICENSE)).

La librairie [TradingView Lightweight Charts](https://www.tradingview.com/lightweight-charts/) utilisée par la commande `massivibe chart` est sous licence **Apache 2.0** (voir [src/massivibe/chart/NOTICE](./src/massivibe/chart/NOTICE) et [LICENSE-2.0.txt](./src/massivibe/chart/LICENSE-2.0.txt)).
