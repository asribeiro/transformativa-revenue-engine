# `deploy/hermes/projecao-entrega/` — a raiz do registro de entregas é DECLARADA

Card de origem: **TRE-W0-E04-T11** (`t_b74c2edd`). Base: `d6e510c` (develop).
Validação: `docs/validation/projecao-entrega-raiz-declarada.md`.

## Por que existe

O dashboard projeta o ciclo de entrega (`validation` → `candidate` → `human_approval` → `done`)
lendo os artefatos do projeto: `<raiz>/control-plane/deliveries/*.json`. Até 29/09/2026 essa raiz era
um **caminho fixo dentro de cada helper** do plugin (`repo_root = Path("/workspace/financial-dash")`,
4 ocorrências) e a composição do board chamava os helpers **sem argumento** — ou seja, a projeção de
entrega do board de um projeto era decidida pelo diretório de **outro** projeto.

Consequência medida em 30/09/2026 (ver a prova causal na suíte): lendo o caminho fixo, o
`control-plane/deliveries/` do repo do TRE não era lido → **12 cards `done`** do board
`transformativa-revenue-engine` ficavam presos na projeção `validation`, **sem que faltasse
evidência**: a evidência estava escrita num diretório que ninguém lia. O paliativo daquele dia foi um
symlink em `/workspace/financial-dash/...` apontando para o repo do TRE; **este card remove a
necessidade do symlink**.

## O conserto

A raiz passa a ser **declarada**, com precedência (a primeira que existir vence):

1. **`delivery_repo` no `board.json`** do board sendo renderizado — é isto que substitui o caminho
   literal: a raiz fica declarada junto do board. No board do TRE:
   `"delivery_repo": "/opt/data/repos/transformativa-revenue-engine"`.
2. **env `HERMES_DELIVERIES_ROOT`** — override de processo (board ainda não declarado, fixtures,
   testes). `HERMES_DELIVERY_REPO` continua aceito: foi o nome do override que rodou entre 29/09 e a
   entrada deste conserto.
3. **`_LEGACY_DELIVERIES_ROOT`** (`/workspace/financial-dash`) — compatibilidade com o único
   consumidor que existia quando a projeção foi escrita. É o **único** caminho literal que sobra, e
   ele é uma constante documentada, não uma decisão escondida num helper.

Não mudou mais nada:

- **fail-closed intacto**: ausência de evidência continua projetando `validation` (nenhuma heurística
  nova para "adivinhar" entrega concluída);
- **semântica por item intacta**: item `DONE` ⇒ filhos terminais; item em
  `VALIDATION`/`CANDIDATE`/`HUMAN_APPROVAL` ⇒ filhos projetados na coluna correspondente; item `DONE`
  com filho explicitamente pendente ⇒ filho **não** terminal;
- **a projeção é APRESENTAÇÃO**: o status nativo do card não é tocado (o plugin só lê o board).

## Arquivos

| Arquivo | Papel |
|---|---|
| `editar_plugin_api_delivery_root.py` | aplica/reverte/normaliza as edições **ancoradas** no `plugin_api.py`; idempotente, backup datado, `py_compile` antes de escrever, `--check` para relatar sem escrever, `--autoteste` para o ciclo completo em memória (16 itens). |
| `aplicar_projecao_entrega.sh` | casca do operador: aplica nas duas cópias que existirem (user e bundled) e reporta por cópia. |
| `plugin_api.patch` | o **delta** versionado (baseline → canônico), para revisão. Não é a via de aplicação. |
| `baseline/plugin_api.pristina.py` | a baseline que este patch atende: `sha256 a0d99603463b9188a8cf67adacbfcabad1c8d2d40a02f7c5d81cf4a6674472ef` (imagem 24/09 + customização da projeção). Cópia fiel de `/opt/hermes/plugins/kanban/dashboard/plugin_api.py` medida em 30/09/2026. |

Canônico (resultado do patch/editor): `sha256 cef4412392c89bdcf51cf4500bb19dea8c4b2e4930c33a3e89d50b58f25eacba`.

Para regerar o `.patch` depois de mexer no editor:

```bash
cp deploy/hermes/projecao-entrega/baseline/plugin_api.pristina.py /tmp/api.py
python3 deploy/hermes/projecao-entrega/editar_plugin_api_delivery_root.py --alvo /tmp/api.py
diff -u deploy/hermes/projecao-entrega/baseline/plugin_api.pristina.py /tmp/api.py
```

## Onde aplicar (importa)

O dashboard resolve plugins nesta ordem: `$HERMES_HOME/plugins` (**user**) antes do diretório
empacotado (**bundled**). Medido em 30/09/2026:

| Cópia | Caminho | Fonte carregada | Quem escreve |
|---|---|---|---|
| user | `/opt/data/plugins/kanban/dashboard/plugin_api.py` | **sim** (é a que o dashboard carrega) | o agente (sem root) |
| bundled | `/opt/hermes/plugins/kanban/dashboard/plugin_api.py` | não, enquanto a user existir | só o operador (root) |

Ou seja: consertar só a cópia empacotada **não muda o que o board responde** — por isso a via
principal é a cópia `user`, e a `bundled` fica coberta pela linha do operador (necessária se a cópia
`user` for removida ou reinstalada).

## Operação

```bash
# 1. aplicar / conferir / reverter nas cópias graváveis (agente, sem root)
bash deploy/hermes/projecao-entrega/aplicar_projecao_entrega.sh
bash deploy/hermes/projecao-entrega/aplicar_projecao_entrega.sh --check
bash deploy/hermes/projecao-entrega/aplicar_projecao_entrega.sh --reverter

# 2. UMA LINHA DO OPERADOR (root, dentro do container) — cobre a cópia EMPACOTADA
docker exec -u root hermes bash /opt/data/repos/transformativa-revenue-engine/deploy/hermes/projecao-entrega/aplicar_projecao_entrega.sh
```

Códigos de saída do `aplicar_projecao_entrega.sh`: `0` tudo canônico; `2` ok nas graváveis, mas há
cópia pendente de root; `1` âncora ausente / erro de escrita (o plugin mudou de forma — revisar o
patch, **não** adivinhar).

Depois de aplicar na cópia `user`, o processo do dashboard em execução mantém o módulo antigo **em
memória** até reiniciar (`/command/s6-svc -r /run/service/dashboard`, root). Neste conserto isso não
é urgente: o delta é de *de onde ler*, e a cópia em uso desde 29/09 já lia por board — o que muda em
relação a ela é a forma canônica (override de env com o nome declarado, constante legada
documentada). O reinício é o que torna "o que roda" == "o que está versionado" byte a byte.

## Numa troca de imagem do Hermes

A imagem apaga `/opt/hermes`; a cópia `user` (`/opt/data`) sobrevive. Se a cópia `user` também for
reinstalada:

1. reaplique primeiro a **projeção de entrega** (a customização que já vinha na imagem, ver skill
   `kanban-plugin-update`);
2. depois este delta, com o comando acima — o editor exige cada âncora **exatamente 1x** e **falha
   sem escrever** quando a forma do arquivo mudou.

Se uma âncora não for encontrada, a mensagem diz qual e quantas vezes apareceu
(`... aparece 0x (esperado 4x) — o kernel mudou de forma; NÃO editei`). É o comportamento certo:
atualização que mexa nesses trechos exige revisar o patch aqui.

## Suíte

```bash
/opt/hermes/.venv/bin/python scripts/verificar_projecao_entrega.py            # mede o board real
/opt/hermes/.venv/bin/python scripts/verificar_projecao_entrega.py --manter   # mantém o tmp
```

A suíte reproduz a baseline por sha256, aplica o editor/patch numa cópia temporária (não toca em
`/opt/hermes`), mede o **board real** em modo leitura e fecha 47 itens: conserto (A), evidência de
aceitação (B), prova causal do achado (B'), guardrails homologados (C) e autoteste por mutação (D).
Nenhum worker de verdade nasce; nada é escrito no board.

## Achado de campo (30/09/2026): o conserto já existia solto, em runtime

A cópia `user` do plugin já trazia, desde 29/09, um override equivalente (`# --- OVERRIDE ASR:
repositorio de entrega por board`) escrito **fora do repo** — funcionava, mas não estava versionado,
e a projeção continuava frágil (env com nome provisório, caminho legado como literal solto no
resolvedor). Este card **normaliza** essa variante para a forma canônica: é por isso que a suíte tem
o item "applier normaliza a cópia em uso para a canônica (byte a byte)", medido sobre o arquivo real,
e depois "plugin em uso == canônico versionado" (sha256).
