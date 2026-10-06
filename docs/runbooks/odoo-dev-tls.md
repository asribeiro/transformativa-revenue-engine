# Runbook — TLS, reverse proxy e hardening do Odoo no ambiente **dev** do TRE

**Card:** `TRE-W2-E01-T02` (`t_1acf11f2`, perfil `devops`) · **Status:** instalado e aceito em dev (01/10/2026)
**Máquina:** VPS Contabo `vmi3619453` (169.58.24.102) · **Ambiente:** dev — `homolog` e `prod` **não** provisionados (ADR-005)
**Depende de:** `TRE-W2-E01-T01` (Odoo Community 19.0 em `127.0.0.1:8069` — runbook `odoo-dev.md`)
**Artefatos versionados:** `deploy/compose/dev/{Caddyfile,proxy.yml}`, `deploy/environments/dev-proxy.env`,
`scripts/provision/{instalar,verificar,remover}-proxy-dev.sh`, `scripts/provision/prova-de-dente-tls-dev.sh`

Este runbook é o registro da **decisão** (portas/entradas/exposição), do procedimento, do aceite
**medido**, do rollback **executado** e do que ficou pendente do dono.

---

## 1. Decisões registradas

### 1.1 Portas e entradas (decidido pelo executor em dev; o card manda "decidir portas/entradas e registrar")

| Porta | Entrada | Por quê | Onde está registrado |
|---|---|---|---|
| `22/tcp` | pública (já existia) | operação/SSH | já era o estado da VPS |
| `80/tcp` | **pública** | redirect para HTTPS + challenge ACME HTTP-01 do certificado público | `dev-proxy.env`, UFW, verificador item 14 |
| `443/tcp` | **pública** | HTTPS (o proxy atende aqui) | idem |
| `8069/tcp` | **loopback** | Odoo — **não** entra na UFW | verificador item 13 |
| `5432/5433/tcp` | **loopback** | Postgres — **não** entra na UFW | varredura externa |

`ufw status` (medido em 01/10/2026): **`22/tcp, 80/tcp, 443/tcp`** — e mais nada. O padrão da UFW
continua `deny (incoming)`; o `fail2ban` segue ativo.

### 1.2 Domínio — **decisão do dono, PENDENTE** (é o que falta para o card fechar)

O critério de aceite homologado diz "**Decisão do Anderson:** domínio, portas e exposição". Portas e
exposição foram decididas e registradas acima (mesmo padrão do T01, que decidiu versão/portas em dev
e deixou pendente de ratificação). O **domínio não é decidível nem executável por mim**:

- medido: `odoo-dev.transformativa.com.br` **não resolve**; a VPS **não tem PTR**; a zona
  `transformativa.com.br` está em NS1 (dns1..4.p02.nsone.net) e **não tem CAA** (Let's Encrypt livre);
- criar o registro A exige **credencial de DNS**, fora da declaração deste card (`credencial: false`);
- **proposta** (segue a convenção já usada na casa — `n8n.transformativa.com.br`):
  `odoo-dev.transformativa.com.br` → `169.58.24.102`.

Enquanto o registro não existir, o dev roda com a **CA interna do proxy** (`tls internal`): o
certificado é validado de verdade contra a raiz local (nunca `-k`). **Ligar o certificado público é
trocar uma linha** no `Caddyfile` (apagar `tls internal`) + ter o DNS apontando — não muda mais nada.

### 1.3 Proteção do dev exposto (`basic_auth`) — e por que ela não é decorativa

Medido neste ambiente antes de expor qualquer coisa: **a credencial padrão `admin`/`admin` do Odoo
APROVA** (`POST /web/login` → `303 SEE OTHER` para `/odoo`, com `session_id`). Um `odoo-dev` na
internet sem essa correção é *admin takeover* a um login de distância.

Por isso o dev vai atrás de `basic_auth` do proxy (credencial própria, gerada na VPS). **Não é o
conserto**: é a proteção enquanto o conserto não vem. O conserto da credencial do Odoo é achado
registrado (§7) e não é deste card — ele muda o banco do Odoo.

### 1.4 O que o proxy faz, além de terminar TLS

| Item | Estado | Verificado por |
|---|---|---|
| TLS termina no proxy; Odoo nunca recebe conexão pública | feito | itens 4, 13 |
| HTTP 80 → HTTPS (nada de conteúdo em texto claro) | feito | item 5 |
| HSTS + `X-Content-Type-Options` + `X-Frame-Options` + `Referrer-Policy`; cabeçalho `Server` removido | feito | item 8 |
| `/web/database*` (gerenciador de bases do Odoo) → **403** | feito | item 9 |
| Caminho do challenge ACME **não** fica atrás do `basic_auth` (senão a emissão pública não funciona) | medido (404, não 401) | item 10 |
| Sem a credencial do proxy, o Odoo **não** é servido | medido (401) | item 11 |
| Host desconhecido **não** recebe o Odoo (handshake TLS recusado) | medido | item 12 |
| API de administração do Caddy desligada (`admin off`) | feito | `Caddyfile` |

### 1.5 `proxy_mode` no Odoo — medido, não suposto

O Odoo 19 só aplica o `ProxyFix` quando **as duas** condições valem
(`http.py:2830`: `if config['proxy_mode'] and environ.get("HTTP_X_FORWARDED_HOST")`). Medida A/B no
alvo (A = com, B = sem `proxy_mode`), com `X-Forwarded-For: 203.0.113.77` (TEST-NET-3):

| | log do Odoo |
|---|---|
| **COM** `proxy_mode = True` | `werkzeug: 203.0.113.77 - - [...]` — honrou o cabeçalho |
| **SEM** `proxy_mode` | `werkzeug: 172.18.0.1 - - [...]` — registrou o par do socket |

Ou seja: sem isso, **todo acesso externo vira "o proxy" no log do Odoo** — auditoria e qualquer
proteção por IP (fail2ban/allowlist) perdem o cliente real. É por isso que o instalador garante a
linha, e o verificador **mede o efeito** (item 16), não só a presença no arquivo.

*(Medição que não deu em nada, registrada por honestidade: nas superfícies `Location`, URLs absolutas
da página de login e flag `Secure` do cookie de sessão, o A/B **não mostrou diferença**. O que muda
está na tabela acima; o resto não foi afirmado.)*

---

## 2. O que existe na VPS depois desta instalação

| Objeto | Valor medido (01/10/2026) |
|---|---|
| Container | `proxy-dev` (`caddy:2-alpine`, id `2f3b62f8fb44…`, `restart=unless-stopped`, **rede host**) |
| Imagem | `caddy:2-alpine` · digest `sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b` · binário `v2.11.4` |
| Volumes | `proxy-data-dev` (CA local + certificados), `proxy-config-dev`, `proxy-log-dev` |
| Segredos | `/etc/tre/proxy-dev/basicauth.env` (600 root) — usuário, senha e hash do `basic_auth`; **nenhum valor sai da VPS** |
| Estado do rollback | `/opt/tre/dev/proxy/ufw-antes.txt` — a UFW de antes, é o que o rollback devolve |
| Par do ambiente | `/opt/tre/dev/compose/{Caddyfile,proxy.yml,proxy.env}` (cópia do versionado; sha256 igual nos dois lados) |
| Scripts operacionais | `/opt/tre/dev/scripts/{instalar,verificar,remover}-proxy-dev.sh`, `prova-de-dente-tls-dev.sh` |

**Por que rede host:** o proxy precisa das duas pontas ao mesmo tempo — escutar 80/443 na interface
pública (é o único que publica porta neste ambiente) e falar com o Odoo em `127.0.0.1:8069`, que
continua loopback e não ganha regra nenhuma na UFW. Sem host networking o container não alcançaria o
loopback do host.

**O certificado de dev é de CA local** (raiz em `proxy-data-dev`, `Caddy Local Authority - 2026 ECC
Root`); a folha vale 12 h e é renovada sozinha. O verificador ancora na **raiz**, então a rotação não
o quebra.

---

## 3. Procedimento de instalação

```bash
# no repo (container do Hermes), transferindo com conferência de identidade
ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 'cat > /opt/tre/dev/compose/Caddyfile' < deploy/compose/dev/Caddyfile
ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 'cat > /opt/tre/dev/compose/proxy.yml' < deploy/compose/dev/proxy.yml
ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 'cat > /opt/tre/dev/compose/proxy.env' < deploy/environments/dev-proxy.env
ssh -i ~/.ssh/id_ed25519_ops root@169.58.24.102 'cat > /opt/tre/dev/scripts/instalar-proxy-dev.sh' < scripts/provision/instalar-proxy-dev.sh
bash /opt/tre/dev/scripts/instalar-proxy-dev.sh      # na VPS
```

O script, em ordem: **guardas** (docker, ufw, par e compose presentes, compose sob `/opt/tre/dev`,
nenhum container de outro ambiente, `odoo-dev` de pé — com espera curta, porque o dev é
compartilhado, porta livre, `proxy-dev` inexistente sem `TRE_PROXY_RECRIAR=1`) → **segredos** (gera
senha com `openssl rand` e o hash com o próprio `caddy hash-password`, senha por **stdin**) →
**UFW** (registra o estado anterior e libera 80/443) → **Odoo** (`proxy_mode`, com `restart` só se
mudou) → **compose** (`config -q` e `up -d`) → **espera de 200 real** por HTTPS com a âncora da CA →
**evidência** (imagem, digest, ids, hora, destino, portas).

---

## 4. Aceite — TEST PLAN medido

`bash /opt/tre/dev/scripts/verificar-tls-dev.sh` → **`RESULTADO: TLS_DEV_OK (28 itens, 0 falhas)`**, exit 0.
Itens: compose válido e imagem declarada; container de pé com `restart=unless-stopped` e rede host;
imagem e digest iguais ao par; 80/443 escutando em endereço público; HTTP 80 redireciona para HTTPS;
HTTPS 200 com a **cadeia validada contra a âncora**; **sem a âncora o pedido falha** (prova de que a
validação é real); cabeçalhos de hardening presentes e `Server` removido; `/web/database/manager` →
403; challenge ACME livre; **sem credencial do proxy o Odoo não é servido**; Host desconhecido não
recebe o Odoo; 8069 só loopback e sem regra na UFW; UFW com exatamente `22/80/443` e `fail2ban`
ativo; Odoo respondendo 200 em loopback; `proxy_mode` **com efeito medido**.

**Provas negativas que dão dente ao aceite** (`bash /opt/tre/dev/scripts/prova-de-dente-tls-dev.sh` →
`TLS_DENTE_OK (6 itens, 0 falhas)`):

| Prova | Resultado |
|---|---|
| Controle: alvo íntegro antes de mutar | `TLS_DEV_OK (28 itens, 0 falhas)` |
| D1 `docker port odoo-dev` dizendo `0.0.0.0` | `TLS_DEV_FALHOU (28 itens, 1 falha)` — item da porta administrativa reprova |
| D2 `ufw status` com a 8069 liberada | `TLS_DEV_FALHOU (28 itens, 2 falhas)` — itens de UFW reprovam |
| D3 proxy mutante **sem** `basic_auth` (porta 8443) | `TLS_DEV_FALHOU (28 itens, 5 falhas)` — "dev exposto sem proteção" reprova |
| D4 `proxy_mode` removido de verdade (com restart) | `TLS_DEV_FALHOU (28 itens, 2 falhas)` — item 16 reprova |
| D4 desfeito | volta a `TLS_DEV_OK (28 itens, 0 falhas)` |
| **D5** verificador rodado **depois do rollback** | `TLS_DEV_FALHOU (24 itens, 15 falhas)` — o ambiente ausente não passa |

### Evidência externa (medida de FORA da VPS, do container do Hermes — ponto de vista de terceiro)

| Medida | Resultado |
|---|---|
| Varredura de portas | **3 abertas**: `22`, `80`, `443`. `8069`, `8071`, `8072`, `5432`, `5433`, `8443`, `2019` **fechadas/filtradas** |
| Chamada externa HTTPS (sem `-k`, sem credencial) | `HTTP 401` — o TLS **validou a cadeia** e o `basic_auth` barrou; o corpo **não** é o Odoo |
| A mesma chamada **sem a âncora** | `exit 60` (certificado não confiável) — prova de que a validação do item acima é real |
| HTTP 80 externo | `308` → `https://odoo-dev.transformativa.com.br/web/login` |
| Host desconhecido, de fora | handshake TLS **recusado** (nenhum certificado para esse nome) |
| Quem serviu o 401 | o log do próprio Caddy, com a origem externa `187.127.56.17` |
| `ufw status` | `22/tcp`, `80/tcp`, `443/tcp` |

> A credencial do proxy **não saiu da VPS**: a chamada externa vai sem ela. O 200 *com* credencial é
> medido na VPS pelo verificador (item 6). O único material que atravessou foi a **raiz da CA**, que
> é um certificado público.

---

## 5. Rollback (executado — não só escrito)

```bash
# na VPS
TRE_PROXY_CONFIRMAR_REMOCAO=1 bash /opt/tre/dev/scripts/remover-proxy-dev.sh
```

Remove: container `proxy-dev`, volumes `proxy-*-dev`, segredos `/etc/tre/proxy-dev`, o
`proxy_mode = True` que o instalador acrescentou, e **devolve a UFW ao estado anterior** (lê
`ufw-antes.txt`; remove só o que este card abriu — não reabre nada às cegas).
Preserva: Odoo e Postgres do Odoo (containers, volumes e dados), `pg-sales-dev`/`sales_intelligence`,
`/opt/tre/{homolog,prod}`, o par não-secreto e o compose versionados.
Sem `TRE_PROXY_CONFIRMAR_REMOCAO=1` o script **recusa** e não toca em nada (medido).

**Testado de verdade em 01/10/2026:** recusa sem confirmação → rollback completo (UFW de volta a
`[22/tcp]`, `pg-sales-dev` e `odoo-dev` intactos) → verificador **reprova** (`TLS_DEV_FALHOU`,
15 falhas) → **reinstalação limpa** (`PROXY_DEV_INSTALADO … https=200`) → aceite de novo
`TLS_DEV_OK (28 itens, 0 falhas)` → e o aceite do card anterior continua verde
(`verificar-odoo-dev.sh` → `ODOO_DEV_OK (19 itens, 0 falhas)`).

---

## 6. Operação do dia a dia

```bash
# estado
docker compose --env-file /opt/tre/dev/compose/proxy.env -f /opt/tre/dev/compose/proxy.yml ps
# logs (acesso + erros)
docker exec proxy-dev tail -f /var/log/caddy/acesso.log
# credencial do dev (na VPS, como root) — o valor não aparece em log nenhum
sed -n 's/^TRE_PROXY_SENHA=//p' /etc/tre/proxy-dev/basicauth.env   # vem entre aspas simples
# ligar o certificado PUBLICO (depois do registro DNS do dono)
#   editar /opt/tre/dev/compose/Caddyfile: apagar a linha `tls internal`
#   docker compose --env-file ... -f ... up -d --force-recreate
#   rodar o verificador de novo (ele passa a validar contra o store do sistema, sem --cacert)
```

---

## 7. Achados e defeitos desta execução

**Achados (não são defeitos deste card — precisam de card/decisão):**

1. **A credencial padrão `admin`/`admin` do Odoo dev APROVA** (medido: `303` para `/odoo` +
   `session_id`). O dev exposto está protegido pelo `basic_auth` do proxy, mas isso é contenção, não
   conserto. **Precisa ser corrigido antes de homologação/produção** (muda o banco do Odoo → card
   próprio).
2. **O cookie de sessão do Odoo não leva `Secure`** — medido nas duas situações (com e sem
   `proxy_mode`). Num serviço que só é acessível por HTTPS, é endurecimento que falta.

**Defeitos encontrados e consertados nesta execução (todos executando os caminhos):**

| # | Defeito | Conserto |
|---|---|---|
| 1 | `caddy hash-password` lê **uma linha** do stdin: sem `\n` final morre com `Error: EOF`; e a primeira versão mandava o stderr para `/dev/null`, então o instalador **morria sem dizer por quê** | senha por `printf '%s\n'` + erro capturado e **mostrado** |
| 2 | O item 7 do próprio verificador estava **invertido**: tratava "curl falhou sem a âncora" (o resultado BOM) como reprovação | lógica reescrita com nome que diz o que quer dizer (`SEM_ANCORA_PASSOU`) |
| 3 | `source` no arquivo de segredos **quebra o script** na reexecução: o hash bcrypt tem `$2a$14$…` e o shell, sob `set -u`, tenta expandir `$2` | leitura por `sed`/`le_segredo`, como o T01 já faz |
| 4 | **`docker compose` interpola `$` também nos valores de `env_file`**: o sal do bcrypt (`$` + letras) é lido como nome de variável e vira string vazia → o container recebia um hash **truncado** (`hashedSecret too short`) e o `basic_auth` recusava tudo. **Intermitente**: depende do 1º caractere do sal sorteado (na 1ª instalação passou, na reinstalação quebrou) | valores escritos **entre aspas simples** no `env_file` (medido: o compose passa o literal e o container recebe **sem** as aspas); as aspas são removidas na leitura |
| 5 | O rollback acusava `regra do estado anterior nao esta mais presente: To / --`: o estado anterior é a saída crua do `ufw status`, **com o cabeçalho** | filtro por `ALLOW` na leitura do estado guardado |
| 6 | Colisão com outro card `devops` rodando no mesmo dev (o `odoo-dev` apareceu `Exited (0)` por um `compose run` de fora) | guarda com **espera curta e explícita** (e o achado de hotspot foi registrado no board) |

Todas foram achadas **executando** (instalação, aceite, dente, rollback, reinstalação), e cada
conserto foi remedido.

---

## 8. Pendências declaradas (não são deste card)

- **Domínio + registro DNS** — decisão do Anderson (§1.2). É o que falta para o certificado público.
- **Ratificação** das portas/exposição decididas em dev (§1.1), como no T01.
- **Credencial do Odoo** (`admin`/`admin`) e **cookie sem `Secure`** (§7) — antes de homolog/prod.
- **Backup do Odoo** — card `TRE-W2-E01-T01-F01` (`t_a5afde31`), em execução.
- **Colisão de cards `devops` no mesmo dev** — serializar quem toca `/opt/tre/dev` e os containers
  `*-dev` (registrado como hotspot no card `t_1acf11f2`).
