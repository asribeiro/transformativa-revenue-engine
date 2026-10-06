# Composição do plugin do dashboard (`plugin_api.py` + `dist/index.js`)

**Por que esta pasta existe.** Em 05/10/2026 a cópia do plugin que o dashboard
carrega (`/opt/data/plugins/kanban/dashboard/`) **não era** o que estava versionado
no repositório. Medido com sha256, o que rodava tinha três diferenças em relação ao
canônico do card TRE-W0-E04-T11 (`cef44123`):

| # | delta | quando apareceu | onde vivia |
|---|-------|-----------------|------------|
| 1 | raiz do registro de entregas passa a ser DECLARADA (`delivery_repo` no board.json > env > legado) | 30/09 | **versionado** (`deploy/hermes/projecao-entrega/`) |
| 2 | filtro de snapshots `_artifact_files` (um `.bak-*.json` com `current_gate: VALIDATION` estacionava 4 cards na coluna de validação para sempre) | 02/10 | **só em runtime** — nenhum card o versionava |
| 3 | coluna `production` ("Em produção (entrega)") | 05/10 | **só em runtime** |
| 4 | frontend `dist/index.js` (relabel pt-BR das colunas + a coluna nova) | ~30/09 e 05/10 | **só em runtime** |

Sem os deltas 2-4, uma troca de imagem do Hermes (ou qualquer atualização do plugin)
devolve o dashboard a um estado em que o conserto do card 1 **não aparece** — foi
exatamente o que produziu o BLOCKED do card em 04/10.

## A cadeia é uma máquina de estados, verificada por sha

```
S0  a0d99603…  baseline empacotado na imagem  (deploy/hermes/projecao-entrega/baseline/plugin_api.pristina.py)
 │  delta 1 — editor ancorado do card T11 (`editar_plugin_api_delivery_root.py`)
S1  cef44123…  canônico do card
 │  delta 2 — `_artifact_files` + `_SNAPSHOT_MARCADORES`
S2  0ed3674f…  = a cópia que o verificador acusava de DIVERGENTE
 │  delta 3 — coluna `production`
S3  63438987…  canônico COMPOSTO == a cópia em uso hoje (provado byte a byte)
```

Cada elo é **verificado pelo sha do resultado**: `_passo()` só age se o sha de origem
casar e exige o sha de destino; se não cair exatamente nele, **nada é gravado**.
Estado desconhecido → **aborta** (não adivinha). Por isso reaplicar é no-op por
construção — não por heurística de texto. (A primeira versão usava a heurística
"destino presente e âncora ausente": num delta cujo destino **contém** a âncora ela dá
falso e a edição se reaplica. Foi assim que a coluna `production` foi duplicada no
arquivo em 05/10/2026, antes desta correção.)

## Como o operador reaplica (troca de imagem)

```bash
bash deploy/hermes/projecao-entrega/aplicar_projecao_entrega.sh --check     # só relata
bash deploy/hermes/projecao-entrega/aplicar_projecao_entrega.sh             # aplica a cadeia inteira
bash deploy/hermes/projecao-entrega/aplicar_projecao_entrega.sh --reverter  # desfaz
```

O script roda a cadeia inteira nos dois alvos: `/opt/data` (cópia `user` — é a que o
dashboard carrega; o agente grava sozinho) e `/opt/hermes` (cópia empacotada; pertence
ao `root`, exige a linha do operador — o script reporta `PENDENTE (root)` e sai com 2).

Ferramentas (uso direto):

```bash
PY=/opt/hermes/.venv/bin/python
$PY deploy/hermes/plugin-composicao/compor_plugin_api.py --alvo <plugin_api.py> [--aplicar|--check|--reverter]
$PY deploy/hermes/plugin-composicao/compor_plugin_api.py --bundle-raiz /opt/data [--aplicar|--check|--reverter]
$PY deploy/hermes/plugin-composicao/compor_plugin_api.py --canonico-destino <arquivo>
$PY deploy/hermes/plugin-composicao/compor_plugin_api.py --autoteste
```

`--autoteste` cobre: sha do canônico, **reaplicação no-op**, desfazer delta 3 (cai em
S2), desfazer 3 e 2 (cai em S1), reaplicar (idêntico) e **estado desconhecido aborta**.

## Frontend

`dist/index.js` é distribuído **sem fonte** no repositório (não há `src/`, não há build
reproduzível daqui). A forma versionada dele é o patch `dist_index.patch`
(`a/plugins/kanban/dashboard/dist/index.js` → aplica com `patch -p1` a partir da raiz
do plugin). O compositor recusa agir se o sha não for **nem o da imagem**
(`5b309b30…`) **nem o canônico** (`3b27d66f…`): bundle de terceiro não se adivinha.

Verificado: imagem + patch = bundle em uso, byte a byte (`3b27d66f…`), `node --check` OK,
e o inverso volta a `5b309b30…`.

## Evidência de aceitação (05/10/2026)

- `scripts/verificar_projecao_entrega.py`: **53/53 PASS, exit 0**, duas passadas com
  log byte-idêntico (`a9193a43…`), bloco de mutação reprovando o verificador quando o
  código é quebrado (4/4).
- O verificador passou a conferir os deltas 2 e 3 também: identidade **byte a byte** da
  cadeia versionada com a cópia em uso, `--autoteste` do compositor e do editor.
- Os números do aceite de 30/09 (14 itens/16 ids/44 terminais/12 cards) foram trocados
  por **invariantes** (todo filho de item DONE é terminal; nenhum projeta `validation`;
  filho de item fora de DONE e nativo `done` segue projetado; a decomposição é
  `produção ∪ item-DONE`; a precedência board > env > legado fecha). Os números medidos
  continuam impressos como `INFO`. Números fixos já tinham apodrecido uma vez: o board
  passou de 170 para 232+ cards e o artefato da W0 de 14 para 32 itens.
