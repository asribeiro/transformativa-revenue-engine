# Runbook — campos de rastreio em `crm.lead` (módulo `transformativa_sales_ai`)

**Card:** `TRE-W2-E04-T02` (`t_d3bd6660`, perfil `desenvolvedor`) · **Depends on:** `TRE-W2-E03-T01`
(módulo base, `t_c536ce86`) · **Status:** entregue e medido em dev
**Branch:** `feature/TRE-W2-E04-T02` (local — `completion_contract: local-only`)
**Máquina:** VPS Contabo `vmi3619453` (169.58.24.102) · **Ambiente:** dev (homolog/prod **não**
provisionados — ADR-005)
**Artefatos versionados:** `odoo/addons/transformativa_sales_ai/models/crm_lead.py`,
`odoo/addons/transformativa_sales_ai/tests/test_crm_lead_rastreio.py`,
`scripts/odoo/{conferir_crm_lead_no_contrato.py,medir_crm_lead.py,verificar-crm-lead-odoo.sh}`,
este runbook.

Este runbook é o registro dos campos do doc 11 §2, das decisões de implementação, do
procedimento, do aceite medido, das provas negativas e do rollback (§1 é a fonte canônica; o
registro resumido no fio do card remete para cá).

---

## 1. Campos do card (doc 11 §2) e decisões registradas

| Campo | O que foi definido e registrado |
|---|---|
| **ACCEPTANCE CRITERIA** (homologados por Anderson, 29/09/2026) | AC1 campos previstos no contrato presentes em `crm.lead`; AC2 CRM padrão não quebra: criação/consulta de lead funcionam; AC3 teste de criação e consulta com dado sintético |
| **TEST PLAN** (proposto no card) | 6 passos: confronto estático módulo × contrato → instalação em banco limpo → campos no banco (`ir_model_fields` + índice em `pg_indexes`) → `--test-enable` do Odoo → dado sintético pelo ORM (+ conferência por SQL fora da sessão) → rollback por desinstalação. Executado por `scripts/odoo/verificar-crm-lead-odoo.sh`, item a item |
| **ROLLBACK PLAN** (proposto no card) | Reverter pelo módulo: desinstalação (§6), executada como **passo 5** do próprio aceite; no repo, `git revert` do commit do card |
| **AFFECTED COMPONENTS** | `odoo/addons/transformativa_sales_ai/**` (modelo novo + 3 linhas de `__init__` + README), `scripts/odoo/**` (3 ferramentas novas), este runbook, `scripts/verificar_estrutura.sh`, `CHANGELOG.md`, `docs/operations/registro-de-execucoes.md`. **No ambiente:** só a dupla descartável do aceite (nada no `odoo-dev`/`pg-odoo-dev`/`/opt/tre/repo`, nada em homolog/prod) |
| **RISK LEVEL** (proposto) | **Médio** — `crm.lead` é o modelo central do CRM (o AC2 é justamente "não quebrar"); os campos entram aditivos, sem view/ACL (E06/E07), reversíveis por desinstalação |

**Decisões de implementação deste card** (não mudam o contrato; valem para as próximas cards):

| # | Decisão | Porquê |
|---|---|---|
| D1 | Os **13 campos** entram como inventário explícito no modelo (`CAMPOS_DE_RASTREIO`), com **2 nomes fixados pelo contrato** (`tf_opportunity_id`, `tf_priority_score`) e 11 derivados de artefatos que o contrato publica | o contrato nomeia, em `crm.lead`, exatamente esses 2 (`canonical_ids.odoo_map` §3); o número "13" do plano não tem fonte materializada no repo (conferido por `git grep` em todas as refs) — os outros 11 são espelho de score model §8, vocabulário §7 e trilha de correlação/idempotência/eventos §3/§6. Cada nome tem proveniência declarada (§2) e o conferidor confronta com o JSON congelado |
| D2 | Nomes no padrão do contrato (`tf_` + nome do conceito do contrato), igual ao card irmão `TRE-W2-E04-T01` | um só vocabulário de campos customizados no módulo (integração, views e API leem o mesmo nome) |
| D3 | `tracking=True` nos campos de valor (não no derivado) | "campo de rastreio" também no sentido Odoo: a mudança do valor espelhado fica registrada no chatter do lead. O derivado (`tf_priority_tier`) não é rastreado de novo — ele muda porque o score (rastreado) mudou |
| D4 | Índice só nos 3 campos de busca por identidade/correlação (`tf_opportunity_id`, `tf_correlation_id`, `tf_idempotency_key`) | são as chaves por onde a integração encontra o lead; o contrato **não** pede busca por score (§8 consulta `scores` por `score_type` no `sales_intelligence`) |
| D5 | `tf_priority_tier` é **computado e armazenado** a partir de `tf_priority_score`, com as faixas do contrato §8 | uma só fonte de verdade (o score); sem faixa "digitada" que possa divergir do score. O conferidor reprova se as faixas do modelo deixarem de ser as do contrato |
| D6 | Sem `default`: lead comum **não** ganha valor artificial | o AC2 é "o CRM padrão não quebra"; valor inventado em 100% dos leads seria regra nova e ruído |
| D7 | `tf_opportunity_id` tem constraint de UUID | §3: o ID canônico É UUID (`id UUID`); texto livre no campo do ID canônico não é "mais permissivo", é outra coisa (mesmo critério do E04-T01 para `tf_company_id`) |
| D8 | Nada de view, ACL, normalização de valor, faixa de score recusada ou cálculo de score | E06/E07 e o `sales_intelligence` são os donos dessas partes; o espelho não inventa regra mais dura que o contrato |

## 2. Os 13 campos (inventário e proveniência)

| # | Campo | Tipo | Origem no contrato |
|---|---|---|---|
| 1 | `tf_opportunity_id` | Char, index, rastreado | §3 `odoo_map.canonical_opportunity` |
| 2 | `tf_priority_score` | Float, rastreado | §3 `odoo_map.priority score` + §8 PRIORITY |
| 3 | `tf_icp_score` | Float, rastreado | §8 score `ICP` |
| 4 | `tf_automation_fit_score` | Float, rastreado | §8 score `AUTOMATION_FIT` |
| 5 | `tf_buying_signal_score` | Float, rastreado | §8 score `BUYING_SIGNAL` |
| 6 | `tf_data_quality_score` | Float, rastreado | §8 score `DATA_QUALITY` |
| 7 | `tf_score_version` | Char, rastreado | §8 ("score sem `score_version` não é reprodutível e é recusado") |
| 8 | `tf_priority_tier` | Selection computado/stored | §8 tiering `A+`/`A`/`B`/`C`/`Nurture` |
| 9 | `tf_next_best_action` | Selection, rastreado | §7 vocabulário fechado `next_best_action` (9 valores) |
| 10 | `tf_correlation_id` | Char, index, rastreado | §3 `correlation_columns` + §6 regra 1 |
| 11 | `tf_idempotency_key` | Char, index, rastreado | §3/§6 `sync_events.idempotency_key` (única por operação) |
| 12 | `tf_last_sync_at` | Datetime, rastreado | §6 trilha de sincronização (`sync_events`) |
| 13 | `tf_last_event_type` | Char, rastreado | §6 lista de eventos (6 PG→Odoo + 7 Odoo→PG, no `help` do campo) |

**Lacunas declaradas** (para não inventar regra):

- `Float` do Odoo não distingue "nulo" de `0.0`: um lead sem score informado devolve `0.0` e,
  por isso, **não** recebe faixa (`tf_priority_tier` fica vazio). `0` como score legítimo é
  Nurture pela faixa do contrato, mas não é distinguível de "sem score" no armazenamento atual —
  a distinção fina (score ausente × score zero) fica para quando o produtor precisar dela
  (`sales_intelligence` guarda o score versionado; o espelho guarda o corrente);
- valor de score fora de 0–100 (negativo) não tem faixa no contrato e o espelho devolve vazio
  em vez de inventar faixa;
- `tf_last_event_type` é texto livre cujos valores são os `event_type` do contrato (documentados
  no `help`): promover para `Selection` criaria um vocabulário fechado novo (o contrato declara
  os eventos, não um vocabulário de "estado do último evento").

## 3. Procedimento (na VPS do dev)

```bash
# 1) do container do Hermes: transferir o módulo, as ferramentas e o contrato (tar por ssh, sem scp)
#    (caminho PRÓPRIO do card: o card irmão TRE-W2-E04-T01 roda no mesmo módulo e usa o seu)
tar -C odoo/addons -cf - transformativa_sales_ai | ssh ... 'mkdir -p /opt/tre/evid-t_d3bd6660/modulo && tar -C /opt/tre/evid-t_d3bd6660/modulo -xf -'
tar -C scripts/odoo -cf - . | ssh ... 'mkdir -p /opt/tre/evid-t_d3bd6660/scripts && tar -C /opt/tre/evid-t_d3bd6660/scripts -xf -'
tar -C docs/data -cf - data_contract_v1.json | ssh ... 'mkdir -p /opt/tre/evid-t_d3bd6660/contrato && tar -C /opt/tre/evid-t_d3bd6660/contrato -xf -'
(cd odoo/addons && find transformativa_sales_ai -type f | LC_ALL=C sort | xargs sha256sum)  # e do outro lado

# 2) na VPS (sempre a partir de ARQUIVO — nunca por stdin, ver armadilha do `docker compose run`)
#    As TRÊS variáveis de caminho vão juntas (export, não prefixo de um comando só): o
#    `TRE_CONTRATO_JSON` **não tem default que resolva** — o caminho padrão
#    `/opt/tre/dev/contrato/data_contract_v1.json` não existe em nenhum ambiente medido — e o
#    verificador **recusa de cara** (`exit 1`, antes de subir qualquer coisa) quando o contrato
#    não está em disco (defeito 5 do §8).
cd /opt/tre/evid-t_d3bd6660/scripts
export TRE_MODULO_DIR=/opt/tre/evid-t_d3bd6660/modulo/transformativa_sales_ai
export TRE_CONTRATO_JSON=/opt/tre/evid-t_d3bd6660/contrato/data_contract_v1.json
export TRE_LOG_DIR=/opt/tre/evid-t_d3bd6660/logs
bash verificar-crm-lead-odoo.sh                       # aceite completo (6 passos)
bash verificar-crm-lead-odoo.sh --apenas-confronto    # só o confronto módulo x contrato
bash verificar-crm-lead-odoo.sh --prova-de-dente      # baseline não mutado + 5 mutações
```

O verificador, em ordem: **guardas** — contrato, conferidor e medidor **primeiro** (são arquivos:
sem eles o comando recusa de cara, sem medir nada), depois docker, as duas imagens com os digests
medidos, `openssl`, módulo e inventário em disco e nome de banco descartável; e mede o estado do
dev **antes** → **dupla descartável própria** (rede própria, `postgres:16` e `odoo:19.0` com
configuração própria) → **passo 0** confronto estático módulo × contrato → **passo 1** instalação
em banco limpo → **passo 2** os 13 campos no `ir_model_fields` + índices reais em `pg_indexes` →
**passo 3** `--test-enable` (testes do módulo) → **passo 4** dado sintético pelo ORM
(`medir_crm_lead.py`) + conferência por SQL fora da sessão do Odoo → **passo 5** rollback
(desinstalação) com medição de resquício → **limpeza** e conferência do dev. No modo
`--prova-de-dente`, antes de qualquer mutação o verificador roda o **caminho não mutado**
(baseline: passo 0 + 1 + 2 + 3) e **exige verde** — sem baseline verde ele termina em
`CRM_LEAD_DENTE_FALHOU (baseline nao medido — nenhum dente exercitado)`, `exit 1` (§5).

**Isolamento:** o aceite **não** usa `pg-odoo-dev`, `odoo_dev` nem `/opt/tre/repo` — o AC pede
banco **limpo** e a instância do dev carrega o funil do `TRE-W2-E02-T01`; além disso o Odoo do
dev abre sessão em qualquer banco novo da instância (medido no E03-T01, runbook §7 daquele card).
Como o card irmão `TRE-W2-E04-T01` roda no **mesmo módulo** ao mesmo tempo, o aceite deste card
usa **caminho próprio** (`/opt/tre/evid-t_d3bd6660/`) — inclusive para as ferramentas, que
também vivem no repo compartilhado.

## 4. Aceite — TEST PLAN medido (01/10/2026, VPS `vmi3619453`)

```bash
cd /opt/tre/evid-t_d3bd6660/scripts
TRE_MODULO_DIR=/opt/tre/evid-t_d3bd6660/modulo/transformativa_sales_ai \
TRE_LOG_DIR=/opt/tre/evid-t_d3bd6660/logs \
TRE_CONTRATO_JSON=/opt/tre/evid-t_d3bd6660/contrato/data_contract_v1.json \
bash verificar-crm-lead-odoo.sh
RESULTADO: CRM_LEAD_OK (64 itens, 0 falhas) modulo=transformativa_sales_ai \
  banco=tre_e04_t02_crm_lead imagens=odoo:19.0+postgres:16
EXIT=0
```

| Passo | Medição |
|---|---|
| **0 confronto módulo × contrato** | `CONFERIDOR_CRM_LEAD_OK (18 itens, 0 falhas)`: contrato nomeia 2 campos em `crm.lead` e ambos estão no módulo; os 5 score types do §8 têm espelho; tiering do modelo == `scores.tiers` do JSON (5 faixas, cobrindo 0–100 sem lacuna/sobreposição); vocabulário `next_best_action` == §7 (9 valores); os 13 eventos do §6 (6 PG→Odoo + 7 Odoo→PG) são os do modelo; inventário do card == campos implementados (13, nenhum a mais); índices == declarados; `tf_opportunity_id` com constraint de UUID |
| **1 instalação em banco limpo** | banco `tre_e04_t02_crm_lead` **não existia** → criado do zero; `odoo --init exit 0`; **0 ERROR/CRITICAL**; `'Modules loaded.'`; `state=installed`; módulo padrão `crm` instalado |
| **2 campos no banco (AC1)** | os **13** campos do inventário em `ir_model_fields` de `crm.lead` (13 contados por `name like 'tf_%'` — nenhum a mais), **0** com `state=manual`; índice real em `pg_indexes` para `tf_opportunity_id`, `tf_correlation_id` e `tf_idempotency_key`; os 2 nomes do contrato presentes; os 5 `tf_*_score` no banco |
| **3 teste do Odoo** | `odoo --test-enable exit 0`; `0 failed, 0 error(s) of 13 tests` (6 do módulo base + **7 deste card**, contados no log pelo runner: `Starting TestCrmLeadRastreio.test_01…test_07`); 0 linha de teste reprovado; `state=installed` depois |
| **4 dado sintético (AC2 + AC3)** | `odoo shell` + ORM → `MEDICAO_CRM_LEAD_OK (58 itens, 0 falhas)`: lead com os 13 campos criado, **lido de volta campo a campo**, achado por `tf_opportunity_id` e por `tf_idempotency_key` + `tf_next_best_action`, `tf_priority_tier` derivado (87,5 → `A`; após `write` de 40,0 → `Nurture`), UUID inválido recusado e UUID v4 aceito, lead comum criado/renomeado/buscado e **sem valor artificial** nos campos novos, campos padrão intactos. Conferido **também por SQL fora da sessão do Odoo**: 1 linha com o `tf_opportunity_id`+`tf_idempotency_key` sintéticos com `Nurture|NURTURE|PRIORITY_SCORE_CHANGED|91.00`, 0 lead comum residual, parceiro sintético gravado, 4/4 colunas padrão do `crm_lead` |
| **5 rollback (desinstalação)** | `DESINSTALACAO_OK estado_antes=installed estado_depois=uninstalled`; **0** campo `tf_` em `ir_model_fields`, **0** coluna `tf_` em `crm_lead`, **0** índice dos campos de rastreio em `pg_indexes`; as 4 colunas padrão do `crm_lead` intactas; os dados do CRM **não** são apagados (1 linha em `crm_lead` depois) |
| **6 limpeza / dev intocado** | banco, postgres, rede e diretório de configuração descartáveis removidos; instância do dev com **os mesmos 4 bancos antes e depois** (`odoo_dev, postgres, template0, template1`); `homolog`/`prod` com **0 arquivo**; nenhuma escrita em `/opt/tre/repo` |

**Identidade do que foi testado:** `odoo:19.0` no digest
`sha256:77bac5cd1e065210828f34883a7f76740b7373d06dd3a5a55d3eeb31ee2f85cd` (o mesmo do par de dev) e
`postgres:16` no digest `sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54`.
sha256 **igual no repo e na VPS** (medido nos dois lados): `models/crm_lead.py bc18a74e…`,
`tests/test_crm_lead_rastreio.py 64bbceee…`, `__init__.py faa0bf14…`, `models/__init__.py 40516ddf…`,
`tests/__init__.py 61625721…`, `README.md 3dd18241…`, `__manifest__.py cd84f4ec…` (inalterado),
`conferir_crm_lead_no_contrato.py c90d7e7f…`, `medir_crm_lead.py 193d1ab8…`,
`verificar-crm-lead-odoo.sh 161b512e…`, `docs/data/data_contract_v1.json dfc74c95…`.

**Regressão do módulo base (com este card em cima):** o aceite do `TRE-W2-E03-T01`
(`/opt/tre/dev/scripts/odoo/verificar-modulo-odoo.sh`, sha256 `72d00aa1…` — o mesmo do card base)
rodado contra **este** módulo (banco `tre_e04_t02_base`) deu
`RESULTADO: MODULO_ODOO_OK (51 itens, 0 falhas)`, **exit 0** — instalação em banco limpo, teste do
Odoo, desinstalação e reinstalação seguem passando com os campos novos dentro.

**Logs brutos:** `/opt/tre/evid-t_d3bd6660/` — `aceite.out` (64 itens + `EXIT=0`),
`prova-de-dente.out` (`EXIT=0`), `regressao-modulo-base.out` (`EXIT=0`),
`logs/{0-confronto-contrato,1-instalacao,3-teste,4-dado-sintetico,5-desinstalacao}.out` e
`sha256-ferramentas.txt`.

## 5. Provas negativas — o aceite tem dentes

`bash verificar-crm-lead-odoo.sh --prova-de-dente` →
**`RESULTADO: CRM_LEAD_DENTE_OK (5 provas, 0 falhas)`**, **exit 0** (medido na rodada 2 com
`verificar-crm-lead-odoo.sh` sha256 `0fbaa3c5…`). Cada prova roda numa **cópia** do módulo (o módulo
real não é tocado) e espera **reprovação** — mas só depois de o **baseline** medir verde:

| Prova | Mutação (em cópia) | Caminho medido | Assinatura de falha exigida | Resultado medido |
|---|---|---|---|---|
| **Baseline** | **nenhuma** (módulo real, caminho intacto) | passo 0 + 1 + 2 + 3 (`--apenas-instalacao-e-testes`) | — (tem de medir **verde**; sem isto o comando termina em `CRM_LEAD_DENTE_FALHOU (baseline nao medido — nenhum dente exercitado)`, **exit 1**) | `RESULTADO: CRM_LEAD_OK (43 itens, 0 falhas)`, **exit 0** |
| **Dente 1** | `tf_opportunity_id` → `tf_opp_id` (campo nomeado pelo contrato renomeado) | **banco** (`--apenas-instalacao-e-campos`, com `TRE_PULAR_CONFRONTO=1`) | `campo nomeado pelo contrato AUSENTE: tf_opportunity_id` **ou** `so' 12 de 13 campos do inventario` | `FALHOU so' 12 de 13 campos do inventario em ir_model_fields de crm.lead`, `FALHOU sem indice no banco para tf_opportunity_id`, `FALHOU campo nomeado pelo contrato AUSENTE: tf_opportunity_id` → `CRM_LEAD_FALHOU (35 itens, 3 falha(s))`, **exit 1** |
| **Dente 2** | `index=True` removido do `tf_idempotency_key` | **banco** (idem) | `sem indice no banco para tf_idempotency_key` | `FALHOU sem indice no banco para tf_idempotency_key (busca por identidade/correlacao — contrato §3/§6)` → `CRM_LEAD_FALHOU (35 itens, 1 falha(s))`, **exit 1** |
| **Dente 3** | declaração do `tf_next_best_action` apagada do modelo | **banco** (idem) | `crm.lead tem 12 campo` | `FALHOU so' 12 de 13 campos do inventario…`, `FALHOU crm.lead tem 12 campo(s) tf_ (inventario: 13)` → `CRM_LEAD_FALHOU (35 itens, 2 falha(s))`, **exit 1** |
| **Dente 5** | a mesma mutação do dente 1 | **confronto estático** (`--apenas-confronto`) | `inventario != implementado` **ou** `AUSENTE no modulo: tf_opportunity_id` | `FALHOU conferidor de contrato reprovou (14 OK / 4 falhas, exit 1): … campo nomeado pelo contrato AUSENTE no modulo: tf_opportunity_id … inventario != implementado: faltando=['tf_opportunity_id'] sobrando=['tf_opp_id']; indices divergem` → `CRM_LEAD_FALHOU (13 itens, 1 falha(s))`, **exit 1** |
| **Dente 4** | teste plantado que falha (`test_99_prova_de_dente`) | **teste do Odoo** (`--apenas-instalacao-e-testes`) | `1 failed, 0 error\(s\) of` | `FALHOU odoo --test-enable exit 1`, `FALHOU runner do Odoo: 1 failed, 0 error(s) of 14 tests (minimo 13)`, `FALHOU 1 linha(s) de teste reprovado(a) no log` → `CRM_LEAD_FALHOU (43 itens, 3 falha(s))`, **exit 1** |

**Por que a mutação não chega só ao passo 0:** o confronto estático reprova a mutação antes do banco,
e o verificador pararia ali. Por isso os dentes 1–3 rodam com `TRE_PULAR_CONFRONTO=1` (pula **só** o
passo 0 e registra o pulo como `INFO`) — assim quem reprova é a **medição de banco** (passo 2) — e o
dente 5 mede o caminho estático com a mesma mutação. Os dentes 1–3 também provam que o caminho de
banco **não** depende do conferidor estático (defeito 4 do §8). O dente 4 é o que prova que os itens
do **passo 3** medem de verdade (inclusive o item "nenhuma linha de teste `FAIL:`/`ERROR:`", que na
primeira versão estava **cego** — defeito 2 do §8).

**Os números da tabela são os da rodada 1 e os da rodada 2** (mesma contagem em todos os dentes:
`35/3`, `35/1`, `35/2`, `13/1`, `43/3`), o que também mostra que o conserto do **defeito 5** (o
baseline e a assinatura por dente) não mudou o que cada mutação mede. **Falso-verde fechado e
medido** (§8.1): com os **defaults** do script (nada exportado) e com **só `TRE_MODULO_DIR`**
exportado — os dois caminhos que davam `CRM_LEAD_DENTE_OK`, `exit 0`, sem medir nada — o comando
agora devolve `CRM_LEAD_DENTE_FALHOU (baseline nao medido — nenhum dente exercitado)`, **exit 1**
(`EXIT_A=1`, `EXIT_B=1`). E o **julgamento** do dente foi sondado: o `confere_dente` extraído do
artefato sob teste, alimentado com (g1) a saída real do dente 1 → **OK**; (g2) a saída de **aborto**
de guarda → reprovado; (g3) a saída real **sem** o passo medido → reprovado; (g4) a saída real do
dente 1 julgada pela assinatura de **outro** dente → reprovado; (g5) a saída **verde** do aceite →
reprovado → `DENTE_FALHAS=4` = `CONTROLE_JULGAMENTO_OK`, **exit 0**.

## 6. Rollback

**Nível 1 — reverter pelo módulo (rollback declarado no card):** desinstalação, executada como
**passo 5** do aceite e medido ali:

```bash
docker run --rm -i --network <rede> -v <conf>:/etc/odoo/odoo.conf:ro \
    -v <modulo>:/mnt/extra-addons/transformativa_sales_ai:ro -e TRE_MODULO=transformativa_sales_ai \
    --entrypoint odoo odoo:19.0 shell -d <banco> --no-http < desinstalar_modulo.py
```

O passo 5 mede, depois do uninstall: `state = uninstalled`, **0** campo `tf_` em
`ir_model_fields`, **0** coluna `tf_` em `crm_lead`, **0** índice dos campos de rastreio em
`pg_indexes` e as colunas padrão do `crm_lead` intactas. Diferente do card base (E03-T01, cujo
módulo não tinha modelo e cujo "0 resquício" era estruturalmente vazio), **aqui o resquício é
medível**: os 13 campos e os 3 índices existem para desaparecer.

**Nível 2 — reverter o card no repo:** `git revert` do commit do card.

## 7. Pendências declaradas (não são deste card)

- **Publicação na cópia operacional `/opt/tre/repo`**: herdada do E03-T01 (runbook §9 daquele
  card) — o `odoo-dev` monta essa cópia e o módulo ainda não está publicado nela; enquanto isso,
  o aceite mede o módulo pelo caminho deste runbook (dupla descartável + cópia do repo na VPS).
- **Views e ACLs**: `TRE-W2-E06-T01` e `TRE-W2-E07-T01` (os campos aqui não aparecem em nenhuma
  tela ainda — é o card de views que os expõe).
- **Escrita dos campos pela integração**: `TRE-W2-E05-T01` (modelo canônico) e a API controlada
  (`TRE-W3-E01-T01`) são quem vai popular `tf_*`; este card entrega o espelho.
- **Ratificação da versão do Odoo (19.0) e homologação (estágio 7)**: Anderson.

## 8. Defeitos encontrados nesta execução (e conserto)

Todos achados **executando** (não por leitura), todos consertados e **remédidos** — e, por isso, a
bateria inteira (confronto + aceite de 6 passos + 5 dentes) foi reexecutada ao final, já com o
script em `161b512e…`, obtendo `CRM_LEAD_OK (64 itens, 0 falhas)` e `CRM_LEAD_DENTE_OK (5 provas)`,
ambos `exit 0`.

1. **Teste do card reprovava** — `test_07_vocabulario_e_eventos_do_contrato` estourava
   `ValueError: too many values to unpack (expected 2)`: o teste desempacotava
   `modelo.VOCABULARIO_NEXT_BEST_ACTION` como pares `(valor, rotulo)`, mas a constante é a **lista
   de VALORES** do contrato §7 (o rótulo é o próprio valor). A primeira rodada do aceite pegou:
   `1 error(s) of 13 tests`, `exit 1`. **Conserto:** comparar `set(...)` direto. Nenhum outro teste
   foi afetado.
2. **Item do passo 3 cego** — o item "nenhuma linha de teste `FAIL:`/`ERROR:`" usava
   `grep -cE '^(FAIL|ERROR): '`, e o log do Odoo prefixa a linha com data/hora/nível (`2026-10-01
   16:52:45,273 1 ERROR …`): com **1 erro real** o item imprimia "nenhuma linha". **Conserto:**
   padrão sem âncora de início (`' (FAIL|ERROR): '`). **Prova de que agora mede:** dente 4, que
   reprova o passo 3 com `3 falha(s)`, incluindo `FALHOU 1 linha(s) de teste reprovado(a) no log`.
3. **Dentes não rodavam quando o verificador era chamado por caminho relativo** — as provas
   reexecutavam o verificador por `"$0"`; com `bash verificar-crm-lead-odoo.sh` (sem barra) o shell
   procurava no `PATH` e as 4 provas morriam com
   `verificar-crm-lead-odoo.sh: command not found`, devolvendo
   `CRM_LEAD_DENTE_FALHOU (4 prova(s) sem dente)`, `exit 1`. **Conserto:** caminho absoluto do
   próprio script (`SELF="$(readlink -f "$0")"`) e chamada `bash "$SELF"`.
4. **Os dentes de campo/índice mediam só o caminho estático** — como o passo 0 (confronto
   módulo × contrato) reprova uma mutação de campo/índice, o verificador encerrava no resumo logo
   depois do par, com `16 itens, 1 falha`: os itens de **banco** (passo 2) nunca eram exercitados e
   o dente não provava quem mede o quê. **Conserto:** `TRE_PULAR_CONFRONTO=1` (pula **só** o passo 0,
   registrando `INFO`) usado pelos dentes 1–3, que passaram a reprovar pelo **banco** (`35 itens`,
   `3`/`1`/`2` falhas), mais o **dente 5**, que mede o caminho estático (`13 itens, 1 falha`) com a
   mesma mutação do dente 1. Cobertura: banco + confronto + teste do Odoo.

**Defeito herdado do card base, não consertado aqui (fora do escopo, não deixa o aceite passar
falso-negativo):** o mesmo `grep -cE '^(FAIL|ERROR): '` existe em
`scripts/odoo/verificar-modulo-odoo.sh` (E03-T01, sha256 `72d00aa1…`), no item de linhas
reprovadas do passo 2. Lá ele também fica cego, mas o item vizinho (relatório do runner do Odoo,
com `N failed`/`N error`) reprova o passo — o aceite **falha fechado**; é fraqueza de diagnóstico,
não falso-verde. Registrado para o `tester`/próximo card que tocar aquele script.

### 8.1 Defeito 5 — o modo dente dava verde sem medir (rodada 2, achado da revisão independente)

**Achado pela revisão independente** (`tester`, rodada 1, sobre o commit `5ee09faa`), reproduzido
por mim com o artefato da rodada 1 antes de consertar. O julgamento do modo `--prova-de-dente`
aceitava **qualquer** `CRM_LEAD_FALHOU` como prova de dente, inclusive a falha que vem das
**guardas**, antes de medir qualquer coisa. Com os **defaults** do script — exatamente o comando do
USO do cabeçalho, e o que o bloco da §3 exportava até esta rodada (só `TRE_MODULO_DIR` e
`TRE_LOG_DIR`) — as 5 provas morriam na guarda do contrato e o comando dava verde:

```
$ bash /opt/tre/evid-t_d3bd6660/scripts/verificar-crm-lead-odoo.sh --prova-de-dente   # 161b512e…
FALHOU contrato ausente em /opt/tre/dev/contrato/data_contract_v1.json (sem contrato nao ha confronto)
RESULTADO: CRM_LEAD_FALHOU (6 itens, 1 falha(s)) …
OK    dente 1: a mutacao REPROVOU o aceite (o item tem dente)
… (dentes 2, 3, 5 e 4 iguais, todos morrendo na guarda) …
RESULTADO: CRM_LEAD_DENTE_OK (5 provas, 0 falhas) modulo=transformativa_sales_ai
EXIT_ANTES=0
```

O aceite sempre foi **fail-CLOSED** (contrato ausente reprova — medido); quem era **fail-OPEN** era
o modo dente, e é justamente ele que o TEST PLAN do card promete ("campo renomeado, índice removido
e campo apagado têm de reprovar"). **Conserto (rodada 2):** (a) o modo dente roda o **caminho não
mutado** (baseline: passo 0 + 1 + 2 + 3) e **exige verde** — sem baseline ele termina em
`CRM_LEAD_DENTE_FALHOU (baseline nao medido — nenhum dente exercitado)`, `exit 1`; (b) cada dente
exige a **sua assinatura de falha** (o texto que só aquela mutação produz) em vez de "qualquer
FALHOU"; (c) saída que abortou numa guarda — ou que não chegou ao passo medido — **reprova** o
dente; (d) o `TRE_CONTRATO_JSON` deixou de ter um default morto: sem contrato em disco o comando
**recusa de cara**, `exit 1`, antes de subir qualquer coisa, dizendo o que exportar (as guardas de
arquivo passaram a ser as primeiras), e o USO do cabeçalho + o bloco da §3 passaram a exportar as
três variáveis de caminho. A bateria inteira foi reexecutada com o script final (§5 e §4).
