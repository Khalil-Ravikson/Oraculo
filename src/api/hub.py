# src/api/hub.py — versão limpa (sem HTML inline)
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.infrastructure.settings import settings

router     = APIRouter()
templates  = Jinja2Templates(directory="templates")


def _verificar_admin(request: Request) -> bool:
    """Verifica sessão do admin via cookie."""
    token = request.cookies.get("admin_session")
    return token == settings.ADMIN_API_KEY


@router.get("/", response_class=HTMLResponse)
async def hub_index(request: Request):
    if not _verificar_admin(request):
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request}
        )
    return templates.TemplateResponse(
        "hub/index.html",
        {
            "request": request,
            "modelo":  settings.GEMINI_MODEL,
            "dev_mode": settings.DEV_MODE,
        }
    )


@router.post("/admin/login")
async def admin_login(request: Request):
    from fastapi.responses import RedirectResponse
    form = await request.form()
    if form.get("password") == settings.ADMIN_API_KEY:
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie("admin_session", settings.ADMIN_API_KEY, httponly=True)
        return resp
    return templates.TemplateResponse(
        "admin/login.html",
        {"request": request, "erro": "Senha incorreta"}
    )
