# Registro de promoções entre ambientes

**Promover ≠ aprovar.** A promoção `develop` → `homolog` é ato do Hermes **mediante evidência** (desenho em
`docs/operations/ambientes-e-promocao.md`); a passagem `homolog` → `main` exige **aprovação humana** e vive em
`registro-de-aprovacoes.md`. Nenhum valor de credencial entra aqui, nunca.

Regra de evidência (a mesma do registro de aprovações, 30/09/2026): a linha cita **identidade imutável** — o
commit, o sha256 dos artefatos **naquele commit** e a leitura do verificador **datada**. Contagem solta
envelhece no commit seguinte e vira afirmação falsa.

| Data | De → Para | Commit | O que muda | Evidência | Quem |
|---|---|---|---|---|---|
| 06/10/2026 | `develop` → `homolog` (**primeira promoção**) | `fbf5bb152403bf62645ad4911b5127578573cc23` (fast-forward de `3252bba`; `homolog` remoto e cópia publicada no mesmo commit) | **13 arquivos, +921/-1 — nenhum arquivo de APLICAÇÃO** (`odoo/`, `db/`, `control-plane/`, `n8n/` intocados; conferido por `git diff --name-only homolog develop` filtrando esses prefixos = vazio). Conteúdo: proxy de borda (Caddyfile + compose + par + runbook), stack de Homolog (compose + par + instalador + verificador + runbook), gancho `pre-push`, registro do desenho de ambientes | Verificador `scripts/provision/verificar-odoo-homolog.sh` **no commit `fbf5bb152403bf62645ad4911b5127578573cc23`** (sha256 `a98d554248fd8107b4832a1cf276dcb42a50e70572ba0da988f30ce56de75012`): **15 itens, 0 falhas**, rodado **depois** da troca da cópia publicada. Cópia de `/opt/tre/homolog/repo`: `.publicado` registra `commit: fbf5bb152403bf62645ad4911b5127578573cc23`, digest `2208a0288baaa5b55b09bd3d776120aef19ef9c7103cd9774530d31d20ff31b0`, 788 arquivos, trava `chattr +i` armada. Nada reiniciado no serviço; o addons montado (`/mnt/extra-addons`) seguiu visível ao container depois do `rsync --delete`. **Divergência declarada:** o campo `ref:` do `.publicado` saiu `develop` (o `git name-rev` escolhe o nome mais curto e os dois branches apontavam para o mesmo commit) — o commit é a identidade; corrigir o rótulo no `publicar.sh` fica como melhoria pendente | Hermes Agent — ato automático **com evidência**, autorizado pelo dono no Telegram em 06/10/2026 ("(a)"), dentro do desenho aprovado (D5 + plano de execução) |
| 06/10/2026 | `develop` → `homolog` (**segunda promoção**) | `23abddf9b44a9f425cefdef2a9886c8ceba7fc67` (fast-forward de `fbf5bb1`; `homolog` remoto e cópia publicada no mesmo commit) | **15 arquivos, +1652/-10 — nenhum arquivo de APLICAÇÃO** (`odoo/`, `db/`, `control-plane/`, `n8n/` intocados, conferido pelo mesmo filtro de prefixos). Conteúdo: os artefatos do ciclo E2E (compose, par, instalador e verificador do n8n em dev e homolog; runbooks `n8n-dev.md` e `n8n-homolog.md`), o doc de ambientes atualizado, os registros — e o **conserto do publicador**: o rótulo `ref:` deixou de sortear um branch via `git name-rev` (passa a aceitar `--ref` e, sem ele, lista todos os nomes do commit) e a lista vazia deixou de matar o script sob `set -euo pipefail` | Verificador `scripts/provision/verificar-odoo-homolog.sh` no commit `23abddf9b44a9f425cefdef2a9886c8ceba7fc67` (sha256 `a98d554248fd8107b4832a1cf276dcb42a50e70572ba0da988f30ce56de75012`): **15 itens, 0 falhas**, rodado **depois** da troca da cópia. `.publicado` da cópia: `commit: 23abddf9b44a9f425cefdef2a9886c8ceba7fc67`, `ref: homolog` (primeira vez com o rótulo correto), digest `2b110a2d0ddde39ea558393bb8ebab17306e8f379768db1453714e180d79e1ad`, 799 arquivos, trava `chattr +i` armada, sem staging nem lock residuais. Nada reiniciado no serviço | Hermes Agent — ato automático **com evidência**, autorizado pelo dono no Telegram em 06/10/2026 ("(e)") |

## Identidades no commit desta promoção

| Artefato | sha256 |
|---|---|
| `scripts/provision/verificar-odoo-homolog.sh` | `a98d554248fd8107b4832a1cf276dcb42a50e70572ba0da988f30ce56de75012` |
| `scripts/provision/instalar-odoo-homolog.sh` | `14e3c4caebf0bcce0868ede2774ddf85e4488c0233b92bee54148e9134f26325` |
| `deploy/compose/edge/Caddyfile` | `0a5f6e22e5e4d38a5a3cea31a08b4f8ce4eef6fc090ab10394f3170f219e620a` |
| `deploy/compose/homolog/odoo.yml` | `452d68a50012f8da3c5c4ef73f09fc5ec11da11e025f2ae01b6b37494a5065ac` |
| `deploy/environments/homolog.env` | `6d73bb3c96052ce84156165625f183ecca23f785488b3356a21ba6de55aadd59` |

## Identidades no commit da segunda promoção (23abddf)

| Artefato | sha256 |
|---|---|
| `scripts/provision/verificar-odoo-homolog.sh` | `a98d554248fd8107b4832a1cf276dcb42a50e70572ba0da988f30ce56de75012` |
| `scripts/provision/verificar-n8n-dev.sh` | `af99f13c05449acd7869c6c294e62d57836161b1f627b0f3c4b65777d45045a2` |
| `scripts/provision/verificar-n8n-homolog.sh` | `c25e9e713540346cb8f2e8c581616dfd93c62dbbfd5e16adaca0cf51ba151421` |
| `deploy/publicar.sh` | `6be95844c480e7ab6bf165631f2dc5c9766fcea0a806015f0bd09eafae5487e5` |
| `deploy/compose/dev/n8n.yml` | `8240925c778de25f93b8cea35890634f56315ace6c36a4e66c1454a8a694e82e` |

## Promoção 3 — `develop` → `homolog` (06/10/2026)

- **Origem:** `develop` = `dbae3e4` · **destino:** `homolog` = `dbae3e4` (**fast-forward**: `homolog` era ancestral).
- **Delta:** 17 arquivos, +1580/-11 — **zero arquivo de app** (`odoo/addons/` = 0). Entram os **artefatos de
  Produção** (compose/env/instaladores/verificadores + runbooks) e dois registros do ciclo de Homolog.
- **Por que promoveu:** os instaladores de Produção rodam **da cópia do ambiente na VPS**
  (`/opt/tre/prod/repo/scripts/provision/...`) e a cópia é publicada do branch do ambiente. Sem esta
  promoção o runtime de Produção não teria de onde ler os artefatos.
- **Evidência:** `bash -n` OK nos 6 scripts derivados; inversão de isolamento revisada item a item (as
  listas recusam dev E homolog, nunca produção); verificadores de Homolog em 15/0 e 20/0.
- **Na VPS (autorizado):** apenas a rede `tre-odoo-prod` e o diretório `/etc/tre/odoo-prod` (700 root).
  Nenhum container ou volume de produção.
- **Pendente:** `homolog` → `main` (release de Produção) exige **aprovação humana registrada**.

## Promoção 4 — `develop` → `homolog` (06/10/2026)

- **Origem:** `develop` = `09e7699` · **destino:** `homolog` = `09e7699` (**fast-forward**: `homolog` era `dbae3e4`).
- **Delta:** 13 commits, 16 arquivos, +431/-20 — os consertos e registros do dia de Produção: `--ensaio` de
  verdade nos instaladores de Odoo, defaults do runner de migração (`pg-sales-homolog`/`sales_ai`), política
  `politica_producao.json`, Autorizações 5 e 6, porta **8071**, a borda passando a servir `tre` e os runbooks
  de produção com as medições.
- **Por que promoveu:** o runtime de Produção lê os artefatos **da cópia publicada do ambiente**, e a cópia é
  publicada do branch do ambiente. Sem esta promoção os consertos do dia (porta 8071, Caddyfile) não chegariam
  à cópia de produção.
- **Evidência:** aceites de produção medidos no mesmo dia — banco de vendas 20/0, Odoo 15/0, n8n 44/0 —, ciclo
  E2E nas duas direções, replay sem duplicata e dente do portão com par antes/depois; card `t_ba84b412`.

## Promoção 5 — `homolog` → `main` (o release de produção) (06/10/2026)

- **Origem:** `homolog` = `09e7699` · **destino:** `main` = **`70b4442`** — **merge commit de dois pais**
  (`1378018` primeiro pai, `09e7699` segundo), executado como **merge vindo de `homolog`**: ninguém escreve
  direto em `main` (D4).
- **Delta:** 266 commits, 269 arquivos, **+60.906 / -479**.
- **Aprovação humana:** **Autorização 7** em `docs/operations/registro-de-aprovacoes.md` (Telegram, opção
  **"A"** do dono), commit `99eba3b`. Verificador de aprovação humana: **PASS (18 itens, 0 falhas)**.
- **Por que promoveu:** a definição do dono — "produção = `main`, com Dev/Homolog/Produção espelhados em
  branches no GitHub e controlados via Git". Com este merge, `main` passa a ser o conteúdo que a produção
  **já roda e mediu** (o desvio `ref: homolog` deixa de existir).
- **Evidência:** o que a produção mediu no dia — banco de vendas `PROD_SALES_OK 20/0`, Odoo `PROD_ODOO_OK 15/0`,
  n8n `N8N_PROD_OK 44/0`, ciclo E2E nas duas direções, replay sem duplicata, dente do portão com par
  antes/depois; `docs/runbooks/{banco-de-vendas-prod,odoo-prod,n8n-prod}.md`; card `t_ba84b412` (388-389).
- **Pendência declarada:** `main` entra **sem CI e sem branch protection** (plano Free) — o freio é o gate do
  motor + o gancho `pre-push` versionado. `develop` segue à frente por commits de documentação.
