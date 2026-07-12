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
```

### Commandes CLI

| Commande | Description |
|---|---|
| `massivibe setup-key` | Configure la clé API dans `.env` |
| `massivibe config` | Affiche la configuration résolue (clé masquée) |
| `massivibe contracts [--product ES] [--refresh]` | Liste/rafraîchit le cache contrats |
| `massivibe fetch [--product ES] [--force] [--dry-run] [--no-cascade]` | Historise les chandeliers OHLCV 1min |
| `massivibe aggregate [--product ES] [--no-cascade]` | Régénère le cache agrégé |
| `massivibe query <product> [--start] [--end] [--adjust] [--normalize-tick-size] [--check-ticksize-accuracy] [--output] [--limit] [--no-cascade]` | Interroge l'historique continu |
| `massivibe status [--product ES]` | Affiche l'état de chaque produit (incluant la RolloverChain) |

### Cascade automatique

Les commandes `fetch`, `aggregate` et `query` vérifient automatiquement leurs prérequis et les déclenchent en cascade si manquants (avec WARNING) :

```
contracts → fetch → aggregate → query
```

Utiliser `--no-cascade` pour désactiver l'auto-cascade (erreur explicite si prérequis manquant — utile pour cron/CI).

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
│  └─ query/                    # Reader (query, normalize, check_ticksize)
└─ tests/                       # 111 tests pytest + respx
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

## Licence

MIT
