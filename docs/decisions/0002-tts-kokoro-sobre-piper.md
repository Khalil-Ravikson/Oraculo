# ADR 0002 — TTS local: Kokoro-82M no lugar de Piper

- **Status:** ativo
- **Data:** 2026-08-12 (Fase 0/pesquisa) → decisão tomada e implementada 2026-08-12/17
- **Fonte:** extraído de `.claude.md` (versão anterior a 2026-08-25) e `notas.md` §11-12

## Contexto

O roadmap multimodal precisava de um provider de Text-to-Speech local (sem
depender de API paga por padrão). Piper (`rhasspy/piper`) era a escolha
inicial da pesquisa de Fase 0.

## Decisão

Piper foi **descartado por licença**: o repositório `rhasspy/piper` (MIT) foi
arquivado em 10/2025; o sucessor `piper-tts` no PyPI é GPL-3.0-or-later a
partir da v1.4.0 — achado confirmado via WebFetch, contradizendo a pesquisa
original de Fase 0. Trocado por **Kokoro-82M (Apache-2.0)**, testado
localmente antes de implementar (síntese pt-BR real, ~2.2s para ~4s de
áudio).

## Implementação

- `KokoroTTSProvider` (`src/infrastructure/adapters/kokoro_tts_provider.py`),
  pipeline lazy-load (~15s na 1ª síntese por processo).
- `settings.TTS_PROVIDER` default `kokoro` (era `gtts`).
- 3 vozes pt-BR disponíveis (`pf_dora` padrão, `pm_alex`, `pm_santa`).
- Não precisa de `torch` extra nem `espeak-ng` via `apt` — `espeakng-loader`
  já embute os binários via pip.
- `KPipeline(lang_code='p')` retorna `torch.Tensor` — precisa `.detach().cpu().numpy()`
  antes de `soundfile.write()`.
- Kokoro gera WAV cru; WhatsApp/Evolution API não entrega áudio `audio/wav`
  de forma confiável — codificação para MP3 via `lameenc` (puro-Python, sem
  precisar de `ffmpeg` via apt) foi necessária para a entrega funcionar de
  verdade (achado real de bug de produção, ver `notas.md` §12).

## Consequências

- `requirements.txt` ganhou `kokoro`, `lameenc` — dependências que também vão
  para a imagem Docker de `main` (o `Dockerfile` instala `requirements.txt`
  por inteiro e baixa o modelo Kokoro em build-time — ver auditoria de
  2026-08-24 para o risco de isso nunca ter sido testado em `docker build`
  real).
- TTS roda no worker `media` (não `default`) — rodar inline no `default`
  causou OOM real em produção durante o carregamento do Kokoro (`torch`
  compartilhando `mem_limit` com Playwright/Chromium do SIGAA).
