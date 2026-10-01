# Runbook — Módulo Odoo `transformativa_sales_ai` (base do Sales AI)

**Card:** `TRE-W2-E03-T01` (`t_c536ce86`, perfil `desenvolvedor`) · **Status:** módulo entregue e
medido em dev (01/10/2026) · **Depends on:** `TRE-W2-E02-T01` (CRM básico, `t_adea8e6b`)
**Branch:** `feature/TRE-W2-E03-T01` (local — `completion_contract: local-only`)
**Máquina:** VPS Contabo `vmi3619453` (169.58.24.102) · **Ambiente:** dev (homolog/prod **não**
provisionados — ADR-005)
**Artefatos versionados:** `odoo/addons/transformativa_sales_ai/**`,
`scripts/odoo/{verificar-modulo-odoo.sh,manifesto_do_modulo.py,desinstalar_modulo.py}`

Este runbook é o registro dos campos do doc 11 §2, das decisões de implementação, do
procedimento, do aceite medido, das provas negativas e do rollback.

---

## 1. Campos do card (doc 11 §2) e decisões registradas

| Campo | O que foi definido e registrado |
|---|---|
| **ACCEPTANCE CRITERIA** (homologados por Anderson, 29/09/2026) | AC1 módulo instala e desinstala limpo em banco limpo (idempotente); AC2 manifesto e versão corretos, dependências declaradas; AC3 `--test-enable` do Odoo sem erro no módulo |
| **TEST PLAN** (proposto no card) | Os quatro passos: instalação em banco limpo → teste do Odoo → desinstalação → reinstalação; evidência = log dos quatro passos. Executado por `scripts/odoo/verificar-modulo-odoo.sh`, item a item |
| **ROLLBACK PLAN** (proposto) | Desinstalar o módulo (§6). Executado de verdade: é o **passo 3** do próprio aceite, e o rollback total da dupla descartável é a limpeza do passo 4 |
| **AFFECTED COMPONENTS** | `odoo/addons/transformativa_sales_ai/**` (novo), `scripts/odoo/**` (novo), `docs/runbooks/odoo-modulo-sales-ai.md` (novo), `CHANGELOG.md`, `docs/operations/registro-de-execucoes.md`, `scripts/verificar_estrutura.sh`. **No ambiente:** dupla descartável própria (não o `odoo-dev`/`pg-odoo-dev`) |
| **RISK LEVEL** (proposto) | **Médio** — módulo novo, sem modelo/dado, com `depends = ['base','crm']`; reversível por desinstalação; nada em homolog/produção |

**Decisões de implementação deste card** (não mudam contrato; a base para as próximas cards):

| # | Decisão | Porquê |
|---|---|---|
| D1 | Módulo nasce **sem modelo, view ou ACL** | o card pede o *módulo base*; as customizações são `TRE-W2-E04-T01/T02`, `E05`, `E06`, `E07` e `W3-E01` — todas penduradas nesta base |
| D2 | `version = 19.0.1.0.0` (esquema `<série do Odoo>.<major>.<minor>.<patch>`) | a série acompanha a do Odoo em dev (19.0) e o verificador **reprova** se divergir (é o que o dente 1 mede) |
| D3 | `depends = ['base', 'crm']` | `crm` é o alvo das customizações (E04/E05/E06/E07): declarar desde a base torna a dependência explícita e resolvível no alvo — e há item que confere cada dependência nos addons da imagem |
| D4 | `license = 'LGPL-3'`, `application = False`, `installable = True` | licença de módulo comunitário coerente com a imagem oficial; módulo base não é aplicação |
| D5 | Aceite roda numa **dupla descartável própria** (`postgres:16` + `odoo:19.0`, as imagens do par de dev), não no `odoo-dev`/`pg-odoo-dev` | §7: o AC pede **banco limpo** (o `odoo_dev` não é limpo — carrega o funil do `TRE-W2-E02-T01`) e o Odoo do dev varre todo banco novo da instância |
| D6 | Container do Odoo sempre **descartável** (`--rm`), módulo montado em `/mnt/extra-addons` e configuração própria (senha gerada na hora, arquivo 600 dono uid 100) | nada de segredo em argumento/log/artefato; nenhuma escrita na cópia operacional `/opt/tre/repo` |
| D7 | Testes do módulo são `post_install` e **contam para o aceite**: o item do aceite exige `N >= 1` testes no relatório do runner | sem teste coletado o relatório vira "0 tests" e o aceite **reprova** (fail-closed) |

## 2. O que o módulo é (e o que ele não é ainda)

`transformativa_sales_ai` é o módulo Odoo Community que hospeda as customizações e a
integração do Sales AI da Transformativa. Neste card ele é **só a base**: manifesto, versão,
dependências e teste. Quem acrescenta conteúdo:

| Conteúdo | Card |
|---|---|
| Campos de dedup em `res.partner` (CNPJ, domínio, LinkedIn) | `TRE-W2-E04-T01` |
| Campos de rastreio em `crm.lead` (13 previstos no contrato) | `TRE-W2-E04-T02` |
| Modelo canônico `tf.process.opportunity` | `TRE-W2-E05-T01` |
| Views do Sales AI | `TRE-W2-E06-T01` |
| ACLs / segurança (carteira × tenant) | `TRE-W2-E07-T01` |
| API controlada | `TRE-W3-E01-T01` |

Quando a primeira card de customização entrar, ela acrescenta `from . import models` no
`__init__.py` e cria o pacote `models/` — nenhuma mudança no manifesto é necessária além de
`depends` (já cobre `crm`) e, se houver dado, as chaves `data`/`demo` (hoje vazias).

**Teste do módulo (`tests/test_modulo_base.py`, 6 testes, tag `post_install`/`-at_install`):**

1. `test_01_instalado_com_a_versao_do_manifesto` — a versão gravada no banco é a do manifesto **em disco**;
2. `test_02_serie_da_versao_acompanha_o_odoo` — série do módulo == série do Odoo em execução;
3. `test_03_manifesto_declarado` — licença/instalabilidade/`application` conferidos contra o banco;
4. `test_04_dependencias_declaradas_e_instaladas` — manifesto == `ir.module.module.dependency` e todas instaladas;
5. `test_05_ancoras_das_customizacoes_respondem` — `res.partner` e `crm.lead` criam e leem dado sintético;
6. `test_06_instalacao_assentada` — nenhum módulo pendurado em `to install`/`to upgrade`/`to remove`.

## 3. Procedimento (na VPS do dev)

```bash
# no container do Hermes: transferir o módulo e os scripts (tar por ssh, sem scp)
tar -C odoo/addons -cf - transformativa_sales_ai | ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 \
    'mkdir -p /opt/tre/dev/modulos && tar -C /opt/tre/dev/modulos -xf -'
tar -C scripts -cf - odoo | ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 \
    'mkdir -p /opt/tre/dev/scripts && tar -C /opt/tre/dev/scripts -xf -'
sha256sum odoo/addons/transformativa_sales_ai/__manifest__.py   # e do outro lado: tem de bater

# na VPS (sempre a partir de ARQUIVO — nunca por stdin, ver armadilha do `docker compose run`)
bash /opt/tre/dev/scripts/odoo/verificar-modulo-odoo.sh                  # aceite completo (4 passos)
bash /opt/tre/dev/scripts/odoo/verificar-modulo-odoo.sh --apenas-manifesto
bash /opt/tre/dev/scripts/odoo/verificar-modulo-odoo.sh --prova-de-dente
TRE_LOG_DIR=/opt/tre/dev/evidencias/<card>/logs bash /opt/tre/dev/scripts/odoo/verificar-modulo-odoo.sh
```

`--prova-de-dente` pode ser rodado com o **mesmo** `TRE_LOG_DIR` do aceite: o dente escreve
em `$TRE_LOG_DIR/dente/` e não toca os logs do aceite (§5.1).

O verificador, em ordem: **guardas** (docker, as duas imagens com os digests medidos, `openssl`,
módulo em disco, nome de banco descartável; e mede o estado do dev **antes**) → **dupla
descartável** (rede própria, `postgres:16` com senha gerada na hora, `odoo:19.0` com
configuração própria) → **manifesto** → **passo 1** instalação em banco limpo → **passo 2**
teste do Odoo → **passo 3** desinstalação pelo ORM → **passo 4** reinstalação → **limpeza**
(banco, dupla, rede, diretório de configuração) e **conferência do dev** (bancos iguais antes
e depois; `homolog`/`prod` sem arquivo). Saída item a item, com resumo em uma linha e exit code.

## 4. Aceite — TEST PLAN medido (01/10/2026, VPS `vmi3619453`)

`bash /opt/tre/dev/scripts/odoo/verificar-modulo-odoo.sh` →
**`RESULTADO: MODULO_ODOO_OK (51 itens, 0 falhas) modulo=transformativa_sales_ai
banco=tre_e03_t01_modulo imagens=odoo:19.0+postgres:16`**, **exit 0**.

| Passo | Medição |
|---|---|
| **1 instalação em banco limpo** | banco **não existia** (dropado antes) → criado pelo Odoo do zero; `odoo --init exit 0`; 554 linhas de log, **0 ERROR/CRITICAL**, `'Modules loaded.'` presente; `ir_module_module.state = installed`; `latest_version = 19.0.1.0.0` == manifesto; `license = LGPL-3` == manifesto; dependências gravadas `base crm` == declaradas; todas instaladas; 0 módulo pendurado |
| **2 teste do Odoo** | `odoo -u transformativa_sales_ai --test-enable exit 0`; `odoo.tests.result: 0 failed, 0 error(s) of 6 tests when loading database 'tre_e03_t01_modulo'`; 0 linha `FAIL:`/`ERROR:`; sem `'At least one test failed...'`; módulo segue `installed` |
| **3 desinstalação** | `odoo shell` + ORM → `DESINSTALACAO_OK estado_antes=installed estado_depois=uninstalled`; estado no banco `uninstalled`; **0 resquício** em `ir_model_data`/`ir_ui_view`/`ir_model_fields` e 0 tabela com prefixo do módulo |
| **4 reinstalação (idempotência)** | `odoo --init` (2ª vez) **exit 0**, 0 ERROR/CRITICAL, módulo `installed` de novo com `latest_version = 19.0.1.0.0` |
| **limpeza / dev intocado** | banco descartável, `postgres` descartável, rede descartável e diretório de configuração removidos; **instância do dev com os mesmos 4 bancos antes e depois** (`odoo_dev, postgres, template0, template1`); `homolog`/`prod` com **0 arquivo** |

**Identidade do que foi testado:** imagem `odoo:19.0` no digest
`sha256:77bac5cd1e065210828f34883a7f76740b7373d06dd3a5a55d3eeb31ee2f85cd` (a mesma do par de dev) e
`postgres:16` no digest `sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54`.
sha256 do módulo sob teste (igual no repo e na VPS):
`__manifest__.py cd84f4ec…`, `__init__.py 04812263…`, `tests/__init__.py 6c4150e5…`,
`tests/test_modulo_base.py ea75d283…`, `README.md abaa206b…`, `.gitkeep e3b0c442…` (vazio).
Scripts do aceite (também iguais nos dois lados): `verificar-modulo-odoo.sh 72d00aa1…`,
`manifesto_do_modulo.py f314948c…`, `desinstalar_modulo.py b859b3c6…`.

**Logs brutos:** `/opt/tre/dev/evidencias/t_c536ce86/` — `verificacao-completa.out` (51 itens + `EXIT=0`),
`manifesto.out`, `prova-de-dente.out`, `bateria.out` e `logs/{1-instalacao,2-teste,3-desinstalacao,4-reinstalacao}.log`.

## 5. Provas negativas — o aceite tem dentes

`bash /opt/tre/dev/scripts/odoo/verificar-modulo-odoo.sh --prova-de-dente` →
**`RESULTADO: MODULO_ODOO_DENTE_OK (2 provas, 0 falhas)`**, **exit 0**. Cada prova roda numa
**cópia** do módulo (o módulo real não é tocado) e espera **reprovação**:

| Prova | Mutação | Resultado medido |
|---|---|---|
| **Dente 1** | `version` → `18.0.1.0.0` (cópia do módulo) | `FALHOU versao do manifesto (18.0.1.0.0) diferente da esperada (19.0.1.0.0)`; `FALHOU serie da versao (18.0) != serie do Odoo na imagem (19.0)`; `FALHOU serie da versao (18.0) diferente da esperada (19.0)` → `MODULO_ODOO_FALHOU (19 itens, 3 falhas)`, **exit 1** |
| **Dente 2** | teste plantado que falha (`test_99_prova_de_dente`) | `FALHOU odoo --test-enable exit 1`; `FALHOU runner do Odoo: 1 failed, 0 error(s) of 7 tests`; log com `Starting TestModuloBase.test_99_prova_de_dente` e `FAIL:` → `MODULO_ODOO_FALHOU (36 itens, 2 falhas)`, **exit 1** |

O dente 2 mostra também que **o exit code do comando do Odoo reflete teste reprovado**
(`exit 1`) — o item de exit code não é decorativo.

### 5.1 Logs do aceite × logs do dente (defeito `TRE-W2-E03-T01-D02`, consertado em 01/10/2026)

O modo `--prova-de-dente` **não escreve no diretório do aceite**: cada prova usa um
diretório próprio (`$TRE_LOG_DIR/dente/prova-1`, `.../prova-2`) e os `.out` do dente ficam
em `$TRE_LOG_DIR/dente/`. Rodar a bateria inteira (aceite → dente) com o **mesmo**
`TRE_LOG_DIR` deixa os 4 logs do aceite intactos — medido: `sha256` idêntico antes e depois
das provas, e os 4 arquivos seguindo com o banco do aceite (`tre_e03_t01_modulo`), enquanto
o log do dente (`logs/dente/prova-2/2-teste.log`) cita o banco mutado.

Isso não era verdade antes do conserto: o sub-run do dente herdava o `TRE_LOG_DIR` do
chamador **por ambiente** e usava os mesmos nomes de passo, então encadear aceite → dente
sobrescrevia `1-instalacao.log` e `2-teste.log` do aceite com a execução mutada e a
evidência bruta do aceite passava a existir só no console. Reproduzido com o script anterior
(`72d00aa1…`), aceite (51/51, exit 0) seguido de `--prova-de-dente` no mesmo diretório:
`1-instalacao.log` ficou com **547** referências ao banco `tre_e03_t01_modulo_dente` e
`2-teste.log` com **33** (os passos 3 e 4, que o dente não executa, seguiam do aceite).

Além do diretório próprio, o dente ganhou uma **guarda fail-closed**: antes das provas ele
fotografa o `sha256` dos `[1-4]-*.log` do aceite e, no fim, imprime
`OK logs de passo do aceite intactos depois das provas (N arquivo(s) com sha256 identico)`
ou reprova o dente com `FALHOU o modo dente mexeu nos logs de passo do aceite …`. O controle
negativo (cópia do script com o caminho compartilhado de volta) é medido e registrado no
card `t_5c4fc7ac` / `docs/operations/registro-de-execucoes.md`.

### 5.2 As duas formas de chamada (defeito `TRE-W2-E03-T01-D04`, consertado em 01/10/2026)

O cabeçalho do script documenta a chamada pelo **nome**, de dentro do diretório do script
(`bash verificar-modulo-odoo.sh --prova-de-dente`). O modo de dente era o **único** que re-invocava
o próprio arquivo — e fazia isso com `"$0"`: chamado por nome simples, `$0` é um nome **sem
diretório**, que não está no `PATH`, e a re-invocação morria com

```
verificar-modulo-odoo.sh: line 103: verificar-modulo-odoo.sh: command not found
FALHOU dente 1: versao mutada NAO reprovou — o item de versao nao tem dente
```

ou seja: as duas provas eram acusadas de **não ter dente** (`MODULO_ODOO_DENTE_FALHOU (2 prova(s)
sem dente)`, exit 1) quando o que falhou foi a **invocação** — *fail-closed*, mas com diagnóstico
falso e alarmante (diz que o aceite é oco justamente para quem foi ler os dentes). A bateria do E03
não pegou porque chama por **caminho absoluto**, forma em que `$0` resolve.

Conserto: o script resolve o próprio caminho em `EU="$(readlink -f "$0")"` (usado também para
derivar `AQUI`) e **toda** re-invocação usa `bash "$EU" --…`. Sub-run **sem** linha `RESULTADO:`
passou a ser reportado como **falha de invocação**, com contador próprio
(`…, N falha(s) de invocacao)`) — nunca como "item sem dente"; sem caminho resolvido o modo reprova
antes de qualquer prova. Medido nas **duas formas** (VPS, 01/10/2026): `bash verificar-modulo-odoo.sh
--prova-de-dente` (cwd = diretório do script) e `bash /caminho/absoluto/…/verificar-modulo-odoo.sh
--prova-de-dente` → `RESULTADO: MODULO_ODOO_DENTE_OK (2 provas, 0 falhas)`, **exit 0** nas duas.
Controle negativo (re-invocação apontada para caminho inexistente) → `2 falha(s) de invocacao` e
**0** "prova(s) sem dente". Evidência e controles no card `t_025f9a2a`.

## 6. Rollback

**Nível 1 — desinstalar o módulo (rollback declarado no card):** é o **passo 3** do aceite e está
medido (`DESINSTALACAO_OK estado_antes=installed estado_depois=uninstalled`; 0 resquício no
banco; aceite volta a passar na reinstalação do passo 4). Em qualquer base onde o módulo venha
a ser instalado:

```bash
docker run --rm -i --network <rede> -v <conf>:/etc/odoo/odoo.conf:ro \
    -v <modulo>:/mnt/extra-addons/transformativa_sales_ai:ro -e TRE_MODULO=transformativa_sales_ai \
    --entrypoint odoo odoo:19.0 shell -d <banco> --no-http < scripts/odoo/desinstalar_modulo.py
```

O marcador `DESINSTALACAO_OK`/`DESINSTALACAO_FALHOU` existe porque `odoo shell` é console
interativo: exceção imprime traceback e **o processo ainda termina com exit 0** — quem confere é
o marcador **e** o estado lido no banco, nunca o exit code sozinho.

**Nível 2 — reverter o card:** `git revert` do commit do card; nada no ambiente fica para trás
(o verificador limpa a dupla descartável; a instância do dev é conferida bancos-antes ×
bancos-depois).

## 7. Isolamento: por que o aceite roda numa dupla descartável própria

O AC pede **banco limpo**. O `odoo_dev` **não é limpo** (carrega o funil do `TRE-W2-E02-T01`),
e medir a instalação lá não provaria o critério — provaria outra coisa (convivência com o dev).

Além disso, na primeira rodada deste verificador (que usava `pg-odoo-dev`) apareceu um
comportamento do ambiente que vale registrar: **o Odoo do dev abre sessão em qualquer banco
novo da instância, sem ninguém pedir**. Medido: banco probe `tre_probe_cron_<pid>` criado do
zero em `pg-odoo-dev` ganhou sessão de `client_addr 172.18.0.3` (= IP do `odoo-dev`) com
`application_name = odoo-1`, `state = idle`, em ~30s; e o `dropdb` do banco de teste morreu com
`database "tre_e03_t01_modulo" is being accessed by other users / There are 3 other sessions
using the database`. Ou seja: um banco criado na instância do dev fica **sob influência do
serviço do dev** (varredura de bancos do processo de cron), o que polui o teste e atrapalha a
limpeza.

Por isso o verificador sobe a **sua própria dupla** (`postgres:16` + `odoo:19.0`, as mesmas
imagens e digests do par de dev, rede própria, senha gerada na hora) e a remove no fim. O
`odoo-dev`/`pg-odoo-dev` **não é tocado** — o próprio verificador mede os bancos da instância
antes e depois e reprova se mudarem.

## 8. Defeitos encontrados nesta execução (e conserto)

Todos achados **executando**, nenhum por leitura — e cada conserto foi remedido:

1. **Nome técnico do módulo sempre errado (mascarava o AC2).** O verificador lia o manifesto
   dentro do container montando o módulo em `/modulo`; o parser usa o nome do diretório como
   nome técnico, então o item acusava `diretorio (modulo) diferente do modulo
   (transformativa_sales_ai)` para qualquer módulo. Conserto: montar em
   `/leitura/<nome-real-do-modulo>` — e o item passou a medir de verdade o que promete.
2. **O Odoo do dev entra em qualquer banco novo da instância** (§7), e a limpeza morria com
   "being accessed by other users". Conserto: dupla descartável própria + `dropdb --force`.
3. **Consulta de dependências com coluna ambígua.** O item "dependências gravadas == declaradas"
   rodava `select name from ir_module_module_dependency d join ir_module_module m ...` →
   `ERROR: column reference "name" is ambiguous`; o `stderr` é descartado no helper, então o
   item **reprovava sempre** (falso negativo que bloquearia o aceite). Conserto:
   `select d.name` — medido depois como `dependencias gravadas no banco == declaradas (base crm)`.
4. **`ir_module_module_dependency.state` é campo calculado (não tem coluna).** O item "todas as
   dependências instaladas" consultava `d.state` em SQL, que **não existe** no banco. Conserto:
   conferir o estado no módulo dependente (`join ir_module_module dep on dep.name = d.name`).
5. **Falso positivo do scanner de segredo do repo.** O item de segredo do `scripts/secret_scan.sh`
   reprovou `verificar-modulo-odoo.sh` por escrever a chave na forma literal
   `"db_password"` seguida de `=` e uma variável (`"<chave> = <variável>"`) — o valor é variável, não
   segredo. Conserto **no código, não no scanner** (mesma convenção de
   `scripts/provision/instalar-odoo-dev.sh`): as chaves `db_password`/`admin_passwd` passam por
   `CHAVE_*='...'` + `printf '%s = %s\n'`. Medido depois: `secret_scan.sh` → `PASS (nenhum segredo versionado)`.
6. **Regressão da correção 5, pega por reexecutar:** ao trocar o `echo` por `printf` eu deixei o
   `echo "admin_passwd = $MASTER"` antigo no bloco e o `odoo.conf` passou a ter a opção **duas
   vezes** → `configparser.DuplicateOptionError: option 'admin_passwd' in section 'options' already
   exists`, instalação **exit 1** e o aceite caiu para `MODULO_ODOO_FALHOU (24 itens, 3 falhas)`.
   Conserto: remover a linha antiga. Medido depois: aceite de volta a `MODULO_ODOO_OK (51 itens, 0
   falhas)` — é a razão de a bateria inteira ser reexecutada a cada edição do verificador.

Nota de método: o erro 6 só apareceu porque a bateria (manifesto + dentes + aceite) é **reexecutada
depois de qualquer edição** do verificador; a primeira versão do aceite já tinha rodado verde antes
dele existir.

## 9. Pendências declaradas (não são deste card)

- **Publicação do módulo na cópia operacional `/opt/tre/repo`**: o `odoo-dev` monta
  `/opt/tre/repo/odoo/addons` (read-only) como `/mnt/extra-addons`, mas a cópia está na linha
  `fix/t_daca4bda-enforcement`, **divergente do `develop`** — publicar uma árvore nascida do
  `develop` apagaria o enforcement (regressão já registrada no `CHANGELOG`). Enquanto as duas
  linhas não se encontrarem, o módulo é medido pelo caminho deste runbook (dupla descartável +
  cópia do repo na VPS) e **não** aparece no `odoo-dev`. As cards `E04`+ que precisarem do
  módulo vivo no `odoo_dev` herdam esta decisão.
- **Instalar/desinstalar no `odoo_dev`** não é o que o AC pede (ele pede banco **limpo**) e não
  foi feito; fica como opção para quem for configurar dado de negócio.
- **Ratificação da versão do Odoo (19.0)** pelo Anderson: herdada de `TRE-W2-E01-T01`.
- **Homologação**: estágio 7 é do Anderson; este runbook entrega a evidência, não a aprovação.
  A verificação independente (estágio 6) é do perfil `tester`.
