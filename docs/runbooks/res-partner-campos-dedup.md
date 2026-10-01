# Runbook — campos de dedup e IDs canônicos em `res.partner` (TRE-W2-E04-T01)

**Card:** `TRE-W2-E04-T01` (`t_adee6ad7`, perfil `desenvolvedor`) · **Status:** implementado e medido
em dev (01/10/2026) · **Depends on:** `TRE-W2-E03-T01` (módulo base, `t_c536ce86`)
**Branch:** `feature/TRE-W2-E04-T01` (local — `completion_contract: local-only`), nascida do commit
`fe26aa5` (entrega do módulo base)
**Máquina:** VPS Contabo `vmi3619453` (169.58.24.102) · **Ambiente:** dev (homolog/prod **não**
provisionados — ADR-005)
**Artefatos versionados:** `odoo/addons/transformativa_sales_ai/models/**`,
`odoo/addons/transformativa_sales_ai/tests/test_res_partner_dedup.py`,
`scripts/odoo/verificar-res-partner.sh`, `scripts/odoo/conferir_res_partner_no_contrato.py`,
`scripts/odoo/medir_res_partner.py`, este runbook

Este runbook é o registro dos campos do doc 11 §2, das decisões de implementação, do
procedimento, do aceite medido, das provas negativas, dos defeitos encontrados **executando** e do
rollback.

---

## 1. Campos do card (doc 11 §2) e decisões registradas

| Campo | O que foi definido e registrado |
|---|---|
| **ACCEPTANCE CRITERIA** (homologados por Anderson, 29/09/2026) | AC1 campos de dedup (CNPJ, domínio, LinkedIn) presentes **e indexados** em `res.partner`; AC2 IDs canônicos seguem o Data Contract V1.0; AC3 teste de criação e consulta com dado sintético |
| **TEST PLAN** (proposto no card) | Teste do Odoo criando/consultando parceiro com identificadores fortes, em banco **limpo** e descartável. Executado por `scripts/odoo/verificar-res-partner.sh` (contrato → instalação → testes do Odoo → catálogo do PostgreSQL → criação/consulta pelo ORM → desinstalação), item a item |
| **ROLLBACK PLAN** (proposto) | Desinstalar o módulo: os campos são **colunas do módulo** e vão com ele. É o **passo 5** do aceite, medido na mesma execução (colunas e índices `tf_*` somem de `res_partner`) |
| **AFFECTED COMPONENTS** | `odoo/addons/transformativa_sales_ai/models/res_partner.py` (novo), `models/__init__.py` (novo), `__init__.py` (import do pacote de modelos), `tests/test_res_partner_dedup.py` (novo), `tests/__init__.py`, `README.md` do módulo, `scripts/odoo/verificar-res-partner.sh` + `conferir_res_partner_no_contrato.py` + `medir_res_partner.py` (novos), este runbook, `CHANGELOG.md`, `docs/operations/registro-de-execucoes.md`. **No ambiente:** dupla descartável própria (não o `odoo-dev`/`pg-odoo-dev`) |
| **RISK LEVEL** (proposto) | **Médio** — esquema do `res.partner` alterado (5 colunas + 3 índices) num CRM em uso, mas sem dado de negócio em jogo neste card, sem regra automática de merge e reversível por desinstalação; nada em homolog/produção |

**Decisões de implementação** (não mudam contrato; registradas para quem revisa):

| # | Decisão | Porquê |
|---|---|---|
| D1 | Nome do campo = prefixo `tf_` + o nome que o **contrato** usa: `tf_cnpj`, `tf_domain`, `tf_linkedin_url` (fortes) e, literalmente como no `odoo_map`, `tf_company_id` e `tf_priority_score` | o contrato já fixa `res.partner.tf_company_id`; derivar os fortes do mesmo prefixo mantém o espelho distinguível dos campos do Odoo |
| D2 | `tf_company_id` é **Char** guardando o UUID canônico (`organizations.id`), com `@api.constrains` conferindo o **formato** UUID | o Odoo não tem coluna UUID nativa; o contrato §3 exige `id UUID` como identidade, então o espelho não aceita texto livre (a versão do UUID não é imposta) |
| D3 | Os três fortes e `tf_company_id` nascem **indexados** (btree) | o contrato §5 resolve duplicidade por **igualdade de identificador** (CNPJ → domínio → LinkedIn); busca por igualdade sem índice não escala. `tf_company_id` é a chave de ligação com `organizations` |
| D4 | **Não** normaliza o valor, **não** valida CNPJ e **não** cria `UNIQUE` | §5: CNPJ inválido é **armazenado** e o par vai para a fila humana (`REVIEW_REQUIRED`); unicidade no banco impediria o caso que o contrato manda *reportar*; normalização não está definida no contrato V1 (lacuna registrada em §8) |
| D5 | `tf_priority_score` (Float) entra **neste** card | o `canonical_ids.odoo_map` mapeia `"priority score"` para `res.partner.tf_priority_score` **e** `crm.lead.tf_priority_score`, e nenhuma outra card do plano cobre `res.partner` (`E04-T02` é `crm.lead`, `E05-T01` é o modelo `tf.process.opportunity`) |
| D6 | O módulo deste card é transferido para **`/opt/tre/dev/cards/t_adee6ad7/`**, não para `/opt/tre/dev/modulos/` | `E04-T02` e `E05-T01` rodam **em paralelo** na mesma VPS e escrevem no mesmo módulo; usar o diretório compartilhado faria um aceite medir o arquivo do outro |
| D7 | O aceite roda em **dupla descartável própria** (`postgres:16` + `odoo:19.0`, as imagens do par de dev) | herdado do aceite do módulo base (`verificar-modulo-odoo.sh`): o Odoo do dev varre qualquer banco novo da instância `pg-odoo-dev`, e o AC pede banco limpo |

## 2. O que foi entregue

| Campo em `res.partner` | Tipo | Índice | Norma do contrato |
|---|---|---|---|
| `tf_cnpj` | Char | btree | §5, `dedup.strong[0] = cnpj` |
| `tf_domain` | Char | btree | §5, `dedup.strong[1] = domain` |
| `tf_linkedin_url` | Char | btree | §5, `dedup.strong[2] = linkedin_url` |
| `tf_company_id` | Char (UUID) | btree | §3, `canonical_ids.odoo_map["organizations.id"]` |
| `tf_priority_score` | Float | — | §8/§3, `canonical_ids.odoo_map["priority score"]` |

Testes do módulo (`tests/test_res_partner_dedup.py`, 7 testes, tag `post_install`/`-at_install`):
presença no modelo e em `ir.model.fields`; índice declarado no ORM; **índice real** no catálogo do
PostgreSQL (`pg_indexes`); nome/tipo dos campos do `odoo_map`; UUID inválido recusado; criação e
consulta do parceiro sintético pelos quatro identificadores (com negativos); CNPJ inválido
armazenado.

O confronto **dos nomes** com o `docs/data/data_contract_v1.json` congelado é feito fora do Odoo,
por `scripts/odoo/conferir_res_partner_no_contrato.py` (lê o JSON e o código-fonte por AST):
todo `dedup.strong` vira `tf_<nome>` indexado e Char, na ordem de prioridade do contrato, e todo
nome do `odoo_map` que aponta para `res.partner` existe com o tipo do mapeamento — campo ausente,
nome com um caractere diferente ou índice faltando é **FALHOU**.

## 3. Procedimento (na VPS do dev)

```bash
# no container do Hermes: transferir o modulo e os scripts para o diretorio DESTE card (D6)
tar -C odoo/addons -cf - transformativa_sales_ai | ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 \
    'tar -C /opt/tre/dev/cards/t_adee6ad7/modulos -xf -'
tar -C scripts -cf - odoo | ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 \
    'tar -C /opt/tre/dev/cards/t_adee6ad7/scripts -xf -'
ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 \
    'cat > /opt/tre/dev/cards/t_adee6ad7/docs/data/data_contract_v1.json' < docs/data/data_contract_v1.json

# na VPS (sempre a partir de ARQUIVO — nunca por stdin, ver armadilha do `docker compose run`)
#    As TRÊS variáveis de caminho vão juntas (export, não prefixo de um comando só): o modo dente
#    reexecuta o próprio verificador para o baseline e para cada mutação, e quando contrato,
#    conferidor ou medidor não estão em disco ele **recusa de cara** (fail-closed) dizendo o que
#    exportar — antes do conserto do §9 esse erro de ambiente virava "prova de dente" verde.
export TRE_MODULO_DIR=/opt/tre/dev/cards/t_adee6ad7/modulos/transformativa_sales_ai
export TRE_CONTRATO=/opt/tre/dev/cards/t_adee6ad7/docs/data/data_contract_v1.json
export TRE_LOG_DIR=/opt/tre/dev/evidencias/t_adee6ad7/logs-round2
V=/opt/tre/dev/cards/t_adee6ad7/scripts/odoo/verificar-res-partner.sh
bash "$V"                      # aceite completo (contrato + 5 passos)
bash "$V" --apenas-contrato    # so' AC2 (modulo x contrato congelado)
bash "$V" --prova-de-dente     # baseline nao mutado (exige verde) + 3 mutacoes
```

## 4. Aceite — TEST PLAN medido (01/10/2026, VPS `vmi3619453`, rodada final no commit `a569ece`)

`bash verificar-res-partner.sh` →
**`RESULTADO: RES_PARTNER_OK (64 itens, 0 falhas) modulo=transformativa_sales_ai
banco=tre_e04_t01_res_partner imagens=odoo:19.0+postgres:16`**, **exit 0** — medido sobre o módulo
**idêntico (sha256) ao commit `a569ece`** da branch `feature/TRE-W2-E04-T01`.

| Passo | Medição |
|---|---|
| **contrato (AC2)** | `CONTRATO_RES_PARTNER_OK (9 itens, 0 falhas)`: os 3 fortes presentes, Char, **indexados** e na ordem CNPJ → domínio → LinkedIn; `tf_company_id` e `tf_priority_score` com o nome e o tipo do `odoo_map`; todo campo da classe com prefixo `tf_`. Contrato conferido no sha256 `dfc74c95a76f70202af85637bdb73829c7c0e12ca0c4e8cfcb28319b1cf573e4` (versão 1.0) |
| **1 instalação em banco limpo** | banco não existia → criado pelo Odoo do zero; `odoo --init exit 0`; **0** linha ERROR/CRITICAL; `'Modules loaded.'`; `ir_module_module.state = installed`; `latest_version = 19.0.1.0.0` == manifesto |
| **2 testes do Odoo (AC3)** | `--test-enable exit 0`; **`0 failed, 0 error(s) of 13 tests`** (13 ≥ mínimo 13); 0 linha `FAIL:`/`ERROR:`; **7 testes de `TestResPartnerDedup` e 6 de `TestModuloBase` efetivamente rodados** (medido no log — teste que não roda não é teste que passou) |
| **3 catálogo do PostgreSQL (AC1)** | colunas `tf_cnpj`/`tf_domain`/`tf_linkedin_url` = `character varying`, `tf_company_id` = `character varying`, `tf_priority_score` = `double precision`; **índice btree real** (`res_partner__tf_cnpj_index`, `res_partner__tf_domain_index`, `res_partner__tf_linkedin_url_index`); `ir_model_fields.index = true` nos três |
| **4 criação e consulta (AC3)** | pelo `odoo shell` (instrumento independente dos testes): parceiro criado com os 4 identificadores + score 87.5; `MEDICAO_BUSCA` **n=1** por `tf_cnpj`, `tf_domain`, `tf_linkedin_url` e `tf_company_id`; identificador diferente **n=0**; `MEDICAO_ROLLBACK_OK` e **0** parceiro sintético sobrando no banco |
| **5 desinstalação (rollback)** | `DESINSTALACAO_OK`; estado `uninstalled`; **as 5 colunas `tf_*` removidas** de `res_partner`; **0** índice `tf_*` sobrando; **0** resquício do módulo |
| **limpeza / dev intocado** | banco, dupla, rede e diretório de configuração removidos; instância do dev com os **mesmos 4 bancos** antes e depois (`odoo_dev, postgres, template0, template1`); `homolog`/`prod` com **0 arquivo** |

**Identidade do que foi testado:** `odoo:19.0` digest `sha256:77bac5cd1e065210828f34883a7f76740b7373d06dd3a5a55d3eeb31ee2f85cd`;
`postgres:16` digest `sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54`
(as mesmas imagens do par de dev). sha256 do módulo sob teste, igual no repo e na VPS:
`models/res_partner.py 37a93372…`, `models/__init__.py 932f2686…`, `__init__.py f5e43b96…`,
`tests/test_res_partner_dedup.py 76c10063…`, `tests/__init__.py d8c51368…`,
`verificar-res-partner.sh 1bc9e1b8…`, `conferir_res_partner_no_contrato.py`,
`medir_res_partner.py`.

**Logs brutos:** `/opt/tre/dev/evidencias/t_adee6ad7/` — `verificacao-completa-final.out` (64 itens +
`EXIT=0`, a rodada do commit entregue), `prova-de-dente-final.out` (3 provas, exit 0),
`logs-final/{0-contrato.out,1-instalacao.log,2-teste.log,4-medicao-orm.log,5-desinstalacao.log}`,
mais a `logs-dente-final/`; ficam preservadas a **rodada 1** (`verificacao-completa.out`, `logs/`) —
com o defeito F1/F2, ver §6 — e a **rodada 2** (`…-round2.out`), que mediu uma versão do módulo cuja
única diferença era o `README.md` e por isso foi refeita.

## 5. Provas negativas — o aceite tem dentes

`bash verificar-res-partner.sh --prova-de-dente` →
**`RESULTADO: RES_PARTNER_DENTE_OK (3 provas, 0 falhas)`**, **exit 0** — medido depois do conserto do
§9, com o verificador sha256 `4c9056ee…` (`/opt/tre/evid-t_e1f62fae/r-c-dentes.out`). Antes de
qualquer mutação o modo dente roda o **caminho não mutado (baseline)** — contrato + passo 1 + passo 2,
o superconjunto dos caminhos que os 3 dentes medem — e **exige verde**: sem baseline verde ele termina
em `RES_PARTNER_DENTE_FALHOU (baseline nao medido — nenhum dente exercitado)`, `exit 1`. Cada prova
roda numa **cópia** do módulo (o módulo real não é tocado) e só conta como dente quando a saída traz a
**assinatura de falha própria** daquela mutação (o texto que só ela produz) **e** chegou ao passo que
ela mede:

| Prova | Mutação | Caminho medido | Assinatura de falha exigida | Resultado medido |
|---|---|---|---|---|
| **Baseline** | **nenhuma** (módulo real) | contrato + passo 1 + passo 2 (`--apenas-instalacao-e-teste`) | — (tem de medir **verde**; sem isto não existe prova de dente) | `RESULTADO: RES_PARTNER_OK (29 itens, 0 falhas)`, **exit 0** |
| **Dente 1** | `index=True` retirado do `tf_cnpj` (cópia) | contrato (`--apenas-contrato`) | `tf_cnpj NAO esta indexado` | `FALHOU dedup.strong "cnpj" -> tf_cnpj NAO esta indexado` → `CONTRATO_RES_PARTNER_FALHOU (9 itens, 1 falha(s))` e `RES_PARTNER_FALHOU (13 itens, 1 falha(s))`, **exit 1** |
| **Dente 2** | `tf_domain` renomeado para `tf_dominio` (cópia) | contrato (`--apenas-contrato`) | `campo tf_domain AUSENTE em res.partner` | `FALHOU dedup.strong "domain" -> campo tf_domain AUSENTE em res.partner` + ordem divergente → `CONTRATO_RES_PARTNER_FALHOU (9 itens, 2 falha(s))` e `RES_PARTNER_FALHOU (13 itens, 1 falha(s))`, **exit 1** |
| **Dente 3** | teste plantado que falha (`test_99_prova_de_dente`) | testes do Odoo (`--apenas-instalacao-e-teste`) | `1 failed, 0 error(s) of` **e** `rodei 8 teste` | `FALHOU odoo --test-enable exit 1`; `1 failed, 0 error(s) of 13 tests`; `FALHOU 1 linha(s) de teste reprovado(a) … FAIL: TestResPartnerDedup.test_99_prova_de_dente`; `FALHOU rodei 8 teste(s) de TestResPartnerDedup (esperado 7)` → `RES_PARTNER_FALHOU (29 itens, 4 falha(s))`, **exit 1** |

As contagens da revisão independente (`tester`, rodada 1) estão **preservadas**: dente 1 `9/1`,
dente 2 `9/2`, dente 3 `1 failed` + suite `8 != 7`.

Provas negativas adicionais do conferidor de contrato (rodadas fora do verificador, mesma classe de
mutação): contrato com um forte a mais (`telefone`) → `FALHOU dedup.strong "telefone" -> campo
tf_telefone AUSENTE`; módulo real → `CONTRATO_RES_PARTNER_OK`.

## 6. Defeitos encontrados nesta execução (e conserto)

Todos achados **executando** o aceite, nenhum por leitura — e cada conserto foi remedido:

1. **F1 — `ir_model.fields.index` é booleano no Odoo 19 (aceite e teste esperavam `'btree'`).** A
   rodada 1 deu `FALHOU runner do Odoo: 1 failed, 0 error(s) of 13 tests` e `FALHOU
   ir_model_fields.index de res.partner.tf_cnpj: 't' (esperado btree)`. O log do runner mostrou
   `AssertionError: True != 'btree'`. Conserto: o teste passa a exigir **índice declarado** no ORM
   (`is True`) e o *tipo* btree continua sendo provado no catálogo (`pg_indexes`), onde ele é
   realmente medido; o item do verificador passou a aceitar `t`. Medido depois: `0 failed … of 13
   tests` e `indice btree real em res_partner(tf_cnpj): res_partner__tf_cnpj_index`.
2. **F2 — item "nenhuma linha de teste FAIL:/ERROR:" imprimia OK com teste reprovado.** O `grep`
   estava ancorado no início da linha (`^FAIL|^ERROR`), mas o Odoo 19 escreve
   `<hora> <pid> ERROR <banco> <módulo>: FAIL: TestX.test_y` — e na rodada 1 o item deu OK **com um
   teste reprovado** (mesma classe do defeito `D04` do verificador de estrutura: aceite falso).
   Prova da diferença no próprio log da rodada 1: padrão antigo → **0** casamentos, padrão novo →
   **1**. Conserto: `grep -cE '(^| )(FAIL|ERROR): [A-Za-z_]'`, com as linhas reprovadas impressas.
   Medido depois: 0 no aceite verde e **1** no dente 3 (o dente do teste plantado é o que prova que
   o item consertado tem dente).
3. **F3 — o marcador `'At least one test failed when loading the modules.'` é vazio no Odoo 19.**
   Ele **não** aparece nem quando um teste reprova (medido na rodada 1). O item ficou como ausência
   (não pode dar falso OK), e os dentes reais do passo 2 são o **exit code**, o **relatório do
   runner** e as **linhas `FAIL:`** — os três reprovam no dente 3.

Nota de método: F2 só apareceu porque o aceite foi **reexecutado depois de cada edição**, e porque o
dente 3 planta uma falha de verdade — uma bateria que só roda no caso verde não pega esse tipo de
falso OK.

## 7. Rollback

- **Nível 1 — desinstalar o módulo (rollback declarado no card):** é o **passo 5** do aceite, medido
  (`DESINSTALACAO_OK`, estado `uninstalled`, as 5 colunas `tf_*` e todos os índices `tf_*` removidos
  de `res_partner`, 0 resquício). Em qualquer base onde o módulo venha a ser instalado:

```bash
docker run --rm -i --network <rede> -v <conf>:/etc/odoo/odoo.conf:ro \
    -v <modulo>:/mnt/extra-addons/transformativa_sales_ai:ro -e TRE_MODULO=transformativa_sales_ai \
    --entrypoint odoo odoo:19.0 shell -d <banco> --no-http < scripts/odoo/desinstalar_modulo.py
```

- **Nível 2 — reverter o card:** `git revert` do commit do card; o verificador limpa a dupla
  descartável e confere a instância do dev antes × depois. Dado já preenchido nos campos se perde com
  a desinstalação (é coluna do módulo) — o que o card registra é o **procedimento**, não um backup.

## 8. Limites e pendências declaradas (não são deste card)

- **Normalização do identificador** (dígitos do CNPJ, domínio em minúsculas) **não** entra: o
  contrato V1 não define regra de normalização e o motor de dedup compara o valor do identificador
  como ele chega. Fica registrado como lacuna para o W1 decidir (mesma forma da lacuna D4 do runbook
  de dedup).
- **Validação de CNPJ** e **unicidade** ficam de fora de propósito (D4): o contrato manda
  *detectar e reportar*, não recusar.
- **Views e ACL (“quem não pode ver, não vê”)** são de `TRE-W2-E06-T01` e `TRE-W2-E07-T01`; este card
  não cria view, menu nem grupo.
- **`tf_priority_score` não tem índice nem versão de score**: o valor é o corrente do funil; a
  série histórica e a versão (`score_version`) vivem em `scores`, no `sales_intelligence` (§8 do
  contrato).
- **Publicação do módulo em `/opt/tre/repo`** (cópia operacional lida pelo `odoo-dev`) segue pendente
  e é herdada de `TRE-W2-E03-T01`: enquanto as linhas não se encontrarem e o card não for publicado,
  o módulo é medido por este caminho (dupla descartável + cópia do card na VPS) e não aparece no
  `odoo-dev`.
- **Hotspot declarado** (cards paralelos na mesma base): `__init__.py` do módulo, `models/__init__.py`,
  `tests/__init__.py` e `README.md` são os **pontos de contato** com `E04-T02` (campos de `crm.lead`)
  e `E05-T01` (`tf.process.opportunity`). Cada card acrescenta **uma linha** nesses arquivos e o
  arquivo do seu modelo; a integração na `develop` deve juntar as linhas, não reescrever os arquivos.
- **Verificação independente** (estágio 6) é do perfil `tester`; **ratificação da versão do Odoo
  (19.0)** e **homologação** (estágio 7) seguem com o Anderson. Este runbook entrega evidência, não
  aprovação.

## 9. Defeito D1 — o modo `--prova-de-dente` dava verde sem medir (achado da revisão independente)

**Achado** pela revisão independente do card irmão `TRE-W2-E04-T02` (perfil `tester`, 01/10/2026,
observação O6: *"o harness de dente do IRMÃO E04-T01 (`verificar-res-partner.sh`) usa o mesmo critério
('qualquer `RES_PARTNER_FALHOU` = dente OK') e cai na mesma classe"*), registrado no card de defeito
`t_e1f62fae` (registro **retroativo**: a origem já estava `done`). Reproduzido por mim **antes** de
consertar, com o artefato como aprovado (sha256 `1bc9e1b8ab34b5a4397862c0678b4c217b65bdb5558f362ee4022b856742abfd`),
no comando exato do card:

```
$ TRE_CARTAO_DIR=/opt/tre/nao-existe TRE_LOG_DIR=<dir meu> bash verificar-res-partner.sh --prova-de-dente
FALHOU modulo ausente em /tmp/dente-e04t01-XXXXXX/m1 (__manifest__.py nao encontrado)
RESULTADO: RES_PARTNER_FALHOU (6 itens, 1 falha(s)) …
OK    dente 1: campo de dedup sem indice reprova o aceite
… (dentes 2 e 3 iguais, todos morrendo na GUARDA) …
RESULTADO: RES_PARTNER_DENTE_OK (3 provas, 0 falhas) modulo=transformativa_sales_ai
EXIT_A=0
```

**Causa raiz:** o julgamento de cada dente era `printf '%s' "$Dx" | grep -q 'RESULTADO: RES_PARTNER_FALHOU'`
— aceitava **qualquer** reprovação, inclusive a que vem das **guardas do ambiente**, antes de
qualquer medição. Não havia **baseline** (caminho não mutado medido verde antes das mutações) nem
**assinatura de falha** por dente. O aceite em si sempre foi fail-**CLOSED** (o que falhava aberto era
só o modo dente — e é ele que o TEST PLAN do card promete).

**Conserto** (mesmo padrão já medido e aprovado no `TRE-W2-E04-T02`, card `t_d3bd6660` rodada 2 —
reusado, não inventado), todo ele em `scripts/odoo/verificar-res-partner.sh`:

1. **baseline obrigatório:** antes de qualquer mutação o modo dente roda o caminho NAO mutado
   (`--apenas-instalacao-e-teste`: contrato + passo 1 + passo 2, superconjunto dos caminhos dos 3
   dentes) e **exige verde**; sem baseline verde ele termina em
   `RES_PARTNER_DENTE_FALHOU (baseline nao medido — nenhum dente exercitado)`, `exit 1`;
2. **assinatura de falha por dente** (o texto que só aquela mutação produz; mais de uma exigida,
   separadas por `;;`) em vez de "qualquer FALHOU";
3. dente cuja saída **abortou numa guarda** ou **não chegou ao passo** que ele mede é **reprovado**;
4. as guardas de **arquivo** (contrato/conferidor/medidor) passaram a ser as **primeiras** — o comando
   recusa de cara, sem subir nada, dizendo o que exportar;
5. as provas de dente passaram a reexecutar o próprio script por caminho **absoluto** (`SELF`, com
   `bash "$SELF"`) em vez de `"$0"` — mesma classe do defeito D04 do verificador de estrutura.

**Medido depois, na VPS do dev** (evidência bruta: `/opt/tre/evid-t_e1f62fae/`, módulo sob teste
**idêntico por sha256** ao commit `3b0eac3` — `models/res_partner.py 37a93372…`,
`tests/test_res_partner_dedup.py 76c10063…`, 9 arquivos; só o verificador mudou, `4c9056ee…`):

| Bloco | Comando | Resultado | exit |
|---|---|---|---|
| **A — antes** | artefato `1bc9e1b8…`, `TRE_CARTAO_DIR=/opt/tre/nao-existe` | `RES_PARTNER_DENTE_OK (3 provas, 0 falhas)` — verde **sem medir** | **0** (`r-a-antes.out`) |
| **B — depois** | artefato novo, **mesmo** comando do card | `FALHOU artefato ausente: /opt/tre/nao-existe/docs/data/data_contract_v1.json — sem contrato nao ha confronto: exporte TRE_CONTRATO=…` + `RESULTADO: RES_PARTNER_DENTE_FALHOU (baseline nao medido — nenhum dente exercitado)` | **1** (`r-b-nao-resolvido.out`) |
| **B2 — depois** | artefatos resolvem, `TRE_MODULO_DIR` inexistente | `FALHOU modulo ausente em /opt/tre/nao-existe/modulos/… (__manifest__.py nao encontrado)` → `RES_PARTNER_FALHOU (11 itens, 1 falha(s))` → `RES_PARTNER_DENTE_FALHOU (baseline nao medido …)` | **1** (`r-b2-modulo-ausente.out`) |
| **C — depois** | ambiente completo (`TRE_MODULO_DIR` + `TRE_CONTRATO` + `TRE_LOG_DIR`) | baseline `RES_PARTNER_OK (29 itens, 0 falhas)` + dentes `13/1`, `13/1`, `29/4` (assinaturas do §5) → `RES_PARTNER_DENTE_OK (3 provas, 0 falhas)` | **0** (`r-c-dentes.out`) |
| **D — aceite** | `bash verificar-res-partner.sh` | `RES_PARTNER_OK (64 itens, 0 falhas)` (64 linhas OK, 0 FALHOU; runner `0 failed, 0 error(s) of 13 tests`) | **0** (`r-d-aceite.out`) |
| **E — regressão** | `verificar-modulo-odoo.sh` (E03-T01) contra este módulo | `MODULO_ODOO_OK (51 itens, 0 falhas)` | **0** (`r-e-regressao.out`) |
| **F — fail-closed** | aceite completo com o contrato fora de disco | `RES_PARTNER_FALHOU (1 itens, 1 falha(s))` na guarda de arquivo, **sem subir nada** | **1** (`r-f-aceite-contrato-ausente.out`) |
| **G — dente do dente** | `confere_dente` extraído do artefato (`controle-confere_dente.frag`, sha256 `5daa75a5…`, 26 linhas) alimentado com saídas **reais** | g1 saída real do dente 1 → **OK**; g2 mesma saída com a assinatura de **outro** dente → reprovado; g3 a saída do **falso-verde** do artefato antigo (aborto de guarda) → reprovado; g4 saída real **sem** o passo medido → reprovado; g5 saída **verde** do aceite → reprovado; g6 saída real do dente 3 com a assinatura do dente 1 → reprovado → `CONTROLE_JULGAMENTO_OK (5 provas negativas, 0 falso-OK)` | **0** (`r-g-controle-julgamento.out`) |

**Verificadores do projeto** (worktree do commit, depois das edições): `verificar_estrutura.sh` →
`PASS (0 falhas)` RC=0 · `secret_scan.sh` → `PASS` RC=0 · `verificar_papeis.sh` → `PASS (0 falhas)`
RC=0 · `verificar_contrato_dados.py` → `PASS (26 itens, 0 falhas)` RC=0.

**O que não foi tocado (medido ao fim da bateria):** a instância do dev com **os mesmos 4 bancos**
(`odoo_dev, postgres, template0, template1`); `/opt/tre/{homolog,prod}` com **0 arquivo**; **0** arquivo
novo em `/opt/tre/repo`; **nenhum** container/rede `e04t01-*` residual; o módulo sob teste **não** foi
editado (só o verificador, o runbook, o `CHANGELOG` e o registro de execuções).

**Escopo do conserto:** só o artefato deste card. Fica **fora** daqui o `grep -cE '^(FAIL|ERROR): '`
herdado em `scripts/odoo/verificar-modulo-odoo.sh` (E03-T01) — já tem card próprio da classe
(`t_578a4e4d`, consertado) —, views/ACL (E06/E07) e a publicação na cópia operacional.
