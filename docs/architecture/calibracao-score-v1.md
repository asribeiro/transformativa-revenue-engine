# Calibração do score (`calibracao-score-v1`) — desenho · card TRE-W9-E01-T01

**Componente:** `hermes/agentes/analytics/calibracao_score.py`
**Contrato:** `hermes/agentes/analytics/calibracao-score-v1.json`
**Dependências:** `hermes/agentes/analytics/efetividade_score.py` (`efetividade-score-v1`, card W8-E03-T01)
e, por ele, `hermes/agentes/analytics/funil.py` (`funil-v1`, card W8-E01-T01)
**Aceite:** `scripts/agentes/teste_calibracao_score_aceite.sh` · **Suíte offline:** `scripts/agentes/verificar_calibracao_score.py --autoteste`
**Onda:** W9 (Inteligência Avançada) · **Prioridade:** P3 · **Ambiente:** dev (ADR-005)

---

## 1. A pergunta

O card do plano (doc 11, W9) se chama **Score calibration**, tem prioridade P3 e uma **pré-condição do mundo
real**: *volume real suficiente*. O W8-E03 mediu a efetividade do score e declarou, como achado, que a base
real de hoje não sustenta conclusão (`base_suficiente_para_conclusao: false`) — é o gatilho deste card.
Aqui a pergunta é:

> **os pesos e as faixas em vigor são os que melhor separam o desfecho registrado — e o volume sustenta
> mexer neles?**

**Propor não é aplicar.** Mudar fórmula/peso de score e mudar vocabulário/faixa é decisão estrutural
(Data Contract §10) e exige **versão nova do contrato + aprovação humana registrada** (ADR-0004). Este
componente entrega a **proposta** com número, coorte, margem e validação; quem aplica é operador com
aprovação. No relatório isso é explícito: `proposta.aplicado: false`,
`proposta.exige_versao_nova: true`, `proposta.aprovacao_humana: "pendente"`.

## 2. A pré-condição virou mecanismo (não parágrafo)

| Item do gate | Regra |
|---|---|
| `com_desfecho` ≥ `minimo_de_coorte` (30) | coorte com lastro completo **e** desfecho resolvido |
| `won` ≥ `minimo_por_classe` (5) e `lost` ≥ `minimo_por_classe` (5) | as duas classes existem |
| cada lado do ajuste tem as duas classes | senão não há como ajustar e validar |

Falhou qualquer um: o relatório **sai** (é evidência), a proposta **não existe** e a rodada termina em
**exit 6** (`CALIBRACAO_ABSTEVE_VOLUME`). A pré-condição do card é a única coisa que o código recusa por
si — base pequena não vira peso novo.

## 3. O que o componente não faz

- **Não escreve**: só `SELECT`, em transação `READ ONLY`; a auditoria da fonte reprova verbo de escrita
  antes de qualquer conexão. Nenhuma tabela, coluna, `score_type`, tier, migration ou edição do Data Contract.
- **Não decide a aplicação**: a proposta nasce marcada como não aplicada e pendente de aprovação humana.
- **Não reimplementa o desfecho nem a medição**: o alcance por organização vem do `funil.py` e a leitura,
  a validação de contrato e os extratores vêm do `efetividade_score.py` (W8-E03) — ambos importados e
  conferidos por versão e sha256. Duas verdades para o mesmo número é o defeito que o projeto já pagou caro.
- **Não inventa número**: organização sem PRIORITY válido, sem os quatro componentes na mesma versão ou
  sem desfecho resolvido (Won/Lost) fica **fora** do ajuste, em lacuna nomeada — nunca em zero.
- **Não atribui causalidade**: mede separação no desfecho **registrado**, com coorte e margem declaradas.
- **Não lê PII**: a unidade é a organização (UUID); nenhuma coluna de e-mail, telefone, WhatsApp, CNPJ ou nome.
- **Não publica** em homolog/produção nem monta serviço.

## 4. Modelo

### 4.1 Coorte do ajuste

Organização viva com **PRIORITY válido** (versão preenchida, `valid_until` não vencido na referência,
valor na escala 0–100) **e** os quatro componentes (`ICP`, `AUTOMATION_FIT`, `BUYING_SIGNAL`,
`DATA_QUALITY`) válidos **na mesma `score_version`** do PRIORITY; e **desfecho resolvido** na evidência
própria do funil: rótulo `Won` → 1, `Lost` → 0. Organização em aberto não entra no ajuste e não vira zero.

### 4.2 Pesos: grade determinística do simplexo

- Cada componente com peso múltiplo de 0,05 somando 100 → **1.771 vetores** (passo declarado no contrato).
- Métrica: **AUC por posto** (Mann-Whitney, empate vale 0,5) do score ponderado contra `Won=1/Lost=0`.
  Aritmética **inteira** (peso em pontos percentuais × componente em centésimos) — sem erro de float.
- **Corte ajuste/validação determinístico**: `sha256(organization_id) % 3 < 2` → 2/3 ajuste, 1/3 validação.
  É reproduzível (não é sorteio) e a mesma base dá sempre o mesmo corte.
- **Desempate**: entre vetores de igual AUC no ajuste vence o de **menor distância L1** ao peso em vigor
  (preferir o menor afastamento do que está na rua); persistindo empate, ordem lexicográfica do vetor.
- **Margem na VALIDAÇÃO**: a proposta só nasce se `AUC_validação(candidato) − AUC_validação(incumbente)
  ≥ 0,02`. Ganho só no ajuste **não** propõe — overfitting é declarado, não vendido.

### 4.3 Faixas: cortes de Youden recursivos

Com o score do vetor aprovado (ou o peso em vigor quando não há ganho), os cortes saem do método de
**Youden J = sensibilidade − especificidade**, aplicado **recursivamente no grupo com mais organizações**.
Os limites das faixas saem dos **cortes** (não dos valores observados), o que garante a mesma regra das
faixas vigentes: contíguas, cobrindo **0 a 100 sem lacuna e sem sobreposição**. O componente confere a
cobertura por mecanismo (a soma das organizações das faixas tem de ser igual à coorte com desfecho) e
recusa se não fechar. Monotonicidade (a taxa de vitória não pode **cair** ao subir de faixa) é **achado**,
não erro.

## 5. Evidência medida no aceite (VPS vmi3619453, PostgreSQL descartável)

Base semeada: 40 organizações com desfecho empurrado pelo `DATA_QUALITY` e o `ICP` **anti-correlacionado**
(peso de 0,35 no componente que aponta para o lado errado) — exercita as duas propostas.

| Medida | Em vigor | Proposto |
|---|---|---|
| AUC na validação | **0,000** | **1,000** |
| AUC no ajuste | 0,107 | 0,911 |
| Faixas (taxa de vitória) | Nurture 95% · C 10,5% · B 0% (**2 violações** de monotonicidade) | Nurture 0% · C 10% · B 16,7% · A 80% · A+ 100% (**0 violações**) |
| Pesos | `ICP .35 · AF .30 · BS .25 · DQ .10` | `ICP 0 · AF .30 · BS .25 · DQ .45` (L1 = 70) |

Ganho na validação `+1,000` (margem `0,02`) → `status: PROPOSTA_GERADA`, `aplicado: false`. Base fina
(contrato com `minimo_de_coorte: 41` sobre as mesmas 40 organizações) → **exit 6**, `ABSTEVE` com
`COORTE_COM_DESFECHO_ABAIXO_DO_MINIMO`, `proposta.pesos: null`, `faixas_propostas: []`.

## 6. Autoridade

| Fonte | O que manda aqui |
|---|---|
| `docs/data/data_contract_v1.json#scores` | **dono** dos pesos e das faixas — alvo único da proposta |
| `docs/data/DATA_CONTRACT_V1.md` §8 e §10 | fórmula/tiering vigentes e a governança: mudar peso/fórmula exige **versão nova + aprovação humana registrada** |
| `docs/architecture/efetividade-score-v1.md` (W8-E03) | a **medição** que este card usa como instrumento (e a lacuna L3 — coorte pequena — é o gatilho deste W9) |
| `hermes/agentes/analytics/funil-v1.json` (W8-E01) | o **desfecho**: alcance por organização |
| ADR-0004 / ADR-005 | aprovação humana para mudança estrutural; nada nasce em produção |

## 7. Lacunas declaradas (viajam no relatório)

| # | Lacuna |
|---|---|
| L1 | Coorte **acumulada** (safra): o desfecho é o alcance do funil, que soma a base toda |
| L2 | **Associação não é causa**: o ganho de AUC não estima efeito causal de mexer no score |
| L3 | **Sobreajuste**: com ~30 organizações, 1.771 vetores e 4 classes de desfecho a AUC de validação tem variância alta — a margem reduz, não elimina |
| L4 | A faixa é **derivada** e não persistida: a proposta é aritmética sobre a coorte, não um tier gravado |
| L5 | Componente medido pelo **último** valor (viés de sucessão do score) |
| L6 | `Lost` sem ponto de perda no V1: o alvo é "ganhou × perdeu", não "converteu proposta" |
| L7 | Youden **guloso**: a v1 declara UM caminho determinista, não o ótimo global de particionamento |

## 8. Fronteira

Ampliar o escopo (aplicar a proposta, editar o Data Contract, gravar tier, prever canal/timing/nurture —
W9-E02..E05, memória comercial em Qdrant — W9-E06) exige card próprio, versão nova do contrato e
aprovação humana registrada. Este card **propõe** e prova que não aplicou.
