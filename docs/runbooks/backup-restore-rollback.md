# Runbook — Backup, Restore e Rollback (TRE)

**Card:** TRE-W0-E01-T03 · **Status:** vigente desde 29/09/2026 · **Responsável:** Dev Harness
**Máquina:** VPS Contabo `vmi3619453` (`169.58.24.102`) · usuário `tre-deploy`

Este runbook é o procedimento **executável** — nada aqui é intenção: os comandos foram rodados
na VPS e o resultado medido está na seção 7.

---

## 1. O que é copiado — e o que **não** é

| Copiado | Não copiado (de propósito) |
|---|---|
| `pg_dump -Fc` do banco de cada ambiente (schema `sales_intelligence`) | `.env` / arquivos de segredo |
| `pg_dumpall --globals-only` (papéis e globais, sem senhas) | senhas, tokens, chaves de API |
| `contagens.txt` (linhas por tabela) e `manifest.txt` (metadados) | dados de tenant diferente do ambiente copiado |
| `sha256` do dump e `pg_dump.err` (vazio em caso de sucesso) | imagens Docker (são reconstruíveis do repositório) |
| **Odoo do ambiente:** `odoo_dev.dump` + `odoo_dev.dump.sha256`, `odoo-contagens.txt` (por tabela) e `odoo-manifest.txt` | `/etc/tre/odoo-dev/*` (senha mestra e senha do banco), `sessions/` do filestore |

**O artefato de um ambiente carrega o ambiente inteiro** (TRE-W2-E01-T01-F01): o Odoo grava no
**mesmo** diretório do dump do trio, com manifesto próprio (`odoo-manifest.txt`). Quem escolhe "o
dump do artefato" pelo manifesto (`banco:`) e **nunca** por `ls *.dump | head -1` — com o Odoo no
mesmo diretório existem **dois** `*.dump` e `odoo_dev.dump` vem primeiro em ordem alfabética
(`verificar-backup.sh`/`restore-tre.sh` já resolvem pelo manifesto; medido em 01/10/2026).

Segredo se recupera do cofre (`docs/operations/gestao-de-secrets.md`), **não** de arquivo de
backup. Um diretório de backup que carrega senha vira um vazamento com data marcada.

## 2. Rotina automática

Instalação (uma vez, com `sudo`): `scripts/backup/instalar-timers.sh`

| Timer | Quando | O que faz |
|---|---|---|
| `tre-backup.timer` | diário **02:30** (America/Sao_Paulo) | `backup-tre.sh todos` — copia dev, homolog e prod |
| `tre-backup-verify.timer` | domingo **04:00** | `verificar-ultimo-backup.sh todos` — **restaura de verdade** o artefato mais recente e compara |

- Artefatos: `/opt/tre/backup/tre_<ambiente>_<YYYYmmddTHHMMSSZ>/` (permissão 700, dono `tre-deploy`).
- Retenção: **14 dias** (`TRE_BACKUP_RETENCAO_DIAS`), aplicada só ao prefixo do próprio ambiente.
  A remoção **confere o exit do `rm`**: artefato que não pôde ser removido é reportado
  (`FALHOU retencao: NAO consegui remover …`, `RESULTADO: BACKUP_FALHOU`) e **não** é contado como
  removido — `rm -rf` sem conferir exit foi o que deixou um diretório de outro dono escapar da
  retenção para sempre enquanto o log dizia "removido(s)" (§7h).
- **Dono do artefato = usuário de serviço, não quem executa** (`TRE_BACKUP_DONO`; padrão
  `tre-deploy` quando o usuário existe na máquina; sem ele, quem executa, com `NOTA`). Rodando como
  `root`, `backup-tre.sh` aplica `chown` no diretório do artefato antes da retenção: uma execução
  manual do operador como `root` entrega o artefato para `tre-deploy` (o `manifest.txt` registra
  `executado_por:` e `dono_artefato:`). Declarar `TRE_BACKUP_DONO` inexistente é **falha** — o
  artefato ilegível pelo timer não pode nascer em silêncio (§7h).
- Configuração: `/etc/tre/backup.env` (caminhos, `TRE_ENV_DIR` e destino — **sem segredo**).
- **Cópia operacional (`/opt/tre/prod/repo`):** instalada **somente** por `deploy/publicar.sh` (um commit por
  vez, com `.publicado` gravando o commit em uso). Nada de `tar`/`scp`/`rsync` direto — runbook
  `publicacao-da-copia-operacional.md`.
- **O trio (container, usuário, banco) é resolvido POR AMBIENTE**, nesta ordem:
  1. `TRE_PG_SERVICO_<AMBIENTE>` / `TRE_PG_USER_<AMBIENTE>` / `TRE_PG_DB_<AMBIENTE>` (ex.: `TRE_PG_SERVICO_DEV`);
  2. `$TRE_ENV_DIR/<ambiente>.env` — par não-secreto versionado (hoje `deploy/environments/dev.env` =
     `pg-sales-dev` / `sales_ai` / `sales_intelligence`; `TRE_ENV_DIR` aponta para
     `/opt/tre/prod/repo/deploy/environments`);
  3. `TRE_PG_SERVICO`/`TRE_PG_USER`/`TRE_PG_DB` globais — **só em chamada de UM ambiente**;
  4. convenção `pg-<ambiente>` / `tre` / `sales_intelligence`.
  A regra vive em `scripts/backup/lib-ambiente.sh` e é a mesma para a rotina e para a verificação.
  **Uma variável global não atravessa `todos`**: um único `TRE_PG_SERVICO` valendo para os três ambientes
  copiaria o banco do dev três vezes, rotulado dev/homolog/prod (a rotina avisa em `NOTA` quando ignora).
- Ambiente **não** declarado e sem container é **pulado**, não falha: um timer cobre os três desde já.
- Ambiente **declarado** (arquivo do ambiente ou variável por ambiente) cujo container não existe é
  **falha** — nunca `BACKUP_OK`. Ambiente provisionado **sem** backup é **falha** na verificação, assim
  como backup com **mais de 48h** (é o sinal de que a rotina parou).
- Zero ambientes cobertos ⇒ `RESULTADO: BACKUP_SEM_AMBIENTE` (nunca `BACKUP_OK` com exit 0).
- **O Odoo é resolvido por ambiente, na mesma ordem** (`scripts/backup/lib-ambiente.sh`,
  `tre_resolver_odoo`): 1. `TRE_ODOO_PG_SERVICO_<AMBIENTE>`, `TRE_ODOO_PG_USER_<AMBIENTE>`,
  `TRE_ODOO_PG_DB_<AMBIENTE>`, `TRE_ODOO_FILESTORE_<AMBIENTE>`, `TRE_ODOO_IMAGEM_<AMBIENTE>`,
  `TRE_ODOO_IMAGEM_DIGEST_<AMBIENTE>`; 2. as mesmas chaves sem sufixo em
  `$TRE_ENV_DIR/<ambiente>.env`; 3. nada (Odoo **não é** uma quarta etapa global — um Odoo único em
  variável global atravessaria os três ambientes em `todos`, a mesma armadilha do trio).
  - Ambiente que **não declara** Odoo ⇒ `PULADO ambiente ... (Odoo nao declarado)` e o manifesto
    grava `odoo: ausente neste ambiente` — a ausência fica **declarada**, não omitida.
  - Ambiente que **declara** Odoo e cujo container não existe (ou cujo dump/filestore não sai)
    ⇒ **falha**; o artefato do trio ainda é gravado (com `odoo: ausente neste ambiente`) e o
    `RESULTADO` é `BACKUP_FALHOU`, nunca `BACKUP_OK`.
  - O filestore sai do **volume** (`odoo-data-dev`), empacotado por container efêmero
    (`docker run --entrypoint tar -v <volume>:/origem:ro`), sem depender do caminho do host.
- Na verificação, ambiente que declara Odoo cujo artefato mais recente **não** tem o bloco do Odoo
  ⇒ `FALHOU backup do ambiente esta pela metade` (o dump do trio existe e o do Odoo não).
- **Artefato ilegível por permissão ≠ artefato pela metade.** Os três verificadores distinguem os
  dois casos: se o diretório do artefato (ou o `manifest.txt`) existe mas não é legível pelo usuário
  que verifica, a saída é `FALHOU … e PERMISSAO, nao conteudo` / `… e PERMISSAO, nao 'artefato sem
  Odoo'` — nunca "backup pela metade" (§7h item 3). O diagnóstico de conteúdo fica reservado para
  defeito de conteúdo: os negativos de conteúdo continuam reprovando (§7h item 5).

## 3. BACKUP — manual

```bash
# um ambiente (o trio vem de $TRE_ENV_DIR/<ambiente>.env; na VPS: /opt/tre/prod/repo/deploy/environments)
scripts/backup/backup-tre.sh dev

# os tres (o que o timer roda)
scripts/backup/backup-tre.sh todos

# apontando outro container/usuario (variavel POR AMBIENTE — nunca uma global para os tres)
TRE_PG_SERVICO_DEV=pg-sales-dev TRE_PG_USER_DEV=sales_ai scripts/backup/backup-tre.sh dev
```

O script não confia em nada: resolve o trio do ambiente, confere que o serviço responde, gera o dump,
exporta globais, extrai as contagens por tabela, grava `sha256` e o manifesto. Falha em qualquer item —
inclusive ambiente declarado cujo container não existe — ⇒ `RESULTADO: BACKUP_FALHOU` e saída diferente
de zero (o timer registra no journal).

**Schema ausente é falha declarada**, não sucesso silencioso: um dump sem `sales_intelligence`
não tem o que restaurar.

## 4. RESTORE

### 4.1 Teste (é isto que vale como prova)

```bash
scripts/backup/verificar-backup.sh /opt/tre/backup/tre_prod_20260929T174742Z
```

Sobe um PostgreSQL **descartável**, restaura o dump nele e compara: legibilidade do arquivo,
schema de volta, número de tabelas e de índices, **contagens por tabela linha a linha**,
ausência de registros órfãos e presença de conteúdo real. O container é removido sempre.

`RESULTADO: RESTORE_OK` / `RESTORE_FALHOU`, com `OK`/`FALHOU` por item.

### 4.2 Restauração operacional (destrutiva)

```bash
scripts/backup/restore-tre.sh homolog /opt/tre/backup/tre_prod_20260929T174742Z --confirmo
```

- Antes de derrubar, guarda um **snapshot do estado atual** em `/tmp` (para reverter a própria
  restauração se ela der errado). Se não conseguir guardar, **aborta**.
- `--confirmo` é obrigatório: sem ele o script recusa. `--banco NOME` troca o alvo.
- **Em produção**, a política exige aprovação humana registrada
  (`docs/operations/registro-de-aprovacoes.md`) — o script não substitui essa aprovação.
- Ao final, compara as contagens com o backup e imprime o veredito.

### 4.3 Ambiente inteiro a partir de zero

1. provisionar a máquina: `scripts/provision/bootstrap-vps.sh` (uma vez, como root)
2. subir os containers do ambiente: `docker compose -f /opt/tre/prod/compose/*.yml up -d`
3. restaurar os dados: `restore-tre.sh prod <artefato> --confirmo`
4. validar: `verificar-backup.sh <artefato>` e o smoke test do ambiente

### 4.4 Restore do Odoo — a prova é o par banco **+** filestore

```bash
# 1) rotina (já roda no timer das 02:30): o artefato do ambiente passa a levar o Odoo junto
scripts/backup/backup-tre.sh dev

# 2) prova: restore do Odoo em alvo DESCARTAVEL, com o Odoo RESPONDENDO depois
scripts/backup/verificar-odoo.sh /opt/tre/backup/tre_dev_20261001T133653Z

# no domingo o timer já encadeia os dois (verificar-ultimo-backup.sh chama o do Odoo
# quando o artefato mais recente do ambiente traz odoo-manifest.txt)
```

O verificador **não** pergunta se o arquivo existe — ele:

1. confere a identidade do artefato: `sha256` do dump e do filestore **contra o manifesto**,
   número de arquivos do tar, e o **digest da imagem** do Odoo registrado no backup contra o
   `RepoDigest` da imagem local (um Odoo de imagem diferente não é o mesmo Odoo);
2. sobe um PostgreSQL **descartável** (rede própria, **sem porta publicada**) e roda `pg_restore`
   `--no-owner --no-privileges` do `odoo_dev.dump`;
3. compara **tabela por tabela, linha a linha** (`odoo-contagens.txt`) e exige o módulo `base`
   instalado — um banco vazio também "restaura" sem erro;
4. desempacota o filestore e exige o diretório `filestore/odoo_dev` e o mesmo número de arquivos;
5. sobe um **Odoo descartável** (`odoo:<versão do manifesto>`, `--entrypoint /usr/bin/odoo`)
   contra esse banco, publicado **só em loopback**, e só aceita quando `/web/login` responde
   **HTTP 200** com a cara do Odoo e o JSON-RPC `/web/webclient/version_info` responde;
6. derruba tudo e confere que `odoo-dev`, `pg-odoo-dev` e `pg-sales-dev` **continuam running**.

`RESULTADO: RESTORE_ODOO_OK` / `RESTORE_ODOO_FALHOU`, com `OK`/`FALHOU` por item — o container
descartável é removido no `trap` mesmo quando o teste falha.

**Por que `--entrypoint /usr/bin/odoo`:** o `/entrypoint.sh` da imagem acrescenta os argumentos de
banco **depois** dos informados (`exec odoo "$@" "${DB_ARGS[@]}"`), com `HOST` default `db` — o
Odoo subia procurando um host `db` que não existe e o verificador reprovava um backup bom.
Medido em 01/10/2026 (`Database connection failure: could not translate host name "db"`).

## 5. ROLLBACK

Duas naturezas distintas — **não confundir**:

| Tipo | Quando | Como |
|---|---|---|
| **Código/release** | a versão nova quebrou | voltar `compose`/imagem ao commit anterior (release = produção, tag no repositório) e subir de novo |
| **Dados** | a migração ou o processamento corrompeu dados | `restore-tre.sh <ambiente> <artefato> --confirmo` |

**Ordem correta em incidente:** parar quem escreve → decidir o ponto de retorno → restaurar
dados → voltar o código → validar. Restaurar dados com a aplicação escrevendo **desfaz o
restore** (e é o erro clássico).

**Credencial em incidente:** revogar **antes** de investigar (a ordem inversa do instinto).
Registro obrigatório em `docs/operations/registro-de-rotacao.md` — data, credencial, motivo,
responsável, **sem o valor**.

## 6. Rollback do próprio backup

- Dump ilegível ou truncado ⇒ `RESTORE_FALHOU` (provado no teste negativo do item 7.4).
- Nada é apagado fora do prefixo `tre_<ambiente>_` na retenção.
- O diretório de cada execução é novo: uma execução ruim não sobrescreve a anterior.

## 7. Evidência medida — 29/09/2026 (VPS Contabo)

Comando: `scripts/backup/teste-backup-restore.sh` — **`RESULTADO: TESTE_OK (9 itens, 0 falhas)`**

1. container de origem descartável criado e pronto (servidor **definitivo**);
2. **migration `0001_sales_intelligence_v1.sql` aplicada em PostgreSQL 16.15 real** — 12 tabelas
   e 30 índices criados (o DDL do Data Contract V1 deixa de ser só documento);
3. massa de smoke inserida nas 12 tabelas (14 linhas);
4. backup executado: `BACKUP_OK`, dump de 36 KB, manifesto e `sha256` gravados;
5. **restore real em container novo: `RESTORE_OK`** — 65 objetos no índice, 12 tabelas, 30
   índices, **as 12 contagens batendo linha a linha**, nenhuma linha órfã, conteúdo presente;
6. **teste negativo:** dump truncado em 2 KB foi **REPROVADO** (8 falhas apontadas) — o
   verificador não é carimbo;
7. reverificação do dump bom continua aprovada (o teste negativo não corrompeu nada).

## 7b. Evidência medida — 30/09/2026 (TRE-W1-E06-T01, **banco do ambiente dev**)

Comando: `set -a; . /etc/tre/backup.env; set +a; bash /usr/local/lib/tre/backup/teste-backup-restore.sh /opt/tre/prod/repo --ambiente dev`
— **`RESULTADO: TESTE_OK (14 itens, 0 falhas)`**, exit 0.
(Note o `bash` explícito: **na data desta medição** os scripts de `scripts/backup/` estavam `100644` no git
— era o ACHADO ABERTO 1, corrigido depois em §7c; hoje são `100755`.)

1. **origem é o container do ambiente** (`pg-sales-dev`), não um descartável: o modo ambiente não cria
   container de origem e **falha** se o container do ambiente não existir (testado: `pg-nao-existe` →
   `TESTE_FALHOU`, exit 1, sem cair para descartável);
2. `backup-tre.sh dev` com o trio real de `deploy/environments/dev.env` (`pg-sales-dev` / `sales_ai` /
   `sales_intelligence`) → `BACKUP_OK`, artefato `tre_dev_20260930T193704Z`;
3. `sha256` do dump confere com o manifesto (`f92f924d…`) e com o `.sha256`;
4. **manifesto registra `externo: enviado (contabo:tre-backup)`**; o bucket tem os 6 objetos do artefato e
   a **prova de volta** (baixar o dump do bucket, `sha256sum` + `cmp`) devolve `IDENTICOS`;
5. **restore real em container novo: `RESTORE_OK`** — 68 objetos no índice, 12 tabelas, 30 índices, **as 12
   contagens batendo linha a linha**; reproduzido de forma independente (restore próprio → `diff` vazio
   contra o `contagens.txt`, 14 linhas);
6. **teste negativo:** dump truncado a 2 KB **REPROVADO** (8 falhas) e dump de 0 byte **REPROVADO**; dump
   válido de **outro banco** também REPROVADO (5 falhas); contagem mutada REPROVADA (1 falha) — o
   comparativo não é carimbo;
7. fronteira: base com **0 linhas** continua sendo comparada (`RESTORE_OK` + `NOTA backup sem linhas`);
8. `pg-sales-dev` ficou **intacto** (mesmo `Id` e `StartedAt`), produção não existe na máquina.

**Modo do driver:** `--ambiente dev|homolog` é o modo novo; sem argumento o comportamento é o do item 7
(descartável), que nesta mesma rodada seguiu `TESTE_OK (12 itens, 0 falhas)`.

## 7c. Evidência medida — 30/09/2026, correção do bit executável (`fix/TRE-W1-E06-T01-D01`)

Máquina: VPS `vmi3619453`, como `root` (acesso do agente); o exec acontece sob `tre-deploy` (dono do unit).

| Medida | Antes (commit `16c31f0`) | Depois (commit `6a580ee` + `install -m 755`) |
|---|---|---|
| `git ls-files -s scripts/backup/` | `100644` em **6 dos 7** scripts que existiam (`teste-backup-restore.sh` já era `100755`; `verificar-modos-executaveis.sh` não existia) | `100755` nos 8 (`backup-tre.sh`, `restore-tre.sh`, `verificar-backup.sh`, `verificar-ultimo-backup.sh`, `configurar-destino-externo.sh`, `instalar-timers.sh`, `teste-backup-restore.sh`, `verificar-modos-executaveis.sh`) |
| `verificar-modos-executaveis.sh` | `MODOS_FALHOU (4 itens, 2 falhas)`, exit 1 | `MODOS_OK (4 itens, 0 falhas)`, exit 0 |
| `sudo -u tre-deploy test -x /usr/local/lib/tre/backup/backup-tre.sh` | exit 1 | **exit 0** |
| `systemctl start tre-backup.service` | `START_EXIT=1`, `ExecMainStatus=203`, journal `Failed at step EXEC … Permission denied` | **`START_EXIT=0`**, `Result=success`, journal com o `backup-tre.sh` executando (`PULADO` nos três ambientes — motivo de negócio, ACHADO 2) |
| `sha256` copia operacional × repositório | — | **8/8 idênticos** (dump do conteúdo em `/opt/tre/backup` intocado) |

1. **`tre-backup.service` executa**: o journal de 20:03:37 UTC mostra `backup-tre.sh[182191]` imprimindo os
   três blocos de ambiente e `RESULTADO: BACKUP_OK (todos)`, com `Deactivated successfully` — nenhum
   `203/EXEC`.
2. **`tre-backup-verify.service` também executa** e vai além: rodou o **restore real** do último artefato
   (`tre_dev_20260930T193704Z`) sob `tre-deploy` — `RESTORE_OK (11 itens, 0 falhas)`, 12 tabelas, 30
   índices, contagens batendo linha a linha, container descartável removido. É a primeira vez que o ciclo
   do timer roda **como o usuário do unit** (o que o §8 dava como não exercitado).
3. **Guarda nova com dente:** `instalar-timers.sh` chama `verificar-modos-executaveis.sh` antes de habilitar
   os timers; em harness isolado (systemd/sudo dublados), com o alvo do `ExecStart=` em 644 o script
   imprime `ABORTADO … timer NAO habilitado` e sai 1 **sem chamar `systemctl`**; com o alvo em 755 segue
   até `RESULTADO: TIMERS_OK`.
4. **Os timers seguem habilitados e ativos** (`tre-backup.timer` → próxima execução 01/10 02:33 -03).
   **A geração do artefato diário continua pendente**: depende do ACHADO ABERTO 2.

## 7d. Rodada 2 — a cópia operacional foi revertida e remedida (30/09/2026, 20:02–20:13 UTC)

A instalação de §7c **foi desfeita 39 s depois** por uma publicação concorrente de outro card, e os dois
critérios que medem a cópia operacional voltaram a reprovar. Cronologia medida em `/opt/tre/.publicacoes.log`
e `stat` dos arquivos:

| UTC | Evento (fonte) | Estado de `/usr/local/lib/tre/backup/*.sh` |
|---|---|---|
| 20:03:36–37 | `install -m 755` deste card (§7c) | `755`, `test -x` exit 0, `systemctl start` = `success` |
| 20:03:59 / 20:06:19 | publicação de teste do card `t_091cfea9` com `commit=16c31f0…` (**anterior à correção**) | volta a `644` (ctime 20:04:15 UTC), `instalar-timers.sh` sem a guarda |
| 20:10:21 / 20:11:59 | medição da **revisão** (§ comentário 110): `test -x` exit 1, `start` → `ExecMainStatus=203/EXEC` | `644` |
| 20:12:35 | publicação **versionada** do card `t_091cfea9` (`commit=f1f1cb6b…` — fora de ref hoje, nota de rastreabilidade no fim deste § —, `/opt/tre/repo/.publicado`, `execstart_sem_bit: 0`) | `755` de novo |
| **20:13:36–37** | **remedição deste card (rodada 2)** | `755` |

Remedição de 20:13:36Z, na VPS `vmi3619453`:

- `sudo -u tre-deploy test -x /usr/local/lib/tre/backup/backup-tre.sh` → **`TEST_X_EXIT=0`**;
- `systemctl start tre-backup.service` → **`START_EXIT=0`**; `systemctl show` → `Result=success`,
  `ExecMainStatus=0`, `ExecStart pid=230506 code=exited status=0`; journal de 17:13:36-03 (20:13:36Z) com
  `backup-tre.sh[230506]` imprimindo os três blocos de ambiente e `RESULTADO: BACKUP_OK (todos)` /
  `Deactivated successfully`. As linhas `203/EXEC` de 20:10:21Z e 20:11:59Z que ainda aparecem no journal
  **são da medição da revisão**, não desta;
- `sha256` da cópia operacional × repositório (`origin/develop`, `d2a2640`): **8/8 idênticos** (`diff` vazio);
  as árvores de `scripts/backup/` em `origin/develop` e no commit publicado `f1f1cb6b` (fora de ref hoje —
  nota de rastreabilidade no fim deste §) são **os mesmos
  blobs** (`git ls-tree` idêntico), ou seja, o conteúdo publicado é o da correção.

**Não fiz uma nova instalação ad-hoc nesta rodada** — de propósito: a cópia foi restaurada pela publicação
**versionada** de 20:12:35Z (a correção do ACHADO ABERTO 3), com modo preservado, e uma sobreposição manual
seria exatamente o padrão que causou o revert. O que se prova aqui é conteúdo e modo idênticos ao
repositório + os dois critérios do card medidos com horário.

**Republicação idempotente medida às 20:13:40Z:** o mesmo card `t_091cfea9` publicou de novo o mesmo commit
`f1f1cb6b…` (fora de ref hoje — nota de rastreabilidade abaixo) com `digest_antes = digest = e4e1f05d…` (nada
mudou) e os modos **continuaram `755`** — a publicação versionada não só preserva o modo como é idempotente.
Conferido depois dela: `test -x` exit 0, `sha256` de `backup-tre.sh` = `1a430637…` (idêntico ao repositório) e
`Result=success ExecMainStatus=0` como **última** execução do serviço.

**Nota de rastreabilidade do commit publicado (01/10/2026, card de defeito `t_26be11c7`).** As cinco menções a
`f1f1cb6b` nesta seção e no §8 são o commit que a publicação **versionada** de 20:12:35Z registrou **de fato**,
mas o objeto hoje **não se alcança por ref nenhuma**: `git cat-file -t f1f1cb6b` → `commit`;
`git merge-base --is-ancestor f1f1cb6b develop` → rc 1; `git for-each-ref --contains f1f1cb6b` → vazio (69
refs, medido em 01/10/2026); num clone limpo do `origin`, `git fetch origin f1f1cb6b…` → `remote error:
upload-pack: not our ref` (mesma resposta de um sha inexistente — não é servido pelo `origin`, então não se
confere o objeto a partir do remoto). O valor fica **como está** — é o registro do
evento, não um alias — porque o parente alcançável de mesma mensagem/autoria/data (`e1eacd2`) tem conteúdo
**diferente** (`306` arquivos / digest `e4e1f05d…` em `f1f1cb6b` contra `308` / `69b954b0…` em `e1eacd2`).
**Âncora que sobrevive, sem depender de ref:** o digest da árvore publicada `e4e1f05d…` com `306` arquivos,
gravado em `/opt/tre/.publicacoes.log` nas duas publicações (20:12:35Z e 20:13:40Z) e reconferível pelo próprio
objeto onde ele existe — `git archive f1f1cb6b` + manifesto `<modo> <sha256> <caminho>` (o algoritmo do
`deploy/publicar.sh`) → `e4e1f05d…`, `306` arquivos. O conteúdo afirmado aqui continua conferível por caminho
alcançável: `git ls-tree -r f1f1cb6b -- scripts/backup/` é idêntico ao de `e1eacd2` e ao de `origin/develop`.

**Nota de atribuição (medida pelo card `t_091cfea9`):** as publicações de ensaio de 20:03:59Z e 20:06:19Z que
aparecem em `/opt/tre/.publicacoes.log` (card `t_091cfea9-TESTE`, `commit=16c31f0`) foram para o destino
**isolado** `/opt/tre/.teste-publicacao`, **não** para `/opt/tre/repo` — o campo `destino=` só passou a ser
gravado no log depois delas, e é isso que tornava a leitura ambígua. O revert da cópia operacional medido às
20:04:15Z/20:06:19Z é, portanto, de uma sincronização por `tar` (ad-hoc) de outro card, não da publicação
versionada deste item.

## 7e. Evidência medida — 30/09/2026, publicação versionada da cópia operacional (`fix/TRE-W1-E06-T01-F3-publicacao`)

**Destino isolado primeiro** (`TRE_PUBLICAR_DESTINO=/opt/tre/.teste-publicacao`, para não interferir em card
que estivesse usando a cópia real), **depois a cópia operacional de verdade**. Máquina: VPS `vmi3619453`; o
agente conecta como `root` (decisão 5) e a publicação deixa a árvore com `tre-deploy:tre-deploy`.

Destino isolado (300 arquivos, `digest 692c244a…`):

1. duas publicações seguidas do **mesmo commit** (`16c31f0`) → **mesmo `digest`** (`692c244a…`), com
   `idempotente: a copia ja estava neste commit`;
2. `--conferir` → `PUBLICACAO_OK … digest=692c244a… arquivos=300`, exit 0;
3. **sobrescrita ad-hoc simulada** (o defeito, reproduzido de propósito): `README.md` alterado por fora, modo
   de `scripts/db/aplicar_migracoes.sh` de `755` para `664` e um `sobra-de-outro-card.sh` largado na árvore →
   `--conferir` devolveu **`PUBLICACAO_DIVERGENTE`**, **exit 5**, com o `diff` do manifesto apontando
   exatamente os três (conteúdo, modo e arquivo a mais);
4. republicação → `PUBLICACAO_OK` com o `digest` do commit de volta e `--conferir` de novo
   `PUBLICACAO_OK`: o `rsync --delete` espelha o commit, não sobra arquivo de fora nem modo errado;
5. destino isolado removido ao fim (`/opt/tre` ficou sem cópia de teste).

Cópia operacional real (`/opt/tre/repo`), commit `3586d08…` (**hoje fora de ref nenhuma** — nota de
rastreabilidade; 306 arquivos, `digest 502d4381…`):

6. **a cópia estava reescrita por fora do caminho único:** `.publicado` dizia `f1f1cb6b…` (**hoje fora de ref
   nenhuma** — nota de rastreabilidade), mas o manifesto da
   cópia tinha **307 arquivos** e **611 linhas diferentes** do commit registrado — leitura (não medida linha a
   linha, o manifesto anterior não é guardado): sincronização por `tar -cz scripts docs db | ssh …` de outro
   card, que reescreve centenas de arquivos com o modo do *checkout* e acrescenta arquivos ainda não
   commitados. A publicação mediu e **corrigiu**; a segunda publicação do mesmo commit mediu
   **`divergencia_antes = 0`** e o **mesmo `digest`** (`502d4381…`, idempotente);
7. `--conferir` → `PUBLICACAO_OK commit=3586d08… digest=502d4381… arquivos=306`, exit 0; `.publicado` grava
   `commit`, `arvore`, `ref`, `digest`, `arquivos`, `publicado_em`, `publicado_por: t_091cfea9`,
   `arvore_suja: 0`, `execstart_sem_bit: 0`, `divergencia_antes: 0`, `concorrencia: (nenhuma)`; o `3586d08`
   desta seção está **hoje fora de ref nenhuma** — nota de rastreabilidade;
8. **modo e dono na cópia:** `deploy/publicar.sh`, `scripts/backup/backup-tre.sh`,
   `scripts/backup/verificar-ultimo-backup.sh` e `scripts/db/suite_banco.sh` em **`755 tre-deploy tre-deploy`**
   e `README.md` em `644` — o modo é o **do git**. (A primeira versão do script publicava `775`/`664`: o modo
   do arquivo extraído com `tar` leva a marca do `umask`/máscara de ACL de quem extrai, e o `rsync -a` pula o
   arquivo de mesma data e tamanho sem olhar o modo. Corrigido com o mapa de `git ls-tree` aplicado na árvore
   local, no staging **e** no destino depois do `rsync` — os dois digests medidos, `c5f169c9…` antes e
   `692c244a…` depois, são do mesmo commit `16c31f0` e diferem exatamente pelo modo.)
9. **verificação pós-deploy com dado real:** `systemctl start tre-backup.service` → `START_EXIT=0`,
   `Result=success`, `ExecMainStatus=0`, `User=tre-deploy`, journal com `backup-tre.sh[281579]` imprimindo os
   três blocos de ambiente e `RESULTADO: BACKUP_OK (todos)` + `Deactivated successfully` — **nenhum
   `203/EXEC`**; `sudo -u tre-deploy test -x` → exit 0 em `backup-tre.sh`, `verificar-ultimo-backup.sh` e
   `deploy/publicar.sh`;
10. **o que a publicação não deixa passar:** árvore suja → `PUBLICACAO_FALHOU arvore suja`, **exit 2**
    (medido duas vezes, com a lista dos arquivos modificados); `--exigir-modos` contra um commit com os alvos
    do `ExecStart=` em `100644` (o `16c31f0`) → **exit 4** nomeando `backup-tre.sh` e
    `verificar-ultimo-backup.sh`, enquanto contra o commit atual → 0 aviso, exit 0;
11. **lock e log:** `/opt/tre/.publicacao.lock` (uma publicação por vez) e `/opt/tre/.publicacoes.log` com uma
    linha por publicação (`quando, commit, digest, arquivos, card, destino, digest_antes, commit_antes,
    divergencia_antes`) — é o histórico que permite rollback do *código* publicado e a fonte da cronologia
    de §7d.

## 7f. Evidência medida — 30/09/2026, resolução do trio **por ambiente** (`fix/TRE-W1-E06-T01-F2`, card `t_1b2ab418`)

Correção do **ACHADO ABERTO 2** (§8). Commit da correção: **`9b464ed`** (branch `fix/TRE-W1-E06-T01-F2`,
base `e4dc18d` = `origin/develop`, com merge `--no-ff` do commit publicado `3bf5e076`).

1. **publicação versionada** (`deploy/publicar.sh --commit 9b464ed --card t_1b2ab418 --exigir-modos`):
   `PUBLICACAO_OK commit=9b464ed… digest=119f7401… arquivos=310`, `divergencia_antes: 0`, exit 0;
   `.publicado` grava `commit: 9b464ed…`, `ref: fix/TRE-W1-E06-T01-F2`, `publicado_por: t_1b2ab418`,
   `arvore_suja: 0`. (O código é o deste commit `9b464ed`; esta evidência em `docs/` viaja no commit de
   documentação imediatamente seguinte da mesma branch — o `/opt/tre/repo/.publicado` da cópia operacional
   registra o **tip publicado**, que é o que importa para rollback.) Os quatro alvos (`backup-tre.sh`, `verificar-ultimo-backup.sh`, `lib-ambiente.sh`,
   `teste-rotina-ambiente.sh`) chegaram em `/usr/local/lib/tre/backup/` com `sha256` **idêntico ao blob
   do commit** (`3f0bebd9…`, `ad0ad002…`, `e582b484…`, `72ce8ecb…`) e modo **`755 tre-deploy`**.
   **Qualificação honesta:** a *primeira* execução desta mesma publicação falhou com exit 6 —
   `PUBLICACAO_FALHOU a copia transferida nao confere com o commit 9b464ed`, com ~280 linhas
   `sha256sum: <arquivo>: No such file or directory` ao montar o manifesto do staging, enquanto o destino
   **não** foi tocado (medido: `.publicado` ainda `3bf5e076`, `backup-tre.sh` ainda `1a430637…`). A segunda
   execução, idêntica, passou limpa (0 erros de hash) e o replay manual dos mesmos passos (tar → staging →
   normalizar modos → manifesto) também passa. É intermitente e ficou registrado como defeito próprio
   (card de defeito aberto contra a publicação), não escondido aqui.
2. **AC1 — a rotina, com a configuração real do timer** (`set -a; . /etc/tre/backup.env; set +a;
   backup-tre.sh todos`): `RESULTADO: BACKUP_OK (todos; 1 ambiente(s) coberto(s), 2 pulado(s))`, exit 0;
   artefato `/opt/tre/backup/tre_dev_20260930T212904Z` com manifesto
   `servico: pg-sales-dev` / `usuario: sales_ai` / `banco: sales_intelligence` /
   `config: arquivo /opt/tre/repo/deploy/environments/dev.env`, `tabelas: 12`, `externo: enviado
   (contabo:tre-backup)`; `sha256sum -c` do dump → OK.
3. **AC2 — verificação (restore REAL)** com o mesmo trio, `verificar-ultimo-backup.sh todos`:
   `OK origem do artefato confere (container 'pg-sales-dev')` → artefato mais recente
   `tre_dev_20260930T212904Z` (o que a rotina acabou de produzir), container descartável
   `postgres:16`, 11 itens OK (`tabelas: 12`, `indices: 30`, contagens linha a linha, órfãs) →
   `RESULTADO: RESTORE_OK` + `RESULTADO: VERIFICACAO_OK (2 itens)`, exit 0.
4. **caminho REAL do timer (systemd, usuário `tre-deploy`)** — não só chamada manual:
   `systemctl start tre-backup.service` → `START_EXIT=0`, `Result=success`, `ExecMainStatus=0`,
   `User=tre-deploy`, `pid=353726`, journal com `OK postgres responde`, `OK dump: 36K`, `OK sha256 gravado`,
   `OK copiado para o destino externo (contabo:tre-backup)`, `RESULTADO: BACKUP_OK (todos; 1 coberto,
   2 pulados)`, `Deactivated successfully` — **nenhum `203/EXEC`**;
   `systemctl start tre-backup-verify.service` → `START_EXIT=0`, `Result=success`, `ExecMainStatus=0`,
   `RESULTADO: RESTORE_OK (11 itens, 0 falhas)` e `VERIFICACAO_OK (2 itens)` sobre o artefato
   `tre_dev_20260930T213013Z` (0h de idade) — o verificador **não** mais aprova sem backup.
5. **negativos (o que a correção não deixa passar)** — todos medidos na VPS:
   (a) ambiente **declarado** em `$TRE_ENV_DIR/homolog.env` com container inexistente →
   `FALHOU ambiente 'homolog' esta DECLARADO … nao existe — ambiente provisionado sem backup e FALHA,
   nao 'pulado'`, `RESULTADO: BACKUP_FALHOU`, **exit 1**, zero artefato de homolog;
   (b) `TRE_PG_SERVICO_DEV=pg-nao-existe` → mesma falha nomeando a variável por ambiente, **exit 1**;
   (c) **a armadilha do card**: `TRE_PG_SERVICO=pg-sales-dev` global + `todos` → `NOTA TRE_PG_SERVICO global
   ignorado para 'dev'…`, `RESULTADO: BACKUP_SEM_AMBIENTE`, **exit 0 sem artefato nenhum** — antes, o mesmo
   comando copiava o banco do dev rotulado de homolog/prod.
6. **teste hermético na cópia operacional** (dublê de `docker`, nenhum container real tocado):
   `RESULTADO: TESTE_OK (55 itens, 0 falhas)`, exit 0 — incluindo a seção **10. REGRESSÃO (antes × depois)**
   contra os scripts **anteriores** (`backup-tre.sh` `1a430637…`, `verificar-ultimo-backup.sh` `94b84aeb…`,
   publicados como `.antigo.sh` no `/tmp` da VPS): `ANTES: imprimia BACKUP_OK` com **0 artefatos** e
   `ANTES: verificador dizia VERIFICACAO_OK com zero backup`; `DEPOIS: artefatos criados (1)`.
7. **efeitos colaterais verificados (nada de dados tocados):** `pg-sales-dev` com o **mesmo**
   `Id=396ace56…`/`StartedAt=2026-09-30T17:05:15Z` do início da rodada e o **mesmo** `md5` das contagens por
   tabela (`20b6a472…`); nenhum container de verificação deixado para trás (`docker ps -a` = `pg-sales-dev`
   + o `pg-teste-d01-suite` de outro card); `/opt/tre/backup` com 3 artefatos de dev (1 manual de 19:37Z,
   1 da chamada manual e 1 do timer), todos `tre-deploy`, nada removido pela retenção de 14 dias.
8. **configuração aplicada no host:** `/etc/tre/backup.env` ganhou `TRE_ENV_DIR=/opt/tre/repo/deploy/environments`
   (cópia do estado anterior em `/etc/tre/backup.env.f2-antes`; `diff` = só essa linha + comentário) — sem
   `TRE_PG_SERVICO` global, que é justamente o que não escala para três ambientes. Timers seguem habilitados:
   `tre-backup.timer` próxima execução **01/10/2026 02:34 -03**, `tre-backup-verify.timer` **04/10/2026 04:00 -03**.

## 7g. Evidência medida — 01/10/2026, o Odoo do dev dentro do artefato do ambiente (`TRE-W2-E01-T01-F01`, card `t_a5afde31`)

Commit publicado **`e2b960b5bb79e5773a25fa3c594461a699ccf9d5`** (árvore `f10552201efe…`, digest
`4f0c65390a54aa9df22a0871ae920935e5121795303e63145c2f9a9eb878a845`, 323 arquivos) em
`root@169.58.24.102:/opt/tre/repo` às **13:54:33Z**, `--producao` declarado, **trava rearmada**
(`lsattr -d` → `----i---------e-------`) e `--conferir` posterior `PUBLICACAO_OK … trava=travada`.
**Ponta final publicada:** o commit de documentação que acompanha este texto (`--conferir`
`PUBLICACAO_OK … trava=travada` na hora da publicação; os commits seguintes ao `e2b960b5` mudam só
documentação — o mesmo `323 arquivos` e o mesmo código de rotina).
Antes, a cópia estava idêntica ao commit `66c7152` (card `t_daca4bda`): a publicação é **avanço na
própria linha** (branch nascida da `fix/t_daca4bda-enforcement` com `origin/develop` mergeado), e
nenhuma publicação de outro card ficou de fora — `/opt/tre/.publicacoes.log` mostra a última
publicação em `/opt/tre/repo` em `2026-09-30T23:42:48Z` (`66c7152`) e, depois dela, apenas
`watchdog-reparo` do **mesmo** commit.

1. **O que a rotina passou a gravar** (medido na cópia publicada, `backup-tre.sh dev`, artefato
   `/opt/tre/backup/tre_dev_20261001T135513Z`, `BACKUP_OK` exit 0): `odoo_dev.dump` **2 487 524 B**
   (sha256 `529cd429…`), `odoo-contagens.txt` com **281 tabelas / 26 213 linhas**,
   `odoo-filestore.tar.gz` **115 082 B / 21 arquivos** (sha256 `521d4ece…`, volume `odoo-data-dev`
   empacotado por container efêmero) e `odoo-manifest.txt` com `odoo_imagem_restore: odoo:19.0`,
   `odoo_imagem_digest: sha256:77bac5cd…` e `odoo_segredos: fora do artefato (o dump nao leva
   /etc/tre/odoo-dev/*)`. O dump do trio (`sales_intelligence.dump`, 35 428 B) e o `manifest.txt` do
   ambiente seguem no **mesmo** diretório.
2. **Prova de restore em alvo descartável** — `verificar-odoo.sh` →
   `RESTORE_ODOO_OK (27 itens, 0 falhas)`: sha256 dos dois arquivos e **digest da imagem** conferidos
   contra o manifesto; `pg_restore` num PostgreSQL descartável **sem porta publicada**; **281 tabelas
   com as contagens batendo linha a linha**; módulo `base` instalado; filestore desempacotado com
   `filestore/odoo_dev` e 21 arquivos; Odoo descartável respondendo **HTTP 200** em
   `127.0.0.1:32774/web/login` (`Odoo Server 19.0-20260926`) e JSON-RPC respondendo; ao fim,
   `odoo-dev`, `pg-odoo-dev` e `pg-sales-dev` **continuavam `running`**.
3. **Verificação encadeada — o que o timer de domingo roda**, pela cópia publicada
   (`verificar-ultimo-backup.sh todos`): `RESTORE_OK (11 itens, 0 falhas)` (trio) +
   `RESTORE_ODOO_OK (27 itens, 0 falhas)` (Odoo), com `homolog`/`prod` **pulados** por não
   provisionados → `VERIFICACAO_OK (3 itens)`, exit 0. **Por quem foi medida: pelo agente, como
   `root`** (não pela identidade do timer) — a identidade `tre-deploy` só entrou na rodada 2, que
   consertou justamente o que essa diferença escondia (§7h itens 1–2).
4. **Destino externo (storage de objeto) com ida e volta lida** —
   `rclone lsl contabo:tre-backup/prova-t_a5afde31/<artefato>` lista os 14 arquivos, incluindo
   `odoo_dev.dump`, `odoo-filestore.tar.gz`, `odoo-contagens.txt` e `odoo-manifest.txt`;
   `rclone cat …/odoo_dev.dump | sha256sum` = `529cd429…` e `rclone cat …/odoo-filestore.tar.gz |
   sha256sum` = `521d4ece…`, **iguais** aos sha256 do manifesto local. O prefixo
   `prova-t_a5afde31/` é prova, não backup de produção (mesma convenção do `prova-t03/`).
5. **Negativos medidos (exit 1 em todos)** — (a) dump do Odoo **truncado** →
   `RESTORE_ODOO_FALHOU (27 itens, 10 falhas)`, começando em `FALHOU sha256 do dump NAO confere` e
   seguindo com `pg_restore` reprovando o arquivo, `tabelas em public: restaurado=0 backup=281`,
   `modulo 'base' NAO esta instalado no banco restaurado` e `HTTP 500` no Odoo; (b) **filestore
   removido** do artefato → `RESTORE_ODOO_FALHOU (26 itens, 5 falhas)` (`filestore ausente ou vazio`,
   `arquivos do filestore: desempacotado=0 manifesto=21`, `diretorio filestore/odoo_dev AUSENTE`);
   (c) dump truncado **sem** `.sha256` → mesma reprovação (nada de "restaurou sem erro" com banco
   vazio).
6. **Regressão do verificador do trio num artefato com DOIS `*.dump`** — `verificar-backup.sh` →
   `RESTORE_OK (11 itens, 0 falhas)`: a escolha do dump passou a ser pelo **manifesto** (`banco:`), e
   não por `ls *.dump | head -1` (que pegaria `odoo_dev.dump`).
7. **Teste hermético da rotina** (`scripts/backup/teste-rotina-ambiente.sh`, dublê de `docker`, nenhum
   container real tocado): `TESTE_OK (65 itens, 0 falhas)`, incluindo as seções novas **9b** (ambiente
   que **declara** Odoo e cujo container **não existe** → `BACKUP_FALHOU`, com o manifesto gravando
   `odoo: ausente neste ambiente` e sem `odoo-manifest.txt`) e **9c** (o verificador **reprova**
   artefato pela metade: `backup do ambiente esta pela metade`).
8. **Achado que decidiu o aceite (a)+(c):** rodando o ciclo com o `/etc/tre/backup.env` do timer
   **antes** da publicação, a rotina imprimiu `PULADO odoo: ambiente 'dev' nao declara Odoo` — o
   `TRE_ENV_DIR` do timer aponta para o `deploy/environments` **da cópia operacional**, então o bloco
   `TRE_ODOO_*` só existe na rotina depois da publicação. Um "backup do Odoo em dev" medido só no
   destino isolado estaria **aprovando a árvore, não a rotina**.
9. **Rollback** — alvos de teste removidos (cópia isolada, artefatos de teste, dublês `docker`, redes e
   containers `tre-verif-odoo-*`, nada disso sobrou: `0` container e `0` rede `tre-verif-odoo-*`);
   **nenhum timer novo** foi instalado (a rotina usa os `tre-backup.timer`/`tre-backup-verify.timer`
   que já existiam); trava da cópia seguiu armada; e a verificação encadeada rodou **de novo depois do
   rollback** (`VERIFICACAO_OK (3 itens)`, exit 0, log `/opt/tre/rollback-evidencia-t_a5afde31.log`).
   O ambiente ficou com os **mesmos ids de container** do início do card: `pg-sales-dev`
   `396ace563710…` (criado e iniciado em `2026-09-30T17:05:15Z`, `restarts=0` — o mesmo par id/StartedAt
   que o card `t_1b2ab418` registrou), `pg-odoo-dev` `c7cb12f75eb9…` (12:41:17Z, `restarts=0`) e
   `odoo-dev` `12cf65a3c1c6…` (criado 12:42:29Z; iniciado 13:48:40Z — **antes** da publicação deste
   card, `restarts=0`: reinício de fora, container não recriado). O artefato real
   `tre_dev_20261001T135513Z` foi mantido; o artefato enganoso da primeira rodada (trio sem Odoo,
   gravado quando a cópia ainda estava no commit antigo) foi **removido de propósito**, com registro.

## 7h. Rodada 2 — **dono do artefato, retenção honesta e diagnóstico de permissão** (01/10/2026, card `t_a5afde31`, `TRE-W2-E01-T01-F01`)

Correção pedida pela **revisão independente (perfil `tester`, rodada 1)**: o artefato que o handoff
nomeava (`/opt/tre/backup/tre_dev_20261001T135513Z`) era **`root:root 700`** — gravado por execução
manual do operador como `root` — e **não reproduzia sob a identidade do timer**: o
`verificar-ultimo-backup.sh` de `tre-deploy` acusava "backup pela metade" e "artefato sem Odoo"
(defeito de **conteúdo**, falso) para um artefato íntegro; o diretório era irremovível por
`tre-deploy` (a retenção contava como removido mesmo assim). Máquina: VPS `vmi3619453`
(`169.58.24.102`). Publicado com `deploy/publicar.sh --commit 9c17e5f81b154e82e59d785c3ea0a03dbe9d8915
--producao --card t_a5afde31` → `PUBLICACAO_OK commit=9c17e5f… digest=106440348b44ba76a441a8fec66e524e60c07f72ec1bfb5a59343ebc2a41cc90
arquivos=323 trava=travada`, `publicado_em 2026-10-01T14:26:42Z`, `concorrencia: (nenhuma)` e
`digest_antes = 09e36cdf…` (`a879fdf`, o commit imediatamente anterior — a publicação é avanço na
própria linha). sha256 dos scripts publicados: `backup-tre.sh f5fd66cf…`,
`verificar-ultimo-backup.sh 0a58bd3d…`, `verificar-odoo.sh ff6397d0…`, `verificar-backup.sh a9af12e6…`.

1. **O caso real do defeito, consertado: rotina executada a mão pelo `root`.** Com o
   `/etc/tre/backup.env` **do timer** e a cópia publicada, `backup-tre.sh dev` como `root` →
   `BACKUP_OK` e artefato `/opt/tre/backup/tre_dev_20261001T142755Z` **`tre-deploy:tre-deploy 700`**
   (`OK dono do artefato: tre-deploy:tre-deploy (modo 700)`), com o manifesto registrando
   `executado_por: root` e `dono_artefato: tre-deploy`. O **mesmo** artefato verificado pela
   identidade do timer: `sudo -u tre-deploy … verificar-ultimo-backup.sh todos` → `RESTORE_OK (11
   itens, 0 falhas)` + `RESTORE_ODOO_OK (27 itens, 0 falhas)` → **`VERIFICACAO_OK (3 itens)` exit 0**.
   Antes disso o cenário reproduzia o defeito em instrumento isolado (`/opt/tre/neg-r2/ilegivel`, já
   removido): com o código **anterior**, o mesmo usuário obtinha
   `FALHOU nenhum arquivo .dump legivel …` + `FALHOU … artefato mais recente NAO tem o bloco do Odoo —
   backup do ambiente esta pela metade` → `VERIFICACAO_FALHOU (2 itens, 2 falhas)`, e o verificador do
   Odoo dizia `FALHOU manifesto do Odoo ausente … artefato sem Odoo?`.
2. **Retenção que não remove não pode dizer que removeu** (antes × depois, cenário isolado com
   artefato antigo `root:root 700`, executado como `tre-deploy`):
   - **ANTES** (código anterior): `rm: cannot remove …: Permission denied` seguido de
     `OK retencao aplicada (1 dias; 1 artefato(s) antigo(s) removido(s))` e `RESULTADO: BACKUP_OK`
     (exit 0) — com o diretório **ainda no disco**;
   - **DEPOIS**: `FALHOU retencao: NAO consegui remover tre_dev_20200101T000000Z (dono root:root,
     modo 700, rodando como tre-deploy) — este artefato escapa da retencao` +
     `FALHOU retencao 1 dias: 0 de 1 artefato(s) removido(s), 1 NAO removido(s)` →
     `RESULTADO: BACKUP_FALHOU` (exit 1), diretório no disco;
   - **positivo (regressão)**: artefato antigo **removível** → `OK retencao aplicada (1 dias; 1 de 1
     artefato(s) antigo(s) removido(s))`, `BACKUP_OK` — a retenção continua funcionando.
3. **O diagnóstico passou a separar PERMISSÃO de CONTEÚDO** (com o código publicado, identidade
   `tre-deploy`, artefato novo `root:root 700` em destino isolado):
   `FALHOU artefato mais recente de 'dev' (…) existe mas NAO e legivel por 'tre-deploy': dono root:root,
   modo 700 — e PERMISSAO, nao conteudo` → `VERIFICACAO_FALHOU (1 itens, 1 falha)`; e
   `verificar-odoo.sh` → `FALHOU artefato '…' existe mas NAO e legivel por 'tre-deploy': … — e
   PERMISSAO, nao 'artefato sem Odoo'`. Nem "pela metade", nem "sem Odoo".
4. **O caminho real do timer, exercitado de ponta a ponta:** `systemctl start tre-backup.service`
   (`User=tre-deploy`) → `Result=success`, `ExecMainStatus=0`, journal `RESULTADO: BACKUP_OK (todos;
   1 ambiente(s) coberto(s), 2 pulado(s))`; artefato `tre_dev_20261001T142800Z` `tre-deploy:tre-deploy
   700`. `systemctl start tre-backup-verify.service` → `Result=success`, `ExecMainStatus=0`,
   `RESTORE_OK` + `RESTORE_ODOO_OK (27 itens, 0 falhas)` (HTTP 200 em `127.0.0.1:32781/web/login`,
   JSON-RPC e tela de login do banco restaurado) → `RESULTADO: VERIFICACAO_OK (3 itens)`.
5. **Os negativos de CONTEÚDO continuam reprovando** (com o código publicado e identidade
   `tre-deploy`, cópias em `/opt/tre/neg-r4`, já removidas): (a) `odoo_dev.dump` truncado →
   `RESTORE_ODOO_FALHOU (27 itens, 10 falhas)` (`sha256 NAO confere`, `pg_restore` reprovando,
   `tabelas em public: restaurado=0 backup=281`, `HTTP 500`); (b) filestore ausente →
   `RESTORE_ODOO_FALHOU (26 itens, 4 falhas)`; (c) dump do trio truncado → `RESTORE_FALHOU (11 itens,
   7 falhas)`; (d) controle positivo da mesma cópia → `RESTORE_ODOO_OK (27 itens, 0 falhas)`. O
   conserto de dono/permissão **não** afrouxou nenhuma reprovação de conteúdo.
6. **Remediação do artefato da rodada 1** `/opt/tre/backup/tre_dev_20261001T135513Z`:
   `ANTES dono=root:root modo=700` → `DEPOIS dono=tre-deploy:tre-deploy modo=700`, com
   `tre-deploy` lendo **e** escrevendo no diretório. E o artefato que o verificador antigo chamava de
   "sem Odoo" restaurou **inteiro** sob a identidade do timer: `RESTORE_ODOO_OK (27 itens, 0 falhas)`
   com HTTP 200 em `127.0.0.1:32780/web/login` e 281 tabelas batendo linha a linha — estava íntegro;
   o defeito era o dono, exatamente como a revisão apontou.
7. **Destino externo com o artefato novo (ida e volta lida):** `rclone lsl
   contabo:tre-backup/tre_dev_20261001T142800Z` lista os 14 objetos e
   `sha256` lido do bucket **==** manifesto local para `odoo_dev.dump` (`f77d0f27…`),
   `odoo-filestore.tar.gz` (`521d4ece…`) e `sales_intelligence.dump` (`153630db…`).
8. **Nada além disso foi tocado:** `odoo-dev 12cf65a3c1c6` (`StartedAt 2026-10-01T13:48:40Z`),
   `pg-odoo-dev c7cb12f75eb9` (`12:41:17Z`) e `pg-sales-dev 396ace563710`
   (`2026-09-30T17:05:15Z`) seguem `running` com os **mesmos** ids/StartedAt do início do card;
   `0` container de verificação deixado (`tre-verif*`/`tre-restore*`); nenhum timer novo (os dois
   units e o watchdog da publicação seguem `active`); trava da cópia armada
   (`----i---------e-------`); produção intocada (`environments/` só `dev.env`/`dev-odoo.env`).
9. **Teste hermético da rotina** (`scripts/backup/teste-rotina-ambiente.sh`, dublê de `docker`,
   nenhum container real): `TESTE_OK (84 itens, 0 falhas)`, com as seções novas **9d** (retenção que
   não consegue remover: exit != 0, nomeia o artefato, `1 NAO removido(s)`), **9e** (dono ≠ usuário
   de serviço = falha; manifesto `dono_artefato`/`executado_por`; e o `chown` do `root` para
   `tre-deploy` quando o usuário de serviço existe na máquina) e **9f** (permissão ≠ conteúdo nos dois
   verificadores — medido como usuário não-root, porque `[ -r ]` não restringe `root`).
10. **Rollback desta rodada:** cenários isolados removidos (`/opt/tre/neg-r2`, `neg-r3`, `neg-r4`,
    `/opt/tre/ensaio-t_a5afde31-r2`), **nenhum** timer novo, `/opt/tre/backup` mantido com os artefatos
    reais que a rotina publicada criou (`tre_dev_20261001T142755Z` da execução manual como `root` e
    `tre_dev_20261001T142800Z` do unit) e o artefato da rodada 1 **remediado** (dono). Logs brutos:
    `/opt/data/profiles/devops/evidence/t_a5afde31/rodada2/` (agente) e `/opt/tre/evid-t_a5afde31-r2/`
    (VPS). Revert do código é `deploy/publicar.sh --commit a879fdf… --producao`.

## 8. Pendências declaradas (não disfarçadas)

- **RESOLVIDO NO GIT — o serviço do timer não executava (era ACHADO ABERTO 1, alta); a cópia operacional
  já foi revertida duas vezes por publicação anterior à correção.**
  `scripts/backup/*.sh` estavam no git como **100644** (sem bit executável); o `ExecStart=` chama o arquivo
  direto, então toda sincronização a partir do repositório devolvia a cópia operacional para 644 e o unit
  morria com `status=203/EXEC` (`Unable to locate executable …: Permission denied`). Corrigido no **git**
  (`100755`, commit `6a580ee`, branch `fix/TRE-W1-E06-T01-D01`) e na cópia operacional por `install -m 755`;
  adicionada a guarda `scripts/backup/verificar-modos-executaveis.sh` (reprova o estado anterior com
  `MODOS_FALHOU` exit 1, aprova o corrigido com `MODOS_OK` exit 0), chamada pelo `instalar-timers.sh`, que
  agora **aborta sem habilitar timer** se algum `ExecStart=` estiver sem bit. Evidência medida em §7c.
  **Qualificação (30/09/2026, 20:02–20:13 UTC):** a cópia operacional foi **revertida para o estado
  pré-correção** (`commit=16c31f0`) às 20:04:15Z/20:06:19Z pela publicação de teste do card `t_091cfea9`
  (prova: `/opt/tre/.publicacoes.log`; as medições `203/EXEC` de 20:10:21Z e 20:11:59Z são da revisão), e
  **voltará a `203/EXEC` a cada publicação de árvore anterior à correção enquanto o caminho versionado de
  publicação não for o único usado** (ACHADO ABERTO 3). A partir de 20:12:35Z a cópia voltou a ficar
  executável por uma publicação **versionada** (`/opt/tre/repo/.publicado` = `f1f1cb6b` — fora de ref hoje, nota
  de rastreabilidade no §7d —, `execstart_sem_bit: 0`)
  e os dois critérios da cópia operacional foram **remedidos com horário** às 20:13:36Z (`test -x` exit 0;
  `systemctl start` → `Result=success`, `ExecMainStatus=0`), com conteúdo provado por `sha256` 8/8 idêntico
  ao repositório — §7d. O que fica **resolvido de forma durável** é o bit no git e a guarda; o que fica
  **dependente de processo** é a cópia operacional não ser sobrescrita por publicação anterior.
  **A rotina diária ainda não produz artefato** — a causa que resta é o **ACHADO ABERTO 2** (abaixo).
- **RESOLVIDO 30/09/2026 (`fix/TRE-W1-E06-T01-F2`, commit `9b464ed`, card `t_1b2ab418`; evidência em §7f) —
  era ACHADO ABERTO 2 (alta): a rotina cobria zero ambientes e saía `BACKUP_OK`.** `backup-tre.sh` procurava
  `pg-dev`/`pg-homolog`/`pg-prod`, mas o dev real é `pg-sales-dev` (usuário `sales_ai`), e
  `/etc/tre/backup.env` (o `EnvironmentFile` do unit) não declarava `TRE_PG_SERVICO`/`TRE_PG_USER`/`TRE_PG_DB`
  — o trio real estava em `deploy/environments/dev.env`, que nenhum timer lia. Resultado medido no defeito:
  `PULADO` nos três ambientes, exit 0, **nenhum artefato novo**; o `tre-backup-verify.timer` também não
  acusava, porque procurava o mesmo prefixo `tre_dev_*` que a rotina nunca produzia.
  **Decisão tomada — o caminho mínimo que o próprio card declarou (declarar o trio por ambiente, sem
  renomear container nem mover dado):** o trio passa a ser resolvido
  **por ambiente** em `scripts/backup/lib-ambiente.sh` (variável por ambiente → `$TRE_ENV_DIR/<ambiente>.env`
  → variável global **só** em chamada de um ambiente → convenção), com `TRE_ENV_DIR` declarado no
  `/etc/tre/backup.env` apontando para a cópia operacional versionada (`/opt/tre/prod/repo/deploy/environments`);
  o par não-secreto do ambiente versionado no git é a fonte. Ambiente **declarado** cujo container não
  existe agora **falha** (exit 1), `todos` sem nenhum ambiente coberto devolve `BACKUP_SEM_AMBIENTE`, e a
  verificação não aprova mais sem backup. Medido depois da correção, **sob o usuário do timer**, com
  `systemctl start tre-backup.service` → `BACKUP_OK` + artefato `tre_dev_*`, e
  `tre-backup-verify.service` → `RESTORE_OK`/`VERIFICACAO_OK` (§7f itens 2–4).
- **RESOLVIDO 30/09/2026 — a cópia operacional era reescrita por qualquer card (era ACHADO ABERTO 3,
  média).** Cada card publicava o seu pedaço com `tar -cz … | ssh … 'tar -xz -C /opt/tre/repo'`: durante
  a rodada do `TRE-W1-E06-T01`, um `tar` de outro worker reverteu o driver recém-instalado (sha
  `d29c9c97…` → `9f24572a…`). Quem sincroniza por último manda: a cópia operacional não é reproduzível.
  **Atualização 30/09/2026 20:12:35Z (medida):** o card `t_091cfea9` passou a publicar por caminho
  versionado — `/opt/tre/repo/.publicado` registra `commit: f1f1cb6b…` (fora de ref hoje, nota de rastreabilidade
  no §7d), `execstart_sem_bit: 0` e
  `concorrencia: (nenhuma)` — e essa publicação preservou o modo (`755`) e restaurou a cópia executável
  (§7d).
  **Fechamento (30/09/2026 20:27–20:29Z):** o caminho versionado passou a ser o **único** de escrita —
  `deploy/publicar.sh` (branch `fix/TRE-W1-E06-T01-F3-publicacao`) publica um **commit** (`git archive` →
  staging → `rsync -a --delete`, com o modo exato do `git ls-tree`), recusa árvore suja, grava o commit em
  uso em `/opt/tre/repo/.publicado`, aceita uma publicação por vez (`/opt/tre/.publicacao.lock`), avisa
  quando outro card publicou antes, mantém o histórico em `/opt/tre/.publicacoes.log` e confere o `digest`
  depois do `rsync`; `deploy/publicar.sh --conferir` compara a cópia com o commit registrado arquivo a
  arquivo **e modo a modo** e devolve `PUBLICACAO_DIVERGENTE` (exit 5) com o `diff`. Medido na cópia de
  30/09: **307 arquivos e 611 linhas de manifesto divergentes** do commit que o `.publicado` dizia estar
  publicado (sincronização por `tar` de outro card) — detectado e corrigido por uma publicação, e a
  republicação seguinte mediu `divergencia_antes = 0` com o mesmo `digest` (§7e itens 6–10). Runbook:
  `docs/runbooks/publicacao-da-copia-operacional.md`.
  **Regra nova:** nenhum card escreve em `/opt/tre/repo` com `tar`/`scp`/`rsync` direto — o caminho é
  `deploy/publicar.sh`. Enquanto a regra depender de disciplina (e não de um gancho no dispatch), o achado
  fica **resolvido no instrumento e aberto no processo**. Medição de partida do defeito (para comparação):
  antes do caminho único a cópia tinha **122 dos 300 arquivos** versionados e nenhum registro de commit.
- **Usuário do timer não exercitado de ponta a ponta:** o ciclo foi rodado como `root` (acesso do agente,
  decisão 5); provou-se por partes que `tre-deploy` escreve em `/opt/tre/backup` (`test -w`) e usa a
  credencial do bucket (`rclone lsl --config /etc/tre/rclone.conf`). Rodar o ciclo inteiro como
  `tre-deploy` requer um canal de privilégio que o harness bloqueia hoje.
  **Atualização 30/09/2026:** superado para os units — `tre-backup-verify.service` rodou o restore real sob
  `tre-deploy` (§7c item 2), e desde a correção do ACHADO ABERTO 2 os **dois** units rodaram inteiros sob
  `tre-deploy` com o trio real do ambiente (`tre-backup.service` → `BACKUP_OK` + artefato `tre_dev_*`;
  `tre-backup-verify.service` → `RESTORE_OK`) — §7f item 4.
  **Atualização 01/10/2026 (rodada 2, §7h):** a diferença "medido como `root`" × "medido como
  `tre-deploy`" deixou de ser nota de rodapé e **virou o defeito**: o artefato da rodada 1 era
  `root:root 700` e o verificador do timer não conseguia ler (diagnóstico falso de conteúdo). A
  rodada 2 mede as **duas** identidades no mesmo estado entregue: a rotina rodada a mão por `root`
  **entrega** o artefato para `tre-deploy` (chown) e o ciclo inteiro também foi exercitado pelos
  units (`Result=success`, `User=tre-deploy`, `VERIFICACAO_OK (3 itens)`).
- **Leituras que ficaram de fora do conserto, registradas (não bloqueiam):**
  - **`pg_restore.err` dentro do artefato verificado** (apontado pela revisão da rodada 1): o
    `verificar-backup.sh` gravava o stderr do `pg_restore` **dentro** do artefato conferido, que assim
    divergia da cópia no bucket depois do envio. O stderr passou a sair em arquivo temporário
    (`mktemp`, removido no trap). Os arquivos de **0 byte** criados pelo verificador antigo
    continuam nos artefatos anteriores ao conserto (10 deles, em `/opt/tre/backup/tre_dev_*`) — não
    são removidos de propósito: são resíduo histórico do verificador antigo, sem efeito no restore.
  - **`globals.sql` carrega o verificador SCRAM do papel `sales_ai`** (apontado pela revisão da
    rodada 1): comportamento **anterior a este card** (vem do `66c7152`, fora do diff do
    `TRE-W2-E01-T01-F01`) e não é alterado aqui — o `pg_dumpall --globals-only` é o que captura
    papéis; tirar o verificador exige decidir o que fazer com a criação do papel no restore (card
    próprio, se o dono quiser).
- **Destino externo (Object Storage) — ATIVO desde 29/09/2026.** Storage: Object Storage European
  Union, 250 GB (endpoint `https://eu2.contabostorage.com`); bucket `tre-backup`; credenciais em
  `/etc/tre/rclone.conf` (600, dono `tre-deploy`); `TRE_BACKUP_EXTERNO=contabo:tre-backup` em
  `/etc/tre/backup.env`. Provado com um backup real enviado e lido de volta do bucket — o artefato
  da prova fica em `prova-t03/` (não é backup de produção, é a evidência do aceite).
  Para ativar: `scripts/backup/configurar-destino-externo.sh` — pergunta endpoint, access key,
  secret key (sem eco) e bucket; grava `/etc/tre/rclone.conf` com permissão 600 (dono
  `tre-deploy`); cria o bucket e faz **prova de ida e volta** (sobe, confere, remove). O destino
  **só é ligado no backup depois que essa prova passa** — se as chaves estiverem erradas, o
  `backup.env` fica intocado. As chaves são digitadas no prompt da VPS: nunca pelo chat nem no
  histórico do shell. O `manifest.txt` de cada execução registra o resultado do envio (`externo: enviado ...` ou `externo: pendente`).
  - **O bucket não precisa existir antes**: o próprio script cria (`rclone mkdir`). Criar pelo
    painel também serve; se já existir, nada muda.
  - Configuração do rclone conforme a Contabo: `provider = Other`, `force_path_style = true`
    (eles usam path style) e `region = default` como segunda tentativa se a assinatura falhar.
  - **Limites do provedor que importam para o backup**: banda padrão de **10 MB/s** (um dump de
    1 GB leva ~2 min; o volume diário é modesto, mas isso entra na conta quando a base crescer),
    **100 buckets**, arquivo de até 5 TB e 3 milhões de objetos por cliente.
- **Sem PITR**: só dump lógico (não há arquivamento de WAL). Ponto no tempo exato não é
  possível hoje — PITR entra com o PostgreSQL de produção (W1/W2).
- **Watchdog externo** (checar a idade do último backup de fora da máquina, onde o Hermes vive)
  ainda **não** está ligado: hoje o sinal é o `tre-backup-verify.timer` no journal local. Se a
  VPS inteira morrer, ninguém avisa — item para o W1.
- **RESOLVIDO 01/10/2026 (`feature/TRE-W2-E01-T01-F01`, card `t_a5afde31`; evidência em §7g) — era
  "Restauração do Odoo entra quando o Odoo subir (W2)".** O Odoo do dev subiu e a rotina **não** o
  cobria: `backup-tre.sh` copiava só o PostgreSQL do trio, e um restore do dump do `sales_intelligence`
  devolveria um dev sem Odoo nenhum. Agora o **artefato do ambiente carrega o ambiente inteiro** —
  `odoo_dev.dump` + `odoo_dev.dump.sha256`, `odoo-contagens.txt`, `odoo-filestore.tar.gz` (volume
  `odoo-data-dev`) e `odoo-manifest.txt` (com o digest da imagem do Odoo) no **mesmo** diretório do
  dump do trio —, e `scripts/backup/verificar-odoo.sh` **prova** o restore num alvo descartável com o
  Odoo **respondendo HTTP 200** contra o banco restaurado (`RESTORE_ODOO_OK`).
  **O que fica declarado:** o restore do Odoo é provado em alvo descartável; a **restauração
  operacional** (dentro do `odoo-dev`/`pg-odoo-dev` de verdade) continua sendo procedimento manual
  documentado em §4.4 + §4.3, sem script destrutivo próprio (`restore-tre.sh` cobre o trio) — não
  inventar um caminho destrutivo novo sem card.
- **O filestore do Odoo não tem retenção própria:** vai e volta junto com o artefato do ambiente
  (mesma janela de 14 dias). Não há versionamento de anexo por dia.
- **Publicação em produção declarada por worker, sob gate JEV — linha formal de aprovação pendente.**
  Para o AC (a)/(c) valerem na rotina de verdade foi preciso publicar o commit `e2b960b5` na cópia
  operacional com `--producao` (§7g item 8). A base foi o gate JEV do próprio card — aprovação humana
  de **Anderson Ribeiro** (canal telegram, validade **2026-10-07**), `dec-4a54b3c39ce9cc52`,
  `outcome: PASS`, `exige_aprovacao_humana: false` —, que é o que `deploy/publicar.sh` exige ("card e
  aprovação registrados"). **O que fica declarado:** uma linha desta ação em
  `docs/operations/registro-de-aprovacoes.md` **não foi escrita pelo worker** (linha de aprovação é do
  humano, não de quem executa); se o dono entender que a publicação precisava de aprovação
  específica, o revert é o caminho versionado (`publicar.sh --commit 66c7152 --producao`) + o artefato
  do commit anterior que o watchdog já mantém em `/opt/tre/.publicacao-artefato`.

## 9. Armadilhas registradas (custaram tempo real)

- **`pg_isready` mente no início.** A imagem oficial do PostgreSQL sobe um servidor
  **temporário** para rodar a inicialização e o **derruba** depois. Quem espera só por
  `pg_isready` aplica migration no servidor que está caindo → `the database system is shutting
  down`. **Espera correta:** `SELECT 1` funcionar **duas vezes**, com intervalo.
- **`paste -d` não junta com uma string.** `-d` é uma lista de **caracteres** usada em rotação:
  `paste -sd ' UNION ALL '` insere espaço, `U`, `N`, … O `UNION ALL` tem de ser montado **dentro
  do SQL** (`string_agg(..., ' UNION ALL ')`).
- **`psql -c` com vários comandos sem `;` é erro de sintaxe** — não é uma lista de consultas.
- **`git status` não mostra arquivo ignorado**: silêncio ali não é prova de versionamento
  (`git ls-files --error-unmatch <arquivo>` é a prova).
- **Modo de arquivo não sobrevive a `tar`/`git` sozinho.** O bit executável vive no **git** (100755 ×
  100644), não no sistema operacional: sincronizar por `tar`/checkout um script que está 100644 no git
  devolve 644 na cópia operacional e o `ExecStart` do systemd morre com `203/EXEC` — **mesmo que a máquina
  tenha rodado o timer ontem**. Antes de publicar um script por timer, conferir
  `git ls-files -s <arquivo>` e, na cópia operacional, `install -m 755`.
- **Dois `*.dump` no mesmo artefato: `ls *.dump | head -1` escolhe o errado.** Com o Odoo no mesmo
  diretório do trio, `odoo_dev.dump` vem **antes** de `sales_intelligence.dump` em ordem alfabética e
  o verificador compararia o banco errado (ou reprovaria um backup bom). Quem escolhe o dump é o
  **manifesto** (`banco:`), nunca o glob. Medido 01/10/2026.
- **O `CMD` da imagem do Odoo não é o binário.** O `/entrypoint.sh` faz `exec odoo "$@" "${DB_ARGS[@]}"`
  — os argumentos de banco que ele monta (com `HOST` default **`db`**) entram **depois** dos seus e
  vencem. Subir com `--db_host=<container>` não basta: `Database connection failure: could not
  translate host name "db"`. Para um alvo descartável, `--entrypoint /usr/bin/odoo` (sem entrypoint)
  e passar tudo na linha de comando. Medido 01/10/2026.
- **`GET` em `/web/webclient/version_info` devolve 415 (`Unsupported Media Type`)** — o endpoint é
  JSON-RPC e exige `POST` com `Content-Type: application/json`. Um teste que pede `GET` reprova um
  Odoo que está respondendo (o `HTTP 200` em `/web/login` é o critério que vale). Medido 01/10/2026.
- **Verificador com veredito no fim é lento quando o alvo está quebrado:** o `pg_restore` falha e o
  script ainda sobe o Odoo e espera o timeout de HTTP. Para um artefato truncado, ouça o
  `FALHOU sha256 do dump NAO confere` — ele já é a resposta; o resto é confirmação.
- **"Só o meu pedaço" na cópia operacional.** `tar -cz <subconjunto> | ssh … 'tar -xz -C /opt/tre/repo'`
  parece inofensivo e é o defeito: o `tar` da árvore de trabalho leva o modo do *checkout* (não o do
  git), **não apaga** o que não vai no pacote (arquivo velho sobrevive ao lado do novo) e não deixa
  registro de qual commit ficou no ar — a cópia de 30/09/2026 tinha 122 dos 300 arquivos versionados.
  `sha256` igual nos dois lados **do artefato que você lembrou de conferir** não é prova de que a cópia é
  o commit. Publique com `deploy/publicar.sh --commit <commit>` e confira com `--conferir` (§7e).
