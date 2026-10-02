# Integração Titan — IMAP inbound (v1)

**Card:** TRE-W6-E01-T02 · **Onda:** W6 · **Baseline:** V1.1.0 · **Versão:** `titan-imap-v1`
**Componente:** `hermes/integracoes/titan/imap_titan.py`
**Contrato legível por máquina:** `hermes/integracoes/titan/titan-imap-v1.json`
**Runbook:** `docs/runbooks/titan-imap.md` · **Irmão:** `titan-smtp-v1` (TRE-W6-E01-T01)

## 1. Papel no fluxo (doc 06 §9)

`IMAP Titan → n8n → classificação` (inbound). Este card entrega a **camada de configuração validada** e
o **primitivo de leitura** da caixa: listar envelopes e ingerir as mensagens novas **uma única vez
cada**, com trilha append-only. Decidir se a mensagem é resposta, bounce, opt-out ou ruído é do
**W6-E05**; compor e enviar outreach é do W6-E02/E04. Aqui existe a peça que todos eles usam e que
precisa ser confiável **antes** de qualquer leitura em produção.

## 2. Invariante de leitura (o que distingue este card do irmão SMTP)

IMAP é o protocolo em que **ler costuma escrever**: buscar o corpo sem `PEEK` marca a mensagem como
lida, e um cliente desavisado apaga, move e expurga. Este componente não faz nada disso, e a regra é
verificável em **três camadas**:

1. a caixa é aberta com **`EXAMINE`** (`select(..., readonly=True)`); `SELECT` de escrita nunca é usado;
2. todo conteúdo vem de **`BODY.PEEK`** — corpo e cabeçalhos;
3. o componente **audita a própria fonte** antes de qualquer conexão: se encontrar no próprio código um
   comando de escrita (STORE, EXPUNGE, DELETE, COPY, MOVE, APPEND) ou uma busca sem `PEEK`, **RECUSA**
   com `ESCRITA_NO_CODIGO` (exit 3) e não abre conexão.

A auditoria monta os padrões em tempo de execução (por concatenação) para não se encontrar a si mesma, e
mira o **recebedor da sessão** (`sessao.<método>`) e o comando enviado por `uid(...)` — `lista.append()`
de Python não é escrita de IMAP, e uma auditoria que confundisse os dois seria ruído, não guarda. O
aceite tem um dente que injeta uma violação numa cópia do módulo e exige a recusa (item 5.2 da suíte).

**Medição de ponta:** o sink de dev é **estrito** — registra seleção, buscas sem `PEEK` e comandos de
escrita, e aplica `\Seen` quando a busca não tem `PEEK` (mesmo em seleção read-only) para expor a
**intenção** do cliente. O RFC 3501 descarta mudança de flag em `EXAMINE`; um sink fiel a isso mascararia
um cliente que usa `BODY[]` em vez de `BODY.PEEK[]`. O aceite exige, no registro acumulado do sink:
`total_buscas_sem_peek = 0`, `total_comandos_de_escrita = []`, `mensagens_marcadas_lidas = []` e todas as
seleções em `EXAMINE`.

## 3. Matriz porta × TLS (do provedor; o componente confere)

| Porta | Segurança | Decisão |
|---|---|---|
| 993 | `implicit_tls` | caminho principal (TLS na conexão) |
| 143 | `starttls` | alternativa, com STARTTLS negociado |
| 110 / 995 | — | **RECUSA** `PORTA_DE_OUTRO_PROTOCOLO` (POP3) |
| 25 / 465 / 587 | — | **RECUSA** `PORTA_DE_OUTRO_PROTOCOLO` (SMTP) |
| outra | — | **RECUSA** `PORTA_NAO_PREVISTA` |

Divergência entre a porta e a segurança declarada → **RECUSA** `CONFIG_INCOERENTE`. O componente não
"conserta" a configuração: uma configuração que se corrige em silêncio é uma configuração que ninguém
sabe qual é.

**Exceção declarada:** em `dev`, host loopback (sink de teste) aceita porta fora da matriz **com a
segurança explícita** — porta de teste não carrega expectativa de provedor, e inferir TLS de porta de
teste seria inventar regra. As portas de outro protocolo continuam recusadas nesse caso.

## 4. Guardas de ambiente (ADR-005 — nada nasce em produção)

| Ambiente | Host | Login | Caixa | Aprovação | Resultado |
|---|---|---|---|---|---|
| `dev` | loopback obrigatório | domínio de dev obrigatório | livre (padrão `INBOX`) | — | prova contra **sink local**; host real → `HOST_NAO_E_DEV`, login corporativo → `USUARIO_NAO_DEV` |
| `homolog` | provedor permitido | caixa real | lista explícita | `TRE_TITAN_APROVACAO_HUMANA` obrigatória | prova contra `imap.titan.email` |
| `prod` | — | — | — | — | **RECUSA** exit 4 (`PRODUCAO_NAO_E_DESTE_CARD`) |

A guarda de `dev` não é zelo excessivo: **o papel `dev-harness` não tem credencial Titan**
(`hermes/policies/dev-harness.yaml` → `credenciais_proibidas: TRE_TITAN_*`) e **não pode contatar lead
ou cliente**. Sem a guarda, um `dev` mal configurado leria a caixa real de produção.

## 5. Segredo (doc `gestao-de-secrets.md` §1, §3, §6)

- senha **só** por `TRE_TITAN_PASSWORD` (ambiente/cofre); não existe opção de CLI para senha;
- todo relatório e toda trilha mostram `"senha": "<oculta>"`;
- a impressão digital da configuração (`identidade_config`) é `sha256` de
  `host|porta|seguranca|caixa|usuario|timeout|ca` — **sem a senha**;
- o **sink** compara a senha e a descarta; a captura guarda `LOGIN <usuario> <senha-oculta>` — o defeito
  de registrar o comando cru foi medido na rodada 1 e corrigido;
- checagem **fail-closed** no momento de gravar (trilha, relatório e arquivo de mensagem): se o valor da
  senha aparecer, a gravação é recusada e o componente sai com **exit 5 (`SENHA_VAZADA`)**.

## 6. Idempotência e trilha (doc 06 §7)

- `--chave-idempotencia` é obrigatória na ingesta (`exit 2` sem ela) e nomeia o **escopo** da rodada;
- a identidade da mensagem é **`UIDVALIDITY:UID`** (o `UIDVALIDITY` protege contra reutilização de UID
  quando a caixa é recriada) e o `Message-ID` vai no registro;
- mensagem já registrada com `resultado=INGERIDO` **não é buscada de novo nem reescrita** → `JA_INGERIDO`;
- a trilha é `--registro <arquivo.jsonl>` append-only, fora do banco: cada evento traz ambiente,
  identidade da configuração, identidade da mensagem, tamanho e `sha256` do corpo (prova de conteúdo sem
  despejar o corpo na trilha);
- `--ingerir` sem `--confirmo` é DRY_RUN e **não abre conexão**; sem `--saida` RECUSA (`exit 2`);
- `--desfazer <UIDVALIDITY:UID>` é dry-run até `--confirmo`, marca `DESFEITO` e **preserva** o registro
  original — e só uma **ingesta registrada** pode ser desfeita (o DRY_RUN do próprio `--desfazer` entra
  na trilha como auditoria do que não aconteceu, mas não serve de alvo; regressão medida no item 7.9).

## 7. Saída da ingesta (o que o W6-E05 recebe)

`--saida <dir>` recebe **um JSON por mensagem**, nome `UIDVALIDITY-UID.json`:
`uid`, `identidade_mensagem`, `message_id`, `de`, `para`, `assunto`, `data`, `flags`, `tamanho_bytes`,
`sha256_corpo` e `corpo_texto` (o `text/plain`). Nada é classificado aqui.

## 8. Verificação

| Instrumento | O que mede | Onde roda |
|---|---|---|
| `scripts/integracoes/verificar_imap_titan.py` | completude, inferências, matriz, guardas, invariante (inclusive a violação injetada), segredo, trilha — **sem abrir conexão** | offline |
| `scripts/integracoes/teste_imap_titan_aceite.sh` | TLS + LOGIN + EXAMINE + leitura + ingesta contra sink descartável em 127.0.0.1, com flags conferidas antes/depois e `--prova-de-dente` | offline (loopback) |
| `--provar` em `homolog` | saudação + TLS + LOGIN + CAPACIDADE + EXAMINE + NOOP contra `imap.titan.email`, sem trazer corpo | provedor (aprovação do dono) |

## 9. Fora do card (declarado)

- classificação da resposta (W6-E05) — este card entrega a mensagem crua;
- composição e envio (W6-E02/W6-E04) — aqui só se lê;
- Human Approval (W6-E03): a aprovação entra como identificador e é conferida, nunca concedida;
- prova contra o provedor real: exige credencial do Sales AI + aprovação registrada (homolog).

## 10. Lacunas (declaradas)

1. a trilha é arquivo JSONL, não tabela — quando o banco do TRE estiver de pé, o card de sincronização
   decide se ela passa a viver em `sync_events` (não se inventa schema aqui);
2. a v1 não mantém `IDLE`/push: quem repete a rodada é o chamador, com a mesma chave de idempotência;
3. não há parser de anexo nem de HTML: o corpo entregue é o `text/plain`;
4. `TRE_TITAN_IMAP_SEGURANCA=nenhuma` existe para o sink de dev e é recusado fora do loopback.
