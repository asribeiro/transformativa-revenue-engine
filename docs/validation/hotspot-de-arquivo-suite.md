# Validação — encaixe ARQUIVO QUENTE no despacho do board

Card: **`TRE-W3-E01-T03-D01`** (defeito de processo, retroativo). Regra e limites:
[`docs/kanban/hotspot-de-arquivo.md`](../kanban/hotspot-de-arquivo.md).

A regra: card que declara `HOTSPOT:` no corpo entra em voo sozinho por arquivo quente — no
máximo **um** card por arquivo declarado EM VOO (`running`/`review`); o segundo fica
`ready` (parkeado) e é promovido quando o detentor fechar.

Como reproduzir (não exige root nem a instalação no kernel):

```bash
/opt/hermes/.venv/bin/python scripts/verificar_hotspot_gate.py            # 41 itens
/opt/hermes/.venv/bin/python scripts/verificar_hotspot_gate.py --manter   # guarda o tmp
bash scripts/verificar_estrutura.sh                                       # estrutura do repo
```

O que a suíte monta: um **overlay** do pacote `hermes_cli` em diretório temporário (symlink
para o que não é editado, cópia real dos 3 módulos com o encaixe aplicado — ou desaplicado,
na prova por mutação) e o board de teste em `HERMES_KANBAN_HOME` temporário. O `hermes` que
o despachante spawna é um binário **falso** que dorme: nenhum worker de verdade nasce, e o
PID vivo do detentor é o que mantém o card EM VOO entre dois ticks, como no defeito medido.

## 1. Suíte do encaixe — saída bruta

```
diretorio da suite: /opt/data/profiles/devops/cache/scratch/hotspot-gate-0rthbfaq
PASS   1  A1 forma inline `HOTSPOT: a, b` vira 2 arquivos declarados
PASS   2  A2 forma em lista (`**HOTSPOT:**` + itens com crase) vira 1 arquivo
PASS   3  A3 normalizacao: `./api//motor.py` == `api/motor.py`
PASS   4  A4 token que nao e caminho vai para `ignorados`, nao para `arquivos`
PASS   5  A5 diretorio declarado (`.../api/`) casa arquivo abaixo dele
PASS   6  A6 PROSA medida que so MENCIONA "hotspot" NAO vira declaracao (5 amostras)
PASS   7  A7 corpos REAIS do lote lidos do board (5 cards): nenhum ganha declaracao por engano (o encaixe nao liga sozinho em card antigo)
PASS   8  B1 DEFEITO (encaixe desligado): os DOIS irmaos sobem no MESMO tick
PASS   9  B2 DEFEITO: os dois ficam EM VOO ao mesmo tempo, nos mesmos arquivos
PASS  10  B3 DEFEITO: o tick nao tem balde nenhum para esse caso (nenhum `skipped_hotspot`)
PASS  11  C1 encaixe ligado: o PRIMEIRO card sobe (spawn + running)
PASS  12  C2 encaixe ligado: o SEGUNDO nao sobe (nenhum spawn)
PASS  13  C3 o parqueado continua `ready` — parkeado, nao perdido nem bloqueado
PASS  14  C4 o tick reporta o parqueado no balde nomeado `skipped_hotspot`
PASS  15  C5 o motivo do balde nomeia o DETENTOR e o ARQUIVO
PASS  16  C6 o card parqueado recebe o evento `hotspot_wait` com detentor e arquivo
PASS  17  C7 o card CONTINUA sem subir no tick seguinte (nao ha promocao por espera)
PASS  18  C8 o evento nao se repete a cada tick (uma vez por espera distinta)
PASS  19  C9 o detentor fecha (`complete`)
PASS  20  C10 LIBERA: o parqueado sobe no tick seguinte ao fechamento (sem deadlock)
PASS  21  C11 tres irmaos no mesmo arquivo: sobe UM e os outros dois ficam parkeados
PASS  22  C12 depois do primeiro fechar, sobe EXATAMENTE UM dos dois parqueados
PASS  23  D1 o detentor sobe
PASS  24  D2 SEM FALSO POSITIVO: card sem declaracao NAO e parkeado
PASS  25  D3 a regra e POR ARQUIVO, nao por onda/epico: card com outro arquivo sobe
PASS  26  D4 diretorio declarado (`api/`) casa o arquivo do detentor e e parkeado
PASS  27  D5 fora de `hotspot_gate_boards` (outro-board) nada e parkeado
PASS  28  E1 `hermes kanban claim` manual do parqueado e RECUSADO
PASS  29  E2 o claim manual recusado tambem registra o evento `hotspot_wait`
PASS  30  E3 MUTACAO: encaixe ligado + kernel SEM as edicoes => os dois sobem (o codigo do ponto de estrangulamento e a trava, nao a config)
PASS  31  E4 com o claim_task livre, o DESPACHANTE ainda segura o parqueado (a edicao de `_dispatch_lane_task` vale sozinha)
PASS  32  E5 e o claim manual volta a funcionar com essa edicao revertida (a edicao e mesmo a trava do caminho manual)
PASS  33  E6 detentor em revisao (trabalho ainda nao integrado) tambem segura o arquivo quente: o parqueado continua parkeado
PASS  34  F1 `--check` (raiz viva /opt/hermes/hermes_cli) relata o estado sem alterar o runtime
PASS  35  F2 `--check` cobre as 6 ancoras do encaixe + o adaptador
PULADO    F3 adaptador instalado == versionado no repo  [ainda nao instalado (/opt/hermes/hermes_cli/kanban_hotspot_gate.py) — precisa de root; ver deploy/hermes/aplicar_hotspot.sh]
PASS  36  F4 isolamento: adaptador do overlay e copia real (nao symlink) e igual ao versionado
PASS  37  G1 coexistencia: com as edicoes do ARQUIVO QUENTE aplicadas no kernel, as 6 ancoras do gate JEV continuam reconhecidas pelo editor dele (a idempotencia do outro encaixe nao e quebrada por este)
PASS  38  F5 o editor `--aplicar` (caminho do operador) instala as 6 ancoras + o adaptador
PASS  39  F6 o kernel com o encaixe aplicado COMPILA
PASS  40  F7 `--check` depois de aplicar reporta tudo OK (idempotente, sem reescrever)
PASS  41  F8 ROLLBACK: `--reverter` devolve os 3 modulos BYTE A BYTE ao estado de antes e remove o adaptador (rollback testado, nao prometido)

PASS (41 itens, 0 falhas, 1 pulados)
```

## 2. Ferramenta read-only, estado do deploy e identidade dos artefatos — saída bruta

```
### corpo de exemplo (corpo-exemplo.md)
**TRE-W3-E01-T03 — contact upsert**

HOTSPOT: api/motor.py, api/politica_api.json

ACEITE: a escrita declara identidade e valor fixo da politica da API.

### $ /opt/hermes/.venv/bin/python deploy/hermes/kanban_hotspot_gate.py --corpo /opt/data/profiles/devops/evidence/t_6c8ad8bb/corpo-exemplo.md
arquivos quentes declarados: 2
  - api/motor.py
  - api/politica_api.json
exit=0

### $ /opt/hermes/.venv/bin/python deploy/hermes/kanban_hotspot_gate.py --card t_cdc21b43 --board transformativa-revenue-engine
card t_cdc21b43 (done) no board transformativa-revenue-engine
  (sem declaracao `HOTSPOT:` — nao e serializado por arquivo)
encaixe: desligado (inerte)
exit=0

### $ bash deploy/hermes/aplicar_hotspot.sh --check
  PENDENTE                 adaptador            /opt/hermes/hermes_cli/kanban_hotspot_gate.py
  PENDENTE (aplicar)       tick_activity        kanban_db.py
  PENDENTE (aplicar)       claim_task           kanban_db.py
  PENDENTE (aplicar)       dispatch_result      kanban_db_dispatch.py
  PENDENTE (aplicar)       dispatch_lane        kanban_db_dispatch.py
  PENDENTE (aplicar)       kanban_ops_json      kanban_ops.py
  PENDENTE (aplicar)       kanban_ops_texto     kanban_ops.py

conferencia final:
    PENDENTE                 adaptador            /opt/hermes/hermes_cli/kanban_hotspot_gate.py
    PENDENTE (aplicar)       tick_activity        kanban_db.py
    PENDENTE (aplicar)       claim_task           kanban_db.py
    PENDENTE (aplicar)       dispatch_result      kanban_db_dispatch.py
    PENDENTE (aplicar)       dispatch_lane        kanban_db_dispatch.py
    PENDENTE (aplicar)       kanban_ops_json      kanban_ops.py
    PENDENTE (aplicar)       kanban_ops_texto     kanban_ops.py
exit=0

### sha256 dos entregaveis (no commit 43e9756; registro em 0bd63a1)
3996d9eccfea81034467815345a8b57441fb12a7921a89490ce3851cee9a545a  deploy/hermes/kanban_hotspot_gate.py
1183ce00c0c37e47797edb9c6653c769229c2d693a94ed50ac2f449121237d75  deploy/hermes/editar_core_do_hotspot.py
265d7da554b545fac7b3004aaec3ec91b1f319f53894daba8bec7c47e1bfdd52  deploy/hermes/aplicar_hotspot.sh
553741b9d851a0fa2ac3f70c9a8b82c4704f60f5ae11b8ff95f4a7a77dd8aafb  scripts/verificar_hotspot_gate.py
086bc7907d78757edd8529ab117d5145af86cdb3e0ac99c5fc1baa44ddf41851  docs/kanban/hotspot-de-arquivo.md

### git log -1 (commit desta entrega)
0bd63a1 Registro: linha datada do encaixe ARQUIVO QUENTE (decisao do dono no defeito TRE-W3-E01-T03-D01)
 docs/operations/registro-de-aprovacoes.md | 1 +
 1 file changed, 1 insertion(+)

### git status --short

### kernel vivo: encaixe instalado em /opt/hermes?
ls: cannot access '/opt/hermes/hermes_cli/kanban_hotspot_gate.py': No such file or directory
```

## 3. Verificador de estrutura do repositório — saída bruta (final)

```
OK    dir  docs/architecture
OK    dir  docs/data
OK    dir  docs/integrations
OK    dir  docs/business
OK    dir  docs/testing
OK    dir  docs/operations
OK    dir  docs/adr
OK    dir  docs/runbooks
OK    dir  docs/releases
OK    dir  docs/kanban
OK    dir  db/migrations
OK    dir  db/tests
OK    dir  odoo/addons/transformativa_sales_ai
OK    dir  n8n/workflows
OK    dir  n8n/contracts
OK    dir  hermes/agents
OK    dir  hermes/prompts
OK    dir  hermes/policies
OK    dir  hermes/jev/routing
OK    dir  hermes/jev/benchmarks
OK    dir  hermes/jev/receipts
OK    dir  scripts
OK    dir  tests
OK    arq  README.md
OK    arq  BRANCHING.md
OK    arq  .gitignore
OK    arq  .env.example
OK    ADRs iniciais (8)
OK    versionado  docs/data/DATA_CONTRACT_V1.md
OK    versionado  docs/data/data_contract_v1.json
OK    versionado  db/migrations/0001_sales_intelligence_v1.sql
OK    versionado  CHANGELOG.md
OK    versionado scripts/backup/backup-tre.sh
OK    versionado scripts/backup/verificar-backup.sh
OK    versionado scripts/backup/restore-tre.sh
OK    versionado scripts/backup/teste-backup-restore.sh
OK    versionado docs/runbooks/backup-restore-rollback.md
OK    versionado deploy/systemd/tre-backup.timer
OK    versionado hermes/jev/policy_v1.yaml
OK    versionado docs/architecture/jev-decision-policy-v1.md
OK    versionado scripts/verificar_jev_policy.py
OK    versionado scripts/dedup/deduplicar_organizacoes.py
OK    versionado scripts/dedup/teste_dedup_sintetico.sh
OK    versionado scripts/dedup/teste_dedup_ambiente.sh
OK    versionado docs/runbooks/deduplicacao-strong-identifiers.md
OK    versionado scripts/dedup/teste_entity_match_confidence.sh
OK    versionado docs/data/entity-match-confidence.md
OK    executavel scripts/dedup/teste_entity_match_confidence.sh
OK    versionado scripts/db/suite_banco.sh
OK    versionado scripts/db/teste_isolamento_clientes.sh
OK    versionado scripts/db/teste_tenant_rls.sh
OK    versionado docs/runbooks/suite-de-teste-do-banco.md
OK    executavel scripts/db/suite_banco.sh
OK    executavel scripts/db/teste_isolamento_clientes.sh
OK    executavel scripts/db/teste_tenant_rls.sh
OK    versionado docs/kanban/processo-de-defeitos.md
OK    versionado scripts/kanban/abrir-defeito.sh
OK    versionado scripts/kanban/fechar-defeito.sh
OK    versionado scripts/kanban/listar-defeitos.sh
OK    executavel scripts/kanban/abrir-defeito.sh
OK    executavel scripts/kanban/fechar-defeito.sh
OK    executavel scripts/kanban/listar-defeitos.sh
---
RESULTADO: PASS (0 falhas)
```

## 4. O que esta validação **não** prova

1. **Não prova a instalação no kernel.** `/opt/hermes` é `root:root` e o agente do Hermes não
   tem `sudo`; a instalação é ato do operador (`bash deploy/hermes/aplicar_hotspot.sh` + reiniciar
   o despachante). O `--check` medido acima mostra exatamente isso: adaptador e as 6 âncoras
   `PENDENTE`. O que está provado é que o **mesmo código** age quando aplicado (overlay) e que
   ele **reverte byte a byte**.
2. **Não prova efeito em card que não declara `HOTSPOT:`.** O mapa é declarado, não inferido —
   é o desenho do card (a alternativa "um card por épico" foi decidida contra). Os irmãos do
   lote medido (`TRE-W3-E01-T04`, `TRE-W3-E01-T05`) ainda escrevem "hotspot" em prosa: sem o
   marcador, seguem promovidos em paralelo.
3. **Não mede desempenho** do tick com o encaixe ligado (custa uma leitura de `tasks` por
   claim, no próprio board).
