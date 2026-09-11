# Roadmap 2026–2027 — arquivo morto

> ⛔ Nada aqui é fonte de verdade. Arquivado em 2026-09-09 (item A5 do plano
> de recuperação). O que o sistema é hoje está em
> [`docs/ESTADO_ATUAL.md`](../../ESTADO_ATUAL.md).

Estes nove arquivos viviam na **raiz de `docs/`** e eram, juntos, a maior
fonte da perda de rastreabilidade do projeto. O problema não era estarem
errados quando foram escritos — era continuarem no lugar de destaque depois
de a realidade passar por cima deles.

## O que eles diziam, e o que aconteceu

| Diziam | Realidade |
|---|---|
| "Camada 1 (BaseNode + NodeRegistry): decisão pendente" | Foi decidida **sim** e implementada, em `src/graph_studio/` — que acabou sem consumidor de produção e hoje está **congelado** (TD-020). |
| "O Hub vai virar o Graph Studio" | Virou, na ADR 0007/0008. A aba "Grafo de produção" edita a `GraphSpec` real. |
| `INDEX_ROADMAP_2026.md`: "ponto único de verdade" | Conflitava direto com o `.claude.md`, que aponta para outras fontes. Dois documentos alegando exclusividade é o mesmo que nenhum. |
| "Fases 6–11 pendentes" | O plano de fases foi substituído pela decisão de produto do v1: **um bot de menu**, com wiki da CTIC e contatos, e o resto desligado. |

## Por que não foram deletados

Registram o raciocínio de agosto de 2026 e as razões pelas quais certas
escolhas pareciam certas na época. Isso tem valor para entender como o
projeto chegou aqui. O que não podiam continuar é competindo por atenção com
a documentação viva.

## Regra

A raiz de `docs/` fica com **no máximo um roadmap vivo**. Documento de plano
que deixou de valer vem para cá com banner, no mesmo dia em que deixa de
valer.
