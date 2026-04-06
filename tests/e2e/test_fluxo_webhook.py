def test_fluxo_completo_rag_via_webhook(cliente_api, mock_evolution):
    """
    Testa o pipeline completo: 
    Recebe mensagem -> Celery -> LangGraph -> Consulta Redis/LLM -> Responde
    """
    # 1. Payload simulando o que o WhatsApp (Evolution) envia para a API
    payload_webhook = {
        "event": "messages.upsert",
        "instance": "OraculoUEMA",
        "data": {
            "message": {
                "remoteJid": "5598900000000@s.whatsapp.net",
                "conversation": "Quais são as datas de matrícula para veteranos?"
            }
        }
    }

    # 2. Envia para o webhook
    # IMPORTANTE: Devido à configuração do Celery no conftest, 
    # isto vai bloquear e executar todo o LangGraph sincronicamente!
    resposta = cliente_api.post("/api/v1/webhook/evolution", json=payload_webhook)
    
    # O webhook deve aceitar a requisição
    assert resposta.status_code in [200, 201, 202]

    # 3. Verifica se o LangGraph completou e tentou responder!
    # A nossa dependência mock_evolution intercetou a chamada.
    assert mock_evolution.called is True, "A IA não tentou enviar resposta à Evolution API!"

    # 4. Extrai os dados que iam ser enviados para o WhatsApp
    args, kwargs = mock_evolution.call_args
    
    # Dependendo da tua assinatura em EvolutionAdapter.enviar_mensagem_texto(numero, texto)
    numero_destino = kwargs.get("numero") or args[0]
    mensagem_gerada = kwargs.get("mensagem") or kwargs.get("texto") or args[1]

    # 5. Asserções finais de comportamento
    assert "5598900000000" in numero_destino
    assert len(mensagem_gerada) > 10
    
    # Opcional: Validar se a LLM trouxe contexto correto do Redis
    assert "matrícula" in mensagem_gerada.lower() or "fevereiro" in mensagem_gerada.lower()