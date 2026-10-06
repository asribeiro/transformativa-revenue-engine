# Runbook — Odoo no ambiente HOMOLOG

Ambiente provisionado em 05/10/2026 a partir do desenho de ambientes (decisão D5) e **derivado do dev**
para não divergir por digitação. O Homolog existe para detectar diferença de ambiente — então ele nasce com
o mesmo desenho do dev; o que muda é o que for **declarado**.

## 1. O que está no ar

| peça | dev | homolog |
|---|---|---|
| branch | `develop` | `homolog` |
| cópia publicada na VPS | `/opt/tre/repo` (compartilhada, do dono) | `/opt/tre/homolog/repo` (própria) |
| compose | `/opt/tre/dev/compose/odoo.yml` | `/opt/tre/homolog/compose/odoo.yml` |
| par não-secreto | `deploy/environments/dev-odoo.env` | `deploy/environments/homolog-odoo.env` |
| segredos | `/etc/tre/odoo-dev/` | `/etc/tre/odoo-homolog/` |
| containers | `pg-odoo-dev`, `odoo-dev` | `pg-odoo-homolog`, `odoo-homolog` |
| volumes | `pgdata-odoo-dev`, `odoo-data-dev` | `pgdata-odoo-homolog`, `odoo-data-homolog` |
| rede | `tre-odoo-dev` | `tre-odoo-homolog` |
| porta | `127.0.0.1:8069` | `127.0.0.1:8070` |
| acesso público | `dev.tre.…` (basic auth) | `homolog.tre.…` (basic auth) |

Imagem: **a mesma do dev**, por digest (`odoo:19.0`, digest em `homolog-odoo.env`). Roda imagem diferente
não homologa nada — o verificador reprova se os digests divergirem.

## 2. Operar

```bash
# instalar (na VPS; fail-closed: cada guarda reprova ANTES de criar qualquer coisa)
bash /opt/tre/homolog/compose/instalar-odoo-homolog.sh          # copiado de scripts/provision/
# verificar (na VPS; uma linha PASS/FALHOU por item)
bash /opt/tre/homolog/compose/verificar-odoo-homolog.sh
# subir/derrubar
docker compose --env-file /opt/tre/homolog/compose/odoo.env -f /opt/tre/homolog/compose/odoo.yml up -d
```

Publicar a cópia de homolog (caminho único, com destino e artefato **isolados** para não confundir o
watchdog da cópia compartilhada):

```bash
TRE_PUBLICAR_DESTINO=/opt/tre/homolog/repo \
TRE_PUBLICAR_ARTEFATO=/opt/tre/.publicacao-artefato-homolog \
TRE_PUBLICAR_LOCK=/opt/tre/.publicacao-homolog.lock \
  deploy/publicar.sh --commit origin/homolog
```

## 3.1 Funil comercial (CRM) — espelho do dev

O funil não viaja com o deploy: ele é **dado** (`crm_stage` + `crm_team`), aplicado por rotina. O dev tem 12
etapas desde 30/09/2026; homolog nasceu com as **4 de fábrica** do módulo `crm` (New / Qualified / Proposition /
Won — medido em 06/10/2026). Para conferir o desenho na tela de homolog, o caminho é o par espelhado do dev:

```bash
# aplicar (na VPS; par espelhado de scripts/provision/configurar-crm-dev.sh)
TRE_CRM_YAML=<declaracao> TRE_CRM_ORM=<aplicador> \
TRE_ODOO_COMPOSE=/opt/tre/homolog/compose/odoo.yml \
TRE_ODOO_ENV=/opt/tre/homolog/compose/odoo.env \
TRE_ODOO_BANCO=odoo_homolog \
  bash configurar-crm-homolog.sh      # -> RESULTADO: CRM_HOMOLOG_CONFIGURADO ...
# verificar (na VPS) -> RESULTADO: CRM_HOMOLOG_OK (N itens, 0 falhas)
bash verificar-crm-homolog.sh
```

A declaração e o aplicador são os mesmos do dev (`odoo/crm/funil-transformativa.yaml`,
`scripts/provision/aplicar_funil_crm.py`): o aplicador é idempotente, casa etapa por **nome**, adota a etapa
existente em vez de duplicar, e tem guarda fail-closed — etapa com oportunidade (`crm.lead.stage_id`) **não** é
removida; ele reprova em vez de mexer em dado de negócio. O `Nurture` é `crm.team` próprio (ramo lateral), não
etapa do funil principal — foi assim que o contrato o desenhou.

**Guarda invertida de propósito.** O wrapper do dev recusa rodar se existir container de homolog/produção. Aqui
essa guarda seria falsa por construção (os três ambientes convivem neste VPS), então ela **não** foi copiada:
o isolamento do espelho é o caminho do compose (`/opt/tre/homolog/*`) mais os nomes de container deste ambiente.
O verificador mede o invariante oposto — que os vizinhos (`odoo-dev`, `pg-odoo-dev`, `odoo-prod`, `pg-odoo-prod`)
sigam de pé depois da rodada.

## 3.2 Armadilhas medidas

- **O instalador do homolog NÃO recusa a existência do dev** (é o inverso do instalador do dev, que recusa
  homolog/produção). A garantia aqui não é disciplina: ele resolve o compose (`docker compose config`) e
  **para antes de criar nada** se o artefato citar container de outro ambiente.
- **Banco existe ≠ Odoo inicializado**: o `POSTGRES_DB` do `postgres:16` cria o banco vazio. O que prova
  inicialização é a tabela do módulo `base` (`ir_module_module`) — com o banco vazio o Odoo responde 500.
- **`docker compose run` consome o stdin de quem o executa.** Orquestrado por `ssh … 'bash -s' < script`,
  isso mata o resto do script remoto; por isso o `run` de inicialização leva `< /dev/null`.
- **`odoo.conf` precisa de dono `100:101`** (uid/gid do usuário `odoo` dentro do container): o processo não
  lê um arquivo 600 do root. Nenhum usuário do host tem uid 100 por acaso.
- **A porta é loopback de propósito**: quem expõe é a borda, por hostname. Publicar `0.0.0.0:8070` furaria o
  ponto único de entrada; o verificador reprova explicitamente.
- **A borda roteia `homolog.tre.…` para `127.0.0.1:8070`** — antes do provisionamento ela respondia `503`
  ("não provisionado") de propósito: nome que resolve e responde isso é melhor que erro de TLS.
- **Ambiente assimétrico é o defeito clássico** ("funciona em dev, quebra em produção"). O que homolog ainda
  **não** tem, declarado: `n8n` e o banco de vendas (`sales_intelligence` do dev). O par `homolog.env` deixa
  o trio `TRE_PG_*` ausente de propósito — apontar para o container do dev faria o backup de homolog gravar
  artefato com o banco do dev.
