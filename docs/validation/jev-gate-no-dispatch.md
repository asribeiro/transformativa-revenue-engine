# Encaixe do gate JEV no ciclo do board (card TRE-W0-E04-T05)

**Card:** `TRE-W0-E04-T05` (`t_6d326367`) · **Defeito de origem:** `TRE-W0-E04-T02-D06` (fechado pelo `T07`) ·
**Data:** 29/09/2026 · **Status:** encaixe implementado, ativado na configuração e **provado por suite**;
falta a metade que exige root (texto na seção 6).

---

## 1. O que faltava

O roteador JEV funcionava por CLI/API: quem chamava recebia o recibo e a lane. O board **não chamava
ninguém**: um card podia ser reivindicado e executado sem passar pelo JEV. Faltava o encaixe no ciclo —
um ponto que roda **antes de reivindicar ou despachar** o card.

## 2. O que foi entregue

| Peça | Onde vive | O que faz |
|---|---|---|
| `hermes/jev/gate/gate_jev.py` | repo TRE (versionado) | **o hook**: lê o card no board, resolve o **código canônico** da ação, chama o roteador, **grava o recibo (13 campos)** e responde 0 = executa / 2 = escala / 3 = bloqueia / 1 = falha |
| `hermes/jev/acoes-declaradas.yaml` | repo TRE (versionado) | **declaração do código por card** (`card_id` → `acao_codigo`). Sem declaração, o card **não executa** (fail-closed) — e o cabeçalho do arquivo ensina a liberar em uma linha |
| `deploy/hermes/kanban_jev_gate.py` | instalado como `/opt/hermes/hermes_cli/kanban_jev_gate.py` | adaptador: lê `kanban.jev_gate`, roda o hook em subprocesso, trata timeout/erro como **abstenção** e registra o evento no card |
| `deploy/hermes/editar_core_do_gate.py` | repo TRE (versionado) | aplica/reverte as **7 edições ancoradas** no kernel, de forma idempotente, com backup datado e `--check` |
| `scripts/instalar_gate_jev.sh` / `remover_gate_jev.sh` | repo TRE | liga/desliga o encaixe pela configuração (uma metade que **não** precisa de root) |
| `scripts/verificar_gate_jev.py` | repo TRE | a suite (28 itens) |

### Os dois pontos de estrangulamento

1. **`kanban_db.claim_task`** — o claim, por onde passam **todos** os caminhos de execução (o despachante
   chama `claim_task`; o `hermes kanban claim` manual chama `claim_task`). Fica **fora** do `write_txn`:
   o hook roda em subprocesso e não pode segurar o lock de escrita do board.
2. **`kanban_db_dispatch._dispatch_lane_task`** — antes de reservar a vaga de spawn. Segura o card
   *sozinho* (provado em S6c) e o reporta no balde nomeado `skipped_jev_gate`.

A consulta roda **só** para card em `ready` (nenhum subprocesso é gasto em card que o claim recusaria de
qualquer forma). O evento no card é gravado **uma vez por `decision_id` distinto**, não uma vez por tick.

## 3. Contrato do encaixe (como homologado no corpo do card)

1. O dispatch **passa o código canônico** da ação (`acao_codigo`) para o roteador; sem código conhecido a
   ação **não executa** — escala, fail-closed. Isso é o comportamento correto, não defeito do encaixe.
2. **Não afrouxar o D07** para fazer o encaixe funcionar. A postura estrita foi homologada pelo Anderson
   em 29/09/2026 e o caminho para um código que não existe é **nomear um código comum novo** no roteador
   (auditável e reversível), nunca voltar a casar prosa.
3. O encaixe **não** alterou o roteador: nenhum limiar, lane, guardrail, política congelada ou recibo
   mudou. `git diff` da política e do roteador: **vazio**.
4. Não é bloqueio geral: card com código comum declarado **executa** (S3/S3b).

## 4. Código canônico: onde ele está declarado

O adaptador não decide nada por conta própria: ele passa o `acao_codigo` **declarado para o card** em
`hermes/jev/acoes-declaradas.yaml` (arquivo versionado, com `declarado_por` / `declarado_em` / `motivo`
para auditoria). O arquivo nasce **vazio**, de propósito:

- **vazio = fail-closed.** Nenhum card tem código canônico hoje (o corpus anotado mostra 12 ações
  distintas, e o roteador conhece 2 delas — `scripts/analisar_impacto_de_afrouxar.py`);
- **liberar um card é uma linha** no YAML, reversível apagando a linha;
- declarar código **proibido** não libera: o recibo sai `BLOCK` com `exige_aprovacao_humana: true` (S4).

O ponto **operacional honesto**: com o encaixe ligado, um card promovido **sem** código declarado não
despacha. Não é silencioso — o tick reporta `skipped_jev_gate`, o card recebe o evento
`jev_gate_rejected` com o motivo e o recibo, e `hermes kanban tail <id>` mostra tudo. A decisão de
*quais* códigos passar a nomear (por exemplo `execucao_de_card`, que cobriria os 20 cards reais do corpus)
é do dono e ficou **reservada por ele** ("manter estrito agora e revisitar só depois do T05") — este card
entrega o encaixe e o caminho para liberar, e **não** mexeu no critério.

## 5. Prova — `scripts/verificar_gate_jev.py` → `PASS (28 itens, 0 falhas)`

A suite roda o **kernel real** sem escrever em `/opt/hermes` (que é do root): monta um overlay de
`hermes_cli/` em diretório temporário (symlink para tudo, cópia real dos módulos editados, com as edições
aplicadas) e leva `PYTHONPATH=<overlay>` nos subprocessos. O board de teste vive em `HERMES_KANBAN_HOME`
temporário e o `hermes` que o despachante spawna é um **binário falso** que registra a linha de comando —
nenhum worker de verdade nasce.

```
PASS   1  S1 dispatch: card sem codigo NAO sobe worker (nenhum spawn)
PASS   2  S1 dispatch: card continua em `ready` (nao executa, nao some)
PASS   3  S1 dispatch: o tick reporta o card no balde nomeado `skipped_jev_gate`
PASS   4  S1 dispatch: evento `jev_gate_rejected` gravado com motivo e recibo
PASS   5  S1 dispatch: recibo gravado (13 campos) mesmo na recusa
PASS   6  S2 claim manual: `hermes kanban claim` RECUSA o card retido
PASS   7  S2 claim manual: evento `jev_gate_rejected` gravado
PASS   8  S3 dispatch: card COM codigo canonico comum EXECUTA (spawn acontece)
PASS   9  S3 dispatch: evento `jev_gate_allowed` gravado
PASS  10  S3b claim manual: card COM codigo canonico e reivindicado
PASS  11  S4: codigo PROIBIDO declarado nao executa
PASS  12  S4: recibo marca outcome BLOCK e exige_aprovacao_humana
PASS  13  S5 PROVA NEGATIVA: sem encaixe (HERMES_JEV_GATE=off) o card EXECUTA
PASS  14  S5 PROVA NEGATIVA: nenhum evento do gate sem encaixe
PASS  15  S6 MUTACAO: encaixe ligado + kernel sem a edicao => card EXECUTA (o codigo do ponto de estrangulamento e o que segura)
PASS  16  S6b MUTACAO: revertendo SO a edicao de `claim_task`, o claim manual volta a funcionar (a edicao e a trava do caminho manual)
PASS  17  S6b MUTACAO: com o claim_task livre, o DESPACHANTE ainda segura o card (a edicao de `_dispatch_lane_task` vale sozinha)
PASS  18  S6b: o tick reporta o card no balde nomeado `skipped_jev_gate`
PASS  19  S6c MUTACAO: revertendo SO a edicao do despachante, o claim manual continua RECUSADO (a edicao de `claim_task` vale sozinha)
PASS  20  S7 fail-closed: comando do gate inexistente NAO executa
PASS  21  S7 fail-closed: outcome GATE_INDISPONIVEL registrado
PASS  22  S7b fail-closed: saida fora do contrato NAO executa
PASS  23  S7c fail-closed: timeout do gate (1s) NAO executa — abstencao nao e permissao
PASS  24  S8 escopo: fora de `jev_gate_boards` o encaixe NAO interfere (card executa)
PASS  25  S9 recibo: consulta ao gate SEMPRE deixa registro (recibo de 13 campos ou registro explicito de falha do encaixe), em todos os 8 cenarios
PASS  26  S9 recibo: 6 recibos, todos com os 13 campos exatos do contrato da politica (nenhum campo a mais, nenhum a menos)
PASS  27  S9 recibo: nenhum segredo no recibo
PULADO    S10 adaptador instalado em /opt/hermes == versionado no repo  [ainda nao instalado — precisa de root]
PASS  28  S10 `codigos_validos` do acoes-declaradas.yaml == CODIGOS_DE_ACAO_COMUNS do roteador
```

Três provas que respondem direto ao critério de aceitação do card:

- **"card que o roteador bloqueia não executa por nenhum caminho"** → S1 (dispatch: nenhum spawn),
  S2 (claim manual recusado), S6b/S6c (cada ponto de estrangulamento segura sozinho).
- **"recibo gravado sempre, com os 13 campos"** → S1/S3/S4/S9: 6 recibos conferidos campo a campo contra
  `recibo.campos` da política; e quando o **próprio gate não roda** (comando ausente, saída fora do
  contrato, timeout), o adaptador grava um `FALHA-DO-GATE.json` que **declara não ser recibo de decisão** —
  falha nunca vira silêncio nem permissão.
- **"remover o hook faz o card voltar a executar"** → S5 (config desligada: o **mesmo** card despacha) e
  S6 (com o encaixe ligado, kernel sem a edição: despacha). As duas direções da prova.

## 6. Limitações honestas

1. **A metade que precisa de root ainda não está aplicada.** `/opt/hermes` é do root e o agente não tem
   `sudo` (skill `vps-environment`). O que o agente conseguiu aplicar sozinho: o **hook** (versionado no
   repo) e a **configuração** (`kanban.jev_gate`, `kanban.jev_gate_boards=[transformativa-revenue-engine]`,
   `kanban.jev_gate_timeout=60`). O **código do ponto de estrangulamento** espera uma linha do operador:

   ```
   bash /opt/data/repos/transformativa-revenue-engine/deploy/hermes/aplicar_gate_jev.sh
   ```

   Até lá o encaixe está **inerte** (nada lê a config) e o board segue como antes — ou seja, a prova
   acima vale para o código entregue, não para o container em execução.
2. **O despachante em execução só passa a aplicar o encaixe depois de reiniciar.** O gateway
   (`hermes gateway run --replace`, dono do lock de dispatch) tem o kernel antigo carregado em memória.
   Reiniciá-lo é ação do operador (o agente não derruba serviço de produção sem pedir), e o instalador
   avisa isso.
3. **Card `ready` sem código canônico não despacha** — fail-closed de propósito. O aviso está visível no
   tick (`skipped_jev_gate`), no card (`jev_gate_rejected`) e no recibo. Nesta data o board tem
   **0 cards `ready` com despacho autorizado**: a instalação do encaixe não travou nada em execução.
4. **`claim_review_task` (lane `review`) não é consultado** — o encaixe cobre o claim/despacho de card em
   `ready`, que é a execução do card. O despacho de review **é** coberto pelo ponto 2. Um card só chega a
   `review` depois de já ter passado pelo encaixe.
5. **A declaração do código mora em arquivo versionado no repo, não no card.** É auditável
   (`declarado_por`/`declarado_em`/`motivo`), reversível e não exigiu mudar o schema do board. O caminho
   alternativo (coluna `acao_codigo` no `tasks` + `--acao-codigo` no `create`) fica registrado como
   evolução, e o adaptador já aceitaria esse campo se ele existir no payload.
6. **Um subprocesso por consulta** (por card `ready` por tick). Cards retidos são reavaliados a cada tick;
   sem código declarado o custo é o mesmo subprocesso repetido. Se virar problema de volume, o caminho é
   cachear por `task_hash` + mtime das declarações.

## 7. Como operar (resumo; runbook em `docs/runbooks/gate-jev-do-dispatch.md`)

```bash
# liberar um card (uma linha no YAML, versionada)
#   hermes/jev/acoes-declaradas.yaml -> declaracoes: [ - card_id: t_xxx / acao_codigo: ajuste_de_texto ]

# conferir o estado do encaixe
bash scripts/instalar_gate_jev.sh --check

# desligar o encaixe (o board volta a despachar como antes)
bash scripts/remover_gate_jev.sh
```

## 8. Rollback

- **Um comando, sem tocar em código:** `bash scripts/remover_gate_jev.sh` (desarma pela configuração).
- **Código:** `bash deploy/hermes/aplicar_gate_jev.sh --reverter` (reverte as 7 edições; backup datado de
  cada arquivo fica ao lado, `.before-gate-jev-<epoch>`).
- **Sem risco de estado:** o encaixe não escreve no board além do evento de auditoria no próprio card
  (o card **não** muda de status ao ser retido; continua `ready` e volta sozinho quando o código for
  declarado — não precisa re-promover).
