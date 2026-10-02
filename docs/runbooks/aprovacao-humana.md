# Runbook — aprovacao humana do outbound (TRE-W6-E03-T01)

Componente: `hermes/agents/outreach/approval_workflow.py` · Doc de desenho:
`docs/architecture/aprovacao-humana-v1.md`.
Em uma frase: **le os pedidos `PENDING` que o gerador de abordagem criou, cobra a decisao humana, expira o
que envelheceu e so libera o envio do que foi aprovado com o texto intacto** — sem enviar nada.

## 1. Ciclo normal (o que o cron do harness chama)

```bash
# 1) cobrar o operador (notifica quem mudou desde a ultima rodada; grava as mensagens num arquivo)
python3 hermes/agents/outreach/approval_workflow.py --ambiente dev --prefixo "$PORTA_SQL" \
  --fila --notificacoes /var/lib/outreach/notificacoes.txt --correlation-id "$(uuidgen)"

# 2) fechar o que envelheceu (TTL da politica; hoje 72h) — nao inventa operador
python3 ... --ambiente dev --prefixo "$PORTA_SQL" --expirar --correlation-id "$(uuidgen)"
```

`$PORTA_SQL` e a porta de banco completa, ex.: `docker exec -i pg-sales-dev psql -U sales_ai -d
sales_intelligence`.

## 2. Decidir (o ato humano)

```bash
python3 ... --decidir <approval_id> --decisao aprovar   --por "Anderson Ribeiro" --nota "texto ok"
python3 ... --decidir <approval_id> --decisao rejeitar  --por "Anderson Ribeiro" --nota "nao e o momento"
python3 ... --decidir <approval_id> --decisao editar    --por "Anderson Ribeiro" \
  --edicao /tmp/texto_revisado.json     # {"assunto": "...", "corpo": "...", "cta": "..."}
```

- `--por` e **obrigatorio** e tem de ser nome humano da lista `operadores_autorizados` da politica:
  nome de maquina (`agente-*`, `bot`, `hermes`, ...) RECUSA com exit 3 e **nada e escrito**.
- Revisao (`editar`) passa pela validacao deterministica do gerador irmao: fato sem sustentacao na
  evidencia, citacao ausente ou afirmacao proibida RECUSAM a rodada inteira (`EDICAO_INVALIDA`, exit 1)
  **sem gravar** — quem edita precisa usar os numeros/fatos que estao na evidencia.
- Clicou duas vezes no mesmo voto? `JA_DECIDIDO` (nada reescrito). Mudou de ideia? Use o `--desfazer`
  (abaixo) — voto diferente sem desfazer e `CONFLITO_DE_VOTO`.

## 3. Lote (varios pedidos de uma vez)

```bash
python3 ... --decisoes /tmp/decisoes.jsonl            # dry-run: mostra o que aconteceria
python3 ... --decisoes /tmp/decisoes.jsonl --confirmo # aplica
```
Uma linha por decisao: `{"approval_id": "...", "decisao": "aprovar", "por": "...", "nota": "..."}`.

## 4. Enviar (o outro card): o portao

O W6-E04 **nao decide nada**: ele pergunta.

```bash
python3 ... --consultar <approval_id>
# pode_enviar: true  -> status APPROVED + hash do texto confere + contato ainda limpo
# pode_enviar: false -> motivo ("status 'PENDING' nao libera envio", "o texto do pedido nao bate com o
#                       hash aprovado (nao envie)", "contato bloqueado desde a aprovacao: [...]")
```

Se o portao negar, **nao envie** — reabra o ciclo (o pedido pode ter sido revertido, editado ou o contato
ter entrado em opt-out depois da aprovacao).

## 5. Desfazer uma rodada (rollback)

```bash
python3 ... --desfazer <correlation_id>                  # dry-run: quantos seriam revertidos
python3 ... --desfazer <correlation_id> --confirmo --por "Anderson Ribeiro" \
  --motivo "aprovacao registrada por engano"
```
Devolve os pedidos da rodada a `PENDING`, limpa `decided_at`/`decided_by`, restaura o texto original da
edicao e grava `REVERTIDO_POR:<operador>:<motivo>`. A auditoria da rodada original **fica** (agent_runs e
append-only por convencao: nada e apagado).

## 6. Diagnostico

| sintoma | causa provavel | o que fazer |
|---|---|---|
| `JA_NOTIFICADO` com 0 novos e o operador nao viu nada | a rodada anterior ja notificou o mesmo texto | conferir o arquivo de `--notificacoes` e o canal de entrega |
| `RECUSADO ... nao consegui ler as notificacoes anteriores` | a consulta de idempotencia falhou | rodada RECUSOU de proposito (fail-closed); olhar o erro do banco antes de reexecutar |
| `CONFLITO_DE_VOTO` | outro operador (ou o mesmo) ja decidiu diferente | `--consultar` para ver o estado; se foi engano, `--desfazer` |
| `PEDIDO_EXPIRADO` | passou do TTL | evidencia nova gera **pedido novo**; o velho nao volta |
| `EDICAO_INVALIDA` | o texto revisado cita fato/numero que nao esta na evidencia | reescrever usando o que a evidencia sustenta, ou aprovar o texto do gerador |
| `CONTATO_BLOQUEADO` | `do_not_contact`/`opt_out_*` em vigor agora | nao insista: abordagem a quem pediu para nao ser contatado esta proibida |
| `prod` exit 4 | `--ambiente prod` | e regra do ADR-005: nada nasce em producao por este caminho |

## 7. Nunca faca

- Rodar `--ambiente prod` esperando que funcione (exit 4 por desenho).
- Editar o texto direto no banco: o portao compara o `texto_hash` e vai negar o envio (e o autor da
  mudanca nao fica registrado em lugar nenhum).
- Dar `--por` com nome de agente/bot: RECUSA e polui o log de recusas.
- Reexecutar `--desfazer --confirmo` "para garantir": a segunda passada nao encontra os pedidos naquela
  rodada (o `UPDATE` e condicional) — nada quebra, mas o registro de reversao nao e duplicado.
