# Runbook — Captura de lead de evento v1 (`TRE-W7-E06-T01`)

**Card:** `TRE-W7-E06-T01` (W7 · Inbound/Multicanal) · **Componente:** `hermes/agentes/inbound/captura_evento.py`
(`captura-evento-v1`) · **Contrato:** `hermes/agentes/inbound/captura-evento-v1.json`

## 1. O que este componente faz

Recebe a **coleta do evento** (QR, crachá, ficha ou lista) **já normalizada em JSON** e:

1. valida a coleta contra o contrato (`captura-evento-v1.json`) — vocabulário fechado, nunca inventado;
2. trata o **vínculo do evento como barreira**: sem `origem_evento.event_id`, sem `capture_method` no
   vocabulário ou sem `capturado_em` válido, a coleta é **recusada** (`EVENTO_NAO_DECLARADO`) — lead de
   evento sem o vínculo do evento é lead sem atribuição (doc 03, Motor 3 — Relationship);
3. trata **consentimento como barreira**: opt-in explícito + base legal declarada + **forma** (`TERMO_DIGITAL`,
   `FICHA_ASSINADA`, `QR_INSCRICAO`, `LISTA_PRESENCA`), que é a evidência de **como** o opt-in foi dado no
   evento;
4. resolve a identidade da empresa por **identificador forte** (CNPJ → domínio → LinkedIn) antes do **fraco**
   (nome+cidade, nome+telefone) e manda o fraco para `REVIEW_REQUIRED` — nunca merge silencioso;
5. grava `organizations` + `contacts` + `interactions` (canal `EVENTO`, direção `INBOUND`, tipo
   `CAPTURA_EVENTO`, `occurred_at` = `capturado_em`) com UUID canônico gerado no próprio INSERT;
6. grava a trilha de idempotência em `sync_events` com a chave `evento:<event_id>:<captura_id>`;
7. mascara e-mail, telefone e CNPJ na evidência (relatório/trilha).

**Formato da coleta** (ver `coleta.campos_obrigatorios` no contrato):

```json
{
  "captura_id": "evt-2026-10-02-0001",
  "origem_evento": {
    "event_id": "evento-pmi-sp-2026-10",
    "evento_nome": "Encontro PMI Sao Paulo — Gestao e IA",
    "evento_inicio": "2026-10-02",
    "local": "Sao Paulo/SP",
    "stand": "A-12",
    "capture_method": "QR_CODE",
    "capturado_em": "2026-10-02T17:40:00Z"
  },
  "consentimento": {"aceito": true, "legal_basis": "CONSENTIMENTO", "forma": "TERMO_DIGITAL"},
  "empresa": {"nome": "Distribuidora Aurora LTDA", "dominio": "aurora.test", "cidade": "Campinas", "estado": "SP"},
  "contato": {"nome": "Marina Prado", "email": "marina@aurora.test", "telefone": "+55 19 99888-7766", "cargo": "COO", "preferred_channel": "email"}
}
```

`capture_method` ∈ `QR_CODE | CRACHA | FICHA | IMPORTACAO_LISTA`. O `event_id` e o `capture_method` **não
têm coluna** nas 12 tabelas core: viajam no `content_summary`/`content_reference` da interação e no
request_payload da trilha (lacuna L3).

## 2. Como rodar

```bash
# sem tocar o banco
python3 hermes/agentes/inbound/captura_evento.py --planejar
python3 hermes/agentes/inbound/captura_evento.py --conferir
python3 hermes/agentes/inbound/captura_evento.py --autoteste          # auditoria da própria fonte

# dry-run (não grava) e escrita
python3 hermes/agentes/inbound/captura_evento.py --coleta coleta.json
python3 hermes/agentes/inbound/captura_evento.py --coleta coleta.json --confirmo \
  --porta-banco "docker exec -i pg-evt-acc psql -U sales_ai -d sales_intelligence" \
  --relatorio out/relatorio.json --trilha out/trilha.jsonl
```

Variáveis: `TRE_AMBIENTE` (`dev`|`homolog`|`prod`), `TRE_EVENTO_PORTA_BANCO`,
`TRE_EVENTO_APROVACAO_HUMANA`, `TRE_EVENTO_TOKEN` (se aparecer na evidência, a gravação é recusada — exit 5).

Exit codes: `0` capturado/replay/dry-run/recusa registrada · `2` uso errado · `3` recusa (contrato,
fonte, guarda, banco) · `4` produção recusada · `5` segredo vazado.

**Status possíveis** (`vocabulario.status_trilha`): `CAPTURADO`, `JA_CAPTURADO`, `REVIEW_REQUIRED`,
`RECUSADO_CONSENTIMENTO`, `EVENTO_NAO_DECLARADO`, `SEM_DADOS_MINIMOS`, `COLETA_INVALIDA`.

## 3. Verificação

| Instrumento | O que mede | Como rodar |
|---|---|---|
| `scripts/agentes/verificar_captura_evento.py` | decisão do componente contra porta de banco falsa (41 itens) | `python3 scripts/agentes/verificar_captura_evento.py` |
| `... --autoteste` | 5 dentes: cada mutação reprova o item declarado | `python3 scripts/agentes/verificar_captura_evento.py --autoteste` |
| `scripts/agentes/teste_captura_evento_aceite.sh` | cadeia inteira em PostgreSQL descartável (60 itens) | `bash scripts/agentes/teste_captura_evento_aceite.sh` |

O aceite exige `docker` com a imagem `postgres:16` e roda **na VPS do ambiente**; o container
(`pg-evt-acc`) e as pontas são descartáveis e ficam em `127.0.0.1`. Regressão registrada:
`ACEITE_CAPTURA_EVENTO_001_OK (60 itens, 0 falhas)`.

## 4. Guardas de ambiente (ADR-005)

- **dev:** porta de banco **local** obrigatória (`docker exec -i pg-<dev|aceite> psql …`); prefixo remoto
  (`ssh … psql`) é recusado com `BANCO_NAO_E_DEV` (exit 3).
- **homolog:** exige `TRE_EVENTO_APROVACAO_HUMANA` registrada (`HOMOLOG_SEM_APROVACAO`).
- **prod:** recusa por desenho (exit 4). Nada nasce em produção; a promoção é ato humano registrado.
- Escrever exige `--confirmo`; sem ele a rodada é `DRY_RUN` e nada toca o banco.

## 5. Lacunas declaradas (não são falha deste card)

1. **Sem escrita no Odoo.** O lead do evento não chega ao CRM por aqui: o vocabulário de eventos
   PG → Odoo do Data Contract V1 §6 não tem evento de lead capturado e o consumidor de outbox é
   fail-closed (evento fora do contrato → `DEAD_LETTER`). Criar `event_type` novo exige nova versão do
   contrato **+ aprovação humana registrada** (doc 12 / ADR-0004) — decisão do dono.
2. **Sem leitor de QR/crachá e sem endpoint de inscrição.** A captura recebe a coleta já normalizada; ligar o
   leitor/app do evento é operação de n8n/operador, fora do repositório.
3. **Sem entidade de evento no schema.** As 12 tabelas core não têm `events`/`event_attendances`: o vínculo
   do evento vive no `content_summary` da interação e no payload da trilha. Promover evento a entidade é
   mudança de esquema do contrato (nova versão + aprovação humana).
4. **Sem processamento em lote.** Uma coleta por execução; a lista de presença do evento é laço do
   operador/n8n sobre este mesmo componente.
5. `collected_at`/`retention_until` continuam ausentes do schema (lacuna já declarada no Data Contract V1 §9).

## 6. Rollback

Nada nasce em produção e nenhuma DDL acompanha o card: o rollback é **remover o branch/commit**
(5 arquivos novos) e, no ambiente de aceite, remover o container descartável (`docker rm -f pg-evt-acc`).
Não há migração a desfazer nem dado de dev a limpar que afete o ambiente em uso.
