"""
CalendarLLMAdapter — Parser especializado para o Calendário Acadêmico.
Utiliza PyMuPDF para ler o PDF bruto e Gemini 2.0 Flash Lite para
estruturar a tabela do calendário num formato otimizado para o RAG.

Formato de saída gerado:
EVENTO: <Nome do Evento> | DATA: <Data(s)> | SEM: <Semestre>
"""
from __future__ import annotations
import logging
import os
import fitz  # PyMuPDF
from google import genai
from google.genai import types
from src.domain.ports.document_parser import IDocumentParser
from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

class CalendarLLMAdapter(IDocumentParser):
    def __init__(self):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model = "gemini-2.5-flash"

    def parse(self, file_path: str, instruction: str = "") -> str:
        if not os.path.exists(file_path):
            logger.error("❌ Arquivo não encontrado: %s", file_path)
            return ""

        try:
            doc = fitz.open(file_path)
            raw_text = ""
            for page in doc:
                raw_text += page.get_text("text") + "\n\n"
            doc.close()

            if not raw_text.strip():
                logger.warning("⚠️  Nenhum texto extraível em '%s'", file_path)
                return ""

            prompt = f"""
Você é um extrator de dados altamente preciso.
Abaixo está o texto bruto extraído de um PDF de um Calendário Acadêmico (as colunas podem estar desordenadas ou os números soltos).
Sua tarefa é reconstruir os eventos acadêmicos linha por linha EXATAMENTE no seguinte formato:

EVENTO: <Nome do Evento> | DATA: <DD/MM/YYYY a DD/MM/YYYY> | MES: <Nome do Mês por Extenso e Ano> | SEM: <Semestre>

Regras:
1. Extraia APENAS eventos válidos com datas legíveis.
2. Não inclua texto extra, cabeçalhos ou explicações. APENAS as linhas no formato especificado.
3. Se um evento for apenas em um dia, a data deve ser DD/MM/YYYY. Se for um período, use DD/MM/YYYY a DD/MM/YYYY.
4. Adicione a tag MES: contendo o nome do mês por extenso e o ano correspondente à data (Ex: MES: Janeiro de 2026). Se for um período pegando dois meses, inclua ambos (Ex: MES: Fevereiro e Março de 2026).
5. Se o semestre não for evidente, deixe SEM: 2026.1 ou omita a tag SEM.

Texto Bruto:
{raw_text[:30000]}  # Limite seguro de tamanho
"""

            logger.info("🤖 Iniciando estruturação LLM do Calendário Acadêmico...")
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0
                )
            )

            structured_text = response.text.strip()
            logger.debug("📄 Calendar LLM gerou %d caracteres estruturados.", len(structured_text))
            
            return structured_text

        except Exception as e:
            logger.exception("❌ CalendarLLMAdapter falhou para '%s': %s", file_path, e)
            return ""
