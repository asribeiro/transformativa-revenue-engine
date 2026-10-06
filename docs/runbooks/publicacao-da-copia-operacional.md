# Runbook — Publicação da cópia operacional (`/opt/tre/repo`)

**Card:** `t_091cfea9` (DEFEITO F3 do `TRE-W1-E06-T01`) · **Revisão 1.1 (enforcement):** `t_daca4bda`
· **Revisão 1.2 (staging único por publicação e falha que nomeia a fase):** `t_0f74266d`
**Status:** vigente desde 30/09/2026 · **Máquina:** VPS Contabo `vmi3619453` (`169.58.24.102`) · destino `tre-deploy:tre-deploy`
**Scripts:** `deploy/publicar.sh` (único caminho de escrita) · `deploy/watchdog-publicacao.sh` +
`deploy/systemd/tre-publicacao-watchdog.{service,timer}` (vigilância de 2 min na VPS) ·
`deploy/instalar-watchdog-publicacao.sh` (instalação).

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
| `--travar` / `--destravar` | arma/desarma a trava de imutabilidade (`chattr +i`) da cópia publicada |
| `--sem-trava` | publica **sem** armar a trava (`TRE_PUBLICAR_TRAVA=0`) — só para ensaio |
| `--permitir-arvore-suja` | publica mesmo com arquivo versionado modificado — o conteúdo continua vindo do git e o desvio fica registrado |
| `--exigir-modos` | transforma em falha (`exit 4`) o `ExecStart=` sem bit executável no commit |
| `--forcar-lock` | derruba lock obsoleto (> 30 min) de outra publicação |

Códigos de saída: `0` OK · `1` falha · `2` uso/precondição (árvore suja, commit inexistente) ·
`3` lock ocupado · `4` modos do `ExecStart` · `5` divergência na conferência · `6` transferência/post-check.

A última linha da saída é para automatizar:

```text
PUBLICACAO_OK commit=<sha> digest=<sha256> arquivos=<n> digest_antes=<sha256> trava=<armada|ausente>
PUBLICACAO_DIVERGENTE …      (exit 5)
PUBLICACAO_FALHOU …          (exit != 0, nada foi escrito no destino)
```

## 3. O que a publicação garante

1. **O conteúdo sai do git, não do disco:** `git archive <commit>` → staging → `rsync -a --delete` no
   destino. Arquivo modificado ou não rastreado no checkout não entra (por isso o `digest` é reprodutível).
2. **O modo é o modo do git.** O modo publicado vem de `git ls-tree` (`100755` → `755`, `100644` → `644`) e
   é aplicado **nos três pontos**: na árvore local (que gera o `digest`), no staging e no destino **depois**
   do `rsync`. Extrair com `tar` e confiar no modo do disco não serve: o modo do arquivo extraído leva a
   marca do `umask`/máscara de ACL de quem extrai (medido: `755` chegava `775` e `644` chegava `664`) e o
   `rsync -a` pula o arquivo que tem a mesma data e o mesmo tamanho **sem olhar o modo**. Antes de publicar,
   o script lê os `ExecStart=` dos units em `deploy/systemd/` e conta quantos alvos **não** estão `100755`
   no commit — `AVISO modo` em cada um, e o número vai para `.publicado` (`execstart_sem_bit`). Com
   `--exigir-modos` a publicação **para** em vez de deixar o systemd morrer com `203/EXEC`.
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
   divergencia_antes: 0
   concorrencia: (nenhuma)
   ```

   `.publicado` e `.publicado.manifest` são **metadados da publicação** (não existem no commit) e ficam
   de fora do espelho e do digest.
4. **Uma publicação por vez:** lock remoto `/opt/tre/.publicacao.lock` (diretório, obtido por `mkdir`
   atômico; lock mais velho que 30 min é considerado obsoleto e reportado). É o fim do "último manda".
5. **Aviso de concorrência:** se o `.publicado` anterior era de **outro card** com outro commit, a
   publicação avisa e grava `concorrencia:` no registro e no log.
6. **Log append-only:** `/opt/tre/.publicacoes.log` guarda `quando, commit, digest, arquivos, card,
   destino, digest_antes, commit_antes` de cada publicação. É o histórico que permite rollback do *código*.
   Desde a revisão 1.2 a publicação que **aborta** também deixa rastro ali
   (`PUBLICACAO_ABORTADA fase=<fase> motivo=<motivo> commit=… card=… destino=…`): a falha intermitente do
   `t_0f74266d` abortava antes de gravar e a auditoria do evento se perdia.
7. **Staging único por publicação (revisão 1.2).** A transferência monta o staging com `mktemp -d` no
   diretório **pai do destino** — um por destino e por execução — e o remove no fim, inclusive quando a
   publicação aborta. Antes era o caminho **fixo** `/opt/tre/.publicacao-staging`, compartilhado por toda
   publicação de todo card: duas publicações simultâneas se misturavam (o `find` de uma listava o que o
   `rm -rf`/`tar -x` da outra apagava, ~280 linhas de `sha256sum: … No such file or directory`), a falha
   era **intermitente** e a mensagem culpava "a cópia transferida" — o manifesto incompleto era o do
   staging. O mapa de modos deixou de ser o fixo `/opt/tre/.publicacao-modos` e virou irmão do staging.
   A linha `staging:  <alvo>:<dir> (unico desta publicacao)` da saída diz qual foi; ela **nunca** aparece
   depois do fim da publicação (o trap limpa).

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

## 5. Enforcement — a cópia publicada é imutável e vigiada

O caminho único **sozinho não bastou**. Em 30/09/2026 (recorrência do mesmo defeito, card `t_daca4bda`)
um card em execução ressincronizou `/opt/tre/repo` por `tar` ad-hoc, com mtime preservado, e a cópia
voltou para uma árvore **pré-correção**: o `.publicado` continuava dizendo o commit consertado, o
`tre-backup.service` imprimia `BACKUP_OK` cobrindo **zero** ambientes e ninguém rodava o `--conferir`
para ver. Caminho único que só *detecta* quando alguém lembra de rodar não é caminho único. Quatro peças
fecham isso:

1. **Trava de imutabilidade (`chattr +i`).** Toda publicação deixa a cópia **imutável**. Escrita ad-hoc
   (`tar -xz`, `sed -i`, `rsync`, `>>`, arquivo novo) passa a falhar com `Operation not permitted` em vez
   de sobrescrever em silêncio; para escapar é preciso `chattr -i` explícito — o erro deixa de ser
   silencioso. `deploy/publicar.sh` é o único que desarma, e só durante a troca (rearma antes de
   terminar); `--travar`/`--destravar` fazem isso à mão e `--sem-trava` publica sem armar (ensaio).
2. **Artefato do commit publicado.** A publicação grava o commit em `/opt/tre/.publicacao-artefato`
   (`commit.tar` com o modo do git, `modos.txt`, `manifesto`, `commit`, `digest`; `root:root` 700,
   **fora** da cópia). É o que permite conferir e restaurar **sem git** e sem o checkout de quem publica —
   e é uma referência independente: quem escreve na cópia teria de acertar dois lugares. Fail-closed: se o
   artefato gravado não conferir com o commit, a publicação **para** antes de trocar qualquer coisa.
3. **Watchdog de 2 minutos (na VPS).** `tre-publicacao-watchdog.timer` roda
   `/usr/local/lib/tre/watchdog-publicacao.sh --reparar`, instalado **fora** da cópia a partir dos bytes
   publicados (`deploy/instalar-watchdog-publicacao.sh` confere o sha256 dos dois lados) — o watchdog
   sobrevive justamente à cópia quebrada. Ele confere a cópia contra o manifesto do commit registrado,
   **atribui** a divergência (arquivo alterado / plantado / removido, com mtime e se é posterior à
   publicação), grava `/opt/tre/.publicacao-ALERTA` e `/opt/tre/.publicacao-divergencias.log`
   (append-only) e, no reparo, **restaura** a cópia do artefato e rearma a trava. Não interfere em
   publicação em curso: se `/opt/tre/.publicacao.lock` existe, ele só reporta `PUBLICACAO_EM_ANDAMENTO`.

4. **Guarda de produção na publicação.** O destino compartilhado é **produção** (é o alvo do
   `ExecStart=` dos timers). Publicar o **mesmo** commit já registrado (reparo/conferência) ou publicar
   em destino de ensaio passa direto; **substituir** o commit que está no ar exige declarar `--producao`
   (`TRE_PUBLICAR_PRODUCAO=1`), senão a publicação para com `PUBLICACAO_FALHOU` (exit 2) **antes de
   escrever qualquer coisa**, nomeando o commit que a produção executa hoje e o que se pretendia pôr.
   A declaração fica registrada em `.publicado` (`producao_declarado`). É o antídoto para "publiquei a
   minha branch ali só para testar" — a troca deixa de ser acidental.

5. **Não derrubar a própria porta de acesso (achado medido em 30/09).** `publicar.sh` faz ~25 chamadas
   remotas por publicação; com uma conexão TCP por chamada, a rodada de publicações de 22:2x–22:4xZ fez
   a VPS responder `Connection refused` na porta 22 **para o IP de origem inteiro** (todos os cards)
   por ~12 min, com o host de pé (ping 0% de perda) e **sem reboot** (`up 6:10`) — assinatura de
   penalidade por fonte (OpenSSH `PerSourcePenalties`) ou `fail2ban`, agravada pelas retentativas.
   Agora `R()` usa **ControlMaster** (`ControlPersist=30`): a publicação inteira gasta **uma** conexão.
   Regra de operação: se o SSH recusar, **não** insistir em laço — a penalidade se renova; espere expirar
   (medido: ~12 min) e confira `ping`/`uptime` antes de culpar o `sshd`.
6. **O lock não é derrubado por idade inventada.** O cálculo antigo fazia `AGORA - stat -c %Y` e, com o
   `stat` vazio, a idade virava ~56 anos: uma publicação **derrubava o lock vivo** de outra (medido pelo
   card `t_c7281fce`: `idade 1790808317s`). Agora a idade sai do mtime e, se ele não for legível, do
   `inicio` que o próprio lock grava; **sem idade confiável a publicação não derruba o lock** —
   `PUBLICACAO_FALHOU`, exit 3, nada escrito. `--forcar-lock` continua sendo o caminho explícito para
   quem tem certeza.
7. **O lock de OUTRA publicação não cega o watchdog.** O lock passou a registrar `destino=`. O watchdog
   só se cala quando o lock é de uma publicação **para o destino que ele vigia** (e não vencida); lock de
   publicação em destino isolado (que usa o lock padrão) não impede a conferência da produção — medido em
   30/09: com o lock padrão do card `t_0f74266d`/`tester` na mão, a cópia real ficou 2 ciclos divergente
   sem ninguém conferir. Lock sem `destino=` (publicação antiga) continua sendo motivo de silêncio
   (conservador: não conferir durante uma troca em curso é melhor que conferir no meio dela).
8. **Cópia certa mas SEM trava é rearmada no ciclo.** Se a cópia é o commit registrado e está sem
   `chattr +i` (publicação antiga sem `--travar`, ou alguém que destravou na mão), o ciclo `--reparar`
   rearma e reporta `trava=rearmada` — a janela em que o ad-hoc passa não sobrevive 2 minutos.

Saída do watchdog (uma linha, para automatizar):

```text
PUBLICACAO_OK commit=<sha> digest=<sha256> arquivos=<n> em=<quando>
PUBLICACAO_DIVERGENTE …            (exit 5 — a cópia não é o commit registrado)
PUBLICACAO_SEM_REGISTRO …          (exit 5 — sem .publicado/.publicado.manifest)
PUBLICACAO_EM_ANDAMENTO …          (publicação com o lock; não interfere)
PUBLICACAO_REPARO_OK … / PUBLICACAO_REPARO_FALHOU …   (exit 6)
PUBLICACAO_TRAVADA / PUBLICACAO_DESTRAVADA
```

Operação:

```bash
ssh root@169.58.24.102 'bash /usr/local/lib/tre/watchdog-publicacao.sh --estado'
ssh root@169.58.24.102 'cat /opt/tre/.publicacao-ALERTA; tail -20 /opt/tre/.publicacao-divergencias.log'
ssh root@169.58.24.102 'systemctl list-timers tre-publicacao-watchdog.timer; journalctl -u tre-publicacao-watchdog -n 20'
deploy/instalar-watchdog-publicacao.sh          # instala/atualiza a partir dos bytes publicados (--travar arma se estiver solta)
```

**Quando o alerta aparece:** a cópia foi escrita por fora do caminho único. Não force `chattr -i` na cópia
para "resolver" — o destino compartilhado é **produção**. O reparo do timer devolve a cópia ao commit
registrado e registra em `/opt/tre/.publicacoes.log` como `card=watchdog-reparo`. Quem precisava escrever
ali, use **destino isolado** (§9).

**Limites (não disfarçados):** (i) a trava é obstáculo contra o erro, não barreira contra `root` — quem
tem `root` pode `chattr -i` e escrever, mas deixa de ser silencioso, e o watchdog pega no ciclo seguinte;
(ii) o watchdog compara **conteúdo e modo** com o manifesto registrado, não assina nada: se o `.publicado`
**e** o artefato forem reescritos, não há como ver (por isso o artefato é `root:root` 700, fora da cópia);
(iii) o reparo usa o artefato da **última** publicação — sem ele o watchdog detecta e alerta, mas a
restauração volta a exigir `deploy/publicar.sh --commit <registrado>`.

### 5.1 Verificador com dente (prova que reprova)

`deploy/verificar-enforcement.sh` **não** confere se o enforcement existe no código: ele **tenta o
caminho ad-hoc e exige que ele falhe**, em destino isolado (nunca `/opt/tre/repo`). Ele mede 13 itens:
publicação pelo caminho único + trava armada; as quatro tentativas ad-hoc (`>>`, `sed -i`, arquivo novo,
`tar -xz` de árvore alheia) recusadas; conteúdo intacto depois delas; **sabotagem** (com `chattr -i`, como
o defeito real) que o detector **tem de reprovar** (exit 5, com atribuição do arquivo plantado); e o
reparo restaurando do artefato e rearmando a trava.

```bash
bash deploy/verificar-enforcement.sh                        # VERIFICADOR_ENFORCEMENT_OK itens=13
TRE_ENF_SEM_TRAVA=1 bash deploy/verificar-enforcement.sh    # auto-sabotagem: TEM de reprovar (exit 1)
```

A segunda linha é o que separa verificador de decoração: com o guard desligado (`--sem-trava`) ele
**reprova** (`VERIFICADOR_ENFORCEMENT_FALHOU itens=13 falhas=7` — trava ausente, as quatro escritas
aceitas, conteúdo mudou, conferência divergente). Verificador que passa por construção não vale
(defeito D04 do TRE-W0-E04-T01).

### 5.2 Falha que nomeia a FASE, manifesto incompleto e as guardas de caminho fixo (revisão 1.2)

Defeito `t_0f74266d` (recorrência medida no card `t_1b2ab418`, reproduzida pelo `tester` no `t_c9a44f85`):
instrumento que **falha fechado ainda pode mentir sobre por que falhou**. Duas correções de diagnóstico:

1. **Manifesto incompleto não é divergência.** O manifesto é montado com `find` + `stat`/`sha256sum`; se
   um arquivo listado desaparece ou fica ilegível nesse intervalo (escrita/limpeza concorrente), a comparação
   acusava divergência de **conteúdo** e a mensagem apontava o lado errado. Agora a linha vira
   `ILEGIVEL <caminho>` e o manifesto inteiro é reprovado com exit 7 — **contar linhas não bastava**: o
   defeito real mantinha a contagem e zerava o campo do hash. Quem chama decide mensagem e exit code, e
   nunca compara manifesto quebrado. Cada falha nomeia a fase (`ARVORE LOCAL`, `STAGING`, `COPIA
   OPERACIONAL ANTES DA TROCA`, `COPIA OPERACIONAL JA TROCADA`, `COPIA OPERACIONAL`), o arquivo e a causa,
   e diz se o destino foi tocado. `--conferir` ganhou `PUBLICACAO_INDETERMINADA` (exit 5) para o caso em
   que **não dá para afirmar** divergência.
2. **Diff sem truncar.** O diff do manifesto era cortado em `head -30`/`head -60` e o operador via um lado
   só. Agora o `diff -u` completo é gravado em `$TRE_PUBLICAR_DIFF_DIR/publicacao-diff-<fase>-<carimbo>.txt`
   (padrão `$TMPDIR`), o caminho aparece na tela e as primeiras 200 linhas vão para stderr.

Duas guardas novas, da mesma família ("artefato compartilhado em caminho fixo"), ambas **fail-closed**
antes de qualquer escrita:

- **Lock isolado exige destino isolado.** `TRE_PUBLICAR_LOCK` diferente do padrão com o destino
  compartilhado é **recusado** (exit 2): isolar só o lock tira a exclusão mútua sem tirar o alvo — foi
  exatamente assim que a colisão do `tester` aconteceu.
- **Destino isolado exige artefato isolado.** `$ARTEFATO` é a fonte de verdade do **watchdog da cópia
  compartilhada**: publicar em destino isolado deixando o artefato padrão faria o watchdog de
  `/opt/tre/repo` **reparar a produção para o commit do ensaio**. Recusado (exit 2) até você isolar
  (`TRE_PUBLICAR_ARTEFATO=<destino>-artefato`).

`PRODUCAO` passou a ser recalculada **depois** do parse dos argumentos: com `--destino` para um ensaio, o
cálculo antigo (feito antes do parse) fazia o destino isolado passar por produção e a publicação pedia
`--producao`.

Teste (local e sem tocar a VPS nem o destino compartilhado — o `ssh` é substituído por um shim que
executa o comando num sandbox, mapeando `/opt/tre` → sandbox):

```bash
bash deploy/teste-staging-unico.sh          # 39 verificações, 0 falhas
```

Ele roda o `publicar.sh` **real** e prova os dois lados: reproduz o defeito na versão de `3bf5e07`
(exit 6 culpando "a cópia transferida"; duas publicações simultâneas falhando no staging fixo) e prova o
conserto (as mesmas duas passam, com stagings **diferentes**), além das guardas (E/G), do `--destino` por
CLI (H) e do caminho bom com `--conferir` (F).

## 6. Rollback do código publicado

A cópia é código, não dado — restaurar dado é o runbook `backup-restore-rollback.md`. Para voltar a cópia
ao commit anterior:

```bash
ssh root@169.58.24.102 'tail -5 /opt/tre/.publicacoes.log'   # qual era o commit_antes
deploy/publicar.sh --commit <commit_anterior>                # republica aquele commit (fica registrado)
```

Publicar um commit **é** um rollback determinístico: o `digest` do commit é o mesmo de antes, porque o
conteúdo e o modo vêm do git. Não existe "arquivo solto" a limpar: o `rsync --delete` espelha o commit.

## 7. O que este caminho **não** faz

- **não empurra para o git** — publicar ≠ integrar. O `push` para `develop`/`feature` é do fluxo de
  branches (`BRANCHING.md`); publicar serve para o que roda **na VPS agora** (timers, scripts de operação).
- **não reinicia serviço nem recarrega unit** — quem publica não mexe em `systemd`.
- **não toca banco, backup nem `/opt/tre/backup`** — o único diretório escrito é o destino da cópia.
- **não leva segredo**: `.env`/`.env.*` são ignorados pelo git e por isso não têm como entrar no
  `git archive`; a cópia publicada é o commit, e o commit não tem segredo.
- **não decide qual commit é o certo** — publica exatamente o que foi pedido e registra.
- **só arquivo comum:** o commit não pode ter symlink/submódulo nem caminho com espaço/tab — o mapa de
  modos e o manifesto são texto separado por espaço. Se tiver, a publicação **recusa** (exit 2) em vez de
  publicar um digest que não representa a árvore.

## 8. Evidência medida (30/09/2026)

Duas publicações seguidas do **mesmo commit** terminando no **mesmo digest**, na cópia de teste e na
cópia operacional real; a sobrescrita ad-hoc detectada por `--conferir` (exit 5) e desfeita por uma nova
publicação; `tre-backup.service` executando sem `203/EXEC` depois da publicação. Números, comandos e
saídas em `docs/runbooks/backup-restore-rollback.md` §7d e
`docs/operations/registro-de-execucoes.md` (entrada de 30/09/2026 do card `t_091cfea9`).

## 9. Regra para os próximos cards

- **Nada publica por `tar`/`scp`/`rsync` direto em `/opt/tre/repo`.** Um caminho só: `deploy/publicar.sh`.
  Desde a revisão 1.1 isso não depende mais de disciplina: a cópia publicada está **imutável** e escrita
  ad-hoc falha com `Operation not permitted`; se ainda assim algo escapar (um `chattr -i` na mão), o
  watchdog de 2 minutos detecta, alerta e restaura. E **substituir** o commit que a produção executa
  exige `--producao` declarado (senão a publicação para, exit 2, sem escrever nada).
- **O destino compartilhado é PRODUÇÃO — bancada de teste é destino isolado.** Se você precisa de uma
  cópia com a sua árvore (para testar, medir, ensaiar), publique num destino seu e não toque no
  compartilhado:

  ```bash
  TRE_PUBLICAR_DESTINO=/opt/tre/.teste-<seu-card> \
  TRE_PUBLICAR_ARTEFATO=/opt/tre/.teste-<seu-card>-artefato \
  TRE_PUBLICAR_LOCK=/opt/tre/.teste-<seu-card>.lock \
  TRE_PUBLICAR_LOG=/opt/tre/.teste-<seu-card>.log \
  deploy/publicar.sh --commit <sha>              # e --conferir com as MESMAS variáveis
  ```

  Foi usar o destino compartilhado como bancada que produziu o `t_091cfea9`, o `203/EXEC` e a recorrência
  do `t_daca4bda`. Se o seu comando em `/opt/tre/repo` falhar com `Operation not permitted`, **não force
  `chattr -i`**: mudou de lugar o seu ensaio, não a trava.
  Desde a revisão 1.2 as três variáveis andam **juntas**: isolar só o lock (destino compartilhado) é
  **recusado** (exit 2), e destino isolado com o artefato padrão também — este segundo caso faria o
  watchdog da cópia compartilhada **reparar a produção** para o commit do ensaio.
- Para a cópia refletir trabalho ainda não integrado, publique o **commit da sua branch** (fica
  registrado com o seu card em `.publicado`) e **republicie o `develop`** depois do merge — assim o
  registro nunca mente sobre o que está no ar.
- Se a publicação reclamar de `ExecStart` sem bit executável, o conserto é **no git**
  (`chmod +x` + commit), nunca um `chmod` na mão na cópia: na próxima publicação ele se perde.

## 10. Evidência medida da revisão 1.2 (card `t_0f74266d`, 30/09/2026)

Commit `44e0d13` (`fix/t_0f74266d-staging`), medido na VPS `vmi3619453` **sempre em destino isolado**
(`/opt/tre/.teste-t_0f74266d`), lock padrão e artefato isolado (`…-artefato`), `--sem-trava`:

- `PUBLICACAO_OK commit=44e0d13… digest=5d61ef32… arquivos=316` (exit 0), com a linha
  `staging:  root@169.58.24.102:/opt/tre/.publicacao-staging.iYZ1Zy (unico desta publicacao)`;
- `--conferir` no mesmo destino: `PUBLICACAO_OK … digest=5d61ef32… arquivos=316` (exit 0);
- negativos medidos (nada escrito em nenhum dos dois): destino isolado com o artefato **padrão** →
  `PUBLICACAO_FALHOU destino isolado … com o artefato PADRAO do watchdog` (exit 2); lock **isolado** com o
  destino **compartilhado** → `PUBLICACAO_FALHOU lock isolado …` (exit 2);
- **a produção não mudou:** antes e depois `/opt/tre/repo/.publicado` no commit `373ff42f` (digest
  `1421673b`), `sha256 scripts/verificar_estrutura.sh` = `7ecaade3…` e o digest do artefato padrão do
  watchdog = `1421673b…` (o mesmo); nenhum `/opt/tre/.publicacao-staging*` ou `.publicacao-modos*` sobrou;
- local, sem VPS: `deploy/teste-staging-unico.sh` → `PASS=39 FALHAS=0` em duas execuções (reproduz o
  defeito na versão de `3bf5e07` e prova o conserto). O digest que o código novo calcula para o commit
  `719a628` (`d2215645…`, 315 arquivos) é **idêntico** ao que a versão antiga publicou — a semântica do
  manifesto não mudou com o conserto.
