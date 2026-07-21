"""
src/agents/conversation/registration.py
==========================================
Ex `application/routing/registration_funnel.py` (Fase 6 do
PLANO_REFATORACAO_SUPERVISOR.md, seção 2.6) — a máquina de estados de
cadastro/onboarding é conhecimento de negócio do agente de conversa, não
roteamento. SQL cru migrou para
`capabilities/persistence/registration_repository.py`; o envio de botões via
Evolution API migrou para `capabilities/messaging/evolution_tool.py`.

Comportamento idêntico ao original — só a localização e a decomposição em
capabilities mudaram. `application/routing/registration_funnel.py` vira um
shim de compatibilidade (mesmo padrão da Fase 2 para `application/routing/`).

Estados no Redis (TTL 10min):
  register:mode:{sender}  = "1"
  register:step:{sender}  = "awaiting_name" | "awaiting_course"
  register:name:{sender}  = <nome capturado>
"""
from __future__ import annotations
import logging

from src.agents.base import AgentEnabledMixin

logger = logging.getLogger(__name__)

_BOAS_VINDAS = (
    "👋 Olá! Para usar o *Oráculo UEMA*, preciso de alguns dados.\n\n"
    "Qual é o seu *nome completo*?"
)
_PERGUNTA_CURSO = "Perfeito, {nome}! Qual é o seu *curso* na UEMA?"
_CADASTRO_OK = (
    "✅ Cadastro realizado! Bem-vindo(a), *{nome}*!\n\n"
    "Agora pode perguntar sobre calendário, editais, contatos ou suporte."
)


class RegistrationFunnel:

    async def process(self, sender: str, text: str, push_name: str, redis, chat_id: str | None = None) -> str:
        """
        Retorna a próxima mensagem a enviar, ou '' se o fluxo terminou.

        `chat_id`: JID de entrega (grupo homologado ou 1:1). Em grupo, NUNCA é
        igual a `sender` (o JID do remetente individual) — e com o novo
        addressing @lid do WhatsApp, montar um JID a partir do número do
        remetente pra enviar direto pra ele quebra com "exists:false"
        (Evolution não reconhece o LID como número de telefone real). Todo
        envio deve ir para `chat_id`, igual ao resto do funil já faz via
        `gateway.enviar_mensagem(chat_id, reply)` em process_message_task.py.
        """
        step = redis.get(f"register:step:{sender}") or "start"

        if step == "start":
            redis.setex(f"register:mode:{sender}", 600, "1")
            redis.setex(f"register:step:{sender}", 600, "awaiting_name")
            return _BOAS_VINDAS

        if step == "awaiting_name":
            nome = text.strip().title()
            if len(nome) < 3:
                return "Por favor, informe seu nome completo."
            redis.setex(f"register:name:{sender}", 600, nome)
            redis.setex(f"register:step:{sender}", 600, "awaiting_course")
            return _PERGUNTA_CURSO.format(nome=nome.split()[0])

        if step == "awaiting_course":
            nome  = redis.get(f"register:name:{sender}") or push_name or "Aluno"
            curso = text.strip().title()
            if len(curso) < 3:
                return "Por favor, informe o nome do seu curso."

            sucesso = await self._salvar_usuario(sender, nome, curso)
            if not sucesso:
                # Não limpa o estado do Redis nem confirma sucesso: a próxima
                # mensagem do usuário reprocessa este mesmo passo, tentando
                # salvar de novo (Sprint 3, Fase 0 — ver registration_repository.py).
                return (
                    "⚠️ Tivemos um problema técnico ao salvar seu cadastro. "
                    "Por favor, envie o nome do seu curso novamente."
                )

            # Limpa estado de registro
            for key in (f"register:mode:{sender}",
                        f"register:step:{sender}",
                        f"register:name:{sender}"):
                redis.delete(key)

            try:
                from src.capabilities.messaging.evolution_tool import enviar_botoes_confirmacao
                await enviar_botoes_confirmacao(
                    number=chat_id or sender,
                    title="Cadastro Concluído!",
                    description=f"Bem-vindo(a), {nome.split()[0]}! O seu cadastro no curso de {curso} foi salvo. Os dados estão corretos?",
                    buttons=[
                        {"type": "reply", "displayText": "✅ Sim, corretos", "id": "btn_ok"},
                        {"type": "reply", "displayText": "❌ Refazer", "id": "btn_refazer"},
                    ]
                )
                return ""  # Retornamos vazio porque a mensagem já foi enviada pelos botões acima
            except Exception as e:
                logger.error("Erro ao enviar botões: %s", e)
                # Fallback: Se o botão falhar, manda texto normal
                return _CADASTRO_OK.format(nome=nome.split()[0])

    @staticmethod
    async def _salvar_usuario(telefone: str, nome: str, curso: str) -> bool:
        from src.capabilities.persistence.registration_repository import salvar_pessoa
        try:
            await salvar_pessoa(telefone, nome, curso)
            logger.info("✅ Usuário cadastrado via funil: %s", telefone[-6:])
            return True
        except Exception as e:
            logger.error("❌ RegistrationFunnel._salvar_usuario: %s", e)
            return False


class ConversationAgent(AgentEnabledMixin):
    """
    BaseAgent mínimo (ver agents/base.py e agents/registry.py, Fase 2).
    Registrado no Agent Registry, mas AINDA NÃO é o caminho quente de
    produção: o funil de cadastro real é despachado por
    `application/tasks/process_message_task.py` chamando `RegistrationFunnel`
    diretamente (é uma máquina de estados multi-turno amarrada ao ciclo de
    webhook/Redis, não uma pergunta única resolvível via
    `agent.execute(context)`). Existe para uso futuro (ex.: quando o
    Supervisor passar a resolver por Agent Registry em vez de rota crua).
    """
    name = "conversation"
    description = (
        "Saudação, boas-vindas e funil de cadastro de novos usuários. "
        "🧪 Rodada de testes: com settings.DEV_TEST_NO_DB_WRITE ativo, o cadastro final "
        "grava JSON em dados/tmp/cadastro_dev/ em vez de INSERT/UPDATE real em `pessoas`."
    )
    permissions: list[str] = []

    def __init__(self) -> None:
        self._funnel = RegistrationFunnel()

    async def execute(self, context):
        from src.agents.base import AgentResponse

        identity = context.identity or {}
        conversation = context.conversation or {}
        resposta = await self._funnel.process(
            sender=identity.get("telefone", context.session_id),
            text=conversation.get("query", ""),
            push_name=identity.get("nome", ""),
            redis=context.redis,
            chat_id=conversation.get("chat_id"),
        )
        return AgentResponse(answer=resposta or "")
