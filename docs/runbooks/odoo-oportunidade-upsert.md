# Operação de escrita de negócio `oportunidade_upsert` — espelho da oportunidade canônica

**Card:** `TRE-W3-E01-T04` (board `transformativa-revenue-engine`, cartão `t_8b2ed1b7`)
**Módulo:** `odoo/addons/transformativa_sales_ai` — rota `POST /tf/api/v1/oportunidade_upsert`
**Base:** `feature/TRE-W3-E01-T01` (a porta única, `afab91c`) — esta operação **não** abre rota nova
**Verificador:** `scripts/odoo/verificar-oportunidade-upsert.sh`
**Suíte:** `odoo/addons/transformativa_sales_ai/tests/test_oportunidade_upsert.py` (27 itens)

---

## §1 Desenho — a operação é uma DECLARAÇÃO, não código novo

O `TRE-W3-E01-T01` entregou a porta única governada por declaração: a política
(`api/politica_api.json`) diz o que pode ser servido, e o motor monta o plano de leitura ou de
escrita a partir dela. Declarar uma operação de negócio de **escrita** é, portanto, um ato de
política — e é exatamente isso que este card faz:

1. **A política sobe de versão** (`1.0.0` → `1.1.0`) e ganha o bloco de `oportunidade_upsert`:
   `tipo: escrita`, modelo `crm.lead`, `acao: upsert`, identidade `tf_opportunity_id`,
   `campos_obrigatorios: [name]`, `requer_idempotency_key: true`, `aceita_dry_run: true`.
   Versão nova, e não edição do bloco existente, porque `TRE-W3-E01-T02..T05` acrescentam as
   operações deles **na mesma política**: quem chegar depois soma, não reescreve.
2. **Nenhuma linha de controlador foi necessária** — a receita do §9 do runbook
   `odoo-api-controlada.md` se confirmou: operação declarada é servida (o controlador escreve
   exatamente `plano["valores"]`, que é o que a operação declara).
3. **Os testes do E01-T01 deixaram de fixar a lista de operações**: os itens que falavam da lista
   passaram a ler o **próprio artefato** (`api/politica_api.json`) — em vez de literal no teste.
   Sem isso, cada card da onda W3-E01 quebraria a suíte do card anterior (o que já tinha acontecido
   com a política `1.0.0` desta suíte). A âncora `ANCORA:ITEM_DATADO` marca cada ponto desses.

### A decisão de fronteira: o espelho da INTELIGÊNCIA, não o CRM

O contrato §2 divide o dono do dado: o PostgreSQL é dono de empresa, sinais, research e scores; o
**Odoo é dono de estágio, valor, won/lost, atividade, reunião e proposta**. Esta operação declara só
o que é do espelho:

| grupo | campos | quem manda |
| --- | --- | --- |
| apresentação | `name`, `type`, `partner_id` | espelho (apresentação do registro) |
| identidade | `tf_opportunity_id` | produtor do fato (UUID canônico, contrato §3) |
| inteligência | `tf_priority_score`, `tf_icp_score`, `tf_automation_fit_score`, `tf_buying_signal_score`, `tf_data_quality_score`, `tf_score_version`, `tf_next_best_action` | PostgreSQL |
| rastro | `tf_correlation_id`, `tf_idempotency_key`, `tf_last_sync_at`, `tf_last_event_type` | produtor do fato |

Campos de dono do Odoo **não** estão declarados: `stage_id` enviado dá recusa nomeada
(422 `campo_nao_declarado`), nunca "ignorado em silêncio". `tf_priority_tier` também não é
escrevível — é `compute` de `tf_priority_score` (`TRE-W2-E04-T02`) e acompanha o score sozinho.

**O que isso significa na prática:** o funil continua sendo do time comercial. A API move o
*conhecimento* sobre a oportunidade (scores, próxima melhor ação, rastro de sincronização), nunca a
posição dela no pipeline nem o valor negociado.

### De quem é o rastro

`tf_idempotency_key` é campo **declarado**, escrito com o valor que o produtor manda em `valores` —
não há escrita por conta própria a partir do campo homônimo do envelope. A chave do envelope é
registrada na **trilha de auditoria** (uma linha `TF_API_AUDIT` por chamada, `AC7`). O motivo é
deliberado: quem garante "não duplicar" aqui é a **identidade canônica** (o UUID); replay/retry e
deduplicação por chave são o `TRE-W3-E02-T02`. Inventar escrita invisível em campo de negócio
misturaria donos — e o contrato §2 existe justamente para não deixar isso ambíguo.

---

## §2 ACCEPTANCE — o que tem de valer (AC1..AC8)

| AC | critério | onde é medido |
| --- | --- | --- |
| AC1 | operação **declarada** na política em vigor: tipo escrita, `crm.lead`, identidade `tf_opportunity_id`, chave exigida; servida pela mesma porta única (1 rota) | suíte 02 + passo 3 da suíte pura + passo 3 do verificador |
| AC2 | **identidade canônica**: casa pelo UUID, nunca pelo nome; sem UUID → 422 nomeada; UUID fora do formato → 422 `valor_invalido` | suíte 05/06/12 + passo 3d do verificador |
| AC3 | **upsert**: N chamadas com o mesmo UUID → **um** registro (contagem medida no banco); `criar` na 1ª e `atualizar` depois; atualização **parcial** | suíte 08..11 + passo 3 do verificador (SQL) |
| AC4 | **fronteira de dono**: campo de dono do Odoo → 422 `campo_nao_declarado`; registro inteiro de dono **intocado** no upsert (antes/depois) | suíte 13..16 + passo 3 do verificador (SQL) |
| AC5 | **contrato de integração** (doc 06 §7): `idempotency_key` exigida e validada; `dry_run` descreve sem escrever; `correlation_id` ecoado; rastro declarado no espelho | suíte 03/04/18/19/20 + passo 3 do verificador |
| AC6 | **guarda de ambiente** (ADR-005) também na escrita: `dev` atende; `homologacao` fora da política → 503; `producao` sem aprovação → 503 e com aprovação → 200 | suíte 21..23 + passo 3d do verificador (curl, servidor NOVO) |
| AC7 | **trilha**: uma linha `TF_API_AUDIT` por chamada autenticada (inclusive recusas), com operação/ação/ids, **sem token e sem payload** | suíte 24 + passo 3c do verificador (log bruto) |
| AC8 | **vínculo e dono medidos**: `partner_id` liga o lead à empresa do espelho; o lead nasce sob o usuário de integração (atribuição comercial não é desta operação); ambiente de execução intocado | suíte 25/26 + limpeza do verificador |

---

## §3 TEST — como cada AC é medido (na VPS, por execução real)

```bash
# na VPS, a partir de ARQUIVO (nunca por stdin), no checkout publicado do commit
sudo -n env TRE_LOG_DIR=/tmp/verificacao-oportunidade \
  bash scripts/odoo/verificar-oportunidade-upsert.sh

# só a suíte pura do motor (sem Odoo)          --apenas-motor
# instalação + suíte do Odoo (--test-enable)   --apenas-suites
# instalação + servidor HTTP real + curl       --apenas-http
# 3 mutações; cada uma TEM de reprovar         --prova-de-dente
```

O verificador tem de rodar **como root na VPS** (a dupla descartável exige `chown` para o uid do
container `odoo:19.0`). Rodado como usuário comum, o `odoo.conf` fica ilegível para o container e a
instalação falha — está medido na rodada 1 deste card, no registro de execuções.

Cinco fases medidas por execução real (nada de inspeção de código como prova):

1. **passo 0** — suíte pura do motor (68 itens), incluindo a **validação fail-closed da política**:
   a declaração nova só passa se estiver completa e coerente;
2. **passo 1/2** — instalação em banco limpo + `--test-enable` com 109 testes do módulo, piso
   conferido (teste que não roda não passou) e nomes dos 27 testes desta suíte conferidos no log;
3. **passo 3** — dupla descartável própria (`postgres:16` + `odoo:19.0`), servidor HTTP real e as
   chamadas por `curl` (consumidor **externo**, não o cliente de teste): cria/atualiza/repete,
   recusas, dry-run. Depois, **leitura por SQL** no banco: 1 registro por UUID, espelho gravado,
   estágio/valor do Odoo intactos, dono do lead;
4. **passo 3c/3d** — auditoria no log do servidor (1 linha por chamada autenticada, token e payload
   ausentes) e a guarda de ambiente do ADR-005 na escrita, com o ambiente trocado **pelo ORM** e o
   servidor reiniciado depois da troca;
5. **`--prova-de-dente` (fail-closed)** — três mutações em cópias do módulo; cada prova TEM de
   rodar de verdade e reprovar **o item esperado**; prova que não mede nada reprova (lição do
   defeito `t_fa9db205`, o harness fail-open da rodada 1 do E01-T01). A âncora externa por sha256
   do módulo (sem `__pycache__`) prova que o artefato real saiu intacto.

---

## §4 ROLLBACK

A operação é **declaração**: o rollback é de uma linha.

1. **Imediato (sem deploy):** no banco de produção, apontar o parâmetro
   `tf.api.politica` para uma política anterior (ou remover o bloco da operação). O servidor lê a
   política por chamada; a operação volta a responder `404 operacao_nao_declarada`.
2. **Versão anterior da política:** `git show <commit-anterior>:.../api/politica_api.json` —
   a `1.0.0` continua no histórico (nada foi editado no lugar).
3. **Reversibilidade de dados:** a operação só escreve no espelho (`tf_*`, `name`, `type`,
   `partner_id`) e **nunca** em campo de dono do Odoo (AC4) — congelar a operação não exige
   corrigir pipeline nem valor negociado. Os leads criados por ela são rastreáveis por
   `tf_opportunity_id` e pelo rastro (`tf_last_sync_at`, `tf_last_event_type`).
4. **Guarda de ambiente:** desligar a operação sem deploy é a mesma troca de parâmetro do ADR-005.

Ordem de reversão (emergência): (a) congelar a operação na política; (b) avaliar os leads tocados
pela trilha `TF_API_AUDIT`; (c) só então decidir sobre dados. Nunca o contrário.

---

## §5 RISK

| risco | mitigação medida neste card |
| --- | --- |
| o espelho sobrescrever decisão comercial (estágio/valor) | **AC4**: campos de dono não declarados → 422 nomeada, e o registro inteiro de dono é comparado antes/depois (suíte 16) |
| duplicar oportunidade a cada chamada | identidade canônica (UUID) + contagem por SQL: 3 chamadas, 1 registro (passo 3); 5 chamadas, 1 registro (suíte 11) |
| `name` é `compute` no Odoo 19 e "volta sozinho" | medido no ORM: o `compute` só preenche **quando o nome está vazio**, então nome enviado permanece (suíte 08/12) |
| payload de negócio vazar para log/trilha | `AC7`: a trilha não tem payload, medido no log bruto do servidor |
| escrita fora do ambiente declarado | `AC6`: guarda do ADR-005 medida por curl, com o servidor reiniciado **depois** da troca feita pelo ORM (cache de `ir.config_parameter`) |
| política é artefato compartilhado da onda | declaração aditiva + versão nova + testes que leem o artefato (âncora `ANCORA:ITEM_DATADO`) |
| item de aceite "verde" sem medir nada | harness de dentes fail-closed: prova sem medição reprova |

---

## §6 O contrato HTTP da operação

```http
POST /tf/api/v1/oportunidade_upsert
Authorization: Bearer <chave de API>
Content-Type: application/json

{
  "idempotency_key": "tre-...",
  "correlation_id": "opcional — ecoado",
  "dry_run": true,                         // opcional: descreve, não escreve
  "parametros": {
    "valores": {
      "name": "Oportunidade X",            // obrigatório (name é required no crm.lead)
      "tf_opportunity_id": "<uuid v4>",    // identidade canônica
      "tf_priority_score": 82.5,
      "tf_next_best_action": "FOLLOW_UP",
      "tf_idempotency_key": "tre-...",
      "tf_last_event_type": "OPPORTUNITY_RECOMMENDED",
      "tf_last_sync_at": "2026-10-02 03:00:00"
    }
  }
}
```

Resposta `200` (sucesso):

```json
{
  "ok": true, "operacao": "oportunidade_upsert", "acao": "upsert", "ambiente": "dev",
  "politica_versao": "1.1.0", "correlation_id": "...", "idempotency_key": "...",
  "dry_run": false,
  "dados": {"ids": [42], "acao_efetiva": "criar"}
}
```

`dados.acao_efetiva` é `criar` na primeira chamada de um UUID e `atualizar` depois. Em `dry_run`, o
envelope traz `dry_run: true` e `dados` descreve (`acao_efetiva`, `criaria`/`atualizaria`) sem
escrever. Códigos de recusa (envelope de erro com `codigo`):

| `codigo` | HTTP | quando |
| --- | --- | --- |
| `campo_nao_declarado` | 422 | campo de dono do Odoo (`stage_id`, `expected_revenue`, `probability`), derivado (`tf_priority_tier`) ou de outro modelo |
| `campo_obrigatorio_ausente` | 422 | `name` fora do payload, ou o UUID canônico ausente |
| `valor_invalido` | 422 | UUID canônico fora do formato (constraint do modelo, via ORM) |
| `idempotency_key_ausente` | 422 | escrita sem a chave |
| `idempotency_key_invalida` | 422 | chave fora do formato |
| `payload_invalido` | 400 | chave desconhecida no corpo |
| `ambiente_nao_permitido` / `aprovacao_ausente` | 503 | guarda do ADR-005 |
| `operacao_nao_declarada` | 404 | operação fora da política em vigor |

---

## §7 Armadilhas medidas (Odoo 19) — o que custou rodada

1. **`url_open` sem corpo é GET.** `self.url_open(rota, json={})` não envia corpo/Content-Type e o
   roteador responde **405 Method Not Allowed** — barrado **antes** do controlador, ou seja, o item
   não mede o que promete. Correção: `method="POST"` explícito e corpo não vazio (item sem
   parâmetro manda só `correlation_id`).
2. **Semente de teste não commitada é invisível para o HTTP.** A requisição serve-se de outra
   conexão: registro criado pelo ORM da suíte não é encontrado pelo upsert, que então **cria
   outro** — o item mediria o oposto do que afirma. Correção: semear **pela própria API** (o que
   também é o que se quer medir). `ir.config_parameter.set_param` é a exceção: ele commita, então
   trocar ambiente por ele é visível ao servidor (é o que o passo 3d usa).
3. **`crm.lead.name` é `compute` (`_compute_name`, `store=True`, `readonly=False`)** e o compute só
   age **quando o nome está vazio** — por isso o nome enviado permanece, mas um payload sem `name`
   criaria um lead com nome computado pelo Odoo. A declaração fecha isso antes do ORM
   (`campos_obrigatorios: [name]`).
4. **`res.users.groups_id` não existe no Odoo 19** (é `group_ids`) — armadilha herdada do E01-T01,
   que continua valendo para qualquer suíte nova.
5. **Falha cedo mata a medição.** O verificador aborta a fase HTTP se houver qualquer falha nos
   passos anteriores (`if [ "$FALHAS" -gt 0 ]; then resumo; fi`). Item verde depois de um abort
   desses é item que **não rodou** — foi o que a rodada 1 mostrou (35 itens, 3 falhas, fase HTTP
   inteira não executada).

---

## §8 Lacunas e pendências herdadas

- **Deduplicação por `idempotency_key`** (retry/replay): `TRE-W3-E02-T02`. Aqui a chave é exigida,
  validada e registrada na trilha; "não duplicar" vem da identidade canônica.
- **Atribuição comercial** (vendedor/equipe): não é desta operação — o lead nasce sob o usuário de
  integração e é medido assim (suíte 26).
- **Reconciliação de lead criado à mão no CRM** (sem `tf_opportunity_id`): a duplicata é reportada
  pela reconciliação (`TRE-W4-E04-T01`), nunca corrigida em silêncio (contrato §5/§8).
- **Publicação do módulo na cópia operacional** (`/opt/tre/repo`): pendência herdada do `E03-T01`.
- **Ratificação da versão 19.0 e homologação** (estágio 7): com o Anderson; a revisão independente
  deste card é do estágio 6 (perfil `tester`).

---

## §9 Aceite medido — VPS do dev, 02/10/2026

_(preenchido depois da rodada que mediu o commit entregue)_
