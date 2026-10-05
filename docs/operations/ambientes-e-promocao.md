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

- **D4 · Enforcement (05/10/2026) — defesa em profundidade: proteção de branch **e** gate no motor.** `main`
  e `homolog` só recebem **merge** (push direto é violação) e o motor do board **recusa promover** card sem
  critérios de aceite fechados e evidência anexada, registrando o sha promovido; um vigia confere depois que
  `main` só avançou por merge vindo de `homolog` e alerta se não. Cada mecanismo cobre o que o outro não vê:
  a plataforma impede o push direto (inclusive de um agente desatualizado), o motor verifica a evidência/AC do
  card — que a proteção de branch não sabe enxergar (ela só vê "tem PR?").
  **Condição medida:** a proteção de branch exige **GitHub Pro** — o plano atual responde `403: "Upgrade to
  GitHub Pro or make this repository public to enable this feature."` (repo é **privado**; torná-lo público não
  é opção). A assinatura é do dono; o token do agente já tem `admin` no repo, então a configuração é mecânica
  assim que o upgrade existir. Até lá, vale **só** o gate do motor + vigia.
  Descartadas: só plataforma (não vê evidência) e só disciplina (com repo privado e sem proteção, "sem push
  direto" vira convenção).

- **D5 · Escopo de serviços por ambiente (05/10/2026) — o MESMO conjunto nos três** (proxy + Odoo +
  Postgres + n8n), com **limite de memória por stack** e `n8n` sem workers no Dev. Razão: Homolog existe para
  detectar a diferença entre ambientes; conjunto diferente homologa só o que alguém lembrou de replicar.
  Descartadas: ambientes enxutos (quebraria a paridade justamente no n8n, o serviço mais provável de quebrar
  em Produção) e serviços de apoio compartilhados (um Postgres para três: economiza pouco e acopla — um
  restart derruba os três, e um teste pesado em Dev degrada Produção).

## Plano de execução (em ordem)

Dependências do dono marcadas com **[DONO]**. Nada aqui autoriza release: promover para `main` continua
exigindo aprovação humana registrada.

1. **Branch `homolog`** — criar a partir de `develop` (hoje não existe). Sem ele, nada promove.
2. **[DONO] Registros DNS** — `tre`, `dev.tre` e `homolog.tre` → `169.58.24.102` no painel do Netlify (ou
   token do Netlify para o agente criar). Sem isso, Homolog/Produção só sobem com CA local e o webhook dos
   canais não fecha.
3. **Provisionar Homolog** — `deploy/compose/homolog/`, `deploy/environments/homolog-*.env`,
   `scripts/provision/*-homolog.sh` (espelhando o que já existe para dev), `mem_limit` por serviço, `n8n` sem
   workers, segredos em `/etc/tre/<serviço>-homolog/` (600). Fecha com a sonda de TLS e um E2E do funil **em
   Homolog**.
4. **Provisionar Produção** — mesmo formato em `deploy/compose/prod/`, mesmos limites.
5. **Gate no motor + vigia de auditoria** (parte B de D4) — o motor passa a recusar promoção de card sem AC
   fechados e evidência anexada, registrando o sha; o vigia confere que `main` só avançou por merge vindo de
   `homolog`. É script que roda a cada 5 min: entra com dry-run e janela declarada, nunca às cegas.
6. **[DONO] GitHub Pro** (parte A de D4) — assinatura. Com o upgrade feito, os rulesets de `homolog` e `main`
   são configurados por API (o token do agente já tem `admin` no repo).
7. **Política** — registrar o mapa ambiente↔branch↔aprovação em `hermes/policies/human-approval.yaml`:
   promoção Dev→Homolog não exige humano; `homolog`→`main` exige; rollback em Produção exige.
8. **Primeiro release** (D2) — declarar o bloco de cards do caminho crítico; promover para Homolog com a
   evidência anexada nos cards; revalidar em Homolog; montar o pedido de aprovação humana; aprovado, promover
   para `main` com **tag de versão por data**.

## Perguntas em aberto do desenho (não bloqueiam os passos 1, 3 e 5)

- Quem cria os **registros DNS** (passo 2): o dono no painel, ou token do Netlify para o agente.
- O **upgrade para GitHub Pro** (passo 6) entra agora ou depois do primeiro release?


## Pendências de forma (não bloqueiam o desenho)

- `homolog` precisa ser criado a partir de `develop` (`main` deve continuar sendo o ramo de Produção).
- Ao provisionar Homolog/Produção: mesmos três scripts por ambiente (`instalar`/`verificar`/`remover`) e o
  mesmo runbook de TLS — a assimetria entre ambientes é o que produz "funciona em Dev e quebra em Homolog".
