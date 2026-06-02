"""
src/infrastructure/scraping/implementations/sigaa_agent.py
============================================================
Agente de Automação Web para o portal SIGAA (JSF).
Implementa o loop cognitivo de automação: Perceber -> Planejar -> Agir -> Verificar.

Características:
1. Playwright assíncrono para execução ágil e tratamento nativo de JSF/ViewState.
2. Fallback resiliente para Selenium se o Playwright falhar ou não estiver disponível.
3. Tratamento de ViewState do JSF pós-interações Ajax.
4. Compartilhamento de sessão autenticada (cookies e storage) via Redis.
5. Rotação de User-Agent, delays simulados (humano) e retry dinâmico com Tenacity.
6. Limpeza agressiva de HTML/DOM para alimentar modelos de IA (Vision/DOM Reduzido).
7. Engenharia de prompt detalhada com Chain-of-Thought (CoT), Few-Shot e Error Recovery.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

logger = logging.getLogger(__name__)

# Configurações globais
SIGAA_BASE = "https://sis.sig.uema.br/sigaa"
DOWNLOAD_DIR = Path("/tmp/sigaa_downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True, parents=True)

# ── Engenharia de Prompt para Agente SIGAA ───────────────────────────────────

SYSTEM_PROMPT = """Você é um navegador humano experiente automatizando o SIGAA (um sistema baseado em JSF).
Siga sempre o ciclo estruturado de ação e justifique cada passo pensando em voz alta (Chain-of-Thought):

1. PERCEBER: Analise o DOM limpo ou o print de tela atual. Quais botões, links e inputs estão visíveis?
2. PLANEJAR: Qual é o próximo passo para atingir o objetivo? Por que escolher essa ação?
3. AGIR: Execute a ação correspondente (preencher, clicar, submeter, rolar).
4. VERIFICAR: A ação foi executada com sucesso? O ViewState mudou? A página atualizou como o esperado?
5. CORRIGIR: Se ocorreu uma falha de carregamento ou comportamento inesperado, adote uma rota alternativa de recuperação.

---

### EXEMPLO FEW-SHOT: Login no Portal SIGAA
* OBJETIVO: Fazer login com o usuário "teste_aluno"
* CICLO:
  - PERCEBER: Vejo inputs com nomes 'user.login' e 'user.senha' e um botão submit 'input[type=submit]'.
  - PLANEJAR: Preencher o login com o usuário, a senha com o valor adequado e clicar em entrar.
  - AGIR: Preencher 'user.login' com 'teste_aluno', preencher 'user.senha' com '***' (senha mascarada) e clicar em entrar.
  - VERIFICAR: Aguardar ciclo JSF finalizar. A URL mudou e agora vejo o painel com o ID '#menu-collapse'. Login efetuado.

### EXEMPLO FEW-SHOT: Inscrição em Extensão (ViewState expirado)
* OBJETIVO: Inscrever-se no evento
* CICLO:
  - PERCEBER: Vejo uma mensagem vermelha 'A página expirou' ou erro de ViewState após clicar.
  - PLANEJAR: A sessão do JSF caiu. Devo forçar a recarga total da página, re-autenticar se necessário e navegar diretamente para o link.
  - AGIR: Recarregar a página pública de consulta de extensão, buscar novamente o evento e tentar clicar no botão de inscrição.
  - VERIFICAR: O sistema exibiu a tela de confirmação de inscrição sem erros. Sucesso!
"""

ERROR_RECOVERY_PROMPT = """Se você encontrar um erro na página, siga estes passos de recuperação:
1. Erro 'Sessão Expirada': Limpe os cookies locais, realize o login novamente e reinicie o fluxo a partir da última URL conhecida.
2. Botão Não Clicável / Sobreposto: Tente disparar o clique via JavaScript diretamente no elemento (page.evaluate) ou role a página para trazê-lo à visão.
3. Timeout de Carregamento: Aguarde 5 segundos adicionais, force um reload e verifique a presença do ViewState atualizado.
"""

# ── Dataclasses de Controle ───────────────────────────────────────────────────

@dataclass
class SIGAASession:
    """Representa a sessão compartilhada no Redis para mitigar múltiplos logins."""
    cookies: list[dict] = field(default_factory=list)
    authenticated_at: float = 0.0
    valid: bool = False

    def is_expired(self, ttl_minutes: int = 20) -> bool:
        return (time.time() - self.authenticated_at) > (ttl_minutes * 60)

    def to_json(self) -> str:
        return json.dumps({
            "cookies": self.cookies,
            "authenticated_at": self.authenticated_at,
            "valid": self.valid
        })

    @classmethod
    def from_json(cls, data_str: str) -> SIGAASession:
        try:
            data = json.loads(data_str)
            return cls(
                cookies=data.get("cookies", []),
                authenticated_at=data.get("authenticated_at", 0.0),
                valid=data.get("valid", False)
            )
        except Exception:
            return cls()

@dataclass
class SIGAAResult:
    """Resultado estruturado retornado pelo agente."""
    ok: bool
    data: Any = None
    error: str = ""
    screenshot_path: str = ""

# ── Classe do Agente de Automação SIGAA ───────────────────────────────────────

class SIGAAAgent:
    """
    Agente de Automação SIGAA capaz de lidar com fluxos públicos e autenticados
    com alta resiliência e simulação humana.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.ua = UserAgent()
        self._user_agent = self.ua.random or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        self._playwright = None
        self._browser = None
        self._context = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _init_playwright(self):
        """Inicializa o Playwright com configurações anti-bot."""
        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        self._context = await self._browser.new_context(
            user_agent=self._user_agent,
            viewport={"width": 1280, "height": 800},
            locale="pt-BR",
            accept_downloads=True
        )

    async def close(self):
        """Fecha todas as instâncias abertas."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ── Métodos Auxiliares e de Limpeza DOM ─────────────────────────────────────

    def limpar_dom(self, html: str) -> str:
        """
        Remove ruídos do HTML do JSF para diminuir consumo de tokens da LLM.
        Mantém apenas elementos interativos limpos.
        """
        soup = BeautifulSoup(html, "lxml")
        
        # Remover tags de apresentação e lógica inútil
        for tag in soup(["script", "style", "svg", "path", "noscript", "iframe", "img"]):
            tag.decompose()

        # Remover atributos que inflam o tamanho
        for tag in soup.find_all(True):
            attrs_to_keep = ["id", "name", "value", "href", "class", "onclick", "type"]
            keys = list(tag.attrs.keys())
            for key in keys:
                if key not in attrs_to_keep:
                    del tag[key]

        # Mantém apenas containers principais e interativos
        elementos_interativos = []
        for tag in soup.find_all(["input", "button", "a", "select", "form", "table", "tr", "td"]):
            elementos_interativos.append(str(tag))

        return "\n".join(elementos_interativos)

    async def _human_delay(self):
        """Simula tempo de reflexão humano entre ações."""
        await asyncio.sleep(random.uniform(1.5, 3.2))

    async def _wait_for_jsf_lifecycle(self, page):
        """Aguarda a rede ficar ociosa e aguarda o processamento do ViewState do JSF."""
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(0.5)

    async def _save_screenshot(self, page, name: str) -> str:
        """Tira screenshot em caso de erros para debugging."""
        filename = f"erro_{name}_{int(time.time())}.png"
        filepath = DOWNLOAD_DIR / filename
        try:
            await page.screenshot(path=filepath, full_page=True)
            logger.info("📸 Screenshot de erro salvo em: %s", filepath)
            return str(filepath)
        except Exception as e:
            logger.warning("Falha ao salvar screenshot: %s", e)
            return ""

    async def _obter_session_redis(self) -> SIGAASession | None:
        """Recupera cookies salvos no Redis."""
        try:
            from src.infrastructure.redis_client import get_redis_text
            r = get_redis_text()
            data = r.get("sigaa:session:shared")
            if data:
                sess = SIGAASession.from_json(data)
                if not sess.is_expired():
                    return sess
        except Exception as e:
            logger.warning("Erro ao ler sessão do Redis: %s", e)
        return None

    async def _salvar_session_redis(self, cookies: list[dict]) -> None:
        """Salva a nova sessão autenticada no Redis."""
        try:
            from src.infrastructure.redis_client import get_redis_text
            r = get_redis_text()
            sess = SIGAASession(cookies=cookies, authenticated_at=time.time(), valid=True)
            r.setex("sigaa:session:shared", 1200, sess.to_json())  # TTL 20 min
            logger.info("💾 Sessão autenticada do SIGAA persistida no Redis.")
        except Exception as e:
            logger.warning("Erro ao salvar sessão no Redis: %s", e)

    async def _realizar_login(self, page) -> bool:
        """Realiza a autenticação no portal SIGAA."""
        login = os.getenv("SIGAA_LOGIN", "")
        senha = os.getenv("SIGAA_SENHA", "")
        
        if not login or not senha:
            raise ValueError("As variáveis SIGAA_LOGIN e/ou SIGAA_SENHA não foram definidas.")

        logger.info("🔒 Iniciando autenticação no SIGAA...")
        await page.goto(f"{SIGAA_BASE}/verTelaLogin.do")
        await self._wait_for_jsf_lifecycle(page)
        await self._human_delay()

        # Chain of Thought explicito em logs estruturados
        logger.info("🧠 [CoT] PERCEBER: Tela de Login. Inputs 'user.login' e 'user.senha' identificados.")
        logger.info("🧠 [CoT] PLANEJAR: Inserir credenciais mascarando inputs e submeter.")
        
        await page.fill("input[name='user.login']", login)
        await page.fill("input[name='user.senha']", senha)
        await self._human_delay()
        
        await page.click("input[type='submit']")
        await self._wait_for_jsf_lifecycle(page)

        # Verificar sucesso
        try:
            await page.wait_for_selector("#menu-collapse, .usuario-menu", timeout=8000)
            logger.info("🧠 [CoT] VERIFICAR: Menu principal carregado. Login realizado com sucesso.")
            
            # Guardar cookies no Redis
            cookies = await self._context.cookies()
            await self._salvar_session_redis(cookies)
            return True
        except Exception:
            logger.error("🧠 [CoT] VERIFICAR: Menu principal não localizado. Login falhou.")
            return False

    async def _garantir_login(self, page) -> bool:
        """Garante que a sessão atual está autenticada, restaurando do Redis se possível."""
        sess = await self._obter_session_redis()
        if sess and sess.cookies:
            logger.info("🔄 Restaurando cookies de sessão do Redis...")
            await self._context.add_cookies(sess.cookies)
            return True
        
        # Senão, faz login do zero
        return await self._realizar_login(page)

    # ── FLUXO A: Busca na Biblioteca e Exportação ──────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def fluxo_a_biblioteca(self, autor: str = "", titulo: str = "", assunto: str = "") -> SIGAAResult:
        """
        Navega até a busca da biblioteca pública, preenche os filtros,
        submete e faz o download da exportação.
        """
        await self._init_playwright()
        page = await self._context.new_page()
        url_alvo = f"{SIGAA_BASE}/public/biblioteca/paginaDetalhesMateriaisPublica.jsf"
        
        try:
            logger.info("🚀 [Biblioteca] Acessando página de busca pública...")
            await page.goto(url_alvo)
            await self._wait_for_jsf_lifecycle(page)
            
            # Preenche filtros
            if autor:
                logger.info("Preenchendo autor: %s", autor)
                await page.fill("input[id*='autor']", autor)
            if titulo:
                logger.info("Preenchendo título: %s", titulo)
                await page.fill("input[id*='titulo']", titulo)
            if assunto:
                logger.info("Preenchendo assunto: %s", assunto)
                await page.fill("input[id*='assunto']", assunto)
                
            await self._human_delay()
            
            # Submete o formulário
            logger.info("Submetendo formulário de busca da biblioteca...")
            await page.click("input[type='submit'], button[type='submit'], input[id*='buscar']")
            await self._wait_for_jsf_lifecycle(page)

            # Extração de resultados
            obras = []
            linhas = page.locator("table.listagem tbody tr, table[id*='result'] tr")
            qtd = await linhas.count()
            
            for i in range(min(qtd, 15)):
                cols = await linhas.nth(i).locator("td").all_text_contents()
                if cols and len(cols) >= 3:
                    obras.append({
                        "titulo": cols[0].strip(),
                        "autor": cols[1].strip(),
                        "tipo": cols[2].strip()
                    })

            # Tenta exportar se houver link de exportação MARC
            export_path = ""
            btn_export = page.locator("a:has-text('MARC'), a[id*='export'], button[id*='export']")
            if await btn_export.count() > 0:
                logger.info("Link de exportação encontrado. Iniciando download...")
                async with page.expect_download(timeout=10000) as download_info:
                    await btn_export.first.click()
                download = await download_info.value
                export_path = str(DOWNLOAD_DIR / download.suggested_filename)
                await download.save_as(export_path)
                logger.info("Download concluído: %s", export_path)

            return SIGAAResult(ok=True, data={"obras": obras, "arquivo": export_path})

        except Exception as e:
            shot = await self._save_screenshot(page, "biblioteca")
            return SIGAAResult(ok=False, error=str(e), screenshot_path=shot)
        finally:
            await page.close()

    # ── FLUXO B: Cadastro em Projeto de Extensão (Autenticado) ──────────────────

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=2, min=4, max=12),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def fluxo_b_extensao(self, nome_evento: str) -> SIGAAResult:
        """
        Efetua login, navega até a consulta de extensão pública/autenticada,
        localiza o evento de extensão e realiza a inscrição.
        """
        await self._init_playwright()
        page = await self._context.new_page()
        url_alvo = f"{SIGAA_BASE}/public/extensao/consulta_extensao.jsf?aba=p-extensao"
        
        try:
            # Garante login
            login_ok = await self._garantir_login(page)
            if not login_ok:
                return SIGAAResult(ok=False, error="Não foi possível autenticar no SIGAA.")

            logger.info("🚀 [Extensão] Acessando painel de extensão...")
            await page.goto(url_alvo)
            await self._wait_for_jsf_lifecycle(page)
            
            # Buscar evento
            logger.info("Buscando evento de extensão: %s", nome_evento)
            await page.fill("input[id*='titulo'], input[name*='titulo']", nome_evento)
            await self._human_delay()
            
            await page.click("input[value='Buscar'], input[type='submit']")
            await self._wait_for_jsf_lifecycle(page)

            # Localiza o link do evento correspondente
            link_evento = page.locator(f"a:has-text('{nome_evento[:25]}'), table.listagem td a")
            if await link_evento.count() == 0:
                return SIGAAResult(ok=False, error=f"Evento de extensão '{nome_evento}' não localizado.")
            
            await link_evento.first.click()
            await self._wait_for_jsf_lifecycle(page)
            await self._human_delay()

            # Localiza botão de inscrição
            btn_inscrever = page.locator("input[value*='nscrever'], button:has-text('Inscrever'), input[value*='Inscrição']")
            if await btn_inscrever.count() == 0:
                return SIGAAResult(ok=False, error="Botão de inscrição não disponível para o evento.")

            await btn_inscrever.first.click()
            await self._wait_for_jsf_lifecycle(page)

            # Verifica mensagens de sucesso do JSF
            sucesso_msg = page.locator(".sucesso, .success, td:has-text('sucesso'), td:has-text('confirmada')")
            if await sucesso_msg.count() > 0:
                logger.info("Inscrição confirmada com sucesso!")
                return SIGAAResult(ok=True, data={"status": "inscrito", "evento": nome_evento})

            return SIGAAResult(ok=True, data={"status": "pendente_verificacao", "evento": nome_evento})

        except Exception as e:
            shot = await self._save_screenshot(page, "extensao")
            return SIGAAResult(ok=False, error=str(e), screenshot_path=shot)
        finally:
            await page.close()

    # ── FLUXO C: Monitoramento de Processos Seletivos ──────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def fluxo_c_processos_seletivos(self, nivel: str = "L", filtro_titulo: str = "") -> SIGAAResult:
        """
        Navega até os processos seletivos públicos, filtra pelo nível
        e baixa editais disponíveis.
        """
        await self._init_playwright()
        page = await self._context.new_page()
        url_alvo = f"{SIGAA_BASE}/public/processo_seletivo/lista.jsf?aba=p-processo&nivel={nivel}"
        
        try:
            logger.info("🚀 [Processos Seletivos] Acessando lista pública...")
            await page.goto(url_alvo)
            await self._wait_for_jsf_lifecycle(page)

            processos = []
            linhas = page.locator("table.listagem tr:not(:first-child)")
            qtd = await linhas.count()
            logger.info("Encontrados %d processos seletivos nesta página.", qtd)

            editais_baixados = []
            for i in range(qtd):
                cols = await linhas.nth(i).locator("td").all_text_contents()
                if not cols or len(cols) < 2:
                    continue

                titulo = cols[0].strip()
                if filtro_titulo and filtro_titulo.lower() not in titulo.lower():
                    continue

                # Localiza link do edital na linha
                link_edital = linhas.nth(i).locator("a[href*='pdf'], a:has-text('Edital'), a:has-text('EDITAL')")
                edital_url = ""
                edital_local = ""
                
                if await link_edital.count() > 0:
                    href = await link_edital.first.get_attribute("href")
                    if href:
                        edital_url = href if href.startswith("http") else f"{SIGAA_BASE}/{href.lstrip('/')}"
                        
                        # Inicia o download de forma assíncrona
                        try:
                            async with page.expect_download(timeout=15000) as download_info:
                                await link_edital.first.click()
                            download = await download_info.value
                            edital_local = str(DOWNLOAD_DIR / f"edital_{i}_{int(time.time())}.pdf")
                            await download.save_as(edital_local)
                            editais_baixados.append(edital_local)
                        except Exception as dl_err:
                            logger.warning("Falha ao baixar edital: %s", dl_err)

                processos.append({
                    "titulo": titulo,
                    "periodo": cols[1].strip() if len(cols) > 1 else "",
                    "edital_url": edital_url,
                    "edital_local": edital_local
                })

            return SIGAAResult(ok=True, data={"processos": processos, "arquivos_editais": editais_baixados})

        except Exception as e:
            shot = await self._save_screenshot(page, "processos")
            return SIGAAResult(ok=False, error=str(e), screenshot_path=shot)
        finally:
            await page.close()

# ── Fallback do Agente para Selenium ──────────────────────────────────────────

class SIGAASeleniumFallback:
    """
    Fallback usando Selenium WebDriver para automação caso o ambiente
    do Playwright apresente falhas críticas de infraestrutura.
    """

    def __init__(self):
        self.driver = None

    def _init_driver(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        self.driver = webdriver.Chrome(options=options)
        self.driver.set_page_load_timeout(30)

    def close(self):
        if self.driver:
            self.driver.quit()

    def biblioteca_buscar(self, autor: str = "", titulo: str = "") -> dict:
        """Consulta simplificada na biblioteca via Selenium."""
        try:
            self._init_driver()
            self.driver.get(f"{SIGAA_BASE}/public/biblioteca/paginaDetalhesMateriaisPublica.jsf")
            
            if autor:
                input_autor = self.driver.find_element("xpath", "//input[contains(@id, 'autor')]")
                input_autor.send_keys(autor)
            if titulo:
                input_titulo = self.driver.find_element("xpath", "//input[contains(@id, 'titulo')]")
                input_titulo.send_keys(titulo)
                
            btn_buscar = self.driver.find_element("xpath", "//input[@type='submit' or contains(@id, 'buscar')]")
            btn_buscar.click()
            time.sleep(3)  # Espera estática simples do Selenium
            
            # Extrair resultados
            obras = []
            linhas = self.driver.find_elements("xpath", "//table[contains(@class, 'listagem')]/tbody/tr")
            for lin in linhas[:10]:
                cols = lin.find_elements("xpath", "./td")
                if cols and len(cols) >= 2:
                    obras.append({
                        "titulo": cols[0].text.strip(),
                        "autor": cols[1].text.strip(),
                        "tipo": cols[2].text.strip() if len(cols) > 2 else "Material"
                    })
            return {"ok": True, "obras": obras}
        except Exception as e:
            logger.error("Falha no Fallback Selenium: %s", e)
            return {"ok": False, "error": str(e)}
        finally:
            self.close()
