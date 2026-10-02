# Integração — ingestão e classificação de respostas (Titan)

Cards: `TRE-W6-E05-T01` (ingestão/classificação v1). Componente: `hermes/agentes/respostas/ingestao_respostas.py`
(`ingestao-respostas-v1`), contrato declarativo `hermes/agentes/respostas/ingestao-respostas-v1.json`.

## O que faz

Lê a caixa de respostas por IMAP (reusando o primitivo do card `TRE-W6-E01-T02`, sem reimplementar
IMAP), classifica cada mensagem em um vocabulário fechado e grava a resposta em
`sales_intelligence.interactions` com trilha de idempotência em `sales_intelligence.sync_events`.

Vocabulário fechado (v1) — categoria fora da lista faz o **contrato recusar** (não há fallback silencioso):

| categoria | significado | sinal principal |
|---|---|---|
| `INTERESSE` | quer avançar | "podemos conversar", "me liga", "manda horário" |
| `SEM_INTERESSE` | recusa sem pedir saída | "não faz sentido agora", "já tenho fornecedor" |
| `OPT_OUT` | pede para sair | "remover meu e-mail", "descadastrar" |
| `BOUNCE` | endereço falhou | `From: MAILER-DAEMON`, `Content-Type: multipart/report`, `Delivery-Status` |
| `AUTO_RESPOSTA` | ausência automática | `Auto-Submitted`, `X-Autoreply`, `X-Autorespond` |
| `RUIDO` | newsletter/marketing | `List-Unsubscribe`, `Precedence: bulk` |
| `INDEFINIDO` | não casou nada | fail-closed: sem palpite |
| `NAO_RESPOSTA` | mensagem nossa ou sem conteúdo de resposta | remetente do próprio domínio, sem corpo |

## Precedência (o ponto de risco)

`OPT_OUT` vence `INTERESSE` sempre. A precedência é dada por `ordem` decrescente no contrato, não pela
ordem de escrita das regras no arquivo — reordenar o JSON não muda o resultado. A `--prova-de-dente`
muta exatamente esse ponto (item `opt-out-sem-prioridade`).

`BOUNCE` e `AUTO_RESPOSTA` vêm antes de `OPT_OUT` porque uma resposta automática de descadastro **não é**
um pedido de descadastro da pessoa.

## Exclusões declaradas (não viram interação)

- texto citado do histórico do fio (`Em <data>, <quem> escreveu:` e linhas `>`) — senão a nossa própria
  chamada para descadastro no rodapé citado viraria `OPT_OUT` do lead;
- assinatura depois de `-- `.

## Garantias de leitura (o "ler que escreve")

- abre a caixa em `EXAMINE` (read-only) — nunca `SELECT`;
- busca com `BODY.PEEK[]` — nunca `BODY[]`;
- **não** marca lido, **não** altera flag, **não** move, **não** apaga, **não** responde;
- auditoria da própria fonte antes de conectar: o caminho de ingesta **executa** a auditoria e recusa se
  a fonte ganhar escrita.

## Escrita no banco

- apenas `INSERT` em `sales_intelligence.interactions` e `sales_intelligence.sync_events`;
- nenhum `DDL`, `UPDATE`, `DELETE` ou `TRUNCATE`;
- idempotência por `resposta:<UIDVALIDITY:UID>` — replay não duplica linha;
- sem vínculo com `contacts` → `SEM_VINCULO` na trilha, em vez de inventar organização/contato.

## Ambiente e credencial

- credenciais **só** por variável de ambiente (`TRE_TITAN_USER`, `TRE_TITAN_PASSWORD`); segredo nunca em
  arquivo versionado, nunca em log (relatório mascara);
- `dev` = host loopback + login de dev; `homolog` = caixa declarada em lista explícita; `prod` recusa por
  desenho (exit 4);
- `--confirmo` obrigatório para ingerir; sem ele o componente apenas mede (dry-run).

## Como medir

```bash
python3 scripts/agentes/verificar_ingestao_respostas.py              # suite offline (50 itens)
python3 scripts/agentes/verificar_ingestao_respostas.py --prova-de-dente   # 6 mutações (56 itens)
bash   scripts/agentes/teste_ingestao_respostas_aceite.sh           # E2E (43 itens), ver runbook
```

Sem rede, sem banco e sem credencial nos dois primeiros: eles importam o componente e usam banco
`stub` que recusa comandos fora do contrato.
