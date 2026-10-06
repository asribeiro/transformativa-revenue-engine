# Runbook — WhatsApp engaged-lead workflow v1 (`TRE-W7-E05-T01`)

**Card:** `TRE-W7-E05-T01` (W7 · Inbound/Multicanal) · **Componente:**
`hermes/agentes/inbound/whatsapp_lead.py` (`whatsapp-lead-v1`) · **Contrato:**
`hermes/agentes/inbound/whatsapp-lead-v1.json`

## 1. O que este componente faz

Conduz o **lead que já está na base** e escreve por WhatsApp. Recebe a mensagem inbound **já
normalizada em JSON** (o webhook do provedor é do n8n/operador — lacuna L2) e:

1. valida o evento contra o contrato (`whatsapp-lead-v1.json`) — tipo de mensagem e vocabulário
   fechados, nunca inventados pelo código; `recebido_em` inválido **recusa** (a hora não se inventa,
   porque a janela de atendimento depende dela);
2. resolve a identidade pelo **núcleo nacional do telefone** (11 dígitos; `+55 11 98888-7777`,
   `11988887777` e `5511988887777` dão o mesmo núcleo) contra `contacts.whatsapp` e `contacts.phone`;
   desconhecido → `SEM_VINCULO`, ambíguo (2+ contatos) → `REVIEW_REQUIRED` — **nada é criado**;
3. classifica por **regra declarada** no contrato, com `OPT_OUT` na ordem 1 (descadastro vence
   qualquer sinal de interesse);
4. calcula a **janela de atendimento** do provedor (24 h desde a última entrada do contato) e decide o
   **próximo passo proposto** — sem nunca enviar;
5. grava `interactions` (`channel=WHATSAPP`, `direction=INBOUND`, `interaction_type=WHATSAPP_MENSAGEM`)
   e a trilha de idempotência `whatsapp:<message_id>` em `sync_events` (INSERT apenas);
6. mascara o telefone do lead na evidência (relatório/trilha).

**Formato do evento** (ver `evento.campos_obrigatorios` no contrato):

```json
{
  "message_id": "wamid-0001",
  "recebido_em": "2026-10-03T18:12:00Z",
  "remetente": {"telefone": "+55 11 98888-7777", "nome_perfil": "Marina"},
  "mensagem": {"tipo": "TEXTO", "texto": "podemos conversar amanha?"},
  "canal": {"origem": "provedor_whatsapp", "numero_destino": "+55 11 3333-1000"}
}
```

## 2. Como rodar

```bash
# sem tocar o banco
python3 hermes/agentes/inbound/whatsapp_lead.py --planejar
python3 hermes/agentes/inbound/whatsapp_lead.py --conferir
python3 hermes/agentes/inbound/whatsapp_lead.py --autoteste          # auditoria da própria fonte

# dry-run (não grava) e escrita
python3 hermes/agentes/inbound/whatsapp_lead.py --evento evento.json
python3 hermes/agentes/inbound/whatsapp_lead.py --evento evento.json --confirmo \
  --porta-banco "docker exec -i pg-whatsapp-acc psql -U sales_ai -d sales_intelligence" \
  --relatorio out/relatorio.json --trilha out/trilha.jsonl
```

Variáveis: `TRE_AMBIENTE` (`dev`|`homolog`|`prod`), `TRE_WHATSAPP_PORTA_BANCO`,
`TRE_WHATSAPP_APROVACAO_HUMANA`, `TRE_WHATSAPP_TOKEN` (se aparecer na evidência, a gravação é recusada
— exit 5), `WHATSAPP_LEAD_CONTRATO` (contrato alternativo).

Exit codes: `0` recebido/replay/dry-run/recusa registrada · `2` uso errado · `3` recusa
(contrato/fonte/guarda/banco) · `4` produção recusada · `5` segredo vazado.

**Status possíveis** (`vocabulario.status_trilha`): `RECEBIDO`, `JA_RECEBIDO`, `SEM_VINCULO`,
`REVIEW_REQUIRED`, `BLOQUEADO_POR_BLOQUEIO`, `EVENTO_INVALIDO`, `SEM_DADOS_MINIMOS`.

**Próximo passo proposto** (`vocabulario.proximo_passo`): `RESPOSTA_LIVRE_SUGERIDA` (janela aberta),
`REENGAJAMENTO_COM_TEMPLATE_APROVACAO_HUMANA` (janela fechada) e `NENHUM_FILA_HUMANA` (bloqueio ou
descadastro). **É proposta**: o envio é ação L1 com aprovação humana e não existe neste componente.

## 3. Verificação

| Instrumento | O que mede | Como rodar |
|---|---|---|
| `scripts/agentes/verificar_whatsapp_lead.py` | decisão do componente contra porta de banco falsa (**60 itens**) | `python3 scripts/agentes/verificar_whatsapp_lead.py` |
| `... --autoteste` | **10 dentes**: cada mutação reprova o item declarado | `python3 scripts/agentes/verificar_whatsapp_lead.py --autoteste` |
| `scripts/agentes/teste_whatsapp_lead_aceite.sh` | cadeia inteira em PostgreSQL descartável (**68 itens**) | `bash scripts/agentes/teste_whatsapp_lead_aceite.sh` |

O aceite exige `docker` com a imagem `postgres:16` e roda **na VPS do ambiente**; o container
(`pg-whatsapp-acc`) e as pontas são descartáveis e ficam em `127.0.0.1`. Regressão registrada:
`ACEITE_WHATSAPP_LEAD_001_OK (68 itens, 0 falhas)`.

Cenário do aceite (um contato por jornada, números distintos para não cruzar as jornadas): lead
engajado (interesse + replay), contato com `do_not_contact`, contato que manda `PARAR` (descadastro),
contato com última entrada há 3 dias (janela fechada), par ambíguo (mesmo núcleo em formas diferentes)
e telefone fora da base.

## 4. Guardas de ambiente (ADR-005)

- **dev:** porta de banco **local** obrigatória (`docker exec -i pg-<dev|aceite> psql …`); prefixo
  remoto (`ssh … psql`) é recusado com `BANCO_NAO_E_DEV` (exit 3).
- **homolog:** exige `TRE_WHATSAPP_APROVACAO_HUMANA` registrada (`HOMOLOG_SEM_APROVACAO`).
- **prod:** recusa por desenho (exit 4). Nada nasce em produção; a promoção é ato humano registrado.
- Escrever exige `--confirmo`; sem ele a rodada é `DRY_RUN` e nada toca o banco.

## 5. Defeitos medidos na rodada 1 do aceite (corrigidos e com dente)

Os dois foram encontrados **por execução real** (rodada 1: 50 itens, 18 falhas) e cada um tem item de
regressão com dente próprio:

- **D-A — trilha duplicada por mensagem.** `gravar_interacao` gravava a trilha em `sync_events` e o
  núcleo gravava de novo; a chave `whatsapp:<message_id>` é UNIQUE, então o segundo INSERT estourava
  (`duplicate key value violates unique constraint "sync_events_idempotency_key_key"`) e a mensagem
  terminava em `RECUSA` **com a interação já gravada**. Correção: quem grava a trilha é só o núcleo
  (`gravar_trilha`), com o status do veredito. Dente: `D9` (a linha de trilha duplicada reprova o item
  “uma mensagem gera UMA trilha”).
- **D-B — porta de banco lendo só a última linha do JSON.** O `psql` quebra o valor agregado em várias
  linhas (`json_agg` com 2+ linhas sai como `[{"…"}, \n {"…"}]`), e ler `linhas[-1]` derrubava **toda
  leitura com 2+ resultados** (`BANCO_RESPOSTA_INVALIDA`) — foi o caso do telefone ambíguo, que existe
  justamente para ir a `REVIEW_REQUIRED`. Correção: a porta lê o **documento JSON** (reúne as linhas).
  Dente: `D10` (voltar a ler só a última linha reprova o item “porta de banco lê o DOCUMENTO JSON”).

## 6. Lacunas declaradas (não são falha deste card)

1. **Sem envio.** Outbound de canal é ação L1 (primeiro contato/reengajamento) com aprovação humana
   registrada, e as duas pontas reais (provedor + número) exigem credencial do Sales AI. A saída do
   card é a **proposta** de próximo passo.
2. **Sem webhook HTTP.** O callback do provedor é do n8n/operador (fora do repositório); aqui entra a
   mensagem já normalizada.
3. **Sem propagação de descadastro.** `OPT_OUT` é classificado e registrado, mas **não** altera
   `contacts` (dono operacional é o Odoo, doc 05 §4) nem o CRM: a propagação é do `W6-E06`.
4. **Sem escrita no Odoo.** O vocabulário de eventos PG → Odoo do Data Contract V1 §6 não tem evento de
   mensagem de canal e o consumidor de outbox é fail-closed. Criar `event_type` novo exige nova versão
   do contrato **+ aprovação humana registrada** (doc 12 / ADR-0004).
5. **Sem mídia.** Imagem/áudio/vídeo/documento não são baixados nem transcritos: a interação guarda
   metadados e categoria `INDEFINIDO`.
6. **Janela de 24 h é parâmetro declarado**, não medida contra o provedor real (sem credencial) — a
   homologação com credencial é ato humano.
7. `collected_at`/`retention_until` continuam ausentes do schema (lacuna já declarada no Data Contract
   V1 §9).

## 7. Rollback

Nada nasce em produção e nenhuma DDL acompanha o card: o rollback é **remover o branch/commit**
(5 arquivos novos + 4 apêndices de doc/portão) e, no ambiente de aceite, remover o container descartável
(`docker rm -f pg-whatsapp-acc`). Não há migração a desfazer nem dado de dev a limpar que afete o
ambiente em uso; `contacts`, `outbox_events` e `recommendations` nunca são tocadas pelo componente.
