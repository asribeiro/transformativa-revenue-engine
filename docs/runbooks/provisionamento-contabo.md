# Runbook — provisionamento da VPS do TRE (Contabo Cloud VPS 6)

**Status:** pronto, **não executado** (compra realizada em 29/09/2026; provisionamento em andamento no provedor).
**Cards relacionados:** W1 (PostgreSQL), W2 (Odoo) e `TRE-W0-E01-T03` (backup).

## 1. Antes de começar

| Item | Estado |
|---|---|
| VPS Contabo Cloud VPS 6 (6 vCPU · 12 GB · 200 GB) | comprada, aguardando provisionamento (30 min) |
| Object Storage (destino do backup) | comprado (provisionamento imediato) |
| Chave pública do Hermes para acesso dedicado | gerada (`ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINCs…`), anexada no pedido de bootstrap |
| Credenciais de root da VPS | chegam por e-mail para o Anderson |

## 2. Passo 1 — bootstrap (uma vez, como root)

```bash
TRE_PUBKEY="<chave publica do Hermes>" bash bootstrap-vps.sh
```

O que ele faz, em ordem: pacotes base e fuso `America/Sao_Paulo`; Docker CE + compose plugin pelo repositório
oficial; usuário dedicado de deploy (`tre-deploy`, grupos `docker` e `sudo`, sudo sem senha); árvore
`/opt/tre/{dev,homolog,prod}/{pg,odoo,n8n,backups,compose}`; firewall `ufw` com só a 22 aberta; `fail2ban`;
SSH sem senha e root sem senha; atualizações automáticas **sem** reboot.

## 3. Passo 2 — decisões de segurança registradas

- **Acesso dedicado, não root**: o Hermes entra como `tre-deploy`. Sendo honesto sobre o alcance: pertencer ao
  grupo `docker` já é equivalente a root na máquina, e por isso o sudo sem senha foi concedido de forma
  explícita e documentada, em vez de simular um isolamento que não existiria.
- **Porta 22 aberta; 80/443 fechadas** até existir serviço para publicar. Cada publicação exige abrir porta
  explicitamente e registrar no runbook.
- **Backup fora da máquina** (storage de objeto), conforme ADR-0007.

## 4. Passo 3 — credenciais que o Anderson precisa inserir (nunca por chat)

1. **Chaves do Object Storage** (painel da Contabo) → gravadas no arquivo de ambiente da VPS, permissão 600,
   conforme a política do `TRE-W0-E01-T02`. Registro em `docs/operations/registro-de-rotacao.md`.
2. Nada mais: a chave SSH do Hermes já entra no bootstrap.

## 5. Passo 4 — o que o Hermes faz depois (W1/W2)

1. Confere o bootstrap (docker, compose, árvore, firewall, fuso) e emite veredito por item.
2. Sobe PostgreSQL dos 3 ambientes por compose, com volume próprio e senha gerada na hora (sem valor em Git).
3. Roda as migrations do Data Contract (`db/migrations`) no banco de **dev**, depois homologação — nunca direto
   em produção (ADR-005: nenhuma DDL nasce em produção).
4. Instala o Odoo Community no ambiente de dev, instala o módulo `transformativa_sales_ai` e valida o aceite
   do W2 (empresa A+ criada com os campos e views corretos).
5. Configura o backup diário (script + timer) com destino no Object Storage e **roda o primeiro restore testado**,
   fechando o `TRE-W0-E01-T03` com evidência real.

## 6. Rollback

- Bootstrap: reverter criando nada destrutivo — remover o usuário, o arquivo em `/etc/sudoers.d` e a árvore
  `/opt/tre`; o firewall pode ser reaberto com `ufw disable` em caso de perda de acesso (feito pelo painel do
  provedor, com console VNC).
- Serviços: cada ambiente é um compose independente; `docker compose down` no ambiente afetado não toca os
  outros dois.
