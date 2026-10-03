# Runbook — pontuacao preditiva do score (TRE-W9-E02-T01)

Transforma o score ordinal de PRIORITY em **probabilidade de ganho** e mede essa previsao fora da
amostra. **Nao aplica nada**: o numero nasce no relatorio, marcado `aplicado: false`.

## Pre-requisitos

- Ambiente de **dev** com PostgreSQL local alcancavel por `docker exec -i pg-<nome> psql ...`
  (nunca prefixo remoto: `BANCO_NAO_E_DEV`). `prod` e' recusa por desenho (ADR-005).
- O **relatorio da calibracao** do card W9-E01-T01 (JSON), gerado na MESMA base e com a mesma
  `--agora`: e' dele que vem o peso em uso e a particao ajuste/validacao.
- Migrations aplicadas e base com `PRIORITY`, os quatro componentes e desfecho (Won/Lost).

## Passo a passo

```bash
# 1. calibracao (dependencia): gera o relatorio na mesma base
python3 hermes/agentes/analytics/calibracao_score.py --ambiente dev \
  --porta-banco "docker exec -i pg-analytics-pred psql -U sales_ai -d sales_intelligence" \
  --saida /tmp/rel --agora 2026-10-03T00:00:00Z

# 2. conferencia sem banco (contrato, dependencia, guardas)
python3 hermes/agentes/analytics/pontuacao_preditiva.py --ambiente dev --conferir \
  --calibracao /tmp/rel/calibracao-score.json

# 3. planejamento declarado (sem banco)
python3 hermes/agentes/analytics/pontuacao_preditiva.py --ambiente dev --planejar

# 4. rodada real
python3 hermes/agentes/analytics/pontuacao_preditiva.py --ambiente dev \
  --porta-banco "docker exec -i pg-analytics-pred psql -U sales_ai -d sales_intelligence" \
  --calibracao /tmp/rel/calibracao-score.json --saida /tmp/rel --agora 2026-10-03T00:00:00Z
```

Saida: `pontuacao-preditiva.json` + `pontuacao-preditiva.html` (auto-contido, sem recurso externo).

## Leitura do relatorio

| Campo | O que diz |
| --- | --- |
| `gate_de_volume.base_suficiente` | `false` => ABSTEVE (exit 6): sem `modelo`, sem `avaliacao`, sem previsao |
| `calibracao.origem_dos_pesos` | `proposta_de_calibracao` ou `contrato_em_vigor` |
| `modelo.blocos` | curva final (PAVA): probabilidade por bloco de binos, monotona |
| `modelo.bins_sem_base` | quantos binos nao tem organizacao no ajuste |
| `avaliacao.validacao` | AUC, Brier, `brier_skill` (contra a taxa-base), log-loss, cobertura |
| `confiabilidade[]` | previsto x observado por bino e o desvio |
| `previsao_por_organizacao.itens` | UUID, score, bino, faixa e probabilidade das organizacoes em aberto |

## Diagnostico rapido

- `RECUSA DEPENDENCIA_CALIBRACAO` → falta `--calibracao`, ou o JSON e' de outro card/sem hash.
- `RECUSA CORTE_DIVERGENTE` → o relatorio da calibracao foi gerado com outra particao; regere ambos
  com o mesmo contrato.
- `RECUSA BANCO_NAO_E_DEV` → o prefixo da porta de banco nao e' `docker exec -i pg-<nome> psql`.
- `exit 6` → base insuficiente (coorte com desfecho < 30, ou lado sem as duas classes). Nao e'
  defeito: e' a pre-condicao do card funcionando. Amplie a base de desfecho ou baixe o minimo SO'
  com versao nova do contrato.
- `brier_skill <= 0` → a curva nao bate a taxa-base: leia `confiabilidade` antes de usar o numero.

## O que NAO fazer

- Nao gravar a probabilidade no banco nem criar coluna/`score_type`: exige versao nova do Data
  Contract (§10) + aprovacao humana registrada (ADR-0004).
- Nao rodar em homolog/producao para "ver como fica": `prod` recusa (exit 4) e `homolog` exige
  `--confirmo` e passo de operador.
- Nao tratar o numero como efeito causal de mexer no score (lacuna L2).
