# Registro de execuções do agente nas máquinas

Regra (decisão 5 do desenho `docs/architecture/aprovacao-humana-com-executor.md`): toda
execução do agente em máquina operada por ele entra aqui com **comando e saída reais**, para
a auditoria não depender da narrativa do agente. Uma linha por execução, datada, com o
identificador da máquina. Segredos nunca aparecem aqui.

## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102)

- **Chave de acesso instalada** (por Anderson, no terminal dele): `authorized_keys` do root
  recebeu `hermes-ops@transformativa` (fingerprint `SHA256:oUy0ikl21DXxRDrONQlLD4l/jl1p+KJ92ffnYZAA4uA`).
- **Teste de conexão (agente):** `ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102` →
  `CONECTOU`, host `vmi3619453`, `Docker version 29.8.1, build 4a63305`.
- **Tentativa de subir o banco dev (agente):** script `tre_pg_dev_up_v3.cmd` enviado por stdin
  → guarda fail-closed respondeu `container pg-sales-dev: JA EXISTE - nao mexo nele,
  pare aqui e me chame` / `fim. abort=1`. **Nada foi criado nem alterado por mim.**
- **Medição read-only do que já existia (agente):** `pg-sales-dev | Up 4 minutes |
  127.0.0.1:5433->5432/tcp`; `image=postgres:16 restart=unless-stopped`;
  `POSTGRES_DB=sales_intelligence POSTGRES_USER=sales_ai`; volume `pgdata-sales-dev`;
  `/root/.tre_pg_dev.env` 600 (51 bytes); `pg_isready` → `accepting connections`;
  `SHOW server_version` → `16.15 (Debian 16.15-1.pgdg13+2)`.
- Origem do container existente: as tentativas anteriores no terminal do dono (o colar
  quebrado abortou por sintaxe, mas uma delas completou). Estado é o esperado pelo desenho.
## 2026-09-30 — host do Hermes (Hostinger srv1912562, 187.127.56.17)

- **Chave de acesso instalada** (por Anderson, no terminal dele): `authorized_keys` do root
  recebeu `hermes-ops@transformativa`.
- **Teste (agente):** `ssh -i ~/.ssh/id_ed25519_ops root@187.127.56.17` -> host `srv1912562`,
  **16 containers** rodando.
- **Janela aprovada pelo dono:** "podemos rodar os 8 pacotes com reboot".
- **Dry-run e upgrade (agente):** `apt-get -qq update` + `apt-get -y -qq upgrade` ->
  **1 pacote subiu** (`linux-libc-dev` 6.8.0-142 -> 6.8.0-146). O `needrestart` reiniciou
  `cron`, `ssh`, `systemd-journald`, `systemd-networkd`, `systemd-resolved`,
  `systemd-timesyncd`, `systemd-udevd` (a conexao do agente nao caiu) e **diferiu**
  `systemd-logind`, `dbus`, `unattended-upgrades`. Saida explicita: "No containers need to
  be restarted". Medicao posterior: **16 containers no ar** e **6 servicos
  transformativa-\* ativos** — nada perdido.
- **Reboot NAO executado, por medicao:** kernel em uso `6.8.0-142-generic` e o maior
  instalado em `/boot` e o mesmo `6.8.0-142`; `needrestart` reportou "Running kernel seems
  to be up-to-date"; sem `/var/run/reboot-required`. Reiniciar derrubaria 16 containers (e o
  proprio agente) sem ganho medido. Decisao devolvida ao dono.
- **7 pacotes segurados (kept back)**, nao tocados: `libegl-mesa0`, `libgbm1`,
  `libgl1-mesa-dri`, `libglx-mesa0`, `mesa-libgallium`, `mesa-vulkan-drivers`
  (bibliotecas GL, tipicamente dependencia de navegador headless) e `linux-image-virtual`
  (metapacote que hoje aponta para o kernel ja instalado — bump cosmetico). Subir exige
  `apt-get dist-upgrade` (mudanca de dependencias), nao executado.

## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — TRE-W1-E06-T01: ciclo de backup/restore contra o banco do DEV

- **Linha de base (read-only):** 12 tabelas, 30 indices, 14 linhas (`organizations 2`, `scores 2`, as outras 10 com 1); `docker ps -a` so `pg-sales-dev`.
- **Ciclo em modo ambiente real (agente, como `root`):** `set -a; . /etc/tre/backup.env; set +a; /opt/tre/repo/scripts/backup/teste-backup-restore.sh /opt/tre/repo --ambiente dev` -> `origem: pg-sales-dev`, `OK origem e o container do ambiente dev ... (nenhum container descartavel de origem)`, artefato `tre_dev_20260930T193704Z`, `OK manifesto registra o envio externo (externo: enviado (contabo:tre-backup))`, `RESULTADO: TESTE_OK (14 itens, 0 falhas)`, **exit 0**.
- **Producao intocada / dev intacto:** `docker inspect pg-sales-dev` com o **mesmo** `Id` (`396ace5637103224a550ab0b9687744e43ccdb32cb242ffc8e0dd6087a68a152`) e `StartedAt` (`2026-09-30T17:05:15.574220813Z`) antes e depois; `docker ps -a` sem container `tre-smoke-src`; so `pg-sales-dev` mais descartaveis de outros cards.
- **Verificacao independente:** `sha256sum` do dump == manifesto == `.sha256` (`f92f924d…`); `pg_restore --list` dentro de container -> 68 objetos; restore proprio (`pg_restore --no-owner --no-privileges`) -> contagens **identicas** ao `contagens.txt` (diff vazio, 14 linhas); `rclone lsl contabo:tre-backup/tre_dev_20260930T193704Z` -> 6 objetos; **prova de volta** (`rclone copyto` do dump + `sha256sum` + `cmp`) -> `IDENTICOS`.
- **Negativos (todos reprovados como deviam):** contagem mutada -> `RESTORE_FALHOU (1 falha)` exit 1; dump de 0 byte -> `RESTORE_FALHOU` exit 1; dump valido de outro banco -> `RESTORE_FALHOU (5 falhas)` exit 1; dump truncado a 2 KB -> `RESTORE_FALHOU (8 falhas)` exit 1; destino externo inexistente -> `BACKUP_FALHOU`, manifesto `externo: falhou`, exit 1. Fronteiras: base com 0 linhas -> `RESTORE_OK` com `NOTA backup sem linhas`; destino externo vazio -> `externo: pendente` exit 0; `--ambiente prod` -> recusado, exit 2; container inexistente -> exit 1 sem cair para descartavel.
- **TESTE NEGATIVO DO PROPRIO TIMER (achado 1):** `systemctl start tre-backup.service` -> `Job for tre-backup.service failed … code=exited, status=203/EXEC`; journal: `Unable to locate executable '/opt/tre/repo/scripts/backup/backup-tre.sh': Permission denied`. Causa raiz medida: `git ls-files -s scripts/backup/` -> **100644** (sem bit executavel) e a copia operacional `-rw-r--r--`; o unit tem `ExecStart=…/backup-tre.sh todos`. `systemctl reset-failed` aplicado depois.
- **TESTE NEGATIVO DA ROTINA (achado 2):** com a config real do timer (`. /etc/tre/backup.env`), `bash /opt/tre/repo/scripts/backup/backup-tre.sh todos` -> `PULADO ambiente dev/homolog/prod: container 'pg-dev'/'pg-homolog'/'pg-prod' nao existe`, `RESULTADO: BACKUP_OK (todos)`, exit 0, **zero artefato novo** em `/opt/tre/backup` (o trio real do dev e `pg-sales-dev`/`sales_ai`, declarado so em `deploy/environments/dev.env`, que nenhum timer le).
- **Concorrencia medida:** a copia operacional `/opt/tre/repo` foi sobrescrita por outro card durante a rodada (sha do driver `d29c9c97…` -> `9f24572a…`); o ciclo principal ja havia rodado com o driver correto (sha impresso no inicio da execucao).
- **Restricao de execucao registrada:** o ciclo rodou como `root` (acesso do agente); a execucao do ciclo sob `tre-deploy` com as variaveis do timer nao foi possivel nesta sessao (encadeamento de privilegio bloqueado pelo guard de comandos do harness). Provado por partes: `sudo -u tre-deploy test -w /opt/tre/backup` -> OK; `sudo -u tre-deploy rclone lsl --config /etc/tre/rclone.conf contabo:tre-backup/…` -> OK.
- **O que NAO foi tocado:** producao (nao existe na maquina), `homolog`, os scripts sob teste (`backup-tre.sh 1a430637…`, `verificar-backup.sh 885ad388…`, `restore-tre.sh c1750401…`), o `.env`/segredos (nenhum valor em argumento, arquivo ou log).
