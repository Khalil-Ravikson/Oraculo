"""Testes de src/infrastructure/security/ssrf_validator.py — superfície de
segurança crítica (registro de URL de MCP server/webhook). Sem rede real:
DNS é mockado via monkeypatch em socket.getaddrinfo."""

import socket
import pytest
from unittest.mock import patch
from src.infrastructure.security.ssrf_validator import (
    validar_url_publica, URLInseguraError,
)


def _addrinfo(*ips: str):
    """Simula retorno de socket.getaddrinfo pra uma lista de IPs."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


class TestEsquema:
    """Validação de esquema (scheme)."""

    def test_esquema_ftp_rejeitado(self):
        with pytest.raises(URLInseguraError, match="não permitido"):
            validar_url_publica("ftp://example.com/arquivo")

    def test_esquema_file_rejeitado(self):
        with pytest.raises(URLInseguraError, match="não permitido"):
            validar_url_publica("file:///etc/passwd")

    def test_esquema_vazio_rejeitado(self):
        with pytest.raises(URLInseguraError):
            validar_url_publica("example.com/sem-esquema")

    def test_sem_host_rejeitado(self):
        with pytest.raises(URLInseguraError, match="sem host"):
            validar_url_publica("https:///caminho-sem-host")


class TestIPLiteral:
    """URL com IP literal no lugar de hostname."""

    @pytest.mark.parametrize("host", [
        "127.0.0.1",              # loopback
        "127.0.0.53",             # loopback (systemd-resolved, alvo comum)
        "10.0.0.1",                # RFC1918
        "172.16.0.1",              # RFC1918
        "192.168.1.1",             # RFC1918
        "169.254.169.254",         # link-local — metadata AWS/GCP/Azure, alvo clássico de SSRF
        "0.0.0.0",                  # unspecified
        "224.0.0.1",                # multicast
        "203.0.113.10",             # TEST-NET-3 (RFC 5737, documentação) — ipaddress trata como is_private
        "[::1]",                     # loopback IPv6 (colchetes obrigatórios em URL)
        "[fe80::1]",                  # link-local IPv6
        "[fc00::1]",                  # unique local IPv6 (privado)
    ])
    def test_ip_privado_ou_reservado_rejeitado(self, host):
        with pytest.raises(URLInseguraError, match="privado/reservado"):
            validar_url_publica(f"http://{host}/mcp")

    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "9.9.9.9"])
    def test_ip_publico_aceito(self, ip):
        validar_url_publica(f"https://{ip}/mcp")  # não deve levantar


class TestHostnameComDNS:
    """Hostname que precisa de resolução DNS (mockada)."""

    def test_hostname_resolve_para_ip_publico_aceito(self):
        with patch("socket.getaddrinfo", return_value=_addrinfo("8.8.8.8")):
            validar_url_publica("https://gateway.pipeworx.io/stackexchange/mcp")

    def test_hostname_resolve_para_loopback_rejeitado(self):
        with patch("socket.getaddrinfo", return_value=_addrinfo("127.0.0.1")):
            with pytest.raises(URLInseguraError, match="privado/reservado"):
                validar_url_publica("https://malicioso.example/mcp")

    def test_hostname_resolve_para_metadata_endpoint_rejeitado(self):
        """O alvo clássico de SSRF em nuvem: um hostname público que
        aponta (via DNS attacker-controlled ou rebinding) pro endpoint de
        metadata da cloud."""
        with patch("socket.getaddrinfo", return_value=_addrinfo("169.254.169.254")):
            with pytest.raises(URLInseguraError, match="privado/reservado"):
                validar_url_publica("https://attacker-controlled.example/mcp")

    def test_hostname_com_multiplos_ips_um_privado_rejeitado(self):
        """Se getaddrinfo devolve vários endereços (IPv4 + IPv6, múltiplos
        registros A), TODOS precisam ser públicos — um só privado já
        rejeita, mesmo que o primeiro da lista seja público."""
        with patch("socket.getaddrinfo", return_value=_addrinfo("8.8.8.8", "10.0.0.5")):
            with pytest.raises(URLInseguraError, match="privado/reservado"):
                validar_url_publica("https://mix.example/mcp")

    def test_hostname_nao_resolve_rejeitado(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
            with pytest.raises(URLInseguraError, match="Não foi possível resolver"):
                validar_url_publica("https://nao-existe.invalid/mcp")

    def test_hostname_sem_enderecos_rejeitado(self):
        with patch("socket.getaddrinfo", return_value=[]):
            with pytest.raises(URLInseguraError, match="não resolveu"):
                validar_url_publica("https://vazio.example/mcp")
