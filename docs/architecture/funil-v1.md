# Funil — dashboard derivado (`funil-v1`) — card TRE-W8-E01-T01 (W8 / Analytics)

**Card:** `TRE-W8-E01-T01` (board `transformativa-revenue-engine`) · **Onda:** W8 · **Épico:** E01 · **Prioridade:** P2
**Depende de:** `TRE-W6-E07-T01` (E2E Outbound #002, fechado e medido) · **Destrava:** `TRE-W8-E02-T01`
(conversão por segmento), `TRE-W8-E03-T01` (eficácia dos scores) e `TRE-W8-E05-T01` (custo de agentes).
**Componente:** `hermes/agentes/analytics/funil.py` · **Contrato:** `hermes/agentes/analytics/funil-v1.json`
**Runbook:** `docs/runbooks/funil.md` · **Aceite:** `scripts/agentes/teste_funil_aceite.sh`
**Suíte offline:** `scripts/agentes/verificar_funil.py --autoteste`

---

## 1. Problema que o card resolve

O doc 03 §2 e o Data Contract V1 §7.1 congelam a **ordem** do funil (`Descoberto → … → Negociação →
Won | Lost`, com `Nurture` lateral a partir de `Qualificado`), mas o **dono do estágio é o Odoo**
(contrato §2) e **nada materializa estágio em coluna do PostgreSQL**: a ingestão dos eventos
Odoo → PostgreSQL (`n8n/contracts/odoo-events-ingest.v1.json`, card `TRE-W3-E03-T01`) grava o **evento**
com o payload integral em `sync_events` e deixa a materialização, por decisão declarada, "para quem
precisar". Este card é quem precisa: o funil é **derivado em leitura**.

## 2. Decisões de desenho (nenhuma regra nova de contrato)

| # | Decisão | Porquê |
|---|---|---|
| D1 | A unidade do funil é a **organização** (UUID canônico do contrato §3), não a linha de fato | funil é jornada de empresa; contar linhas de interação mistura canal com estágio |
| D2 | O estágio é derivado por **evidência declarada por estágio** (tabela + filtro), nunca por heurística de texto | o contrato diz qual tabela é dona de cada fato; adivinhar estágio é inventar dado |
| D3 | **Alcance cumulativo**: quem chegou a Reunião passou por Abordagem iniciada | funil é alcance; contagem de evidência própria quebra a leitura de conversão |
| D4 | `Won` e `Lost` dividem o **nível terminal**: perdido não conta em ganho (e vice-versa); no nível terminal vale apenas a evidência própria do rótulo | são desfechos mutuamente exclusivos do mesmo nível |
| D5 | `Nurture` é **ramo lateral**: conta só por evidência própria, não implica nem é implicado pela linear; converte de `Qualificado` | doc 03 §2 desenha o ramo saindo de Qualificado |
| D6 | Rótulo de estágio (de `organizations.status` ou de `payload.estagio_novo` na trilha) casa por **igualdade exata depois de normalizar** (trim, caixa, acento, espaço) | casar prosa por semelhança/radical foi reprovado três vezes no roteador JEV deste projeto (D04/D06/D07): é peneira, não barreira |
| D7 | Atribuição do evento da trilha: `entity_id` quando casa organização conhecida; senão `payload.organizacao_id`; senão **lacuna** | `OPPORTUNITY_WON/LOST` carregam o UUID da **oportunidade** — atribuir ao "primeiro lead parecido" seria inventar vínculo |
| D8 | Leitura pura **provada por mecanismo**: `SET default_transaction_read_only = on` em `-c` separado, mais auditoria da fonte que reprova verbo de escrita antes de conectar | medido: `SET …; SELECT …` num único `-c` **não** vale (a transação implícita já começou e o INSERT de prova passou) |
| D9 | O dashboard é um **artefato gerado** (JSON + HTML auto-contido), não um serviço | ADR-005: nada nasce em produção; servir página por HTTP é ato de operador/homolog |
| D10 | `--ambiente prod` RECUSA (exit 4) **antes de ler contrato, base ou formato** | guarda de ambiente não pode depender do estado dos artefatos |

## 3. Estágios, evidência e dono

| # | Estágio | Nível | Fonte declarada (evidência) | Dono |
|---|---|---|---|---|
| 1 | Descoberto | 0 | linha viva em `organizations` (`deleted_at IS NULL`) | PostgreSQL |
| 2 | Pesquisado | 1 | `research_runs` concluída **ou** `signals` da organização | PostgreSQL |
| 3 | Qualificado | 2 | `scores` com `score_type = 'PRIORITY'` **e** `score_version` preenchida | PostgreSQL |
| 4 | Contato identificado | 3 | `contacts` com canal de resposta preenchido (e-mail **ou** telefone **ou** WhatsApp) — a **existência**, nunca o valor | PostgreSQL |
| 5 | Abordagem iniciada | 4 | `interactions` `direction = 'OUTBOUND'` | PostgreSQL |
| 6 | Engajamento | 5 | `interactions` `direction = 'INBOUND'` **com** `response_category` (resposta classificada) | PostgreSQL |
| 7 | Reunião | 6 | `recommendations` `CREATE_MEETING` executada **ou** fato `MEETING_CREATED` na trilha | Odoo |
| 8 | Diagnóstico | 7 | estágio publicado pelo Odoo na trilha | Odoo |
| 9 | Proposta | 8 | estágio publicado pelo Odoo na trilha | Odoo |
| 10 | Negociação | 9 | estágio publicado pelo Odoo na trilha | Odoo |
| 11 | Won | 10 | fato `OPPORTUNITY_WON` (ou estágio `Won`) na trilha | Odoo |
| 12 | Lost | 10 | fato `OPPORTUNITY_LOST` (ou estágio `Lost`) na trilha | Odoo |
| 13 | Nurture | — (lateral) | estágio `Nurture` na trilha | Odoo |

As duas vias dos estágios do Odoo existem porque o contrato §7 declara `organizations.status` como
`DISCOVERED` + os estágios do funil: quando a materialização do estágio passar a escrever na coluna
(decisão de outro card), **este componente já lê**, sem mudança de código.

## 4. Saída

- **JSON** (`funil.json`): `versao`, `card`, `ambiente`, `janela`, `contrato` (versão + sha256),
  `base` (organizações + digest), `estagios[]` (`nome`, `nivel`, `lateral`, `dono`, `alcancadas`,
  `evidencia_propria`, `conversao_de`, `conversao_da_anterior_pct`, `conversao_do_topo_pct`),
  `resumo`, `fontes` (**organizações distintas** que cada fonte devolve — as consultas são
  `DISTINCT` por organização), `lacunas`, `lacunas_declaradas`, `hash_do_relatorio`.
- **HTML** (`funil.html`): página **auto-contida** (uma string, CSS inline, nenhum recurso externo,
  nenhum script) com as barras do funil, a tabela estágio/alcance/conversão, as lacunas declaradas e
  o rodapé de proveniência (card, contrato + sha, ambiente, janela, `hash_do_relatorio`).
- **Determinismo:** a mesma base com o mesmo contrato devolve o **mesmo** `hash_do_relatorio`;
  `gerado_em` é a única diferença e **não** entra no hash.

## 5. Lacunas declaradas (medidas, não escondidas)

- **L1** — o estágio é do Odoo e nenhum componente materializa estágio em coluna do PostgreSQL hoje:
  o que existe é a **trilha**. Derivar da trilha é leitura; virar tabela de negócio exige versão nova
  do contrato de dados (decisão do dono).
- **L2** — evento cujo `entity_id` (e o `payload.organizacao_id`) não casa organização conhecida
  **não é atribuível**: fica em `lacunas.eventos_sem_atribuicao` e **não** entra no estágio. É o caso
  típico de `OPPORTUNITY_WON/LOST`.
- **L3** — o **vocabulário literal** dos estágios do Odoo (`crm.stage`) não está congelado no Data
  Contract V1 — só a ordem conceitual. Rótulo fora da lista declarada cai em lacuna até o vocabulário
  ser fechado (decisão do dono).
- **L4** — `Lost` é terminal sem registro do estágio de origem: uma organização perdida conta como
  tendo alcançado todos os níveis lineares anteriores.
- **L5** — sem janela declarada o funil é o **acumulado** de toda a base; `--desde/--ate` filtram pela
  coluna de tempo de cada fonte; comparação de períodos não está no V1.

## 6. Guardas

| Guarda | Regra | Código |
|---|---|---|
| Ambiente | `dev` exige porta de banco **local** (`docker exec -i pg-<dev\|funil\|analytics\|aceite> psql`); prefixo remoto recusa | 3 (`BANCO_NAO_E_DEV`) |
| Ambiente | `homolog` exige `--confirmo` | 3 (`HOMOLOG_SEM_CONFIRMO`) |
| Ambiente | `prod` recusa por desenho (ADR-005), **antes** de qualquer leitura | **4** |
| Leitura pura | SQL montado é só `SELECT` (auditoria antes de conectar) **e** transação `READ ONLY` no banco | 3 (`ESCRITA_NO_CODIGO`) |
| Segredo | valor de `TRE_FUNIL_TOKEN` presente na evidência recusa a rodada | 5 (`SENHA_VAZADA`) |
| Jargão fechado | estágio sem fonte declarada, fonte sem componente, nível não consecutivo, lateral com nível | 3 |

## 7. O que este card **não** entrega

Conversão por segmento (E02), eficácia dos scores (E03), performance de mensagem (E04), custo de agente
(E05); materialização de estágio em tabela; servir o dashboard por HTTP; leitura de homolog/produção;
qualquer escrita no CRM (o estágio é do Odoo).
