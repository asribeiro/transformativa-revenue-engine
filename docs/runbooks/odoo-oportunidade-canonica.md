# Runbook — oportunidade canônica `tf.process.opportunity` (lado Odoo)

**Card:** `TRE-W2-E05-T01` (`t_9c91ecce`, perfil `desenvolvedor`) · **Status:** em execução
**Depends on:** `TRE-W2-E03-T01` (base do módulo `transformativa_sales_ai`, `t_c536ce86`)
**Branch:** `feature/TRE-W2-E05-T01` (local — `completion_contract: local-only`)
**Máquina:** VPS Contabo `vmi3619453` (169.58.24.102) · **Ambiente:** dev (homolog/prod **não**
provisionados — ADR-005)
**Artefatos versionados:** `odoo/addons/transformativa_sales_ai/models/**`, `tests/**`,
`__init__.py`, `__manifest__.py`, `README.md`, este runbook, `CHANGELOG.md`,
`docs/operations/registro-de-execucoes.md`, `scripts/verificar_estrutura.sh`

---

## 1. Campos do card (doc 11 §2) e decisões registradas

| Campo | O que foi definido e registrado |
|---|---|
| **ACCEPTANCE CRITERIA** (homologados por Anderson, 29/09/2026) | AC1 modelo `tf.process.opportunity` criado com os campos do contrato; AC2 a oportunidade canônica vive no Odoo (regra do contrato), com vínculo a `res.partner`; AC3 teste de criação, consulta e relação com parceiro |
| **TEST PLAN** (proposto) | Testes `post_install` do Odoo (9 novos + os 6 herdados do E03) medindo criação, consulta e relação com parceiro contra o `crm` real, mais o aceite de 4 passos do módulo: instalação em banco limpo → `--test-enable` → desinstalação → reinstalação, em dupla descartável própria. Evidência = logs do runner + leitura no banco |
| **ROLLBACK PLAN** (proposto) | Desinstalar o módulo — é o **passo 3** do próprio aceite, que roda com o modelo instalado (§6). Reverter o card: `git revert` (o módulo não está instalado em ambiente persistente) |
| **AFFECTED COMPONENTS** | `odoo/addons/transformativa_sales_ai/{models/**,tests/**,__init__.py,__manifest__.py,README.md}`, `docs/runbooks/odoo-oportunidade-canonica.md`, `CHANGELOG.md`, `docs/operations/registro-de-execucoes.md`, `scripts/verificar_estrutura.sh`. **No ambiente:** dupla descartável própria na VPS do dev |
| **RISK LEVEL** (proposto) | **Médio** — modelo novo com constraint única e FK `restrict` para `res.partner`; reversível por desinstalação; nada em homolog/produção (ADR-005) |

**Decisões de implementação** (nenhuma regra nova de contrato — o contrato manda a oportunidade
canônica viver no Odoo e lista os fatos que são dele):

| # | Decisão | Porquê |
|---|---|---|
| D1 | a identidade canônica é o **UUID** (`tf_uuid`: obrigatório, único, imutável) | Data Contract V1.0 §3 — `id UUID` é a chave canônica do contrato e ID de sistema externo nunca substitui o UUID. No Odoo a PK é inteira, então o UUID canônico vive em coluna própria; é por ele que o lado PostgreSQL/n8n referencia a oportunidade (`recommendations.opportunity_id`, UUID sem FK) |
| D2 | UUID **do produtor é preservado**; ausente gera v4; valor que não é UUID é **recusado** | §3 — o ID nasce no produtor do fato, antes da chamada externa. Quando o fato nasce no Odoo (usuário/CRM), o Odoo é o produtor; quando chega de `OPPORTUNITY_RECOMMENDED`, o valor recebido é preservado. Nada é "consertado" em silêncio (§5 do contrato, mesma regra da dedup) |
| D3 | `tf_uuid` **imutável** (write com valor diferente levanta; o mesmo valor é idempotente) | chave canônica é identidade: trocá-la mudaria o dono do fato sem evento nenhum |
| D4 | `partner_id` → `res.partner`, `required`, `ondelete='restrict'` | §2 (`contato comercial: Odoo`) + AC2 (vínculo a `res.partner`); oportunidade órfã não é oportunidade, logo a FK é real e não decorativa |
| D5 | fatos do Odoo: `stage_id` (`crm.stage`), `expected_revenue` (valor), `lost_reason_id` (`crm.lost.reason`) | §2 (`estágio`, `valor`, `motivo de perda`: Odoo) e §7.1 (funil). O won/lost do funil **não** é inventado como vocabulário novo: vem de `crm.stage.is_won`, a mesma regra que o core usa no `won_status` de `crm.lead` (medido na imagem: `crm_stage.py:27 is_won`) |
| D6 | **não** entra `tf_priority_score` | §3 mapeia `priority score` para `res.partner.tf_priority_score` / `crm.lead.tf_priority_score` — cards E04-T01/T02, não a oportunidade |
| D7 | **não** entra FK para `crm.lead` | §3 mapeia a oportunidade canônica para `crm.lead.tf_opportunity_id`, do lado do lead (card E04-T02) |
| D8 | **não** há `data` novo (sem view, sem ACL) | views são `TRE-W2-E06-T01` e ACLs são `TRE-W2-E07-T01`; o card não pede nenhuma das duas |
| D9 | versão do módulo **mantida** em `19.0.1.0.0` | o módulo não está instalado em nenhum ambiente persistente (runbook do E03 §9: nem no `odoo_dev`, nem publicado em `/opt/tre/repo`), então não há upgrade a sinalizar; o verificador do aceite confere versão/série item a item (dente 1 muta o manifesto e o aceite **reprova**); e três cards da mesma onda (E04-T01, E04-T02, E05-T01) editam o mesmo manifesto em paralelo |
| D10 | aceite roda em **diretório de módulo próprio** na VPS (`/opt/tre/dev/modulos-e05t01/`) e com `TRE_BANCO`/`TRE_LOG_DIR` próprios | o E04-T01/T02 usam `/opt/tre/dev/modulos/transformativa_sales_ai` no mesmo momento; apontar o aceite para um diretório compartilhado mediria o módulo do vizinho |

## 2. O que o card entrega (e o que ele não entrega)

Entrega o **modelo canônico da oportunidade do lado Odoo** — `tf.process.opportunity` — com os
campos do contrato, o vínculo com `res.partner` e a identidade canônica em UUID, mais os testes
que provam criação, consulta e relação com parceiro. É a primeira carga de conteúdo no pacote
`models/` do módulo criado pelo E03 (que sobe a linha `from . import models` no `__init__.py`).

Não entrega: campos de `res.partner`/`crm.lead` (E04-T01/T02), views (E06), ACLs (E07) nem a
API controlada (W3-E01). Nenhuma etapa, vocabulário ou regra foi inventada sem o contrato.

## 3. Procedimento (na VPS do dev)

```bash
# no container do Hermes: transferir o módulo (tar por ssh, sem scp) para um diretorio PROPRIO
tar -C odoo/addons -cf - transformativa_sales_ai | ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 \
    'mkdir -p /opt/tre/dev/modulos-e05t01 && tar -C /opt/tre/dev/modulos-e05t01 -xf -'
sha256sum -c <(cd odoo/addons/transformativa_sales_ai && find . -type f | LC_ALL=C sort | xargs sha256sum)  # e do outro lado

# na VPS (sempre a partir de ARQUIVO — nunca por stdin, ver armadilha do `docker compose run`)
TRE_MODULO_DIR=/opt/tre/dev/modulos-e05t01/transformativa_sales_ai \
TRE_BANCO=tre_e05_t01_oportunidade \
TRE_LOG_DIR=/opt/tre/dev/evidencias/t_9c91ecce/logs \
    bash /opt/tre/dev/scripts/odoo/verificar-modulo-odoo.sh
```

O verificador (herdado do E03, `scripts/odoo/verificar-modulo-odoo.sh`) sobe a própria dupla
descartável (`postgres:16` + `odoo:19.0`, imagens e digests do par de dev), instala o módulo em
banco limpo, roda o `--test-enable`, desinstala pelo ORM e reinstala — e confere a instância do
dev antes/depois.

**Estado medido da imagem antes de escrever o modelo** (sonda read-only, §8.1): `crm.stage` tem
`is_won`/`sequence`/`fold`; `crm.lost.reason` existe (`name`, `active`); `won_status` de
`crm.lead` é **calculado** a partir do estágio; `models.Constraint` é a API de constraint do
Odoo 19 (194 arquivos do core a usam; `_sql_constraints` aparece em 1) e `fields.Uuid` **não**
existe nesta versão (a identidade canônica é `Char(36)`).

## 4. Aceite — TEST PLAN medido (01/10/2026, VPS `vmi3619453`)

Bateria: `bash /opt/tre/dev/baterias/bateria-e05t01.sh` (só na VPS — orquestração, não é artefato do
repo; sha256 `eab932ef…`) → **`EXIT_BATERIA=0`**, três alvos verdes:

```
alvo manifesto -> EXIT=0  RESULTADO: MODULO_ODOO_OK (19 itens, 0 falhas)
alvo aceite    -> EXIT=0  RESULTADO: MODULO_ODOO_OK (51 itens, 0 falhas) modulo=transformativa_sales_ai banco=tre_e05_t01_oportunidade imagens=odoo:19.0+postgres:16
alvo dentes    -> EXIT=0  RESULTADO: MODULO_ODOO_DENTE_OK (2 provas, 0 falhas)
```

O aceite é o mesmo verificador de 4 passos herdado do E03 (`scripts/odoo/verificar-modulo-odoo.sh`,
sha256 `72d00aa1…` — o mesmo do E03), apontado para o módulo **deste** card com
`TRE_MODULO_DIR=/opt/tre/dev/modulos-e05t01/transformativa_sales_ai`,
`TRE_BANCO=tre_e05_t01_oportunidade` e `TRE_LOG_DIR=…/logs-aceite`:

| Passo | Medição |
|---|---|
| **guardas** | docker responde; as duas imagens presentes com os digests do par de dev (`odoo:19.0` `sha256:77bac5cd…`, `postgres:16` `sha256:1a6ab3f5…`); banco descartável com nome seguro; módulo em disco (sha256 dos 9 arquivos listados na bateria, **idênticos aos do repo** — mesma árvore, medido arquivo a arquivo antes e depois da transferência) |
| **1 instalação em banco limpo** | banco **não existia** → criado do zero pelo Odoo; `odoo --init exit 0`; **0 ERROR/CRITICAL** no log; `'Modules loaded.'`; `ir_module_module.state=installed`; `latest_version=19.0.1.0.0` == manifesto; `license=LGPL-3`; dependências `base crm` == declaradas e todas instaladas; 0 módulo pendurado |
| **2 teste do Odoo** | `odoo -u transformativa_sales_ai --test-enable exit 0`; **`0 failed, 0 error(s) of 15 tests when loading database 'tre_e05_t01_oportunidade'`** — as 15 são os 6 herdados do E03 (`TestModuloBase`) + os **9 deste card** (`TestOportunidadeCanonica.test_01..test_09`); **0 linha `FAIL:`/`ERROR:`**; 0 ERROR/CRITICAL no log; módulo segue `installed`. O teste `test_06` imprime a prova da relação com o parceiro no log: `recusa da exclusao do parceiro = ForeignKeyViolation: update or delete on table "res_partner" violates foreign key constraint "tf_process_opportunity_partner_id_fkey" on table "tf_process_opportunity"` |
| **3 desinstalação (rollback declarado)** | `odoo shell` + ORM → `DESINSTALACAO_OK estado_antes=installed estado_depois=uninstalled`; `state=uninstalled`; **0 resquício** em `ir_model_data`/`ir_ui_view`/`ir_model_fields` e **0 tabela** com prefixo do módulo (`tf_process_opportunity` sumiu com o modelo) |
| **4 reinstalação (idempotência)** | `odoo --init` de novo **exit 0**, 0 ERROR/CRITICAL, `state=installed` com `latest_version=19.0.1.0.0` |
| **limpeza / dev intocado** | banco, postgres, rede e diretório de configuração descartáveis removidos; **instância do dev com os mesmos 4 bancos antes e depois** (`odoo_dev, postgres, template0, template1`); `/opt/tre/{homolog,prod}` com **0 arquivo** antes e depois (ADR-005) |

**Aviso esperado (não é falha):** o `ir_module_module`/`odoo.modules.loading` registra
`WARNING … The models ['tf.process.opportunity'] have no access rules` — o modelo nasce sem ACL por
decisão D8 (as regras de acesso são do card `TRE-W2-E07-T01`). É `WARNING`, não `ERROR/CRITICAL`, e
por isso não aparece nos contadores acima.

**Logs brutos:** `/opt/tre/dev/evidencias/t_9c91ecce/` — `bateria.out` (bateria completa com
`EXIT_*=0` por alvo), `manifesto.out`, `aceite.out` (51 itens), `dentes.out` (dentes herdados do
E03) e `logs-aceite/{1-instalacao,2-teste,3-desinstalacao,4-reinstalacao}.log`.

## 5. Provas negativas — os dentes

**5.1 Dentes herdados do E03** (`--prova-de-dente`, 2 provas, cada uma em **cópia** do módulo):
`RESULTADO: MODULO_ODOO_DENTE_OK (2 provas, 0 falhas)`, exit 0 — versão do manifesto mutada para
`18.0.1.0.0` → `MODULO_ODOO_FALHOU (19 itens, 3 falhas)` / ticket de versão e série; teste
plantado que falha (`1 failed … of 16 tests`) → `MODULO_ODOO_FALHOU (36 itens, 2 falhas)` com
`odoo --test-enable exit 1`. Isso prova os itens de manifesto e de `--test-enable` — **não** prova
que os testes deste card reprovam quando o modelo quebra.

**5.2 Dentes próprios deste card** (`/opt/tre/dev/baterias/dentes-e05t01.sh`, sha256 `fce2fa5d…`,
só na VPS): cada dente muta **uma cópia** do módulo em diretório próprio (o módulo real não é
tocado), roda `--apenas-instalacao-e-teste` em banco próprio e exige **reprovação do aceite com o
teste esperado caindo** (não basta reprovar):

| Dente | Mutação (na cópia) | Esperado e medido |
|---|---|---|
| **A** | `tf_uuid` deixa de ser `required` | `test_01_modelo_criado_com_os_campos_do_contrato` cai → aceite `FALHOU`, exit 1 |
| **B** | guarda de imutabilidade do UUID neutralizada (`if 'tf_uuid' in vals:` → `if False:`) | `test_08_uuid_canonico_e_imutavel` cai → aceite `FALHOU`, exit 1 |
| **C** | constraint única trocada (`unique (tf_uuid)` → `unique (id)`) | `test_07_uuid_canonico_e_unico` cai → aceite `FALHOU`, exit 1 |

Resultado medido (log bruto `dentes-proprios.out`): **3/3 `DENTE_OK`**.

## 5.3 Resultado medido dos dentes próprios

```
dente_a: EXIT=1  MODULO_ODOO_FALHOU (36 itens, 2 falhas)  runner: 1 failed, 0 error(s) of 15 tests
         FAIL: TestOportunidadeCanonica.test_01_modelo_criado_com_os_campos_do_contrato
         AssertionError: False is not true : tf_uuid tem de ser obrigatorio (chave canonica)
         -> DENTE_OK dente_a
dente_b: EXIT=1  MODULO_ODOO_FALHOU (36 itens, 2 falhas)  runner: 1 failed, 0 error(s) of 15 tests
         FAIL: TestOportunidadeCanonica.test_08_uuid_canonico_e_imutavel
         AssertionError: ValidationError not raised
         -> DENTE_OK dente_b
dente_c: EXIT=1  MODULO_ODOO_FALHOU (36 itens, 2 falhas)  runner: 1 failed, 0 error(s) of 15 tests
         FAIL: TestOportunidadeCanonica.test_07_uuid_canonico_e_unico
         AssertionError: IntegrityError not raised
         -> DENTE_OK dente_c
```

Nas três, o aceite reprovou **exatamente** com o teste declarado caindo, cada mutação numa cópia
própria (`/opt/tre/dev/dentes-e05t01/<dente>/transformativa_sales_ai`, removida ao fim: sobra 0) e em
banco próprio (`tre_e05_t01_dente_{a,b,c}`). Os fingerprints das cópias mutadas ficam nos primeiros
itens do log de cada dente (sha256 de `models/tf_process_opportunity.py` diferente do real) e o módulo
real **não** foi tocado — o aceite completo voltou a passar depois (`logs-aceite/`).

## 5.4 Por que os dentes próprios eram necessários

Os dentes herdados do E03 provam os itens de **manifesto** e de **`--test-enable`**, não o
comportamento que este card entrega. Sem os dentes A/B/C, um modelo que perdesse `required`, a
imutabilidade ou a unicidade do UUID continuaria com o aceite verde — os três itens são exatamente os
que o contrato exige (§3) e os únicos que este card tem para oferecer como barreira.


## 6. Rollback

**Nível 1 — desinstalar o módulo (rollback declarado no card).** É o **passo 3** do aceite e roda
com o modelo instalado: `odoo shell` + ORM → `DESINSTALACAO_OK`, estado `uninstalled`, sem
resquício em `ir_model_data`/`ir_ui_view`/`ir_model_fields` e sem tabela com prefixo do módulo
(`tf_process_opportunity`). O passo 4 (reinstalação) prova que voltar atrás e reinstalar é
idempotente.

**Nível 2 — reverter o card:** `git revert` do commit; nada fica no ambiente (o aceite usa dupla
descartável e o verificador confere a instância do dev antes/depois).

## 7. Isolamento do aceite

O aceite roda em **dupla descartável própria** (imagens do par de dev, rede própria, senha gerada
na hora em arquivo 600 dono uid 100) e **não** no `odoo-dev`/`pg-odoo-dev`: o AC pede o modelo
criado e medido, e o `odoo_dev` carrega o funil do `TRE-W2-E02-T01`; além disso o Odoo do dev
abre sessão em qualquer banco novo da instância `pg-odoo-dev` (medido no E03).

O **diretório do módulo é próprio deste card** (`/opt/tre/dev/modulos-e05t01/`), porque E04-T01 e
E04-T02 editam o mesmo módulo em paralelo no caminho compartilhado do E03 — medir pelo caminho
compartilhado mediria o módulo do vizinho, não o deste card (decisão D10).

## 8. Defeitos encontrados nesta execução (e conserto)

Todos achados **executando** (nenhum por leitura) e cada conserto remedido com a bateria inteira:

1. **Teste com `assertRaises` de tupla derruba o runner do Odoo 19.** A primeira versão de
   `test_06_relacao_com_parceiro_e_restricao_de_exclusao` usava
   `self.assertRaises((IntegrityError, UserError))`. O `TransactionCase.assertRaises` do Odoo 19 faz
   `issubclass(exception, AccessError)` e morre com `TypeError: issubclass() arg 1 must be a class` —
   o teste não reprovava, **errava**. Medido (primeira rodada do aceite):
   `FALHOU odoo --test-enable exit 1` + `FALHOU runner do Odoo: 0 failed, 1 error(s) of 15 tests`,
   `RESULTADO: MODULO_ODOO_FALHOU (51 itens, 2 falha(s))`, exit 1 — **o próprio aceite pegou o defeito**
   (é para isso que ele existe). Conserto: uma classe só, e a classe certa foi **medida**, não
   adivinhada — no Odoo 19 o core não engole a violação de FK `restrict` e ela chega como
   `psycopg2.errors.ForeignKeyViolation` (subclasse de `IntegrityError`); o teste passou a imprimir a
   classe no log (`test_06: recusa da exclusao do parceiro = ForeignKeyViolation: … fkey
   "tf_process_opportunity_partner_id_fkey"`). Medido depois: `0 failed, 0 error(s) of 15 tests`.

2. **Prova de dente não tem diretório de log próprio** — o modo `--prova-de-dente` herda o
   `TRE_LOG_DIR` do chamador e os sub-runs escrevem os mesmos nomes de arquivo de passo, apagando os
   logs do aceite. Medido: depois de encadear aceite + dentes com o mesmo `TRE_LOG_DIR`, o
   `logs/2-teste.log` passou a ser o do **dente** (`banco tre_e05_t01_oportunidade_dente, 1 failed`),
   não o do aceite verde. Conserto **nesta execução**: um diretório de log por alvo
   (`logs-aceite/` e `logs-dente/`, além de `logs-<dente>/` nas provas próprias). **Registrado como
   defeito do artefato do E03**: card **`t_5c4fc7ac`** (`TRE-W2-E03-T01-D02`), pré-requisito do card de
   origem `t_c536ce86`.

3. **Item de aceite código morto no Odoo 19** — o item "nenhuma linha de teste 'FAIL:'/'ERROR:' no
   log" (`grep -cE '^(FAIL|ERROR): '`) **passa mesmo com teste reprovado**, porque no Odoo 19 a linha de
   reprovação vem prefixada (`data pid NÍVEL banco logger: FAIL: TestX.test_…`). Medido no dente `dente_a`
   (cópia com `tf_uuid` sem `required`): o aceite reprovou, o log tinha a linha `FAIL:`, e o item
   imprimiu `OK nenhuma linha de teste 'FAIL:'/'ERROR:' no log`. Conserto registrado como defeito do
   artefato do E03: card **`t_578a4e4d`** (`TRE-W2-E03-T01-D01`), pré-requisito do card de origem
   `t_c536ce86`. Nesta execução o impacto ficou contido porque os itens de exit code e do relatório do
   runner pegaram a reprovação.

4. **A guarda de nome de banco recusa maiúscula** — a primeira rodada das provas próprias morreu nas
   guardas (`FALHOU nome de banco fora do padrao descartavel (^tre_[a-z0-9_]+$): tre_e05_t01_denteA`,
   `MODULO_ODOO_FALHOU (6 itens, 1 falha(s))`) por causa do sufixo `denteA`. Conserto: nomes em
   minúscula (`dente_a/b/c`). É a guarda funcionando — o registro fica como prova de que ela tem dente.

5. **Meu próprio verificador de dentes usava o mesmo padrão morto do defeito 3.** Ele conferia o
   veredito com `grep -E '^(FAIL|ERROR): '` e imprimia `DENTE_FALHOU` num dente que havia funcionado
   (a linha real é prefixada). Conserto: `grep "FAIL: .*<teste-esperado>"` — o dente só conta como dente
   se **o teste declarado** cair. Medido depois: 3/3 `DENTE_OK`.

**Nota de método:** os itens 2, 3 e 5 são a mesma lição em três lugares diferentes — padrão de leitura
de log ancorado em formato de terceiro. É por isso que os defeitos 2 e 3 viraram card (e não só um
conserto local): o próximo a escrever um item de aceite herda o registro.

## 9. Pendências declaradas (não são deste card)

- **Defeitos abertos nesta execução** (registrados como pré-requisitos do card de origem `t_c536ce86`,
  no modo retroativo — a origem já estava `done`): **`t_578a4e4d`** (`TRE-W2-E03-T01-D01`, item de aceite
  código morto no Odoo 19) e **`t_5c4fc7ac`** (`TRE-W2-E03-T01-D02`, prova de dente sobrescrevendo os logs
  do aceite). Nenhum dos dois bloqueia os critérios deste card — os itens que provam o aceite do E05
  (exit code, relatório do runner, estado lido no banco, desinstalação) são independentes deles —, mas os
  dois são conserto de **artefato do E03** e por isso são cards próprios, não edição silenciosa daqui.
- **Views (E06) e ACLs (E07)**: o modelo nasce sem as duas, de propósito. Sem regra de acesso, o
  Odoo registra aviso de "modelo sem regra de acesso" e só usuários com privilégio de sistema
  alcançam o modelo — é o estado esperado até o card E07 (que é dono da carteira × tenant).
- **`crm.lead.tf_opportunity_id`**: é do card E04-T02. Este card entrega a entidade de destino e
  documenta o vínculo; nada do lado do lead foi tocado aqui.
- **Publicação do módulo na cópia operacional `/opt/tre/repo`**: pendência herdada do E03 (§9 do
  runbook do módulo) — a cópia está numa linha divergente do `develop`.
- **Ratificação da versão do Odoo (19.0)** e **homologação**: estágio 7 é do Anderson.
- **Verificação independente**: estágio 6 é do perfil `tester`. Quem entrega não homologa.
