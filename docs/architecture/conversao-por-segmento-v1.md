# Conversão por segmento (`conversao-segmento-v1`) — TRE-W8-E02-T01

**Card:** `TRE-W8-E02-T01` · **Onda:** W8 (Analytics) · **Depende de:** `TRE-W8-E01-T01` (funil)

## 1. O que este componente é

Recorta o **funil já derivado** (`funil-v1`, card W8-E01-T01) por **eixo de segmentação declarado** e
mede a conversão de cada segmento contra a conversão da base inteira.

Artefatos:

| Artefato | Papel |
|---|---|
| `hermes/agentes/analytics/conversao_segmento.py` | o componente |
| `hermes/agentes/analytics/conversao-segmento-v1.json` | o contrato do recorte (eixos, fontes, guardas, lacunas) |
| `scripts/agentes/verificar_conversao_segmento.py` | suíte offline (34 itens + 12 dentes por mutação) |
| `scripts/agentes/teste_conversao_segmento_aceite.sh` | aceite de ponta em PostgreSQL descartável |

## 2. A decisão de arquitetura: o funil é UMA só derivação

O componente **importa `funil.py`** e usa as mesmas funções de contrato, guarda de ambiente, leitura pura,
resolução de evidência, alcance cumulativo, terminal Won/Lost e ramo Nurture. O relatório por segmento é o
**mesmo funil recortado por organização** — não existe segunda implementação de estágio, ordem, nível,
alcance nem atribuição.

Por que: duas cópias da regra de funil divergem em silêncio. O aceite prova a coerência de forma direta —
o recorte da base inteira tem de ser **idêntico** ao relatório do `funil.py` na mesma base (estágios,
alcance, evidência própria, conversões, won/lost/nurture e **as mesmas lacunas**). Se alguém reintroduzir a
regra aqui dentro, esse item reprova.

Corolários herdados: leitura pura em dois `-c` (`SET default_transaction_read_only = on` e depois a
consulta), auditoria que reprova verbo de escrita antes de qualquer conexão, `prod` recusado por desenho,
`homolog` exigindo `--confirmo`, e a lacuna de evento sem atribuição valendo para a base (não recalculada
por segmento).

## 3. Os eixos (vocabulário fechado, nada inventado)

| Eixo | Fonte | Vocabulário | Como o valor nasce |
|---|---|---|---|
| `faixa_funcionarios` | `organizations.employee_band` | `vocabularies.employee_band` do Data Contract V1 (doc 03 §5) | igualdade exata contra o rótulo declarado (mesma normalização do funil) |
| `tier_prioridade` | `scores` (PRIORITY com `score_version`) | `scores.tiers` do Data Contract V1 (doc 03 §4: A+/A/B/C/Nurture) | **derivado** da pontuação vigente aplicada às faixas congeladas |

Regras de borda (todas medidas):

- **Valor ausente** → bucket `SEM_DADO`.
- **Valor fora do vocabulário** → bucket `FORA_DO_VOCABULARIO`; **nunca** vira segmento por semelhança
  (o dente do aceite usa uma faixa quase idêntica, `150_299X`, e prova que ela **não** entra em `150_299`).
- **Pontuação vigente** = maior `calculated_at`, empate pelo maior `score_value`. O dente do aceite dá à
  mesma organização uma pontuação antiga de 95 e uma recente de 70: ela tem de cair em `B`, não em `A+`.
- **Score sem `score_version` não qualifica** (contrato §8) — a organização vai para `SEM_DADO`.
- `UNKNOWN` é valor **declarado** do vocabulário: quem está em `UNKNOWN` aparece como segmento. A diferença
  entre "declaradamente desconhecido" e "sem dado" é preservada de propósito.

## 4. O que o relatório publica

- por eixo: `classificadas`, `cobertura_pct`, `sem_dado`, `fora_do_vocabulario`;
- por segmento: `organizacoes`, `won`, `lost`, `nurture`, `em_aberto`, `taxa_conversao_pct`,
  `taxa_conversao_da_base_pct`, `indice_vs_base_pct`, `amostra_pequena` e os estágios do recorte
  (alcance, evidência própria, conversão da anterior, conversão do topo);
- `global`: o funil da base inteira, com a taxa de conversão global;
- `lacunas` (da base), `fontes`, `lacunas_declaradas`, `contrato` (versão + sha256 do recorte **e** do
  funil) e `hash_do_relatorio`.

Invariantes: a soma dos buckets de um eixo tem de **fechar** com a base (`RECORTE_NAO_FECHA_COM_A_BASE`);
segmento com zero organizações aparece com taxa `null` (não `0`); segmento abaixo de `amostra_minima`
(5) carrega `amostra_pequena: true` — o componente **não** elege vencedor, publica taxa, índice e tamanho
da amostra; `gerado_em` não entra no hash (determinismo).

Saída: `conversao-segmento.json` + `conversao-segmento.html` (auto-contido, sem recurso externo).

## 5. Lacunas declaradas (viajam no relatório)

L1 estágio é do Odoo (via trilha, herdado do funil) · L2 evento sem organização atribuível fica em lacuna
da base · L3 vocabulário literal do `crm.stage` não congelado · L4 Lost é terminal sem estágio de origem ·
L5 sem janela o relatório é o acumulado · **L6 o eixo é a foto de hoje, não história** (mudar faixa/tier
move a organização de bucket retroativamente) · L7 `employee_band` fora do vocabulário congela em
`FORA_DO_VOCABULARIO` até nova versão do contrato de dados · L8 tier só existe com pontuação versionada
vigente (a cobertura do eixo cai quando não existe) · L9 o contrato não persiste tier (a faixa é derivada
na leitura; mudar as faixas muda o relatório — por isso versão e sha256 viajam) · L10 `UNKNOWN` é valor
declarado, não `SEM_DADO`.

## 6. Uso

```bash
# plano e conferência (sem banco)
python3 hermes/agentes/analytics/conversao_segmento.py --ambiente dev --planejar
python3 hermes/agentes/analytics/conversao_segmento.py --ambiente dev --conferir

# relatório em dev (porta de banco LOCAL — prefixo remoto é recusa)
python3 hermes/agentes/analytics/conversao_segmento.py --ambiente dev \
  --porta-banco "docker exec -i pg-analytics-seg-acc psql -U sales_ai -d sales_intelligence" \
  --saida /tmp/seg

# janela (opcional): --desde/--ate em ISO-8601
```

Exit codes: `0` relatório · `2` uso errado · `3` recusa · `4` produção recusada · `5` segredo vazado.

**Nome do container de dev:** a guarda reusada do funil aceita `pg-<sales|funil|analytics|aceite>…`;
por isso o aceite usa `pg-analytics-seg-acc` (um container fora desses prefixos é recusado como
`CONTAINER_NAO_LOCAL_DE_DEV`).
