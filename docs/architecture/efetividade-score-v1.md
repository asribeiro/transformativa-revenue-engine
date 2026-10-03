# Efetividade do score (`efetividade-score-v1`) — desenho · card TRE-W8-E03-T01

**Componente:** `hermes/agentes/analytics/efetividade_score.py`
**Contrato:** `hermes/agentes/analytics/efetividade-score-v1.json`
**Dependência:** `hermes/agentes/analytics/funil.py` (`funil-v1`, card TRE-W8-E01-T01)
**Aceite:** `scripts/agentes/teste_efetividade_score_aceite.sh` · **Suíte offline:** `scripts/agentes/verificar_efetividade_score.py --autoteste`
**Onda:** W8 (Analytics) · **Prioridade:** P2 · **Ambiente:** dev (ADR-005)

---

## 1. A pergunta

O card do plano (doc 11, W8) se chama **Score effectiveness** e não detalha nada além do título, da
prioridade (P2) e da dependência (W8-E01-T01). Este documento registra o que o card responde:

> **o score está funcionando?** — as faixas separam desfecho comercial, o número armazenado bate com a
> fórmula declarada e o score cobre a base?

**Medir não é recalibrar.** `TRE-W9-E01-T01 — Score calibration` (P3, pré-condição "volume real
suficiente") é o card de mexer em peso; mudar fórmula/peso exige **versão nova do contrato** e aprovação
humana registrada (Data Contract §10 + ADR-0004). Aqui nenhum peso é proposto: os pesos são **lidos** do
Data Contract e apenas conferidos contra o que está gravado. É por isso que este card é o **instrumento**
do W9, e não o W9.

Quatro medidas, cada uma com número próprio e lacuna nomeada quando não há dado:

| # | Medida | Pergunta que responde |
|---|---|---|
| 1 | **Cobertura** | quantas organizações vivas têm PRIORITY **válido** (versão + validade + escala); o que ficou de fora e por quê |
| 2 | **Efetividade por faixa** | por faixa (`A+ A B C Nurture`), taxa de avanço no funil, Won/Lost, taxa de vitória e **lift** contra a taxa-base; a ordem das faixas separa? |
| 3 | **Adesão à fórmula** | o PRIORITY **armazenado** bate com `0.35·ICP + 0.30·AF + 0.25·BS + 0.10·DQ` na mesma versão? |
| 4 | **Efetividade por componente** | ICP, AUTOMATION_FIT, BUYING_SIGNAL e DATA_QUALITY separam desfecho (quartis por posto)? |

## 2. Autoridade

| Fonte | O que manda aqui |
|---|---|
| `docs/data/data_contract_v1.json#scores` | **dono das faixas** (`tiers`), **dono dos pesos** (`priority_weights`), tipos de score e a exigência de versão (`requires_version_column`) |
| `docs/data/DATA_CONTRACT_V1.md` §8 e §10 | o tier não é um sexto `score_type`; score é **histórico**; mudança de fórmula/peso = versão nova |
| `03_ARQUITETURA_DE_NEGOCIO.md` §3–§4 | fórmula V1, tiering, conceito de score |
| `hermes/agentes/analytics/funil-v1.json` (`funil-v1`) | **desfecho**: alcance por organização, estágios e a trilha Odoo → PostgreSQL |
| `hermes/scores/priority/score-priority-v1.json` (`priority-v1`) | validade de 30 dias do score; componente ausente/vencido não sustenta prioridade |
| `hermes/scores/tiering/score-tiering-v1.json` (`tiering-v1`) | mesma leitura das faixas; PRIORITY vencido não classifica |
| ADR-005 / ADR-0004 | nada nasce em produção; mudança estrutural exige aprovação humana registrada |

**Precedência em conflito:** este componente **não decide** faixa nem peso — ele **recusa** (`exit 3`)
quando o contrato de dados está incoerente (peso que não soma 1,00; faixa com lacuna ou sobreposição;
componente fora de `scores.types`), antes de ler o banco.

## 3. O que o componente não faz

- Não escreve: **só `SELECT`**, em transação `READ ONLY`; não cria tabela, coluna, `score_type` nem tier.
- Não recalibra nem propõe peso (W9).
- Não lê PII: não seleciona e-mail, telefone, WhatsApp, CNPJ ou nome — a unidade é a organização (UUID).
- Não reimplementa o funil: o desfecho sai de `alcance_por_organizacao` do `funil.py` (import), e o
  sha256 do contrato do pai viaja no relatório.
- Não inventa faixa: organização sem PRIORITY válido vai para **lacuna nomeada**.
- Não faz atribuição causal: mede **associação declarada por faixa**, com base declarada.
- Não publica em homolog/produção nem monta serviço.

## 4. Modelo

### 4.1 Coorte

Organização **viva** (`deleted_at IS NULL`) com PRIORITY **válido**: `score_version` preenchida +
`valid_until` não vencido na referência temporal + valor dentro da escala do Data Contract (0–100).
O PRIORITY usado é o **mais recente** (`ORDER BY calculated_at DESC, id DESC`) — mesmo recorte do
`tiering`; as linhas antigas ficam em `lacunas.historico_ignorado` (score é histórico, não mutável).

### 4.2 Faixa

`tier = faixa do Data Contract que contém o valor` (`min ≤ valor ≤ max`), com a cobertura da escala
conferida a cada rodada (lacuna ou sobreposição **recusa** o contrato). A faixa é **derivada**, nunca
persistida: não existe coluna de tier no schema, e criar uma exige versão nova do contrato (lacuna **L4**).

### 4.3 Desfecho e endpoints

O desfecho é o **alcance** da organização no funil (as mesmas fontes do funil, as mesmas regras: alcance
cumulativo, terminal separado, `Nurture` lateral). Endpoints declarados no contrato:

| Endpoint | Regra | Por quê |
|---|---|---|
| **Reunião** *(principal)* | nível linear ≥ 6 | primeiro estágio cujo **fato é do Odoo** (contrato §7.1): primeiro marco comercial real, não um sinal do próprio motor de score |
| Engajamento | nível ≥ 5 | resposta classificada (W6-E05) |
| Proposta | nível ≥ 8 | estágio avançado da trilha |
| Won | rótulo `Won` | desfecho terminal |

`avanco_pct` = atingiram / organizações da faixa; `lift` = taxa da faixa / taxa-base da coorte (o ganho de
usar a faixa em vez de tratar todo mundo igual); denominador zero devolve `null`.

### 4.4 Monotonicidade e base suficiente

Na ordem das faixas (`A+ … Nurture`, a ordem das **melhores** para as piores), a taxa de avanço **não deve
subir** ao descer de faixa. Violação **não é recusa**: é **achado medido**, com os dois números nomeados —
medir um score que não separa é resultado legítimo. Toda faixa viaja com `base_suficiente` (mínimo de 5
organizações, declarado); faixa abaixo do mínimo **não sustenta conclusão** e o relatório diz isso
(`base_suficiente_para_conclusao`).

### 4.5 Adesão à fórmula

Para cada organização da coorte com os **quatro** componentes na **mesma `score_version`** do PRIORITY,
não vencidos e dentro da escala: recalcula `SUM(peso × componente)` com os pesos do Data Contract,
arredonda `ROUND_HALF_UP` em 2 casas (escala `NUMERIC(5,2)`) e compara com o valor armazenado na
tolerância declarada (0,01). Motivo de exclusão vira lacuna nomeada (`componente_ausente`,
`componente_sem_versao`, `componente_vencido`, `componente_fora_da_escala`, `versoes_divergentes`) — nada
é estimado. **Divergência é achado**, com desvio máximo/médio e até 5 exemplos (organization_id, faixa,
valores, versão): corrigir é ato de outro card.

### 4.6 Quartis por componente

Por componente, as organizações elegíveis são ordenadas por **posto** (valor decrescente, empate desfeito
por `organization_id`) e cortadas em 4 grupos (o maior primeiro). Q1 são os **maiores** valores. O que se
mede é a taxa do endpoint principal em Q1 contra Q4 e o lift — se o componente não separa, aparece.

## 5. Guardas

| Guarda | Mecanismo |
|---|---|
| Ambiente (ADR-005) | `dev` exige porta de banco **local** (`docker exec -i pg-<sales\|funil\|analytics\|aceite>… psql`); prefixo remoto **recusa** (`BANCO_NAO_E_DEV`); `homolog` exige `--confirmo`; `prod` **recusa por desenho** (exit 4) **antes** de ler contrato ou base |
| Leitura pura | toda consulta roda com `default_transaction_read_only = on` em **dois** `-c` (o defeito medido no W8-E01-T01: `SET` e `SELECT` num único `-c` **não** valem) **e** a auditoria da fonte reprova verbo de escrita antes de qualquer conexão (`ESCRITA_NO_CODIGO`, exit 3) |
| Contrato | peso que não soma 1,00, faixa com lacuna/sobreposição, componente fora de `scores.types` ou divergência com as fontes do funil **recusam** antes do banco |
| Dependência | funil ausente, versão diferente de `funil-v1` ou sem `alcance_por_organizacao` → **recusa** (`DEPENDENCIA_*`): o alcance não é recalculado por conta própria |
| Segredo | valor de `TRE_EFETIVIDADE_TOKEN`, `TRE_EFETIVIDADE_SCORE_TOKEN` ou `TRE_FUNIL_TOKEN` presente na evidência → recusa (exit 5) |
| Privacidade | saída com contagem e UUID canônico; nenhuma coluna de PII é lida |

Exit codes: `0` ok/plano/conferência · `2` uso errado · `3` recusa · `4` produção recusada · `5` segredo vazado.

## 6. Extensão do pai (card W8-E01-T01)

Medir efetividade exige o desfecho **por organização**; o relatório do funil é agregado. Em vez de
reimplementar o alcance (duas verdades para o mesmo número — o defeito que este projeto já pagou caro
para aprender), o `funil.py` ganhou:

- `propria_por_estagio(contrato, organizacoes, evidencia)` e
  `alcance_por_organizacao(contrato, organizacoes, evidencia)` — a **mesma** regra que o relatório já usa,
  extraída de `calcular_funil` (refatoração **sem mudança de saída**);
- `--por-organizacao <arquivo.json>` — exportação **aditiva**: fica **fora** do relatório e do
  `hash_do_relatorio` (não muda `funil-v1`).

**Prova de que o pai não mudou:** o aceite deste card roda o aceite do pai (`teste_funil_aceite.sh`,
34 itens) com o `funil.py` estendido exige `ACEITE_FUNIL_OK`; a suíte offline do pai também passa
(24 itens + 8 dentes).

## 7. Saída

`efetividade-score.json` (relatório) e `efetividade-score.html` (dashboard auto-contido, sem recurso
externo e sem servidor). O JSON carrega: `versao`, `card`, `ambiente`, `janela`, `referencia_temporal`,
`contrato` (versão + sha256), `dependencia` (função + contrato do pai + sha256), `base` (organizações +
digest), `parametros`, `cobertura`, `efetividade_por_faixa`, `resumo`, `adesao_a_formula`, `por_componente`,
`lacunas`, `lacunas_declaradas`, `fontes` e `hash_do_relatorio`.

**Determinismo:** a mesma base, o mesmo contrato, a mesma janela e a mesma referência temporal produzem o
**mesmo** relatório; `gerado_em` e `referencia_temporal` são a única diferença e **não** entram no hash.

## 8. Lacunas declaradas

| # | Lacuna |
|---|---|
| L1 | O desfecho é o alcance **acumulado** de toda a base (L5 do funil): a efetividade medida é de coorte acumulada, não de safra. A janela filtra pela coluna de tempo de cada fonte, mas comparação entre safras não está na v1 |
| L2 | **Associação não é causa**: faixa alta pode coincidir com desfecho por tamanho de amostra, setor ou canal. A v1 mede separação com base declarada e **não** estima efeito causal |
| L3 | **Coorte pequena**: com a base real de hoje várias faixas ficam abaixo do mínimo de 5 organizações — as taxas viajam com `base_suficiente: false` e a conclusão **não** está sustentada (é o gatilho declarado do W9-E01-T01) |
| L4 | A faixa é **derivada**, não persistida (não existe coluna de tier; criar exige versão nova do contrato). Se o Odoo calcular tier com faixa diferente, a divergência só aparece quando o vocabulário do tier for congelado |
| L5 | Componente medido pelo **último** valor por `score_type`: um ICP recalculado **depois** da abordagem é leitura do presente sobre um desfecho do passado (viés de sucessão de score) — declarado, não corrigido |
| L6 | "Avanço" usa o nível linear alcançado e `Won` por rótulo; `Lost` não tem ponto de perda registrado no V1 (lacuna L4 do funil), então `taxa_de_vitoria_pct` é `won/(won+lost)` e **não** "conversão de propostas" |

As seis viajam no próprio relatório (`lacunas_declaradas`).
