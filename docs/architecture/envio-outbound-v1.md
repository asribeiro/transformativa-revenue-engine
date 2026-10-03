# Envio outbound v1 (`envio-outbound-v1`) — card TRE-W6-E04-T01

Componente de **envio** da onda W6 (canal outbound): consome um pedido ja **aprovado por humano** pelo card
irmao W6-E03-T01 e o entrega pelo primitivo de SMTP Titan do card irmao W6-E01-T01. Ele nao gera texto, nao
decide nada e nao inventa destinatario: **integra os dois pais** e e o unico ponto de escrita.

- Codigo: `hermes/agents/outreach/send_workflow.py` (CLI `--planejar`, `--regras`, `--fila`, `--enviar`, `--desfazer`)
- Contrato do componente: `hermes/agents/outreach/envio-outbound-v1.json`
- Politica: `hermes/agents/outreach/politica-envio-v1.json` (status `PROPOSTA_A_HOMOLOGAR`)
- Primitivo (pai E01): `hermes/integracoes/titan/smtp_titan.py`
- Portao (pai E03): `hermes/agents/outreach/approval_workflow.py` -> `consultar()`

## Fluxo de uma rodada

1. **Portao** — `consultar()` do card irmao decide se o pedido pode ser enviado: status `APPROVED`, hash do
   texto batendo com o hash aprovado e contato sem bloqueio (`do_not_contact`/`opt_out_*`). O que o portao
   recusa **nao e enviado** (`PORTAO_NAO_LIBEROU`); pedido inexistente/ilegivel tambem nao.
2. **Guardas de ambiente e destino** — `dev` exige host loopback, remetente e destino do dominio de
   desenvolvimento (`DESTINO_NAO_DEV`); `homolog` exige a aprovacao humana registrada
   (`HOMOLOG_SEM_APROVACAO`); `prod` e RECUSADO (exit 4) sem escrever nada.
3. **Idempotencia (claim exatamente-uma-vez)** — a chave `envio:<approval_id>:<texto_hash>` e reclamada em
   `sync_events` como `ENVIANDO` **antes** de falar com o SMTP. Chave ja `ENVIADO` responde `JA_ENVIADO` (nao
   reenvia); chave em voo responde `ENVIO_EM_VOO` (entrega possivelmente feita nao se repete); chave `FALHOU`
   permite retentativa (incrementa `tentativas`).
4. **Envio** — o primitivo do card irmao recebe `--para/--assunto/--corpo/--chave-idempotencia` via subprocess.
   Corpo = **texto aprovado + CTA**; o destinatario vem **do pedido**, nunca da linha de comando.
5. **Fato** — so o orquestrador escreve, e so em duas tabelas: `interactions` (fato do que foi autorizado, com
   `content_reference` = `<approval_id>:<texto_hash>`) e `sync_events` (claim `ENVIANDO` -> `ENVIADO` ligado a
   `interaction_id`, ou `FALHOU` com o `error_message`). O `contact_id` da FK vem do proprio pedido
   (`entity_id`), como **leitura** — o portao devolve e-mail e nome, nao o id.

## Guarda de escrita

`validar_sql()` recusa, antes de qualquer execucao: SQL vazio, `DDL` (`CREATE`/`ALTER`/`DROP`/`TRUNCATE`),
`DELETE`, `UPDATE` fora das rodadas declaradas e escrita em tabela que nao seja
`sales_intelligence.interactions` ou `sales_intelligence.sync_events`. A politica declara `ddl` e `delete`
como `recusado` — declarar outro veredito e `RecusaDePolitica`.

## Vereditos e motivos

`PLANO` (dry-run) · `ENVIADO` · `JA_ENVIADO` · `RECUSADA` com motivo: `PORTAO_NAO_LIBEROU`, `DESTINO_AUSENTE`,
`DESTINO_NAO_DEV`, `HOMOLOG_SEM_APROVACAO`, `ENVIO_EM_VOO`, `PRIMITIVO_AUSENTE`, `ENVIO_FALHOU`,
`ORGANIZACAO_AUSENTE`, `CONTATO_AUSENTE`, `OPERADOR_AUSENTE`, `MOTIVO_AUSENTE`, `CONFIRMACAO_AUSENTE`,
`POLITICA_INCOERENTE`, `PORTA_DE_BANCO_AUSENTE`. Nada e enviado sem `--confirmo`.

## Desfazer

`--desfazer <correlation_id>` (dry-run) conta o que seria desfeito; com `--confirmo --por <operador> --motivo
<texto>` marca `DESFEITO` e grava `DESFEITO_POR:<operador>:<motivo>` em `error_message`. **O fato em
`interactions` nao e apagado** (auditoria preservada) e um e-mail entregue nao volta: a barreira e o
`--confirmo` antes do envio.

## Evidencia (execucao real, 2026-10-03)

| Medicao | Resultado |
|---|---|
| `python3 scripts/agentes/verificar_envio_outbound.py` | `PASS (60 itens, 0 falhas)` |
| idem `--autoteste` (7 mutacoes, cada uma reprovando o item que nomeia) | 7/7 dentes + `PASS (60 itens)` |
| `bash scripts/agentes/teste_envio_outbound_aceite.sh --prova-de-dente` (VPS, PostgreSQL descartavel + sink SMTP local) | `ACEITE_ENVIO_OUTBOUND_001_OK (44 itens, 0 falhas)` + 3/3 dentes |

O aceite E2E mede: pedido nasce do gerador irmao e e aprovado pelo card irmao; entrega conferida na captura do
sink (TLS, `rcpt_to`, AUTH sem senha, assunto e corpo iguais ao **aprovado**); `interactions`/`sync_events`
conferidos no banco; replay `JA_ENVIADO` sem segunda mensagem; pedido REJEITADO nao envia; `prod` exit 4 sem
escrita; `--desfazer` preservando o fato; nenhuma tabela nova e nenhuma outra tabela tocada.
