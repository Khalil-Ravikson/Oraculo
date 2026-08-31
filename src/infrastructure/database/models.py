# src/infrastructure/database/models.py
from __future__ import annotations
from sqlalchemy import Boolean, Column, DateTime, Enum as SQLEnum, BigInteger, ForeignKey, Index, Text, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase
# Importa os Enums do domínio para tipar as colunas
from src.domain.entities.enums import RoleEnum, CentroEnum, StatusMatriculaEnum,TurnoEnum
from datetime import datetime, timezone
from sqlalchemy.orm import relationship

from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
class Base(DeclarativeBase):
    pass

class Pessoa(Base):
    """
    Fonte da Verdade no PostgreSQL.
    Usada apenas pelo PessoaRepository para consultar/cadastrar e montar a IdentidadeRica.
    """
    __tablename__ = "pessoas" # Letra minúscula é padrão em Postgres, mas pode usar "Pessoas" se já estiver no banco.

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    email = Column(String(200), unique=True, index=True, nullable=False)
    telefone = Column(String(20), unique=True, index=True, nullable=True)
    
    matricula = Column(String(20), unique=True, index=True, nullable=True)
    centro = Column(SQLEnum(CentroEnum), nullable=True)
    curso = Column(String(200), nullable=True)
    semestre_ingresso = Column(String(10), nullable=True)
    turno = Column(SQLEnum(TurnoEnum, name="turno_enum", create_type=False), nullable=True)
    role = Column(SQLEnum(RoleEnum), default=RoleEnum.publico, nullable=False)
    status = Column(SQLEnum(StatusMatriculaEnum), default=StatusMatriculaEnum.pendente, nullable=False)
    
    pode_abrir_chamado = Column(Boolean, default=True, nullable=False)
    verificado = Column(Boolean, default=False, nullable=False)

    criado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    @property
    def display_name(self) -> str:
        return self.nome.split()[0] if self.nome else "usuário"



"""
Adição ao src/infrastructure/database/models.py
================================================
Modelos ltree para a árvore institucional da UEMA (Graph RAG preparatório).

COMO USAR:
  Cole estas classes no seu models.py existente.
  Execute: alembic revision --autogenerate -m "add_ltree_models"
  E depois: alembic upgrade head

REQUER:
  1. Extensão ltree no PostgreSQL:
     Em uma migration anterior ou manualmente:
     CREATE EXTENSION IF NOT EXISTS ltree;

  2. sqlalchemy-utils>=0.41.1 no requirements.txt

ESTRUTURA ltree:
  UEMA
  UEMA.REITORIA
  UEMA.REITORIA.PROG
  UEMA.REITORIA.PROEXAE
  UEMA.CECEN
  UEMA.CECEN.ENGENHARIA_CIVIL
  UEMA.CESB
  UEMA.CTIC

BENEFÍCIOS para o RAG:
  - Queries hierárquicas: "todos os contatos do CECEN e subsetores"
  - Filtros de contexto: injetar path do aluno na query de retrieval
  - Navegação do org chart via SQL puro (sem código Python)
"""


# sqlalchemy-utils fornece o tipo Ltree nativo
try:
    from sqlalchemy_utils import LtreeType
    _LTREE_AVAILABLE = True
except ImportError:
    _LTREE_AVAILABLE = False
    LtreeType = String  # fallback para String se não instalado


# ── Nó da árvore institucional ────────────────────────────────────────────────

class UnidadeInstitucional:
    """
    Representa um nó na hierarquia da UEMA.
    
    Exemplos de path:
      UEMA                          → Universidade raiz
      UEMA.REITORIA                 → Reitoria
      UEMA.REITORIA.PROG            → Pró-Reitoria de Graduação
      UEMA.CECEN                    → Centro de Ciências Exatas e Naturais
      UEMA.CECEN.ENGENHARIA_CIVIL   → Curso de Engenharia Civil
    """
    __tablename__ = "unidades_institucionais"

    id       = Column(BigInteger, primary_key=True, autoincrement=True)
    path     = Column(LtreeType, nullable=False, unique=True, index=True)
    sigla    = Column(String(20),  nullable=False, index=True)
    nome     = Column(String(200), nullable=False)
    tipo     = Column(String(30),  nullable=False)   # reitoria|proretoria|centro|departamento|curso
    email    = Column(String(100), nullable=True)
    telefone = Column(String(20),  nullable=True)
    campus   = Column(String(50),  nullable=True,  default="São Luís")
    ativo    = Column(Boolean,     nullable=False, default=True)
    criado_em = Column(DateTime(timezone=True),
                       server_default="now()", nullable=False)
    atualizado_em = Column(DateTime(timezone=True),
                           onupdate=datetime.now, nullable=True)

    # Relacionamento com documentos do RAG
    documentos = relationship("DocumentoUnidade", back_populates="unidade",
                              cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Unidade path={self.path} sigla={self.sigla}>"

    @property
    def nivel(self) -> int:
        """Profundidade na árvore (UEMA=1, REITORIA=2, PROG=3...)."""
        return len(str(self.path).split("."))

    @property
    def path_label(self) -> str:
        """Label legível do path para uso no RAG como metadata."""
        return str(self.path).replace(".", " > ")


class DocumentoUnidade:
    """
    Relaciona documentos do RAG (chunks no Redis) com unidades institucionais.
    Permite filtrar chunks por unidade ou subárvore institucional.
    """
    __tablename__ = "documentos_unidades"

    id            = Column(BigInteger, primary_key=True, autoincrement=True)
    unidade_id    = Column(BigInteger, ForeignKey("unidades_institucionais.id",
                           ondelete="CASCADE"), nullable=False)
    chunk_id      = Column(String(50),  nullable=False, index=True)  # ID no Redis
    source        = Column(String(200), nullable=False)              # nome do arquivo
    doc_type      = Column(String(30),  nullable=True)
    titulo        = Column(String(200), nullable=True)
    indexado_em   = Column(DateTime(timezone=True),
                           server_default="now()", nullable=False)

    unidade = relationship("UnidadeInstitucional", back_populates="documentos")

    def __repr__(self) -> str:
        return f"<DocUnidade chunk={self.chunk_id} source={self.source}>"


# ── Migration SQL (para usar em alembic ou manual) ────────────────────────────

MIGRATION_SQL = """
-- Execute ANTES de rodar alembic upgrade:
CREATE EXTENSION IF NOT EXISTS ltree;

-- Seed de dados base da UEMA
INSERT INTO unidades_institucionais (path, sigla, nome, tipo, campus) VALUES
  ('UEMA',                        'UEMA',    'Universidade Estadual do Maranhão',     'universidade',  'São Luís'),
  ('UEMA.REITORIA',               'REI',     'Reitoria',                              'reitoria',      'São Luís'),
  ('UEMA.REITORIA.PROG',          'PROG',    'Pró-Reitoria de Graduação',             'proretoria',    'São Luís'),
  ('UEMA.REITORIA.PROEXAE',       'PROEXAE', 'Pró-Reitoria de Extensão',              'proretoria',    'São Luís'),
  ('UEMA.REITORIA.PRPPG',         'PRPPG',   'Pró-Reitoria de Pós-Graduação',        'proretoria',    'São Luís'),
  ('UEMA.REITORIA.PRAD',          'PRAD',    'Pró-Reitoria de Administração',         'proretoria',    'São Luís'),
  ('UEMA.CTIC',                   'CTIC',    'Centro de Tecnologia da Informação',    'departamento',  'São Luís'),
  ('UEMA.CECEN',                  'CECEN',   'Centro de Ciências Exatas e Naturais',  'centro',        'São Luís'),
  ('UEMA.CESB',                   'CESB',    'Centro de Estudos Superiores de Bacabal','centro',       'Bacabal'),
  ('UEMA.CESC',                   'CESC',    'Centro de Estudos Superiores de Caxias','centro',        'Caxias'),
  ('UEMA.CCSA',                   'CCSA',    'Centro de Ciências Sociais Aplicadas',  'centro',        'São Luís')
ON CONFLICT (path) DO NOTHING;

-- Índices para queries hierárquicas eficientes
CREATE INDEX IF NOT EXISTS idx_unidades_path_gist ON unidades_institucionais USING GIST (path);
CREATE INDEX IF NOT EXISTS idx_unidades_tipo ON unidades_institucionais (tipo);
CREATE INDEX IF NOT EXISTS idx_doc_unidade_source ON documentos_unidades (source);
"""


# ── Helper de query hierárquica ────────────────────────────────────────────────

def query_subarvore_sql(path_raiz: str) -> str:
    """
    Retorna SQL para buscar documentos de uma unidade e todos os seus filhos.
    
    Uso no RAG: quando aluno pergunta sobre "CECEN", busca também
    todos os cursos e departamentos abaixo do CECEN.
    
    Exemplo:
        sql = query_subarvore_sql("UEMA.CECEN")
        # Retorna todos os chunks de CECEN e subunidades
    """
    return f"""
        SELECT du.chunk_id, du.source, du.doc_type, u.sigla, u.nome, u.path::text
        FROM documentos_unidades du
        JOIN unidades_institucionais u ON u.id = du.unidade_id
        WHERE u.path <@ '{path_raiz}'
          AND u.ativo = true
        ORDER BY u.path
    """




class IntentRouter(Base):
    __tablename__ = "intents_router"

    id           = Column(Integer, primary_key=True)
    nome         = Column(String(50), nullable=False, unique=True)
    regex        = Column(String(400), nullable=True)
    exemplos     = Column(ARRAY(String), default=list)
    doc_type     = Column(String(50), nullable=True)
    k_vector     = Column(Integer, default=6)
    k_text       = Column(Integer, default=8)
    ativo        = Column(Boolean, default=True)
    criado_em    = Column(DateTime(timezone=True), server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), onupdate=func.now())

class AgenteCatalogo(Base):
    """Catálogo administrável de agentes (Sprint 2, Fase 3).

    `ativo`/`descricao` são o estado administrável de verdade. A coluna
    `permissions` (espelho vazio do RBAC nunca conectado) foi removida na
    Fase 5 — o vínculo agente↔capability virou a tabela `agente_tools`.
    """
    __tablename__ = "agentes_catalogo"

    id             = Column(Integer, primary_key=True)
    nome           = Column(String(50), nullable=False, unique=True, index=True)
    descricao      = Column(Text, nullable=True)
    ativo          = Column(Boolean, server_default="true", nullable=False)
    # Override de provider/modelo LLM por agente (migration 007). NULL nos
    # dois = herda o provider/modelo global (settings.LLM_PROVIDER ou
    # override em runtime via Redis `admin:llm_provider`).
    llm_provider   = Column(String(20), nullable=True)
    llm_model      = Column(String(50), nullable=True)
    criado_em      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_em  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    atualizado_por = Column(String(100), nullable=True)


class LlmPricing(Base):
    """Tabela de preço/1M tokens por provider+modelo (migration 008),
    editável via `/hub/llm-custo` — fonte de verdade que substitui
    `pricing.py::_PRECOS` hardcoded (que passa a ser só seed/fallback)."""
    __tablename__ = "llm_pricing"
    __table_args__ = (UniqueConstraint("provider", "modelo", name="uq_llm_pricing_provider_modelo"),)

    id             = Column(Integer, primary_key=True)
    provider       = Column(String(20), nullable=False)
    modelo         = Column(String(50), nullable=False)
    input_por_1m   = Column(Numeric(10, 4), nullable=False)
    output_por_1m  = Column(Numeric(10, 4), nullable=False)
    cache_por_1m   = Column(Numeric(10, 4), nullable=True)
    atualizado_em  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    atualizado_por = Column(String(100), nullable=True)


class AgentPrompt(Base):
    """Histórico versionado de prompts por agente (Sprint 2, Fase 7/8).

    Nunca faz UPDATE do texto — sempre INSERT de uma nova versão. No máximo
    1 linha `active=true` por `agent_name` (garantido por índice parcial
    único no schema, migration 006). Sem FK para `agentes_catalogo.nome`
    (deliberado, ver migration).
    """
    __tablename__ = "agent_prompts"

    id           = Column(Integer, primary_key=True)
    agent_name   = Column(String(50), nullable=False, index=True)
    prompt_text  = Column(Text, nullable=False)
    version      = Column(Integer, nullable=False)
    active       = Column(Boolean, server_default="false", nullable=False)
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_by   = Column(String(100), nullable=True)


class ConfigDinamica(Base):
    """Valor atual de cada chave de configuração dinâmica (migration 009,
    Plano A / Fase 1). Fonte de verdade é o Postgres; `dynamic_config.py`
    mantém um espelho no Redis para leitura no caminho quente.

    `versao` sobe a cada escrita e é usada para controle de concorrência
    otimista no repositório (`WHERE versao = :versao_lida`, §N). `tenant_id`
    é sempre NULL hoje — reservado para a Fase 9 (§M), nunca filtrado no
    código das Fases 1-5. Índice único `(tenant_id, chave)` com
    `NULLS NOT DISTINCT` (ver migration).
    """
    __tablename__ = "config_dinamica"
    __table_args__ = (
        Index(
            "ux_config_dinamica_tenant_chave", "tenant_id", "chave",
            unique=True, postgresql_nulls_not_distinct=True,
        ),
    )

    id             = Column(Integer, primary_key=True)
    chave          = Column(String(80), nullable=False)
    valor          = Column(Text, nullable=False)
    tipo           = Column(String(10), nullable=False)   # bool | int | str
    versao         = Column(Integer, server_default="1", nullable=False)
    tenant_id      = Column(PGUUID(as_uuid=True), nullable=True)
    atualizado_em  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_por = Column(String(100), nullable=True)


class ConfigDinamicaHistorico(Base):
    """Histórico append-only de `config_dinamica` (migration 009, §N).
    Nunca recebe UPDATE/DELETE do código — cada escrita insere uma linha
    com `valor_antigo`/`valor_novo` para o botão "reverter" do Hub.
    """
    __tablename__ = "config_dinamica_historico"

    id             = Column(Integer, primary_key=True)
    chave          = Column(String(80), nullable=False, index=True)
    valor_antigo   = Column(Text, nullable=True)
    valor_novo     = Column(Text, nullable=False)
    versao         = Column(Integer, nullable=False)
    tenant_id      = Column(PGUUID(as_uuid=True), nullable=True)
    atualizado_por = Column(String(100), nullable=True)
    atualizado_em  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RouteRegistry(Base):
    """Mapa rota→EXECUÇÃO (migration 010, Plano A / Fase 2). Colapsa os
    dicts/frozensets hardcoded de `contracts.py` / `dispatcher.py` /
    `dispatcher_langgraph.py` / `supervisor.py::_HINTS`.

    Fonte de verdade é o Postgres; `route_registry.py` espelha no Redis para
    o caminho quente. `versao` = optimistic lock (§N). `tenant_id` sempre
    NULL hoje (§M). Índice único `(tenant_id, rota)` NULLS NOT DISTINCT.
    Não cobre classificação — isso é `intents_router` (migration 003).
    """
    __tablename__ = "route_registry"
    __table_args__ = (
        Index(
            "ux_route_registry_tenant_rota", "tenant_id", "rota",
            unique=True, postgresql_nulls_not_distinct=True,
        ),
    )

    id              = Column(Integer, primary_key=True)
    rota            = Column(String(40), nullable=False)
    entrypoint_node = Column(String(40), nullable=False)   # state.route: rag|ticket|crud|greeting|sigaa|media_download|check_status
    owner           = Column(String(24), nullable=False)   # langgraph | langgraph_conditional | legacy
    agente          = Column(String(50), nullable=True)    # nome no registry; NULL p/ rotas utilitárias
    cacheavel       = Column(Boolean, nullable=False)
    permite_detour  = Column(Boolean, server_default="false", nullable=False)
    doc_type        = Column(String(30), nullable=True)
    k               = Column(Integer, nullable=True)
    planner_steps   = Column(ARRAY(String), nullable=True)
    versao          = Column(Integer, server_default="1", nullable=False)
    tenant_id       = Column(PGUUID(as_uuid=True), nullable=True)
    atualizado_em   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_por  = Column(String(100), nullable=True)


class RouteRegistryHistorico(Base):
    """Histórico append-only de `route_registry` (migration 010, §N). Guarda
    um snapshot da linha inteira por versão — o botão "reverter" do Hub
    restaura o snapshot como uma escrita nova."""
    __tablename__ = "route_registry_historico"

    id             = Column(Integer, primary_key=True)
    rota           = Column(String(40), nullable=False, index=True)
    versao         = Column(Integer, nullable=False)
    snapshot       = Column(JSONB, nullable=False)
    tenant_id      = Column(PGUUID(as_uuid=True), nullable=True)
    atualizado_por = Column(String(100), nullable=True)
    atualizado_em  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgenteTool(Base):
    """Vínculo agente↔capability (migration 012, Plano A / Fase 5). Substitui
    `agentes_catalogo.permissions`. Sem FK física (mesma decisão de
    `agent_prompts`). Código decide QUAIS via `agente.tools`; admin liga/desliga
    `habilitado` via /hub/capabilities. `tenant_id` sempre NULL (§M)."""
    __tablename__ = "agente_tools"
    __table_args__ = (
        Index(
            "ux_agente_tools_tenant_agente_tool", "tenant_id", "agente", "tool",
            unique=True, postgresql_nulls_not_distinct=True,
        ),
    )

    id             = Column(Integer, primary_key=True)
    agente         = Column(String(50), nullable=False)
    tool           = Column(String(60), nullable=False)
    habilitado     = Column(Boolean, server_default="true", nullable=False)
    tenant_id      = Column(PGUUID(as_uuid=True), nullable=True)
    atualizado_em  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_por = Column(String(100), nullable=True)


class GraphNodeConfig(Base):
    """Toggle habilitado/desabilitado + config overrides por nó do
    NodeRegistry (migration 013, continuação da Camada 1 — ver
    docs/decision_camada1_nodes.md). Mesmo padrão de `AgenteTool`: o código
    (`src/graph/nodes/`) decide QUAIS nós existem; esta tabela é só a
    Configuration Layer. `node_id` sem linha aqui é implicitamente
    habilitado. `versao` para optimistic concurrency (mesmo padrão de
    `config_dinamica`, §N)."""
    __tablename__ = "graph_node_config"
    __table_args__ = (
        Index(
            "ux_graph_node_config_tenant_node", "tenant_id", "node_id",
            unique=True, postgresql_nulls_not_distinct=True,
        ),
    )

    id               = Column(Integer, primary_key=True)
    node_id          = Column(String(80), nullable=False)
    habilitado       = Column(Boolean, server_default="true", nullable=False)
    config_overrides = Column(JSONB, server_default="{}", nullable=False)
    versao           = Column(Integer, server_default="1", nullable=False)
    tenant_id        = Column(PGUUID(as_uuid=True), nullable=True)
    atualizado_em    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_por   = Column(String(100), nullable=True)


class McpServer(Base):
    """Registro admin de servidor MCP (migration 014, Fase 8 — "MCP
    Connection Manager", primeira peça). Toda `url` gravada aqui já passou
    por `ssrf_validator.validar_url_publica()` no momento do registro
    (ver `src/graph/mcp_server_registry.py`). Ainda não há conexão real —
    `mcp_lab/clients.py` continua com as 3 URLs hardcoded; esta tabela é
    só o cadastro (dado), não está ligada à execução ainda."""
    __tablename__ = "mcp_servers"
    __table_args__ = (
        Index(
            "ux_mcp_servers_tenant_name", "tenant_id", "name",
            unique=True, postgresql_nulls_not_distinct=True,
        ),
    )

    id             = Column(Integer, primary_key=True)
    name           = Column(String(80), nullable=False)
    url            = Column(String(500), nullable=False)
    description    = Column(String(500), server_default="", nullable=False)
    habilitado     = Column(Boolean, server_default="true", nullable=False)
    versao         = Column(Integer, server_default="1", nullable=False)
    tenant_id      = Column(PGUUID(as_uuid=True), nullable=True)
    atualizado_em  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_por = Column(String(100), nullable=True)
    # Colunas de conexão (migration 019, Hub v2 Sprint 4)
    auth_tipo      = Column(String(20), server_default="none", nullable=False)
    auth_env       = Column(String(100), server_default="", nullable=False)
    latency_ms     = Column(Integer, nullable=True)
    last_checked   = Column(DateTime(timezone=True), nullable=True)
    tools_expostas = Column(JSONB, server_default="[]", nullable=False)


class Canal(Base):
    """Instância de comunicação cadastrada pelo painel (migration 018, Hub
    v2). Hoje só `whatsapp_evolution`. `config` = {base_url, api_key_env,
    instance}; a chave da Evolution segue no .env (`api_key_env` = nome da
    variável). O hot path de mensagem ainda lê de `settings` nesta fase —
    a tabela seeda com os mesmos valores. `tenant_id` sempre NULL."""
    __tablename__ = "canais"
    __table_args__ = (
        Index(
            "ux_canais_tenant_nome", "tenant_id", "nome",
            unique=True, postgresql_nulls_not_distinct=True,
        ),
    )

    id             = Column(Integer, primary_key=True)
    nome           = Column(String(80), nullable=False)
    tipo           = Column(String(30), server_default="whatsapp_evolution", nullable=False)
    config         = Column(JSONB, server_default="{}", nullable=False)
    webhook_url    = Column(String(500), server_default="", nullable=False)
    habilitado     = Column(Boolean, server_default="true", nullable=False)
    origem         = Column(String(10), server_default="painel", nullable=False)
    versao         = Column(Integer, server_default="1", nullable=False)
    tenant_id      = Column(PGUUID(as_uuid=True), nullable=True)
    atualizado_em  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_por = Column(String(100), nullable=True)


class LlmProvider(Base):
    """Provedor de LLM cadastrado pelo painel (migration 017, Hub v2). A
    chave de API NUNCA fica aqui — `api_key_env` guarda só o nome da
    variável de ambiente (decisão do dono; §P). `tipo` gemini/deepseek/groq
    são seeds dos builders de código; `openai_compat` é provedor novo
    instanciado por `OpenAICompatibleProvider`. `tenant_id` sempre NULL."""
    __tablename__ = "llm_providers"
    __table_args__ = (
        Index(
            "ux_llm_providers_tenant_nome", "tenant_id", "nome",
            unique=True, postgresql_nulls_not_distinct=True,
        ),
    )

    id             = Column(Integer, primary_key=True)
    nome           = Column(String(40), nullable=False)
    tipo           = Column(String(20), nullable=False)
    base_url       = Column(String(300), server_default="", nullable=False)
    api_key_env    = Column(String(100), server_default="", nullable=False)
    modelos        = Column(JSONB, server_default="[]", nullable=False)
    modelo_default = Column(String(100), server_default="", nullable=False)
    habilitado     = Column(Boolean, server_default="true", nullable=False)
    origem         = Column(String(10), server_default="painel", nullable=False)
    versao         = Column(Integer, server_default="1", nullable=False)
    tenant_id      = Column(PGUUID(as_uuid=True), nullable=True)
    atualizado_em  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_por = Column(String(100), nullable=True)


class ToolCatalogo(Base):
    """Ferramenta criada pelo painel (migration 016, Hub v2). O admin
    cadastra via /hub/capabilities uma ferramenta `http` (chamada REST
    definida por dado, URL validada por SSRF no cadastro) ou `mcp`
    (ferramenta exposta por um servidor de `mcp_servers`). Fica disponível
    para vincular a um agente via `agente_tools`. Ferramenta com lógica
    Python arbitrária continua vindo de código (`capabilities/registry.py`).
    `config` guarda o shape do tipo. `tenant_id` sempre NULL hoje (§M)."""
    __tablename__ = "tools_catalogo"
    __table_args__ = (
        Index(
            "ux_tools_catalogo_tenant_nome", "tenant_id", "nome",
            unique=True, postgresql_nulls_not_distinct=True,
        ),
    )

    id             = Column(Integer, primary_key=True)
    nome           = Column(String(60), nullable=False)
    tipo           = Column(String(20), nullable=False)   # "http" | "mcp"
    descricao      = Column(String(500), server_default="", nullable=False)
    config         = Column(JSONB, server_default="{}", nullable=False)
    permissoes     = Column(JSONB, server_default="[]", nullable=False)
    confirmacao    = Column(Boolean, server_default="false", nullable=False)
    habilitado     = Column(Boolean, server_default="true", nullable=False)
    versao         = Column(Integer, server_default="1", nullable=False)
    tenant_id      = Column(PGUUID(as_uuid=True), nullable=True)
    atualizado_em  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_por = Column(String(100), nullable=True)


class GraphTopology(Base):
    """Composição visual de grafo (migration 015, adendo de nós
    declarativos, Camada 3). `topology_json` = {"nodes": [...], "edges":
    [...]} — validado por `src/graph/topology_validator.py` (tipos de
    porta + DAG) ANTES de qualquer escrita. Sem execução real ainda
    (GraphExecutor não existe) — só persistência da composição."""
    __tablename__ = "graph_topology"
    __table_args__ = (
        Index(
            "ux_graph_topology_tenant_name", "tenant_id", "name",
            unique=True, postgresql_nulls_not_distinct=True,
        ),
    )

    id             = Column(Integer, primary_key=True)
    name           = Column(String(80), nullable=False)
    description    = Column(String(500), server_default="", nullable=False)
    topology_json  = Column(JSONB, nullable=False)
    status         = Column(String(20), server_default="draft", nullable=False)
    versao         = Column(Integer, server_default="1", nullable=False)
    tenant_id      = Column(PGUUID(as_uuid=True), nullable=True)
    atualizado_em  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_por = Column(String(100), nullable=True)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id            = Column(Integer, primary_key=True)
    chunk_id      = Column(String(16), nullable=False, unique=True, index=True)
    source        = Column(String(300), nullable=False, index=True)
    titulo        = Column(String(500), nullable=True)
    doc_type      = Column(String(50), nullable=True, index=True)
    chunk_index   = Column(Integer, nullable=False)
    chars         = Column(Integer, nullable=True)
    parser_usado  = Column(String(50), nullable=True)
    chunker_usado = Column(String(50), nullable=True)
    label         = Column(String(300), nullable=True)
    indexado_em   = Column(DateTime(timezone=True), server_default=func.now())