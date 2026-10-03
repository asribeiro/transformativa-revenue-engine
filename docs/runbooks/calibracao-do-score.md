# Runbook — Calibração do score (`calibracao-score-v1`)

Card: **TRE-W9-E01-T01** · Onda W9 · Ambiente: **dev** (ADR-005) · Componente:
`hermes/agentes/analytics/calibracao_score.py`

## 1. Quando rodar

1. Depois de medir a efetividade do score (`docs/runbooks/efetividade-do-score.md`) e receber
   `resumo.base_suficiente_para_conclusao: false` — **não** rode esperando proposta: o gate vai abster.
2. Quando houver base real com desfecho resolvido (Won/Lost) suficiente: é a pré-condição declarada
   do card ("volume real suficiente"). O gate mede, não estima.
3. A proposta NÃO é aplicada por este componente. Aplicar (contrato novo + migration/verificação +
   aprovação registrada) é ato de operador.

## 2. Comandos

```bash
# sem banco: valida contratos e dependências (contrato, instrumento W8-E03, funil W8-E01)
python3 hermes/agentes/analytics/calibracao_score.py --ambiente dev --conferir

# sem banco: declara gate, grade, pesos em vigor e faixas
python3 hermes/agentes/analytics/calibracao_score.py --ambiente dev --planejar

# com banco (dev): porta de banco LOCAL obrigatória
python3 hermes/agentes/analytics/calibracao_score.py --ambiente dev \
  --porta-banco "docker exec -i pg-analytics-calib psql -U sales_ai -d sales_intelligence" \
  --agora 2026-10-03T00:00:00Z --saida /tmp/calibracao

# homolog (exige --confirmo) · prod é RECUSADO por desenho (exit 4)
python3 hermes/agentes/analytics/calibracao_score.py --ambiente homolog --confirmo --porta-banco "..."
```

Códigos de saída: `0` relatório gerado (com ou sem proposta) · `2` uso errado · `3` recusa
(contrato/dependência/fonte/guarda/banco) · `4` produção recusada · `5` segredo vazado ·
**`6` volume insuficiente (absteve)**.

## 3. Leitura do relatório

- `gate_de_volume.base_suficiente` — `false`: **não houve proposta** e não há o que ler adiante.
  `motivos` nomeia cada item (coorte, classe Won, classe Lost, classe ausente em um lado).
- `incumbente.validacao.auc` x `proposta.pesos.validacao.auc` — o ganho que a proposta entrega **fora**
  da amostra do ajuste. `aprovada: false` significa: nenhum vetor da grade ganhou a margem (0,02) na
  validação; o peso em vigor fica como está (não é defeito, é o resultado).
- `proposta.pesos.vetor` + `distancia_l1_ao_peso_em_vigor` — o vetor escolhido e o quanto ele anda em
  relação ao que está na rua. Empate de AUC é desfeito pelo **menor** afastamento.
- `incumbente.faixas_vigentes` x `faixas_propostas` — antes/depois por faixa, com `taxa_de_vitoria_pct`.
  `violacoes_de_monotonicidade`: taxa de vitória **caindo** ao subir de faixa é **achado**, e é o sintoma
  clássico de faixa mal calibrada (foi o caso da base medida no aceite: Nurture 95% > C 10,5%).
- `lacunas` — motivo de cada organização fora do ajuste (`sem_desfecho`, `componente_*`,
  `versoes_divergentes`, `prioridade_*`, `faixas_nao_propostas`).

## 4. A proposta NÃO se aplica sozinha

- O relatório sai com `proposta.aplicado: false`, `exige_versao_nova: true` e
  `aprovacao_humana: "pendente"`. O alvo declarado é `docs/data/data_contract_v1.json#scores`.
- Aplicar exige: **versão nova do contrato** (1.1/2.0), atualização de `docs/data/DATA_CONTRACT_V1.md`,
  do JSON, do `CHANGELOG.md`, o verificador passando e **aprovação humana registrada** (§10 + ADR-0004).
- O aceite prova o mecanismo: `sha256` do contrato idêntico antes/depois e snapshot das 12 tabelas
  inalterado.

## 5. Se algo falhar

| Sintoma | Causa provável | Ação |
|---|---|---|
| `RECUSA CONTRATO_...` | contrato do card/instrumento/funil com versão desconhecida ou fontes divergentes | conferir `--conferir`; contrato é dono, não se edita para fazer passar |
| `RECUSA BANCO_NAO_E_DEV` | porta de banco remota (`ssh ... psql`) | em dev a porta é `docker exec -i pg-<...> psql` |
| `RECUSA DEPENDENCIA_VERSAO_INCOMPATIVEL` | instrumento (W8-E03) ou funil com versão diferente | rodar a suíte do instrumento; não reimplementar o desfecho |
| exit 6 (`CALIBRACAO_ABSTEVE_VOLUME`) | volume insuficiente — **a pré-condição do card** | é a resposta correta: juntar base real com desfecho antes de calibrar |
| `FAIXAS_PROPOSTAS_NAO_COBREM_A_COORTE` | corte/limite fora da regra de cobertura | defeito: abrir card, o conserto é do componente |
| `lacunas.faixas_nao_propostas` | desfecho perfeitamente separável (sem corte a oferecer) | achado, não erro: os pesos podem ser propostos e as faixas ficam para quando houver base mista |

## 6. Verificação

```bash
python3 scripts/agentes/verificar_calibracao_score.py --autoteste   # 51 itens + 7 dentes
bash scripts/agentes/teste_calibracao_score_aceite.sh               # PostgreSQL descartável (VPS de dev)
bash scripts/verificar_estrutura.sh                                 # portão de estrutura do repo
```
