# Hermes Dev Harness × Hermes Sales AI — separação de papéis

**Card:** TRE-W0-E02-T01 · **Base:** 13_HERMES_IMPLEMENTATION_BRIEF e 12_CONTRATOS_E_GOVERNANCA

## Regra que manda

> O **Hermes Sales AI não tem permissão para alterar código nem fazer deploy de produção.**

Os dois papéis são separados **logicamente** (políticas distintas, credenciais distintas, registro
distinto) e a verificação é automatizada: `bash scripts/verificar_papeis.sh` reprova se um papel carregar
credencial ou permissão do outro.

## Matriz de permissões

| Capacidade | Dev Harness | Sales AI |
|---|---|---|
| Escrever código / testes / migrations | **sim** | não |
| Deploy e promoção de release | **sim** (com Human Approval) | **não** |
| Rollback de produção | **sim** (com Human Approval) | **não** |
| DDL / migration em qualquer ambiente | **sim** (dev/homolog) | **não** |
| Ler/escrever dados de negócio no PG | sim | **sim** (sem DDL) |
| Publicar/alterar workflow no n8n | **sim** (dev) | **não** |
| Disparar webhook aprovado | sim | **sim** |
| Operar lead/atividade/proposta no Odoo | sim | **sim** (API de negócio) |
| Administrar o módulo Odoo | **sim** | **não** |
| E-mail Titan (outbound) | não | **sim** (com Human Approval) |
| Contatar lead / cliente | não | **sim** (com Human Approval) |
| Conceder aprovação humana | não | **não** (só o Anderson) |
| Credencial de infraestrutura/host | via operador humano | **não** |

## Como a separação é imposta

1. **Políticas versionadas** em `hermes/policies/` (`dev-harness.yaml`, `sales-ai.yaml`,
   `human-approval.yaml`): permissões, proibições e credenciais permitidas/proibidas por papel.
2. **Credenciais segregadas** conforme `docs/operations/gestao-de-secrets.md`: o Sales AI não recebe
   `GITHUB_TOKEN`, chave de publicação do n8n nem credencial de host.
3. **Verificação automatizada** (`scripts/verificar_papeis.sh`): confere os invariantes e reprova se o
   Sales AI ganhar capacidade de deploy/código/DDL ou se os papéis colidirem em credencial.
4. **Runtime**: quando a instância de produção do Sales AI subir, ela roda com apenas as credenciais do
   próprio papel; até então o papéis são exercidos pelo mesmo processo com políticas distintas e
   verificação — declarado no card para não parecer mais do que é.

## Homologação

- **Quem aprovou:** Anderson Ribeiro (operador humano).
- **Quando:** 29/09/2026, pelo canal do Hermes (Telegram).
- **O que foi aprovado:** a matriz de permissões dos dois papéis (Dev Harness × Sales AI) e a regra de que
  **infraestrutura do host não pertence a nenhum dos papéis** e **conceder aprovação humana é só do
  Anderson**.
- **Ressalva mantida:** a separação é por política versionada + verificação automatizada; a separação de
  *runtime* (processo dedicado) entra quando a instância de produção do Sales AI subir.
- **Evidência:** comentário nos cards `TRE-W0-E02-T01` e `TRE-W0-E01-T02`; registro em
  `docs/operations/registro-de-aprovacoes.md`.
