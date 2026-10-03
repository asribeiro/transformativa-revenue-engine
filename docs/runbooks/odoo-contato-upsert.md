# Upsert de contato (`res.partner` pessoa) pela API controlada — `POST /tf/api/v1/contato_upsert`

**Card:** `TRE-W3-E01-T03` (`t_e6e3b0b3`) · **Leva:** W3 / E01 · **Branch:** `feature/TRE-W3-E01-T03`
**Base:** commit aprovado do `TRE-W3-E01-T02` (`8439f7b`) — o vocabulário da escrita
(`campos_de_identidade`, `valores_fixos`, `identificador_ausente`, `campo_fixo_divergente`,
`valor_ambiguo`) já estava entregue ali; este card **não** cria segundo mecanismo para o mesmo conceito.
**Depende de:** `TRE-W3-E01-T01` (a porta única) e `TRE-W3-E01-T02` (a primeira escrita de negócio).
**Libera:** `TRE-W3-E01-T05` (activity create), que ficou atrás deste por colisão de arquivos.

Este runbook é o par do `docs/runbooks/odoo-empresa-upsert.md`: o **desenho** (§1), o que tem de valer
(§2), como foi medido (§3), rollback (§4), risco (§5), o contrato da operação (§6), os códigos de erro
(§7), as armadilhas **medidas nesta rodada** (§8), a receita para a próxima operação (§9), as lacunas
(§10) e o aceite medido na VPS (§11).

---

## §1 Desenho — a identidade é o e-mail (a decisão que o card tinha de tomar antes de escrever código)

O contato comercial é dono Odoo (contrato §2 e `canonical_ids.odoo_map`: `contacts.id` ↔
`res.partner` **via `odoo_partner_id`, coluna do lado PostgreSQL**). A operação espelha o evento
`DECISION_MAKER_FOUND` (contrato §6) no espelho operacional do CRM, pelo caminho do consumidor
(n8n, `TRE-W3-E02-T01`).

### Campos declarados (o que a operação aceita — e mais nada)

| Campo | Origem | Exigência |
|---|---|---|
| `name` | `contacts.full_name` | **obrigatório** (`campo_obrigatorio_ausente` se faltar) |
| `email` | `contacts.email` | **identidade declarada** (`campos_de_identidade: ["email"]`) |
| `is_company` | — | **valor fixo** declarado (`false`): a operação é de **pessoa** |
| `function` | `contacts.job_title` | opcional |
| `phone` | `contacts.phone` | opcional |

Campo fora dessa lista ⇒ **422 `campo_nao_declarado`** — inclusive `tf_cnpj` (campo de empresa, que
existe no modelo mas **não** é desta operação) e os de compliance (`do_not_contact`,
`opt_out_email`, `opt_out_whatsapp`, `preferred_channel`, `legal_basis`).

### A decisão, em três perguntas

1. **Qual é a identidade?** O mapa canônico da V1 **não** dá UUID de contato do lado Odoo — o UUID de
   `contacts.id` chega ao parceiro por `odoo_partner_id`, que é coluna do PostgreSQL. Logo a operação
   **não tem UUID para casar**; ela casa pelo **identificador natural que existe nos dois lados e é
   indexado pelo contrato §4**: `email` (`contacts(email)` de um lado, `res.partner.email` do outro).
   Criar um campo espelho `tf_contact_id` em `res.partner` seria **acrescentar linha ao mapa canônico
   do contrato (§3)** — ato de contrato (governança §10), com aprovação registrada; **não** é decisão
   deste card.
2. **O que a operação decide sozinha?** `is_company: false` — em `valores_fixos`, sem código. É a
   mesma forma do `empresa_upsert` (`is_company: true`) e é exatamente o que o runbook do E01-T02 §9
   pediu a este card ("o valor fixo é a forma de dizer isso na política").
3. **O que fica FORA (e por quê)?** Os campos de opt-out/`legal_basis`/`preferred_channel` são do
   **PostgreSQL** (`contacts`, contrato §9) e **não** são espelhados — a lacuna é declarada, não
   silenciosa: enviá-los é recusa nomeada (AC9). Pelo mesmo motivo
   `first_name`/`last_name`/`department`/`seniority`/`decision_role`/`whatsapp` e os scores de contato
   (`influence`/`contactability`/`relationship`) **não** entram: o contrato V1 não define coluna de
   espelho para eles no Odoo, e inventá-la seria mudança de contrato.

### Split motor × controlador (mantido do E01-T01)

Nada de código novo no controlador: a operação **declarada** já é servida (o motor monta o plano e o
controlador executa pelo ORM, com as ACLs do dono da chave). O único código que este card mudou está
no **motor**, e é um portão (§7, "o parâmetro que era aceito e descartado").

### O parâmetro que era aceito e descartado (defeito fechado neste card)

O motor recusava `parametros.identificador` quando a operação identifica por **lista ordenada**
(`campos_de_identidade`) **olhando o tamanho da lista resultante** (`len(ordem) != 1`). Para uma lista
de **um** elemento — exatamente o caso desta operação (`["email"]`) — o parâmetro passava pela
validação e era **descartado em silêncio**: o chamador dizia "identifique por este e-mail" e a API
identificava por outro caminho (ou por nada), sem dizer nada. Medido no aceite: HTTP **200** com
`identificador` ignorado, antes da correção.

A correção é pequena e na direção da casa ("o que o chamador disse nunca é ignorado em silêncio" —
mesma razão de `campo_fixo_divergente`): quem decide se `identificador` existe é a **forma da
declaração**, não o tamanho da lista.

```python
# motor.py — _plano_de_escrita()
por_lista = declaracao.get("campos_de_identidade") is not None
if identificador is not None and (por_lista or len(ordem) != 1):
    raise ErroApi("payload_invalido", ...)   # HTTP 400, com a lista declarada na mensagem
```

Efeito medido: `200` (descartado) → **`400 payload_invalido`** nomeado, com dois itens de teste
(o caso novo na suíte pura do motor e o caso HTTP no aceite). Não há operação declarada por lista de
um elemento antes desta, então nenhum comportamento existente mudou — a suíte anterior segue verde.

### Fronteira do card

- **Não publica** nada em ambiente persistente (`/opt/tre/repo` continua com o `.gitkeep`).
- **Não homologa**: revisão independente é do estágio 6 (perfil `tester`); homologação é do Anderson.
- **Não cria** DDL, migration, tabela ou coluna — escreve por ORM no espelho que já existia.

---

## §2 ACCEPTANCE — o que tem de valer (AC1..AC9)

- **AC1** Operação declarada e versionada na política real (`contato_upsert`), servida pela **mesma**
  porta única — nenhuma rota nova no controlador (**1 rota**).
- **AC2** Identidade declarada, não literal: payload sem o valor de identidade ⇒ **422** nomeado;
  valor de identidade nunca é inferido de outro campo; parâmetro de identidade **nunca** é descartado
  em silêncio.
- **AC3** Upsert cria **uma** vez e atualiza depois: N chamadas com a mesma identidade ⇒ **1**
  registro; contagem conferida por consulta ao banco; `acao_efetiva` = `criar`/`atualizar`.
- **AC4** Ambiguidade é reportada, nunca resolvida: identidade casando **mais de um** registro ⇒
  **409 `valor_ambiguo`** e **nada** escrito nem alterado.
- **AC5** Semântica de contato: o parceiro nasce/atualiza como **pessoa**; payload tentando tornar
  empresa é recusado (mesmo portão de valor fixo do T02, sem mecanismo paralelo).
- **AC6** Contrato de integração: `idempotency_key` exigida (ausente/fora do formato ⇒ 422);
  `dry_run` valida e **descreve sem tocar o dado**; `correlation_id` ecoado; campo fora da declaração
  ⇒ 422 (nunca ignorado).
- **AC7** Guarda de ambiente (ADR-005) + rastro: só o ambiente permitido pela política; `homologacao`
  sem aprovação registrada ⇒ **503**; **uma** linha `TF_API_AUDIT` por chamada, inclusive nas
  recusas, **sem payload e sem token** (o e-mail do contato é dado pessoal: não entra na trilha).
- **AC8** Ambiente intocado: dupla descartável própria (`postgres:16` + `odoo:19.0`), dev/homolog/prod
  medidos antes e depois; **nenhum** DDL, migration ou escrita em `sales_intelligence`.
- **AC9** Escopo declarado do compliance: os campos de opt-out/`legal_basis`/`preferred_channel`
  **não** são criados no Odoo; a operação não os aceita (campo fora da declaração ⇒ 422) e a lacuna
  fica escrita (§1, §10).

---

## §3 TEST — como cada AC é medido (na VPS, por execução real)

Script **próprio** deste card (`scripts/odoo/verificar-contato-upsert.sh`, o mesmo padrão do
E01-T01/E01-T02 e **sem** tocar no aceite do E01-T01, que é o arquivo compartilhado da leva):

```bash
# na VPS, a partir de ARQUIVO, no checkout do card (root: o harness descarta os diretorios
# do proprio aceite para o uid do container Odoo; a dupla descartavel e' criada e removida na rodada)
cd /opt/tre/rev-t_e6e3b0b3-r3
sudo env TRE_LOG_DIR=/opt/tre/evid-t_e6e3b0b3-r3 bash scripts/odoo/verificar-contato-upsert.sh
sudo env TRE_LOG_DIR=/opt/tre/evid-t_e6e3b0b3-dente bash scripts/odoo/verificar-contato-upsert.sh --prova-de-dente
```

Ordem interna: suíte pura do motor (sem Odoo) → guardas de ambiente → dupla descartável própria
(`e01t03-pg-*`, `e01t03-api-*`, rede `e01t03-net-*`, chave de API gerada na hora em arquivo `600` e
removida no fim) → instalação em banco limpo → suíte do Odoo `--test-enable` com **piso de testes**
(o piso é o número de testes do módulo: 120 = 101 do E01-T02 + 19 deste card) → **HTTP real por
`curl` de fora do processo** → auditoria lida **do log do servidor** → contrato (greps: 1 rota,
0 SQL, motor sem `import odoo`) → limpeza + dev/homolog/prod conferidos.

### A prova de dente (e o que a torna não-decorativa)

Cada mutação roda o aceite `--apenas-http` numa **cópia** mutada do módulo e **tem** de reprovar o
item que ela quebra; o harness só conta como dente o sub-run que passou pelas guardas de ambiente e
chegou à fase HTTP (a lição do defeito `t_fa9db205` do E01-T01).

| Dente | Mutação | Item que tem de reprovar |
|---|---|---|
| 1 | política **sem** `contato_upsert` | "`contato_upsert` declarada como escrita com chave" (e o aceite cai junto) |
| 2 | controlador **sem** o portão de ambiguidade | "recusa nomeia `valor_ambiguo`" |
| 3 | motor **sem** aplicar `valores_fixos` na atualização | "o valor fixo declarado é aplicado na ATUALIZAÇÃO" |

Dois **controles** provam que a prova não é decorativa: um sub-run com ambiente quebrado e uma
mutação **inócua** têm de ser reportados como *dente inconclusivo* / *mutação sem dente* — se o
harness os contasse como prova, a prova inteira seria decorativa. O autoteste do harness ainda exige
que **cada marcador de dente seja texto de item do próprio verificador** (marcador digitado errado
não pode viver dizendo "mutação sem dente").

---

## §4 ROLLBACK

Reverter o commit deste card: **não há** DDL, migration, dado migrado, tabela ou coluna nova.
Onde o módulo estiver instalado:

1. **Rollback por política** (sem reinstalar nada): retirar a operação de `api/politica_api.json`
   faz a rota responder `404 operacao_nao_declarada`; o resto do módulo segue igual.
2. **Desinstalar o módulo** (`scripts/odoo/desinstalar_modulo.py`) reverte tudo o que o módulo criou.

O módulo **não** está publicado em ambiente persistente: `/opt/tre/repo` segue com o `.gitkeep`.
Impacto zero em homologação, produção, n8n e `sales_intelligence`.

---

## §5 RISK

**Médio.** É a **primeira escrita de dado pessoal** (contato) no CRM por consumidor externo — e dado
pessoal é a superfície mais sensível do contrato (§9). Mitigações **medidas** no desenho:

- nasce em dev e só em dev (ADR-005; `homologacao` sem aprovação ⇒ 503, medido);
- superfície fechada por declaração, campo a campo (`422 campo_nao_declarado` medido);
- identidade declarada e ausência de identidade é recusa nomeada (medido);
- ambiguidade **fail-closed**: `409` **sem** escrever nem alterar (medido por contagem antes/depois);
- `dry_run` que descreve e **não** toca o dado (medido);
- `idempotency_key` exigida e validada;
- trilha por chamada **sem payload**: o e-mail do contato **não** vai para o log (medido por grep no
  log de auditoria).

**Riscos residuais declarados (honestos, não resolvidos aqui):**

- **(a)** Um parceiro-**empresa** que já exista com o mesmo e-mail é **reescrito como pessoa** na
  atualização, porque `is_company` é valor fixo e o valor fixo é aplicado também no update. É
  consequência direta do desenho do E01-T02 (e é medido, de propósito, no dente 3). Restringir a
  identidade a pessoas exigiria declarar **escopo de identidade** — o contrato V1 não define isso;
  é decisão de contrato, registrada em §10.
- **(b)** Contato **sem e-mail** não entra por esta operação (recusa nomeada, não identidade
  inventada).
- **(c)** Dedup por `idempotency_key` (retry/replay) é do **`TRE-W3-E02-T02`** — até lá quem não
  duplica é a identidade.
- **(d)** Merge/reconciliação de contatos duplicados é do **`TRE-W3-E04-T01`**.
- **(e)** Não há rate limit: é do **`TRE-W3-E05-T01`**.
- **(f)** O e-mail é gravado como recebido (sem normalização/caixa/trim): a V1 não define
  normalização de identidade de contato. Um e-mail com espaço à esquerda cria registro distinto e
  não casa o anterior — mesma lacuna herdada do E01-T02 para os identificadores fortes.

---

## §6 O contrato da operação

Declaração na política real (`api/politica_api.json`, versão **1.1.0 → 1.2.0**):

```json
{
  "nome": "contato_upsert",
  "tipo": "escrita",
  "modelos": {
    "res.partner": {
      "campos": ["name", "email", "is_company", "function", "phone"],
      "campos_obrigatorios": ["name"],
      "campos_de_identidade": ["email"],
      "valores_fixos": {"is_company": false},
      "acao": "upsert"
    }
  },
  "requer_idempotency_key": true,
  "aceita_dry_run": true
}
```

Chamada (criar):

```json
POST /tf/api/v1/contato_upsert
Authorization: Bearer <chave de API do Odoo>
{
  "idempotency_key": "tre-e01-t03-http-0001",
  "correlation_id": "tre-e01-t03-http-1",
  "parametros": {"valores": {"name": "Contato HTTP Um", "email": "contato-http@upsert-t03.example",
                             "function": "Gerente de Compras", "phone": "+55 11 90000-0100"}}
}
```

Resposta (`200`): `ok`, `codigo: "ok"`, `operacao`, `ambiente`, `politica_versao`, `correlation_id`
(ecoado), `idempotency_key` e `dados: {acao_efetiva: "criar"|"atualizar", ids: [...],
modelo: "res.partner", campos_escritos: [...], dry_run: bool}`.

---

## §7 Códigos de erro que este card usa (e o portão que ele acrescentou)

| Código | HTTP | Quando (medido no aceite) |
|---|---|---|
| `identificador_ausente` | 422 | payload sem `email` (a identidade tem de vir com valor) |
| `valor_ambiguo` | 409 | mais de um parceiro com o mesmo e-mail — **nada** é escrito nem alterado |
| `campo_fixo_divergente` | 422 | chamador tentando decidir `is_company` |
| `campo_nao_declarado` | 422 | campo de empresa (`tf_cnpj`) ou de compliance (`do_not_contact`) |
| `campo_obrigatorio_ausente` | 422 | payload sem `name` |
| `idempotency_key_ausente` / `idempotency_key_invalida` | 422 | chave ausente / fora do formato |
| `ambiente_nao_permitido` | 503 | ambiente do banco fora da política (ADR-005) |
| **`payload_invalido`** | **400** | **`identificador` escalar em operação declarada por lista** — portão acrescentado neste card (§1) |

Todos os códigos são os do E01-T01/T02: este card **não** inventou código novo; o que ele fez foi
fechar a única porta em que um parâmetro declarado era descartado sem resposta nomeada.

---

## §8 Armadilhas medidas nesta rodada

1. **`insert ... returning id` no `psql` não devolve só o id.** O `psql` imprime a **etiqueta do
   comando** (`INSERT 0 1`) em STDOUT, e o `-tA` **não** a suprime (ao contrário do que o `-t`
   sugere para SELECT). Medido: o id capturado veio `"63\nINSERT01"` — e as **três** medições que
   dependiam dele (`is_company` do fixture, o casamento do update e o `id in (...)` da ambiguidade)
   mediram outra coisa, reprovando itens que a API tinha feito certo. Correção no harness: o id sai
   de um `SELECT` sobre um **CTE** (`with novo as (insert ... returning id) select id from novo`) —
   SELECT não imprime etiqueta — e o valor passou a ser validado como **numérico** antes de usar
   (fail-closed: id sujo agora vira falha **nomeada** do preparo, não cascata de itens reprovados).
2. **Texto copiado de saída de ferramenta pode vir mascarado.** O harness novo foi escrito a partir
   da leitura do harness do E01-T02, e a linha do cabeçalho de autorização voltou da leitura com o
   valor **mascarado** (`Bearer ***` onde o arquivo real tem o especificador de formatação). Copiada
   assim, a chave de API **nunca** era enviada: as 18 chamadas autenticadas viraram `401 Invalid
   apikey` (47 itens reprovados na rodada 1). Verificado por `od -c` (bytes, não texto renderizado) e
   comparado com o arquivo original (que traz o `%s`): a regra é **nunca** copiar linha com forma de
   credencial — escrever a linha e conferir os bytes. O `curl` também não pode receber o valor em
   argumento (`ps`): a chave é lida de arquivo (`--config`, `600`).
3. **O fixture por SQL cru precisa de `active` explícito** (herdada do E01-T02, válida aqui também):
   o default `True` de `active` é do **ORM**, não da coluna; sem `active = true` a linha existe para
   o SQL e é **invisível** para o `search` do Odoo — a API não casaria nada e o fixture mediria outra
   coisa. Por isso o aceite grava `active` explícito **e** lê o fixture **de volta** pelo caminho
   declarado (`crm_registros_ler`) antes de usar.
4. **O e-mail é a identidade "natural" — e isso é uma escolha com consequência.** `res.partner.email`
   não é `unique` no Odoo: dois parceiros podem ter o mesmo e-mail, e é exatamente por isso que o
   `409 valor_ambiguo` do motor é o portão certo (recusar, não escolher). O fixture do caso ambíguo
   nasce por SQL (a API não cria o segundo).
5. **Compliance não se espelha por engano.** `do_not_contact` **não existe** como campo em
   `res.partner` (contrato §9 o põe no PostgreSQL): o teste de AC9 mede o **modelo** (o campo não
   existe) e a **resposta** (enviá-lo é `422 campo_nao_declarado`) — a lacuna é declarada, não
   silenciosa.
6. **A trilha de auditoria é o log do servidor, não a leitura da tabela.** O aceite conta as linhas
   `TF_API_AUDIT` no log (`15 linhas para 15 chamadas autenticadas`) e prova por `grep` que **nenhum**
   e-mail do payload entrou na trilha — dado pessoal fora do log.

---

## §9 Como declarar a próxima operação de escrita (receita para `E01-T04..T05`)

A receita do `odoo-empresa-upsert.md` §9 continua valendo, com o que este card acrescentou:

1. Acrescente a operação em `api/politica_api.json` (`nome`, `tipo: "escrita"`, `modelos` com
   `campos`, `campos_obrigatorios`, `acao`, `requer_idempotency_key: true`, `aceita_dry_run`) e
   **suba a `versao`**. Identidade em `campos_de_identidade` (lista, ordem = prioridade de busca);
   o valor que a operação decide sozinha, em `valores_fixos`.
2. Rode `python3 scripts/odoo/testar_motor_api.py` (fail-closed antes de qualquer deploy). Se a
   operação declarar identidade por **lista**, o parâmetro `identificador` **é recusado** — a forma é
   `valores` (§1, §7). Se a operação precisar de identidade **literal** de um campo, use
   `campo_de_identidade` (forma singular do E01-T01).
3. **Nada de código novo no controlador.** Só uma **ação** nova (além de `ler`/`criar`/`atualizar`/
   `upsert`) ou uma **fonte** nova exigem galho no motor — e a suíte pura cresce junto.
4. No aceite da operação nova, **não copie literais que expiram** (versão da política, lista de
   operações, quantidade de testes): leia o **próprio artefato** (o piso de testes do módulo é o
   número real de testes; a versão sai da política em vigor).
5. **Todo id vindo de `psql` de escrita passa por `SELECT` sobre CTE e é validado como numérico**
   (§8, armadilha 1). **Toda linha com forma de credencial é escrita e conferida em bytes** (§8,
   armadilha 2).
6. `E01-T04` (opportunity upsert) entra em `crm.lead` (identidade `tf_opportunity_id` → …) e
   `E01-T05` (activity create) em `mail.activity`: os dois editam **esta mesma política** e devem
   seguir a serialização registrada no board (T03 → T05), declarando a operação de forma **aditiva**.

---

## §10 Lacunas e pendências herdadas

- **Dedup por `idempotency_key`** (retry/replay): `TRE-W3-E02-T02`.
- **Consumidor real (n8n) chamando a operação**: `TRE-W3-E02-T01` — aqui o consumidor é medido por
  `curl` de fora do processo.
- **Merge/reconciliação de contatos duplicados**: `TRE-W3-E04-T01`.
- **Observabilidade durável / rate limit / cache de política**: `TRE-W3-E05-T01`.
- **Publicação do módulo na cópia operacional** (`/opt/tre/repo`): pendência herdada do E03-T01.
- **Escopo de identidade** (impedir que o upsert de pessoa case — e reescreva — um parceiro-empresa
  já existente com o mesmo e-mail): exigiria uma linha de declaração (**escopo de identidade**) que o
  contrato V1 **não** define. É decisão de contrato/governança §10, não deste card; hoje o
  comportamento é o do desenho do E01-T02 (valor fixo aplicado também no update), **medido** no
  dente 3 e no caso de atualização com `is_company` = `t`.
- **Normalização de identidade de contato** (trim/caixa do e-mail): a V1 não define; o valor é
  gravado como recebido (§5-f).
- **Campos de contato não espelhados** (`first_name`/`last_name`/`department`/`seniority`/
  `decision_role`/`whatsapp` e os scores): o contrato V1 não define coluna de espelho no Odoo;
  espelhá-los seria mudança de contrato.
- **Compliance/opt-out** (`do_not_contact`, `opt_out_*`, `preferred_channel`, `legal_basis`):
  fora de escopo por contrato §9 — vivem no PostgreSQL (§1, AC9).
- **Homologação** (estágio 7) é do Anderson; a revisão independente (estágio 6) é do perfil `tester`.
  Quem entrega **não** homologa.

---

## §11 Aceite medido (VPS do dev, 02/10/2026)

O artefato é o **commit desta branch** (`feature/TRE-W3-E01-T03`), cuja base é o commit aprovado do
E01-T02 (`8439f7b`). O que roda na VPS é uma cópia **byte a byte** dele: o worktree foi enviado por
`tar` para `/opt/tre/rev-t_e6e3b0b3-r1..r3` e os arquivos da entrega (política, motor,
`tests/__init__.py`, a suíte nova, o verificador próprio, o verificador estrutural, README, CHANGELOG
e este runbook) foram conferidos por `sha256` contra o worktree.

**Rodada 1 — o aceite não podia autenticar (e o defeito era do harness, não da API).**
```
RESULTADO: CONTATO_UPSERT_FALHOU (109 itens, 47 falha(s)) modulo=transformativa_sales_ai
           banco=tre_e01_t03_contato imagens=odoo:19.0+postgres:16
```
As 18 chamadas HTTP voltaram `401 Invalid apikey`: a linha do cabeçalho de autorização do harness
novo tinha sido copiada da leitura do harness antigo **mascarada** (§8, armadilha 2) — a chave
gerada na hora nunca era enviada. Corrigido por escrita da linha + conferência em **bytes**
(`od -c`). A rodada também mostrou o harness reprovando o **preparo** por falta de permissão de
`chown` do diretório descartável quando o script roda como usuário não privilegiado — o aceite desta
dupla roda como **root** (como no E01-T01/E01-T02), e o diretório descartável é do uid do container.

**Rodada 2 — a API passa, o preparo do fixture ainda mente.**
```
RESULTADO: CONTATO_UPSERT_FALHOU (109 itens, 6 falha(s))
```
6 itens reprovados, todos por **uma** causa medida: o id do fixture semeado por SQL vinha com a
etiqueta do `psql` colada (`"63\nINSERT01"`, §8 armadilha 1). Corrigido com o `SELECT` sobre CTE +
validação numérica do id.

**Rodada 3 — verde, o artefato final.**
```
RESULTADO: CONTATO_UPSERT_OK (110 itens, 0 falhas) modulo=transformativa_sales_ai
           banco=tre_e01_t03_contato imagens=odoo:19.0+postgres:16
```
Dentro dos 110 itens: suíte pura do motor **`MOTOR_API_OK (90 itens, 0 falhas)`** (os 75 do
E01-T02 + 15 deste card); instalação em banco limpo; suíte do Odoo
**`0 failed, 0 error(s) of 120 tests`** (piso 120 = 19 testes novos deste card + 101 dos cards
anteriores, **sem regressão**), com os 19 nomes dos testes novos conferidos no log do runner; **HTTP
real por `curl` de fora do processo**: `401` sem token e com token inválido; criar → atualizar no
**mesmo** registro (3 chamadas, **1** registro, `acao_efetiva` criar → atualizar) com o e-mail
conferido no banco; `409 valor_ambiguo` com contagem antes/depois e os dois registros do fixture
**lidos de volta pelo caminho declarado**; `422` de identidade ausente, valor fixo divergente,
campo de empresa, campo de compliance, chave ausente e chave fora do formato; **`400 payload_invalido`
para `identificador` escalar** (o portão novo, medido por HTTP); dry-run que descreve e **não**
escreve; `503` fora do ambiente (servidor novo em `homologacao`, medido com o banco movido pelo
ORM); auditoria **do log do servidor** (**15 linhas para 15 chamadas autenticadas**, sem token, sem
`Bearer`, **sem nenhum e-mail do payload**); greps de contrato (1 rota, `auth='bearer'`, só `POST`,
0 SQL na API, motor sem `import odoo`, política lida do próprio artefato).

**Rodada 4 — o commit congelado, remedido.** O aceite e a prova de dente rodaram de novo sobre o
**`git archive` do commit** desta branch (`88323c4`, extraído em `/opt/tre/rev-t_e6e3b0b3-r4`), com os
**11 arquivos da entrega conferidos por `sha256`** entre o worktree e a cópia da VPS — **11/11
iguais**; e os arquivos do módulo são **byte a byte** os mesmos da rodada que produziu os dentes
(mesmo `sha256` da política, do motor, da suíte e do verificador nas árvores `r3` e `r4`). Saída:
`CONTATO_UPSERT_OK (110 itens, 0 falhas)` e `CONTATO_UPSERT_DENTE_OK (3 provas + 2 controles do
próprio harness, 0 falhas)`. Registros: `aceite.out` em `/opt/tre/evid-t_e6e3b0b3-r4/` e `dente.out`
em `/opt/tre/evid-t_e6e3b0b3-r4-dente/`.

**Prova de dente (veredito).** As 3 provas reprovaram o item que cada mutação quebra — dente 1
(política sem a operação): "`contato_upsert` não declarada como escrita com chave" e o aceite cai
junto (`96 itens, 34 falhas`); dente 2 (controlador sem o portão de ambiguidade): "código de recusa
errado (ambiguidade)"; dente 3 (motor sem aplicar o valor fixo): "o valor fixo declarado **não** foi
aplicado na atualização (`is_company='t'`)". Os 2 controles do harness foram reportados como
*inconclusivo* (sub-run que reprova por ambiente **não** conta como dente — não é fail-open) e
*mutação sem dente*, e a **guarda externa** confirmou o artefato real intacto (`30 arquivos`, mesmo
`sha256` `97feb79b…` antes e depois das mutações).

**Registros brutos (na VPS, em `/opt/tre/evid-t_e6e3b0b3-r*/`):** `aceite-r1.out` (47 falhas, o
defeito do harness preservado), `aceite-r2.out` (6 falhas, a etiqueta do `psql`), `aceite.out` da
rodada **r3** (110 itens, 0 falhas) e `aceite.out` da rodada **r4** (a mesma saída, medida no
`git archive` do commit), com os logs por etapa (`1-instalacao.log`, `2-teste.log`, `3-preparo.log`,
`3-http.log`, `3d-http.log`, `4-contrato.log`); os dentes em `dente.out` das árvores `-dente` (r3) e
`-r4-dente` (r4), com `dente1/`, `dente2/`, `dente3/` e os controles em cada uma. Nenhum valor de
segredo em nenhum deles: a chave da API nasce **na VPS**, em arquivo `600` dentro do diretório
descartável do preparo, é lida pelo `curl` por arquivo de configuração (nunca em `ps`, argumento ou
log) e morre com o diretório.

**O que o aceite NÃO toca (medido):** nada em `/opt/tre/{homolog,prod}` (0 arquivo antes e depois),
o `odoo-dev`/`pg-odoo-dev` de pé (a dupla do aceite é própria, `e01t03-*`, criada e removida na
rodada) e **0** container ou rede residual ao fim.
