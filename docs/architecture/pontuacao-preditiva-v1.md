# Pontuacao preditiva — `pontuacao-preditiva-v1` (TRE-W9-E02-T01)

Card: **TRE-W9-E02-T01** · Onda **W9 (Inteligencia Avancada)** · Epic E02 · Prioridade P3
Depende de: **W9-E01-T01 (calibracao do score)** — dependencia MEDIDA, nao presumida.

Componente: `hermes/agentes/analytics/pontuacao_preditiva.py`
Contrato: `hermes/agentes/analytics/pontuacao-preditiva-v1.json`
Suite offline: `scripts/agentes/verificar_pontuacao_preditiva.py --autoteste`
Aceite de ponta: `scripts/agentes/teste_pontuacao_preditiva_aceite.sh`

## Que pergunta o card responde

O score de PRIORITY e' ORDINAL: diz quem esta' na frente, nao diz QUAL a chance de ganhar. Este
componente fecha essa lacuna com um numero honesto e verificavel:

> dado o score de uma organizacao, **qual a probabilidade de ganho registrado** — e ela acerta
> fora da amostra?

Nao e' uma segunda calibracao (pesos e faixas sao do W9-E01-T01). Aqui o objeto e' outro: a
PROBABILIDADE por organizacao, medida contra o desfecho, e a PREVISAO das organizacoes que ainda
nao tem desfecho.

## Como o numero e' construido (deterministico)

1. **Coorte** — organizacoes vivas com PRIORITY valido e os QUATRO componentes na mesma
   `score_version` (herdado do instrumento `efetividade_score.py`, que herdou do `funil.py`).
2. **Pesos em uso** — vem do **relatorio da calibracao**: a proposta, quando aprovada; senao os
   pesos em vigor publicados no proprio relatorio. Pesos escolhidos a mao nao existem aqui.
3. **Corte ajuste/validacao** — `sha256(organization_id) % 3 < 2`, o MESMO do relatorio; se o
   relatorio trouxer outro corte, o componente RECUSA (`CORTE_DIVERGENTE`).
4. **Curva** — o score ponderado (0..100) e' cortado em 10 binos; a taxa de vitoria observada no
   lado do **ajuste** forma a curva, e **PAVA** (pool adjacent violators) impoe monotonicidade
   nao-decrescente, ponderando por n ao fundir.
5. **Bino sem base** — nao recebe valor proprio: herda o bloco ANTERIOR (regra declarada). Antes
   do primeiro bloco nao ha' previsao: a organizacao sai `sem_base`, nunca com zero.
6. **Medicao fora da amostra** (lado de **validacao**): AUC, Brier, Brier skill contra a taxa-base
   do ajuste, log-loss e a tabela de confiabilidade (previsto x observado por bino).
7. **Previsao** — cada organizacao com lastro completo e desfecho EM ABERTO recebe score, bino,
   faixa do Data Contract e probabilidade de ganho.

## Invariantes

- **Previsao nao e' aplicacao.** O relatorio carrega `aplicacao.aplicado: false`,
  `exige_versao_nova: true`, `aprovacao_humana: pendente`. Persistir probabilidade (coluna, tabela,
  `score_type` novo) e' mudanca estrutural (§10 do Data Contract + ADR-0004) e quem aplica e'
  operador com aprovacao registrada.
- **Leitura pura.** Nenhum SQL proprio: a leitura e' a do INSTRUMENTO, com
  `default_transaction_read_only = on` e auditoria de verbo de escrita antes de conectar. O aceite
  prova o snapshot das 12 tabelas identico antes/depois e o PostgreSQL recusando INSERT.
- **Dependencia medida.** Sem o relatorio da calibracao: `DEPENDENCIA_CALIBRACAO` (exit 3). Com
  corte diferente: `CORTE_DIVERGENTE` (exit 3). Com faixas ascendentes divergentes:
  `FAIXA_DIVERGENTE` (exit 3).
- **Volume como mecanismo.** A pre-condicao do card ("volume real suficiente") e' gate medido:
  coorte com desfecho abaixo de 30, ou qualquer lado sem 5 de cada classe, ABSTEVE (exit 6) e o
  relatorio sai sem `modelo`, sem `avaliacao` e sem previsao.
- **Determinismo.** Mesma base + mesmo relatorio + mesma referencia temporal => mesmo
  `hash_do_relatorio`; `gerado_em` e `referencia_temporal` nao entram no hash.
- **Privacidade.** UUID canonico da organizacao, contagem e numero previsto. Nenhuma coluna de
  contato, nome, e-mail, telefone ou CNPJ e' lida.

## Codigos de saida

`0` relatorio gerado · `2` uso errado · `3` recusa (contrato/dependencia/fonte/guarda/banco) ·
`4` producao recusada (ADR-005) · `5` segredo vazado · `6` volume insuficiente (absteve).

## Como ler o resultado

- `avaliacao.validacao.brier_skill > 0` → a curva bate a taxa-base constante **fora da amostra**.
  `<= 0` e' resultado legitimo e publicavel: a probabilidade nao ganha da constante.
- `confiabilidade` mostra onde a curva mente: desvio positivo grande = probabilidade otimista.
- `modelo.bins_sem_base` e `previsao_por_organizacao.sem_base` dizem quanto ficou sem numero —
  cobertura declarada, nunca escondida.

## Lacunas declaradas (v1)

L1 coorte acumulada · L2 associacao nao e' causa · L3 variancia alta com ~30 por lado operacional ·
L4 probabilidade derivada, nao persistida · L5 vies de sucessao do score (ultimo valor) · L6 alvo e'
'ganhou x perdeu' · L7 PAVA e' guloso, nao o otimo global · L8 so' quem tem lastro completo recebe
numero (cobertura declarada em `cobertura_pct`).
