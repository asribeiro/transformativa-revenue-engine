# Workflow de aprovacao humana do outbound v1

Card: **TRE-W6-E03-T01** · Onda W6 · Baseline V1.1.0 · Depende de: TRE-W6-E02-T01 (gerador de abordagem).
Status: **implementado e medido** (suite offline 79 OK / autoteste 20/20 · aceite E2E em PostgreSQL
descartavel 104 OK / 0 FALHOU · 4/4 dentes).

## 1. O que este componente e (e o que NAO e)

E o **meio-campo humano** do outbound: le os pedidos de aprovacao que o gerador (W6-E02) gravou em
`sales_intelligence.human_approvals` com `status = PENDING`, monta a mensagem para o operador,
recebe os atos humanos (aprovar / editar / rejeitar), expira o que envelheceu e **libera** o envio
pelo portao de consulta.

- **NAO envia nada.** Envio e `TRE-W6-E04-T01`. Aqui nao existe SMTP, HTTP ou fila de saida; a
  auditoria registra `entrega_externa: false` e `envio.executado: false` no `--planejar`.
- **NAO gera texto.** O rascunho e do gerador irmao; a revisao do operador e revalidada por ele
  (mesma evidencia, mesma politica de citacao) — dono unico, sem copia de regra.
- **NAO cria pedido novo.** `--fila`, `--decidir`, `--expirar`, `--consultar` e `--desfazer` so
  escrevem nas **duas** tabelas permitidas: `human_approvals` e `agent_runs`.

### Arquivos

| arquivo | papel |
|---|---|
| `hermes/agents/outreach/approval_workflow.py` | modulo unico do workflow (CLI + regras) |
| `hermes/agents/outreach/politica-aprovacao-v1.json` | politica do card: status, verbos, transicoes, operadores, TTL, notificacao |
| `hermes/agents/outreach/notificacao-aprovacao-v1.md` | template da mensagem (marcadores `{{...}}`) |
| `hermes/agents/outreach/aprovacao-humana-v1.json` | contrato do componente (o que le, o que escreve, lacunas) |
| `scripts/agentes/verificar_fluxo_aprovacao.py` | suite offline + autoteste de mutacoes |
| `scripts/agentes/teste_fluxo_aprovacao_aceite.sh` | aceite E2E em PostgreSQL descartavel + prova de dente |

## 2. Vocabulario e fonte da verdade

O **vocabulario de estados NAO esta no codigo**: vem do Data Contract
(`docs/data/data_contract_v1.json` -> `vocabularies["human_approvals.status"]`), e a politica so
declara **papeis simbolicos** (`PAPEL_PENDENTE`, `PAPEL_APROVADO`, `PAPEL_REJEITADO`,
`PAPEL_EXPIRADO`) apontando para eles. `STATUS_AGENT_RUNS` (COMPLETED/FAILED/REJECTED) e status de
**rodada**, nao de pedido, e por isso pode ser literal.

Carregar a politica e **fail-closed** (`RecusaDePolitica`): papel faltando, estado fora do contrato,
estado do contrato sem papel, papel repetido, verbo apontando para fora do mapa, verbo devolvendo o
pedido a `PENDING`, ato desconhecido, transicao para estado inexistente, `operadores_autorizados`
vazio, padrao de maquina que nao compila, TTL invalido, marcador do template sem contrato.

## 3. Estados, verbos e transicoes

```
PENDING ──aprovar──▶ APPROVED ──┐
   │                            ├── reversao (--desfazer) ──▶ PENDING
   ├──editar──▶ APPROVED ───────┘
   ├──rejeitar──▶ REJECTED ────────▶ PENDING (tambem reversivel)
   └──expiracao (TTL) ──▶ EXPIRED ──▶ terminal (nao volta atras)
```

- A decisao so sai de `PENDING` (`transicoes.decisao`); a expiracao so age em `PENDING`
  (`transicoes.expiracao`); a reversao so age em `APPROVED`/`REJECTED` (`transicoes.reversao`).
- **A lista e declarada, nao adivinhada**: os atos vivem em `transicoes` da politica
  (`decisao`/`expiracao`/`reversao`) e o modulo exige os tres.
- `EXPIRED` e terminal: um pedido vencido nao aceita aprovacao nem rejeicao (evidencia nova gera
  pedido novo, nao ressuscita o velho).

## 4. Recusa por motivo (nunca por excecao crua)

| motivo | quando |
|---|---|
| `OPERADOR_AUSENTE` | `--por` nao informado |
| `OPERADOR_NAO_AUTORIZADO` | nome fora de `operadores_autorizados` |
| `OPERADOR_NAO_HUMANO` | nome casa com `padroes_nao_humanos` (`agente-*`, `bot`, `hermes`, `system`, ...) |
| `PEDIDO_NAO_ENCONTRADO` | id inexistente |
| `PEDIDO_EXPIRADO` | decisao sobre pedido `EXPIRED` |
| `PEDIDO_JA_REJEITADO` | pedido `REJECTED` nao se reabre por `--decidir` |
| `CONFLITO_DE_VOTO` | pedido ja decidido com voto **diferente** (nao reescreve) |
| `CONFLITO_DE_ESTADO` | o `UPDATE` condicional nao pegou a linha (`WHERE status = PENDING`) |
| `CONTATO_BLOQUEADO` | `do_not_contact`/`opt_out_*` em vigor **na hora da decisao** |
| `RECOMENDACAO_NAO_ABERTA` | a recomendacao de origem saiu de `OPEN` |
| `EDICAO_INVALIDA` | texto revisado reprovado pela validacao do gerador irmao |
| `MARCADOR_NAO_SUBSTITUIDO` | template com marcador declarado sem valor (a mensagem nao sai com `{{...}}`) |
| `MOTIVO_AUSENTE` | `--desfazer --confirmo` sem `--motivo` |
| `PORTA_AUSENTE` | a porta de banco recusou o SQL |

## 5. Atos humanos

- **aprovar**: `status=APPROVED`, `decided_by` (nome **canonico** da politica), `decided_at`,
  `decision_notes`, e o carimbo `proposed_action.decisao.{texto_hash, revisado=false, ...}` do texto
  que foi realmente lido.
- **editar**: o texto revisado passa pela validacao deterministica do **gerador irmao**
  (`validar_abordagem` sobre a mesma evidencia) antes de qualquer escrita. Aprovado o texto novo,
  ele substitui `assunto/corpo/cta` e o original fica em `decisao.texto_original`; o `texto_hash`
  muda e `revisado=true`. Reprovar (`EDICAO_INVALIDA`) **nao escreve nada**.
- **rejeitar**: `status=REJECTED` com operador e nota. Nao reabre.
- **replay do mesmo voto** (mesmo operador, mesmo resultado) = `JA_DECIDIDO`, nada reescrito —
  idempotencia para quem clica duas vezes.
- **voto diferente** = `CONFLITO_DE_VOTO` (RECUSADA). Para mudar de ideia existe o `--desfazer`
  explicito, com operador e motivo.

## 6. Expiracao por TTL

`--expirar` marca `EXPIRED` os `PENDING` com `requested_at < NOW() - INTERVAL '<ttl> hours'`
(TTL declarado na politica; hoje 72h), com `decision_notes = EXPIRADO_POR_TTL:<n>h` e
**`decided_by` VAZIO** — expiracao e transicao automatica, nao ato humano, e a auditoria nao pode
inventar operador. O `UPDATE` e condicional (`WHERE status = PENDING`), entao pedido decidido no meio
do caminho nao e afetado.

## 7. Notificacao (fila)

`--fila` le os `PENDING`, monta a mensagem pelo template e **notifica uma vez por (pedido, texto)**:

- o registro de auditoria (`output.notificados`) guarda `approval_id` + `texto_hash`;
- a rodada seguinte reconstroi o mapa de idempotencia e so considera **novo** o que mudou
  (`JA_NOTIFICADO` quando nada e novo, `notificados: []`);
- **fail-closed**: se a leitura das notificacoes anteriores falhar, a rodada **RECUSA** — renotificar
  o mesmo pedido em loop e pior do que nao notificar.

A mensagem sai com codigo curto `APR-<8 hex>`, empresa, contato, acao, canal, assunto/corpo/CTA e os
**tres comandos** prontos (aprovar / editar / rejeitar). Nenhum `{{MARCADOR}}` fica pendurado.

## 8. Portao do envio (para o W6-E04)

`--consultar <id>` responde `pode_enviar` **somente** quando as tres condicoes valem:

1. `status = APPROVED`;
2. `hash_do_texto(texto_atual) == decisao.texto_hash` (ninguem mexeu no texto depois da aprovacao);
3. contato **ainda** limpo (`do_not_contact`/`opt_out_*`).

Devolve tambem destinatario, canal, `revisado`, quem decidiu, quando e a nota. Qualquer outro estado
sai `pode_enviar: false` com o motivo. Este e o unico caminho pelo qual o envio pode sair.

## 9. Guarda de escrita e desfazer

- Toda escrita passa por `validar_sql`: DDL (`CREATE/ALTER/DROP/TRUNCATE/GRANT/REVOKE`) RECUSA;
  `INSERT/UPDATE/DELETE` fora de `human_approvals`/`agent_runs` RECUSA; `DELETE` so com
  `--desfazer <correlation_id> --confirmo`.
- `--desfazer <correlation_id>`: sem `--confirmo` e **dry-run** (`seriam_revertidos`); com
  `--confirmo --por <operador> --motivo <texto>` devolve os pedidos daquela rodada a `PENDING`,
  limpa `decided_at`/`decided_by`, restaura o **texto original** da edicao e grava
  `REVERTIDO_POR:<operador>:<motivo>` em `decision_notes`. A auditoria da rodada original **fica**.

## 10. CLI

```
--planejar                     # intencao, sem conexao: o que le, o que escreve, quem autoriza
--regras                       # regras efetivas da politica carregada
--fila [--notificacoes arq]    # le os pedidos aguardando decisao e monta a notificacao
--decidir <id> --decisao <verbo> --por <operador> [--nota t] [--edicao arq.json]
--decisoes arq.jsonl [--confirmo]   # lote: dry-run ate --confirmo
--expirar                      # TTL
--consultar <id>               # portao para o envio (W6-E04)
--desfazer <correlation_id> [--confirmo --por --motivo]
```
Exit: `0` ok · `1` recusa de decisao (com motivo) · `2` uso · `3` politica recusada · `4` ambiente
`prod` recusado sem tocar no banco. `--ambiente prod` e a **regra de ambiente do ADR-005**: nada
nasce em producao por este caminho.

## 11. Medicao (o que foi executado, de verdade)

**Suite offline** (`scripts/agentes/verificar_fluxo_aprovacao.py`, sem banco e sem rede):
**79 OK / 0 falhas** + **autoteste 20/20** (cada mutacao injetada tem de reprovar o item esperado).
Grupos: A (politica/contrato) · B (operador) · C (o que nao pode estar no codigo) · D (guarda de
escrita) · E (notificacao) · F (idempotencia/hash) · G (expiracao) · H (CLI) · I (contrato do
componente).

**Aceite E2E** (`scripts/agentes/teste_fluxo_aprovacao_aceite.sh`): PostgreSQL descartavel na VPS
(`pg-aprovacao-acc`, removido pelo proprio aceite; `pg-sales-dev`/`pg-odoo-dev`/`odoo-dev`/
`proxy-dev` intactos) com a migration `0001`. A cadeia e a **real**: o **gerador do card irmao**
cria os pedidos `PENDING` e este componente os decide. **ACEITE_APROVACAO_001_OK (104 OK / 0 FALHOU)**:

| item | o que mede |
|---|---|
| A1 | fila notifica com codigo curto, empresa, contato, texto e os tres comandos; 2a rodada nao renotifica (0 novos) |
| A2 | aprovar grava status/operador/data/nota + hash do texto; `entrada_hash` do gerador intacto |
| A3 | replay = `JA_DECIDIDO` (nada reescrito) · voto diferente = `CONFLITO_DE_VOTO` (auditado) |
| A4 | portao libera o aprovado (hash confere) e NEGA o `PENDING` |
| A5 | rejeitar fecha o pedido e ele nao reabre |
| A6 | contato que virou opt-out **depois** do pedido RECUSA na aprovacao, sem escrever |
| A7 | operador ausente/nao autorizado/maquina RECUSAM (exit 3) e nada e escrito |
| A8 | editar com fato inventado RECUSA sem gravar; editar valido aprova preservando o original |
| A9 | recomendacao fora de `OPEN` RECUSA |
| A10 | `--expirar` marca o vencido, `decided_by` vazio, os novos ficam, rodada auditada |
| A11 | pedido `EXPIRED` nao aceita aprovacao nem rejeicao |
| A12 | `prod` exit 4 · `--planejar` exit 0 · guarda de escrita recusa DDL e escrita fora das duas tabelas |
| A13 | `--desfazer` dry-run x `--confirmo` (motivo obrigatorio), reabre o pedido, preserva auditoria |
| A14 | nada fora das duas tabelas em rodada nenhuma; nenhum envio e nenhum evento de outbox |

**Prova de dente (4/4)**: desligar a guarda de contato reprova A6; desligar a validacao da edicao
reprova A8; desligar a transicao por estado reprova A11; desligar o replay idempotente reprova A3.

**Defeito real encontrado e corrigido pelo aceite**: `json_agg(output->'notificados')` produzia
**lista de listas** (uma por rodada) e o mapa de idempotencia ficava vazio — a segunda `--fila`
renotificava os 5 pedidos (`NOTIFICADO`, 5 novos). Correcao: `jsonb_array_elements(...)` no `FROM`
+ `_achatar()` defensivo + **fail-closed** na leitura. A suite offline ganhou C6 e o autoteste, a
mutacao D21 que reprova esse item.

## 12. Limites conhecidos

- A notificacao **grava a mensagem em arquivo** (`--notificacoes`); o transporte (Telegram/e-mail)
  e do W6-E04/harness. O componente nao fala com o operador por conta propria.
- O TTL e avaliado **na rodada** (`--expirar`), nao por agendador proprio: quem chama decide a
  cadencia (o cron do harness).
- `--decisoes` aplica lote em um unico `correlation_id`: o `--desfazer` do lote reverte o lote
  inteiro, nao item a item.
