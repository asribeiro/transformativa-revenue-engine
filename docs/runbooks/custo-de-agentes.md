# Runbook — Custo de agentes (`custo-agentes-v1`)

Card **TRE-W8-E05-T01** (W8 / Epic E05). Componente `hermes/agentes/analytics/custo_agentes.py` ·
contrato `hermes/agentes/analytics/custo-agentes-v1.json` · desenho `docs/architecture/custo-agentes-v1.md`.

## Quando usar

- para saber **quanto custa cada agente** e quanto custa **cada sucesso** dele;
- para decidir onde cortar gasto (visão por modelo) e qual workflow é caro (visão por workflow);
- para conferir, depois de um lote, se o custo declarado bate com a expectativa.

Não usar para: aprovar/recusar lane, escolher modelo em produção, ou atribuir custo a receita — nada disso
está no escopo (lacuna L5 do desenho).

## Comandos

```bash
# 1. conferir contrato, DDL e guardas — SEM banco (o mais barato; primeiro passo depois de mexer no repo)
python3 hermes/agentes/analytics/custo_agentes.py --ambiente dev --conferir

# 2. ver o plano declarado (colunas lidas, vocabulário de status, ranking) — SEM banco
python3 hermes/agentes/analytics/custo_agentes.py --ambiente dev --planejar

# 3. medir em dev (porta de banco LOCAL; prefixo remoto é recusado)
python3 hermes/agentes/analytics/custo_agentes.py \
  --ambiente dev \
  --porta-banco "docker exec -i pg-custo-acc psql -U sales_ai -d sales_intelligence" \
  --saida /tmp/custo-agentes

# 4. recorte por período (filtra por started_at)
python3 hermes/agentes/analytics/custo_agentes.py --ambiente dev --porta-banco "..." \
  --desde 2026-10-01T00:00:00Z --ate 2026-10-07T23:59:59Z --saida /tmp/custo-semana

# 5. suíte offline (30 itens + 8 dentes) e aceite de ponta em Postgres descartável
python3 scripts/agentes/verificar_custo_agentes.py --autoteste
bash scripts/agentes/teste_custo_agentes_aceite.sh
```

A porta de banco também pode vir de `TRE_CUSTO_AGENTES_PORTA_BANCO`.

## Saídas

`custo-agentes.json`, `custo-agentes.csv` (uma linha por agente) e `custo-agentes.html` (dashboard
auto-contido) no diretório de `--saida`. Sem `--saida`, só o resumo de uma linha vai para o stdout.
Sem `--com-carimbo` a saída é byte a byte reproduzível.

## Leitura da linha de resumo

```
CUSTO_AGENTES_OK veredito=ANALISADO runs=16 concluidas=11 falhas=1 recusadas=1 \
custo_total=0.022400 runs_sem_custo=4 agentes=5 mais_caro=scout(0.002000) hash=...
```

- `runs_sem_custo > 0` significa que parte das execuções **não declarou** custo: a média continua válida
  sobre o que foi declarado, mas **não** é o custo do sistema inteiro (lacuna L1). Nunca leia
  `custo_total` como teto.
- `mais_caro=nenhum(MOTIVO)` não é erro: é o ranking se recusando a coroar com base subdeclarada ou sem
  amostra. `SEM_EXECUCOES`, `SEM_CUSTO_DECLARADO`, `CUSTO_NAO_DECLARADO_EM_PARTE`, `AMOSTRA_INSUFICIENTE`.

## Falhas e o que fazer

| Sintoma | Motivo provável | Ação |
|---|---|---|
| `RECUSA PRODUCAO_RECUSADA` (exit 4) | ambiente `prod` | é por desenho (ADR-005): promova por ato de operador com aprovação registrada |
| `RECUSA BANCO_NAO_E_DEV` (exit 3) | porta remota em dev | use `docker exec -i pg-<dev|aceite> psql ...` |
| `RECUSA CONTAINER_NAO_LOCAL_DE_DEV` | container fora da lista (`pg-sales|pg-custo|pg-analytics|pg-aceite`) | renomeie o container de dev ou use um da lista |
| `RECUSA HOMOLOG_SEM_CONFIRMO` | leitura em homolog sem `--confirmo` | acrescente `--confirmo` |
| `RECUSA CONTRATO_DIVERGE_DA_DDL` (exit 3) | a DDL congelada não tem uma coluna que a métrica lê | a DDL é a verdade: conserte o contrato do componente — não invente a coluna |
| `RECUSA LINHA_FORA_DO_FORMATO` (exit 3) | a porta devolveu linha com nº de campos ≠ 14 | a porta não é o `psql` do contrato (ou há `|` no dado) |
| `RECUSA SENHA_VAZADA` (exit 5) | o valor de `TRE_CUSTO_AGENTES_TOKEN` apareceu na saída | rotacione o valor e rode sem despejar o ambiente |
| `RECUSA CONSULTA_EXCEDEU_TEMPO` | base grande sem índice útil | restrinja com `--desde/--ate` |

## Operação segura

- **Nunca** rode com `--ambiente prod`. O componente recusa, mas não conte com isso: a decisão é humana.
- `agent_runs` pode conter `input`/`output` (JSONB com texto de empresa). O componente **não** seleciona
  essas colunas; se algum dia selecionar, é defeito — a suíte tem item que reprova.
- O container descartável do aceite (`pg-custo-acc`) é removido no fim; o runbook do aceite não toca os
  containers do ambiente.
- Evidência de rodada (saída completa + exit code) vai para o registro de execuções; nenhum segredo em
  texto claro.

## Rollback

Reverter o commit do card (arquivos novos, sem DDL e sem migration) e remover o container descartável do
aceite. Nada em homolog/produção; nenhum serviço, cron ou credencial tocada.

## Risco

**Baixo.** Leitura pura sobre base de dev, saída agregada (sem PII, sem UUID de organização), nenhum ato
externo. Os riscos declarados são de **leitura do dado**, não de operação: custo subdeclarado (L1), tarifa
divergente por produtor (L2), unidade não declarada (L3), vocabulário de status em aberto (L4) e janela por
`started_at` (L5) — todos viajam no próprio relatório em `lacunas_declaradas`.
