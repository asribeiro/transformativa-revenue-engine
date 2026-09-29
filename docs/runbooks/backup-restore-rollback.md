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

## 8. Pendências declaradas (não disfarçadas)

- **Destino externo (Object Storage) não configurado**: hoje o backup é **só local**. Falta o
  Anderson inserir as chaves da Contabo no `/etc/tre/backup.env` (nunca pelo chat). Enquanto
  isso, `manifest.txt` registra `externo: pendente`.
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
