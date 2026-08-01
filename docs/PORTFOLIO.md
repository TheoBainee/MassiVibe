# Analyse de portefeuille (MPT)

Commande CLI : `myquantstore portfolio …`

## Principes

- **Univers v1** : stocks configurés (track **1day** Yahoo).
- **Returns total-return** : prix split-adjusted (défaut query) + dividend adjust (`adjust_rollover`).
- **Fréquence** : `day` (défaut) ou `week` (resample).
- **Stack** : Polars (panel) + numpy (corr/cov/optim). Pas de pandas / PyPortfolioOpt en v1.
- **Optim** long-only \(\sum w=1\), \(w_i\ge 0\) via candidats analytiques projetés + tirages Dirichlet.

## Sous-commandes

| Cmd | Description |
|---|---|
| `stats` | μ_ann, σ_ann, Sharpe par titre |
| `corr` | Matrice de corrélation |
| `cov` | Covariance annualisée |
| `optimize --objective equal\|min-vol\|max-sharpe` | Poids optimaux |
| `allocate --objective … [--value V]` | Lots entiers + cash + poids effectifs (`default_value` config) |
| `frontier` | Frontière efficiente approximée |

### Chart paniers (lazy)

Dashboard section Stocks : boutons **Max Sharpe** / **Min Vol** →
`/portfolio:max-sharpe` et `/portfolio:min-vol`.

- Optim calculée **au premier accès** (pas au boot, pas de cache TTL).
- Série OHLCV : combinaison linéaire des legs sur la **barre de base**
  (1min ou 1day), puis resample UT ; **rebase 100** à t0.
- Invariant : jamais de combo sur barres déjà resamplées.

Flags communs : `--from`, `--to`, `--timescale day|week`, `--rf`, `--log-returns`, `--no-div`, `-i` (répétable), `--export path.parquet|.csv`.

Affichage stdout tronqué via `[display]` (`max_rows` / `max_columns`) — comme `query`/`status`. Export = matrice complète.

## Config `[portfolio]`

```toml
risk_free_rate = 0.04
trading_days_per_year = 252
min_coverage = 0.95
frontier_samples = 5000
default_lookback_years = 5
optim_seed = 42
```

## Formules

- Returns simple : \(r_t = P_t/P_{t-1}-1\)
- Annualisation daily : \(\mu_{ann}=\bar r\cdot 252\), \(\sigma_{ann}=s\sqrt{252}\)
- Sharpe : \((\mu_p - r_f)/\sigma_p\)
- Min-vol : \(\min w^\top\Sigma w\)
- Max-Sharpe : \(\max (w^\top\mu - r_f)/\sqrt{w^\top\Sigma w}\)

## Limites v1

- Sample covariance (pas Ledoit-Wolf)
- Long-only, pas de shorts / market-neutral
- Frontier = approximation par échantillonnage (pas QP exact)
- Stocks only
- Biais de sélection de l’univers config (survivorship)

## Exemples

```bash
myquantstore portfolio stats
myquantstore portfolio optimize --objective min-vol
myquantstore portfolio optimize --objective max-sharpe -i AAPL -i NVDA -i COST
myquantstore portfolio allocate --objective min-vol --value 20000
myquantstore portfolio corr --from 2020-01-01 --export /tmp/corr.parquet
myquantstore portfolio frontier --timescale week --points 30
myquantstore chart   # boutons Max Sharpe / Min Vol dans Stocks
```
