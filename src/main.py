from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# 1. Importação dos Roteadores (Certifique-se que os arquivos existem em src/api/)
from src.api.webhook import router as webhook_router
from src.api.admin_portal import router as admin_router
from src.api.hub import router as hub_router
from src.api.monitor import router as monitor_router

app = FastAPI(
    title="Oráculo UEMA API",
    description="Backend de Orquestração Híbrida do HelpDesk",
    version="2.0.0"
)

# 2. Configuração de Arquivos Estáticos e Templates
# Verifique se as pastas 'static' e 'templates' estão na raiz do projeto Oraculo/
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# 3. Registro das Rotas (A fiação que estava faltando)
# Webhook do WhatsApp/Evolution
app.include_router(webhook_router, prefix="/api/v1", tags=["Webhook"])

# Portais HTML (Onde estavam dando os 404s)
app.include_router(admin_router, prefix="/admin", tags=["Admin"])
app.include_router(hub_router, prefix="/hub", tags=["Hub"])
app.include_router(monitor_router, prefix="/monitor", tags=["Monitor"])

@app.get("/health")
async def health_check():
    """Endpoint para monitoramento de saúde do contêiner."""
    return {"status": "online", "system": "Oráculo UEMA"}