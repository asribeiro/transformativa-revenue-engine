# Runbook — views do Sales AI (modulo `transformativa_sales_ai`)

Card: **TRE-W2-E06-T01** (kanban `t_cf7519c9`).

Criterios de aceitacao **homologados por Anderson em 29/09/2026** (nada acrescentado, nada
removido; declarados antes da primeira medicao — doc 11 §2):

| # | Criterio |
|---|----------|
| AC1 | as views exibem os campos das entidades customizadas, sem erro de renderizacao |
| AC2 | o acesso respeita o perfil de usuario (quem nao pode ver, nao ve) |
| AC3 | evidencia de abertura das views (lista/formulario) |

Este runbook registra o que foi construido, as decisoes de forma, como o aceite e' medido e as
armadilhas do Odoo 19 que a medicao revelou.

## 1. O que este card entrega

No modulo `transformativa_sales_ai` (base TRE-W2-E03-T01):

| Artefato | Arquivo | Papel |
|---|---|---|
| `view_tf_process_opportunity_list` | `views/tf_process_opportunity_views.xml` | lista da entidade canonica `tf.process.opportunity` |
| `view_tf_process_opportunity_form` | `views/tf_process_opportunity_views.xml` | formulario da entidade canonica |
| `view_tf_process_opportunity_search` | `views/tf_process_opportunity_views.xml` | busca (o UUID canonico e' caminho de busca — contrato §3) |
| `view_partner_form_tf_sales_ai` | `views/res_partner_views.xml` | herda `base.view_partner_form` e acrescenta a secao "Sales AI" (5 campos do parceiro) |
| `view_crm_lead_form_tf_sales_ai` | `views/crm_lead_views.xml` | herda `crm.crm_lead_view_form` e acrescenta a secao "Sales AI" (13 campos do lead) |
| `action_tf_process_opportunity` | `views/tf_process_opportunity_views.xml` | `ir.actions.act_window`, `view_mode = list,form` |
| `menu_tf_sales_ai_root` / `menu_tf_sales_ai_oportunidades` | `views/tf_process_opportunity_views.xml` | menu "Sales AI" pendurado no menu raiz do CRM; item "Oportunidades" abre a acao |

Os cinco registros de view, os dois menus e a acao sao declarados **no modulo** (nao em `data`
de outro): a desinstalacao os remove — medido no passo 5 do aceite.

Campos por view (contrato de dados, doc 11 §2):

* `tf.process.opportunity` — lista: `name`, `partner_id`, `stage_id`, `expected_revenue`,
  `company_id`; formulario: os mesmos + `currency_id`, `lost_reason_id`, `tf_uuid`;
  busca: `name`, `partner_id`, `tf_uuid`, `stage_id`.
* `res.partner` — `tf_cnpj`, `tf_domain`, `tf_linkedin_url`, `tf_company_id`, `tf_priority_score`.
* `crm.lead` — os 13 campos `tf_*` de rastreio/pontuacao (o card TRE-W2-E04-T02 e' o dono do modelo).

## 2. Decisoes de forma (e por que)

* **D1 — a entidade canonica tem views proprias** (lista/form/busca) em vez de so' menu+ACL: sem
  uma lista e um formulario declarados, a acao nao abre (AC3) e o AC1 nao tem onde medir campo.
* **D2 — heranca das views padrao por `xpath` em `//notebook` com `position="inside"`**: a secao
  "Sales AI" entra como `<page name="tf_sales_ai">` no formulario do parceiro e do lead, sem
  reescrever a view padrao do Odoo (o que quebraria na proxima versao do base/crm).
* **D3 — o recorte por perfil (AC2) e' feito com `groups` na propria pagina da secao** e com
  `groups` no `menuitem` do modulo. Ver §6: no Odoo 19 o *registro* de uma view herdada **nao
  pode** carregar `groups` — o recorte tem de estar na arch (pagina) e/ou no menu.
* **D4 — `company_id` e `currency_id` seguem o convencional do Odoo**: ficam sob os grupos padrao
  `base.group_multi_company` / `base.group_multi_currency` (e' assim no proprio `crm.lead`).
  Consequencia para a medicao: a expectativa da view **renderizada** de um vendedor simples nao
  inclui esses dois campos; eles sao medidos com o usuario MEMBRO MULTI (duas companhias), que
  tem os grupos padrao. Sem essa separacao, o recorte padrao do Odoo apareceria como falha do card.
* **D5 — as views da entidade canonica nao levam `groups` no registro**: quem protege
  `tf.process.opportunity` e' a ACL/record rule do card TRE-W2-E07-T01 (fail-closed, medido:
  o restrito recebe `AccessError`). Duplicar o recorte na view so' criaria um segundo lugar para
  divergir; o que o modulo controla na UI (menu da entidade, secoes do parceiro/lead) esta no
  grupo `group_tf_sales_ai_user`.
* **D6 — o que o grupo do modulo protege, dito com precisao**: ele recorta a **superficie de UI**
  (menu + secoes "Sales AI") e o acesso a entidade canonica (via ACL). Ele **nao** e' uma ACL
  campo-a-campo dos campos `tf_*` de `res.partner`/`crm.lead`: quem ja' le o parceiro/lead pelos
  direitos padrao do Odoo continua lendo esses campos pelo ORM. Se o negocio exigir sigilo
  campo-a-campo nessas duas entidades, isso e' um card de ACL proprio (nao este).

## 3. Como rodar o aceite

Na VPS do dev (a partir de arquivo, nunca por stdin — armadilha do `docker compose run`):

```
TRE_MODULO_DIR=/opt/tre/dev/e06t01/odoo/addons/transformativa_sales_ai \
TRE_LOG_DIR=/opt/tre/dev/e06t01/evidencias/logs \
  bash /opt/tre/dev/e06t01/scripts/odoo/verificar-views-sales-ai.sh
```

Formas auxiliares:

```
bash verificar-views-sales-ai.sh --apenas-artefatos       # sem a suite de testes do Odoo
bash verificar-views-sales-ai.sh --banco tre_e06t01_alt   # outro banco descartavel
bash verificar-views-sales-ai.sh --prova-de-dente         # duas mutacoes do artefato (dentes)
```

Variaveis: `TRE_MODULO`, `TRE_MODULO_DIR`, `TRE_PROVA`, `TRE_DESINSTALADOR`, `TRE_BANCO`,
`TRE_IMAGEM`, `TRE_IMAGEM_PG`, `TRE_PG_USER`, `TRE_MIN_TESTS`, `TRE_MIN_METODOS_VIEWS`,
`TRE_MIN_ITENS_PROVA`, `TRE_LOG_DIR`, `TRE_DEV_PG_CT`, `TRE_MANTER_BANCO=1`.

Saida: um item por linha (`OK`/`FALHOU`), uma linha `RESULTADO:` e o exit code
(`0` = `VIEWS_OK`, `1` = `VIEWS_FALHOU`, `2` = guarda de ambiente). O script cria o **proprio
par descartavel** (`postgres:16` + `odoo:19.0`, nomes `e06t01-*`) porque o Odoo do container de
dev abre sessao de cron contra qualidade de banco da instancia `pg-odoo-dev`; nao toca
`pg-odoo-dev`, `odoo_dev` nem `/opt/tre/repo`, e confere no fim que a instancia do dev e o
`/opt/tre/homolog`/`/opt/tre/prod` ficaram identicos (ADR-005).

## 4. O que e' medido onde

* **passo 1** — instalacao em banco limpo (`--init`): exit 0, `state = installed`, sem
  `ERROR`/`CRITICAL`, sem erro de arch.
* **passo 2** — suite do modulo (`--test-enable`): relatorio do runner (0 failed / 0 error) e a
  classe de aceite `TestViewsSalesAi` com os 10 metodos (se um metodo sumir, o aceite reprova).
* **passo 3 — leitura NO BANCO** (o que o XML *virou* depois de instalado): contagem de views,
  menus e acao do modulo; presenca de cada campo na arch **gravada**; `groups` do menu em
  `ir_ui_menu_group_rel`; `groups` da secao na arch das views herdadas; hierarquia de menu
  (raiz do CRM -> Sales AI -> Oportunidades) e `view_mode` da acao.
* **passo 4 — prova independente** (`scripts/odoo/provar_views_sales_ai.py`, 21 itens): monta a
  cena com tres usuarios que diferem **so'** pelo grupo do modulo (membro, membro multi, restrito)
  e mede a view **renderizada** pelo ORM (`get_view`, o mesmo caminho do web client) e a leitura
  da entidade canonica. Nao usa o usuario de teste do autor nem mock do alvo.
* **passo 5 — rollback**: desinstalacao pelo ORM (`scripts/odoo/desinstalar_modulo.py`) e medicao
  de que sobrou zero view/menu/registro do modulo, e de que a secao "Sales AI" sumiu do formulario
  do parceiro.
* **passo 6** — limpeza e intactibilidade do dev/homolog/prod.

## 5. Evidencia medida (rodada de aceite)

```
RESULTADO: VIEWS_OK (83 itens, 0 falhas) modulo=transformativa_sales_ai
           modelo=tf.process.opportunity banco=tre_e06t01_views imagens=odoo:19.0+postgres:16
EXIT_ACEITE=0
```

Destaques da rodada (log completo em `evidencias/aceite.out` da copia de aceite):

* `odoo --init exit 0`; `ir_module_module.state = installed`; log sem `ERROR`/`CRITICAL`.
* `runner do Odoo: 0 failed, 0 error(s) of 50 tests`; `TestViewsSalesAi` com 10 metodos.
* `modulo declara 5 views (3 da oportunidade + 2 herdadas)`, `2 menus`, `1 acao de janela`.
* AC1: 4 campos na lista, 8 no formulario e `tf_uuid` na busca do modelo; os 5 campos do parceiro
  e os 13 do lead nas views herdadas (arch gravada).
* AC2: a secao esta recortada pelo grupo nas duas views herdadas e o menu esta preso ao grupo.
* AC3: `acao abre a lista e o formulario (tf.process.opportunity|list,form)`; a prova independente
  abriu lista e formulario com o membro (`ausentes=nenhum`) e a lista devolveu o dado sintetico
  (`n=1`, com UUID canonico).
* Prova independente: `21 itens, 0 falhas`, `VIEW_RESULTADO: OK`, sem traceback.
* AC2 na prova: o menu do Sales AI aparece para o membro e **nao** aparece para o restrito; a
  seccao "Sales AI" e os campos `tf_*` **nao** aparecem no parceiro/lead do restrito; a leitura e
  a abertura da lista da entidade canonica pelo restrito recusam com `AccessError`.
* Rollback: 0 view, 0 menu, 0 registro do modulo; nenhuma view de parceiro carrega a secao.

Prova de dente (`--prova-de-dente`): duas mutacoes do artefato em copia propria, cada uma tem de
reprovar o aceite — (1) o `groups` da secao do parceiro removido (o restrito passaria a ver a
secao) e (2) a view do modelo fora do manifesto `data`. Se alguma passar, o aceite nao tem dente.

## 6. Armadilhas do Odoo 19 medidas neste card

1. **`<group>` de busca nao aceita `expand`/`string`**: a arch de busca com
   `<group expand="0" string="Agrupar por">` e' recusada pelo RNG (`Invalid attribute expand for
   element group`). O `<group>` da busca vai sem atributo (nome opcional).
2. **View herdada nao pode carregar `groups` no registro**: `<field name="group_ids">` numa view
   com `inherit_id` levanta `ParseError: Inherited view cannot have 'groups' defined on the
   record. Use 'groups' attributes inside the view definition`. O recorte vai no no' da arch.
3. **`groups` e' aplicado no servidor, nao so' no cliente**: com `groups` na `<page>`, a arch
   renderizada para quem nao tem o grupo **nao** traz a pagina nem os campos (medido: restrito
   `tf_sales_ai=False`; membro `True`). E' o que da' o AC2.
4. **`base.group_multi_company` nao se concede na mao**: `UsersMultiCompany.create/write`
   (`res_users.py`) **retira** o grupo de quem tem 1 companhia e **concede** a quem tem 2+.
   Medir `company_id` na view exige um usuario com duas companhias, nao um `group_ids` manual.
5. **`ir_ui_view.arch_db` e' jsonb** (campo traduzivel): comparar com `arch_db::text` nao casa
   `name="x"` porque o dump do jsonb escapa as aspas (`name=\"x\"`). Usar `arch_db->>'en_US'`.
6. **`ir_ui_menu_group_rel` usa `menu_id` + `gid`** (nao `group_id`) — conferido em
   `information_schema.columns`.
7. **`--without-demo=all`** emite `WARNING ... invalid boolean value: 'all'` no 19.0 mas nao
   impede a instalacao (o par do Odoo sobe com demo desligado pelo `odoo.conf`).

## 7. Desinstalacao

O modulo e' dono de tudo o que declara. `scripts/odoo/desinstalar_modulo.py` (ORM) desinstala e o
aceite mede o resíduo: zero view, zero menu, zero registro em `ir_model_data`, e a secao "Sales AI"
ausente do formulario do parceiro. A desinstalacao **nao** remove os campos `tf_*` das tabelas
`res_partner`/`crm_lead`: as colunas sao dos cards TRE-W2-E04-T01/T02 e o Odoo as derruba quando
*os modulos donos* saem — este modulo so' declara as views.
