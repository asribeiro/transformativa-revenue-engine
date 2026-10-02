# `deploy/hermes/` — encaixe do gate JEV no kernel do board

Card de origem: **TRE-W0-E04-T05** (`t_6d326367`). Validação: `docs/validation/jev-gate-no-dispatch.md`.

## Por que existe

O board (Hermes kanban) não chamava o roteador JEV antes de executar um card. O encaixe é um **ponto de
estrangulamento** no kernel, por onde passam todos os caminhos de execução, que pergunta ao roteador
"pode executar?" antes de reivindicar ou despachar.

O kernel instalado é `/opt/hermes` — **do root**. O agente do Hermes roda como `hermes` (uid 10000) e
**não tem `sudo`** (ver skill `vps-environment`). Por isso o encaixe é versionado aqui e aplicado por
**uma linha do operador**; a configuração (`kanban.jev_gate`) o agente aplica sozinho por
`scripts/instalar_gate_jev.sh`.

## Arquivos

| Arquivo | Papel |
|---|---|
| `kanban_jev_gate.py` | adaptador instalado como `/opt/hermes/hermes_cli/kanban_jev_gate.py`. Lê a config, roda o hook em subprocesso, trata timeout/erro/saída inválida como **abstenção** (nunca permissão) e registra o evento no card. |
| `editar_core_do_gate.py` | aplica/reverte as **7 edições ancoradas** no kernel. Idempotente, com backup datado por arquivo, `compile()` antes de escrever e `--check` para relatar sem escrever. |
| `aplicar_gate_jev.sh` | casca do operador (root): chama o editor e confere no fim. |

## As 7 edições (todas aditivas)

| # | Arquivo | O que muda |
|---|---|---|
| 1 | `kanban_db.py` | `claim_task`: consulta o gate antes do `write_txn` (fora do lock de escrita) e recusa o claim |
| 2 | `kanban_db.py` | `_TICK_ACTIVITY_FIELDS`: o tick com card retido não é reportado como "idle" |
| 3 | `kanban_db_dispatch.py` | `DispatchResult.skipped_jev_gate` (balde nomeado, acionável pelo operador) |
| 4 | `kanban_db_dispatch.py` | `_dispatch_lane_task`: consulta o gate antes de reservar a vaga de spawn |
| 5 | `kanban_ops.py` | `skipped_jev_gate` na saída `--json` do `hermes kanban dispatch` |
| 6 | `kanban_ops.py` | linha legível "Retido pelo gate JEV: …" na saída humana |
| 7 | `kanban_jev_gate.py` | o adaptador em si (arquivo novo) |

Nada disso toca política do JEV, roteador, schema do board ou os outros boards. **Sem
`kanban.jev_gate` configurado, o adaptador devolve `None` e o board se comporta exatamente como antes** —
é o que permite a prova negativa da suite (`scripts/verificar_gate_jev.py`, cenário S5).

## Operação

```bash
# 1. configurar (agente, sem root)
bash scripts/instalar_gate_jev.sh
bash scripts/instalar_gate_jev.sh --check

# 2. aplicar o código (OPERADOR, root, dentro do container)
bash /opt/data/repos/transformativa-revenue-engine/deploy/hermes/aplicar_gate_jev.sh

# 3. reiniciar o despachante para ele carregar o kernel novo
#    (o gateway em execução mantém o código antigo em memória até reiniciar)

# reverter
bash /opt/data/repos/transformativa-revenue-engine/deploy/hermes/aplicar_gate_jev.sh --reverter
```

## Se uma âncora não for encontrada

`editar_core_do_gate.py` **falha e não escreve nada** (`ancora antiga aparece 0x … o kernel mudou de
forma; NAO editei`). É o comportamento certo: atualização do Hermes que mexa nesses trechos exige revisar
o patch aqui, não adivinhar. O texto de cada âncora está em `EDICOES` no topo do editor.

## Suíte

```bash
/opt/hermes/.venv/bin/python scripts/verificar_gate_jev.py       # 28 itens
/opt/hermes/.venv/bin/python scripts/verificar_gate_jev.py --manter   # mantém o tmp para inspeção
```

A suíte não precisa das edições aplicadas em `/opt/hermes`: ela monta um **overlay** do pacote
`hermes_cli` em diretório temporário (symlink para tudo, cópia real dos módulos editados) e roda o kernel
patcheado por `PYTHONPATH`. O board de teste é temporário (`HERMES_KANBAN_HOME`) e o `hermes` que o
despachante spawna é um binário falso — **nenhum worker de verdade nasce**. Quando o adaptador já está
instalado, a suíte também confere que a cópia instalada é byte a byte a versionada aqui.

---

# Segundo encaixe: ARQUIVO QUENTE (card `TRE-W3-E01-T03-D01`)

Mesmo mecanismo, outro ponto de decisão: aqui o kernel passa a olhar **arquivo** antes de pôr um card em
voo, não só dependência e prioridade. Regra e formato da declaração:
[`docs/kanban/hotspot-de-arquivo.md`](../../docs/kanban/hotspot-de-arquivo.md).

| Arquivo | Papel |
|---|---|
| `kanban_hotspot_gate.py` | adaptador instalado como `/opt/hermes/hermes_cli/kanban_hotspot_gate.py`. Lê o `HOTSPOT:` do corpo, compara os caminhos com os cards em voo e registra `hotspot_wait` (uma vez por espera distinta). |
| `editar_core_do_hotspot.py` | aplica/reverte as **6 edições ancoradas** (`claim_task`, `_dispatch_lane_task`, `DispatchResult`, `_TICK_ACTIVITY_FIELDS`, `kanban_ops` JSON e texto). Idempotente, backup datado, `compile()` antes de escrever, `--check` para relatar. |
| `aplicar_hotspot.sh` | casca do operador (root). |

As âncoras foram escolhidas **fora** das regiões que as 7 edições do gate JEV usam: os dois encaixes
convivem, cada editor continua idempotente e nenhum "acha" que o outro não está aplicado.

```bash
# configurar (o dono liga quando quiser; sem isto o encaixe é inerte)
#   kanban: { hotspot_gate: true, hotspot_gate_boards: [transformativa-revenue-engine] }
# aplicar o código (OPERADOR, root)
bash deploy/hermes/aplicar_hotspot.sh
bash deploy/hermes/aplicar_hotspot.sh --check
bash deploy/hermes/aplicar_hotspot.sh --reverter
# reiniciar o despachante para ele carregar o kernel novo

# suíte (não precisa de root nem da instalação)
/opt/hermes/.venv/bin/python scripts/verificar_hotspot_gate.py     # 41 itens
```

A suíte reproduz o defeito de origem (encaixe desligado: dois irmãos que declaram o mesmo arquivo sobem
no mesmo tick), prova a correção (um por arquivo em voo, liberado quando o detentor fecha), a ausência
de falso positivo nos corpos reais do lote, o caminho único (claim manual também é recusado), a mutação
(kernel sem as edições volta a deixar os dois subirem) e o **rollback** do editor, byte a byte.
