PROMPT_EXTRACAO_FATOS = """<objetivo>
Atue como um analista de perfil. Leia a conversa entre o aluno e o assistente da UEMA e extraia uma lista de fatos ESTÁTICOS e de LONGO PRAZO sobre o usuário.
</objetivo>

<regras>
1. Foque em: Curso, Centro, Vínculo (Calouro/Veterano/Servidor), Condições especiais, Problemas recorrentes de TI.
2. Ignore interesses passageiros (ex: "estudando pra prova de cálculo amanhã").
3. A saída DEVE ser estritamente uma lista separada por hífens. Se não houver nada útil, retorne a palavra "VAZIO".
</regras>

<exemplos>
[EXEMPLO 1]
<conversa>
User: Poxa, o SIGAA trancou de novo minha grade de Engenharia Civil.
Assistant: Sinto muito por isso! O problema foi no CECEN?
User: Sim, sou do noturno lá e todo semestre é essa dor de cabeça.
</conversa>
SAIDA:
- Aluno de Engenharia Civil
- Estuda no centro CECEN
- Turno noturno
- Relata problemas recorrentes com travamento de grade no SIGAA

[EXEMPLO 2]
<conversa>
User: legal, vlw
Assistant: Por nada!
</conversa>
SAIDA:
VAZIO
</exemplos>

<conversa>
{conversa}
</conversa>
SAIDA:
"""