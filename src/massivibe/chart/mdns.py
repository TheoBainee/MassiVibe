"""Enregistrement mDNS (zeroconf) pour découverte réseau local.

Permet aux autres appareils du réseau local de découvrir le serveur chart
sans connaître l'IP exacte. Le service est enregistré sous le nom
``massivibe-chart._http._tcp.local.`` et peut être résolu via Bonjour/Avahi.

Usage typique : ``massivibe chart --mdns`` → accessible depuis une tablette
sur le même réseau via ``http://massivibe-chart.local:8050`` (selon le resolver
mDNS du client).
"""

from __future__ import annotations

from zeroconf import ServiceInfo, Zeroconf


def register_mdns(host: str, port: int, service_name: str = "massivibe-chart") -> Zeroconf:
    """Enregistre le serveur chart sur le réseau local via mDNS.

    :param host: Host bind (ex: "127.0.0.1" ou "0.0.0.0").
    :param port: Port d'écoute.
    :param service_name: Nom du service mDNS (sans extension).
    :return: Instance Zeroconf (à appeler ``.unregister()`` à l'arrêt).
    """
    import socket

    # Récupérer l'IP locale pour l'enregistrement
    # Si host = 0.0.0.0, on utilise l'IP de l'interface principale
    if host in ("0.0.0.0", "::"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    else:
        local_ip = host

    info = ServiceInfo(
        type_="_http._tcp.local.",
        name=f"{service_name}._http._tcp.local.",
        addresses=[socket.inet_aton(local_ip)],
        port=port,
        properties={"path": "/"},
        server=f"{service_name}.local.",
    )

    zeroconf = Zeroconf()
    zeroconf.register_service(info)
    return zeroconf
