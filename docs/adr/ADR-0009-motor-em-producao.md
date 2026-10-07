# ADR-0009 — Motor de vendas em produção, com campanha subordinada à aprovação humana de texto e lista

**Status:** aceito (baseline V1.2.0 — proposta de nova versão)
**Data:** 07/10/2026
**Versão:** 1.0
**Aprovação humana:** Anderson Ribeiro, Telegram, 07/10/2026 — *"O motor deverá funcionar em produção, vamos alterar o ADR-005 para isso, só que TODA Campanha só nasce após eu aprovar texto e lista de disparo."*

## Contexto

O ADR-0005 (Separação Hermes Dev Harness x Hermes Sales AI) mantém o motor de vendas fora de produção: os
agentes de descoberta, sinais, scores e Next Best Action recusam ambiente vivo, e as bases do motor em
produção estão vazias por desenho (medido em 07/10/2026: `sales_intelligence` de produção com 0 organizações,
0 recomendações e 0 pedidos de aprovação). O efeito prático é que uma campanha de prospecção real não pode
existir: o motor calcularia sobre uma base que não é a do negócio, e o Odoo de registro está em produção.

O dono decidiu que o motor passa a rodar em produção — e, junto com a permissão, fixou a condição que a
torna aceitável para ele.

## Decisão

1. **O motor de vendas pode rodar em produção**, do mesmo jeito que já roda em dev e homolog: as mesmas
   guardas de escrita (schema próprio, `INSERT` nas tabelas do motor, DDL/UPDATE/DELETE de negócio
   recusados), a mesma idempotência e a mesma auditoria em `agent_runs`.
2. **NENHUMA campanha nasce sem aprovação humana expressa do dono sobre DUAS coisas: o TEXTO e a LISTA de
   disparo.** A aprovação é por campanha, registrada em `human_approvals` e no registro de aprovações;
   sem as duas aprovações o motor produz no máximo recomendação interna, nunca material de disparo.
3. **O canal humano continua sendo o único que fala com terceiros.** O LinkedIn segue assistido (a máquina
   rascunha e pede aprovação; o humano publica e envia), sem API do LinkedIn e sem automação de navegador —
   a regra do ADR-0004 e a do dono não mudam com esta decisão.
4. **A credencial de deploy continua proibida para o papel Sales AI.** Esta decisão abre **dados** de
   produção para o motor; não abre deploy, publicação nem infraestrutura.

## Alternativas

- **Manter o motor fora de produção** (estado atual): rejeitada pelo dono — sem isso a campanha não é real,
  porque o Odoo de registro é o de produção.
- **Motor em produção sem aprovação por campanha**: rejeitada — contraria a regra do dono e o ADR-0004
  (primeiro contato outbound exige aprovação expressa).
- **Motor em dev com espelho dos dados de produção**: rejeitada — duplica a base de registro, cria segunda
  verdade e mantém o material de campanha fora do ambiente onde ele é usado.

## Consequências

- **Nova versão do baseline (V1.2.0)** — exigência do próprio ADR-0005 para mudança estrutural.
- O papel do motor ganha escrita nas tabelas de dados de produção (`sales_intelligence.*`) e **não** ganha
  credencial de deploy; a proibição de deploy segue válida e verificada.
- **Rollback:** reverter este ADR e revogar o acesso do motor às tabelas de produção — as bases de dev e
  homolog permanecem intactas, e nenhum dado de negócio é apagado pelo rollback.
- **Regressão a rodar:** as baterias de aceite de cada agente contra o ambiente de produção, com a prova de
  mutação de cada uma; e o guardrail de papel, que passa a medir que o motor escreve em dados e continua
  **incapaz** de deployar.
- O ICP do negócio passa a ser critério medível no contrato do score (ver `docs/business/icp-transformativa-v1.md`,
  homologação do modelo `icp-v1.0.0` que estava `PROPOSTA_A_HOMOLOGAR`).
