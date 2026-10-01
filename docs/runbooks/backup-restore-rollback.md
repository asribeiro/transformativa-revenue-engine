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

Segredo se recupera do cofre (`docs/operations/gestao-de-secrets.md`), **não** de arquivo de
backup. Um diretório de backup que carrega senha vira um vazamento com data marcada.

## 2. Rotina automática

Instalação (uma vez, com `sudo`): `scripts/backup/instalar-timers.sh`

| Timer | Quando | O que faz |
|---|---|---|
| `tre-backup.timer` | diário **02:30** (America/Sao_Paulo) | `backup-tre.sh todos` — copia dev, homolog e prod |
| `tre-backup-verify.timer` | domingo **04:00** | `verificar-ultimo-backup.sh todos` — **restaura de verdade** o artefato mais recente e compara |

- Artefatos: `/opt/tre/backup/tre_<ambiente>_<YYYYmmddTHHMMSSZ>/` (permissão 700).
- Retenção: **14 dias** (`TRE_BACKUP_RETENCAO_DIAS`), aplicada só ao prefixo do próprio ambiente.
- Configuração: `/etc/tre/backup.env` (caminhos e destino — **sem segredo**).
- Ambiente ainda não provisionado é **pulado**, não falha: um timer cobre os três desde já.
- Ambiente provisionado **sem** backup é **falha** na verificação, assim como backup com
  **mais de 48h** (é o sinal de que a rotina parou).

## 3. BACKUP — manual

```bash
# um ambiente
TRE_PG_SERVICO=pg-dev scripts/backup/backup-tre.sh dev

# os tres (o que o timer roda)
scripts/backup/backup-tre.sh todos
```

O script não confia em nada: confere que o serviço responde, gera o dump, exporta globais,
extrai as contagens por tabela, grava `sha256` e o manifesto. Falha em qualquer item ⇒
`RESULTADO: BACKUP_FALHOU` e saída diferente de zero (o timer registra no journal).

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

Comando: `set -a; . /etc/tre/backup.env; set +a; bash scripts/backup/teste-backup-restore.sh /opt/tre/repo --ambiente dev`
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
| `sudo -u tre-deploy test -x /opt/tre/repo/scripts/backup/backup-tre.sh` | exit 1 | **exit 0** |
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

| UTC | Evento (fonte) | Estado de `/opt/tre/repo/scripts/backup/*.sh` |
|---|---|---|
| 20:03:36–37 | `install -m 755` deste card (§7c) | `755`, `test -x` exit 0, `systemctl start` = `success` |
| 20:03:59 / 20:06:19 | publicação de teste do card `t_091cfea9` com `commit=16c31f0…` (**anterior à correção**) | volta a `644` (ctime 20:04:15 UTC), `instalar-timers.sh` sem a guarda |
| 20:10:21 / 20:11:59 | medição da **revisão** (§ comentário 110): `test -x` exit 1, `start` → `ExecMainStatus=203/EXEC` | `644` |
| 20:12:35 | publicação **versionada** do card `t_091cfea9` (`commit=f1f1cb6b…` — fora de ref hoje, nota de rastreabilidade no fim deste § —, `/opt/tre/repo/.publicado`, `execstart_sem_bit: 0`) | `755` de novo |
| **20:13:36–37** | **remedição deste card (rodada 2)** | `755` |

Remedição de 20:13:36Z, na VPS `vmi3619453`:

- `sudo -u tre-deploy test -x /opt/tre/repo/scripts/backup/backup-tre.sh` → **`TEST_X_EXIT=0`**;
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
- **ACHADO ABERTO 2 (alta) — a rotina cobre zero ambientes e sai `BACKUP_OK`.** `backup-tre.sh` procura
  `pg-dev`/`pg-homolog`/`pg-prod`, mas o dev real é `pg-sales-dev` (usuário `sales_ai`), e
  `/etc/tre/backup.env` (o `EnvironmentFile` do unit) não declara `TRE_PG_SERVICO`/`TRE_PG_USER`/`TRE_PG_DB`
  — o trio real está em `deploy/environments/dev.env`, que nenhum timer lê. Resultado medido:
  `PULADO` nos três ambientes, exit 0, **nenhum artefato novo**. O `tre-backup-verify.timer` também não
  acusa, porque procura o mesmo prefixo `tre_dev_*` que a rotina nunca produz.
  Alinhar (ou congelar) o nome do container é **decisão do dono** — o card pode declarar o trio no
  `backup.env` (caminho mínimo, sem renomear container nem mover dado).
- **ACHADO ABERTO 3 (média) — a cópia operacional `/opt/tre/repo` é reescrita por qualquer card.** Durante
  a rodada do `TRE-W1-E06-T01`, um `tar` de outro worker reverteu o driver recém-instalado (sha
  `d29c9c97…` → `9f24572a…`). Quem sincroniza por último manda: a cópia operacional não é reproduzível.
  **Atualização 30/09/2026 20:12:35Z (medida):** o card `t_091cfea9` passou a publicar por caminho
  versionado — `/opt/tre/repo/.publicado` registra `commit: f1f1cb6b…` (fora de ref hoje, nota de rastreabilidade
  no §7d), `execstart_sem_bit: 0` e
  `concorrencia: (nenhuma)` — e essa publicação preservou o modo (`755`) e restaurou a cópia executável
  (§7d). O achado segue **aberto** enquanto esse não for o **único** caminho de publicação em uso.
- **Usuário do timer não exercitado de ponta a ponta:** o ciclo foi rodado como `root` (acesso do agente,
  decisão 5); provou-se por partes que `tre-deploy` escreve em `/opt/tre/backup` (`test -w`) e usa a
  credencial do bucket (`rclone lsl --config /etc/tre/rclone.conf`). Rodar o ciclo inteiro como
  `tre-deploy` requer um canal de privilégio que o harness bloqueia hoje.
  **Atualização 30/09/2026:** superado para os units — `tre-backup-verify.service` rodou o restore real sob
  `tre-deploy` (§7c item 2); o backup com o trio real do ambiente continua pendente do ACHADO ABERTO 2.
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
- **Restauração do Odoo** (arquivos + banco) entra quando o Odoo subir (W2): este runbook cobre
  o PostgreSQL.

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
