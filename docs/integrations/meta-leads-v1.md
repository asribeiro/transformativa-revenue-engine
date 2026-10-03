# Ingestão de leads Meta/Instagram v1 (`meta-lead-ingestion-v1`)

Contrato legível por máquina: `hermes/agentes/inbound/meta-lead-ingestion-v1.json`.
Componente: `hermes/agentes/inbound/ingestao_leads_meta.py`. Card: **TRE-W7-E02-T01** (onda W7 —
Inbound / Multicanal). Antecedente no fluxo: `TRE-W6-E07-T01` (E2E Outbound #002).

## 1. O que a integração é, na Meta (e o que ela NÃO é)

Um formulário de **Lead Ads** (Facebook/Instagram) não manda os dados do lead no webhook: o webhook do
objeto `page`, campo `leadgen`, entrega apenas o **identificador**. O dado pessoal vem **depois**, numa
leitura da Graph API (`GET /<versao>/{leadgen_id}`) com o token da página. Por isso este componente tem
duas metades — e as duas são medidas separadamente no aceite:

| Metade | O que é | Onde está |
|---|---|---|
| **porta de entrada** | as entregas cruas do webhook (corpo + `X-Hub-Signature-256`) | arquivo JSONL, como o n8n entrega |
| **primitivo de leitura** | `GET` na Graph API pelo `leadgen_id`, com `Bearer` | `puxar_lead()` + stub em dev |

A assinatura **HMAC-SHA256** do corpo cru com o app secret é o único controle que prova que a entrega
veio da Meta — e é aplicada **antes** de qualquer chamada externa (fail-closed). Entrega sem assinatura
válida não gera interação e não consome cota da Graph API.

## 2. O que o componente faz com o lead

1. lê o `field_data` e traduz os campos pelo **mapa declarado** (`campos.mapa`);
2. campo fora do mapa entra em `campos_desconhecidos` **apenas pelo nome** (sem valor) — não se persiste
   dado pessoal não mapeado nem se inventa coluna;
3. exige **e-mail OU telefone** em formato declarado (`email`, `phone`); sem nenhum dos dois, o lead é
   recusado (`DADOS_INSUFICIENTES`) em vez de virar contato vazio;
4. busca o vínculo real em `sales_intelligence.contacts` (e-mail, sem caixa, ou telefone só-dígitos):
   contato desconhecido **não cria** organização/contato — fica `SEM_VINCULO` na trilha;
5. grava **uma** linha em `sales_intelligence.interactions` para cada lead vinculado, com
   `channel=meta`, `direction=INBOUND`, `interaction_type=LEAD_META`, `response_category=LEAD_META`,
   `intent=PEDIDO_DE_CONTATO`, `sentiment=POSITIVO`, `ai_confidence=0.9` (tudo do contrato, sem LLM) e
   `content_reference = meta-lead:<page_id>:<leadgen_id>`;
6. `content_summary` traz o resumo **sem PII em claro** (e-mail/telefone mascarados; mensagem reduzida a
   tamanho) — o dado pessoal do lead vive no vínculo por `contacts` e na referência, não em texto livre;
7. `occurred_at` é o `created_time` do formulário (quando o lead declarou), não o relógio da ingesta;
8. `odoo_lead_id` e `campaign_id` ficam **nulos e declarados**: o CRM é do fluxo de sync (W6-E06) e o
   `ad_id` do Meta é identificador numérico do anúncio, **não** UUID canônico — virá-lo `campaign_id`
   seria identidade falsa (o anúncio fica em `subject`/`content_reference`/trilha).

## 3. Idempotência e estados da trilha (`sync_events`)

Chave: `meta-lead:<page_id>:<leadgen_id>` (UNIQUE). Replay devolve `JA_INGERIDO`, não grava de novo e
**não re-chama a Graph API**.

| status | quando |
|---|---|
| `PROCESSADO` | lead vinculado e interação gravada |
| `SEM_VINCULO` | contato não existe em `contacts` (nada é inventado) |
| `DADOS_INSUFICIENTES` | sem e-mail e sem telefone (ou fora de formato) |
| `ASSINATURA_INVALIDA` | HMAC ausente/malformado/divergente (antes de qualquer chamada) |
| `ENTREGA_SEM_LEAD` | entrega assinada sem notificação `leadgen` |
| `LEAD_INDISPONIVEL` | 404 na Graph API (lead apagado/anúncio removido) — definitivo, sem retry |
| `ERRO_GRAPH` | 401/403 (definitivo) ou 429/5xx/rede até o **teto de 2 tentativas** |
| `DESFEITO` | `--desfazer` marcou a chave (rollback executável; a trilha é imutável) |

Limite de escrita: **somente INSERT** em `interactions` e `sync_events`. Nenhum PUT/POST/DELETE na Graph
API (o primitivo é `GET`) e nenhuma DDL — a auditoria da própria fonte reprova antes de conectar.

## 4. Segredos

`TRE_META_APP_SECRET` (HMAC) e `TRE_META_ACCESS_TOKEN` (Graph) só por ambiente; o token vai no
**cabeçalho** `Authorization`, nunca na URL (URL vaza em log/proxy) e nunca é impresso. Toda gravação
(local e relatório) é conferida contra os dois valores: se aparecerem, `SENHA_VAZADA` (exit 5).

## 5. Lacunas declaradas (o que esta integração NÃO faz)

- não é o receptor HTTPS do webhook em produção — isso é borda/n8n (TLS, fila, `object`/`app id`);
- não cria organização/contato nem escreve no Odoo;
- não responde ao lead (abordagem é W6, com aprovação humana);
- não pontua ICP (W5/W8) — registra a interação, não a avalia;
- não reconcilia anúncios/`ad_id` com campanha canônica (não há UUID canônico para `ad_id` na V1);
- não consome campos fora do mapa (só registra o nome em `campos_desconhecidos`).
