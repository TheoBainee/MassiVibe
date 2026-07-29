"""Point d'entrée pour ``python -m myquantstore`` et le script console ``myquantstore``.
# PYTHON: ARGCOMPLETE_OK

Le marqueur ``# PYTHON: ARGCOMPLETE_OK`` ci-dessus active l'autocompletion shell
via ``argcomplete`` quand ce module est invoqué par le script console généré par
pip/uv (le script importe :func:`main` depuis ce module).
"""

from myquantstore.cli import main

if __name__ == "__main__":
    main()
