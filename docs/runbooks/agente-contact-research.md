# Runbook — Agente Contact Research v1 (`contact_research/1.0.0`)

Card **TRE-W4-E05-T01**. Este agente roda **depois** do Research: a empresa já está em
`organizations`; o que ele entrega é o **contato comercial** dela em `contacts`.

> Leia primeiro `docs/architecture/agente-contact-research-v1.md` (§7 ACCEPTANCE). Este runbook é
> o passo a passo de operação.

## 1. Onde o agente roda

- **Na VPS, dentro do container do Hermes** (o agente é só Python; o PostgreSQL vive na VPS —
> ADR-0008). `--prefixo` é o comando do `psql` **já apontado** para o banco alvo.
- O agente **não tem credencial**: quem aponta o banco é o `--prefixo`. No dev:

```bash
--prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"
```

- `--ambiente` aceita `dev` e `homolog`. `prod` é recusado de propósito (exit 4) e nada é escrito.
- Não existe caminho HTTP: nenhuma chamada de LLM ou de API externa na v1.

## 2. Rodar uma rodada

```bash
cd /opt/tre/transformativa-revenue-engine

# 1) ensaio sem banco (não abre conexão nenhuma)
python3 hermes/agents/contact_research/contact_research.py --planejar \
    --fonte hermes/agents/contact_research/exemplos/contatos-exemplo.jsonl

# 2) rodada no dev (o correlation_id identifica o lote inteiro)
python3 hermes/agents/contact_research/contact_research.py --ambiente dev \
    --correlation-id "$(uuidgen)" \
    --fonte /tmp/contatos-do-dia.jsonl \
    --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"
```

Exit codes: `0` rodada inteira processada · `1` falha de porta/veredito ERRO · `2` uso · `4`
ambiente recusado.

## 3. Ler o resultado

O agente imprime uma linha JSON por pedido e, no fim, o **relatório**:

```json
{"correlation_id": "...", "total": 11, "por_veredito": {"IDENTIFICADO": 5, "JA_IDENTIFICADO": 0,
 "REVISAO_IDENTIDADE": 1, "RECUSADA": 5, "ERRO": 0}}
```

- `IDENTIFICADO` — contato escrito (criado ou enriquecido). Dá para distinguir por
  `acao` no `request_payload` do `sync_events` (`criado` × `enriquecido`).
- `JA_IDENTIFICADO` — replay idempotente: a chave do pedido já foi reivindicada. Nada foi escrito.
- `REVISAO_IDENTIDADE` — a empresa casou com 2+ organizações: foi para a fila humana
  (`human_approvals`). **Nada** de contato foi escrito.
- `RECUSADA` — entrada inválida (motivo no `output` da auditoria e no `descartados`).
- `ERRO` — porta/auditoria falhou: **fail-closed**, trate antes de seguir.

## 4. Consultas úteis

```sql
-- o que a rodada escreveu (a verdade da rodada)
SELECT id, status, request_payload->>'acao' AS acao, request_payload->>'contato_id' AS contato,
       request_payload->>'organization_id' AS org, request_payload->'antes' AS antes,
       request_payload->'depois' AS depois
  FROM sales_intelligence.sync_events
 WHERE operation = 'CONTACT_RESEARCH'
   AND request_payload->>'correlation_id' = '<correlation_id>'
 ORDER BY created_at;

-- descartes com motivo (nada foi mesclado em silêncio)
SELECT jsonb_array_elements(output->'descartados') AS descarte
  FROM sales_intelligence.agent_runs
 WHERE correlation_id = '<correlation_id>' AND output ? 'descartados';

-- fila humana da rodada
SELECT id, action_type, status, created_at
  FROM sales_intelligence.human_approvals
 WHERE action_type = 'CONTACT_IDENTITY_REVIEW' AND status = 'PENDING';

-- contato e os flags do titular (o agente nunca escreve neles)
SELECT email, job_title, decision_role, do_not_contact, opt_out_email, opt_out_whatsapp
  FROM sales_intelligence.contacts WHERE id = '<contato_id>';
```

## 5. Desfazer uma rodada

```bash
# dry-run (padrão): mostra o que recusaria e o que faria, sem escrever
python3 hermes/agents/contact_research/contact_research.py --desfazer <correlation_id> \
    --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"

# aplica
python3 hermes/agents/contact_research/contact_research.py --desfazer <correlation_id> --confirmo \
    --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence"
```

O que o `--confirmo` faz:

- **restaura** as colunas que a rodada enriqueceu (valor anterior gravado em `antes`);
- **apaga** os contatos que a rodada **criou**;
- **apaga** os `sync_events` da rodada e registra um `sync_events` de `ROLLBACK`;
- **não** toca o contato pré-existente, `agent_runs`, `human_approvals` nem `organizations`.

Quando ele **recusa** (exit 1): se algum contato criado pela rodada já tem `odoo_partner_id`
(espelhado no CRM), apagar aqui deixaria as duas pontas discordando. O runbook manda chamar o
humano, não insistir — o `desfazer` foi desenhado para ser fail-closed.

## 6. Verificação

```bash
# offline (sem banco e sem rede): 60 itens + 25 mutações
python3 scripts/agentes/verificar_agente_contact_research.py --autoteste

# E2E em container descartável NA VPS (o aceite recusa rodar se o container já existir)
bash scripts/agentes/teste_contact_research_aceite.sh            # 65 itens
bash scripts/agentes/teste_contact_research_aceite.sh --prova-de-dente

# estrutura (artefatos versionados)
bash scripts/verificar_estrutura.sh
```

## 7. Quando parar e chamar humano

- `ERRO` em qualquer pedido (porta/auditoria fora do ar) — nada de "seguir e ver depois".
- `REVISAO_IDENTIDADE` acumulando na fila: a identidade da empresa está ambígua (2+ fortes
  apontando para organizações diferentes) e quem decide é gente.
- `--desfazer --confirmo` recusando por contato espelhado: o CRM já viu o contato.
- Ambiguidade de identidade de **contato**: dois e-mails diferentes para a mesma pessoa não é
  resolvido pelo agente (a identidade dele é o e-mail) — cadastro é decisão humana.
