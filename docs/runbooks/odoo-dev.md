# Runbook — Odoo Community no ambiente **dev** do TRE

**Card:** `TRE-W2-E01-T01` (`t_d6dc5a4c`, perfil `devops`) · **Status:** instalado e aceito em dev (01/10/2026)
**Máquina:** VPS Contabo `vmi3619453` (169.58.24.102) · **Ambiente:** dev — `homolog` e `prod` **não** provisionados (ADR-005)
**Artefatos versionados:** `deploy/compose/dev/odoo.yml`, `deploy/environments/dev-odoo.env`,
`scripts/provision/{instalar,verificar,remover}-odoo-dev.sh`

Este runbook é o registro da decisão (versão/portas/onde hospedar), do procedimento, do aceite
medido e do rollback — **testado**, não só escrito.

---

## 1. Decisão registrada (versão, portas e onde hospedar)

| Item | Decisão | Onde está registrado |
|---|---|---|
| **Onde hospedar** | VPS Contabo `vmi3619453`, árvore já provisionada `/opt/tre/dev/*` | ADR-0007, `provisionamento-contabo.md`, par do ambiente |
| **Versão** | **Odoo Community `19.0`** (release atual-1) | `deploy/environments/dev-odoo.env` (`ODOO_VERSION`), cabeçalho do `compose` e esta tabela |
| **Identidade do artefato** | digest do manifesto `sha256:77bac5cd1e065210828f34883a7f76740b7373d06dd3a5a55d3eeb31ee2f85cd` (`odoo:19.0`, imagem de 28/09/2026, `19.0-20260926`) | `deploy/environments/dev-odoo.env` (`ODOO_DIGEST_ESPERADO`), conferido item a item pelo verificador |
| **Porta do Odoo** | **`127.0.0.1:8069`** (só loopback) | `compose` (obrigatório, via `ODOO_HTTP_PORT`), verificador item 6 |
| **Porta do banco do Odoo** | **nenhuma** publicada; o Odoo fala com `pg-odoo-dev` pela rede interna `tre-odoo-dev` | `compose`, verificador item 6 |
| **UFW** | durante o T01: só a `22/tcp`. **Desde o T02** (feito): `22/80/443` — o proxy terminou TLS em 80/443 e o Odoo continua só em loopback | `odoo-dev-tls.md` §1.1; verificador dos dois cards |
| **Banco** | `odoo_dev` em `pg-odoo-dev` (volume `pgdata-odoo-dev`), **separado** de `sales_intelligence`/`pg-sales-dev` | `compose`, verificador item 5 |

**Por que 19.0 e não 20.0** (as duas existem no repo oficial; `20.0` foi construída em 28/09/2026):
19.0 é a release com um ano de correções e API estável para o módulo `transformativa_sales_ai` que as
próximas cards vão desenvolver — não se coloca um major recém-nascido como source of truth comercial.
Trocar é uma linha: `ODOO_VERSION` + `ODOO_DIGEST_ESPERADO` no par do ambiente.

**Ratificação:** a escolha foi feita pelo **executor em dev**, dentro da declaração de ação do dono para
este card (`hermes/jev/acoes-declaradas.yaml`, 01/10/2026, `ambiente_alvo: desenvolvimento`,
`producao=false`, `credencial=false`; recibo JEV `dec-c6650746cbb56e76` = PASS). Fica **pendente de
ratificação do Anderson** — e ela é obrigatória de qualquer forma antes de homologação/produção
(ADR-005), onde a aprovação humana é exigida card a card.

## 2. O que existe na VPS depois desta instalação

| Objeto | Valor medido (01/10/2026) |
|---|---|
| Containers | `odoo-dev` (`odoo:19.0`, id `12cf65a3c1c6…`, `restart=unless-stopped`) e `pg-odoo-dev` (`postgres:16`, id `c7cb12f75eb9…`, healthy) |
| Volumes | `pgdata-odoo-dev` (banco do Odoo), `odoo-data-dev` (filestore/sessões) |
| Rede | `tre-odoo-dev` (interna; `pg-odoo-dev` não publica porta) |
| Banco | `odoo_dev`, módulo `base` instalado, **sem dados de demonstração** |
| Segredos | `/etc/tre/odoo-dev/pg.env` (600 root) e `/etc/tre/odoo-dev/odoo.conf` (600, uid 100 — o usuário `odoo` do container); nenhum valor sai da VPS |
| Par do ambiente | `/opt/tre/dev/compose/odoo.yml` + `odoo.env` (cópia do que está versionado; sha256 igual nos dois lados) |
| Scripts operacionais | `/opt/tre/dev/scripts/{instalar,verificar,remover}-odoo-dev.sh` |

`odoo.conf` fica com **dono uid 100 / gid 101** e modo **600** porque o processo do Odoo roda como usuário
`odoo` (uid 100) dentro do container e precisa ler o arquivo; no host, uid 100 é o `dhcpcd` (nologin) — o
root do host lê via root e mais ninguém. O `pg.env` fica `600 root:root` (o daemon do Docker lê como root).

**Por que o Odoo do dev não roda da cópia operacional `/opt/tre/repo`:** os artefatos de publicação
(`deploy/publicar.sh`, watchdog, enforcement) vivem na linha `fix/t_daca4bda-enforcement`, **divergente do
`develop`** — publicar uma árvore nascida do `develop` apagaria o enforcement da cópia (regressão conhecida
dos defeitos F2/F3). Enquanto as duas linhas não se encontrarem, o dev usa o par em
`/opt/tre/dev/compose/`, e o compose continua **versionado no repo** (é o que o aceite pede).

## 3. Procedimento de instalação

```bash
# no repo (container do Hermes), transferindo o par e os scripts com conferência de identidade
ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 'cat > /opt/tre/dev/compose/odoo.yml' < deploy/compose/dev/odoo.yml
ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 'cat > /opt/tre/dev/compose/odoo.env' < deploy/environments/dev-odoo.env
ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 'cat > /opt/tre/dev/scripts/instalar-odoo-dev.sh' < scripts/provision/instalar-odoo-dev.sh
sha256sum deploy/compose/dev/odoo.yml            # e `sha256sum /opt/tre/dev/compose/odoo.yml` no outro lado: tem de bater

# na VPS
bash /opt/tre/dev/scripts/instalar-odoo-dev.sh
```

O script, em ordem: **guardas** (docker de pé, par e compose presentes, compose sob `/opt/tre/dev`,
nenhum container de outro ambiente, porta livre, `odoo-dev` inexistente sem `TRE_ODOO_RECRIAR=1`) →
**segredos** (gera `pg.env`/`odoo.conf` com `openssl rand -hex 24`; se já existem, confere que as duas
senhas concordam) → **`docker compose config`** → **banco** (`pg-odoo-dev` healthy; inicializa `odoo_dev`
com o módulo `base`, sem demo) → **serviço** (`up -d`, espera HTTP 200 em `/web/login`) → **evidência**
(imagem, digest, ids, hora, porta, banco). Nada de segredo em log, argumento ou artefato.

Rodar de novo é idempotente: com o banco já inicializado o serviço sobe sem reinicializar; com container
existente o script **recusa** e pede `TRE_ODOO_RECRIAR=1`.

## 4. Aceite — TEST PLAN medido

`bash /opt/tre/dev/scripts/verificar-odoo-dev.sh` → `RESULTADO: ODOO_DEV_OK (19 itens, 0 falhas)`, exit 0.
Itens: (1) `compose config` válido + imagem declarada igual à do par; (2) os dois containers de pé com
`restart=unless-stopped`; (3) imagem do container == tag local == digest registrado; (4) HTTP 200 real com
a página do Odoo e o binário respondendo `Odoo Server 19.0-20260926`, banco `odoo_dev` presente;
(5) separação do `sales_intelligence` medida **nos dois lados** (nenhum Postgres tem o banco do outro;
volumes distintos); (6) nenhuma porta pública (Odoo só em loopback, Postgres do Odoo sem porta publicada,
`ss` confirmando `127.0.0.1:8069`, UFW só com 22).

**Provas negativas que dão dente ao aceite (todas medidas nesta execução):**

| Prova | Resultado |
|---|---|
| `docker` falso respondendo `docker port odoo-dev` = `0.0.0.0:8069` | `FALHOU odoo-dev publica endereco publico` → `ODOO_DEV_FALHOU (19 itens, 1 falha)`, exit 1 |
| `remover-odoo-dev.sh` sem `TRE_ODOO_CONFIRMAR_REMOCAO=1` | `FALHOU remocao exige confirmacao explicita`, exit 1, nada tocado |
| Verificador rodado **depois** do rollback | `ODOO_DEV_FALHOU (19 itens, 13 falhas)`, exit 1 (o ambiente ausente não passa) |
| Reinicialização limpa (rollback + instalar de novo) | `ODOO_DEV_INSTALADO` exit 0 e aceite `19 itens, 0 falhas` de novo |

## 5. Rollback (testado — foi executado e a instalação foi refeita)

```bash
# na VPS
TRE_ODOO_CONFIRMAR_REMOCAO=1 bash /opt/tre/dev/scripts/remover-odoo-dev.sh
```

Remove: containers `odoo-dev`/`pg-odoo-dev`, rede `tre-odoo-dev`, volumes `pgdata-odoo-dev` e
`odoo-data-dev` (**os dados do Odoo vão junto** — é o rollback declarado) e `/etc/tre/odoo-dev`.
Preserva: `pg-sales-dev` e `sales_intelligence` (medido: `pg-sales-dev running` ao fim), a UFW, o par
não-secreto, o compose versionado e os scripts. O script recusa se algum container de
`homolog`/`producao` existir, se o caminho não for o do dev, ou se a lista de volumes citar a Sales
Intelligence.

## 6. Operação do dia a dia

```bash
# estado
docker compose --env-file /opt/tre/dev/compose/odoo.env -f /opt/tre/dev/compose/odoo.yml ps
# logs
docker compose --env-file /opt/tre/dev/compose/odoo.env -f /opt/tre/dev/compose/odoo.yml logs -f odoo-dev
# acesso local (a porta NÃO é pública): túnel do operador
ssh -i ~/.ssh/id_ed25519_ops -L 8069:127.0.0.1:8069 root@169.58.24.102   # depois: http://127.0.0.1:8069
# senha mestra / senha do banco (na VPS, como root)
sed -n 's/^admin_passwd = /master: /p' /etc/tre/odoo-dev/odoo.conf
# bases
docker exec pg-odoo-dev psql -U odoo -d postgres -c '\l'
```

Trocar a versão do Odoo: editar `ODOO_VERSION` (e `ODOO_DIGEST_ESPERADO`) em
`deploy/environments/dev-odoo.env`, transferir o par, `TRE_ODOO_RECRIAR=1 bash instalar-odoo-dev.sh`.
Aceitar a mudança exige o verificador de novo (o digest é conferido item a item).

## 7. Defeitos encontrados nesta execução (e conserto)

1. **Banco vazio aceito como "Odoo instalado".** O `POSTGRES_DB` do `postgres:16` já cria o banco
   `odoo_dev` **vazio**; o check por `pg_database` pulou a inicialização e o Odoo respondeu **HTTP 500**
   em `/web/login`. Conserto: o que prova inicialização é a tabela do módulo `base` (`ir_module_module`).
2. **`docker compose run` engoliu o resto do script.** Orquestrado por `ssh 'bash -s' < script`, o
   `compose run` lê o stdin (que é o próprio script) e mata o remoto no meio — a mesma armadilha já
   registrada no `CHANGELOG` deste projeto. Conserto: `-T` + `< /dev/null` no `compose run`, e os scripts
   passaram a ser executados de arquivo na VPS, não por stdin.
3. **Item 6 do próprio verificador reprovava formato, não comportamento.** `docker port` devolve
   `8069/tcp -> 127.0.0.1:8069` e o teste exigia prefixo `127.0.0.1:`. Conserto: o que reprova agora é
   endereço não-loopback (e a prova por mutação acima confirma que o item tem dente).
4. **O instalador reprovava o `secret_scan.sh` do próprio repo** por escrever a chave na forma literal
   `"<chave> = <variável>"` — falso positivo do scanner. Conserto **no código** (nome da chave em
   variável, leitura por `awk`), não no scanner: `secret_scan.sh` → `PASS`.
5. **A guarda de "porta em uso" reprovava a reexecução idempotente**: com o `odoo-dev` de pé, a 8069 é
   dele. Conserto: a guarda só vale quando o container `odoo-dev` ainda não existe.

Todas as cinco foram encontradas **executando** os caminhos (primeira instalação, rollback, reinstalação
e reexecução idempotente), não por leitura — e cada conserto foi remedido.

## 8. Pendências declaradas (não são deste card)

- **Ratificação da versão** pelo Anderson para homologação/produção (§1).
- **Backup do Odoo** (banco + filestore) — **RESOLVIDO 01/10/2026** pelo card `t_a5afde31`
  (branch `feature/TRE-W2-E01-T01-F01`). O artefato diário do ambiente passou a levar o Odoo junto
  (`odoo_dev.dump`, `odoo-contagens.txt`, `odoo-filestore.tar.gz` do volume `odoo-data-dev` e
  `odoo-manifest.txt` com o digest da imagem), e o restore é **provado** por
  `scripts/backup/verificar-odoo.sh` num alvo descartável com o Odoo respondendo HTTP 200
  (`RESTORE_ODOO_OK`) — detalhes, evidência e a proibição de `ls *.dump | head -1` em
  `docs/runbooks/backup-restore-rollback.md` §4.4/§7g/§9. O `verificar-ultimo-backup.sh` do domingo
  já encadeia o verificador do Odoo quando o artefato do ambiente o traz.
- **TLS/reverse proxy e exposição** — **feito** no card `TRE-W2-E01-T02`: `docs/runbooks/odoo-dev-tls.md`
  (Caddy em 80/443, `proxy_mode` no Odoo, `basic_auth` protegendo o dev, aceite 28/28 e dente 6/6).
  Falta a **decisão do dono** sobre o domínio (§1.2 daquele runbook) para o certificado público.
- **Cópia operacional em linha divergente do `develop`** (§2): enquanto não se encontrarem, o dev usa o
  par em `/opt/tre/dev/compose/`.
