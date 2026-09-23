"""Obtención segura de IP cliente para tráfico público servido por Render."""
from ipaddress import ip_address

from fastapi import Request


def _valid_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


def get_client_ip(request: Request) -> str:
    """Usa sólo el encabezado que Cloudflare controla delante de Render.

    Render añade la IP del cliente a ``X-Forwarded-For`` sin eliminar valores
    aportados por el cliente, por lo que ese encabezado no puede usarse para
    límites. ``CF-Connecting-IP`` sí es sobrescrito por Cloudflare. La cabecera
    se acepta únicamente cuando el par TCP es una dirección privada del proxy
    de Render; en cualquier otro caso se conserva la IP del socket.
    """
    peer_ip = _valid_ip(request.client.host if request.client else None)
    if peer_ip:
        peer = ip_address(peer_ip)
        cloudflare_ip = _valid_ip(request.headers.get("cf-connecting-ip"))
        if peer.is_private and cloudflare_ip:
            return cloudflare_ip
        return peer_ip
    return "unknown"
