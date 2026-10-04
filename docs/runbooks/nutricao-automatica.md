# Runbook — nurture automatizado (`nutricao-automatica-v1`, card TRE-W9-E05-T01)

Plano de proximos toques de nurture a partir das duas medicoes dos pais. **O componente nao abre banco,
nao escreve nada e nao envia nada**: a saida e' uma FILA de pedidos de toque, cada um com
`exige_aprovacao_humana: true`.

## 1. Pre-requisitos

- Os DOIS relatorios dos pais, gerados na mesma base e na mesma janela:
  - `previsao-canal-v1` (card W9-E03-T01): `python3 hermes/agentes/analytics/previsao_canal.py
    --ambiente dev --porta-banco "docker exec -i pg-analytics-nurture-acc psql -U sales_ai -d sales_intelligence" \
    --agora 2026-10-03T00:00:00Z --saida saida/`
  - `melhor-horario-v1` (card W9-E04-T01): `python3 hermes/analytics/melhor_horario.py --ambiente dev \
    --prefixo "docker exec -i pg-analytics-nurture-acc psql -U sales_ai -d sales_intelligence" \
    --desde 2026-08-01T00:00:00Z --ate 2026-10-03T00:00:00Z --limite-amostra 5 --json saida/melhor-horario.json`
- O nurture **nao fala com Postgres**: nenhuma credencial e' necessaria para ele.

## 2. Rodar o plano

```bash
python3 hermes/agentes/analytics/nutricao_automatica.py --ambiente dev \
  --relatorio-canal saida/previsao-canal.json \
  --relatorio-horario saida/melhor-horario.json \
  --agora 2026-10-03T00:00:00Z --saida saida/nurture
```

- Leitura da saida: `saida/nurture/nutricao-automatica.json` (fila completa) e
  `saida/nurture/nutricao-automatica.html` (pagina auto-contida).
- Conferencia sem relatorio: `--conferir` (contrato + contratos dos pais + guarda de escrita);
  `--regras` (politica declarada). Nenhum dos dois toca banco.
- Exit codes: `0` plano/abstencao/conferencia · `2` uso errado · `3` recusa (contrato/dependencia/guarda) ·
  `4` producao recusada (ADR-005) · `5` segredo vazado.

## 3. Ler a saida

| Campo | Significado |
|---|---|
| `veredito` | `PLANO_EMITIDO` ou `PLANO_ABSTIDO` |
| `pre_condicoes.faltando` | a camada que faltou (base multicanal do canal, previsoes, janela sem amostra) — **nao ha** plano parcial |
| `janela` | a melhor janela do pai (dia x faixa), com o fuso declarado por ele |
| `fila[]` | os toques, ordenados por `due_at_utc`, com canal, passo, `due_at_local`, `exige_aprovacao_humana` e as condicoes de parada |
| `por_organizacao[]` | resumo por organizacao: canal previsto, amostra do canal, bloqueios e primeiro/ultimo toque |
| `lacunas` | organizacoes com algum canal bloqueado e a contagem de toques por canal |

## 4. Operacao

- **Quem executa o toque e' o caminho de outbound com Human Approval (W6)** — este plano e' o pedido.
  Antes de qualquer contato, checar as condicoes de parada do proprio toque (opt-out chegou depois?
  a organizacao avancou no funil? ja' respondeu?).
- **Nunca** transformar a fila em `recommendations`, cron, evento de calendario ou outbox sem contrato
  novo + aprovacao registrada (doc 12 §10).
- Se o plano abstem, o proximo passo e' **aumentar a base** (mais interacoes multicanal / mais envios por
  janela), nao afrouxar `limite_amostra` nem os minimos da pre-condicao.

## 5. Aceite e verificacao

```bash
python3 scripts/agentes/verificar_nutricao_automatica.py --autoteste   # suite 42 itens + 5/5 mutacoes
bash scripts/agentes/teste_nutricao_automatica_aceite.sh               # ACEITE_NUTRICAO_AUTOMATICA_001_OK (VPS)
bash scripts/verificar_estrutura.sh                                    # PASS
```

## 6. Lacunas declaradas (resumo)

Materializacao/agendamento e deduplicacao entre rodadas (L1/L7) · avaliacao do estagio do funil por toque
na derivacao (L2) · janela global e canal de coorte, sem segmentacao (L3) · cadencia declarada, nao medida
(L4) · feriados e fuso do destinatario (L5) · revalidacao de opt-out na hora do toque e' de quem executa
(L6). A lista completa viaja no relatorio (`lacunas_declaradas`).
