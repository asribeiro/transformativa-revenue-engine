# Runbook — Publicação da cópia operacional (`/opt/tre/repo`)

**Card:** `t_091cfea9` (DEFEITO F3 do `TRE-W1-E06-T01`) · **Status:** vigente desde 30/09/2026
**Máquina:** VPS Contabo `vmi3619453` (`169.58.24.102`) · destino `tre-deploy:tre-deploy`
**Script:** `deploy/publicar.sh` — **único** caminho de publicação da cópia operacional.

---

## 1. Por que existe (o defeito medido)

Até 30/09/2026 cada card instalava o seu pedaço da cópia operacional com o próprio

```bash
tar -cz scripts docs | ssh root@… 'tar -xz -C /opt/tre/repo && chown -R tre-deploy:tre-deploy …'
```

Três consequências, todas medidas na VPS:

1. **quem sincroniza por último manda** — o driver de teste recém-instalado no card `TRE-W1-E06-T01`
   voltou de `sha256 d29c9c97…` (14.986 B) para `9f24572a…` (5.822 B, mtime de 29/09) no meio da rodada,
   porque outro card estava publicando em paralelo;
2. **o modo não vinha do git** — o `tar` da árvore de trabalho devolvia `644` para `scripts/backup/*.sh`
   e o `ExecStart=` do `tre-backup.service` morria com `203/EXEC` (o defeito irmão, `t_22c27625`);
3. **não havia registro nem dono** — `/opt/tre/repo` não tem `.git` nem `.publicado`, então ninguém
   conseguia dizer qual commit estava no ar; na medição de 30/09 a cópia tinha **122 arquivos** de um
   repositório com **300** versionados (o resto simplesmente não estava lá).

A cópia operacional não é dado — é **código publicado**. Código publicado tem de ter um caminho único,
um commit identificável e um registro. É isso que este runbook entrega.

## 2. O caminho único

```bash
cd /caminho/do/checkout            # um checkout LIMPO do repositório
deploy/publicar.sh --commit 6282077              # publica um commit (nunca a árvore de trabalho)
deploy/publicar.sh --conferir                    # confere a cópia contra o commit registrado
```

| Opção | Para que serve |
|---|---|
| `--commit <sha\|ref>` | commit a publicar (padrão: `HEAD`, e aí a árvore de trabalho tem de estar limpa) |
| `--alvo <user@host>` | destino ssh (padrão `root@169.58.24.102`; env `TRE_PUBLICAR_ALVO`) |
| `--destino <dir>` | diretório da cópia (padrão `/opt/tre/repo`; env `TRE_PUBLICAR_DESTINO`) |
| `--dono <user:group>` | dono final da cópia (padrão `tre-deploy:tre-deploy`) |
| `--chave <arquivo>` | chave ssh (padrão `$HOME/.ssh/id_ed25519_ops`, depois `/opt/data/home/.ssh/id_ed25519_ops`) |
| `--card <id>` | card que publica (padrão `$HERMES_KANBAN_TASK`) — vai para `.publicado` e para o log |
| `--ensaio` | mostra o que faria (inclusive o digest e a concorrência) sem escrever nada |
| `--permitir-arvore-suja` | publica mesmo com arquivo versionado modificado — o conteúdo continua vindo do git e o desvio fica registrado |
| `--exigir-modos` | transforma em falha (`exit 4`) o `ExecStart=` sem bit executável no commit |
| `--forcar-lock` | derruba lock obsoleto (> 30 min) de outra publicação |

Códigos de saída: `0` OK · `1` falha · `2` uso/precondição (árvore suja, commit inexistente) ·
`3` lock ocupado · `4` modos do `ExecStart` · `5` divergência na conferência · `6` transferência/post-check.

A última linha da saída é para automatizar:

```text
PUBLICACAO_OK commit=<sha> digest=<sha256> arquivos=<n> digest_antes=<sha256>
PUBLICACAO_DIVERGENTE …      (exit 5)
PUBLICACAO_FALHOU …          (exit != 0, nada foi escrito no destino)
```

## 3. O que a publicação garante

1. **O conteúdo sai do git, não do disco:** `git archive <commit>` → staging → `rsync -a --delete` no
   destino. Arquivo modificado ou não rastreado no checkout não entra (por isso o `digest` é reprodutível).
2. **O modo sobrevive:** o bit executável vem do índice do git. Antes de publicar, o script lê os
   `ExecStart=` dos units em `deploy/systemd/` e conta quantos alvos **não** estão `100755` no commit —
   `AVISO modo` em cada um, e o número vai para `.publicado` (`execstart_sem_bit`). Com `--exigir-modos`
   a publicação **para** em vez de deixar o systemd morrer com `203/EXEC`.
3. **Registro no destino** — `/opt/tre/repo/.publicado` (e `.publicado.manifest`, com modo + sha256 de
   cada arquivo):

   ```text
   commit: 6282077…
   arvore: 9c1e2f…
   digest: <sha256 do manifesto da árvore>
   arquivos: 301
   origem: https://github.com/asribeiro/transformativa-revenue-engine.git
   destino: /opt/tre/repo
   publicado_em: 2026-09-30T20:41:07Z
   publicado_por: t_091cfea9
   publicado_de: <host do agente>
   arvore_suja: 0
   execstart_sem_bit: 0
   concorrencia: (nenhuma)
   ```

   `.publicado` e `.publicado.manifest` são **metadados da publicação** (não existem no commit) e ficam
   de fora do espelho e do digest.
4. **Uma publicação por vez:** lock remoto `/opt/tre/.publicacao.lock` (diretório, obtido por `mkdir`
   atômico; lock mais velho que 30 min é considerado obsoleto e reportado). É o fim do "último manda".
5. **Aviso de concorrência:** se o `.publicado` anterior era de **outro card** com outro commit, a
   publicação avisa e grava `concorrencia:` no registro e no log.
6. **Log append-only:** `/opt/tre/.publicacoes.log` guarda `quando, commit, digest, arquivos, card,
   digest_antes, commit_antes` de cada publicação. É o histórico que permite rollback do *código*.

## 4. Conferir (é isto que vale como prova pós-deploy)

```bash
deploy/publicar.sh --conferir
```

Lê `.publicado`, pega o commit registrado, extrai a árvore do git **desse commit** e compara com a cópia
do destino **arquivo a arquivo e modo a modo**:

- igual ⇒ `PUBLICACAO_OK commit=… digest=… arquivos=… conferido_em=…` (exit 0);
- diferente ⇒ `PUBLICACAO_DIVERGENTE` (exit 5) com o `diff` do manifesto (arquivo alterado, modo alterado,
  arquivo a mais, arquivo faltando);
- sem `.publicado` ⇒ `PUBLICACAO_DIVERGENTE … não há registro de qual commit está publicado` (exit 5).

Rode `--conferir` **depois de qualquer operação na VPS** e antes de culpar o código por um defeito de
ambiente: "o container subiu" não é prova de que a cópia é o commit que você acha que é.

## 5. Rollback do código publicado

A cópia é código, não dado — restaurar dado é o runbook `backup-restore-rollback.md`. Para voltar a cópia
ao commit anterior:

```bash
ssh root@169.58.24.102 'tail -5 /opt/tre/.publicacoes.log'   # qual era o commit_antes
deploy/publicar.sh --commit <commit_anterior>                # republica aquele commit (fica registrado)
```

Publicar um commit **é** um rollback determinístico: o `digest` do commit é o mesmo de antes, porque o
conteúdo e o modo vêm do git. Não existe "arquivo solto" a limpar: o `rsync --delete` espelha o commit.

## 6. O que este caminho **não** faz

- **não empurra para o git** — publicar ≠ integrar. O `push` para `develop`/`feature` é do fluxo de
  branches (`BRANCHING.md`); publicar serve para o que roda **na VPS agora** (timers, scripts de operação).
- **não reinicia serviço nem recarrega unit** — quem publica não mexe em `systemd`.
- **não toca banco, backup nem `/opt/tre/backup`** — o único diretório escrito é o destino da cópia.
- **não leva segredo**: `.env`/`.env.*` são ignorados pelo git e por isso não têm como entrar no
  `git archive`; a cópia publicada é o commit, e o commit não tem segredo.
- **não decide qual commit é o certo** — publica exatamente o que foi pedido e registra.

## 7. Evidência medida (30/09/2026)

Duas publicações seguidas do **mesmo commit** terminando no **mesmo digest**, na cópia de teste e na
cópia operacional real; a sobrescrita ad-hoc detectada por `--conferir` (exit 5) e desfeita por uma nova
publicação; `tre-backup.service` executando sem `203/EXEC` depois da publicação. Números, comandos e
saídas em `docs/runbooks/backup-restore-rollback.md` §7d e
`docs/operations/registro-de-execucoes.md` (entrada de 30/09/2026 do card `t_091cfea9`).

## 8. Regra para os próximos cards

- **Nada publica por `tar`/`scp`/`rsync` direto em `/opt/tre/repo`.** Um caminho só: `deploy/publicar.sh`.
- Para a cópia refletir trabalho ainda não integrado, publique o **commit da sua branch** (fica
  registrado com o seu card em `.publicado`) e **republicie o `develop`** depois do merge — assim o
  registro nunca mente sobre o que está no ar.
- Se a publicação reclamar de `ExecStart` sem bit executável, o conserto é **no git**
  (`chmod +x` + commit), nunca um `chmod` na mão na cópia: na próxima publicação ele se perde.
