# Transformativa Revenue Engine

Sistema comercial inteligente da Transformativa: identificar, pesquisar, qualificar, priorizar,
abordar e acompanhar potenciais clientes com perfil aderente às ofertas de transformação
operacional (automação de processos + IA).

**Baseline de implementação:** V1.1.0 · **Guia-mestre:** `docs/HERMES_BOOTSTRAP_PROMPT` (pacote original)
· **Release atual:** R1 — Foundation (W0–W3).

## Regra arquitetural principal

> PostgreSQL é o source of truth da inteligência comercial e do histórico.
> Odoo é o source of truth da operação comercial.
> n8n executa e integra. Hermes orquestra.
> JEV classifica e roteia decisões tipadas de baixo custo. LLMs raciocinam, sintetizam e geram.

O Hermes Sales AI **não** tem permissão para alterar código/deploy de produção; o Hermes Dev Harness e o
Hermes Sales AI são logicamente separados.

## Estrutura

```text
/docs      arquitetura, dados, integrações, negócio, testes, operação, ADRs, runbooks, releases, kanban
/db        migrations (imutáveis após aplicadas) e testes
/odoo      addon transformativa_sales_ai
/n8n       workflows e contracts
/hermes    agents, prompts, policies e JEV (routing, benchmarks, receipts)
/scripts   utilitários operacionais
/tests     testes de integração/E2E
```

## Caminho crítico

```text
Governança → PostgreSQL → Odoo → Integração → Research → Scoring → Titan
```

## Branches

`main` (produção) · `develop` (integração) · `feature/TRE-*` · `fix/TRE-*` · `release/x.y.z`.
Todo commit referencia o card: `TRE-W0-E01-T01: <descrição>`.
