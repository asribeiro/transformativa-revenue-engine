# Ambientes e promoção (Dev → Homolog → Produção)

Decisão do dono, 05/10/2026. Este documento é a fonte do mapa **ambiente ↔ branch ↔ promoção**.
Substitui o modelo "candidate por release" que vinha do `financial-dash`, onde a homologação era um
ambiente por release.

## Mapa

| ambiente | branch (GitHub) | diretório na VPS | compose | quem promove | evidência exigida |
|---|---|---|---|---|---|
| **Dev** | `develop` | `/opt/tre/dev` | `deploy/compose/dev/` | Hermes (é onde o card é executado) | critérios de aceite do próprio card |
| **Homolog** | `homolog` | `/opt/tre/homolog` | `deploy/compose/homolog/` | **Hermes promove sozinho** | AC do card cumpridos + **evidência crua anexada no card do kanban** |
| **Produção** | `main` | `/opt/tre/prod` | `deploy/compose/prod/` | **Hermes prepara e envia para aprovação humana**; só depois do "aprovado" registrado é que promove | AC cumpridos **em Homolog** + evidências anexadas no card + registro de aprovação (quem, quando, o quê) |

O fluxo em uma frase: **o card nasce e é validado em Dev; Hermes sobe para Homolog com a evidência no card;
Homolog é revalidado e, só então, Hermes pede a aprovação humana para o `main`.**

## Fluxo detalhado

1. **Dev** — o motor despacha o card; a execução roda em Dev (`develop`). O fechamento exige os critérios de
   aceite do card **e a evidência crua anexada** (`kanban_complete(artifacts=[...])` — todos os paths têm de
   existir, 1 fantasma zera a lista inteira).
2. **Promoção Dev → Homolog (Hermes, sem humano)** — permitida **mediante evidência anexada no card** de que
   os AC foram cumpridos. Passos: merge `develop` → `homolog`, deploy da stack Homolog, e **registro do
   commit promovido + a evidência no card**. Se a evidência não estiver no card, a promoção não acontece.
3. **Revalidação em Homolog** — os AC são medidos **no ambiente Homolog** (não basta ter passado em Dev:
   ambiente diferente, dado diferente, segredo diferente). A evidência dessa passada também vai para o card.
4. **Homolog → Produção (exige humano)** — Hermes monta o pedido de aprovação com: o que muda (commits), o
   que foi medido em Homolog (evidência), o risco e o alvo de rollback; envia ao dono; **espera**. A
   aprovação é registrada em `docs/operations/registro-de-aprovacoes.md` (quem, quando, o que, evidência) e no
   artefato de entrega. Só com esse registro Hermes faz o merge `homolog` → `main` e o deploy em Produção.
5. **Produção** — o que está em `main` **e** implantado na stack de Produção.

## Invariantes (o que o desenho proíbe)

- **Ninguém escreve direto em `main`**: só entra por merge vindo de `homolog`. Push direto e merge que não
  passou por Homolog são violação, não atalho.
- **`homolog` só recebe merge vindo de `develop`.**
- **Promover não é aprovar.** A promoção para Homolog é automática (com evidência); a passagem para Produção
  é sempre humana — é o que a política `hermes/policies/human-approval.yaml` já exige
  (`promocao de release para producao` e `rollback em producao` estão em `exige_aprovacao`).
- **Rollback é por ambiente.** Rollback em Produção exige aprovação humana; em Homolog/Dev não.
- **Identidade por ambiente**: cada stack carrega o SHA do commit implantado e o nome do ambiente. "Produção
  no ar" é uma afirmação que se prova por ancestralidade (`git merge-base --is-ancestor`) + rótulo do
  container, nunca por "eu empurrei".
- **Segredo não entra no repo**: no repositório ficam só os pares não-secretos
  (`deploy/environments/<ambiente>-*.env`); os segredos vivem em `/etc/tre/<serviço>-<ambiente>/` na VPS,
  modo 600 (`docs/operations/gestao-de-secrets.md`).
- **Backup por ambiente** (`/opt/tre/<ambiente>/backups`), com o vigia externo já existente.

## O que já existe (medido em 05/10/2026)

- **Dev**: provisionado e no ar. Containers `proxy-dev` (Caddy), `odoo-dev`, `pg-odoo-dev`, `pg-sales-dev`,
  `pg-wa-probe`; rede `tre-odoo-dev`; volumes `odoo-data-dev`, `pgdata-odoo-dev`, `pgdata-sales-dev`,
  `proxy-*-dev`. Compose em `/opt/tre/dev/compose/` (cópia de `deploy/compose/dev/`).
- **Convenção de provisionamento já estabelecida** (replicar para os outros dois ambientes):
  `deploy/compose/<ambiente>/`, `deploy/environments/<ambiente>-*.env`,
  `scripts/provision/*-<ambiente>.sh` (`instalar-*`, `verificar-*`, `remover-*`), runbooks em
  `docs/runbooks/` (ex.: `odoo-dev-tls.md`, `provisionamento-contabo.md`).
- **Homolog e Produção**: existem apenas como esqueleto na VPS — `/opt/tre/homolog` e `/opt/tre/prod` têm
  6 diretórios e **zero arquivos** (`compose`, `odoo`, `pg`, `n8n`, `backups`). Nenhum container de
  Homolog/Produção existe, nem parado.
- **Branches no GitHub**: `develop`, `main` e dezenas de `feature/*` e `fix/*`. **Não existe `homolog`.**
- **`main` está 222 commits atrás de `develop`** e o repositório **não tem nenhuma tag**.
- **Sem CI** (`.github/workflows` não existe) e sem proteção de branch: hoje nada impede um push direto no
  `main`.

## Decisões tomadas

- **D1 · Unidade de promoção (05/10/2026) — bloco de cards com AC fechados.** Um release é um **conjunto
  declarado de cards** com critérios de aceite fechados; a versão segue a data (`AAAA.MM.N`) e é marcada com
  **tag no `main`**. Descartadas: **por onda** (W0 = 38 cards, W3 = 64 commits — grande demais para revalidar
  em Homolog, e rollback grosseiro) e **por card** (dissolve a noção de versão: como `homolog` muda antes de o
  humano aprovar, o "aprovado" ficaria ambíguo; e exigiria suíte amarrada a cada merge, que não existe sem CI).
  Consequência de desenho: existe **um pacote de evidência por release**, montado das evidências anexadas nos
  cards que entram nele.

- **D2 · Tamanho do primeiro release (05/10/2026) — fatia de valor ponta a ponta, pequena.** O primeiro
  release cobre o **caminho crítico** (base: proxy/TLS + Odoo + Postgres, mais um funil real de captura de
  lead → CRM) e existe para **provar o processo com carga leve** (merge em `homolog` → deploy → revalidação →
  pedido de aprovação → registro), não para entregar valor. Descartadas: **W0–W2** (primeiro release já com
  carga média: se falhar, o diagnóstico é caro justo na primeira volta) e **W0–W9 completo** (revalidar 9
  ondas não cabe em janela e a aprovação humana viraria carimbo de fé sobre ~170 evidências).
- **D3 · Domínios por ambiente (05/10/2026) — `tre.transformativa.com.br` (canônico, Produção),
  `homolog.tre.transformativa.com.br` e `dev.tre.transformativa.com.br`.** Espelha a convenção da casa
  (`candidate.finance.transformativa.com.br` = ambiente como subdomínio do produto). Os três stacks convivem
  no IP da VPS (`169.58.24.102`) e o roteamento é por hostname, com certificado por nome. `dev.` e `homolog.`
  ficam atrás de **basic auth**; o canônico é público (é ele que atende o webhook dos canais, que exige HTTPS
  com domínio válido). Descartadas: prefixo no primeiro nível (`tre-dev.…`), domínio próprio do produto (marca
  separada — volta à mesa se houver SaaS com marca própria) e IP:porta (bloquearia o webhook). DNS vive no
  **Netlify**; os três registros `A → 169.58.24.102` **ainda não existem**.

## Decisões que ainda faltam (para o desenho virar operação)

1. **Enforcement.** Proteger `homolog` e `main` no GitHub (PR obrigatório, sem push direto) e registrar o
   mapa ambiente↔branch↔aprovação na política (`hermes/policies/human-approval.yaml`) para o portão ser
   verificável por máquina, não por disciplina. **Alterar a política é decisão do dono.**
2. **Escopo de serviços por ambiente.** Odoo + Postgres + n8n + proxy nos três? A VPS tem 193 GB de disco
   (12 GB em uso) e 11 GB de RAM (≈9 GB disponíveis): cabe, mas convém fixar limites de memória por stack.


## Pendências de forma (não bloqueiam o desenho)

- `homolog` precisa ser criado a partir de `develop` (`main` deve continuar sendo o ramo de Produção).
- Ao provisionar Homolog/Produção: mesmos três scripts por ambiente (`instalar`/`verificar`/`remover`) e o
  mesmo runbook de TLS — a assimetria entre ambientes é o que produz "funciona em Dev e quebra em Homolog".
