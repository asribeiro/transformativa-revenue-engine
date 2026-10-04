# Runbook — Previsão do melhor canal (`previsao-canal-v1`)

**Card:** `TRE-W9-E03-T01` · **Componente:** `hermes/agentes/analytics/previsao_canal.py` · **Contrato:**
`hermes/agentes/analytics/previsao-canal-v1.json` · **Dependência:** `hermes/agentes/analytics/funil.py`

Leitura pura sobre `sales_intelligence`. Não escreve, não envia e não cria recomendação (ADR-005).

---

## 1. Conferir o contrato e a dependência (sem banco)

```bash
python3 hermes/agentes/analytics/previsao_canal.py --ambiente dev --conferir
# PREVISAO_CANAL_CONFERIR_OK contrato=previsao-canal-v1 canais=3 minimos={...} funil=funil-v1
python3 hermes/agentes/analytics/previsao_canal.py --ambiente dev --planejar
# imprime vocabulario de canal + bloqueio, minimos da pre-condicao e o endpoint principal
```

Recusas esperadas neste passo: `CONTRATO_AUSENTE`, `CONTRATO_ILEGIVEL`, `VERSAO_DO_CONTRATO_DESCONHECIDA`,
`CONTRATO_SEM_VOCABULARIO_DE_CANAL`, `CANAL_SEM_NOME_OU_DUPLICADO`, `BLOQUEIO_DE_CANAL_DESCONHECIDO`,
`CONTRATO_DE_DADOS_SEM_CAMPO_DE_COMPLIANCE`, `MINIMO_INVALIDO`, `MINIMOS_INCOERENTES`,
`ENDPOINT_FORA_DOS_ESTAGIOS_DO_PAI`, `NIVEL_DO_ENDPOINT_DIVERGENTE`, `FONTES_DO_FUNIL_DIVERGEM`,
`FONTES_PROPRIAS_DIVERGEM_DO_CONTRATO`, `DEPENDENCIA_AUSENTE`, `DEPENDENCIA_VERSAO_INCOMPATIVEL`,
`DEPENDENCIA_INCOMPATIVEL`.

## 2. Rodar a medição (dev — porta de banco LOCAL)

```bash
python3 hermes/agentes/analytics/previsao_canal.py \
  --ambiente dev \
  --porta-banco "docker exec -i pg-analytics-canal psql -U sales_ai -d sales_intelligence" \
  --agora 2026-10-03T00:00:00Z \
  --saida /tmp/out-canal
# PREVISAO_CANAL_OK pre_condicao=atendida canais_suficientes=N orgs_com_interacao=N previsoes=N ...
```

Saída: `/tmp/out-canal/previsao-canal.json` + `previsao-canal.html`.

- `--desde`/`--ate` (ISO) filtram a janela de cada fonte (`interactions.occurred_at`, `contacts.created_at` e as
  colunas de tempo das fontes do funil). Sem janela, a coorte é o acumulado (lacuna L5).
- `--agora` é a referência temporal (ISO). Entra no relatório como `referencia_temporal` e **não** entra no hash.
- **Em dev a porta de banco tem de ser local** (`docker exec -i pg-<sales|funil|analytics|aceite>... psql`).
  Prefixo remoto (`ssh ... psql`) é recusado com `BANCO_NAO_E_DEV` (exit 3).
- `homolog` exige `--confirmo`; `prod` **recusa por desenho** (exit 4, ADR-005), antes de ler o contrato.

**Se a pré-condição não for atendida**, a rodada continua (exit 0) e o relatório diz o que falta:

```bash
python3 - <<'PY'
import json; r=json.load(open("/tmp/out-canal/previsao-canal.json"))
print(r["pre_condicao_dados_multicanal"])
PY
# {'atendida': False, 'canais_suficientes': 1, ..., 'faltando': ['canais com base suficiente >= 2 (obtido 1)', ...]}
```

Nesse caso `previsao_emitida=false` e `previsoes=[]` — **não** há canal previsto. Não force o número: o caminho é
volume real de interações, não afrouxar o mínimo (mudar mínimo = nova versão do contrato).

## 3. Ler o resultado

| Campo | O que responde |
|---|---|
| `pre_condicao_dados_multicanal` | a base sustenta a previsão? `faltando` diz o que falta |
| `por_canal[].avanco_pct` / `.lift_avanco` | efetividade de cada canal contra a taxa-base |
| `por_canal[].base_suficiente` | canal com base declarada (fora do ranking quando `false`) |
| `ranking_de_canais` | canais elegíveis em ordem de taxa de avanço |
| `previsoes[]` | por organização: canal, taxa, `amostra_do_canal`, `empate_desfeito_por`, `canais_bloqueados` |
| `lacunas` | o que ficou fora, nomeado (vocabulário, bloqueios, sem contato, sem canal elegível) |

## 4. Trocas de peça (o que exige nova versão do contrato)

- **canal novo** (ex.: `PHONE`): declarar no contrato do componente (`vocabulario_de_canal.canais`) com o
  bloqueio; sem isso o canal cai em `canal_fora_do_vocabulario` (fail-closed). Congelar o vocabulário no Data
  Contract é decisão do dono (lacuna L1).
- **mínimos da pré-condição**: mudar `minimo_canais_com_base` / `minimo_organizacoes_por_canal` /
  `minimo_organizacoes_com_evidencia` = **nova versão** do contrato (`previsao-canal-v2`), com motivo registrado.
- **endpoint principal**: `Reunião` (nível 6) é declarado e conferido contra os estágios do pai; trocar exige
  coerência com `funil-v1`.
- **peso/faixa de score**: fora do escopo (W9-E01/E02).

## 5. Sinais de alarme

| Sintoma | Leitura |
|---|---|
| `previsao_emitida=false` | base não atende a pré-condição — leia `faltando`; **não** é defeito do componente |
| `canal_fora_do_vocabulario` | alguém gravou canal novo na trilha: declarar ou investigar a origem |
| `canal_bloqueado_*` subindo | opt-out/`do_not_contact` crescendo — leia como compliance, não como erro |
| `DEPENDENCIA_VERSAO_INCOMPATIVEL` | o `funil.py`/contrato do pai mudou de versão: conferir W8-E01-T01 antes de seguir |

## 6. Reproduzir as provas

```bash
# suíte offline + autoteste (mutação) — não precisa de banco
python3 scripts/agentes/verificar_previsao_canal.py --autoteste

# aceite de ponta: PostgreSQL descartável + migration 0001 + base semeada (na VPS de dev, com docker)
bash scripts/agentes/teste_previsao_canal_aceite.sh          # container pg-analytics-canal, removido no fim
bash scripts/agentes/teste_previsao_canal_aceite.sh --manter # mantém o container para inspeção
```

O aceite remove o container sozinho (`trap limpar EXIT`) e usa uma base **própria** em `/tmp/aceite-w9e03t01`
(configurável por `TRE_ACEITE_BASE`) — nunca reaproveita diretório de outro card.
