# Runbook — envio outbound v1 (`envio-outbound-v1`)

Card TRE-W6-E04-T01. Componente que **envia** o que o humano aprovou (W6-E03-T01) pelo SMTP do Titan
(W6-E01-T01). Doc de arquitetura: `docs/architecture/envio-outbound-v1.md`.

## Antes de qualquer coisa

- `prod` e **recusado** por este componente (exit 4). Envio em producao e decisao do dono e nao esta nesta v1.
- Nada sai sem `--confirmo`. Rodar sem ele e **dry-run** (veredito `PLANO`, nenhuma escrita, nenhum e-mail).
- O destinatario e o assunto/corpo vem **do pedido aprovado**. Se o texto aprovado mudou, o portao recusa —
  nao "ajuste" a mensagem na linha de comando: nao ha esse argumento, de proposito.

## Ver o que esta na fila (leitura, sem risco)

```
python3 hermes/agents/outreach/send_workflow.py --ambiente dev --prefixo "$PREFIXO" --fila
python3 hermes/agents/outreach/send_workflow.py --ambiente dev --prefixo "$PREFIXO" --enviar <approval_id>
python3 hermes/agents/outreach/send_workflow.py --regras
```

`--fila` mostra apenas pedido **aprovado** que ainda nao foi enviado (`ELEGIVEL`), o que ja saiu
(`JA_ENVIADO`) e o que esta em voo (`ENVIO_EM_VOO`). `$PREFIXO` e o comando de porta de banco, ex.:
`docker exec -i pg-envio-acc psql -U sales_ai -d sales_intelligence`.

## Enviar de verdade (dev)

1. Confirme o ambiente e o destino: `dev` so entrega para `@dev.local` contra host loopback (sink).
2. Rode com `--confirmo`:

```
python3 hermes/agents/outreach/send_workflow.py --ambiente dev --prefixo "$PREFIXO" \
  --enviar <approval_id> --confirmo --correlation-id "<uuid da rodada>" \
  --primitivo hermes/integracoes/titan/smtp_titan.py
```

3. Leia o JSON: `veredito: ENVIADO` com `interaction_id`/`sync_event_id`, ou `RECUSADA` com `motivo`.
4. Confira no banco: `interactions.content_reference` = `envio:<approval_id>:<texto_hash>` e
   `sync_events.status = 'ENVIADO'`.

## Homolog (quando o dono autorizar)

Exige `TRE_TITAN_APROVACAO_HUMANA` preenchida e destino. Sem ela o componente responde
`HOMOLOG_SEM_APROVACAO` — de proposito.

## Desfazer / reverter uma rodada

```
# dry-run: conta o que seria desfeito (nao escreve)
python3 hermes/agents/outreach/send_workflow.py --ambiente dev --prefixo "$PREFIXO" --desfazer "<correlation_id>"

# efetivo: exige operador e motivo
python3 hermes/agents/outreach/send_workflow.py --ambiente dev --prefixo "$PREFIXO" \
  --desfazer "<correlation_id>" --confirmo --por "Anderson Ribeiro" --motivo "pedido errado"
```

Marca `DESFEITO` e grava `DESFEITO_POR:<operador>:<motivo>`. **O e-mail ja entregue nao volta** e o fato em
`interactions` e preservado — para impedir um envio futuro, o caminho e o portao (rejeitar/expirar o pedido).

## Quando algo der errado

| Sintoma | O que aconteceu | O que fazer |
|---|---|---|
| `PORTAO_NAO_LIBEROU` | pedido fora de `APPROVED`, hash divergente, contato bloqueado ou pedido inexistente | conferir o pedido no card irmao (`--consultar`) |
| `ENVIO_EM_VOO` | a chave ficou `ENVIANDO` (rodada interrompida no meio) | conferir no sink/SMTP se a mensagem saiu antes de tentar de novo |
| `ENVIO_FALHOU` | o primitivo recusou (exit != 0) | ler `error_message` da chave (traz a trilha do primitivo) e a config `TRE_TITAN_*` |
| `DESTINO_NAO_DEV` | destino fora do dominio de dev | corrigir o contato; nao relaxar a guarda |
| `GRAVACAO_FALHOU` | escrita recusada no banco | ler o `detalhe`; nenhuma escrita parcial fica (transacao) |
| `PORTA_DE_BANCO_AUSENTE` | a porta nao respondeu | conferir `--prefixo`/psql |
| `POLITICA_INCOERENTE` | politica/contrato divergentes (tabelas, vereditos, vocabulario) | corrigir a politica; nao afrouxar |

## Medicao

```
python3 scripts/agentes/verificar_envio_outbound.py --autoteste            # offline: 60 itens + 7 dentes
bash scripts/agentes/teste_envio_outbound_aceite.sh --prova-de-dente       # VPS: E2E 44 itens + 3 dentes
```

O aceite E2E boota PostgreSQL descartavel (`pg-envio-acc`, migration 0001) e um **sink SMTP local** em
`127.0.0.1` com certificado proprio — nenhuma credencial Titan e nenhum destino real sao usados (ADR-005).
Se o container `pg-envio-acc` ja existir, o aceite **para e avisa**: ele nao mexe em container que nao criou.
