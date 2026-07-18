# INITIAL_SPEC (archivée)

Ceci est la spécification initiale fournie au début du projet (focus futures-only).
Archivée le 2026-07-18.

Le projet a évolué en un outil multi-type complet (futures, stocks, forex, indices pleinement supportés).
Voir le AGENTS.md courant + docs/MULTI_TYPE.md + README.md pour l'état actuel.

La seule contrainte forte sur les dumps est de pouvoir reconstruire les agrégats depuis les dumps pseudo-bruts existants.
Aucune garantie de rétrocompatibilité des formats en phase alpha.

--- Contenu original ci-dessous ---

Tu es un expert Python senior. Développe un projet complet et professionnel pour historiser périodiquement les données de contrats futures via l'API de Massive.com.

### Objectifs principaux
- Récupérer et historiser **chaque semaine** les données OHLCV 1 minute des contrats futures.
- Utiliser **Polars** en priorité (Pandas uniquement si vraiment nécessaire).
- Tout le stockage des données historiques se fait en **fichiers Parquet**.
- Mise en cache intelligente des contrats (`/futures/v1/contracts`) : appeler l'endpoint uniquement quand nécessaire.

### Configuration
- Utiliser un système de configuration clair et standard (idéalement `pydantic-settings` ou `toml` + `dynaconf`/`configparser` selon ce qui est le plus adapté et maintenable).
- L'emplacement des fichiers Parquet doit être configurable.
- L’**API Key** de Massive.com doit être demandée à l'utilisateur et stockée dans un fichier `.env` ou équivalent (jamais commité).
- Dans le fichier de configuration, pouvoir définir :
  - Instruments à suivre (ex: Mini Nasdaq, Mini S&P 500, Mini Russell, Mini Dow Jones)
  - Timeframe (1m pour commencer)
  - Taille du buffer de recouvrement (en jours)
  - Niveau de logging (par défaut `DEBUG` pour ce projet)

### Logique d'historisation
1. **Premier run** : Récupérer **tout l'historique disponible** (2 ans gratuits).
2. **Runs suivants** : Récupérer uniquement les données depuis la dernière date historisée + buffer de recouvrement configurable.
3. À chaque exécution :
   - Sauvegarder un **dump brut** de la réponse API (un fichier par contrat et par run).
   - Mettre à jour l'historique agrégé.

### Gestion des contrats et rollovers
- Un dossier/fichier Parquet **par contrat** pour les dumps bruts.
- Un fichier Parquet **agrégé** unique contenant l'historique continu.
- **Logique de rollover** :
  - Passer au contrat suivant **1 semaine avant l'expiration** (ex: contrat expirant le vendredi 19 → dernier jour conservé = vendredi 12).
  - Les chandeliers à partir du lundi suivant appartiennent au nouveau contrat.
- Pour les queries sur l'historique agrégé, proposer un paramètre `adjust_rollover` (bool) :
  - `False` (défaut) : conserver les gaps naturels entre contrats.
  - `True` : ajuster pour supprimer les gaps (à définir précisément).

### Agrégation
- L’agrégation doit fusionner tous les dumps, **supprimer les doublons** de chandeliers (basé sur timestamp + contrat) - utilisation de polars.
- Le cache agrégé doit être régénéré après chaque mise à jour.

### Logging & Observabilité
- Mode **DEBUG** par défaut :
  - Logger tous les appels API (endpoint + paramètres).
  - Logger les skips grâce au cache.
  - Lors de la pagination, logger un extrait de la réponse JSON pour éviter les boucles infinies.

### Tests & Qualité
- Écrire des **tests avec pytest**.
- Ajouter des commentaires clairs et explicatifs dans le code.
- Structure de projet propre et maintenable.
- Avant de lancer l'historisation des 2 années d'historique il faudra tester la recuperation fonctionnelle de l'historique d'un contrat entier pour optimiser le workflow.

### Contraintes techniques
- Tu peux installer `uv` si besoin pour la gestion des dépendances.
- Préférer les solutions modernes et propres (type hints, logging structuré, etc.).
- Le code doit être prêt à être relu et maintenu.

### Documentation :
- tu peux trouver la documentation dédiée LLM ici : https://massive.com/docs/llms.txt elle devrait t'aider a comprendre l'API.
- Rédige une documentation détaillé de ce que tu fais.

Commence par proposer l'arborescence du projet, puis le `pyproject.toml` (ou équivalent avec uv), puis le fichier de configuration, et enfin le code principal.
