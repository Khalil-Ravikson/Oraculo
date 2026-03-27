# src/application/use_cases/messages.py

MSG_BOAS_VINDAS_PUBLICO = (
    "👋 Olá! Sou o *Oráculo*, o assistente virtual da *UEMA* (Universidade Estadual do Maranhão).\n\n"
    "Posso ajudar com:\n"
    "📅 Calendário acadêmico e prazos\n"
    "📋 Edital e informações do PAES 2026\n"
    "📞 Contatos e setores da universidade\n"
    "💻 Suporte técnico (Wiki do CTIC)\n\n"
    "O que você gostaria de saber sobre a UEMA? 🎓"
)

MSG_BOAS_VINDAS_USUARIO = (
    "Olá, {nome}! 😊 Bem-vindo de volta ao *Oráculo* da UEMA.\n"
    "No que posso te ajudar hoje?"
)

MSG_CADASTRO_NECESSARIO = (
    "Para acessar essa informação, você precisa ter cadastro no sistema do Oráculo. 📝\n\n"
    "O cadastro é rápido e gratuito! Me diga seu *email institucional UEMA* e te ajudo agora mesmo."
)

MSG_FORA_DOMINIO = (
    "Fico feliz em conversar, mas minha especialidade é a *UEMA*! 😊\n"
    "Posso te ajudar com calendário acadêmico, editais, vagas, contatos da universidade ou suporte técnico do CTIC.\n"
    "O que você precisa sobre a UEMA?"
)

OUTPUTS_INVALIDOS = [
    "agent stopped due to max iterations",
    "agent stopped due to iteration limit",
    "parsing error",
    "invalid or incomplete response",
]

MSG_NAO_ENCONTRADO = (
    "Não encontrei essa informação específica nos meus documentos. 🔍\n"
    "Tente reformular a pergunta, ou consulte diretamente o site da UEMA: "
    "*uema.br* | Secretaria do seu curso | Email: ctic@uema.br para suporte técnico."
)