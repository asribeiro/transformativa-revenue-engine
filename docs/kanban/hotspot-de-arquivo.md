# Arquivo quente — o promotor da onda olha ARQUIVO, não só dependência

Origem: defeito de processo `TRE-W3-E01-T03-D01` (retroativo, medido em 02/10/2026 pelo worker do
`TRE-W3-E01-T03`, card `t_e6e3b0b3`; recorrência medida no mesmo dia pelo worker do `TRE-W3-E01-T05`,
card `t_cb615018`).

## 1. O defeito que esta regra corrige

O despacho promovia cards por **dependência e prioridade**, sem **mapa de arquivo quente**. Dois cards
irmãos que editam os MESMOS arquivos subiram no mesmo tick (`TRE-W3-E01-T02` e `TRE-W3-E01-T03`); o
`git status` do worktree do T02 listava os 7 arquivos que o T03 obrigatoriamente toca
(`api/motor.py`, `api/politica_api.json`, `controllers/api_controlada.py`, `tests/__init__.py`,
`tests/test_api_controlada.py`, `scripts/odoo/testar_motor_api.py`, `scripts/odoo/verificar-api-controlada.sh`).

Rodar em paralelo nesse caso só tem duas saídas, ambas ruins: **duplicar** o mecanismo (duas
implementações do mesmo conceito, no mesmo arquivo) ou **divergir** (cada card inventa o seu). Não é
hipótese: os dois irmãos divergiram no esquema da política da API (`campos_de_identidade` em lista
ordenada por prioridade × `campo_de_identidade` singular) — no ponto que é a fonte da verdade da
integração. A mitigação manual (serializar por dependência, o que os dois workers fizeram) vale para
aquele caso e **não impede a recorrência**: cada leva nova no mesmo lote reproduz o quadro, e o custo
cresce com o número de irmãos na mesma leva (na recorrência foram três).

## 2. A regra

> **Card que declara `HOTSPOT:` no corpo entra em voo sozinho por arquivo quente: no máximo UM card por
> arquivo declarado em voo por vez.** O segundo fica em `ready` (parkeado, não perdido nem bloqueado) e é
> reivindicado no tick seguinte ao fechamento do primeiro.

- **Em voo** = `running` ou `review` — trabalho ainda não integrado. `blocked` **não** conta: card parado
  não está editando, e quem serializa card bloqueado é o próprio vínculo de dependência do board.
- **A regra é por arquivo, não por onda/épico**: card do mesmo épico que declara arquivo diferente sobe
  junto normalmente.
- **Dependência continua sendo a ordem dura.** O arquivo quente resolve o caso de irmãos que o grafo de
  dependência não distingue; ele não substitui `kanban_link`.
- **Sem declaração, sem serialização.** O encaixe não adivinha arquivo: ele respeita o que o card
  declara. A declaração é responsabilidade de **quem cria o card** (coordenador), que é quem sabe o ponto
  de contato — é o mesmo `hotspot:` que os cards da W2 já escrevem em prosa desde então, agora com forma
  legível por máquina.

## 3. Como declarar

Em linha própria no corpo do card, com o marcador `HOTSPOT:` seguido dos caminhos — as duas formas são
aceitas:

```markdown
HOTSPOT: api/motor.py, api/politica_api.json
```

```markdown
**HOTSPOT:**
- `api/motor.py`
- `controllers/api_controlada.py`
```

Regras do formato (todas medidas na suíte):

- o marcador precisa **começar** a linha (aceita marcação de markdown em volta: `**HOTSPOT:**`, `> HOTSPOT:`);
- separadores: vírgula, ponto e vírgula ou espaço;
- **diretório** termina em `/` (`odoo/addons/x/api/`) e casa qualquer arquivo abaixo dele;
- normalização: `./` fora e `//` colapsado — `./api//motor.py` é `api/motor.py`;
- token que não tem forma de caminho (uma palavra de prosa, `nenhum`) **não** vira arquivo: ele fica
  registrado como ignorado, para o operador ver o que o card quis dizer em vez de o encaixe adivinhar;
- **prosa que apenas menciona a palavra "hotspot" não declara nada.** Os corpos reais do lote
  (`HOTSPOT — fazer UMA vez…`, `…é hotspot de append…`, `(hotspot: D01/D02…)`) **não** ligam o encaixe —
  é a prova de ausência de falso positivo, item A6/A7 da suíte.

## 4. O que o board faz quando a regra trava

| Onde | O que aparece |
|---|---|
| tick do despachante (`hermes kanban dispatch --json`) | balde nomeado **`skipped_hotspot`** com `{task_id, motivo}`; o motivo nomeia o detentor e o arquivo |
| tick humano | linha `Parkeado por arquivo quente: <card> — <motivo>` (não é "ociosamente ocioso": é acionável) |
| card parqueado | evento **`hotspot_wait`** com `arquivos`, `detentores` (`task_id`, `status`) e `impressao`; um evento por **espera distinta**, não um por tick |
| card parqueado | continua `ready` — o claim manual (`hermes kanban claim`) também é recusado, porque o encaixe está no ponto de estrangulamento por onde passam todos os caminhos de execução |

**Não há deadlock possível:** quem espera é sempre um card que ainda não entrou em voo; o detentor já está
rodando e não espera por ninguém. Card cujo worker morreu é reciclado pelo próprio board no tick seguinte
e deixa de segurar.

## 5. Como conferir (read-only)

```bash
# o que um corpo de card declara (antes de o card entrar no board)
/opt/hermes/.venv/bin/python deploy/hermes/kanban_hotspot_gate.py --corpo corpo-do-card.md

# o que um card existente declara e quem está segurando
/opt/hermes/.venv/bin/python deploy/hermes/kanban_hotspot_gate.py --card t_cdc21b43 --board transformativa-revenue-engine

# quem foi parkeado no último tick
hermes kanban --board transformativa-revenue-engine dispatch --json | python3 -m json.tool | grep -A6 skipped_hotspot
```

## 6. Ligar, desligar, instalar

O código vive versionado no repositório e é **inerte por omissão**: sem `kanban.hotspot_gate` ligado,
nada muda no board.

```yaml
# /opt/data/config.yaml
kanban:
  hotspot_gate: true                                   # ausente/off => inerte
  hotspot_gate_boards: [transformativa-revenue-engine] # ausente = todos os boards
```

```bash
# instalação do código no kernel do board (OPERADOR, root: /opt/hermes é do root)
bash deploy/hermes/aplicar_hotspot.sh            # aplica (idempotente, com backup datado)
bash deploy/hermes/aplicar_hotspot.sh --check    # só relata
bash deploy/hermes/aplicar_hotspot.sh --reverter # desfaz
# depois de aplicar: reiniciar o despachante (o gateway mantém o kernel antigo em memória)
```

Emergência: `HERMES_HOTSPOT_GATE=off` desliga o encaixe sem tocar no config.

Arquivos:

| Arquivo | Papel |
|---|---|
| `deploy/hermes/kanban_hotspot_gate.py` | adaptador (instalado como `<hermes>/hermes_cli/kanban_hotspot_gate.py`); parser da declaração, detentores em voo e registro do evento |
| `deploy/hermes/editar_core_do_hotspot.py` | as 6 edições ancoradas no kernel (idempotentes, `--check`/`--aplicar`/`--reverter`) |
| `deploy/hermes/aplicar_hotspot.sh` | casca do operador (root) |
| `scripts/verificar_hotspot_gate.py` | suíte: reproduz o defeito, prova a correção e o rollback, e a convivência com o gate JEV (41 itens) |

## 7. Limites declarados (o que esta correção **não** faz)

1. **Não infere arquivo.** O mapa é declarado, lido do corpo do card; não há diff prévio nem análise do
   worktree. Card sem `HOTSPOT:` continua promovido como antes — inclusive dois irmãos que se
   atropelariam. Fechar esse buraco exige declarar no card (ou a alternativa barata de "um card por épico
   que compartilha arquivo", que **não** foi implementada: ela serializa demais — o mesmo épico pode ter
   cards sem contato nenhum).
2. **Não substitui dependência**, só a complementa: a ordem dura continua vindo do grafo.
3. **Não bloqueia o card**: ele fica `ready` e volta sozinho quando o detentor fechar. Não gera
   `blocked` nem `triage`.
4. **Espera enquanto a revisão roda.** Detentor em `review` (ou com o revisor rodando) segura o arquivo
   até fechar. É o preço de "trabalho ainda não integrado", e é o que o card do defeito pediu
   ("promovido quando o primeiro fechar").
5. **A instalação no kernel exige root.** Enquanto `deploy/hermes/aplicar_hotspot.sh` não for rodado pelo
   operador, o encaixe existe apenas versionado e não muda o despacho — a suíte declara isso em vez de
   fingir que está instalado.

## 8. Evidência

`scripts/verificar_hotspot_gate.py` — **41 itens PASS, 0 falhas** (1 pulado: a conferência
"adaptador instalado == versionado", que só existe depois da instalação pelo operador).
Saída bruta, identidade dos artefatos e o que a validação **não** prova:
[`docs/validation/hotspot-de-arquivo-suite.md`](../validation/hotspot-de-arquivo-suite.md).

Ela monta um
overlay do kernel em diretório temporário (nunca escreve em `/opt/hermes`), roda o board de verdade em
`HERMES_KANBAN_HOME` temporário e prova, item a item:

- **o defeito, reproduzido:** com o encaixe desligado, dois cards que declaram o mesmo arquivo sobem os
  dois no mesmo tick e ficam em voo juntos (B1–B3) — o quadro medido no card de origem;
- **a correção:** o segundo não sobe, continua `ready`, aparece em `skipped_hotspot` e recebe
  `hotspot_wait` com detentor e arquivo (C1–C8); ao fechar o detentor, o parqueado sobe no tick seguinte
  (C9–C10); com três irmãos, sobe um por vez (C11–C12);
- **sem falso positivo:** card sem declaração, card com outro arquivo e os **corpos reais do lote** lidos
  do board (A6–A7, D2–D3);
- **escopo:** fora de `hotspot_gate_boards` nada é parkeado (D5);
- **caminho único:** o claim manual também é recusado (E1–E2) e a **prova por mutação** desfaz as edições
  do kernel para mostrar os dois subindo de novo (E3–E5) — o código do ponto de estrangulamento é a
  trava, não a configuração;
- **revisão:** detentor em revisão também segura (E6);
- **rollback:** `--aplicar` e `--reverter` do editor devolvem os módulos byte a byte ao estado anterior
  (F1–F8) — rollback testado antes de precisar dele;
- **convivência:** com as edições deste encaixe no kernel, as 6 âncoras do gate JEV continuam sendo
  reconhecidas pelo editor dele (G1) — nenhum dos dois editores acha que o outro não está aplicado.
