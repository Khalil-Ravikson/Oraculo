# tests/simulate_webhook.py

# lembrar de uvicorn src.main:app --reload


import json
import urllib.request
import urllib.error

# URL do nosso webhook local
WEBHOOK_URL = "http://127.0.0.1:8000/api/v1/evolution/webhook"

def enviar_mensagem(telefone: str, texto: str):
    """Monta o payload da Evolution API e envia para o Oráculo."""
    payload = {
        "data": {
            "key": {"remoteJid": f"{telefone}@s.whatsapp.net"},
            "message": {"conversation": texto}
        }
    }
    
    # Converte o dicionário para JSON em formato de bytes (UTF-8)
    data = json.dumps(payload).encode('utf-8')
    
    # Prepara a requisição HTTP
    req = urllib.request.Request(
        WEBHOOK_URL, 
        data=data, 
        headers={'Content-Type': 'application/json; charset=utf-8'}
    )
    
    print(f"🚀 Enviando de {telefone}: '{texto}'")
    
    try:
        with urllib.request.urlopen(req) as response:
            resposta = json.loads(response.read().decode('utf-8'))
            print(f"✅ Resposta da API: {resposta}\n")
    except urllib.error.URLError as e:
        print(f"❌ Erro ao conectar com a API: {e.reason}\n(O servidor Uvicorn está rodando?)\n")

if __name__ == "__main__":
    print("="*50)
    print("🤖 SIMULADOR DA EVOLUTION API - ORÁCULO UEMA")
    print("="*50)
    
    # Teste 1: Um usuário comum mandando mensagem com acento
    print("\n--- TESTE 1: Usuário Novo (Guest) ---")
    enviar_mensagem("5598999999999", "Olá, Oráculo! Como faço minha matrícula?")

    # Teste 2: Testando a Trava do Redis (Lock Antispam)
    print("\n--- TESTE 2: Simulação de Spam (Testando o Redis) ---")
    telefone_spammer = "5598888888888"
    enviar_mensagem(telefone_spammer, "Primeira mensagem!")
    
    # Mandamos a segunda mensagem instantaneamente (o Redis deve bloquear)
    print("⏳ Mandando outra mensagem logo em seguida para o mesmo número...")
    enviar_mensagem(telefone_spammer, "Oi? Alguém aí?")