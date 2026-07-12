"""Point d'entrée pour ``python -m massivibe`` et le script console ``massivibe``.
# PYTHON: ARGCOMPLETE_OK

Le marqueur ``# PYTHON: ARGCOMPLETE_OK`` ci-dessus active l'autocompletion shell
via ``argcomplete`` quand ce module est invoqué par le script console généré par
pip/uv (le script importe :func:`main` depuis ce module).
"""

from massivibe.cli import main

if __name__ == "__main__":
    main()
