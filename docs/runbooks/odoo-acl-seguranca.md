# Runbook — ACLs e segurança do módulo `transformativa_sales_ai` (carteira × tenant)

**Card:** `TRE-W2-E07-T01` (`t_e0b1bcbf`, perfil `desenvolvedor`) · **Status:** entregue e medido em
dev em 01/10/2026 · **Depends on:** `TRE-W2-E03-T01` (`t_c536ce86`, base do módulo) — executado sobre
`feature/TRE-W2-E05-T01`, ver D1
**Branch:** `feature/TRE-W2-E07-T01` (local — `completion_contract: local-only`)
**Máquina:** VPS Contabo `vmi3619453` (169.58.24.102) · **Ambiente:** dev (homolog/prod **não**
provisionados — ADR-005)
**Artefatos versionados:** `odoo/addons/transformativa_sales_ai/security/**`,
`odoo/addons/transformativa_sales_ai/tests/test_acl_seguranca.py`,
`odoo/addons/transformativa_sales_ai/{__manifest__.py,README.md,tests/__init__.py}`,
`scripts/odoo/{verificar-acl-modulo.sh,provar_acl_modulo.py}`,
`scripts/verificar_estrutura.sh`, `CHANGELOG.md`, `docs/operations/registro-de-execucoes.md`

---

## 1. Campos do card (doc 11 §2) e decisões registradas

O doc 11 não detalha W1/W2: os campos foram definidos **antes da primeira medição** e são estes.

| Campo | O que foi definido e registrado |
|---|---|
| **ACCEPTANCE CRITERIA** (homologados por Anderson, 29/09/2026 — não alterados) | **AC1** regras de acesso por carteira/tenant aplicadas; **AC2** teste negativo: usuário de um tenant **não** vê dado de outro — vazio ou erro, nunca material alheio; **AC3** aprovação humana jamais concedida por máquina |
| **TEST PLAN** (executado de verdade) | (a) `tests/test_acl_seguranca.py` — 11 testes `post_install`, tag `post_install`/`-at_install`, rodando com **usuários de verdade** (`with_user`) em dois tenants e três carteiras; (b) prova negativa **independente dos testes** (`scripts/odoo/provar_acl_modulo.py`, via `odoo shell`), que monta a cena de novo e mede o ataque por **busca** e por **leitura de id**; (c) verificação das regras **lidas no banco** (grupos, ACL, `ir.rule` com domínio e alcance); (d) duas provas de dente que mutam o artefato. Tudo orquestrado por `scripts/odoo/verificar-acl-modulo.sh`, item a item, numa **dupla descartável própria**. Evidência = log do verificador + logs brutos + exit code |
| **ROLLBACK PLAN** (executado) | §6: desinstalar o módulo (as ACLs e regras vivem nos dados do módulo e morrem com ele — medido no passo 3 do aceite do E03); `git revert` do commit do card; nada em homolog/produção (ADR-005) |
| **AFFECTED COMPONENTS** | `odoo/addons/transformativa_sales_ai/security/**` (novo), `tests/test_acl_seguranca.py` (novo), `tests/__init__.py`, `__manifest__.py` (chave `data` + comentário), `README.md`, `scripts/odoo/{verificar-acl-modulo.sh,provar_acl_modulo.py}` (novos), `scripts/verificar_estrutura.sh`, `docs/runbooks/odoo-acl-seguranca.md` (novo), `CHANGELOG.md`, `docs/operations/registro-de-execucoes.md`. **No ambiente:** dupla descartável própria (`e07t01-*`), não o `odoo-dev`/`pg-odoo-dev` |
| **RISK LEVEL** (proposto no card) | **Alto — isolamento entre clientes** (é o risco declarado do card). Mitigação medida: fail-closed sem grupo, sete itens de teste negativo, prova negativa independente e duas provas de dente |

**Decisões de implementação** (não mudam contrato, não inventam campo, não tocam produção):

| # | Decisão | Porquê |
|---|---|---|
| D1 | Executado sobre **`feature/TRE-W2-E05-T01`** (que contém a base do E03 **e** o modelo `tf.process.opportunity`) | as ACLs são do modelo do módulo; o caminho declarado do card é `W2-E03-T01`, e o E05 é o descendente direto dele que traz o modelo — basear no E03 puro daria ACL para modelo inexistente. O E04-T01/T02 editam `res.partner`/`crm.lead` em branches paralelas: nada deles é necessário aqui |
| D2 | **TENANT = `res.company`** (multicompanhia do Odoo), regra global `[('company_id', 'in', company_ids)]` | o isolamento entre clientes na V1 é **físico** (um banco por cliente), decidido pelo dono em 30/09/2026 e registrado em `docs/data/DATA_CONTRACT_V1.md`; o contrato **não tem** dimensão de cliente e coluna nova exigiria nova versão + aprovação. A companhia é o tenant nativo do Odoo: barreira de 2ª linha dentro da base e caminho já pronto para o V2 |
| D3 | **CARTEIRA = `res.partner.user_id`** ("vendedor" do parceiro, campo **nativo** e `store=True` no Odoo 19 — medido na imagem) | a carteira de um vendedor é o conjunto de parceiros dele; a oportunidade herda a carteira pelo `partner_id`. Nenhum campo novo. **Alternativa descartada:** `crm.team` — o modelo da oportunidade não tem equipe (`tf.process.opportunity` só tem `partner_id`/`company_id`/`stage_id`/`expected_revenue`/`lost_reason_id`), e criar campo ou regra sobre o `crm.lead` nativo mudaria o CRM de todo mundo, fora do escopo deste card (o `crm.lead` tem `team_id`/`user_id` e segue com as regras do core) |
| D4 | Dois grupos + **`res.groups.privilege`** numa `ir.module.category` do módulo | medido na imagem `odoo:19.0`: `res.groups.category_id` **não existe mais** nesta versão (o agrupamento passou a `privilege_id` → `res.groups.privilege.category_id`). Escrever por suposição dava `ValueError: Invalid field 'category_id' in 'res.groups'` |
| D5 | Nenhum grupo do módulo implica `base.group_user`; o gestor implica o vendedor | quem concede acesso interno é o administrador do Odoo; o módulo **não promove ninguém sozinho**. A hierarquia interna (gestor ⊃ vendedor) é a única que o módulo cria |
| D6 | ACL do modelo (`ir.model.access.csv`): vendedor `1,1,1,0`; gestor `1,1,1,1`; **nenhuma** ACL para `base.group_user`/portal/público | vendedor não apaga (apagar não é da carteira); **sem o grupo, o modelo não é alcançável** — fail-closed medido (`AccessError`), não "vazio silencioso" |
| D7 | As três regras cobrem as 4 permissões e a regra de **tenant é global** (sem grupo) | uma regra de tenant presa a um grupo poderia ser contornada por outro grupo (um superusuário de app, por exemplo); a barreira de tenant vale para todo mundo que alcança o modelo. A carteira e o gestor são regras de grupo (somam por OU, como no Odoo) |
| D8 | Os dados de segurança **não** usam `noupdate` | o domínio das regras é invariante de segurança: tem de voltar ao valor do código em cada `-u`. Deriva silenciosa é o que o verificador mede (item "domínio da regra X é o declarado") |
| D9 | A versão do módulo **não** sobe (`19.0.1.0.0`) | mesma razão do D6 do E05: nada instalado em ambiente persistente e três cards da mesma onda editam o manifesto em paralelo; subir a versão quebraria o `TRE_VERSAO_ESPERADA` do verificador do E03 sem ganho |
| D10 | **AC3** provado como *ausência de caminho*, não como narrativa: superfície de ACL do módulo = exatamente 1 modelo; nenhum grupo alcança `group_system`/`group_erp_manager`; o usuário do Sales AI não administra **outro** usuário, não cria `ir.rule`, e a tentativa de se dar o grupo de administrador **não promove** | a aprovação humana do TRE vive **fora** do Odoo (registro de aprovações + gate JEV) e é decisão do Anderson (`docs/architecture/hermes-dev-x-sales.md`). O que este card pode e deve provar é que o módulo não abre nenhuma porta para uma máquina conceder aprovação ou se promover |
| D11 | Verificador **próprio** (`scripts/odoo/verificar-acl-modulo.sh`) em vez de editar `verificar-modulo-odoo.sh` | o verificador do E03 está em consolidação por outro card (`t_de461d14`) e tem defeitos abertos (`t_578a4e4d`, `t_5c4fc7ac`, `t_9e402411`): editá-lo aqui criaria conflito em cima de trabalho de terceiros. O verificador do E03 continua rodando **sem edição** e o passo 2 do aceite deste card usa a mesma suíte (`--test-enable`) |

## 2. O que a segurança do módulo é

| Peça | Onde | O que faz |
|---|---|---|
| `ir_module_category_tf_sales_ai` + `privilege_tf_sales_ai` | XML | agrupa os grupos do módulo na App *Access rights* (Odoo 19) |
| `group_tf_sales_ai_user` ("Sales AI: Vendedor (carteira)") | XML | grupo base: vê e opera a **própria carteira** no(s) tenant(s) dele |
| `group_tf_sales_ai_manager` ("Sales AI: Gestor (tenant)") | XML | herda o vendedor; alcança **todas as carteiras do seu tenant** e nenhuma do outro |
| `rule_tf_oportunidade_tenant` (**global**) | XML | `[('company_id', 'in', company_ids)]` — isolamento por companhia (tenant) |
| `rule_tf_oportunidade_carteira` (grupo do vendedor) | XML | `[('partner_id.user_id', '=', user.id)]` |
| `rule_tf_oportunidade_gestor` (grupo do gestor) | XML | `[(1, '=', 1)]` — a carteira inteira do tenant |
| `access_tf_process_opportunity_user` / `_manager` | CSV | vendedor `ler/criar/escrever` (sem apagar); gestor `ler/criar/escrever/apagar` |

**As duas dimensões do critério, materializadas sem campo novo:**

* **tenant** = companhia do Odoo (`res.company`). Na V1 o isolamento entre *clientes* é físico (um
  banco por cliente, decisão do dono de 30/09/2026); esta regra é a barreira de 2ª linha dentro da
  base e o mecanismo que o V2 multi-cliente vai reusar.
* **carteira** = vendedor do parceiro (`res.partner.user_id`). A oportunidade pertence à carteira
  do dono do `partner_id`.

**O que este card NÃO faz (declarado, para não parecer mais do que é):** não cria campo de
carteira/tenant (o contrato não tem a dimensão e coluna nova é gatilho de nova versão), não muda
as regras nativas de `res.partner`/`crm.lead`, não cria view (E06), não cria API (W3) e **não**
toca o registro de aprovações nem o gate JEV.

## 3. Procedimento (na VPS do dev)

```bash
# no container do Hermes: transferir o módulo e os scripts (tar por ssh, sem scp)
tar -C odoo/addons -cf - transformativa_sales_ai | ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 \
    'mkdir -p /opt/tre/dev/e07t01/modulos && tar -C /opt/tre/dev/e07t01/modulos -xf -'
tar -C scripts -cf - odoo/verificar-acl-modulo.sh odoo/provar_acl_modulo.py \
  | ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 \
    'mkdir -p /opt/tre/dev/e07t01/scripts/odoo && tar -C /opt/tre/dev/e07t01/scripts/odoo -xf -'
sha256sum odoo/addons/transformativa_sales_ai/security/transformativa_sales_ai_security.xml  # tem de bater dos dois lados

# na VPS (sempre a partir de ARQUIVO — nunca por stdin, ver armadilha do `docker compose run`)
export TRE_MODULO_DIR=/opt/tre/dev/e07t01/modulos/transformativa_sales_ai
export TRE_LOG_DIR=/opt/tre/dev/e07t01/evidencias/logs
bash /opt/tre/dev/e07t01/scripts/odoo/verificar-acl-modulo.sh                    # aceite completo
bash /opt/tre/dev/e07t01/scripts/odoo/verificar-acl-modulo.sh --apenas-artefatos # so guardas+regras+prova
bash /opt/tre/dev/e07t01/scripts/odoo/verificar-acl-modulo.sh --prova-de-dente   # duas mutacoes
```

O verificador, em ordem: **guardas** (docker, as duas imagens com os digests medidos, `openssl`,
módulo e prova em disco com sha256, nome de banco descartável; e mede o estado do dev **antes**) →
**dupla descartável** (rede própria, `postgres:16` com senha gerada na hora, `odoo:19.0` com
configuração própria) → **passo 1** instalação em banco limpo → **passo 2** suíte do Odoo
(`--test-enable`; exige o relatório do runner, zero falhas, *e* que a classe `TestAclSeguranca`
tenha rodado) → **passo 3** regras lidas no banco (grupos, privilégio/categoria, ACL, as três
regras com domínio/alcance/global) → **passo 4** prova negativa independente → **limpeza** e
conferência de que a instância do dev não foi tocada.

O modo `--prova-de-dente` **não escreve no diretório do aceite**: cada prova usa
`"$TRE_LOG_DIR/dente/prova-N"` (os `.out` dos dentes ficam em `"$TRE_LOG_DIR/dente/"`) e uma **guarda
fail-closed** fotografa o sha256 dos `[1-4]-*.log` do aceite antes das provas e confere no fim —
qualquer mudança reprova o dente. Por isso a sequência acima (um único `export TRE_LOG_DIR` seguido
do aceite **e** dos dentes) é segura; antes do conserto da revisão rodada 1 ela **sobrescrevia** a
evidência bruta do aceite com a do mutante (§9).

## 4. Aceite — TEST PLAN medido (01/10/2026, VPS `vmi3619453`)

```
TRE_MODULO_DIR=/opt/tre/dev/e07t01/modulos/transformativa_sales_ai \
TRE_LOG_DIR=/opt/tre/dev/e07t01/evidencias/logs \
bash /opt/tre/dev/e07t01/scripts/odoo/verificar-acl-modulo.sh
→ RESULTADO: ACL_OK (51 itens, 0 falhas) modulo=transformativa_sales_ai
  modelo=tf.process.opportunity banco=tre_e07t01_acl imagens=odoo:19.0+postgres:16     exit 0
```

| Passo | Medição |
|---|---|
| **1 instalação em banco limpo** | `tre_e07t01_acl` **não existia** (dropado antes) e foi criado do zero pelo Odoo; `odoo --init exit 0`, **0 ERROR/CRITICAL**, `'Modules loaded.'`, `state=installed`, 0 módulo pendurado |
| **2 suíte do Odoo** | `odoo -u transformativa_sales_ai --test-enable exit 0`; `0 failed, 0 error(s) of 26 tests` (6 do E03 + 9 do E05 + **11 deste card**); 0 linha `FAIL:`/`ERROR:`; **`TestAclSeguranca` rodou (11 métodos no log)** |
| **3 regras aplicadas (lidas no banco)** | 2 grupos (via `ir_model_data`) no privilégio/categoria do módulo; gestor implica vendedor; nenhum grupo alcança `group_system`/`group_erp_manager`; **superfície de ACL = só `tf.process.opportunity`**; matriz `t\|t\|t\|f` (vendedor) e `t\|t\|t\|t` (gestor); 3 regras, todas ativas, todas no modelo, todas cobrindo as 4 permissões; domínios exatamente os declarados; regra de tenant **global**, carteira no grupo do vendedor, gestor no grupo do gestor |
| **4 prova negativa independente** | `provar_acl_modulo.py` → `ACL_ITENS=22 ACL_FALHAS=0`, `ACL_RESULTADO: OK`, **0 acusação de material alheio**, sem traceback: vendedor de cada carteira vê só a sua; vendedor sem carteira devolve **vazio**; leitura por id de carteira/tenant alheio → **AccessError**; gestor vê as duas carteiras do SEU tenant e nenhuma do outro; sem o grupo do módulo → **AccessError** (fail-closed); criação em carteira alheia recusada e na própria aceita; AC3 (superfície, escalada e autopromoção) fechado |
| **limpeza / dev intocado** | banco, `postgres`, rede e diretório de configuração descartáveis removidos; **instância do dev com os mesmos 4 bancos antes e depois** (`odoo_dev, postgres, template0, template1`); `homolog`/`prod` com **0 arquivo** |

**Logs brutos:** `/opt/tre/dev/e07t01/evidencias/logs/` — `1-instalacao.log`, `2-teste.log`
(relatório do runner), `4-prova-negativa.log` (a prova negativa item a item) — e
`/opt/tre/dev/e07t01/evidencias/logs/dente/` (`dente-1-carteira-aberta.out`,
`dente-2-acl-plantada.out` e `prova-N/[1-4]-*.log`), as duas rodadas mutadas terminando em
`RESULTADO: ACL_DENTE_OK (2 provas, 0 falhas)`. **Este é o par de evidências re-medido no retrabalho
da revisão rodada 1 (§9)**, com o harness commitado: os três arquivos do aceite estão intactos
depois dos dentes (`sha256` antes/depois idêntico) e citam o banco do aceite (`tre_e07t01_acl`).

## 5. Provas negativas — o aceite tem dentes

```
TRE_MODULO_DIR=... TRE_LOG_DIR=... bash scripts/odoo/verificar-acl-modulo.sh --prova-de-dente
→ RESULTADO: ACL_DENTE_OK (2 provas, 0 falhas)                                          exit 0
```

| Dente | Mutação (em **cópia** do módulo, banco próprio) | Medição |
|---|---|---|
| 1 | domínio da regra de **carteira** trocado por `[(1, '=', 1)]` (o vendedor passaria a ver tudo) | `FALHOU dominio da regra de carteira: '[(1, '=', 1)]'`; `FALHOU runner do Odoo: 4 failed … of 26 tests`; `FALHOU prova negativa: 22 itens, 6 falha(s)` com **acusação de material alheio**; `RESULTADO: ACL_FALHOU (51 itens, 6 falha(s))`, exit 1 |
| 2 | ACL plantada no CSV dando **escrita em `res.users`** ao grupo do vendedor | `FALHOU superficie de ACL do modulo: 'res.users, tf.process.opportunity'`; `FALHOU ACLs do modulo: 3 (esperado 2)`; `FALHOU runner do Odoo: 1 failed … of 26 tests`; `FALHOU prova negativa: 22 itens, 2 falha(s)`; `RESULTADO: ACL_FALHOU (51 itens, 6 falha(s))`, exit 1 |

A mutação é **do artefato**, em cópia: o módulo real não é tocado, e cada dente roda num banco
próprio (`tre_e07t01_acl_d1` / `_d2`) que é removido no fim.

**O que cada dente exercita (e o que o impede de voltar a ser frouxo):**

* cada prova escreve no **seu** diretório de log (`"$TRE_LOG_DIR/dente/prova-N"`), nunca no do aceite;
* a **guarda** compara o sha256 dos `[1-4]-*.log` do aceite antes e depois das provas e reprova o
  dente se algum mudar (controle medido: com o caminho compartilhado de volta → `ACL_DENTE_FALHOU`,
  exit 1 — §9);
* o dente 2, além de exigir que o aceite reprove, exige que **o item do log de teste dispare**
  (`OK dente 2: o item de linha de teste reprovado disparou (item com dente proprio)`) — sem isso o
  item "nenhuma linha de teste `FAIL:`/`ERROR:`" pode voltar a ser código morto sem a bateria notar
  (aprendizado do `TRE-W2-E03-T01-D01`).

## 6. Rollback

1. **Do artefato (o caminho do card):** `git revert <commit>` na branch do card. Nada mais depende
   dele: os dados de segurança são dados do módulo.
2. **Do efeito (executado de verdade na instalação):** desinstalar o módulo pelo ORM
   (`scripts/odoo/desinstalar_modulo.py`) — as ACLs e as três regras, sendo dados do módulo, vão
   com ele. É o passo 3 do aceite do E03, medido com **0 resquício** em
   `ir_model_data`/`ir_ui_view`/`ir_model_fields` e 0 tabela com prefixo do modelo.
3. **Do ambiente:** nada a fazer — tudo rodou numa dupla descartável (`e07t01-*`), removida no fim;
   `/opt/tre/{homolog,prod}` continuam com 0 arquivo (ADR-005).

## 7. Identidade do que foi testado

- Imagens: `odoo:19.0` no digest `odoo@sha256:77bac5cd1e065210828f34883a7f76740b7373d06dd3a5a55d3eeb31ee2f85cd`
  e `postgres:16` no digest `postgres@sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54`
  (as mesmas do par de dev).
- sha256 dos artefatos do módulo **iguais no repo (worktree do commit `060c369`) e na VPS**:
  `security/transformativa_sales_ai_security.xml c5d89ba3…`,
  `security/ir.model.access.csv 1066372f…`, `tests/test_acl_seguranca.py 3dca5720…`,
  `tests/__init__.py 9183eb85…`, `__manifest__.py 015399f6…`, `README.md 06806487…`,
  `models/tf_process_opportunity.py 2045dd0a…` (intocado do E05),
  `tests/test_modulo_base.py ea75d283…` (intocado do E03),
  `tests/test_oportunidade_canonica.py 56dc0865…` (intocado do E05) — **12/12 arquivos
  idênticos**, conferidos arquivo a arquivo depois do commit.
- Scripts do aceite: `scripts/odoo/verificar-acl-modulo.sh` e `scripts/odoo/provar_acl_modulo.py`
  (o verificador imprime o sha256 do prover na rodada; os dois estão versionados com bit `100755`).
  Blob do verificador nesta versão (retrabalho da revisão rodada 1, §9): `fa47f627…` — a versão
  anterior (que sobrescrevia os logs do aceite) era `1ce368e6…`; o prover segue `76c25e79…`, intocado.

## 8. Defeitos e pendências declaradas

- **Defeitos abertos do E03** (`t_578a4e4d`/D01, `t_5c4fc7ac`/D02, `t_9e402411`/D03, `t_de461d14`/D05)
  continuam no verificador do E03; **este card não edita o verificador do E03** — o verificador deste
  card é próprio (`scripts/odoo/verificar-acl-modulo.sh`, D11). Corrigindo a versão anterior deste
  runbook: o script deste card **não** usa `grep` ancorado em `^ACL_ITEM FALHOU` (essa linha não
  existe nele; as contagens da prova negativa vêm de `ACL_ITENS=`/`ACL_FALHAS=`) e a primeira versão
  **tinha** o padrão morto `^(FAIL|ERROR): ` (`t_578a4e4d`/D01) **e** o dente herdando o diretório de
  log do aceite (`t_5c4fc7ac`/D02) — as duas classes de defeito do E03 **reincidiram neste card** e
  foram consertadas na revisão rodada 1 (**§9**).
- **Publicação do módulo na cópia operacional `/opt/tre/repo`**: pendência herdada do E03 (§9
  daquele runbook) — a cópia está numa linha divergente do `develop`.
- **Views (E06)**: a view do modelo vai mostrar a oportunidade **já filtrada** pelas regras
  (a lista é o `search`); quando o E06 entrar, o aceite dele herda estas regras — vale repetir o
  item "usuário restrito não vê" com o par de usuários deste card.
- **`crm.lead`/`res.partner`**: as regras nativas do core continuam valendo; este card **não** cria
  regra sobre eles (a carteira do lead é do CRM, por `team_id`/`user_id`). Se um card futuro quiser
  a mesma carteira no lead, é decisão nova — não está declarada aqui.
- **Ratificação da versão do Odoo (19.0)**, **homologação** (estágio 7) e **verificação
  independente** (estágio 6, perfil `tester`) seguem fora deste card: quem entrega não homologa.

## 9. Retrabalho — verificação independente, rodada 1 (01/10/2026)

**O que o revisor mediu** (card `t_e0b1bcbf`, veredito *mudanças pedidas*, anexo
`evidencia-t_e0b1bcbf-r1.txt`): os **3 AC homologados estão OK** — ele reproduziu o aceite
(`ACL_OK`, 51 itens, exit 0), a sonda independente dele (28 itens, 0 falhas, incluindo "o tenant
trunca a carteira"), os 2 dentes e dois controles negativos próprios (banco do ambiente recusado;
cópia sem a classe de aceite reprovada fail-closed) —, mas o **harness novo deste card** havia
reimplementado a forma **anterior** aos consertos do E03:

| # | Defeito medido pelo revisor | Classe |
|---|---|---|
| 1 | o `--prova-de-dente` herdava `TRE_LOG_DIR="$LOG_DIR"` nos dois sub-runs mutados e escrevia os **mesmos nomes de passo** no diretório do aceite: a sequência do §3 (um `export TRE_LOG_DIR` + aceite **e** dentes) deixava `2-teste.log` = `1 failed, 0 error(s) of 26 tests` e `4-prova-negativa.log` = `ACL_ITENS=22 ACL_FALHAS=2` — a evidência bruta do aceite passava a ser a do **mutante** (`tre_e07t01_acl_d2`) | `TRE-W2-E03-T01-D02` reincidente |
| 2 | o item "nenhuma linha de teste 'FAIL:'/'ERROR:' no log" usava `grep -cE '^(FAIL|ERROR): '` (ancorado no início da linha — o Odoo 19 prefixa com `data pid NIVEL banco logger:`) e imprimiu `OK` numa rodada com **4 testes reprovados** | `TRE-W2-E03-T01-D01` reincidente |
| 3 | o §8 (e a assertion do handoff) afirmava um `grep` ancorado em `^ACL_ITEM FALHOU` que **não existe** no script | documentação incorreta |
| 4 | o anexo do aceite vinha de uma revisão anterior do harness (`instalacal` × `instalacao`) | proveniência da evidência |

**Conserto** — um arquivo: `scripts/odoo/verificar-acl-modulo.sh` (blob `fa47f627…`; **não** toca o
XML/CSV de segurança, o módulo, os testes nem o prover `76c25e79…`, como a revisão pediu):

1. cada prova escreve em `"$TRE_LOG_DIR/dente/prova-N"`; os `.out` dos dentes vão para
   `"$TRE_LOG_DIR/dente/"` — o modo dente **não** escreve arquivo nenhum no diretório do aceite;
2. **guarda fail-closed**: sha256 dos `[1-4]-*.log` do aceite fotografado antes das provas e
   conferido no fim; qualquer mudança → `FALHOU o modo dente mexeu nos logs de passo do aceite` e
   `ACL_DENTE_FALHOU`, exit 1;
3. item do log de teste: `grep -E '(^| )(FAIL|ERROR): [A-Za-z_]'` (padrão já medido em
   `scripts/odoo/verificar-res-partner.sh`), com as linhas casadas impressas na falha, **e dente
   próprio** no dente 2 (o item tem de disparar com um teste reprovado — aprendizado do D01).

**Medições do retrabalho** (VPS `vmi3619453`, mesmo `TRE_LOG_DIR` do aceite — a forma do §3; bateria em
`/opt/tre/dev/e07t01-retrabalho/retrabalho-e07t01.sh` sha256 `5319f4fb…` e controles em
`.../controles-e07t01.sh`, saídas em `.../out/`):

| # | Medição | Resultado |
|---|---|---|
| 1 | aceite → dentes na **mesma** sessão | `RESULTADO: ACL_OK (51 itens, 0 falhas)` **exit 0** + `RESULTADO: ACL_DENTE_OK (2 provas, 0 falhas)` **exit 0** |
| 2 | sha256 dos `[1-4]-*.log` do aceite antes × depois dos dentes | **idênticos** — `1-instalacao.log da4cbf55…`, `2-teste.log 54b457d8…`, `4-prova-negativa.log e307d12f…` |
| 3 | conteúdo final dos 3 arquivos | `2-teste.log`: `0 failed, 0 error(s) of 26 tests`; `4-prova-negativa.log`: `ACL_ITENS=22 ACL_FALHAS=0` e `ACL_RESULTADO: OK`; os dois citam **`tre_e07t01_acl`** (banco do aceite, não o do dente) |
| 4 | dente 2 (ACL plantada) | `FALHOU 1 linha(s) de teste reprovado(a) no log: … : FAIL: TestAclSeguranca.test_11…` e `OK dente 2: o item de linha de teste reprovado disparou (item com dente proprio)` |
| 5 | **controle da guarda** — cópia do harness com o sub-run apontando de volta para `$LOG_DIR` (diff = só os dois `TRE_LOG_DIR`) | `FALHOU o modo dente mexeu nos logs de passo do aceite` com os sha256 antes/depois impressos → `ACL_DENTE_FALHOU (0 prova(s) sem dente, 1 falha(s) na guarda dos logs do aceite)` **exit 1** |
| 6 | **controle do item** — cópia com o padrão morto de volta (diff = só o item) | no **mesmo** log com `1 failed, 0 error(s) of 26 tests`: o item imprime `OK nenhuma linha de teste 'FAIL:'/'ERROR:' no log` (o defeito, reproduzido); o padrão antigo casa **0** linha e o novo **1** linha nesse log |
| 7 | logs do dente | `logs/dente/prova-1/` e `logs/dente/prova-2/` com `[1-4]-*.log` próprios — caminho separado do aceite |
| 8 | ambiente | mesmos 4 bancos no dev (`odoo_dev, postgres, template0, template1`); **0 container** e **0 rede** `e07t01-*`; `homolog`/`prod` com 0 arquivo (ADR-005) |

**Verificadores do projeto** no commit do retrabalho: `verificar_estrutura.sh` → `PASS (0 falhas)`
RC=0 · `secret_scan.sh` → `PASS` RC=0 · `verificar_papeis.sh` → `PASS (0 falhas)` RC=0 · `bash -n` OK.

**Anexo do aceite re-gerado** a partir do harness commitado (`fa47f627…`): a saída completa do aceite
desta rodada é a anexada ao card (a anterior tinha sido capturada de uma revisão anterior do harness).
