# ADR-0007 — Ambientes de dados do TRE na Contabo VPS 6 e backup em storage de objeto externo

**Status:** aceito (decisão do Anderson, 29/09/2026)
**Versão:** 1.0

## Contexto

O TRE precisa de PostgreSQL (Sales Intelligence), PostgreSQL do Odoo, Odoo Community e memória vetorial.
O card `TRE-W0-E01-T03` exige backup e **restore testável**, o que depende de saber onde os dados vivem.
Medição da VPS Hostinger atual (que roda Hermes, n8n, o MVP financeiro em produção, kanban e site):
8 GB de RAM com **5,0 GB disponíveis e 1,1 GB de swap já em uso**, **2 vCPU** com **6–7% de steal**
(limite de CPU do provedor já atuando) e 64 GB de disco livre.

## Decisão

1. **PostgreSQL, Odoo e a memória vetorial do TRE vão para a Contabo Cloud VPS 6** (6 vCPU · 12 GB ·
   200 GB), que já era a VPS decidida para os ambientes do produto. A Hostinger fica com o Hermes, o n8n
   e o MVP congelado.
2. **Backup automatizado pelo Hermes** (script versionado + timer na VPS + teste de restore no ambiente
   espelho + watchdog no Hermes, que roda em outra máquina).
3. **Destino do backup: storage de objeto externo S3-compatível** (~€2,49/250 GB), **não** o disco da
   própria VPS.
4. O **backup automático do provedor (~US$ 4–6/mês)** fica como segunda camada **opcional**, para o cenário
   de perda da máquina inteira.

## Alternativas

- **Tudo na Hostinger atual**: rejeitada — ~3 a 4,5 GB de serviços novos sobre 5,0 GB livres, numa máquina
  já paginando e com CPU limitada; risco de o OOM killer derrubar o MVP congelado ou o Hermes, além de
  contrariar a separação de ambientes do ADR-005.
- **Só o add-on do provedor**: rejeitada como única camada — snapshot de máquina não tem granularidade nem
  prova de restore, e não permite recuperar uma tabela específica.
- **Backup apenas no disco da VPS**: rejeitada — não é backup; morre com a máquina.

## Consequências

- Custo recorrente: storage externo (~€2,49/mês) + VPS Contabo (US$ 7,20/mês em 24 meses); add-on opcional.
- **W1/W2 (PostgreSQL e Odoo) ficam aguardando o provisionamento** da VPS nova.
- `TRE-W0-E01-T03` só fecha como VERIFIED com o primeiro restore testado de verdade; até então permanece
  aberto, com o desenho pronto (`Desenho — backup e restore (TRE)`, no vault).
- RPO inicial de 24 h; arquivamento de WAL (point-in-time) registrado como evolução futura.
