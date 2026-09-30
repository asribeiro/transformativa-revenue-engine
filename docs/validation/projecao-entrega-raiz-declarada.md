# Raiz do registro de entregas é DECLARADA — relatório de evidência

**Card:** TRE-W0-E04-T11 (`t_b74c2edd`) · **Base obrigatória:** `d6e510c` (develop) · **Execução:**
30/09/2026 (UTC) · **Perfil executor:** `desenvolvedor`
**Board medido:** `transformativa-revenue-engine` (`/opt/data/kanban/boards/transformativa-revenue-engine/kanban.db`,
aberto em modo leitura) · **Código medido:** o `plugin_api.py` **em uso** pelo dashboard
(`/opt/data/plugins/kanban/dashboard/plugin_api.py`, fonte resolvida = `user`).

Artefatos versionados por este card (sha256 completo):

| Artefato | sha256 |
|---|---|
| `scripts/verificar_projecao_entrega.py` (suíte) | `5dada54b68ba167976978d93e977b348775d21a75a27eefcae4d48756a461260` |
| `deploy/hermes/projecao-entrega/editar_plugin_api_delivery_root.py` (editor ancorado) | `fd02ab6eea72a1242976207cfe787a06a712868923b3f6305a37a4bcfa16131e` |
| `deploy/hermes/projecao-entrega/plugin_api.patch` (delta) | `4aae6e24d8d41a63525b369a595be870556934acf8c981537b3a2225156e004a` |
| `deploy/hermes/projecao-entrega/aplicar_projecao_entrega.sh` (casca do operador) | `0ddef020c9138d1f5a46d388633ff0c40397451f328d5d05676a14d92edd76c1` |
| `deploy/hermes/projecao-entrega/baseline/plugin_api.pristina.py` (baseline do patch) | `a0d99603463b9188a8cf67adacbfcabad1c8d2d40a02f7c5d81cf4a6674472ef` |
| `deploy/hermes/projecao-entrega/README.md` | `12e5e0aee0d8811685a1d6a305539b980db297c8dec10b020f6576bbea445034` |

Resultado do conserto aplicado (cópia em uso): `sha256 cef4412392c89bdcf51cf4500bb19dea8c4b2e4930c33a3e89d50b58f25eacba`.

Toda evidência abaixo é saída bruta de comando, com `exit code`. Nenhum número foi estimado.

Leitura combinada do gate JEV neste card: `hermes/jev/receipts/t_b74c2edd--dec-*.json`
(a ação `ajuste_de_texto` foi declarada no catálogo pelo dono em 30/09 — commit `9a2b64c`; antes
disso o gate reteve o card, e os recibos `ESCALATE` ficaram registrados).

---

## 1. Critério de aceitação → teste → evidência

| Critério (card) | Teste que prova / reprova | Evidência | Veredito |
|---|---|---|---|
| Raiz do registro de entregas **declarada** (config explícita), sem caminho literal no caminho de decisão | Suíte `A`: invariante "1 único literal, e só na constante legada documentada"; precedência medida (board.json > env > legado) | §3 e §5 | **OK** |
| Sem configuração, manter `/workspace/financial-dash` | Suíte `B`: com env vazia e sem board declarado, a raiz legada é usada e o resultado é o de hoje (44 ids) | §4 | **OK** |
| Documentar no código por que a raiz existe e o que a substitui | Bloco `# --- RAIZ DO REGISTRO DE ENTREGAS (DECLARADA)` no código + `deploy/hermes/projecao-entrega/README.md` | §7.1 | **OK** |
| Com a raiz apontada para o repo do TRE: os 14 itens do `W0-governanca-e-baseline.json` produzem os **16 ids terminais** | Suíte `B`: 14 itens ⇒ 16 ids; interseção terminais ⊇ os 16; total = 16 | §3.1 | **OK** |
| Com a raiz apontada para o repo do TRE: **nenhum** card `done` com pai do board projeta `validation` (medido: 0 de 12 anteriores) | Suíte `B`: 0 de 12, 0 de 16, 0 no board inteiro (39 done) | §3.2 | **OK** |
| Com a raiz padrão (`/workspace/financial-dash`): resultado **idêntico ao de hoje** (44 ids = 19 + 9 + 16) | Suíte `B`: 44 ids; decomposição 19 (produção) + 37 (item DONE, que inclui os 16) ⇒ união 44; 0 projetando `validation` | §4 | **OK** |
| Guardrail: **fail-closed intacto** (ausência de evidência ⇒ `validation`; nada de heurística nova) | Suíte `C`: item sem DONE ⇒ filho não terminal; item DONE com filho pendente ⇒ filho não terminal; raiz sem `deliveries/` ⇒ nada terminal; mutação fail-open é reprovada | §5 | **OK** |
| Guardrail: **semântica por item inalterada** (DONE ⇒ filhos terminais; VALIDATION/CANDIDATE/HUMAN_APPROVAL ⇒ filhos projetados) | Suíte `C`: artefato sintético com um item em cada estágio; resultado **idêntico** ao da baseline pristina no mesmo artefato | §5 | **OK** |
| Guardrail: **projeção é APRESENTAÇÃO** (o status nativo do card não pode ser tocado) | Suíte `C`: status nativo de todos os cards idêntico antes/depois; a suíte abre o board em `mode=ro` e a escrita é recusada pelo SQLite | §5 | **OK** |
| Entrega = **patch versionado** no repo + **uma linha** para o operador | `deploy/hermes/projecao-entrega/` (patch + editor + casca) e §7.2 | §7 | **OK** |

---

## 2. O achado, reproduzido (prova causal)

Com o código de **caminho fixo** (baseline pristina) e a raiz do outro projeto **sem** o artefato do
TRE alcançável (a fixture reproduz o estado anterior ao symlink paliativo), o board do TRE fica com
evidência de outro projeto e ZERO do TRE:

```
=== B'. prova causal (o achado se reproduz sem o conserto) ===
OK    codigo com caminho fixo (sem o W0 no caminho): 28 ids terminais  [28]
OK    codigo com caminho fixo (sem o W0 no caminho): 12 cards done em validation  [12]
```

Os 12 são exatamente os do achado de 30/09/2026: `t_25ca689e, t_38cbab9a, t_4be20bcc, t_4f20bd10,
t_6d326367, t_722b6cbd, t_8c332df7, t_b3387f25, t_c8e69f74, t_d8bc83b3, t_e7d9decd, t_e9535df3`.

---

## 3. O conserto medido (raiz = `delivery_repo` do board)

`board.json` do board (declaração que substitui o caminho literal — medido):

```json
{"slug": "transformativa-revenue-engine", "name": "Transformativa Revenue Engine", "description": "", "icon": "", "color": "", "default_workdir": null, "project_id": null, "created_at": 1790693217, "archived": false, "delivery_repo": "/opt/data/repos/transformativa-revenue-engine"}
```

### 3.1 Os 16 ids terminais

```
OK    artefato W0: 14 itens => 16 ids de filhos  [16 ids]
OK    raiz do board (TRE): os 16 ids do artefato W0 sao terminais  [faltando: []]
OK    raiz do board (TRE): 16 ids terminais hoje (o registro do TRE e o W0)  [16]
```

### 3.2 Nenhum card preso

```
OK    raiz do board (TRE): os 12 cards do achado estao terminais  [faltando: []]
OK    raiz do board (TRE): NENHUM dos 12 cards do achado projeta validation  [0 de 12]
OK    raiz do board (TRE): NENHUM dos 16 filhos do W0 projeta validation  [0 de 16]
OK    raiz do board (TRE): 0 cards done projetando validation no aceite  [0 de 39 done: []]
```

O último item é o estado do board **no aceite**. Se um card novo for fechado com pai de dependência
e sem evidência de entrega, o fail-closed o mantém em `validation` e o item acusa — é o
comportamento correto, e a linha diz qual card.

---

## 4. Raiz padrão: idêntico ao de hoje

```
OK    raiz padrao: 44 ids terminais (identico a hoje)  [44]
OK    raiz padrao: 0 cards done projetando validation  [0]
OK    raiz padrao: os 16 do W0 continuam terminais
OK    raiz padrao: decomposicao 19 (producao) + 9 (item DONE do DTV1) + 16 (W0) = 44  [producao=19 item_done=37 uniao=44]
```

Os 19 de produção vêm dos artefatos `V8.14E.json`/`V8.14F.json` (`production_promoted_task_ids`); os
9 "por item DONE" do `DTV1.json`; os 16 do `W0-governanca-e-baseline.json`. Os 37 "item DONE" são a
união dos filhos terminais por item DONE (incluem os 16 do W0 e 8 do `V8.14F` que já estão na lista
de promoção) — a união com os 19 dá 44, como medido.

---

## 5. Guardrails homologados

```
OK    semantica por item inalterada: identico a pristina no mesmo artefato
OK    item DONE => filho terminal
OK    item sem DONE => filho NAO terminal (fail-closed)
OK    item DONE com filho explicitamente pendente => filho NAO terminal (fail-closed por filho)
OK    item VALIDATION => filho projetado em validation
OK    item CANDIDATE => filho projetado em candidate
OK    item HUMAN_APPROVAL (aprovacao pendente) => filho projetado em human_approval
OK    raiz sem control-plane/deliveries => nenhuma evidencia terminal
OK    precedencia: board.json declarado vence a env
OK    precedencia: env vence o legado (board nao informado)
OK    alias HERMES_DELIVERY_REPO (o nome que rodou em producao) honrado
OK    sem configuracao => raiz legada (/workspace/financial-dash)
OK    projecao e apresentacao: status nativo identico antes/depois
OK    verificador abre o board em modo leitura (escrita recusada)  [attempt to write a readonly database]
```

---

## 6. Autoteste por mutação (o verificador tem de reprovar)

```
OK    mutacao muda o arquivo: raiz nao resolvida por board (o outro projeto decide)
OK    verificador REPROVA a mutacao: raiz nao resolvida por board (o outro projeto decide)  [12 em validation; 12 dos 12 do achado]
OK    mutacao muda o arquivo: literal de volta dentro do helper
OK    verificador REPROVA a mutacao: literal de volta dentro do helper  [2 literais, literal no helper=sim]
OK    mutacao muda o arquivo: fail-open: filho sem evidencia vira terminal
OK    verificador REPROVA a mutacao: fail-open: filho sem evidencia vira terminal  [filho explicitamente pendente virou terminal]
OK    mutacao muda o arquivo: env ignorado (override de processo deixa de existir)
OK    verificador REPROVA a mutacao: env ignorado (override de processo deixa de existir)  [raiz da env deixou de ser lida]
```

E o editor ancorado, em memória, fecha o ciclo (aplicar → conferir → compilar → reverter → conferir):

```
autoteste: 16/16 itens OK, 0 falhas
```

---

## 7. A cópia em uso e a linha do operador

### 7.1 O que roda está versionado

Achado de campo do card: a cópia `user` do plugin já trazia, desde 29/09, um override equivalente
escrito **fora do repo** (marker `# --- OVERRIDE ASR: repositorio de entrega por board`). O dashboard
resolve `$HERMES_HOME/plugins` (user) **antes** do diretório empacotado — medido:

```
   {'name': 'kanban', 'source': 'user', 'has_api': True, 'version': '1.0.0+asr.1'}
   _dir: /opt/data/plugins/kanban/dashboard
```

Ou seja: consertar só a cópia empacotada **não muda o que o board responde**. O editor reconhece
essa variante legada e a normaliza; a suíte prova isso sobre o **arquivo real** antes de aplicar:

```
OK    applier normaliza a copia em uso para a canonica (byte a byte)
```

Depois de aplicar (casca do operador, sem root — só a cópia gravável):

```
$ bash deploy/hermes/projecao-entrega/aplicar_projecao_entrega.sh
OK    aplicado: /opt/data/plugins/kanban/dashboard/plugin_api.py
      backup: /opt/data/plugins/kanban/dashboard/plugin_api.py.bak-entrega-root-20260930T144027Z
      sha256: cef4412392c89bdcf51cf4500bb19dea8c4b2e4930c33a3e89d50b58f25eacba
PENDENTE (root) sem permissao de escrita: /opt/hermes/plugins/kanban/dashboard/plugin_api.py

RESULTADO: OK nas copias gravaveis; a copia EMPACOTADA ainda depende da linha do operador (root).
exit=2
```

Medição depois (a suíte inteira, `exit 0`):

```
OK    copia em uso (user) ja e a canonica versionada
OK    plugin em uso (copia `user` do dashboard) == canonico versionado  [cef4412392c89bdc]
OK    plugin em uso resolve a raiz por board (nao ha caminho fixo no helper)
...
RESULTADO: 49/49 itens PASS, 0 falhas
```

O patch versionado reproduz a cópia canônica byte a byte (`patch -p1` sobre a baseline ⇒ `sha256`
idêntico), e o editor recusa escrever quando uma âncora não casa (comportamento provado no
autoteste).

### 7.2 Uma linha do operador (root, dentro do container)

```bash
docker exec -u root hermes bash /opt/data/repos/transformativa-revenue-engine/deploy/hermes/projecao-entrega/aplicar_projecao_entrega.sh
```

Cobre a cópia **empacotada** (`/opt/hermes/...`, `644 root:root` — o agente não tem `sudo`). O
reinício do dashboard (`/command/s6-svc -r /run/service/dashboard`, root) é o passo que faz o
processo carregar os bytes canônicos; **não** é necessário para o comportamento, porque o delta é de
*de onde ler* e a cópia em uso desde 29/09 já lia por board — medido: processo do dashboard de
`03:47:17`, arquivo canônico de `14:40:27`.

---

## 8. O que **não** ficou provado (limites, com todas as letras)

1. **Não medi a tela.** Toda a evidência é do backend (funções de projeção + composição de colunas
   replicada a partir de `get_board`). O desenho das colunas no navegador é verificação humana.
2. **Não reiniciei o dashboard** (exige root). O processo em execução mantém o módulo antigo em
   memória; o comportamento é idêntico (medido), mas "o processo carregado == arquivo canônico" só
   vale depois do reinício da linha 7.2.
3. **Não removi o symlink** `/workspace/financial-dash/control-plane/deliveries/W0-governanca-e-baseline.json`
   → repo do TRE. Com o conserto ele é dispensável (a raiz do board é declarada); a remoção é do
   operador, e enquanto ele existir a raiz padrão continua enxergando os 16 do W0 por esse caminho.
   A suíte mede a raiz padrão **com** o symlink (o "idêntico ao de hoje") e a raiz do board **sem**
   depender dele.
4. **Não toquei a cópia empacotada** (`/opt/hermes`, root). Ela segue pristina; o patch/editor estão
   versionados para a linha do operador.
5. **Não escrevi no board.** A suíte abre o SQLite em modo leitura; o item de status nativo
   antes/depois é a prova de que a projeção não escreve.
6. **A remoção do symlink e o reinício ficam como pendência de operação** (não bloqueiam o aceite:
   os números de aceitação foram medidos com o symlink presente, que é o estado de hoje).
