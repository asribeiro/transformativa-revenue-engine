# Convenções de nomenclatura — TRE

Decisões do Anderson (29/09/2026). Onde uma convenção nascer, ela vive aqui — não em conversa.

## n8n

Recursos do TRE no n8n (workflows, credenciais, tags, pastas) começam com **`TRE`**, para não se misturar
com a instância que hoje roda o negócio atual (`n8n.transformativa.com.br`, ~7 workflows, 5.480 linhas JS
em nós Code). Exemplos: `TRE — ingestão ECD`, `TRE — outbox consumer`.

Motivo: uma instância só serve dois donos; sem prefixo, a separação existe apenas na cabeça de quem escreveu.

## Board e cards

- Card: `TRE-W<onda>-E<épico>-T<task>` (ex.: `TRE-W1-E01-T01`).
- Card de defeito: `<código do card que originou>-D<nn>` (ex.: `TRE-W0-E04-T02-D01`), com `DEFEITO` no título.
- Perfil responsável pela execução vai no corpo do card, nunca deduzido.

## Ambientes

`/opt/tre/{{dev,homolog,prod}}` — nunca um caminho sem ambiente declarado (ADR-005: nenhuma DDL nasce em produção).
