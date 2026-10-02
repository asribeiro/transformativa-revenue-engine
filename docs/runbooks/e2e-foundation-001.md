# E2E Foundation #001 — o caminho ponta a ponta da fundacao

Runbook do card **TRE-W3-E06-T01**. O que e' este aceite, como se roda, o que cada item significa e
**o que ele NAO mede** (lacuna declarada — nao se inventa medicao que mente).

Aceite: `scripts/e2e/verificar-e2e-foundation-001.sh`.
Cenario de origem: `Comercial Transformativa/08_PLANO_DE_TESTES_E_VALIDACAO.md` §3.

## 1. O que este aceite e' (e o que nao e')

**E'** o encadeamento dos cinco cards da onda W3 do board (`E01-T05`, `E02-T02`, `E03-T01`,
`E04-T01`, `E05-T01`) num **unico trio descartavel** — um postgres, um Odoo com o modulo
`transformativa_sales_ai` instalado e um n8n com os quatro workflows derivados — para provar que as
portas **conversam entre si** e que o caminho **nao duplica** quando o mesmo evento volta.

Ate' aqui, cada card media a sua porta no seu proprio trio. Cada aceite individual pode estar verde
e a **junta** pode nao fechar (o ID que o Odoo devolve nao voltar para a trilha, o evento de chave
ja' entregue ser entregue de novo, a reconciliacao acusar divergencia no dado que acabou de ser
gravado). E' exatamente essa a pergunta de que a fundacao depende — e ela nao se responde por
inspecao de codigo.

**NAO e'** o doc 08 §3 inteiro. O cenario tem 19 passos; a fundacao entrega 1..3, 11..19 e o sentido
Odoo -> PostgreSQL (porta de ingestao). Os passos **4..10** (research da empresa, 3 signals, os 4
scores, `priority>90`, tier A+, pain hypothesis e a recommendation "Contact CFO") sao dos cards
W4-*/W5-* e aparecem no aceite como linha `DECLARADO` — nunca como `OK`. Quem citar este aceite deve
citar o escopo com ele: *a fundacao fecha o caminho ponta a ponta e nao duplica; o diagnostico
comercial em cima dele ainda nao existe.*

## 2. Como se roda

Nada aqui e' feito a mao: o aceite sobe o trio, mede, fecha o trio.

```bash
# regressao barata: gates do projeto + os 4 aceites de origem em --apenas-codigo
bash scripts/e2e/verificar-e2e-foundation-001.sh --apenas-codigo

# o cenario completo (trio descartavel; e' o modo de producao do aceite)
TRE_LOG_DIR=/opt/tre/<dir>/logs-e2e bash scripts/e2e/verificar-e2e-foundation-001.sh

# o E2E tem dentes? (baseline NAO mutado + 3 mutacoes nomeadas do consumidor)
bash scripts/e2e/verificar-e2e-foundation-001.sh --prova-de-dente

# deixar o trio de pe para investigar (nao faz limpeza no fim)
bash scripts/e2e/verificar-e2e-foundation-001.sh --manter
```

Modos: `--apenas-codigo` (so' os gates e as lentes/suites das 4 portas) · `--apenas-cenario` (o
cenario, sem os gates; e' o que os sub-runs do dente usam) · `--prova-de-dente` · `--manter`.

**Requer**: docker com as imagens `odoo:19.0`, `postgres:16` e `n8nio/n8n:latest`; `openssl`, `curl`,
`python3`; o repositorio em disco (o script descobre a raiz a partir do proprio caminho). Roda no
host do board (VPS), nunca dentro de container sem docker.

**Variaveis** (todas com default seguro): `TRE_WORKFLOW` (consumidor sob teste), `TRE_MODULO_DIR`,
`TRE_BANCO` (banco do Odoo, tem de casar `^tre_[a-z0-9_]+$`), `TRE_BANCO_SI`, `TRE_IMAGEM`,
`TRE_IMAGEM_PG`, `TRE_IMAGEM_N8N`, `TRE_LOG_DIR`, `TRE_PG_USER`, `TRE_DEV_PG_CT`,
`TRE_DEV_HOMOLOG_PROD`, `TRE_MANTER_BANCO`.

**Saida**: um item por linha (`OK` / `FALHOU` / `DECLARADO`), o detalhe em `$TRE_LOG_DIR`, resumo em
uma linha e exit code (`0` = cumprido, `1` = falhou ou nao deu para medir, `2` = uso errado).

## 3. O que cada passo mede

| passo | o que mede | por que nao da' para fingir |
| --- | --- | --- |
| 0 | gates do projeto (`verificar_estrutura`, `secret_scan`, `verificar_papeis`, `verificar_contrato_dados`) + os **4 aceites de origem** em `--apenas-codigo` | derivar os 4 workflows de contrato+nucleo nao e' aceitavel: as lentes e as suites dos nucleos rodam de verdade |
| guardas | docker/imagens/ferramentas, artefatos em disco, `sha256` dos 6 artefatos sob teste, nome do banco descartavel, ambiente do dev **antes** | medicao feita sobre artefato que muda no meio nao vale; o sha256 e' reconferido no fecho |
| 1 | schema do contrato (12 tabelas) no banco descartavel + modulo `installed` | sem o modulo instalado nao existe porta unica para medir |
| 2 | servidor da porta unica + chave em arquivo `600` + **sonda** `dry_run` autenticada `HTTP 200` | a sonda prova que a credencial vale antes do cenario: senao o aceite mediria 401 como se fosse contrato |
| 3 | n8n descartavel: cofre com postgres/API/token da porta, os **4 workflows** importados e exportaveis pelo `id` estavel, o da ingestao **ativo** | o webhook de producao so' existe com o workflow ativo — importar e nao ativar da' 404 e mascara o teste |
| B | organizacao da ACME na fonte da verdade (`organizations`) com UUID canonico + evento `COMPANY_QUALIFIED` no `outbox_events` | e' o passo 1..3, 11 do doc 08 medido no banco, nao na narrativa |
| C | consumidor entrega pela porta unica: **um** `res.partner` com `tf_company_id` = UUID (nome, CNPJ, dominio, `is_company` do evento), evento `PROCESSED`, trilha `postgres->odoo` `UPSERT` `COMPLETED`, **o ID devolvido pelo Odoo dentro do `response_payload`**, a ponta no PG (`organizations.odoo_partner_id`) registrada com o MESMO id, e chamada autenticada real na auditoria | AC5: o ID que o Odoo devolve tem de voltar para a trilha e fechar a ida-e-volta; sem isso o espelho nao tem chave |
| D | `contato_upsert` pela porta (2x pela **mesma identidade**): 1 registro, `is_company=false`, rastro na auditoria | AC4/AC6: identidade forte nao duplica no CRM |
| E | `atividade_criar` ancorada no parceiro do contato, com `tf_idempotency_key`; 1 registro, `res_id` conferido | AC4: a atividade existe e aponta para o registro certo |
| F | reconciliacao (E04) rodando **no mesmo trio**: veredito `OK` e **0 divergencia** | prova que o dado que o E2E acabou de gravar e' coerente entre as duas pontas |
| G | sentido Odoo -> PG pela porta de ingestao: os **7 eventos do contrato** na fila, o remetente entrega (`SENT = fila`), uma linha de trilha por evento, todas `COMPLETED`, `source_version` 1.0, e **o reenvio do MESMO envelope nao cria linha nova** | AC5/AC6 no sentido inverso: idempotencia nao se supõe, se mede |
| H | observabilidade (E05) no mesmo trio: veredito `OK` na rodada saudavel | AC8: a fotografia do sync tem de fechar depois do E2E ter mexido em tudo |
| I | o evento do outbox **volta para a fila**: `REPLAY` — 0 chamada nova na porta, **a mesma linha** de trilha (mesmo `id`, mesmo `completed_at`), 1 empresa no CRM | AC6: e' o item que a mutacao `sem_consulta_de_trilha` reprova |
| J | `fail-closed` no meio do caminho: evento **sem `event_version`** -> `DEAD_LETTER` **sem chamada** a porta, com motivo nomeado | AC7: envelope fora do contrato nao chega na ponta |
| K | contagens de duplicata lidas do banco, dev **depois**, homolog/producao sem arquivo novo, token da porta ausente de log e de `request_payload`, sha256 reconferido (passo 19) | AC10: sem isso o aceite nao distingue "passou" de "nao mediu" |

## 4. Como se le o veredito

- `E2E_FOUNDATION_001_OK` — a fundacao fecha o caminho e nao duplica; os passos fora do escopo estao
  contados em `DECLARADO`.
- `E2E_FOUNDATION_001_FALHOU` — alguma medida nao bateu: cada `FALHOU` nomeia o passo e aponta o log.
- `E2E_FOUNDATION_001_DENTE_OK|DENTE_FALHOU` — so' no modo `--prova-de-dente`.

**Regra do dente** (fail-closed): o modo roda primeiro o cenario **sem mutacao** — se o baseline nao
ficar verde, o veredito e' `DENTE_FALHOU` (o ambiente nao mede o cenario, entao "o dente morde" nao
seria conclusao, seria ruido). Depois aplica 3 mutacoes nomeadas com o mutador versionado do card
`E02-T02`:

| mutacao | item que tem de reprovar |
| --- | --- |
| `sem_validacao_de_envelope` | evento sem `event_version` vai para `DEAD_LETTER` sem chamada |
| `mapeamento_trocado` | o parceiro nasceu com o dominio do evento |
| `sem_consulta_de_trilha` | repetir o evento NAO duplica |

Cada dente so' **conta** se o item declarado aparecer como `FALHOU` na saida do sub-run mutado.
Qualquer outro veredito (`NAO_CONTA`, `MUTACAO_SEM_DENTE`, `MUTACAO_NAO_APLICADA`, baseline vermelho)
fecha `DENTE_FALHOU`. Dai' a regra de redacao: o item que uma mutacao deve reprovar tem o **mesmo
trecho** nos dois ramos (ok e falhou).

## 5. Operacao — o que sobra depois da rodada

- **Nao toca o ambiente**: o trio (container de postgres, container do Odoo, container do n8n, rede)
  e' descartavel e some no fim; o diretorio de trabalho (`/tmp/e2e-foundation-XXXXXX`, modo `700`)
  guarda senha, chave e token e e' removido junto. `--manter` preserva tudo para investigacao — e
  nesse caso o operador apaga na mao.
- **Nao toca o dev**: a instancia `pg-odoo-dev` e' medida antes e depois (lista de bancos) quando
  esta' de pe; `homolog`/`prod` sao conferidos por contagem de arquivos. Nenhum arquivo e' escrito
  la'.
- **Segredo**: a chave da API entra por arquivo `600` e nunca em argumento de linha de comando (a
  linha de comando fica visivel em `ps`). O token da porta entra pelo arquivo `600` do descartavel e
  o aceite confere que ele **nao** aparece no log do preparo nem em `request_payload`.
- **Idempotencia dos arquivos de importacao**: `credenciais.json` e `workflow.json` saem do cofre do
  n8n depois de importados (o cofre nao guarda copia em claro de credencial).
- **Falha na subida do trio**: as guardas e o passo 1..2 fecham o aceite com `FALHOU` em vez de
  seguir medindo contra um ambiente incompleto (medicao ausente nao vira medicao zero).

## 6. Lacunas declaradas (o que este aceite nao prova)

1. **Passos 4..10 do doc 08 §3** (research, signals, scores, tier, pain, recommendation): nao
   existem na fundacao — cards W4-*/W5-*.
2. **Carga e volume**: o cenario e' unitario (1 organizacao, 1 evento, 1 contato, 1 atividade). Nao
   mede fila longa, concorrencia entre dois consumidores, nem tempo de rodada.
3. **Odoo real do cliente**: o aceite usa a imagem `odoo:19.0` com o modulo versionado. Nao cobre
   customizacoes de instancia, nem a versao de banco do ambiente de producao.
4. **Reprocesso operacional** (reprocessar dead-letter depois de corrigir a causa, ligar/desligar
   cron do consumidor): e' passo de operacao, tem o seu aceite proprio no card `E02-T02`.
5. **A ponta do vinculo no PG (`organizations.odoo_partner_id`) nao e' escrita por nenhuma porta da
   fundacao**: o consumidor escreve a fila (`outbox_events`) e a trilha (`sync_events`) e devolve o ID
   na trilha; `organizations` e' da esteira de negocio (W4/W5). O aceite registra essa ponta com o ID
   que veio na trilha (passo 17 do doc 08) e **mede a ida-e-volta** — a reconciliacao (E04) acusa
   `id_cruzado_sem_volta_no_pg` justamente quando ela falta. Quem escrever essa coluna na esteira de
   negocio tem de escrever o MESMO id que a trilha guardou.
