# Runbook — Titan IMAP (inbound W6)

**Card:** TRE-W6-E01-T02 · **Componente:** `hermes/integracoes/titan/imap_titan.py`
(versao `titan-imap-v1`) · **Contrato:** `hermes/integracoes/titan/titan-imap-v1.json` ·
**Documento:** `docs/integrations/titan-imap-v1.md`

## 1. O que este componente faz — e o que nao faz

Faz: le a configuracao `TRE_TITAN_*`, valida (completude, matriz porta x TLS, guardas de ambiente,
invariante de leitura), conecta, mede saudacao/TLS/LOGIN/CAPACIDADE, abre a caixa com **EXAMINE**,
lista envelopes e **ingere** as mensagens novas uma unica vez cada — com trilha append-only.

Nao faz: classificar a mensagem (W6-E05), compor/enviar outreach (W6-E02/E04), conceder Human Approval
(W6-E03), **escrever na caixa** (nunca: nenhuma flag, nenhuma remocao, nenhuma pasta criada).

## 2. Configuracao (nomes; valores vivem no cofre, `docs/operations/gestao-de-secrets.md`)

| Variavel | Obrigatoria | Nota |
|---|---|---|
| `TRE_TITAN_IMAP_HOST` | sim | `imap.titan.email` em homolog/prod; sink local em dev |
| `TRE_TITAN_IMAP_PORT` | sim | `993` (implicit TLS) ou `143` (STARTTLS); portas de outro protocolo recusadas |
| `TRE_TITAN_IMAP_SEGURANCA` | nao | `implicit_tls` \| `starttls` \| `nenhuma`; inferida da porta 993/143 |
| `TRE_TITAN_IMAP_CAIXA` | nao | caixa a ler; padrao `INBOX` |
| `TRE_TITAN_IMAP_LIMITE` | nao | maximo de mensagens por rodada; padrao 50, teto 500 |
| `TRE_TITAN_USER` | sim | caixa autenticada (a mesma do SMTP) |
| `TRE_TITAN_PASSWORD` | sim | **segredo** — nunca em argumento, saida, relatorio, trilha ou captura |
| `TRE_TITAN_TIMEOUT` | nao | padrao 15 s |
| `TRE_TITAN_CA` | nao | CA alternativa (sink de dev com certificado proprio) |
| `TRE_TITAN_DOMINIO_DEV` | nao | dominio de dev aceito no login (padrao `dev.local`) |
| `TRE_TITAN_CAIXAS_PERMITIDAS` | nao | lista CSV exigida em homolog/prod (sem ela, nao le caixa nenhuma) |
| `TRE_TITAN_APROVACAO_HUMANA` | nao | identificador do registro de aprovacao (exigido em homolog) |

## 3. Uso

```bash
# 1) planejar (nunca conecta; declara faltantes, recusas e o resultado da auditoria de leitura)
python3 hermes/integracoes/titan/imap_titan.py --planejar

# 2) conferir (valida completude + matriz + guardas + invariante; nunca conecta)
python3 hermes/integracoes/titan/imap_titan.py --conferir --relatorio /tmp/imap.json

# 3) provar contra o sink de dev (saudacao, TLS, LOGIN, CAPACIDADE, EXAMINE, NOOP — sem trazer corpo)
python3 hermes/integracoes/titan/imap_titan.py --provar \
  --env-file deploy/environments/dev-imap.env --relatorio /tmp/imap-prova.json

# 4) listar envelopes (cabecalhos; nunca traz corpo e nunca marca lido)
python3 hermes/integracoes/titan/imap_titan.py --listar --relatorio /tmp/imap-ler.json

# 5) ingerir: dry-run primeiro, depois com --confirmo (grava uma copia por mensagem)
python3 hermes/integracoes/titan/imap_titan.py --ingerir --saida /tmp/mensagens \
  --chave-idempotencia "w6-e05:rodada-1" --registro /tmp/trilha.jsonl
python3 hermes/integracoes/titan/imap_titan.py --ingerir --saida /tmp/mensagens \
  --chave-idempotencia "w6-e05:rodada-1" --confirmo --registro /tmp/trilha.jsonl

# 6) desfazer a identidade (dry-run ate --confirmo; auditoria preservada; nada e apagado do servidor)
python3 hermes/integracoes/titan/imap_titan.py --desfazer "999:1" --registro /tmp/trilha.jsonl
python3 hermes/integracoes/titan/imap_titan.py --desfazer "999:1" --confirmo --registro /tmp/trilha.jsonl
```

`--env-file` carrega um arquivo de nomes NAO secretos (ex.: `deploy/environments/dev-imap.env`); a
senha continua vindo do ambiente. Exit codes: `0` OK/DRY_RUN/replay · `1` falha de execucao ·
`2` uso · `3` recusa de guarda/configuracao (inclui `ESCRITA_NO_CODIGO`) · `4` recusa de producao ·
`5` senha vazada.

## 4. Como provar a configuracao em DESENVOLVIMENTO (sem credencial Titan)

O papel `dev-harness` nao tem credencial Titan (`hermes/policies/dev-harness.yaml`) e nao contata
lead/cliente. A prova em dev usa o sink descartavel:

```bash
bash scripts/integracoes/teste_imap_titan_aceite.sh            # baseline
bash scripts/integracoes/teste_imap_titan_aceite.sh --prova-de-dente
python3 scripts/integracoes/verificar_imap_titan.py           # suite offline
```

O aceite gera um certificado proprio (openssl), sobe o sink em `127.0.0.1` nos dois modos de TLS
(`implicit_tls` e `starttls`), prova/lista/ingere e confere a captura; **nada sai para a internet** e o
sink e derrubado no fim. O sink e **estrito**: registra selecao, buscas sem `PEEK` e comandos de
escrita, e aplica `\Seen` na busca sem `PEEK` para expor a intencao do cliente.

## 5. Provar contra o provedor (homolog) — decisao do dono

1. Credencial Titan real entregue ao papel **Sales AI** (nunca ao dev-harness) pelo cofre.
2. `TRE_TITAN_APROVACAO_HUMANA=<id do registro>` em `docs/operations/registro-de-aprovacoes.md`.
3. `TRE_TITAN_IMAP_HOST=imap.titan.email`, `TRE_TITAN_IMAP_PORT=993`,
   `TRE_TITAN_IMAP_SEGURANCA=implicit_tls`, `TRE_TITAN_CAIXAS_PERMITIDAS=INBOX`.
4. `--provar` mede saudacao/TLS/LOGIN/CAPACIDADE/EXAMINE/NOOP **sem trazer corpo**.
5. `--listar` e `--ingerir` so depois, com a caixa na lista permitida e `--confirmo`.

## 6. Diagnostico dos motivos de recusa

| Motivo | Significado | O que fazer |
|---|---|---|
| `CONFIG_INCOMPLETA` | variavel obrigatoria sem valor (os nomes sao listados) | completar no cofre |
| `CONFIG_INCOERENTE` | porta e seguranca declarada divergem | usar a matriz (993/implicit, 143/starttls) |
| `PORTA_DE_OUTRO_PROTOCOLO` | porta de SMTP (25/465/587) ou POP3 (110/995) | usar 993 ou 143 |
| `PORTA_NAO_PREVISTA` | porta fora da matriz | idem |
| `SEGURANCA_AUSENTE` | porta de sink local sem seguranca explicita | declarar `TRE_TITAN_IMAP_SEGURANCA` |
| `TLS_OBRIGATORIO` | `nenhuma` fora do loopback | nunca texto claro contra provedor |
| `CA_AUSENTE` | `TRE_TITAN_CA` aponta para arquivo inexistente | corrigir o caminho |
| `LIMITE_INVALIDO` | `TRE_TITAN_IMAP_LIMITE` fora de 1..500 | corrigir o valor |
| `CAIXA_INVALIDA` | nome de caixa com caractere nao aceito | usar letras, numeros, `. _ - / [ ]` |
| `HOST_NAO_E_DEV` | host real em `dev` | a prova contra o provedor e de homolog |
| `USUARIO_NAO_DEV` | login fora do dominio de dev | usar `@dev.local` em dev |
| `CAIXA_NAO_PERMITIDA` | caixa fora da politica (ou lista ausente) em homolog | incluir em `TRE_TITAN_CAIXAS_PERMITIDAS` |
| `HOMOLOG_SEM_APROVACAO` | falta aprovacao registrada | registrar antes de falar com o provedor |
| `ESCRITA_NO_CODIGO` | a auditoria achou comando de escrita na fonte | **nao conectar**: corrigir o codigo; o invariante de leitura esta violado |
| `PRODUCAO_NAO_E_DESTE_CARD` | `--ambiente prod` | promocao e decisao humana registrada (ADR-005) |
| `LOGIN_RECUSADO` / `FALHOU` | credencial/conexao | conferir credencial e rede (sem imprimir a senha) |
| `SENHA_VAZADA` | a senha apareceu no que seria gravado | **incidente**: o componente recusou gravar (exit 5); tratar como exposicao |

## 7. O invariante de leitura em operacao

Antes de qualquer conexao o componente audita a propria fonte. Se o `ESCRITA_NO_CODIGO` aparecer depois
de uma alteracao no arquivo, **nao** se contorna a guarda: a saida e corrigir o codigo (ou reverter o
commit). O aceite tem um dente que injeta uma violacao numa copia do modulo e exige a recusa — a guarda
tem prova de que funciona, nao e decoracao.

## 8. Rollback

Nao ha DDL, migration, tabela nem ato em producao: o rollback e `git revert` do commit do card. A
ingesta grava apenas arquivos locais (`--saida`) e a trilha JSONL; `--desfazer <identidade> --confirmo`
marca `DESFEITO` e preserva a auditoria. Nenhum caso apaga mensagem do servidor — se a leitura, por
defeito, tiver marcado algo como lido no provedor, o conserto e do lado do provedor (marcar como nao
lida na interface) e o incidente entra em `docs/operations/registro-de-execucoes.md`.
