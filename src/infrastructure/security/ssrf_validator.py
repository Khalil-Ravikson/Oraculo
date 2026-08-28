"""
Validação SSRF — obrigatória para toda URL registrada por admin que o
sistema vai posteriormente conectar (MCP servers, webhooks de canal, etc).

Motivação (docs/historico/plataforma_orientada_a_configuracao.md §G):
"SSRF continua o risco concreto mais alto do roadmap" — CVE real do
`stacklok/toolhive` (SSRF em descoberta de auth de servidor MCP remoto)
citado como precedente. Nenhuma validação SSRF existia no projeto antes
deste módulo (confirmado por grep) — nem mesmo no scraping de URL já
existente (`GenericHTTPScraper`), que é dívida pré-existente fora do
escopo deste módulo.

Escopo desta validação: **registro** (admin cadastra uma URL no Hub), não
**conexão** (o momento em que o sistema de fato chama a URL). Isso cobre o
caso de uso de hoje (nenhum código ainda conecta de fato nos MCP servers
registrados via Hub — ver mcp_server_registry.py) mas **não** protege
contra DNS rebinding (a URL resolver para IP público no registro e IP
privado numa chamada real posterior). Se/quando um MCP Connection Manager
de verdade conectar nessas URLs, ele precisa repetir esta validação (ou
pinar o IP resolvido) a cada conexão, não confiar só no registro — dívida
registrada aqui explicitamente, não escondida.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ESQUEMAS_PERMITIDOS = frozenset({"http", "https"})


class URLInseguraError(ValueError):
    """URL rejeitada por apontar (ou poder apontar) para rede privada/reservada."""


def _ip_privado_ou_reservado(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def validar_url_publica(url: str) -> None:
    """
    Valida que `url` usa esquema http(s) e resolve só para endereços
    públicos. Levanta `URLInseguraError` caso contrário — nunca retorna
    um booleano silencioso, porque esquecer de checar o retorno de uma
    validação de segurança é exatamente o tipo de bug que este módulo
    existe para prevenir.

    Cobre: esquema fora de http/https, IP literal privado/loopback/
    link-local/reservado/multicast (inclusive o clássico alvo de SSRF
    `169.254.169.254`, endpoint de metadata de nuvem), hostname que
    resolve (via DNS) para qualquer um desses — todos os endereços
    retornados por `getaddrinfo` são checados, não só o primeiro.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ESQUEMAS_PERMITIDOS:
        raise URLInseguraError(
            f"Esquema '{parsed.scheme or '(vazio)'}' não permitido — use http ou https."
        )

    hostname = parsed.hostname
    if not hostname:
        raise URLInseguraError("URL sem host.")

    try:
        ip_literal = ipaddress.ip_address(hostname)
    except ValueError:
        ip_literal = None

    if ip_literal is not None:
        if _ip_privado_ou_reservado(str(ip_literal)):
            raise URLInseguraError(
                f"IP '{hostname}' é privado/reservado — não permitido."
            )
        return

    try:
        enderecos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise URLInseguraError(f"Não foi possível resolver '{hostname}': {exc}") from exc

    if not enderecos:
        raise URLInseguraError(f"'{hostname}' não resolveu para nenhum endereço.")

    for _family, _type, _proto, _canon, sockaddr in enderecos:
        ip_resolvido = sockaddr[0]
        if _ip_privado_ou_reservado(ip_resolvido):
            raise URLInseguraError(
                f"'{hostname}' resolve para endereço privado/reservado "
                f"({ip_resolvido}) — não permitido."
            )
