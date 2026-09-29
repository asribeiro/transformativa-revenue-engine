# Estratégia de branches — Transformativa Revenue Engine

| Branch | Papel | Regra |
|---|---|---|
| `main` | produção | só entra via `release/x.y.z`; sempre com rollback definido |
| `develop` | integração | recebe `feature/TRE-*` e `fix/TRE-*` |
| `feature/TRE-W{wave}-E{epic}-T{task}` | um card | nasce de `develop`, morre no merge |
| `fix/TRE-*` | correção | card de correção obrigatório |
| `release/x.y.z` | empacotamento | congela versão; gera release artifact |

**Commits:** `TRE-W0-E01-T01: implement idempotent company upsert` (ID do card + descrição no imperativo).
**Release artifact:** commit SHA, digest da imagem, versão de migration, versão do módulo Odoo, versões de
workflow, versões de prompt, test report e alvo de rollback.
**Migrations:** imutáveis — nunca editar uma migration já aplicada em PROD.
