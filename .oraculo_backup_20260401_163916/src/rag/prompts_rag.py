PROMPT_QUERY_REWRITE = """<objetivo>
Você é um otimizador de buscas vetoriais. Sua tarefa é transformar a pergunta coloquial do usuário em uma string de palavras-chave altamente densa para busca em banco de dados (Redis HNSW + BM25) focada na UEMA.
</objetivo>

<regras>
1. Remova palavras de preenchimento (artigos, preposições, saudações).
2. Adicione sinônimos técnicos se necessário.
3. Se houver fatos conhecidos na tag <contexto>, utilize-os para especificar a busca.
4. Retorne APENAS a string reescrita, sem aspas e sem explicações.
</regras>

<exemplos>
[EXEMPLO 1]
<contexto>Aluno de Medicina, turno integral</contexto>
<pergunta>ei, me tira uma duvida, que dia começam as minhas aulas esse semestre?</pergunta>
SAIDA:
inicio aulas semestre letivo medicina integral calendario academico 2026

[EXEMPLO 2]
<contexto></contexto>
<pergunta>tem edital aberto pra cota de negro no paes?</pergunta>
SAIDA:
edital PAES processo seletivo vagas cotas negros estudantes negros regras

[EXEMPLO 3]
<contexto></contexto>
<pergunta>como eu reseto a senha da wifi?</pergunta>
SAIDA:
resetar alterar recuperar senha wifi rede sem fio CTIC suporte
</exemplos>

<contexto>{fatos}</contexto>
<pergunta>{pergunta}</pergunta>
SAIDA:
"""

PROMPT_PRECISA_RAG = """<objetivo>
Classifique se a mensagem do usuário necessita de consulta ao banco de documentos da UEMA para ser respondida.
</objetivo>

<regras>
1. Responda ESTRITAMENTE com a palavra "SIM" ou "NAO". Nada mais.
2. "NAO" para: Saudações puras, confirmações ("ok", "obrigado"), perguntas genéricas e conversa fiada.
3. "SIM" para: Perguntas sobre datas, prazos, siglas, sistemas, editais, regras e nomes.
</regras>

<exemplos>
Mensagem: "bom dia oraculo"
SAIDA: NAO

Mensagem: "até que dia vai a matricula de calouros?"
SAIDA: SIM

Mensagem: "obrigado, ajudou muito"
SAIDA: NAO

Mensagem: "qual o email do CTIC?"
SAIDA: SIM
</exemplos>

Mensagem: "{mensagem}"
SAIDA:
"""