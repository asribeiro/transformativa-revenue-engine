# Runbook — Titan SMTP (outbound W6)

**Card:** TRE-W6-E01-T01 · **Componente:** `hermes/integracoes/titan/smtp_titan.py`
(versao `titan-smtp-v1`) · **Contrato:** `hermes/integracoes/titan/titan-smtp-v1.json` ·
**Documento:** `docs/integrations/titan-smtp-v1.md`

## 1. O que este componente faz — e o que nao faz

Faz: le a configuracao `TRE_TITAN_*`, valida (completude, matriz porta x TLS, guardas de ambiente),
conecta, mede EHLO/TLS/AUTH e entrega **uma** mensagem — com trilha e idempotencia.

Nao faz: compor o outreach (W6-E02), decidir ou conceder Human Approval (W6-E03), orquestrar o
workflow de envio (W6-E04) — quem chama o primitivo traz a aprovacao.

## 2. Configuracao (nomes; valores vivem no cofre, `docs/operations/gestao-de-secrets.md`)

| Variavel | Obrigatoria | Nota |
|---|---|---|
| `TRE_TITAN_SMTP_HOST` | sim | `smtp.titan.email` em homolog/prod; sink local em dev |
| `TRE_TITAN_SMTP_PORT` | sim | `465` (implicit TLS) ou `587` (STARTTLS); `25` e recusada |
| `TRE_TITAN_SMTP_SEGURANCA` | nao | `implicit_tls` \| `starttls` \| `nenhuma`; inferida da porta 465/587 |
| `TRE_TITAN_USER` | sim | caixa autenticada |
| `TRE_TITAN_PASSWORD` | sim | **segredo** — nunca em argumento, saida, relatorio ou trilha |
| `TRE_TITAN_FROM` | nao | assume `TRE_TITAN_USER` |
| `TRE_TITAN_TIMEOUT` | nao | padrao 15 s |
| `TRE_TITAN_CA` | nao | CA alternativa (sink de dev com certificado proprio) |
| `TRE_TITAN_DOMINIO_DEV` | nao | dominio de dev aceito em remetente/destino (padrao `dev.local`) |
| `TRE_TITAN_DESTINOS_PERMITIDOS` | nao | lista CSV exigida em homolog/prod |
| `TRE_TITAN_APROVACAO_HUMANA` | nao | identificador do registro de aprovacao (exigido em homolog) |

## 3. Uso

```bash
# 1) planejar (nunca conecta; declara faltantes e recusas)
python3 hermes/integracoes/titan/smtp_titan.py --planejar

# 2) conferir (valida completude + matriz + guardas; nunca conecta)
python3 hermes/integracoes/titan/smtp_titan.py --conferir --relatorio /tmp/smtp.json

# 3) provar contra o sink de dev (EHLO, TLS, AUTH, NOOP — sem enviar mensagem)
python3 hermes/integracoes/titan/smtp_titan.py --provar \
  --env-file deploy/environments/dev-smtp.env --relatorio /tmp/smtp-prova.json

# 4) enviar UMA mensagem em dev: dry-run primeiro, depois com --confirmo
python3 hermes/integracoes/titan/smtp_titan.py --enviar --para caixa@dev.local \
  --assunto "prova" --corpo "corpo" --chave-idempotencia "prova:1" --registro /tmp/trilha.jsonl
python3 hermes/integracoes/titan/smtp_titan.py --enviar --para caixa@dev.local \
  --assunto "prova" --corpo "corpo" --chave-idempotencia "prova:1" --confirmo \
  --registro /tmp/trilha.jsonl

# 5) desfazer a chave (dry-run ate --confirmo; auditoria preservada)
python3 hermes/integracoes/titan/smtp_titan.py --desfazer "prova:1" --registro /tmp/trilha.jsonl
```

`--env-file` carrega um arquivo de nomes NAO secretos (ex.: `deploy/environments/dev-smtp.env`); a
senha continua vindo do ambiente. Exit codes: `0` OK/DRY_RUN/replay · `1` falha de execucao ·
`2` uso · `3` recusa de guarda/configuracao · `4` recusa de producao · `5` senha vazada.

## 4. Como provar a configuracao em DESENVOLVIMENTO (sem credencial Titan)

O papel `dev-harness` nao tem credencial Titan (`hermes/policies/dev-harness.yaml`) e nao envia e-mail
em nome da Transformativa. A prova em dev usa o sink descartavel:

```bash
bash scripts/integracoes/teste_smtp_titan_aceite.sh            # baseline
bash scripts/integracoes/teste_smtp_titan_aceite.sh --prova-de-dente
python3 scripts/integracoes/verificar_smtp_titan.py           # suite offline
```

O aceite gera um certificado proprio (openssl), sobe o sink em `127.0.0.1` (TLS implicito),
prova/entrega e confere a captura; **nada sai para a internet** e o sink e derrubado no fim.

## 5. Provar contra o provedor (homolog) — decisao do dono

1. Credencial Titan real entregue ao papel **Sales AI** (nunca ao dev-harness) pelo cofre.
2. `TRE_TITAN_APROVACAO_HUMANA=<id do registro>` em `docs/operations/registro-de-aprovacoes.md`.
3. `TRE_TITAN_SMTP_HOST=smtp.titan.email`, `TRE_TITAN_SMTP_PORT=465`, `TRE_TITAN_SMTP_SEGURANCA=implicit_tls`.
4. `--provar` mede EHLO/TLS/AUTH/NOOP **sem enviar**: e a prova de configuracao contra o provedor.
5. Enviar de verdade so com destino em `TRE_TITAN_DESTINOS_PERMITIDOS` e `--confirmo`.

## 6. Diagnostico dos motivos de recusa

| Motivo | Significado | O que fazer |
|---|---|---|
| `CONFIG_INCOMPLETA` | variavel obrigatoria sem valor (os nomes sao listados) | completar no cofre |
| `CONFIG_INCOERENTE` | porta e seguranca declarada divergem | usar a matriz (465/implicit, 587/starttls) |
| `PORTA_NAO_AUTORIZADA` | porta 25 | usar 465 ou 587 |
| `PORTA_NAO_PREVISTA` | porta fora da matriz | idem |
| `SEGURANCA_AUSENTE` | porta de sink local sem seguranca explicita | declarar `TRE_TITAN_SMTP_SEGURANCA` |
| `TLS_OBRIGATORIO` | `nenhuma` fora do loopback | nunca texto claro contra provedor |
| `CA_AUSENTE` | `TRE_TITAN_CA` aponta para arquivo inexistente | corrigir o caminho |
| `HOST_NAO_E_DEV` | host real em `dev` | a prova contra o provedor e de homolog |
| `REMETENTE_NAO_DEV` | remetente fora do dominio de dev | usar `@dev.local` em dev |
| `DESTINO_NAO_PERMITIDO` | destino fora da politica do ambiente | incluir na lista (homolog) ou usar dominio de dev |
| `HOMOLOG_SEM_APROVACAO` | falta aprovacao registrada | registrar antes de falar com o provedor |
| `PRODUCAO_NAO_E_DESTE_CARD` | `--ambiente prod` | promocao e decisao humana registrada (ADR-005) |
| `AUTH_RECUSADO` / `FALHOU` | credencial/conexao | conferir credencial e rede (sem imprimir a senha) |
| `SENHA_VAZADA` | a senha apareceu no que seria gravado | **incidente**: o componente recusou gravar (exit 5); tratar como exposicao |

## 7. Rollback

Nao ha DDL, migration, tabela nem ato em producao: o rollback e `git revert` do commit do card. As
chaves ja registradas na trilha podem ser marcadas com `--desfazer <chave> --confirmo` (marca
`DESFEITO` e preserva a auditoria) — um e-mail ja entregue nao volta, e por isso o `--confirmo` e a
barreira antes de qualquer envio.
