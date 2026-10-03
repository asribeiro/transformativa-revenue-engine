# Runbook — gerador de abordagem outbound v1 (TRE-W6-E02-T01)

Componente `gerador-abordagem-v1` (`hermes/agents/outreach/outreach_generator.py`). Ele **não envia nada**:
grava um pedido de aprovação humana (`human_approvals`, `PENDING`) e para.

## 1. Antes de rodar

- Banco: `sales_intelligence` na VPS do ambiente (ADR-0008). A porta de banco é o comando `psql` completo, por
  exemplo `docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence`.
- Ambiente: `--ambiente dev|homolog`. **`prod` é recusado (exit 4)** — ADR-005.
- A empresa precisa ter: recomendação `NEXT_BEST_ACTION` com status `OPEN` (saída do NBA, card W5-E07-T01),
  contato indicado e evidência (pesquisa, dor, sinal, score ou registro TIER).
- Provedor: por padrão `offline` (renderizador determinístico, sem rede, sem custo). Para modelo real:
  `--provedor chat-completions --base-url <https://…> --modelo <nome>` **e** `TRE_OUTREACH_API_KEY` no
  ambiente. A credencial nunca vai em argumento, arquivo ou relatório.

## 2. Comandos

```bash
# ler a politica sem tocar no banco
python3 hermes/agents/outreach/outreach_generator.py --planejar
python3 hermes/agents/outreach/outreach_generator.py --regras

# gerar a abordagem de uma empresa (grava o pedido PENDING + auditoria)
python3 hermes/agents/outreach/outreach_generator.py --ambiente dev \
  --prefixo "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --organizacao <uuid-da-empresa> --relatorio /tmp/outreach.json

# várias empresas de uma vez
python3 hermes/agents/outreach/outreach_generator.py --ambiente dev --prefixo "..." --jsonl lista.jsonl

# com modelo real (credencial SO pelo ambiente)
TRE_OUTREACH_API_KEY=… TRE_OUTREACH_BASE_URL=https://api.exemplo/v1 TRE_OUTREACH_MODELO=<modelo> \
python3 hermes/agents/outreach/outreach_generator.py --ambiente dev --prefixo "..." \
  --provedor chat-completions --organizacao <uuid>

# desfazer uma rodada (dry-run e, depois, confirmado)
python3 hermes/agents/outreach/outreach_generator.py --ambiente dev --prefixo "..." --desfazer <correlation_id>
python3 hermes/agents/outreach/outreach_generator.py --ambiente dev --prefixo "..." --desfazer <correlation_id> --confirmo
```

## 3. Como ler a saída

| Linha | Significado |
|---|---|
| `acao=… canal=… veredito=GERADA pedido=<uuid>` | pedido de aprovação gravado (`PENDING`) |
| `veredito=JA_GERADA` | mesma entrada citável: **nada** duplicado, nada expirado |
| `veredito=ABSTEVE motivo=SEM_ACAO_RECOMENDADA` | não há recomendação `OPEN` para a empresa (rode o NBA antes) |
| `veredito=ABSTEVE motivo=ACAO_SEM_ABORDAGEM_DECLARADA` | a ação recomendada não gera abordagem nesta política (`WAIT`, `NURTURE`, …) |
| `veredito=RECUSADA motivo=CONTATO_BLOQUEADO` | `do_not_contact`/`opt_out_*` ligado: **não é preferência, é bloqueio** |
| `veredito=RECUSADA motivo=SEM_EVIDENCIA` | nenhum fato lido (pesquisa/sinal/dor/score/TIER): abordagem seria genérica |
| `veredito=RECUSADA motivo=ABORDAGEM_INVALIDA` | texto voltou fora dos limites, sem citação, com marcador inexistente ou afirmação proibida |
| `veredito=ERRO motivo=PROVEDOR_*` | provedor incompleto/recusou/falhou ou resposta ilegível — nada foi gravado |
| `veredito=ERRO motivo=PORTA_DE_BANCO_AUSENTE` | a porta `--prefixo` não é `psql` funcional |

## 4. Operação e diagnóstico

- **Conferir o pedido**: `SELECT id, action_type, status, proposed_action->>'canal', left(proposed_action->>'assunto', 60),
  proposed_action->>'entrada_hash' FROM sales_intelligence.human_approvals WHERE status='PENDING' ORDER BY requested_at DESC;`
- **Conferir a rodada**: `SELECT status, input->>'prompt_version', input->>'model_provider', input->>'model_name',
  output->>'human_approval_id' FROM sales_intelligence.agent_runs WHERE correlation_id = '<correlation_id>';`
- **Expirados**: pedidos `PENDING` que perderam a vez aparecem como `EXPIRED` (histórico preservado). É o
  comportamento esperado quando a evidência citável muda — não é falha.
- **Alucinação do modelo** (`ERRO`/`RECUSADA` com `numero sem sustentacao`, `URL sem sustentacao`,
  `e-mail sem sustentacao`, `marcador de evidencia inexistente`): a evidência lida não sustenta o texto. Ou o
  modelo está inventando, ou falta evidência no banco — nos dois casos **nada** foi gravado. Corrija a evidência
  (pesquisa/sinais) em vez de afrouxar a validação.
- **Ajustar tom/canal/limites**: é a política (`hermes/agents/outreach/politica-outreach-v1.json`), não o código.
  Mudar o **texto** do prompt exige arquivo novo + versão nova (`abordagem-v2`) e atualização do contrato do
  componente — o `prompt_version` da auditoria é o que liga a abordagem ao texto que a gerou.
- **Nunca** contornar `human_approvals`: o envio é do W6-E04 e só depois da decisão humana (W6-E03).

## 5. Verificação

```bash
python3 scripts/agentes/verificar_gerador_abordagem.py              # suite offline: PASS (100 OK / 0 falhas)
python3 scripts/agentes/verificar_gerador_abordagem.py --autoteste # 12/12 mutacoes detectadas
bash scripts/agentes/teste_gerador_abordagem_aceite.sh              # ACEITE_OUTREACH_001_OK (69 OK / 0 FALHOU)
bash scripts/agentes/teste_gerador_abordagem_aceite.sh --prova-de-dente   # 4/4 dentes
```
