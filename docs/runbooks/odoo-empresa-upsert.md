# Upsert de empresa (`res.partner`) pela API controlada — `POST /tf/api/v1/empresa_upsert`

Runbook do card **`TRE-W3-E01-T02`** (board `transformativa-revenue-engine`, card `t_cdc21b43`),
onda W3, épico E01. Artefatos: `odoo/addons/transformativa_sales_ai/api/politica_api.json`
(política **1.1.0**), `api/motor.py`, `controllers/api_controlada.py`,
`tests/test_empresa_upsert.py` (19 testes de aceite), `scripts/odoo/testar_motor_api.py`
(suíte pura, 75 itens) e `scripts/odoo/verificar-empresa-upsert.sh` (aceite com dentes).

Os cinco campos que o doc 11 §2 exige e não detalha (DESENHO, ACCEPTANCE, TEST, ROLLBACK, RISK)
estão aqui (§1 a §5) e no comentário de abertura do card.

Este card é a **primeira operação de escrita de NEGÓCIO** da porta única entregue por
`TRE-W3-E01-T01` — o runbook `odoo-api-controlada.md` continua sendo a referência do *mecanismo*
(rota, autenticação, envelope, taxonomia, guarda de ambiente); aqui está o que a operação
`empresa_upsert` decide e como isso é medido.

---

## §1 Desenho — a identidade é o contrato

O consumidor (n8n, `TRE-W3-E02-T01`) espelha a empresa pesquisada do `sales_intelligence`
(`organizations`, contrato §5 / `canonical_ids.odoo_map`) no espelho operacional do CRM
(`res.partner`). A operação inteira é **declaração**, não código: o controlador não sabe o que é
uma empresa — quem sabe é a política.

### Campos declarados (o que o card tinha de definir antes de escrever código)

| Grupo | Campos |
|---|---|
| Escrita permitida | `name`, `is_company`, `tf_company_id`, `tf_cnpj`, `tf_domain`, `tf_linkedin_url`, `tf_priority_score` — **e mais nada**: campo fora dessa lista é `422 campo_nao_declarado` |
| Obrigatório | `name` (sem ele: `422 campo_obrigatorio_ausente`) |
| Identidade (`campos_de_identidade`) | `tf_company_id` → `tf_cnpj` → `tf_domain` → `tf_linkedin_url` — o canônico primeiro, depois os **fortes de dedup do contrato §5**, nessa ordem |
| Valor fixo (`valores_fixos`) | `is_company: true` — a operação é de **empresa**; o chamador não decide esse valor |

### A decisão, em três perguntas

1. **Identidade.** Cada identificador declarado que veio **com valor** no payload casa registros
   (`limit=2` por busca de propósito: mais de um achado já é ambiguidade). A união dos achados é o
   conjunto candidato.
2. **Ambiguidade.** Conjunto com **mais de um** registro → `409 valor_ambiguo` e **nada** é escrito
   nem alterado. O contrato (§5) manda **reportar** ambiguidade, nunca resolvê-la por heurística —
   escolher "o mais antigo" ou "o que tem score maior" seria inventar uma regra que o negócio não
   pediu, sobre o dado do cliente.
3. **Upsert.** Um candidato → **atualiza** (`write`) os campos declarados; nenhum → **cria** com os
   valores declarados **mais** os valores fixos. A resposta sempre diz o que aconteceu
   (`acao_efetiva: criar|atualizar`) e os `ids` tocados.

Identificadores do mesmo pedido que casam **o mesmo** registro não são ambiguidade — é o caso
normal de um pedido que manda `tf_company_id` **e** `tf_cnpj` (teste `test_10` e item HTTP próprio).

### Split motor × controlador (mantido do E01-T01)

| Peça | Arquivo | Responsabilidade |
|---|---|---|
| Política | `api/politica_api.json` | a fonte da verdade, campo a campo (declaração versionada **1.1.0**) |
| Motor (decisão) | `api/motor.py` | valida a chamada, resolve a identidade, aplica valor fixo e monta o **plano**; **não importa `odoo`** (exercitável sem subir Odoo) |
| Controlador (execução) | `controllers/api_controlada.py` | executa o plano **pelo ORM** e grava a trilha; não decide nada |

### A escrita que falha não deixa rastro (defeito fechado neste card)

O ORM levanta **no meio** do `create`/`write` (constraint, campo inválido). Capturar a exceção
depois disso devolvia o envelope correto (`422 valor_invalido`) com o registro **já gravado** na
transação — medido no aceite (`test_15`: 4 registros depois da recusa, 3 antes). A execução passou a
rodar dentro de um **savepoint** (`request.env.cr.savepoint()`): a operação que falha é descartada e
a transação continua viva para responder o envelope. Vale para **toda** escrita da API, não só o
upsert. O teste que mede isso ficou no card (`test_15`), e o item correspondente foi acrescentado ao
verificador (`a recusa por UUID invalido NAO gravou registro`).

### Fronteira do card

A operação de **empresa**. `contact upsert` (`TRE-W3-E01-T03`), `opportunity upsert`
(`TRE-W3-E01-T04`) e `activity create` (`TRE-W3-E01-T05`) entram **declarando** a operação na
política (§9). A **dedup por `idempotency_key`** (retry/replay: mesma chave = mesmo efeito, sem
repetir escrita) é do `TRE-W3-E02-T02` — aqui a chave é exigida, validada e registrada, e a
garantia de "não duplicar" que este card entrega vem da **identidade**. Reconciliar empresas que já
estejam duplicadas na base é do `TRE-W3-E04-T01`.

---

## §2 ACCEPTANCE — o que tem de valer (AC1..AC8)

| AC | O que afirma | Onde é medido |
|---|---|---|
| AC1 | Operação declarada e versionada: `empresa_upsert` na política real (versão 1.0.0 → **1.1.0**), tipo `escrita`, modelo `res.partner`, ação `upsert`, `requer_idempotency_key: true`; servida pela **mesma** porta única (1 rota no controlador) | suite do Odoo `test_01` + passo 3 (curl) + passo 4 (greps) |
| AC2 | Identidade **declarada**, não literal: `campos_de_identidade` na ordem canônica → fortes; payload sem nenhum identificador com valor → `422 identificador_ausente` | suite pura (5 itens) + suite do Odoo `test_07` + curl |
| AC3 | Upsert idempotente **por identidade**: cria uma vez, atualiza depois (`acao_efetiva`); contagem por identidade conferida no banco (0 duplicata em chamadas repetidas); identidade pelo canônico e pelos fortes (CNPJ → domínio → LinkedIn) | suite do Odoo `test_02..test_06` + curl + `psql` de leitura no banco descartável |
| AC4 | Ambiguidade **reportada**, nunca resolvida: união dos casados com mais de um registro → `409 valor_ambiguo`, nada criado e nada alterado | suite do Odoo `test_08`/`test_09` + curl (contagem antes/depois + os dois registros intactos) |
| AC5 | Semântica de empresa: `valores_fixos: {is_company: true}` declarado, aplicado na criação e mantido na atualização (medido no banco); chamador divergindo → `422 campo_fixo_divergente` | suite do Odoo `test_03`/`test_11` + curl + `psql` (`is_company = t`) |
| AC6 | Contrato de integração: `idempotency_key` exigida (ausente/fora do formato → 422); `dry_run` descreve **sem** tocar o dado (nem cria nem atualiza); `correlation_id` ecoado; `tf_company_id` fora do formato UUID → `422 valor_invalido` **sem gravar** | suite do Odoo `test_12..test_17` + curl (contagens antes/depois) |
| AC7 | Guarda de ambiente + rastro: a escrita só atende no ambiente permitido pela política (`dev`); fora dela → `503 ambiente_nao_permitido` sem escrever; **uma** linha `TF_API_AUDIT` por chamada autenticada, sem payload e sem token | suite do Odoo `test_18`/`test_19` + passo 3c/3d (`docker logs` do servidor, contagem vs. chamadas autenticadas) |
| AC8 | Ambiente intocado: dupla descartável própria (`postgres:16` + `odoo:19.0`); dev/homolog/prod medidos antes e depois; **nenhum** DDL, migration ou escrita em `sales_intelligence` | guardas + limpeza do verificador |

**Lacuna declarada:** dedup por chave (`E02-T02`), rate limit/cache/auditoria durável (`E05-T01`) e
merge de duplicatas já existentes (`E04-T01`) estão **fora** deste card — dito aqui para não virar
leitura otimista.

---

## §3 TEST — como cada AC é medido (na VPS, por execução real)

```bash
# na VPS, a partir de ARQUIVO, no checkout do card
cd /opt/tre/evid-t_cdc21b43/repo-r1
TRE_LOG_DIR=/opt/tre/evid-t_cdc21b43/logs-aceite bash scripts/odoo/verificar-empresa-upsert.sh
bash scripts/odoo/verificar-empresa-upsert.sh --prova-de-dente
```

| Modo | O que roda |
|---|---|
| (padrão) | passo 0 suíte pura do motor → guardas do ambiente → dupla descartável própria → passo 1 instalação em banco limpo → passo 2 suíte do Odoo (`--test-enable`, piso de testes medido) → passo 3 **HTTP real por `curl`** → passo 3c auditoria no log do servidor → **passo 3d** guarda de ambiente da escrita (servidor novo, com o parâmetro trocado pelo ORM) → passo 4 contrato (greps e leitura da política) → limpeza + dev/homolog/prod conferidos |
| `--apenas-motor` | só o passo 0 (sem Docker) |
| `--apenas-suites` | passos 1 e 2 |
| `--apenas-http` | passos 1, 3 e 3d (usado pelos dentes) |
| `--prova-de-dente` | 3 mutações em **cópias** do módulo + 2 controles do próprio harness (abaixo) |

Variáveis: `TRE_MODULO_DIR` (padrão: o módulo do próprio checkout), `TRE_BANCO` (padrão
`tre_e01_t02_empresa`, formato `^tre_[a-z0-9_]+$`, nome do ambiente é recusado), `TRE_IMAGEM`/
`TRE_IMAGEM_PG`, `TRE_LOG_DIR`, `TRE_PISO_DE_TESTES`, `TRE_MANTER_BANCO=1`.

Identidade da chave de API: `scripts/odoo/preparar_api_teste.py` roda por `odoo shell` com o
diretório descartável montado em `/preparo` (700) e grava a chave em `/preparo/chave.txt` (**600**);
o `curl` lê os cabeçalhos de um **arquivo de configuração 600** (`curl --config`) — o token não
aparece em stdout, log nem em argumento de comando (`ps`).

**Isolamento:** o verificador sobe a **sua** dupla descartável em rede própria (senha por
`openssl rand`), e não usa `pg-odoo-dev`, `odoo_dev` nem a cópia operacional — o Odoo do dev abre
sessão em qualquer banco novo da instância, então medir ali dentro mediria o ambiente errado.

### A prova de dente (e o que a torna não-decorativa)

| Dente | Mutação (em **cópia** do módulo) | Item que **tem** de reprovar |
|---|---|---|
| 1 | política **sem** a operação `empresa_upsert` | `empresa_upsert declarada como escrita com idempotency_key exigida` |
| 2 | controlador **sem** o portão de ambiguidade (`if len(registros) > 1:` → `if False:`) | `recusa nomeia valor_ambiguo (ambiguidade e' reportada, nao resolvida)` |
| 3 | motor **sem** aplicar o valor fixo (`valores_finais.update(fixos)` → `update({})`) | `o parceiro criado e' EMPRESA (is_company=true, medido no banco)` |

Um dente só conta se o sub-run (a) **passou** pelas guardas de ambiente, (b) **chegou** à fase HTTP
(`OK servidor Odoo no ar`) e (c) reprovou **o item que a mutação quebra**. Mais dois **controles do
próprio harness**, com entradas sintéticas: um sub-run que reprova por ambiente **não** conta como
dente, e mutação que não muda nada é reportada como `mutacao sem dente`. Sem isso a prova de dente é
fail-open: um ambiente quebrado encerra o sub-run em `FALHOU` e o harness imprimiria "o dente tem
dente" sem ter medido nada — é exatamente o defeito `t_fa9db205` medido pelo `tester` no aceite do
E01-T01, que **não** se repete aqui. A âncora do alvo é o sha256 do conteúdo do módulo (sem
`__pycache__`), conferida **antes e depois** das provas.

---

## §4 ROLLBACK

- **Reverter o commit do card** (módulo, scripts, runbook): não há migration, DDL, tabela nova em
  `sales_intelligence` nem dado migrado. O módulo **ainda não está instalado** em ambiente
  persistente (o `.gitkeep` segue sendo o que está publicado em `/opt/tre/repo`).
- **Rollback por política (sem instalar nada):** retirar a operação de `api/politica_api.json` faz a
  chamada responder `404 operacao_nao_declarada` — é o que o **dente 1** mede, e é o caminho mais
  barato para desligar a escrita sem tocar em código.
- **Desligar tudo sem reverter:** sem `tf.api.ambiente` declarado a API recusa **tudo** com
  `503 ambiente_nao_declarado` (fail-closed do ADR-005).
- **Onde o módulo já esteja instalado:** desinstalar pelo ORM
  (`python3 scripts/odoo/desinstalar_modulo.py` com `odoo shell`) remove o controlador, a política e
  os campos `tf_*`.
- **Desfazer uma escrita de negócio já feita:** a empresa é um `res.partner` comum; `unlink` pelo ORM
  (ou `active = false`, se o registro tiver histórico) — a API não tem operação de exclusão
  declarada, e o contrato não pede uma.
- Sem impacto em produção, homologação, n8n, backup e Sales Intelligence.

---

## §5 RISK

- **Médio.** É a primeira escrita de **negócio** por consumidor externo no CRM. O desenho fecha os
  caminhos: nasce em dev e só em dev (ADR-005); identidade **declarada** (não heurística);
  ambiguidade fail-closed (nada é escrito); `is_company` fixo declarado; `dry_run` disponível para
  ensaio; chave de idempotência exigida; trilha por chamada; escrita que falha **não** deixa rastro
  (savepoint).
- **Custos honestos do que este card NÃO resolve:**
  (a) **retry/replay** por `idempotency_key` é do `E02-T02` — entre duas chamadas com a mesma
  identidade a operação é idempotente, mas *sem* identidade não há como reencontrar o registro
  (é recusa nomeada, não invenção de identidade);
  (b) sem CNPJ, domínio, LinkedIn **nem** `tf_company_id` a empresa **não entra** (`422
  identificador_ausente`) — o que evita duplicata por conteúdo, mas exige que o produtor mande pelo
  menos um identificador do contrato;
  (c) **merge** de empresas já duplicadas na base é do `E04-T01` — aqui, se dois registros casarem o
  mesmo pedido, a resposta é `409` e ninguém é alterado;
  (d) `tf_priority_score` é escrito como o consumidor mandar (não há regra de score nesta camada:
  o score canônico vive no `sales_intelligence`);
  (e) a política é lida por chamada (§1 do runbook da porta única) — cache/observabilidade é do
  `E05-T01`;
  (f) a ACL do dono da chave vale: se ela não permitir `create`/`write` em `res.partner`, a resposta
  é `403 acesso_negado` (**medido** no aceite: o usuário de integração cria e atualiza).

---

## §6 O contrato da operação

```
POST /tf/api/v1/empresa_upsert
Authorization: Bearer <chave de API do Odoo>      (auth='bearer', tipo 'rpc')
Content-Type: application/json
```

```json
{"idempotency_key": "tre-e01-t02-sync-000123",
 "correlation_id": "run-2026-10-02-000123",
 "dry_run": false,
 "parametros": {"valores": {
    "name": "Alfa Consultoria Ltda",
    "tf_company_id": "3f1c0a52-6f0e-4a2b-9d3e-000000000001",
    "tf_cnpj": "11.222.333/0001-81",
    "tf_domain": "alfa.example",
    "tf_priority_score": 72.5}}}
```

Sucesso (`200`) — `acao_efetiva` diz o que aconteceu:

```json
{"ok": true, "operacao": "empresa_upsert", "acao": "upsert", "ambiente": "dev",
 "politica_versao": "1.1.0", "correlation_id": "run-2026-10-02-000123",
 "idempotency_key": "tre-e01-t02-sync-000123", "dry_run": false,
 "dados": {"acao_efetiva": "criar", "ids": [37]}}
```

Com `dry_run: true` a resposta descreve **sem tocar o dado**:
`{"dry_run": true, "acao_efetiva": "criar", "criaria": ["is_company", "name", ...]}` (registro novo)
ou `{"dry_run": true, "acao_efetiva": "atualizar", "id": 37, "atualizaria": [...]}` (registro
existente).

Recusa — envelope padrão da porta única (`ok: false`, `codigo`, `mensagem`).

---

## §7 Códigos de erro que este card acrescentou

| Código | HTTP | Quando |
|---|---|---|
| `identificador_ausente` | 422 | ação `atualizar`/`upsert` sem **nenhum** identificador declarado com valor no pedido (fail-closed: a API não inventa identidade) |
| `campo_fixo_divergente` | 422 | o chamador mandou o campo de `valores_fixos` com valor **diferente** do declarado (ex.: `is_company: false` numa operação de empresa) |
| `valor_ambiguo` | 409 | os identificadores do pedido casaram **mais de um** registro — agora também na `acao: upsert` (antes só na `atualizar`) |

Os demais (`campo_nao_declarado`, `campo_obrigatorio_ausente`, `valor_invalido`,
`idempotency_key_ausente`, `idempotency_key_invalida`, `ambiente_nao_permitido`, …) seguem a
taxonomia única de `CODIGOS_DE_ERRO` no motor — código novo sem entrada ali é erro de programação, e
a suíte pura cobre (`todo codigo de erro tem status 4xx/5xx`).

---

## §8 Armadilhas medidas nesta rodada

1. **Recusa que gravava.** Exceção do ORM capturada **depois** do `INSERT`: envelope `422` com o
   registro na transação. O teste de aceite pegou (`4 != 3`) e o conserto é o **savepoint** (§1).
   Fica a lição: "a operação foi recusada" e "o banco continua igual" são **duas** afirmações — a
   segunda precisa ser medida no banco, não inferida do status HTTP.
2. **`ir.config_parameter` é cacheado pelo ORM.** Trocar `tf.api.ambiente` por `UPDATE ... SET` no
   banco com o servidor de pé **não** invalida o cache: a medição daria verde falso. O passo 3d troca
   o parâmetro pelo **mesmo caminho do preparo** (`set_param` no ORM, com commit) e **sobe um
   servidor novo** para medir o `503`. Quem medir ambiente por SQL direto vai medir a resposta
   antiga.
3. **Campo obrigatório e identidade não são a mesma recusa.** Um `upsert` sem `tf_cnpj` numa
   operação em que ele é `campos_obrigatorios` falha **antes**, por campo obrigatório
   (`campo_obrigatorio_ausente`); `identificador_ausente` é a recusa de identidade quando ela **não**
   é campo obrigatório. Um item antigo da suíte pura esperava o nome errado e passava pelo motivo
   errado — foi reescrito com o rótulo dizendo qual dos dois é.
4. **Item de teste datado expira sozinho.** O aceite do E01-T01 afirmava `politica_versao == "1.0.0"`
   e a lista **fechada** de operações — a chegada desta operação (1.1.0) os quebraria sem nenhuma
   regressão por trás. Os dois passaram a **ler o próprio artefato** (marcador
   `ANCORA:POLITICA_EM_VIGOR`), na suíte do Odoo e no verificador do E01-T01.
5. **Prova de dente fail-open (herança `t_fa9db205`).** O harness copiado do E01-T01 considerava
   "dente que pegou" qualquer sub-run que terminasse em `FALHOU` — inclusive quando ele abortou por
   imagem ausente. Aqui a avaliação exige guardas OK + fase HTTP alcançada + **o item alvo**
   reprovando, e há dois controles sintéticos do próprio harness (§3).
6. **`docker exec` não lê o `stdin` do chamador:** todo SQL de leitura roda por
   `psql -tAc "<consulta>"` como argumento (`psql_bd`), não por pipe — o pipe fica vazio e a consulta
   "passa" sem medir.
7. **SQL cru não herda default do ORM.** O `active = True` de `res.partner` é default **de ORM**, não
   default de coluna: linha inserida por `INSERT` sem `active` fica **NULL** — existe para o SQL
   (`select count(*)` conta) e é **invisível** para o `search` do Odoo (que filtra `active = true`).
   Medido aqui: a API não casou os dois registros do caso ambíguo e **criou um terceiro** em vez de
   recusar (`HTTP 200 criar`, 9 → 10 registros); o verificador agora insere com `active = true` e
   **lê o fixture de volta pela própria API** (`crm_registros_ler`) antes de medir — fixture que o
   ORM não vê reprova como item próprio, em vez de virar medição de outra coisa. Vale para qualquer
   carga por SQL (a ingestão do `E03`) que precise ser visível ao ORM.
8. **O container `odoo:19.0` roda como uid 100/gid 101:** o diretório descartável do preparo precisa
   ser gravável por ele (`chown 100:101`), senão o `open()` da chave falha mesmo com o chamador root
   no host.

---

## §9 Como declarar a próxima operação de escrita (receita para `E01-T03..T05`)

1. Acrescente a operação em `api/politica_api.json` (`nome`, `tipo: "escrita"`, `modelos` com
   `campos`, `campos_obrigatorios`, `acao`, `requer_idempotency_key: true`, `aceita_dry_run`) e
   **suba a `versao`**. Para a identidade, use `campos_de_identidade` (lista, ordem = prioridade de
   busca); para o valor que a operação decide sozinha, `valores_fixos`. Valores sensíveis de
   segurança/ambiente **não** entram aqui (a política é artefato publicado).
2. Rode `python3 scripts/odoo/testar_motor_api.py`: a validação da política é fail-closed —
   identificador fora de `campos`, lista de identidade vazia/duplicada, valor fixo fora de `campos`
   ou campo ao mesmo tempo obrigatório **e** fixo são reprovados **antes** de qualquer deploy.
3. **Nada de código novo no controlador**: operação declarada já é servida (o motor monta o plano de
   leitura/escrita e o controlador executa). Só uma **ação** nova (além de `ler`/`criar`/`atualizar`/
   `upsert`) ou uma **fonte** nova (como `capacidades`) exigem galho no motor — e aí a suíte pura
   cresce junto.
4. Ao escrever o aceite da operação nova, **não copie literais que expiram** (versão da política,
   lista de operações, quantidade de testes): leia o próprio artefato (marcadores
   `ANCORA:POLITICA_EM_VIGOR` e `ANCORA:OPERACOES_DE_LEITURA_DO_E01_T01` são o modelo). E, se
   copiar o harness de dentes, mantenha a avaliação **não fail-open** (§3).
5. A operação de `opportunity upsert` (`E01-T04`) terá o mesmo desenho com outro modelo
   (`crm.lead`, identidade `tf_opportunity_id` → ...); a de `contact upsert` (`E01-T03`) precisa
   decidir explicitamente se aceita parceiro **pessoa** (`is_company: false` fixo) — e, se aceitar,
   o valor fixo é a forma de dizer isso na política, sem código.

---

## §10 Lacunas e pendências herdadas

- **Dedup por `idempotency_key`** (retry/replay): `TRE-W3-E02-T02`.
- **Observabilidade durável / rate limit / cache de política**: `TRE-W3-E05-T01`.
- **Reconciliação/merge de empresas duplicadas**: `TRE-W3-E04-T01`.
- **Publicação do módulo na cópia operacional** (`/opt/tre/repo`): pendência herdada do E03-T01 —
  hoje está publicado só o `.gitkeep` de `odoo/addons/`.
- **Consumidor real (n8n) chamando a operação**: `TRE-W3-E02-T01` — aqui o consumidor é medido por
  `curl` de fora do processo.
- **Homologação** (estágio 7) é do Anderson; a revisão independente (estágio 6) é do perfil
  `tester`. Quem entrega **não** homologa.

---

## §11 Aceite medido (VPS do dev, 02/10/2026)

O artefato entregue é o **commit desta branch** (`feature/TRE-W3-E01-T02`). O que roda na VPS é uma
cópia **byte a byte** dele: o worktree foi enviado por `tar` para
`/opt/tre/evid-t_cdc21b43/repo-r1` e os **11 arquivos desta entrega** (política, motor, controlador,
as três suítes de teste, os dois verificadores de Odoo, o verificador estrutural e este runbook)
foram conferidos por `sha256` contra o worktree — **9/9 iguais** nas rodadas 1 e 2 do aceite e
**11/11 iguais** na rodada final.

**Rodada 1 — o aceite achou defeito (e é para achar).**
```
RESULTADO: EMPRESA_UPSERT_FALHOU (102 itens, 5 falha(s)) modulo=transformativa_sales_ai
           banco=tre_e01_t02_empresa imagens=odoo:19.0+postgres:16
```
As duas linhas do caso ambíguo (AC4) foram criadas por SQL cru **sem `active`**: o ORM não as
enxerga (o default de `active` é do ORM, não da coluna — §8), então a API não viu ambiguidade
nenhuma, criou um terceiro registro e o aceite reprovou o banco, não a resposta. Corrigido: o
preparo grava `active` explícito e o nome esperado passa a vir da chamada por CNPJ.

**Rodada 2 — verde.**
```
RESULTADO: EMPRESA_UPSERT_OK (104 itens, 0 falhas) modulo=transformativa_sales_ai
           banco=tre_e01_t02_empresa imagens=odoo:19.0+postgres:16
```
Dentro dos 104 itens: suíte pura do motor **`MOTOR_API_OK (75 itens)`**; instalação em banco limpo;
suíte do Odoo **`0 failed, 0 error(s) of 101 tests`** (os 19 testes novos + 82 dos cards anteriores,
sem regressão); HTTP real por `curl` **de fora do processo** (criar → atualizar sem duplicar pela
identidade canônica e pelos fortes; `409` ambíguo com contagem conferida antes e depois;
`422` de identificador ausente / valor fixo divergente / chave ausente ou fora do formato / campo
fora da declaração; dry-run que descreve e **não** escreve; `401` sem token e com token inválido;
`503` fora do ambiente medido com servidor novo em `homologacao`); auditoria lida **do log do
servidor** (`14 linhas para 14 chamadas autenticadas`), sem token e sem payload; greps de contrato.

**Rodada 3 — o artefato final, remedido.** Depois das correções no harness de dente (abaixo), o
aceite rodou de novo sobre o artefato final e deu a mesma saída: `EMPRESA_UPSERT_OK (104 itens, 0
falhas)`, com a auditoria medindo agora **`15 linhas para 15 chamadas autenticadas`** — o número
subiu de 14 para 15 porque a correção do AC4 acrescentou a chamada por CNPJ que define o nome
esperado. Registro desta rodada: `aceite-r3.out`.

**Prova de dente.** Cada mutação roda o aceite `--apenas-http` numa cópia mutada e **tem** de
reprovar o item que ela quebra; o harness só conta como dente o sub-run que passou pelas guardas de
ambiente e chegou à fase HTTP (a lição do defeito `t_fa9db205` do E01-T01). A rodada 1 dos dentes
reprovou o **próprio harness**: a conferência da mutação 1 era um `grep` pela palavra
`empresa_upsert`, que continua aparecendo na `descricao` da política com a mutação aplicada. A
rodada 2 reprovou de novo, agora por outro motivo medido: o `avaliar_dente` exigia o texto exato do
ramo `ok`, e o `falhou` do mesmo item traz o diagnóstico (texto diferente) — ou seja, o dente
dependia da **redação** do item, não do comportamento. Corrigido com marcadores múltiplos por
dente **e** um autoteste do harness (cada marcador tem de existir como texto de item no próprio
verificador).

Na rodada 3 as 3 provas e os 2 controles saíram todos `OK` — e o veredito saiu
`EMPRESA_UPSERT_DENTE_FALHOU (2 prova(s) sem dente)`: um **falso vermelho**, tão desonesto quanto um
falso verde. Os dois controles usam o contador `DENTE_FALHAS` como sinal de que pegaram o caso, e o
incremento ficava no contador — os controles passavam a contar como provas reprovadas. O incremento
passou a ser **desfeito no ramo de sucesso** (e mantido quando o controle falha, senão o harness
vira fail-open).

**Rodada 4 — verde:**
```
RESULTADO: EMPRESA_UPSERT_DENTE_OK (3 provas + 2 controles do proprio harness, 0 falhas)
```
As 3 provas: política sem a operação (`empresa_upsert`) → o item de AC1 reprova e o resto do aceite
cai junto (91 itens, 32 falhas); controlador sem o portão de ambiguidade → o `409` deixa de ser
exigido; motor sem aplicar `valores_fixos` → o parceiro nasce pessoa. Os 2 controles são do
**harness**, não do módulo: um sub-run que reprova por ambiente quebrado e uma mutação inócua têm de
ser reportados como **dente inconclusivo** / **mutação sem dente** — se o harness os contasse como
prova, a prova inteira seria decorativa. A rodada fecha com **19 itens `OK`, 0 `FALHOU`** e a guarda
externa confirmando que o artefato real não foi tocado (`29 arquivos`, mesmo `sha256` de antes das
mutações). Registro: `dente-r4.out`.

**Registros brutos (na VPS, em `/opt/tre/evid-t_cdc21b43/`):** `aceite-r1.out` (102 itens, com os
FALHOU originais preservados), `aceite-r2.out` e **`aceite-r3.out`** (104 itens, o artefato final),
`dente-r1.out` a `dente-r4.out` (o veredito válido é o `r4`), e os diretórios de log `logs-motor/`,
`logs-suites/`, `logs-suites-r2/`, `logs-aceite-r2/`, `logs-aceite-r3/` e `logs-dente*/`. Nenhum
valor de segredo em nenhum deles: a chave da API nasce na
VPS em arquivo `600` dentro do diretório descartável do preparo, é lida pelo `curl` por arquivo de
configuração (não aparece em `ps`, argumento ou log) e morre com o diretório.

**O que o aceite NÃO toca (medido):** nada em `/opt/tre/{homolog,prod}` (0 arquivo antes e depois),
o `odoo-dev`/`pg-odoo-dev` de pé (a dupla do aceite é própria, `e01t02-*`, criada e removida na
rodada) e **0** container ou rede residual ao fim.
