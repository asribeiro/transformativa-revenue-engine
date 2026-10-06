# Runbook — Captura de lead do site v1 (`TRE-W7-E01-T01`)

**Card:** `TRE-W7-E01-T01` (W7 · Inbound/Multicanal) · **Componente:** `hermes/agentes/inbound/captura_site.py`
(`captura-site-v1`) · **Contrato:** `hermes/agentes/inbound/captura-site-v1.json`

## 1. O que este componente faz

Recebe a submissão do formulário do site **já normalizada em JSON** e:

1. valida a submissão contra o contrato (`captura-site-v1.json`) — vocabulário fechado, nunca inventado;
2. trata **consentimento como barreira** (sem opt-in explícito e base legal declarada: nada é cadastrado);
3. resolve a identidade da empresa por **identificador forte** (CNPJ → domínio → LinkedIn) antes do **fraco**
   (nome+cidade, nome+telefone) e manda o fraco para `REVIEW_REQUIRED` — nunca merge silencioso;
4. grava `organizations` + `contacts` + `interactions` (canal `WEBSITE`, direção `INBOUND`, tipo
   `FORMULARIO_SITE`) com UUID canônico gerado no próprio INSERT;
5. grava a trilha de idempotência em `sync_events` com a chave `site:<submission_id>`;
6. mascara e-mail, telefone e CNPJ na evidência (relatório/trilha).

**Formato da submissão** (ver `submissao.campos_obrigatorios` no contrato):

```json
{
  "submission_id": "form-2026-10-03-0001",
  "enviado_em": "2026-10-03T15:30:00Z",
  "origem": {"pagina": "/contato", "utm_source": "linkedin", "utm_campaign": "eficiencia"},
  "consentimento": {"aceito": true, "legal_basis": "CONSENTIMENTO", "texto_versao": "privacidade-v1"},
  "empresa": {"nome": "Distribuidora Aurora LTDA", "cnpj": "", "dominio": "aurora.test", "cidade": "Campinas", "estado": "SP"},
  "contato": {"nome": "Marina Prado", "email": "marina@aurora.test", "telefone": "+55 19 99888-7766", "cargo": "COO", "preferred_channel": "email"}
}
```

## 2. Como rodar

```bash
# sem tocar o banco
python3 hermes/agentes/inbound/captura_site.py --planejar
python3 hermes/agentes/inbound/captura_site.py --conferir
python3 hermes/agentes/inbound/captura_site.py --autoteste          # auditoria da própria fonte

# dry-run (não grava) e escrita
python3 hermes/agentes/inbound/captura_site.py --submissao submissao.json
python3 hermes/agentes/inbound/captura_site.py --submissao submissao.json --confirmo \
  --porta-banco "docker exec -i pg-site-acc psql -U sales_ai -d sales_intelligence" \
  --relatorio out/relatorio.json --trilha out/trilha.jsonl
```

Variáveis: `TRE_AMBIENTE` (`dev`|`homolog`|`prod`), `TRE_SITE_PORTA_BANCO`, `TRE_SITE_APROVACAO_HUMANA`,
`TRE_SITE_TOKEN` (se aparecer na evidência, a gravação é recusada — exit 5).

Exit codes: `0` capturado/replay/dry-run/recusa registrada · `2` uso errado · `3` recusa (contrato,
fonte, guarda, banco) · `4` produção recusada · `5` segredo vazado.

**Status possíveis** (`vocabulario.status_trilha`): `CAPTURADO`, `JA_CAPTURADO`, `REVIEW_REQUIRED`,
`RECUSADO_CONSENTIMENTO`, `SEM_DADOS_MINIMOS`, `SUBMISSAO_INVALIDA`.

## 3. Verificação

| Instrumento | O que mede | Como rodar |
|---|---|---|
| `scripts/agentes/verificar_captura_site.py` | decisão do componente contra porta de banco falsa (32 itens) | `python3 scripts/agentes/verificar_captura_site.py` |
| `... --autoteste` | 5 dentes: cada mutação reprova o item declarado | `python3 scripts/agentes/verificar_captura_site.py --autoteste` |
| `scripts/agentes/teste_captura_site_aceite.sh` | cadeia inteira em PostgreSQL descartável (46 itens) | `bash scripts/agentes/teste_captura_site_aceite.sh` |

O aceite exige `docker` com a imagem `postgres:16` e roda **na VPS do ambiente**; o container
(`pg-site-acc`) e as pontas são descartáveis e ficam em `127.0.0.1`. Regressão registrada:
`ACEITE_CAPTURA_SITE_001_OK (46 itens, 0 falhas)`.

## 4. Guardas de ambiente (ADR-005)

- **dev:** porta de banco **local** obrigatória (`docker exec -i pg-<dev|aceite> psql …`); prefixo remoto
  (`ssh … psql`) é recusado com `BANCO_NAO_E_DEV` (exit 3).
- **homolog:** exige `TRE_SITE_APROVACAO_HUMANA` registrada (`HOMOLOG_SEM_APROVACAO`).
- **prod:** recusa por desenho (exit 4). Nada nasce em produção; a promoção é ato humano registrado.
- Escrever exige `--confirmo`; sem ele a rodada é `DRY_RUN` e nada toca o banco.

## 5. Lacunas declaradas (não são falha deste card)

1. **Sem escrita no Odoo.** O lead inbound não chega ao CRM por aqui: o vocabulário de eventos
   PG → Odoo do Data Contract V1 §6 não tem evento de lead capturado e o consumidor de outbox é
   fail-closed (evento fora do contrato → `DEAD_LETTER`). Criar `event_type` novo exige nova versão do
   contrato **+ aprovação humana registrada** (doc 12 / ADR-0004) — decisão do dono.
2. **Sem endpoint HTTP.** O POST público do formulário é do n8n/website (fora do repositório); aqui entra a
   submissão já normalizada.
3. **Sem atribuição de campanha** além do que o formulário declara: atribuição de clique é `W7-E02` (Meta)
   e `W7-E03` (Google).
4. `collected_at`/`retention_until` continuam ausentes do schema (lacuna já declarada no Data Contract V1 §9).

## 6. Rollback

Nada nasce em produção e nenhuma DDL acompanha o card: o rollback é **remover o branch/commit**
(4 arquivos novos) e, no ambiente de aceite, remover o container descartável (`docker rm -f pg-site-acc`).
Não há migração a desfazer nem dado de dev a limpar que afete o ambiente em uso.
