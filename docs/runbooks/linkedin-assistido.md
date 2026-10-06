# LinkedIn assistido v1 — o canal LinkedIn assistido por IA (o humano publica)

Runbook do card **TRE-W7-E04-T01** (W7 · E04 · P2). O que é, como se roda e **o que não é medido**
(lacuna declarada — não se inventa medição que mente).

Componente: `hermes/agents/linkedin/linkedin_assistido.py` (`linkedin-assistido-v1`).
Contrato/política: `hermes/agents/linkedin/linkedin-assistido-v1.json`.
Aceite: `scripts/linkedin/verificar-linkedin-assistido.sh`.
Cenário de negócio: doc 03 §1 (Motor 2 — Inbound / Demand Generation, LinkedIn orgânico) e §8
(ação `PREPARE_LINKEDIN` do Next Best Action).

## 1. O que este componente é

O workflow **assistido** do canal LinkedIn. A máquina faz três coisas e só três:

| verbo | o que faz | onde vive |
|-------|-----------|-----------|
| `PREPARAR_RASCUNHO` | monta o rascunho (post ou DM) a partir da recomendação `PREPARE_LINKEDIN` + evidência lida do banco | `human_approvals.proposed_action` |
| `PEDIR_APROVACAO` | registra o pedido e o submete à decisão humana | `human_approvals` (`LINKEDIN_RASCUNHO`) |
| `REGISTRAR_ENGAJAMENTO` | registra o que chega (visita, curtida, comentário, DM, convite, menção) | `interactions` (`channel='LINKEDIN'`) |

**Quem publica é o humano, no LinkedIn, fora do sistema.** O que o sistema entrega, depois de
`APPROVED`, é o **artefato** (texto + `texto_hash`) para o dono colar — nunca um envio.

## 2. O que este componente NÃO é (e não vira por afrouxamento)

- **não publica, não agenda publicação** — `--tentar-publicar` e `AGENDAR_PUBLICACAO` recusam (exit 5);
- **não comenta, não reage, não segue, não convida, não manda DM, não menciona, não responde comentário**
  — as 11 ações humanas exclusivas recusam com exit 5 e ficam auditadas em `agent_runs`;
- **não usa API do LinkedIn nem automação de navegador** — o aceite mede por `grep` no código: **0**
  referência a `linkedin.com`, `requests`, `urllib`, `selenium`, `playwright`, `webdriver`;
- **não decide aprovação** — o dono da decisão é o workflow irmão (`TRE-W6-E03-T01`, ADR-0004); o
  componente **lê** o status e nunca o move (UPDATE é recusado por guarda de escrita);
- **não inventa fato** — sem evidência não existe rascunho; inferência só entra marcada com
  `ai_confidence` e nunca é apresentada como fato.

## 3. Como se roda

Roda **na VPS do ambiente** (ADR-0008: é lá que vive o Docker do TRE), do repositório em disco:

```bash
# o aceite (banco descartável `pg-lk-e04`, ~1 min)
bash scripts/linkedin/verificar-linkedin-assistido.sh

# com a prova de dente (3 sub-runs, cada um com o módulo mutado)
bash scripts/linkedin/verificar-linkedin-assistido.sh --prova-de-dente

# deixar o banco de pé para investigar
bash scripts/linkedin/verificar-linkedin-assistido.sh --manter

# uso operacional (dev/homolog; `prod` recusa)
python3 hermes/agents/linkedin/linkedin_assistido.py --planejar | --regras
python3 hermes/agents/linkedin/linkedin_assistido.py --ambiente dev --prefixo "docker exec -i pg-lk-... psql -U sales_ai -d sales_intelligence" --preparar <recommendation_id> [--tipo POST|DM]
python3 hermes/agents/linkedin/linkedin_assistido.py --ambiente dev --prefixo "..." --fila
python3 hermes/agents/linkedin/linkedin_assistido.py --ambiente dev --prefixo "..." --engajamento <organization_id> --tipo LINKEDIN_CURTIDA --ocorrido-em 2026-10-03T10:00:00Z
python3 hermes/agents/linkedin/linkedin_assistido.py --ambiente dev --prefixo "..." --tentar-publicar <approval_id>   # RECUSA exit 5
```

**Requer**: docker com a imagem `postgres:16`, `python3` e o checkout do repositório.
**Guardas**: o aceite ABORTA se o container `pg-lk-e04` já existir (não toca em `pg-sales-dev`,
`pg-odoo-dev`, `odoo-dev`, `proxy-dev`); a porta de banco é sempre um container do aceite (`--prefixo`).
**Exit codes do componente:** 0 OK · 1 FALHOU · 2 uso · 3 política/escrita recusada · 4 ambiente
recusado · **5 ação humana exclusiva recusada**.

## 4. O aceite, item a item (70 itens, 0 falhas em 03/10/2026)

- **0.x** — banco descartável de pé (`init` concluído duas vezes) e migration 0001 aplicada (12 tabelas);
- **1.x** — o contrato/política declara o canal, as 3 ações da máquina, as 11 proibidas, a aprovação
  obrigatória, o `action_type`, a ação `PREPARE_LINKEDIN` no vocabulário do Data Contract, os status no
  vocabulário de `human_approvals.status` e as 4 tabelas de escrita;
- **3.x** — o rascunho nasce da recomendação OPEN, o pedido nasce `PENDING` com `recommendation_id`,
  `texto_hash`, evidência citada e `publicacao = EXCLUSIVA_DO_HUMANO`, e a rodada fica auditada;
- **4.x/5.x/6.x** — idempotência (`JA_PEDIDO`), guarda de evidência (`SEM_EVIDENCIA`) e guarda de
  contato (`CONTATO_BLOQUEADO`), sempre com **zero escrita**;
- **7.x/8.x** — sem `APPROVED` nada é entregável; com a decisão humana registrada o pedido vira
  `ENTREGAVEL_AO_HUMANO` com texto e hash (o aceite faz o papel do operador, declarado);
- **9.x** — **o que a máquina nunca faz**: publicar e as 10 ações humanas exclusivas recusam (exit 5),
  com motivo nomeado, auditoria em `agent_runs` e zero escrita; 0 referência a rede no código;
- **10.x** — engajamento inbound vira `interactions` (`LINKEDIN`/`INBOUND`) com `ai_confidence` e é
  idempotente por chave;
- **11.x** — `prod` recusa (exit 4), as 8 tabelas fora do card não mudam, nenhum termo sensível na
  saída e o `--desfazer` **marca** em `sync_events` (não apaga, não muda status) e exige `--confirmo`.

## 5. Lacunas declaradas (o que este aceite NÃO mede)

1. **O ato de publicar do humano** — por desenho, fora do sistema: não há token, API nem navegador.
   O aceite mede que a máquina **não publica**; o que o dono faz depois, no LinkedIn, não é medido aqui.
2. **O rascunho por LLM** — no aceite vale o **renderizador offline declarado** (não inventa fato: cita
   a evidência lida). O gerador real é injetável (`--gerador <arquivo.py>`) e fica para homolog, como no
   card irmão de e-mail; a interface do gerador não é contratual neste card.
3. **Leitura do LinkedIn** — nenhum crawler/API: o engajamento é **informado** (telemetria do dono),
   não raspado. Quem alimenta a entrada é humano/harness, declarado.
4. **Vínculo com o Odoo** — este card não escreve no CRM (`odoo_lead_id` segue lacuna do caminho de
   fundação/sync medida no `TRE-W6-E07-T01`); o que se cria aqui é o pedido de aprovação e a interação.

## 6. Defeitos reais medidos nesta rodada (e corrigidos)

- **D1 — `psql` sem `-q` fazia `ON CONFLICT DO NOTHING` parecer escrita:** o `INSERT ... DO NOTHING`
  devolvia o tag `INSERT 0 0` no stdout e o claim de idempotência do engajamento era lido como sucesso —
  duas interações para o mesmo evento (medido no item 10.4/10.5). Correção: `-q` no `executar_sql` **e**
  guarda fail-closed (só vale a reclamação com um id UUID de volta).
- **D2 — chave achatada do contrato:** `docs/data/data_contract_v1.json` usa `vocabularies` com chaves
  literais (`"human_approvals.status"`, `"recommendations.status"`), não aninhadas; a leitura aninhada
  devolvia lista vazia e **todos** os status pareciam fora do vocabulário (medido no item 1.7 — a
  política de leitura estava errada, não a política do componente). Correção no módulo + comentário.
- **D3 — a foto da guarda de escrita era tirada antes da massa:** o item 11.3 acusava a própria massa do
  aceite como "escrita fora do card". Correção: a foto passa a ser tirada **depois** do insumo.
