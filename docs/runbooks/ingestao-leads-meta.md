# Ingestão de leads Meta — runbook do card TRE-W7-E02-T01

Componente: `hermes/agentes/inbound/ingestao_leads_meta.py` (`meta-lead-ingestion-v1`).
Contrato: `hermes/agentes/inbound/meta-lead-ingestion-v1.json`. Integração: `docs/integrations/meta-leads-v1.md`.
Regra de ambiente: **nada nasce em produção** (ADR-005) — a promoção a produção é ato humano registrado.

## 1. Como se roda

**Suíte offline** (sem banco e sem rede externa; roda em qualquer máquina com python3 ≥ 3.11):

```bash
python3 scripts/agentes/verificar_ingestao_leads_meta.py              # 51 itens
python3 scripts/agentes/verificar_ingestao_leads_meta.py --autoteste  # + 7 mutações (prova de dente)
```

**Aceite E2E** (roda na **VPS do ambiente**, onde vive o Docker do TRE — ADR-0008):

```bash
bash scripts/agentes/teste_ingestao_leads_meta_aceite.sh                  # 53 itens, ~1 min
bash scripts/agentes/teste_ingestao_leads_meta_aceite.sh --prova-de-dente # dente do item 5.8
bash scripts/agentes/teste_ingestao_leads_meta_aceite.sh --manter         # deixa o triO de pé
```

O aceite cria um Postgres **descartável** (`pg-meta-acc`, migration 0001 + massa de 1 organização e 2
contatos), sobe o **stub local da Graph API** (`scripts/agentes/stub-meta-graph-dev.py`, 127.0.0.1) e
gera **webhooks assinados de verdade** (o próprio aceite calcula o HMAC-SHA256 do corpo cru) — inclusive
o caso de assinatura **errada** e a entrega inválida **repetida**. Requer `docker` com `postgres:16` e
`python3`. Ele **aborta** se o container ou a porta já existirem (sobra de rodada anterior não é medida).

**Uso manual do componente:**

```bash
python3 hermes/agentes/inbound/ingestao_leads_meta.py --planejar      # config efetiva (segredos mascarados)
python3 hermes/agentes/inbound/ingestao_leads_meta.py --conferir      # contrato + guardas + auditoria da fonte
python3 hermes/agentes/inbound/ingestao_leads_meta.py --ingerir --webhook entregas.jsonl \
    --chave-idempotencia "w7-e02:rodada-1" --env-file deploy/environments/dev-meta.env [--confirmo]
python3 hermes/agentes/inbound/ingestao_leads_meta.py --desfazer "meta-lead:<page>:<leadgen>" [--confirmo]
```

Exit: `0` OK/DRY_RUN/replay · `1` falha de execução · `2` uso · `3` recusa de guarda/contrato/fonte ·
`4` recusa de produção · `5` segredo vazado.

**Formato da entrega (JSONL)** — uma linha por entrega do webhook, como o n8n repassa:

```json
{"corpo": {"object": "page", "entry": [{"id": "<page_id>", "changes": [{"field": "leadgen",
  "value": {"leadgen_id": "<id>", "form_id": "<id>", "ad_id": "<id>", "created_time": 1790000000}}]}]},
 "assinatura": "sha256=<hmac do corpo canônico com o app secret>"}
```

Se `corpo` vier como objeto, o corpo assinado é a serialização canônica (`sort_keys`, separadores
compactos) — **uma só verdade de assinatura**, declarada aqui para não existirem duas.

## 2. Critérios de aceitação (ACCEPTANCE)

1. Webhook sem `X-Hub-Signature-256` válida é RECUSADO antes de qualquer chamada à Graph API
   (`ASSINATURA_INVALIDA`), e o replay da MESMA entrega inválida é `JA_INGERIDO` — não derruba a rodada.
2. Lead sem e-mail **e** sem telefone nunca vira linha em `interactions` (`DADOS_INSUFICIENTES`).
3. Contato desconhecido em `contacts` não inventa organização nem contato (`SEM_VINCULO`, 0 interação).
4. Idempotência por `meta-lead:<page_id>:<leadgen_id>`: replay é `JA_INGERIDO`, sem linha nova e **sem
   nova chamada** à Graph API.
5. Escrita restrita a `interactions` e `sync_events`, só INSERT (snapshot das 12 tabelas antes/depois).
6. Campo fora do mapa entra só pelo **nome** em `campos_desconhecidos`; `content_summary` não expõe
   e-mail/telefone em claro.
7. Guardas ADR-005: `dev` exige Graph em loopback e banco em container local; `homolog` exige aprovação
   humana registrada + lista de páginas; `prod` RECUSA (exit 4); `--ingerir` sem `--confirmo` é DRY_RUN.
8. Token e app secret nunca aparecem na saída (`SENHA_VAZADA`, exit 5, se aparecerem na gravação).

**TEST:** suíte offline (51 itens + 7 mutações) e aceite E2E (53 itens) + prova de dente.
**ROLLBACK:** reverter o commit da branch `feature/TRE-W7-E02-T01`; em runtime, `--desfazer <chave>`
marca a trilha `DESFEITO` por INSERT (a trilha de auditoria não é apagada — contrato §9).
**RISK:** Médio — ponta externa + segredo de aplicação; mitigado por assinatura fail-closed,
idempotência por chave única, escrita em 2 tabelas e aceite medido ponta a ponta.

## 3. O que este aceite **NÃO** mede (declarado, não escondido)

- **a Graph API real** e o webhook HTTPS da Meta: exige token de página e app secret reais, que o
  dev-harness não tem. A prova contra a ponta real é de **homolog** (aprovação humana + página na lista)
  — o componente já recusa homolog sem `TRE_META_APROVACAO_HUMANA`;
- a **verificação de `object`/app id** do lado da Meta (feita na borda);
- a **promoção do lead ao CRM** (Odoo) — é do fluxo de sync (W6-E06);
- a **criação do contato** quando ele não existe (`SEM_VINCULO` é o estado correto hoje; a política de
  criação/merge é da deduplicação do W1-E04 e do dono operacional do contato no Odoo);
- **rate limits** reais da Meta além do teto de tentativas declarado no contrato.

## 4. Defeito MEDIDO e corrigido nesta rodada (rodada 1 do aceite)

- **Sintoma:** o replay do lote morria com `BANCO_RECUSOU` — `duplicate key value violates unique
  constraint "sync_events_idempotency_key_key"` na trilha da entrega com **assinatura inválida**
  (chave `meta-lead:<assinatura-invalida>:<sha256 do corpo>`). A rodada inteira abortava (exit 3).
- **Causa raiz:** o caminho de "entrega sem lead" (assinatura inválida / entrega vazia) escrevia a trilha
  **sem** passar por `ja_ingerido` e sem defesa de conflito — os leads passavam, as trilhas de
  bookkeeping não.
- **Correção:** `ja_ingerido` nesses dois caminhos (replay ⇒ `JA_INGERIDO`) **e** `ON CONFLICT
  (idempotency_key) DO NOTHING` no `gravar_trilha(..., sem_conflito=True)` (defesa em profundidade, para
  duplicata concorrente não derrubar a rodada).
- **Detectado por:** o próprio aceite (item 4.7b, que passou a exigir o replay da entrega inválida).
- **Lição:** trilha de bookkeeping é escrita como o dado de negócio — com idempotência, não com INSERT cru.
