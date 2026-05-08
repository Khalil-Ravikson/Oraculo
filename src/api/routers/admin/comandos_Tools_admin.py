"""
api/comandos_Tools_admin.py — Sandbox e Testes de Tools da LLM
======================================================================
Área isolada para testar comandos da LLM e simular a execução de tools
como envio de e-mails e cadastro de usuários convidados (guests).
"""
import os
import logging
from typing import Any, Dict
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr

# Importando a métrica que criamos anteriormente para o Prometheus/Grafana
from src.api.routers.admin.comandos_Tools_admin import TOOL_CALL_COUNT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/llm-tools", tags=["LLM Sandbox"])

# Configuração de templates (aponta para a pasta onde o HTML será salvo)
templates = Jinja2Templates(directory="templates")

# ─────────────────────────────────────────────────────────────────────────────
# Autenticação (Mantida do portal original)
# ─────────────────────────────────────────────────────────────────────────────
def _get_admin_key() -> str:
    try:
        from src.infrastructure.settings import settings
        return settings.ADMIN_API_KEY or ""
    except Exception:
        return os.environ.get("ADMIN_API_KEY", "")

def require_admin(x_admin_key: str = Header(None, alias="X-Admin-Key")):
    key = _get_admin_key()
    if not key:
        raise HTTPException(status_code=503, detail="ADMIN_API_KEY não configurada no .env")
    if x_admin_key != key:
        raise HTTPException(status_code=401, detail="Chave inválida")
    return True

@router.post("/auth")
async def auth_check(request: Request):
    """Valida a chave admin enviada pelo browser."""
    try:
        body = await request.json()
        key  = body.get("key", "")
    except Exception:
        raise HTTPException(400, "Corpo JSON inválido")

    expected = _get_admin_key()
    if not expected:
        raise HTTPException(503, "ADMIN_API_KEY não configurada")
    if key != expected:
        raise HTTPException(401, "Chave incorreta")

    return {"ok": True, "msg": "Autenticado com sucesso"}

# ─────────────────────────────────────────────────────────────────────────────
# Schemas e Implementação das Tools
# ─────────────────────────────────────────────────────────────────────────────
class EmailToolSchema(BaseModel):
    destinatario: EmailStr
    assunto: str
    corpo: str

class GuestUserSchema(BaseModel):
    nome_completo: str
    email: EmailStr
    telefone: str
    motivo_visita: str

def tool_enviar_email(data: EmailToolSchema) -> Dict[str, Any]:
    # Mock do envio de email (aqui você chamaria seu email_service.py)
    logger.info(f"Simulando envio de e-mail para {data.destinatario}")
    return {
        "status": "success",
        "message": f"E-mail enviado com sucesso para {data.destinatario}",
        "detalhes": {"assunto": data.assunto, "tamanho_corpo": len(data.corpo)}
    }

def tool_cadastrar_guest(data: GuestUserSchema) -> Dict[str, Any]:
    # Mock do cadastro de usuário guest
    logger.info(f"Simulando cadastro de guest: {data.nome_completo}")
    return {
        "status": "success",
        "message": f"Usuário {data.nome_completo} cadastrado como Guest.",
        "guest_id": "GST-2026-001",
        "credenciais_temporarias": "Enviadas via WhatsApp/Email"
    }

# ─────────────────────────────────────────────────────────────────────────────
# Endpoints da Sandbox
# ─────────────────────────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def admin_sandbox_portal(request: Request):
    """Serve a interface HTML do Sandbox."""
    return templates.TemplateResponse("admin/test_area.html", {"request": request})

@router.post("/execute")
async def execute_tool(
    request: Request,
    x_admin_key: str = Header(None, alias="X-Admin-Key")
):
    """Executa a tool simulando o output da LLM e registra métricas no Grafana."""
    require_admin(x_admin_key)
    
    try:
        body = await request.json()
        tool_name = body.get("tool_name")
        tool_data = body.get("tool_data", {})
        
        resultado = {}
        
        if tool_name == "enviar_email":
            val_data = EmailToolSchema(**tool_data)
            resultado = tool_enviar_email(val_data)
        elif tool_name == "cadastrar_guest":
            val_data = GuestUserSchema(**tool_data)
            resultado = tool_cadastrar_guest(val_data)
        else:
            raise HTTPException(400, "Tool desconhecida.")

        # Sucesso: Exportar métrica para o Prometheus
        TOOL_CALL_COUNT.labels(
            department="admin", team_id="sandbox", user_email="admin", 
            service_name="oraculo", function_name=tool_name, success="true", decision="manual_test"
        ).inc()

        return {"ok": True, "resultado": resultado}

    except Exception as e:
        # Falha: Exportar métrica de erro para o Prometheus
        tool_name_err = body.get("tool_name", "unknown") if 'body' in locals() else "unknown"
        TOOL_CALL_COUNT.labels(
            department="admin", team_id="sandbox", user_email="admin", 
            service_name="oraculo", function_name=tool_name_err, success="false", decision="manual_test"
        ).inc()
        
        raise HTTPException(status_code=422, detail=f"Erro de validação ou execução: {str(e)}")
    
