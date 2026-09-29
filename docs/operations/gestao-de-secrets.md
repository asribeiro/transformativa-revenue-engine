# Gestão de Secrets — Transformativa Revenue Engine

**Card:** TRE-W0-E01-T02 · **Status:** política V1 **homologada** · **Data:** 29/09/2026

## 1. Princípios

1. **Nenhum segredo em Git** — nem no código, nem em workflow, nem em prompt, nem no histórico.
2. **Um segredo, um uso** — credencial separada por consumidor (Dev Harness, Sales AI, n8n, Odoo).
3. **Menor privilégio** — a credencial só pode o que a função exige.
4. **Rotação com data** — todo segredo tem gatilho de rotação registrado; nenhum é eterno.
5. **JEV nunca recebe segredo** — o payload de decisão é contexto mínimo (card, tipo, prioridade, risco).
6. **Receipt não carrega segredo** — nem em log, nem em telemetria, nem em mensagem de erro.

## 2. Onde os segredos vivem

| Local | O que guarda | Proteção |
|---|---|---|
| `/opt/data/.env` (fora do repositório) | credenciais do Dev Harness e do Sales AI | permissão `600`, dono único |
| credenciais do n8n | acessos usados por workflow | cofre do próprio n8n, nunca em nó Code |
| API key do módulo Odoo | integração Odoo ↔ n8n/PG | campo de sistema no Odoo, não em XML versionado |
| `/opt/data/.git-credentials` | push no GitHub | `600`, fora de qualquer repositório |

Nada de valor real em `.env.example` — ali só existem **nomes** de variáveis.

## 3. Segregação Hermes Dev Harness × Hermes Sales AI

| Credencial | Dev Harness | Sales AI |
|---|---|---|
| PG — DDL/migration | sim | **não** |
| PG — leitura/escrita de dados de negócio | sim | sim |
| Odoo — administração do módulo | sim | **não** |
| Odoo — operação comercial (lead, atividade, proposta) | sim | sim |
| n8n — publicar/alterar workflow | sim | **não** |
| n8n — disparar webhook aprovado | sim | sim |
| GitHub — push/deploy | sim | **não** |
| Titan — IMAP/SMTP (outbound) | não | sim |
| Infra do host (docker, chaves de VPS) | **não** (executa via operador) | **não** |

Regra derivada do brief: o **Hermes Sales AI não altera código nem faz deploy de produção**.

## 4. Inventário (nomes, nunca valores)

Hoje presentes no ambiente: `GITHUB_TOKEN` (rotacionado em 29/09/2026), credenciais dos provedores de LLM,
`TITAN_*`, `GOOGLE_*`, token do bot de Telegram.
Previstas para o TRE: `TRE_PG_*`, `TRE_ODOO_*`, `TRE_N8N_*`, `TRE_TITAN_*`, `TRE_JEV_*` (ver `.env.example`).

## 5. Ciclo de vida

- **Criação:** só o operador humano gera; o valor entra direto no cofre, nunca por chat.
- **Distribuição:** por variável de ambiente ou cofre do serviço; nunca por arquivo versionado.
- **Rotação:** imediata em caso de exposição ou troca de pessoa; periódica conforme o serviço; registro
  obrigatório em `docs/operations/registro-de-rotacao.md` (data, credencial, motivo, responsável — sem valor).
- **Revogação:** revogar **antes** de qualquer investigação (ordem inversa do instinto).

## 6. Prevenção

- `.gitignore` cobre `.env`, chaves, JSON de credencial e tokens.
- `scripts/secret_scan.sh` roda sobre o repositório e reprova o commit com achado.
- `scripts/hooks/pre-commit` bloqueia commit com padrão de segredo no **staged diff**; instalação via
  `bash scripts/install_hooks.sh`.
- Revisão de PR confere que nenhum segredo novo entrou por configuração.

## 7. Resposta a incidente

1. Revogar a credencial exposta.
2. Rotacionar e redistribuir pelos canais previstos.
3. Avaliar o alcance (o que ela permitia acessar) e registrar no changelog.
4. Se o vazamento veio de log/receipt, corrigir a origem antes de seguir.

## 8. Homologação

- **Quem aprovou:** Anderson Ribeiro (operador humano).
- **Quando:** 29/09/2026, pelo canal do Hermes (Telegram).
- **O que foi aprovado:** princípios, locais dos segredos, **matriz de segregação Dev Harness × Sales AI**
  (§3) e **gatilhos de rotação e revogação** (§5).
- **Evidência:** mensagem de aprovação registrada como comentário nos cards `TRE-W0-E01-T02` e
  `TRE-W0-E02-T01`, no board `transformativa-revenue-engine`; registro em `registro-de-aprovacoes.md`.
