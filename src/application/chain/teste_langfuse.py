import os
import logging
import importlib.metadata
from langfuse import Langfuse

# Ativa os logs pra gente ver o que tá rolando por debaixo dos panos
logging.basicConfig(level=logging.DEBUG)

print("="*50)
print("🔍 TESTE DE CONEXÃO LANGFUSE")
print("="*50)

# Verifica a versão instalada
try:
    versao = importlib.metadata.version('langfuse')
    print(f"📦 Versão da biblioteca langfuse: {versao}")
except Exception:
    print("📦 Versão da biblioteca langfuse: Desconhecida")

print(f"🌐 Host configurado: {os.environ.get('LANGFUSE_HOST')}")
print("-" * 50)

try:
    # Inicializa o cliente
    lf = Langfuse()
    
    # Tenta enviar um rastro de teste
    lf.trace(name='teste_script_python')
    lf.flush()
    
    print("\n✅ SUCESSO! Teste concluído. Olhe o painel web!")

except AttributeError as e:
    print(f"\n❌ ERRO DE VERSÃO: {e}")
    print("Isso prova que o SDK instalado no container é velho demais.")
    print("Rode este comando no terminal para atualizar:")
    print("docker compose exec api pip install --upgrade langfuse")
    
except Exception as e:
    print(f"\n❌ ERRO DE CONEXÃO: {e}")