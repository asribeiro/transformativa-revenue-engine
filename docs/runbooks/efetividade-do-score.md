# Runbook — efetividade do score (`efetividade-score-v1`) — card TRE-W8-E03-T01

**Componente:** `hermes/agentes/analytics/efetividade_score.py` · **Contrato:** `hermes/agentes/analytics/efetividade-score-v1.json`
**Documento de desenho:** `docs/architecture/efetividade-score-v1.md` · **Aceite:** `scripts/agentes/teste_efetividade_score_aceite.sh`
**Suíte offline:** `scripts/agentes/verificar_efetividade_score.py --autoteste`
**Depende de:** `funil.py` / `funil-v1` (card TRE-W8-E01-T01)

---

## 1. Campos do card (doc 11 §2)

| Campo | Definição registrada |
|---|---|
| **ACCEPTANCE** | `ACEITE_EFETIVIDADE_SCORE_OK` — exit 0 com 0 falhas: guardas de ambiente, suíte offline verde, medição conferida **à mão** sobre base semeada (cobertura 11/15, taxa por faixa, endpoints, lift, monotonicidade como achado, adesão à fórmula 9/8/1, quartis por componente, lacunas nomeadas), integração com o pai (os totais por faixa fecham com o resumo do funil e a exportação por organização entrega o mesmo alcance), leitura pura provada por snapshot das 12 tabelas **e** pelo mecanismo `READ ONLY`, determinismo, ausência de PII, dashboard auto-contido **e** regressão do pai (`ACEITE_FUNIL_OK`, 34 itens) |
| **TEST** | `python3 scripts/agentes/verificar_efetividade_score.py --autoteste` (**22 itens + 8 mutações**, cada mutação reprovando um item que o alvo limpo não reprova) e `bash scripts/agentes/teste_efetividade_score_aceite.sh` (PostgreSQL descartável `pg-analytics-efet` na VPS de dev + o aceite do pai) |
| **ROLLBACK** | reverter o commit (arquivos novos + os docs do card, **sem DDL** e sem migration) e remover o container descartável do aceite. A mudança no `funil.py` é **aditiva** (função de alcance + `--por-organizacao`): reverter devolve o pai ao estado `a5d9af3`. Nada em homolog/produção; nenhum serviço, nenhum cron, nenhuma credencial |
| **RISK** | **médio** — leitura pura sobre base de dev, saída com contagem (sem PII), nenhum ato externo. Riscos declarados: (a) **amostra pequena** — taxa por faixa com base insuficiente não sustenta conclusão, por isso `base_suficiente` viaja no relatório (lacuna L3, gatilho do W9); (b) **associação ≠ causa** (lacuna L2); (c) tentação de "consertar" o score dentro de um card de **medição** — fora do escopo: exige versão nova do contrato e aprovação humana (§10/ADR-0004); (d) tocar artefato do card pai — mitigado por refatoração sem mudança de saída + reexecução do aceite do pai |

## 2. Uso

```bash
# conferência sem banco (contrato de dados x dependência do funil)
python3 hermes/agentes/analytics/efetividade_score.py --ambiente dev --conferir
python3 hermes/agentes/analytics/efetividade_score.py --ambiente dev --planejar

# medição no ambiente de dev (base canonica em container local)
python3 hermes/agentes/analytics/efetividade_score.py \
  --ambiente dev \
  --porta-banco "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --saida /tmp/efetividade

# referencia temporal fixa (util para reproduzir a leitura de um dia / conferir validade de score)
python3 hermes/agentes/analytics/efetividade_score.py --ambiente dev \
  --porta-banco "docker exec -i pg-analytics-efet psql -U sales_ai -d sales_intelligence" \
  --agora 2026-10-03T00:00:00Z --saida /tmp/efetividade-hoje
```

Saída: `efetividade-score.json` (relatório) e `efetividade-score.html` (dashboard auto-contido — abre no
navegador sem servidor).

Códigos de saída: `0` ok/plano/conferência · `2` uso errado (janela/referência inválida, ambiente
desconhecido) · `3` recusa (contrato/dependência/fonte/guarda/banco) · `4` produção recusada ·
`5` segredo vazado.

## 3. Leitura do relatório

- `cobertura.taxa_pct` — quanto da base viva tem PRIORITY **válido**. Se cair, olhe as lacunas: sem
  versão, vencido (política de 30 dias do `priority-v1`), fora da escala, ausente.
- `efetividade_por_faixa[].avanco_pct` / `lift_avanco` — taxa de avanço por faixa e o ganho contra a
  taxa-base. `lift` **null** = taxa-base zero (base vazia é base vazia, não lift infinito).
- `resumo.monotonico` + `resumo.violacoes` — **achado**, não erro: a ordem das faixas não separou o
  desfecho. Leia junto de `faixas_com_base_suficiente`: com 2 organizações por faixa, a "violação" pode
  ser ruído de amostra.
- `resumo.base_suficiente_para_conclusao` — `false` significa: **não conclua**. É o gatilho declarado do
  card W9-E01-T01 (`Score calibration`, pré-condição "volume real suficiente").
- `adesao_a_formula.taxa_de_conformidade_pct` + `exemplos_de_divergencia` — o PRIORITY gravado contra a
  fórmula declarada. Divergente é achado sobre o caminho de cálculo (W5-E05), nunca algo para "corrigir"
  aqui.
- `por_componente[].quartis` — Q1 são os **maiores** valores. Se Q1 não for melhor que Q4, aquele
  componente não está separando desfecho.
- `lacunas` — motivo de cada organização fora de alguma conta (`historico_ignorado`, `componente_*`,
  `versoes_divergentes`, `evidencia_orfa`).

## 4. Relação com o funil (o pai)

O desfecho **não** é recalculado aqui: sai de `alcance_por_organizacao` do `funil.py`. Consequências
operacionais:

- mudar o contrato do funil (estágios, fontes, alcance) muda a efetividade — os dois relatórios andam
  juntos, e é assim que deve ser (uma verdade só);
- `funil.py --por-organizacao <arquivo.json>` exporta o alcance por organização (aditivo, fora do hash do
  relatório do funil) para quem precisa auditar organização a organização;
- se `alcance_por_organizacao` desaparecer ou a versão do contrato do pai mudar, o componente **recusa**
  (`DEPENDENCIA_*`) em vez de medir com um desfecho duvidoso.

## 5. Ambiente (ADR-005)

| Ambiente | Estado neste card |
|---|---|
| `dev` | **onde roda**: base canônica em container local, pós-migration 0001 |
| `homolog` | não provisionado; exige `--confirmo` e é passo de operador |
| `prod` | **recusado por desenho** (exit 4), antes de qualquer leitura |

Nenhuma credencial real, nenhum destino externo: o aceite sobe um `postgres:16` descartável
(`pg-analytics-efet`), aplica a migration 0001, semeia a base, mede, roda o aceite do pai e **remove o
container**, deixando os containers do ambiente intactos.

## 6. Lacunas declaradas

As seis lacunas do desenho (`docs/architecture/efetividade-score-v1.md` §8) viajam no próprio relatório
(`lacunas_declaradas`). As duas que mais pesam na operação: **base insuficiente** (não concluir com poucas
organizações por faixa) e **associação ≠ causa** (o relatório mede separação, não efeito).

## 7. Registro de execuções

`docs/operations/registro-de-execucoes.md` (entrada `TRE-W8-E03-T01`) — com os números medidos, o resultado
da regressão do pai e as lacunas declaradas.
