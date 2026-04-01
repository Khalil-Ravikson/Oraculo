# tests/e2e/simulate_webhook.py

import json
import urllib.request
import urllib.error
import time

# URL do nosso webhook local (o "Porteiro")
WEBHOOK_URL = "http://127.0.0.1:8000/api/v1/evolution/webhook"

def enviar_mensagem(telefone: str, texto: str):
    """Simula a Evolution API disparando uma mensagem do WhatsApp para o nosso backend."""
    payload = {
        "data": {
            "key": {"remoteJid": f"{telefone}@s.whatsapp.net"},
            "message": {"conversation": texto}
        }
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(WEBHOOK_URL, data=data, headers={'Content-Type': 'application/json; charset=utf-8'})
    
    print(f"🚀 Enviando de {telefone}: '{texto}'")
    
    try:
        with urllib.request.urlopen(req) as response:
            resposta = json.loads(response.read().decode('utf-8'))
            print(f"✅ Resposta da API: {resposta}\n")
    except urllib.error.URLError as e:
        print(f"❌ Erro ao conectar com a API (O Uvicorn está rodando?): {e}\n")

if __name__ == "__main__":
    print("="*60)
    print("🤖 SIMULADOR DA EVOLUTION API - ORÁCULO UEMA")
    print("="*60)
    
    # ---------------------------------------------------------
    # TESTE 1: O VISITANTE DESCONHECIDO
    # ---------------------------------------------------------
    # OBJETIVO: Testar se o banco de dados barra números não cadastrados.
    # RESULTADO ESPERADO: O webhook deve devolver 'onboarding_started' para iniciar o cadastro.
    print("\n[ TESTE 1: Visitante sem cadastro (GUEST) ]")
    enviar_mensagem("5598999999999", "Olá, como faço matrícula na UEMA?")
    time.sleep(1) # Pausa rápida para facilitar a leitura no terminal


    # ---------------------------------------------------------
    # TESTE 2: O ALUNO VIP (ACESSO LIBERADO)
    # ---------------------------------------------------------
    # OBJETIVO: Testar se o número mockado passa pela validação do Pydantic.
    # RESULTADO ESPERADO: O webhook deve processar e enviar a mensagem para o LangGraph no background.
    print("\n[ TESTE 2: Aluno VIP Cadastrado ]")
    telefone_vip = "5598777777777" 
    
    # Esta mensagem entra! O LangGraph (que demora 4s no mock) começará a "pensar".
    enviar_mensagem(telefone_vip, "Quais são as regras do Restaurante Universitário (RU)?")
    

    # ---------------------------------------------------------
    # TESTE 3: O TESTE DE SPAM (O LOCK DO REDIS)
    # ---------------------------------------------------------
    # OBJETIVO: Testar o Redis. Se o Aluno VIP mandar mensagem enquanto o bot "pensa", ele deve ser barrado.
    # RESULTADO ESPERADO: O webhook deve devolver 'locked_ignored' para proteger os tokens da IA.
    print("\n[ TESTE 3: Tentativa de Spam (Lock do Redis) ]")
    print("⏳ O aluno mandou outra mensagem ANTES do bot terminar de responder...")
    
    # Esta mensagem bate na porta e encontra o Lock do Redis fechado.
    enviar_mensagem(telefone_vip, "Ei, Oráculo! Responde logo!!")
    
    print("\n🎉 Bateria de testes concluída. Verifique os logs do servidor Uvicorn!")
    print("="*60)