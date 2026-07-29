"""Cache des corporate actions stocks (splits, dividends).

Package miroir de :mod:`myquantstore.contracts` mais pour les stocks : cache
Parquet + sidecar ``.meta.json`` avec TTL (commun via ``instrument_cache_ttl_days``).
"""
