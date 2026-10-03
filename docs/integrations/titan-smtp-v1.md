# Integração Titan — SMTP outbound (v1)

**Card:** TRE-W6-E01-T01 · **Onda:** W6 · **Baseline:** V1.1.0 · **Versão:** `titan-smtp-v1`
**Componente:** `hermes/integracoes/titan/smtp_titan.py`
**Contrato legível por máquina:** `hermes/integracoes/titan/titan-smtp-v1.json`
**Runbook:** `docs/runbooks/titan-smtp.md`

## 1. Papel no fluxo (doc 06 §9)

`n8n → SMTP Titan` (outbound). Este card entrega a **camada de configuração validada** e o
**primitivo de envio de uma mensagem**. O fluxo completo — gerar o texto (W6-E02), aprovar
(W6-E03), enviar com fila e retry (W6-E04) — é de outros cards; aqui existe a peça que todos eles
usam e que precisa ser confiável antes de qualquer mensagem sair.

## 2. Matriz porta × TLS (do provedor; o componente confere)

| Porta | Segurança | Decisão |
|---|---|---|
| 465 | `implicit_tls` | caminho principal |
| 587 | `starttls` | caminho alternativo |
| 25 | — | **RECUSA** `PORTA_NAO_AUTORIZADA` (o provedor não autoriza; aceitar seria configurar o caminho que não funciona) |
| outra | — | **RECUSA** `PORTA_NAO_PREVISTA` |

Divergência entre a porta e a segurança declarada → **RECUSA** `CONFIG_INCOERENTE`. O componente não
"conserta" a configuração: uma configuração que se corrige em silêncio é uma configuração que ninguém
sabe qual é.

**Exceção declarada:** em `dev`, host loopback (sink de teste) aceita porta fora da matriz **com a
segurança explícita** — porta de teste não carrega expectativa de provedor, e inferir TLS de porta de
teste seria inventar regra. A porta 25 continua recusada nesse caso.

## 3. Guardas de ambiente (ADR-005 — nada nasce em produção)

| Ambiente | Host | Remetente/destino | Aprovação | Resultado |
|---|---|---|---|---|
| `dev` | loopback obrigatório | domínio de dev obrigatório | — | prova contra **sink local**; host real → `HOST_NAO_E_DEV` |
| `homolog` | provedor permitido | lista explícita de destinos | `TRE_TITAN_APROVACAO_HUMANA` obrigatória | prova contra `smtp.titan.email` |
| `prod` | — | — | — | **RECUSA** exit 4 (`PRODUCAO_NAO_E_DESTE_CARD`) |

A guarda de `dev` não é zelo excessivo: **o papel `dev-harness` não tem credencial Titan**
(`hermes/policies/dev-harness.yaml` → `credenciais_proibidas: TRE_TITAN_*`) e **não pode enviar
e-mail em nome da Transformativa** (`nao_pode: enviar e-mail ou mensagem em nome da Transformativa`).
Sem a guarda, um `dev` mal configurado apontaria para o provedor com credencial de produção.

## 4. Segredo (doc `gestao-de-secrets.md` §1, §3, §6)

- senha **só** por `TRE_TITAN_PASSWORD` (ambiente/cofre); não existe opção de CLI para senha;
- todo relatório e toda trilha mostram `"senha": "<oculta>"`;
- a impressão digital da configuração (`identidade_config`) é `sha256` de
  `host|porta|seguranca|usuario|remetente|timeout|ca` — **sem a senha** (trocar a senha não muda a
  identidade, e a identidade não é derivada do segredo);
- checagem **fail-closed** no momento de gravar: se o valor da senha aparecer no que seria gravado, a
  gravação é recusada e o componente sai com **exit 5 (`SENHA_VAZADA`)** — falha alta em vez de log
  contaminado em silêncio.

## 5. Idempotência e trilha (doc 06 §7)

- `--chave-idempotencia` é obrigatória no envio (`exit 2` sem ela);
- chave já vista com `resultado=ENVIADO` na trilha → `JA_ENVIADO`, sem novo envio e **sem conexão**;
- a trilha é `--registro <arquivo.jsonl>` append-only, fora do banco: cada evento traz ambiente,
  identidade da configuração, resultado e, no envio, `Message-ID` e `recusados_por_servidor`;
- `--desfazer <chave>` é dry-run até `--confirmo`; marca `DESFEITO` e **preserva** o registro
  original. E-mail entregue não volta — o `--confirmo` antes do envio é a barreira real.

## 6. Verificação

| Instrumento | O que mede | Onde roda |
|---|---|---|
| `scripts/integracoes/verificar_smtp_titan.py` | completude, inferências, matriz, guardas, segredo, trilha — **sem abrir conexão** | offline |
| `scripts/integracoes/teste_smtp_titan_aceite.sh` | EHLO + TLS + AUTH + **entrega** contra sink descartável em 127.0.0.1, com `--prova-de-dente` | offline (loopback) |
| `--provar` em `homolog` | EHLO + TLS + AUTH + NOOP contra `smtp.titan.email`, sem enviar | provedor (aprovação do dono) |

## 7. Fora do card (declarado)

- compose do outreach (W6-E02) e Human Approval (W6-E03);
- workflow de envio com fila/retry (W6-E04) — aqui existe o primitivo de uma mensagem;
- ingestão de resposta por IMAP (W6-E05, card irmão TRE-W6-E01-T02);
- prova contra o provedor real: exige credencial + aprovação registrada (homolog), decisão do dono.

## 8. Lacunas (declaradas)

1. a trilha é arquivo JSONL, não tabela — quando o banco do TRE estiver de pé na VPS, W6-E04 decide se
   ela passa a viver em `sync_events` (não se inventa schema aqui);
2. a v1 não faz retry/backoff próprio: quem repete é o chamador, com a mesma chave de idempotência;
3. SPF/DKIM/DMARC do domínio não são verificados por este componente (configuração de DNS do domínio).
