# ADR-0008 — Como o Hermes executa trabalho no PostgreSQL e no Odoo do TRE

- **Status:** aceita
- **Data:** 2026-09-29
- **Decisor:** Anderson Ribeiro (confirmado por mensagem em 29/09/2026)
- **Resolve:** item 5 do review do baseline V1.1.0 (*"Sem caminho do Hermes para PostgreSQL/Odoo"*)

## Contexto

O Hermes roda em container na VPS Hostinger e **não tem rota de rede** até a VPS do TRE (Contabo,
`169.58.24.102`), nem cliente `psql` instalado. O Odoo e o PostgreSQL do TRE vivem na Contabo, em Docker.
A partir da onda W1 (PostgreSQL) todo card precisa de banco — a lacuna deixaria de ser teórica.

Havia três caminhos possíveis: (a) expor o banco na rede para o container alcançar; (b) rede compartilhada
entre as máquinas; (c) o Hermes **executar na própria VPS**, por SSH.

## Decisão

**(c)** — o Hermes executa o trabalho de banco **na VPS do TRE, por SSH**, autenticado pela chave
`/opt/data/.ssh/tre_deploy` como usuário `tre-deploy` (com `sudo` e `docker` sem senha). É o mesmo caminho
já provado em backup, restore e rollback (card T03).

- Sem porta de banco exposta e sem rede compartilhada entre as máquinas.
- Segredos de banco continuam **só** na VPS (`/etc/tre/...`, 600).
- Migrations e consultas rodam dentro do ambiente do TRE (`docker exec`), não a partir do container do Hermes.

## Consequências

- Isolamento entre as máquinas preservado: nada de firewall novo, nada de superfície nova.
- O Hermes passa a depender do SSH estar de pé; queda de SSH vira falha **visível** (o vigia externo já cobre
  parte disso) e não pode ser confundida com sucesso.
- Scripts de banco do projeto devem ser escritos para rodar **na VPS** (caminhos `/opt/tre/...`), com o
  container do Hermes apenas orquestrando por SSH.
- Fica descartado, por ora, instalar cliente `psql` no container do Hermes: sem rota, o cliente não serve para nada.

## Alternativas descartadas

- **Expor o PostgreSQL na rede** (`5432` público ou por VPN): abre superfície de ataque para ganhar pouco —
  a decisão do dono foi explícita a favor de manter a operação na VPS.
- **Rede compartilhada entre Hostinger e Contabo**: acopla duas máquinas de papéis diferentes e cria um
  caminho permanente que ninguém audita depois.
