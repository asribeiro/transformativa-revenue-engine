# Runbook — Score Data Quality v1 (TRE-W5-E04-T01)

Operação do Data Quality Score: medir, repetir, desfazer. O **banco vive na VPS do ambiente**
(ADR-0008); o container do Hermes só orquestra — a porta é o prefixo `psql` do ambiente.

## 1. Antes de começar

- Ambiente permitido: `dev` ou `homolog`. **`prod` é recusado por desenho (exit 4)** — promover é
  card próprio com aprovação humana registrada (ADR-005).
- Sem ambiente declarado **nada é escrito** (fail-closed).
- O score **não cria empresa**: ele mede a que já existe. Empresa que não existe (ou identidade
  ambígua) é `RECUSADA` — quem cria é o Scout (`TRE-W4-E01-T01`).
- Nada de rede e nada de LLM: o caminho é determinístico.
- Guarda-se a `--referencia` usada: é ela que torna a medição reproduzível.

## 2. Medir

```bash
# tudo que está ativo no ambiente (uma medição por organização)
python3 hermes/scores/data_quality/data_quality.py --ambiente dev \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --todas --relatorio /tmp/dq-$(date +%Y%m%d).json

# uma organização (ou várias) por UUID — é assim que se encadeia agente na mesma rodada
python3 hermes/scores/data_quality/data_quality.py --ambiente dev --prefixo "..." \
  --organizacao <uuid> [--organizacao <uuid2>]

# pelos identificadores FORTES do contrato (cnpj -> domain -> linkedin_url)
python3 hermes/scores/data_quality/data_quality.py --ambiente dev --prefixo "..." \
  --fonte hermes/scores/data_quality/exemplos/organizacoes-exemplo.jsonl
```

Modo **sem escrita** (mede, reporta, não grava — bom para conferir antes):

```bash
python3 hermes/scores/data_quality/data_quality.py --todas --planejar --prefixo "..." \
  --referencia 2026-10-02
```

Vereditos possíveis: `ESCRITO` (linha nova + espelho), `JA_EXISTE` (replay idempotente — nada foi
escrito), `RECUSADA` (organização não encontrada / identidade ambígua / sem identificador),
`ERRO` (porta/guarda — nada escrito fora de `agent_runs`).

Exit codes: `0` OK · `1` FALHOU (houve `ERRO`) · `2` uso incorreto · `4` ambiente recusado ·
`5` fonte ilegível.

## 3. O que a rodada escreve

| Tabela | Operação | O que fica |
| --- | --- | --- |
| `sales_intelligence.scores` | `INSERT` | a medição: `score_type='DATA_QUALITY'`, `score_version='v1.0'`, `score_value`, `inputs`, `explanation` |
| `sales_intelligence.organizations` | `UPDATE` (só `data_quality_score`) | o valor **atual**; `updated_at` **não** é tocado |
| `sales_intelligence.agent_runs` | `INSERT` | uma linha por organização processada, com `veredito`, `valor` e `score_id` |

Conferir depois da rodada:

```sql
-- histórico da empresa (mais novo primeiro)
SELECT score_value, score_version, calculated_at, explanation->>'inputs_sha256' AS hash
FROM sales_intelligence.scores
WHERE organization_id = '<uuid>' AND score_type = 'DATA_QUALITY' AND score_version = 'v1.0'
ORDER BY calculated_at DESC;

-- espelho × último score (tem de bater)
SELECT o.data_quality_score, s.score_value
FROM sales_intelligence.organizations o
LEFT JOIN LATERAL (SELECT score_value FROM sales_intelligence.scores s
                   WHERE s.organization_id = o.id AND s.score_type='DATA_QUALITY'
                   ORDER BY s.calculated_at DESC LIMIT 1) s ON TRUE
WHERE o.id = '<uuid>';
```

## 4. Repetir (replay)

Rodar o mesmo lote de novo com o banco igual devolve `JA_EXISTE` e **não** cria linha: é a guarda
de idempotência dentro do SQL (`NOT EXISTS` sobre o último `DATA_QUALITY/v1.0` da empresa com o
mesmo `inputs_sha256`). Mudar **qualquer** dado medido (ou a `--referencia`) gera medição nova —
o histórico é append-only e nunca é reescrito.

## 5. Desfazer

```bash
# 1) frio: mostra o que apagaria (nada é apagado)
python3 hermes/scores/data_quality/data_quality.py --ambiente dev --prefixo "..." \
  --desfazer <correlation_id>

# 2) aplicando: apaga as linhas da rodada e devolve o espelho ao valor anterior
python3 hermes/scores/data_quality/data_quality.py --ambiente dev --prefixo "..." \
  --desfazer <correlation_id> --confirmo
```

- A restauração do espelho só acontece quando o valor atual é **exatamente** o valor escrito pela
  rodada: nota mais nova (de outra rodada) **não** é sobrescrita.
- `agent_runs` é auditoria e **fica**.
- O valor anterior viaja dentro da própria linha (`explanation.valor_anterior`); quando não havia
  score, a restauração devolve `NULL`.

## 6. Quando algo falha

| Sintoma | Causa provável | O que fazer |
| --- | --- | --- |
| `RECUSADO_AMBIENTE` (exit 4) | `--ambiente prod` ou ambiente ausente | declarar `dev`/`homolog`; produção é card próprio |
| `FALHOU porta psql falhou` | prefixo errado / container do ambiente fora | conferir `docker ps` do ambiente e o prefixo `psql` |
| `RECUSADA ORGANIZACAO_NAO_ENCONTRADA` | empresa não existe neste ambiente | rodar o Scout/enriquecimento antes (a W4 produz o dado) |
| `RECUSADA IDENTIDADE_AMBIGUA` | mais de uma empresa casou com os fortes | é caso de **dedup**, não do score: resolver `TRE-W1-E04-*` |
| valor suspeito de baixo | falta pesquisa (`research_runs` sem `COMPLETED`) ou fonte fora do vocabulário | conferir a coluna `source` e os `research_runs` da empresa: sem lastro a `confiabilidade` é 0 **por desenho** |
| nota não muda entre rodadas | dado igual ⇒ `JA_EXISTE` | é o esperado; forçar medição nova é `--referencia` de outro dia |
| `GuardaDeEscritaViolada` | SQL tentando escrever fora do contrato | não afrouxar a guarda: é ela que separa "medir" de "consertar o dado" |

## 7. Verificação e portão

```bash
python3 scripts/scores/verificar_score_data_quality.py --autoteste   # suíte + dentes
bash scripts/scores/teste_data_quality_aceite.sh --prova-de-dente   # aceite real (VPS)
bash scripts/verificar_estrutura.sh                                  # artefatos versionados
```

Nada aqui homologa e nada aqui promove: `prod` é recusado por desenho e a sequência
dev → homologação → produção do ADR-005 é decisão do Anderson.
