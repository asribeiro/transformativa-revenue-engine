# Runbook — gate JEV no dispatch do board

Card de origem: **TRE-W0-E04-T05**. Implementação: `deploy/hermes/` (README de lá) e
`hermes/jev/gate/gate_jev.py`. Validação com a prova: `docs/validation/jev-gate-no-dispatch.md`.

## O que o encaixe faz, em uma frase

Antes de o board reivindicar (`claim`) ou despachar (`dispatch`) um card, o **roteador JEV** decide; se o
roteador não liberar, o card **não executa por caminho nenhum**, e o recibo fica gravado em
`hermes/jev/receipts/`.

## Estado do encaixe

```bash
bash scripts/instalar_gate_jev.sh --check
```

Três coisas precisam estar verdadeiras:

1. `kanban.jev_gate` aponta para `hermes/jev/gate/gate_jev.py`;
2. o código está aplicado em `/opt/hermes` (metade que exige root);
3. o despachante foi **reiniciado** depois da aplicação (o gateway só carrega o kernel novo ao subir).

## Liberar um card (o caso do dia a dia)

Card retido aparece assim no despacho:

```
Retido pelo gate JEV: t_xxxxxxxx — ESCALATE: acao nao classificada com seguranca: ... faltou o codigo canonico da acao
```

Para liberar, declare o **código canônico** da ação em `hermes/jev/acoes-declaradas.yaml`:

```yaml
declaracoes:
  - card_id: t_xxxxxxxx
    acao_codigo: ajuste_de_texto      # um dos `codigos_validos` do mesmo arquivo
    declarado_por: Anderson Ribeiro
    declarado_em: '2026-09-29'
    motivo: 'motivo curto e auditável'
```

O próximo tick reavalia o card **sem precisar re-promover** (o encaixe não tira o card de `ready`).
Reversão: apagar a entrada.

**Códigos proibidos** (as 8 ações que nenhuma máquina decide) não liberam nada: declarar um deles faz o
recibo sair `BLOCK` com `exige_aprovacao_humana: true` — o caminho é o Human Approval, não a declaração.

## Ler a decisão

```bash
# recibo de 13 campos da última consulta do card
ls hermes/jev/receipts/ | grep t_xxxxxxxx

# histórico no card (uma linha por decisão distinta, não uma por tick)
hermes kanban --board transformativa-revenue-engine tail t_xxxxxxxx
```

O evento tem `outcome` (`PASS` / `ESCALATE` / `BLOCK` / `GATE_INDISPONIVEL` / `GATE_TIMEOUT`),
`decision_id`, `motivo`, `codigo_de_acao` e `receipt_path`. **Não há segredo** em nenhum deles.

## Sintomas e o que significam

| Sintoma | Significado | Ação |
|---|---|---|
| `Skipped (gate JEV)` com `ESCALATE` | card sem código canônico declarado | declarar o código (seção acima) |
| `Skipped (gate JEV)` com `BLOCK` | ação de decisão humana ou guardrail acionado | Human Approval, não declaração |
| `GATE_INDISPONIVEL` | comando do gate ausente, saída fora do contrato, ou exceção | conferir `kanban.jev_gate` e se o arquivo do hook existe |
| `GATE_TIMEOUT` | o hook não respondeu em `kanban.jev_gate_timeout` segundos | investigar o hook; **aumentar o timeout não é a correção** — abstinência é fail-closed de propósito |
| board inteiro parado de despachar | esperado enquanto os códigos não forem declarados | decidir os códigos por card, ou desligar o encaixe |

## Desligar (emergência)

```bash
bash scripts/remover_gate_jev.sh            # desarma pela configuração, uma linha, sem root
```

Efeito: o adaptador devolve "sem encaixe", o board despacha como antes e **nenhum recibo é gravado** (não
há consulta). Para tirar também o código do kernel: `--kernel` (exige root) ou a linha do operador com
`--reverter`.

## Nunca

- **Não afrouxar o critério do roteador** para fazer um card passar (defeito `D07`). O caminho é declarar
  código existente ou **nomear um código comum novo** no roteador — mudança versionada e auditável.
- **Não declarar código proibido** como atalho: o roteador bloqueia e o registro fica no recibo.
- **Não editar `/opt/hermes` à mão**: use `deploy/hermes/aplicar_gate_jev.sh` (é idempotente e faz backup).
