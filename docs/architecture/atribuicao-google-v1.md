# Atribuicao de lead do Google v1 (`google-lead-attribution-v1`) — TRE-W7-E03-T01

Componente: `hermes/inbound/google/atribuicao_google.py` · contrato:
`hermes/inbound/google/atribuicao-google-v1.json` · suíte offline:
`scripts/inbound/verificar_atribuicao_google.py` · aceite: `scripts/inbound/aceite-atribuicao-google.sh`.

## 1. O que decide

Um lead que chega do Google (formulario de Lead Ads do Google Ads, ou formulario do site com
`gclid`/`utm_*`) e' ATRIBUIDO a um canal e — quando a evidencia permite — a uma campanha, por TABELA
DECLARADA em contrato (primeira regra que casa vence):

| ordem | evidencia | confianca | quando |
|---|---|---|---|
| 1 | `FORMULARIO_GOOGLE_ADS` | 0,95 | fonte `google_ads_lead_form` com `campanha_id` no payload (o produto carrega esse campo) |
| 2 | `GCLID_RESOLVIDO` | 0,90 | `gclid` resolvido pela porta declarada da Ads API, com campanha |
| 3 | `GCLID_NAO_RESOLVIDO` | 0,60 | `gclid` presente e a porta responde `NAO_ENCONTRADO`: canal sim, campanha NAO |
| 4 | `UTM_SOURCE_GOOGLE` | 0,45 | `utm_source=google` (evidencia fraca declarada: diz o canal, nao a campanha) |
| — | `SEM_IDENTIFICADOR` | 0,00 | nada casou: `NAO_ATRIBUIDO`, sem canal, sem linha em `interactions` |

Incoerencia declarada: `google_ads_lead_form` **sem** `campanha_id` e' `FORMULARIO_SEM_CAMPANHA`
(`NAO_ATRIBUIDO`) — nao se adivinha campanha.

## 2. Onde grava (sem coluna nova)

Data Contract V1 nao tem tabela/coluna de atribuicao (mudar exigiria nova versao do contrato), entao:

- `sales_intelligence.interactions` — o fato do lead: `channel='google'`, `direction='INBOUND'`,
  `interaction_type IN (LEAD_FORM, LEAD_WEB)`, `subject` e `content_summary` **sem PII**,
  `content_reference='google:<chave>'` (deterministico). `ai_confidence` fica **NULL**: decisao por
  regra declarada nao e' classificacao de IA.
- `sales_intelligence.sync_events` — a trilha: `idempotency_key='google-lead:<fonte>:<lead_id>'`,
  `status`, e o **envelope de atribuicao** em `request_payload` (evidencia, confianca, motivo,
  campanha/grupo/criativo/palavra-chave/`gclid`, `resolucao_gclid`, contato **mascarado**,
  `campos_desconhecidos`, carimbo sha256 do payload).

O id proprietario da campanha do Google (string) **nao** e' forjado em `interactions.campaign_id`
(coluna UUID canonica) — ele vive na trilha.

## 3. Limites por desenho

- sem contato em `contacts` (e-mail, depois telefone/whatsapp) → `SEM_VINCULO`, zero linha em
  `interactions`; nunca se inventa organizacao/contato (invariante 4 do W6-E05, igual em W7-E02);
- replay da mesma chave → `JA_INGERIDO`, sem linha nova e sem chamar a porta do `gclid`;
- `--desfazer <chave>` grava trilha `DESFEITO` (INSERT append) — a trilha original nao e' apagada;
- guardas ADR-005: `dev` exige banco local (`docker exec -i pg-(google|inbound|sales|odoo|aceite)… psql`)
  e resolvedor em loopback; `homolog` exige `TRE_GOOGLE_APROVACAO_HUMANA`; `prod` RECUSA (exit 4);
  segredo (`TRE_GOOGLE_ADS_TOKEN`) na saida → `SENHA_VAZADA` (exit 5).

## 4. Lacunas declaradas (nao medidas viram "OK")

1. A Ads API **real** (developer token + OAuth) nao e' chamada: mede-se contra a porta declarada em
   loopback (stub do aceite). Prova contra a API real e' de homolog, com credencial e aprovacao do dono.
2. **Sem evento de outbox**: o vocabulario de eventos do Data Contract V1 nao tem evento de lead
   inbound; emitir um exigiria nova versao do contrato. O encaminhamento ao CRM e' do caminho de
   integracao (W3/W4).
3. Criar `organizations`/`contacts` a partir de um lead inbound (empresa nova vinda do anuncio) nao e'
   deste card: o dono operacional do contato e' o Odoo (doc 05 §4).
4. Nada mede volume, taxa de clique ou qualidade do formulario: o aceite mede um lead por evidencia.
5. W7-E01 (captura no site) e W7-E02 (Meta) sao cards irmaos: o formato de payload e os dois
   desfechos (`ATRIBUIDO`/`NAO_ATRIBUIDO`, `SEM_VINCULO`) foram alinhados pelo padrao do W6-E05, mas
   cada card tem o seu contrato — quando E01/E02 chegarem, a comparacao e' humana, nao automatica.
