# API controlada do Odoo — `POST /tf/api/v1/<operacao>`

Runbook do card **`TRE-W3-E01-T01`** (board `transformativa-revenue-engine`, card `t_e0489efc`),
onda W3, épico E01. Artefatos: `odoo/addons/transformativa_sales_ai/api/` (política versionada +
motor), `odoo/addons/transformativa_sales_ai/controllers/api_controlada.py` (a rota),
`odoo/addons/transformativa_sales_ai/tests/test_api_controlada.py` (30 testes) e
`scripts/odoo/verificar-api-controlada.sh` (aceite com dentes).

Os cinco campos que o doc 11 §2 exige e não detalhava (DESENHO, ACCEPTANCE, TEST, ROLLBACK, RISK)
estão aqui (§1 a §5) e no comentário de abertura do card.

---

## §1 Desenho — a porta única, governada por declaração

Três peças, e a divisão entre elas é o que torna a API **controlada**:

| Peça | Arquivo | O que é |
|---|---|---|
| Política versionada | `api/politica_api.json` | A **fonte da verdade**: quais operações existem, qual modelo cada uma toca, **campo a campo**, com limite e exigência de chave de idempotência. Mora dentro do módulo, logo é versionada no repo **e** no artefato publicado |
| Motor (decisão) | `api/motor.py` | Valida a chamada contra a política e monta o **plano**. **Não importa `odoo`** de propósito: é exercitável sem subir Odoo (inclusive nos dentes) |
| Controlador (execução) | `controllers/api_controlada.py` | Uma rota, um verbo: lê o corpo e o ambiente, manda o motor montar o plano, executa o plano **pelo ORM** e grava uma linha de auditoria |

A porta é única e o caminho é fechado: **não existe** rota genérica de "execute qualquer
modelo/método/campo" (é exatamente o que o doc 02 §3 proíbe). O que não está declarado na política
é recusado com código próprio — nunca ignorado em silêncio.

**Alternativas descartadas** (registradas no card): (a) microserviço Python falando XML-RPC com o
Odoo — o chamador é o n8n, não impõe política a ninguém, e um serviço novo duplicaria superfície de
credencial/backup/observabilidade; (b) XML-RPC/JSON-RPC padrão do Odoo chamado direto pelo n8n —
é o caminho irrestrito que o doc 02 §3 proíbe.

**Fronteira do card:** o mecanismo e o portão. As operações de **negócio** (company/contact/
opportunity upsert e activity create) são dos cards `TRE-W3-E01-T02..T05`, que **declaram** a
operação na política. Este card declara duas operações reais de leitura
(`sistema_capacidades`, `crm_registros_ler`) e prova o caminho de **escrita** com política de
teste — sem duplicar operação de negócio.

### Uma decisão de execução declarada (desvio consciente do texto do AC1)

O AC1 fala em política "carregada e validada no start". A implementação carrega e valida a política
**a cada chamada** (`_politica()` no controlador, `carregar_politica()` no motor). Efeito prático
idêntico para o que o AC pede — política ilegível → **nada é servido** (500 `politica_invalida` em
toda chamada, medido) —, com duas vantagens e um custo honesto:

- vantagem: trocar a política não exige reiniciar o Odoo, e um arquivo ilegível **não derruba a
  instalação** do módulo (o pacote `api` não é importado no `__init__` do módulo);
- custo: leitura de um JSON de ~3 KB por requisição (irrelevante para o volume do n8n; cache com
  checagem de mtime é candidato natural do card de observabilidade `TRE-W3-E05-T01`).

---

## §2 ACCEPTANCE — o que tem de valer (AC1..AC8)

| AC | O que afirma | Onde é medido |
|---|---|---|
| AC1 | Superfície fechada e declarada: só as operações da política; não declarada → 404; verbo ≠ POST → 405; **uma** rota no controlador; política inválida → nada é servido | `--apenas-http` (curl) + passo 4 (grep de contrato) + suite do Odoo |
| AC2 | Nada de escrita direta no banco interno: todo acesso por ORM; **0** SQL nos arquivos da API | passo 4 (`grep -rnE 'cr.execute|sql.SQL'`) |
| AC3 | Filtro por campo declarado: campo não declarado no payload → 422 (nunca ignorado); limite acima do teto → 422; ordenação só nos declarados | suite + curl + suite pura |
| AC4 | Credencial fora do repo e fora do log: `auth='bearer'` com chave de API do usuário de integração (as ACLs do E07 valem para ele); sem header → 401; chave inválida → 401; token nunca em resposta/log/artefato | suite (401) + `secret_scan.sh` + grep do token no log do servidor |
| AC5 | Guarda de ambiente (ADR-005): só atende no ambiente declarado **e** permitido; `homologacao`/`producao` exigem aprovação humana registrada; a política desta versão permite `dev` e só | suite pura (10 casos) + suite do Odoo + curl |
| AC6 | Contrato de integração: escrita exige `idempotency_key` (ausente/vazia/fora do formato → 422); `correlation_id` ecoado; `dry_run` valida e descreve **sem** tocar o dado — na escrita (`teste_27`, contagem idêntica antes/depois) e na leitura (`teste_31`, devolve a consulta e não os registros); onde a política declara `aceita_dry_run: false`, o pedido é recusado (`teste_32`, 422 `dry_run_nao_suportado`) | suite do Odoo (itens 23–32) + suite pura |
| AC7 | Rastreabilidade: **uma** linha `TF_API_AUDIT {...}` por chamada, inclusive nas recusas, com correlação/operação/ambiente/versão/resultado/código/ids/latência — **sem payload e sem token** | curl + `docker logs` do servidor descartável (contagem de linhas = chamadas autenticadas) |
| AC8 | Ambiente intocado: dupla descartável própria, banco descartável; dev/homolog/prod medidos antes e depois | guardas + limpeza do verificador |

**Lacuna declarada (AC6):** o motor de deduplicação por `idempotency_key` é do card
`TRE-W3-E02-T02`; aqui a chave é **exigida, validada e registrada**, e nenhuma operação de escrita
entra na política real antes do E02-T02.

## §3 TEST — como cada AC é medido (na VPS, por execução real)

```bash
# na VPS, a partir de ARQUIVO (nunca por stdin), no checkout publicado
cd /opt/tre/evid-t_e0489efc-r1/repo
TRE_LOG_DIR=/opt/tre/evid-t_e0489efc-r1/logs-aceite bash scripts/odoo/verificar-api-controlada.sh
bash scripts/odoo/verificar-api-controlada.sh --prova-de-dente
```

| Modo | O que roda |
|---|---|
| (padrão) | passo 0 suite pura do motor → guardas do ambiente → dupla descartável própria → passo 1 instalação em banco limpo → passo 2 suite do Odoo (`--test-enable`, piso de testes medido) → passo 3 **HTTP real por `curl` de fora do processo** → passo 3b auditoria no log do servidor → passo 4 contrato (greps) → limpeza + dev/homolog/prod conferidos |
| `--apenas-motor` | só o passo 0 (sem Docker): a decisão inteira em segundos |
| `--apenas-suites` | passos 1 e 2 |
| `--apenas-http` | passos 1 e 3 (usado pelos dentes) |
| `--prova-de-dente` | 3 mutações em **cópias** do módulo; cada uma **tem** de reprovar o aceite, e o artefato real tem de sair intacto (guarda externa por sha256) |

Variáveis: `TRE_MODULO_DIR` (padrão: o módulo do próprio checkout), `TRE_BANCO` (padrão
`tre_e01_t01_api`, exige o formato `^tre_[a-z0-9_]+$` e recusa nome do ambiente),
`TRE_IMAGEM`/`TRE_IMAGEM_PG`, `TRE_LOG_DIR`, `TRE_PISO_DE_TESTES`, `TRE_MANTER_BANCO=1`.

**A chave de API da fase HTTP não passa por stdout nem por argumento**: `scripts/odoo/preparar_api_teste.py`
roda por `odoo shell` com o diretório descartável montado em `/preparo` (modo 700) e grava a chave em
`/preparo/chave.txt` (**600**); o `curl` lê os cabeçalhos de um **arquivo de configuração 600**
(`curl --config`), então o token também não aparece na linha de comando (visível em `ps`).

**Isolamento (mesma lição do E03):** o Odoo do dev abre sessão em qualquer banco novo da instância
`pg-odoo-dev`, então o verificador sobe a **sua** dupla descartável (`postgres:16` + `odoo:19.0`,
as mesmas imagens do par de dev) em rede própria com senha gerada por `openssl rand` na hora, e
não usa `pg-odoo-dev`, `odoo_dev` nem `/opt/tre/repo`.

## §4 ROLLBACK

- **Reverter o commit do card** (módulo, scripts, runbook): não há migration, DDL, dado migrado,
  tabela nova em `sales_intelligence` nem alteração no Odoo do dev — a API vive só no módulo, que
  **ainda não está instalado** em ambiente persistente (o `.gitkeep` segue sendo o que está
  publicado em `/opt/tre/repo`).
- **Onde o módulo já esteja instalado:** desinstalar pelo ORM (`python3
  scripts/odoo/desinstalar_modulo.py` com `odoo shell`) remove o controlador e a política; nada
  mais é criado por este card.
- **Desligar sem reverter:** a API fica **inerte sozinha** — sem `tf.api.ambiente` declarado ela
  recusa tudo com 503 (`ambiente_nao_declarado`), que é o fail-closed do AC5.
- Sem impacto em produção, homologação, n8n, backup e Sales Intelligence.

## §5 RISK

- **Médio.** É a primeira superfície HTTP de escrita do projeto dentro do Odoo e o caminho por onde
  o n8n vai escrever no CRM. Mitigações no próprio desenho: nasce em dev e só em dev (AC5/ADR-005);
  loopback apenas (o Odoo do dev não publica porta — `deploy/compose/dev/odoo.yml`); superfície
  fechada por declaração (AC1/AC3); credencial de usuário de integração com as ACLs do E07 (AC4);
  sem SQL (AC2); trilha por chamada (AC7).
- **Riscos residuais declarados:** (a) a chamada autenticada passa por cima do CSRF do Odoo por
  desenho — o que protege é a **chave**, não a sessão; (b) o teto/limites da política são a única
  barreira contra varredura massiva de leitura — **não há rate limit** aqui (limites de sync e
  observabilidade são do `TRE-W3-E05-T01`); (c) confundir esta API com o XML-RPC padrão do Odoo: o
  caminho homologado é o `/tf/api/v1/*`; (d) a política é lida por chamada (§1) — cache é melhoria
  do E05, não requisito deste card.

---

## §6 O contrato HTTP, linha a linha

```
POST /tf/api/v1/<operacao>
Authorization: Bearer <chave de API do Odoo>      (auth='bearer' do Odoo 19, tipo de chave 'rpc')
Content-Type: application/json
```

Corpo (todas as chaves são opcionais, menos as exigidas pela operação):

| Chave | O que é |
|---|---|
| `parametros` | `modelo`, `filtro` (`[[campo, operador, valor]]`), `campos`, `ordem` (`"campo asc\|desc"`), `limite` (leitura) / `valores`, `identificador` (escrita) |
| `idempotency_key` | **Obrigatória** na escrita (doc 06 §7 / doc 13 §9); formato conferido pelo `padroes.idempotency_key` da política |
| `correlation_id` | Correlação do chamador; se ausente, a API gera um UUID e devolve na resposta |
| `dry_run` | `true` valida e descreve o efeito **sem tocar o dado** (só onde a política declara `aceita_dry_run`) |

Resposta de sucesso (`200`):

```json
{"ok": true, "operacao": "crm_registros_ler", "acao": "ler", "ambiente": "dev",
 "politica_versao": "1.0.0", "correlation_id": "...", "idempotency_key": null,
 "dry_run": false, "dados": {"registros": [...], "total": 3}}
```

Resposta de recusa (`4xx`/`5xx`):

```json
{"ok": false, "codigo": "campo_nao_declarado", "mensagem": "...", "operacao": "...",
 "ambiente": "dev", "correlation_id": "..."}
```

Taxonomia de erro (a única fonte é `CODIGOS_DE_ERRO`, no motor — código novo sem entrada é erro de
programação, e o teste puro cobre):

| Código | HTTP | Quando |
|---|---|---|
| `payload_invalido` | 400 | corpo não é objeto JSON, chave desconhecida, `valores` vazio, tipo errado, `dry_run` não booleano |
| `acesso_negado` | 403 | a ACL do dono da chave recusou a operação no ORM |
| `operacao_nao_declarada` | 404 | a operação não está na política |
| `metodo_nao_permitido` | 405 | verbo ≠ POST |
| `modelo_nao_declarado`, `campo_nao_declarado`, `campo_obrigatorio_ausente`, `valor_invalido`, `limite_excedido`, `idempotency_key_ausente`, `idempotency_key_invalida`, `dry_run_nao_suportado` | 422 | pedido dentro do formato, fora da declaração/teto/contrato |
| `valor_ambiguo` | 409 | upsert/atualizar encontrou mais de um registro com a identidade — a API **não escolhe sozinha** |
| `ambiente_nao_declarado`, `ambiente_nao_permitido`, `aprovacao_ausente` | 503 | guarda do ADR-005 |
| `politica_invalida`, `erro_interno` | 500 | política ilegível/inválida; falha interna (com `TF_API_FALHA_INTERNA` no log) |

**Guardas do Bearer (401)** vêm do próprio Odoo (`ir_http._auth_method_bearer`), **antes** do
controlador: sem `Authorization` ou com chave inválida a resposta é 401 e **não** há linha de
auditoria (nada do negócio foi tocado). Por isso a contagem de auditoria do aceite compara com as
chamadas **autenticadas**.

## §7 A guarda de ambiente (ADR-005) — formato da aprovação

`ir.config_parameter`:

| Parâmetro | Para que serve |
|---|---|
| `tf.api.ambiente` | ambiente declarado da instalação (`dev`/`homologacao`/`producao`) |
| `tf.api.aprovacao` | aprovação humana registrada, no formato `card=...,aprovador=...,validade=AAAA-MM-DD` |
| `tf.api.politica` | caminho da política (vazio = a do módulo) — usado para rollback de política e pelos testes |

Ordem das recusas: ambiente ausente/desconhecido → 503 `ambiente_nao_declarado`; ambiente fora de
`ambientes_permitidos` → 503 `ambiente_nao_permitido`; `homologacao`/`producao` sem aprovação válida
(sem os três campos, data não-ISO ou validade vencida) → 503 `aprovacao_ausente`. A guarda é
**portão, não parede**: com aprovação válida a chamada atende — e é isso que os itens de política de
teste provam.

## §8 Armadilhas medidas (Odoo 19) — o que custou rodada

1. **`res.users.groups_id` não existe mais**: em Odoo 19 o campo é **`group_ids`**. O `ValueError:
   Invalid field 'groups_id' in 'res.users'` só aparece na execução (a suíte usa `new_test_user`,
   que trata o nome internamente — o script de preparo não tem essa proteção).
2. **A rota de leitura não é "sem sujeito":** a leitura roda no `request.env` do **dono da chave**,
   então a ACL decide. No CRM, o vendedor só enxerga o **lead dele**: um lead sem `user_id` é
   invisível para o usuário de integração e a leitura devolve `total: 0` com `200`. Ou seja: "a
   ACL do usuário da chave vale" é medível (e foi medido) — leitura por ali não é `sudo`.
3. **`valores: {}` é payload vazio (400), não campo obrigatório ausente (422)**: campo obrigatório
   ausente pressupõe um `valores` **não vazio** sem o campo exigido.
4. **`auth='bearer'` + `type='http'` + `csrf=False` + `save_session=False`**: quem autentica é o
   Odoo, antes do controlador; a sessão não é usada (a chave é a credencial).
5. **A ISO do teste HTTP compartilha a transação do teste** (`HttpCase` + `url_open`): dá para gerar
   `res.users.apikeys` e gravar `ir.config_parameter` **sem commitar** e a requisição vê. Fora do
   teste, `odoo shell` **precisa** de `env.cr.commit()`.
6. **Chave de API do Odoo 19 exige data de validade**: `res.users.apikeys._generate` levanta
   `ValidationError("The API key must have an expiration date")` quando a data é `None` (só passa
   em ambiente `sudo` de usuário de sistema), e o teto é o `api_key_duration` do grupo do usuário
   (1 dia por padrão). Teste e preparo usam **12 horas**. Os dois nomes mudaram em relação ao que
   se lembra do Odoo: `res.users.group_ids` (era `groups_id`) e `res.users.apikeys` (era
   `res.users.apikey`).
7. **O container `odoo:19.0` roda como uid 100/gid 101**: diretório montado para o preparo tem de
   ser gravável por ele (`chown 100:101`) — o `open()` do preparador falha com `Permission denied`
   sem isso, mesmo com o processo chamador sendo root no host.
8. **`docker port <container> 8069/tcp` com `-p 127.0.0.1::8069`**: porta alta aleatória — evita
   colisão com o `proxy-dev`/outros agentes; e `dbfilter` fixo no `odoo.conf` descartável para o
   `db_monodb` resolver o banco certo.
9. **O token não pode ir em `-H` na linha de comando** (aparece em `ps`): `curl --config` lê os
   cabeçalhos de arquivo 600.
10. **O `secret_scan.sh` do projeto reprova `password = <valor>`**: no gerador do `odoo.conf` as
   chaves vão por variável (`CHAVE_SENHA_BANCO='db_password'` + `printf '%s = %s'`), como já fazia o
   provisionamento do dev.

## §9 Como declarar uma operação nova (receita para os cards E01-T02..T05)

1. Acrescente a operação em `api/politica_api.json` (`nome`, `tipo`, `modelos` com
   `campos`/`campos_de_filtro`/`campos_de_ordem`, `acao`, `campo_de_identidade`,
   `campos_obrigatorios`, `requer_idempotency_key`, `aceita_dry_run`, `limite_de_registros`,
   `operadores_de_dominio`) e **suba a `versao`** da política.
2. Rode `python3 scripts/odoo/testar_motor_api.py` — a validação da política é fail-closed: campo
   de filtro fora de `campos`, `limite_de_registros` ausente, escrita sem `requer_idempotency_key`
   etc. são reprovados **antes** de qualquer deploy.
3. **Nada de código novo é necessário** no controlador: operação declarada já é servida (o plano
   de leitura/escrita é montado pelo motor). Só se a operação tiver uma **fonte** nova (como
   `capacidades`) é preciso acrescentar a fonte em `FONTES_CONHECIDAS` e o galho no motor.
4. Acrescente o item ao aceite: `crm_registros_ler` é o modelo de como declarar leitura; a política
   de teste (`tests/politicas/politica_de_teste.json`) é o modelo de como exercitar escrita sem
   colocar operação de negócio antes do E02-T02.

## §10 Lacunas e pendências herdadas

- **Dedup por `idempotency_key`**: `TRE-W3-E02-T02` (aqui a chave é exigida, validada e registrada).
- **Observabilidade durável / rate limit / cache de política**: `TRE-W3-E05-T01`.
- **Publicação do módulo na cópia operacional** (`/opt/tre/repo`): pendência herdada do E03-T01 —
  hoje está publicado só o `.gitkeep` de `odoo/addons/`.
- **Ratificação da versão 19.0 e homologação** (estágio 7): com o Anderson; a revisão independente
  deste card é do estágio 6 (perfil `tester`).
