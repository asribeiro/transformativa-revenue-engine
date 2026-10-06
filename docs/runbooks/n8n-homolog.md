# Runbook — n8n no ambiente HOMOLOG do TRE

Serviço de automação do projeto (workflows de ingestão, consumidor de outbox, reconciliação e
observabilidade). Este runbook cobre **o n8n provisionado como serviço do ambiente homolog**, em
`/opt/tre/homolog`, na VPS Contabo `vmi3619453` (169.58.24.102).

- Container: **`n8n-homolog`** · imagem **`n8nio/n8n:2.41.5`** (digest `sha256:6f532d3b819c…`)
- Porta: **127.0.0.1:5681 → 5678** (loopback; quem entra de fora é a borda, por hostname)
- Estado: **`/opt/tre/homolog/n8n/home`** (bind mount no `/home/node` do container, dono `1000:1000`, 700)
- Segredo: **`/etc/tre/n8n-homolog/n8n.env`** (600 root; `N8N_ENCRYPTION_KEY` gerada na VPS)
- Rede interna: **`tre-odoo-homolog`** (é por ela que o Odoo chama o webhook e o n8n chama a API)
- Artefatos versionados: `deploy/compose/homolog/n8n.yml`, `deploy/environments/homolog-n8n.env`,
  `scripts/provision/{instalar,verificar}-n8n-homolog.sh`

## 1. Versão: por que 2.41.5 e não "latest"

`n8nio/n8n:latest` nesta VPS **é** 2.41.5 (medido: `docker run --rm --entrypoint n8n
n8nio/n8n:latest --version`) e é a versão que as suítes de n8n do projeto (`scripts/n8n/*`)
exercitaram. Tag flutuante não é aceita: a identidade do que está no ar é o **digest**, conferido
pelo verificador. A estável do npm é 2.41.7 — subir de versão é trocar `N8N_VERSAO` +
`N8N_DIGEST_ESPERADO` no par **depois** de re-rodar as suítes de n8n.

## 2. Instalar / subir

```bash
# na VPS
bash /opt/tre/homolog/compose/instalar-n8n-homolog.sh
# reexecutar sobre instalação existente (não recria em silêncio):
TRE_N8N_RECRIAR=1 bash /opt/tre/homolog/compose/instalar-n8n-homolog.sh
```

O instalador **recusa** (fail-closed) se: o daemon não responde; o compose não está sob
`/opt/tre/homolog`; a rede `tre-odoo-homolog` ou o container `odoo-homolog` não existem (o n8n do homolog depende
dos dois); a porta 5681 está ocupada sem container nosso; o digest baixado difere do registrado.

## 3. Verificar (aceite)

```bash
bash /opt/tre/homolog/compose/verificar-n8n-homolog.sh              # 44 itens
bash /opt/tre/homolog/compose/verificar-n8n-homolog.sh --prova-de-dente   # 3 mutações em cópia
```

Esperado: `RESULTADO: N8N_HOMOLOG_OK (44 itens, 0 falhas)` e `N8N_HOMOLOG_DENTE_OK (3 dentes, 0 ruins)`.

O que ele mede, além do óbvio: identidade da imagem em duas pontas (digest do registry do tag **e**
id da imagem que o container roda); porta só em loopback (`ss` + JSON do docker, sem 0.0.0.0);
`healthy` no healthcheck; variáveis que fazem os workflows funcionarem (`N8N_BLOCK_ENV_ACCESS_IN_NODE=false`
para os nós Code lerem `$env`, `N8N_HOST=n8n-homolog` para o webhook nascer com nome interno);
a chave existe, é 600 e **não** aparece no artefato publicado nem em argv; o estado está no bind
mount (`~/.n8n/database.sqlite`); os dois sentidos da rede (n8n→Odoo e Odoo→n8n, este último é o
caminho do webhook); e **contaminação**: nenhum n8n de outro ambiente (dev/produção) na minha rede ou na minha porta.

## 4. Operação

```bash
docker logs --tail 100 n8n-homolog
docker restart n8n-homolog
docker inspect n8n-homolog --format '{{.State.Health.Status}}'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5681/healthz/readiness   # 200
```

## 5. Rollback

```bash
docker compose --env-file /opt/tre/homolog/compose/n8n.env -f /opt/tre/homolog/compose/n8n.yml stop
docker compose --env-file /opt/tre/homolog/compose/n8n.env -f /opt/tre/homolog/compose/n8n.yml down
# estado e segredo ficam intactos: /opt/tre/homolog/n8n/home e /etc/tre/n8n-homolog/n8n.env
```

`down -v` **não** apaga nada aqui (não há volume nomeado — o estado é bind mount), mas evite: o
`down` simples já é o suficiente e reversível.

## 6. Armadilhas medidas (não repetir)

1. **HOME inteiro tem de ser montado.** Com `--user`, o n8n escreve `~/.n8n` **e** `~/.cache`;
   montar só o `.n8n` morre com `EACCES mkdir '/home/node/.cache'`. Por isso o bind é em `/home/node`.
2. **`install -d -o 1000:1000` não existe.** O `install` só aceita **nome** de usuário
   (`getpwnam`) e morre com `install: invalid user: '1000:1000'`. Use `mkdir -p` + `chown 1000:1000`.
3. **Cuidado ao concatenar uid/gid:** `chown -R "$UID":"$UID"` com `UID="1000:1000"` vira
   `1000:1000:1000:1000` → `chown: invalid group`.
4. **Segredo antes de validar o compose.** `docker compose config` resolve o `env_file` e **falha**
   enquanto o arquivo de segredo não existe — a ordem é segredo → validação. (Armadilha medida duas
   vezes neste projeto: dev e homolog.)
5. **Digest do registry ≠ manifesto por-plataforma.** `RepoDigests` do tag é o manifesto
   multi-arquitetura (o que o par registra); `.ImageManifestDescriptor.Digest` do container é o
   manifesto da plataforma (outro valor, por desenho). Comparar os dois dá falso positivo; a prova
   de que o container roda o tag é o **ID da imagem**.
6. **Porta no JSON do docker não é `"ip:porta"`:** é `{"5678/tcp":[{"HostIp":"127.0.0.1","HostPort":"5680"}]}`.
7. **Chave que já existe não se reescreve:** trocar `N8N_ENCRYPTION_KEY` invalida as credenciais
   já cifradas nos workflows. O instalador preserva.
8. **O uid 1000 do host é o usuário `ubuntu`** (a VPS não tem o `node` do container). O diretório
   é 700 e a chave vive em `/etc/tre/n8n-homolog` (600 root) — quem lesse o `database.sqlite` não
   teria como decifrar as credenciais.

## 6.1 Diferenças em relação ao dev (deliberadas)

- Relação completa de diferenças está em `docs/operations/ambientes-e-promocao.md`: o Homolog **não**
  tem o banco de vendas (`sales_intelligence`) nem o trio `TRE_PG_*` de backup apontado — de propósito.
- O serviço de homolog nasceu **do mesmo artefato** do dev (derivação mecânica + correção dos
  conjuntos *próprio × outro*), para que diferença de ambiente seja detectada por medição, não por
  digitação.

## 7. O ciclo ponta a ponta em Homolog (medido em 06/10/2026)

Credenciais e workflows **importados** neste serviço, e as duas agendas ligadas.

| peça | identificador | estado |
|---|---|---|
| workflow ingestor (webhook) | `TREodooEventos1` | **publicado** — `POST /webhook/tre/odoo-eventos` (403 sem token válido) |
| workflow consumidor (poll de 1 min) | `TREOUTBOXCONSUM1` | **publicado** — processa a fila sozinho |
| cron do módulo no Odoo (`cron_tf_entregar_eventos`) | `ir_cron` id 18 | **ativo** (1 min) |
| credenciais | ids do contrato: `tre-dev-postgres`, `tre-dev-api-controlada`, `tre-dev-ingest-token` | no cofre do n8n, apontando para os recursos **de Homolog** |

Ligar/desligar (a ativação desta versão é `publish:workflow`, e exige reiniciar o serviço):

```bash
docker exec --user 1000:1000 n8n-homolog n8n publish:workflow --id=TREodooEventos1
docker exec --user 1000:1000 n8n-homolog n8n publish:workflow --id=TREOUTBOXCONSUM1
docker compose --env-file /opt/tre/homolog/compose/n8n.env -f /opt/tre/homolog/compose/n8n.yml restart n8n-homolog
```

**CRM → outbox → n8n → PostgreSQL: funciona e é autônomo.** Fatos gerados pelo ORM →
`{"SENT": 8, "DEAD_LETTER": 0}` → 8 linhas `COMPLETED` na trilha; e uma mudança de etapa feita pelo
ORM depois disso chegou à trilha sozinha (`crm.lead STAGE_CHANGED COMPLETED`), entregue pelo cron.

**PostgreSQL → n8n → API controlada → CRM: recusado no portão, com motivo nomeado**
(`recusa_da_api:ambiente_nao_permitido`) e linha `REFUSED` na trilha. Não é defeito: a política do
módulo só permite `dev`, e `homologacao`/`producao` exigem **aprovação humana** por desenho. Detalhe
completo, parâmetros corretos e as armadilhas (id de credencial vem do contrato, `publish:workflow`,
vocabulário fechado de ambientes) em **`docs/runbooks/banco-de-vendas-homolog.md`**.

## 8. Pendências declaradas (não são surpresa)

- **UI não exposta publicamente.** A borda hoje serve só os nomes do Odoo (`dev.tre`, `homolog.tre`,
  `tre`). Publicar o n8n por hostname (com autenticação) é **card próprio** — não entrou aqui.
- **Escrita em Homolog depende de decisão do dono:** política que permita `homologacao` + aprovação
  válida. Sem isso, Homolog valida leitura e ingestão, mas não escrita.
- **Sem runner externo de tarefas** (o interno basta para os Code nodes do TRE).
