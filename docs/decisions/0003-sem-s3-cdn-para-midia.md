# ADR 0003 — Sem S3/CDN para mídia (envio de vídeo/áudio via base64 direto)

- **Status:** ativo — decisão explícita de escopo, não definitiva
- **Data:** 2026-08-01
- **Fonte:** extraído de `.claude.md` (versão anterior a 2026-08-25) e `notas.md` §10-11

## Contexto

Ao implementar envio de vídeo/áudio do YouTube para o WhatsApp, existiam duas
opções: subir o arquivo para um storage externo (S3/CDN) e mandar a URL, ou
enviar o arquivo direto em base64 pelo mesmo endpoint `sendMedia` da
Evolution API.

## Decisão

**Sem S3/CDN.** Decisão explícita por custo/complexidade não justificados
para um piloto. Implementado `EvolutionAdapter.enviar_midia_base64()` — lê o
arquivo local, converte para base64, envia, e sempre deleta o arquivo local
depois (sucesso ou falha).

## Limites conhecidos e mitigação

- `_MAX_ENVIO_MB = 16` (mais conservador que o cap de download de 50MB do
  `MediaDownloadService`) — WhatsApp/Evolution não é confiável com payload
  base64 grande.
- Fallback: se o vídeo passar do limite de envio, o worker apaga o arquivo de
  vídeo e baixa de novo só o áudio (`audio_only=True`, mp3, geralmente bem
  menor) antes de desistir.

## Consequências

- Implementado só para `ytb_download` (YouTube). `insta_download` (Instagram)
  segue sem esse caminho — seguiria o mesmo padrão se decidido no futuro.
- Se o volume/tamanho de mídia trafegado crescer, esta decisão deve ser
  reaberta (S3/CDN volta a fazer sentido em escala).
