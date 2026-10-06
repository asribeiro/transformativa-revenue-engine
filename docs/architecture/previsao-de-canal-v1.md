# Previsão do melhor canal (`previsao-canal-v1`) — TRE-W9-E03-T01 (W9 / Inteligência Avançada)

**Card:** `TRE-W9-E03-T01` (Best channel prediction) · **Onda:** W9 · **Prioridade:** P3 · **Owner:** Hermes (dev harness)
**Componente:** `hermes/agentes/analytics/previsao_canal.py` · **Contrato:** `hermes/agentes/analytics/previsao-canal-v1.json`
**Dependência:** `hermes/agentes/analytics/funil.py` (`funil-v1`, card W8-E01-T01) · **Pré-condição:** `dados multicanal`

---

## 1. O que o card entrega

O doc 11 declara o card com a pré-condição `dados multicanal` e **não detalha** ACCEPTANCE/TEST/ROLLBACK/RISK
(a seção 2 do doc 11 exige os quatro campos). Os quatro foram definidos no início da execução e registrados no
card (§7). Este documento descreve o que foi construído.

O componente **mede** a efetividade histórica de cada canal declarado e, quando a base sustenta, **prevê** o
melhor canal por organização:

| Pergunta | Saída |
|---|---|
| A base sustenta a previsão? | `pre_condicao_dados_multicanal` (`atendida`, `canais_suficientes`, `organizacoes_com_interacao`, `faltando`) |
| Qual canal é mais efetivo? | `por_canal` (abordadas, outbound, respostas, taxa de resposta, avanço no endpoint, lift, Won/Lost, `base_suficiente`) + `ranking_de_canais` |
| Que canal usar nesta organização? | `previsoes` (canal previsto, taxa, amostra, desempate declarado, canais bloqueados) |

O que ele **não** faz: não envia nada, não cria recomendação, não toca o Odoo, não grava linha nem coluna, não
recalibra score e não decide o melhor **momento** (W9-E04-T01) nem automatiza nurture (W9-E05-T01).

## 2. Pré-condição `dados multicanal` — a forma testável

Condição do mundo real não vira card (regra do board), então ela virou **mínimo declarado no contrato** e
**medida a cada rodada**:

| Parâmetro | Valor v1 | Por quê |
|---|---|---|
| `minimo_canais_com_base` | 2 | "melhor canal" só existe com ≥ 2 candidatos elegíveis |
| `minimo_organizacoes_por_canal` | 3 | canal com 1–2 organizações é anedota, não efetividade (`base_suficiente=false`) |
| `minimo_organizacoes_com_evidencia` | 4 | coorte mínima para a taxa-base ter significado |

Abaixo de qualquer um deles: **`previsao_emitida=false`, `previsoes=[]` e a lista `faltando` com o número
exigido e o obtido**. O relatório sai (exit 0) porque medir a insuficiência **é** o resultado — prever com base
de brinquedo é que seria o defeito. A medição por canal continua publicada (é ela que mostra o que falta).

## 3. Vocabulário de canal (lacuna declarada L1)

O Data Contract V1 declara `interactions.channel VARCHAR(30)` mas **não congela o vocabulário de valores**. O
contrato deste componente **declara** os canais que o projeto já grava e o **bloqueio** de cada um:

| Canal | Bloqueio | Evidência de que o projeto grava esse canal |
|---|---|---|
| `EMAIL` | `opt_out_email` | `hermes/agents/outreach/politica-envio-v1.json` (`channel=EMAIL`), `hermes/scores/nba/nba.py` |
| `WHATSAPP` | `opt_out_whatsapp` | card TRE-W7-E05-T01; coluna `opt_out_whatsapp` existe no schema |
| `LINKEDIN` | `do_not_contact` | `hermes/scores/nba/politica-nba-v1.json` (`canal_de_linkedin`), `politica-outreach-v1.json` |

O código **não carrega canal literal**: lê a lista e o bloqueio do contrato a cada rodada. Valor gravado fora da
lista vai para lacuna (`canal_fora_do_vocabulario`, nome mascarado) e **nunca** é mapeado por semelhança.
Fechar o vocabulário no contrato de dados é decisão do dono — declarado, não fingido.

## 4. Compliance: opt-out é bloqueio, não preferência

Contrato §9: *"não contatar opt-out — `do_not_contact`/`opt_out_*` são bloqueio, não preferência"*.

- `do_not_contact` em qualquer contato da organização → **todos** os canais bloqueados (organização sem previsão);
- `opt_out_email` → `EMAIL` bloqueado; `opt_out_whatsapp` → `WHATSAPP` bloqueado;
- `LINKEDIN` não tem coluna de opt-out no schema: o bloqueio vem de `do_not_contact` e a **ausência é declarada**
  (lacuna L3) — não presumida;
- `preferred_channel` **não** é permissão: é apenas o 4º critério de desempate, aplicado só entre canais já
  elegíveis e não bloqueados.

O canal bloqueado **nunca** aparece como previsto: aparece em `canais_bloqueados` com o motivo, e o motivo é
contado em `lacunas` (`canal_bloqueado_do_not_contact`, `canal_bloqueado_por_opt_out`).

## 5. Efetividade e previsão

**Desfecho** (não reimplementado): `alcance_por_organizacao` do `funil.py`, importado e conferido por versão
(`funil-v1`) e por sha256 — a mesma função que o relatório do funil usa. Duplicar o alcance criaria duas
verdades para o mesmo número, defeito que este projeto já pagou caro para aprender.

**Métricas por canal** (unidade = organização, UUID canônico):

- `organizacoes_abordadas` — organizações com `interactions.direction='OUTBOUND'` naquele canal;
- `interacoes_outbound`, `respostas_inbound` (INBOUND com `response_category`), `taxa_de_resposta_pct`;
- `avanco_pct` — organizações abordadas que atingiram o endpoint principal (`Reunião`, nível 6: primeiro
  estágio em que o fato é do Odoo) / abordadas;
- `lift_avanco` — `avanco_pct / taxa_base`, onde taxa-base é o avanço da **união** das organizações abordadas
  por qualquer canal declarado (canal com taxa-base zero viaja com `lift: null`);
- `ganharam` / `perderam` pelos rótulos `Won`/`Lost` da trilha;
- `base_suficiente` — `organizacoes_abordadas >= minimo_organizacoes_por_canal`.

**Previsão por organização** — entre os canais **elegíveis** (base suficiente) e **não bloqueados**, o de maior
taxa de avanço, com ordem de desempate **declarada** (sem heurística escondida):

1. maior taxa de avanço do canal (`taxa_de_avanco`);
2. resposta própria da organização naquele canal — INBOUND com `response_category` (`resposta_propria`);
3. interação própria da organização naquele canal (`interacao_propria`);
4. `preferred_channel` do contato (`preferencia_declarada`);
5. ordem do vocabulário do contrato (`ordem_do_vocabulario`).

O motivo do desempate viaja em cada previsão (`empate_desfeito_por`), junto da amostra do canal
(`amostra_do_canal`) — a previsão nunca aparece sem o tamanho da base que a sustenta.

## 6. Invariantes (cada um com item de suíte/aceite)

1. **Leitura pura** — toda consulta roda com `default_transaction_read_only = on` em **dois** `-c` (medido no
   W8-E01-T01: em um único `-c` o `SET` não vale) e a auditoria da fonte reprova verbo de escrita **antes** de
   qualquer conexão (`ESCRITA_NO_CODIGO`, exit 3).
2. **Privacidade** — de `contacts` só entram **contagens** de bloqueio e `preferred_channel`; nenhuma coluna de
   e-mail, telefone, WhatsApp, nome, CNPJ ou domínio é selecionada. A saída carrega contagem + UUID da
   organização.
3. **Fail-closed** — canal fora do vocabulário, organização sem canal elegível, base insuficiente e pré-condição
   não atendida **vão para lacuna nomeada**, nunca para chute.
4. **Determinismo** — mesma base + mesmo contrato + mesma referência temporal ⇒ mesmo `hash_do_relatorio`;
   `gerado_em` e `referencia_temporal` não entram no hash.
5. **Guardas de ambiente (ADR-005)** — `dev` exige porta de banco **local** (`docker exec -i pg-<...> psql`);
   prefixo remoto RECUSA (`BANCO_NAO_E_DEV`); `homolog` exige `--confirmo`; `prod` RECUSA por desenho (exit 4),
   antes de ler contrato.
6. **Segredo** — valor de `TRE_CANAL_TOKEN`/`TRE_PREVISAO_CANAL_TOKEN`/`TRE_FUNIL_TOKEN` na evidência recusa a
   rodada (exit 5).

## 7. Saída

Relatório **JSON** + **HTML auto-contido** (uma string, CSS inline, nenhum recurso externo, nenhum script).
Exit codes: `0` relatório/plano/conferência · `2` uso errado (janela inválida) · `3` recusa · `4` produção
recusada · `5` segredo vazado.

## 8. Lacunas declaradas (viajam no relatório)

- **L1** vocabulário de `interactions.channel` não congelado no Data Contract — declarado aqui (ver §3);
- **L2** associação não é causa: canal mais efetivo pode refletir perfil, momento ou mensagem — a v1 mede
  separação com `base_suficiente`, não efeito causal;
- **L3** `LINKEDIN` sem coluna de opt-out própria — bloqueio apoiado em `do_not_contact`;
- **L4** previsão é **prior de canal da coorte**, não personalização por contato (amostra por organização é
  pequena; desempate usa resposta própria e preferência declarada, nada mais);
- **L5** coorte acumulada (L5 do funil): a janela filtra cada fonte, mas comparação entre safras não existe na v1;
- **L6** medir canal não é medir mensagem: `response_category` diz que houve resposta classificada, não a
  qualidade dela (performance de mensagem é W8-E04-T01);
- **L7** a previsão não é ato: nenhum envio e nenhuma linha gravada; virar `recommendations` exige versão nova do
  contrato de dados + approval (contrato §10 / ADR-0004).

## 9. Campos exigidos pela seção 2 do doc 11

Registrados no card no início da execução (comentário do card):

- **ACCEPTANCE:** `ACEITE_PREVISAO_CANAL_OK` — medido, ver `docs/kanban/criterios-de-aceitacao.md`;
- **TEST:** `scripts/agentes/verificar_previsao_canal.py --autoteste` (suíte offline + dentes) e
  `scripts/agentes/teste_previsao_canal_aceite.sh` (PostgreSQL descartável na VPS de dev);
- **ROLLBACK:** reverter o commit (arquivos novos + docs, **sem DDL**, sem migration, sem cron, sem credencial) e
  remover o container descartável; nada em homolog/produção; nenhum artefato de card anterior é alterado;
- **RISK:** médio — leitura pura em dev, saída com contagem (sem PII), nenhum ato externo; riscos declarados em §8.
