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

## 2026-09-30 — VPS do TRE (Contabo vmi3619453, 169.58.24.102) — W1/E01: schema `sales_intelligence` em dev

- **Sincronizacao da copia operacional (agente):** `tar -cz db scripts deploy docs/{data,runbooks,operations} | ssh tre-deploy@… 'tar -xz -C /opt/tre/repo'` → `SYNC_OK`; sha256 da migration igual nos dois lados (`bc766a818943…`) e do runner (`1eb97c92a321…`).
- **Estado ANTES (read-only):** `bash /opt/tre/repo/scripts/db/estado_do_ambiente.sh dev` → `0 tabelas | 0 indices`, `Did not find any relations.`, tabela de controle ausente — nada de DDL existia no ambiente.
- **Aplicacao (agente):** `bash /opt/tre/repo/scripts/db/aplicar_migracoes.sh dev` → `OK migrations encontradas: 1 arquivo(s)`, `OK postgres responde em 'pg-sales-dev' (servidor definitivo, confirmado duas vezes)`, `OK tabela de controle pronta (public.tre_schema_migrations)`, `OK versao 0001 (0001_sales_intelligence_v1.sql) aplicada e registrada (bc766a818943…)`, `RESULTADO: MIGRACAO_OK …`, exit 0.
- **Idempotencia:** segunda execucao → `PULADO versao 0001 … ja aplicada com o mesmo sha256`, `RESULTADO: MIGRACAO_OK (… 0 aplicada(s), 1 pulada(s))`, exit 0.
- **Rollback exercitado de verdade:** `DROP SCHEMA IF EXISTS sales_intelligence CASCADE` (12 objetos) + `DELETE FROM public.tre_schema_migrations` → `DROP SCHEMA`, `DELETE 1`; reaplicacao pelo runner → `MIGRACAO_OK`, 12 tabelas de volta.
- **Listagem psql:** `estado_do_ambiente.sh dev` → `12 tabelas | 30 indices` e `\dt` com as 12 tabelas (agent_runs … sync_events), owner `sales_ai`.
- **Verificador do contrato CONTRA o banco do ambiente (agente):** `python3 scripts/verificar_contrato_dados.py --banco "docker exec pg-sales-dev psql -U sales_ai -d sales_intelligence"` → `RESULTADO: PASS (37 itens, 0 falhas)`, exit 0 (26 itens dos artefatos + 11 itens do banco real).
- **Provas negativas em container descartavel `tre-neg-7015-31853`** (criado e removido pelo agente, mesmo padrao do `scripts/backup/teste-backup-restore.sh`): runner do zero → `MIGRACAO_OK`; verificador → `PASS (37 itens)`; `DROP TABLE sales_intelligence.agent_runs` → `FALHOU (4 de 37)`, exit 1; coluna inventada em `scores` → `FALHOU (4 de 37)`; `DROP INDEX idx_organizations_domain` → `FALHOU (5 de 37)`; migration editada depois de aplicada (sha diferente) → `FALHOU … migration aplicada e IMUTAVEL`, exit 1.
- **Guardrail de producao:** `aplicar_migracoes.sh prod` → `FALHOU ADR-005: DDL nao nasce em producao…`, exit 1; com `TRE_APROVACAO_HUMANA` declarado → `FALHOU sequencia dev -> homolog -> producao: container de homolog 'pg-homolog' nao existe`, exit 1; ambiente inventado (`staging`) → exit 2. **Nada foi tocado em producao.**
- **Producao intocada (medido):** `docker ps -a` → so `pg-sales-dev`; `find /opt/tre/prod /opt/tre/homolog -type f` → nenhum arquivo.
- **Defeito encontrado e corrigido no mesmo card (detectado por teste, nao por leitura):** o runner deixava o arquivo versionado `deploy/environments/dev.env` sobrescrever a variavel do operador (a primeira prova negativa acabou rodando no ambiente dev) e usava `docker exec -i`, que consumia o stdin de quem orquestra por SSH (o script remoto morreu no meio). Correcao: variavel de ambiente passa a vencer o arquivo e a migration entra no container por `docker cp` + `psql -f`. O ambiente dev foi reparado pelo proprio plano de rollback (drop + reaplicacao) e reverificado: 12 tabelas, 30 indices, `PASS (37 itens)`.
- **Divergencia medida (decisao do dono, nao do agente):** o Data Contract V1.0 declara o banco de inteligencia como `transformativa_ai`; o ambiente dev provisionado usa banco `sales_intelligence`, container `pg-sales-dev` e usuario `sales_ai` — e os scripts de backup ja assumem `sales_intelligence` como padrao. O runner aplica no par declarado em `deploy/environments/dev.env`.
- Segredos: nenhum valor nesta entrada; a conexao usa o socket local do container (sem senha em argumento, arquivo ou log).

## 2026-09-30 — licoes de engenharia desta rodada (commit, recibo, git com worker)

1. **Contrato do recibo e fechado em 13 campos** (`hermes/jev/policy_v1_2.yaml`): rastro novo
   NUNCA vira campo novo no recibo — entra DENTRO de `override` (que existe para registrar
   excecao com razao). O motivo de uma aprovacao NAO aplicavel viaja na **resposta** do gate
   (que o board grava), nao no recibo.
2. **Nao commitar com suite vermelha** — inclusive a suite que "nao tem a ver" com a mudanca.
   Nesta rodada a suite nova passou (18 itens) e o `verificar_gate_jev.py` ficou vermelho por
   causa da mudanca; o commit saiu antes de ver. Regra: mudanca no gate roda, no minimo,
   `verificar_gate_jev.py` + `verificar_aprovacao_humana.py` ANTES do commit.
3. **Repo com worker rodando troca de branch sozinho**: o worker do card cria e faz checkout de
   `feature/<codigo-do-card>` no MESMO diretorio. Commit feito nesse momento cai na branch do
   worker, e `git push origin develop` vira no-op silencioso (`origin/develop` nao anda e o
   `git log` local engana). Procedimento correto: conferir `git branch --show-current` antes de
   commitar e **empurrar pelo SHA** (`git push origin <sha>:develop`), sem `checkout` — trocar
   de branch com worker em execucao atropela o worker.

## 2026-09-30 — Decisão 8 (dono): deixar os 7 pacotes segurados no host do Hermes

Depois da janela aprovada dos 8 pacotes (1 subiu: `linux-libc-dev` 6.8.0-142 -> 146), restaram
7 segurados (`libegl-mesa0`, `libgbm1`, `libgl1-mesa-dri`, `libglx-mesa0`, `mesa-libgallium`,
`mesa-vulkan-drivers`, `linux-image-virtual`). Anderson decidiu **deixar como está** (opção A),
com o racional medido: o kernel em uso e o maior instalado sao o mesmo (`6.8.0-142`), sem
`/var/run/reboot-required`, e `linux-image-virtual` e metapacote que ja aponta para ele — ganho
de zero; as bibliotecas GL so afetariam servico que use navegador headless no HOST. Subir os 7
exigiria `apt-get dist-upgrade` (mudanca de dependencia, com remocao possivel) num host que
sustenta 16 containers. Nada foi executado.
