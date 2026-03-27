from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# O nosso novo Webhook!
from src.api.webhook import router as webhook_router

# Se você já tiver os routers de admin ou hub, pode importar aqui também:
# from src.api.hub import router as hub_router

app = FastAPI(
    title="Oráculo UEMA API",
    description="Backend de Orquestração Híbrida do HelpDesk",
    version="2.0.0"
)

# 1. Monta os arquivos estáticos (CSS, JS) da pasta que você criou
app.mount("/static", StaticFiles(directory="static"), name="static")

# (Opcional) Instância do Jinja2 para quando formos configurar as rotas HTML
templates = Jinja2Templates(directory="templates")

# 2. Registra a nossa rota de webhook do WhatsApp
app.include_router(webhook_router, prefix="/api/v1", tags=["Webhook"])

# (Opcional) Incluir os outros routers do Hub depois
# app.include_router(hub_router, prefix="/hub", tags=["Hub"])

@app.get("/health")
async def health_check():
    """Endpoint para monitoramento de saúde do contêiner."""
    return {"status": "online", "system": "Oráculo UEMA"}