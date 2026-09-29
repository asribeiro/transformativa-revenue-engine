# Fluxo de desenvolvimento e perfis de agente (TRE)

**Origem:** áudio do Anderson, 29/09/2026 —
*"se os cards não têm critérios de aceitação, dá um olhado no card, vê o que eles propõem fazer, e me ajuda
a escrever critérios de aceitação. A gente devia ter um perfil de análise de teste; se não tem, a gente pode
reaproveitar os perfis que a gente usou em projetos como o da construção da Transformativa. E aí a gente
deveria ter essas etapas: análise de requisito, análise de spec-driven development, toda a parte de
arquitetura, teste de aceitação, depois teste de validação e assim por diante."*

## O problema medido

No board existia **um único perfil de agente** (`default`): todo card, de qualquer natureza, cairia no mesmo
agente, sem especialização e sem etapa de teste. Não havia estágio de análise de requisitos, nem de análise
de teste, nem de validação — o card ia de `todo` para execução direto.

## Estágios do fluxo

| # | Estágio | Quem faz | Entrada | Saída (artefato) | Prova |
|---|---|---|---|---|---|
| 0 | BACKLOG | — | card do plano | card no board | card existe |
| 1 | Análise de requisitos | **Analista de Requisitos** | card + doc do baseline | critérios de aceitação escritos no card | critério testável, homologado |
| 2 | Spec | **Analista + Arquiteto** | requisito | spec/contrato da mudança (spec-driven) | spec versionada |
| 3 | Design e arquitetura | **Arquiteto de Software** | spec | decisão de arquitetura (ADR) quando muda estrutura | ADR registrado |
| 4 | Implementação | **Desenvolvedor** | spec + ADR | código + migration + testes | gates mecânicos |
| 5 | Teste (execução) | **Tester (QA)** | código | resultado de gate/smoke/suíte | exit code + saída real |
| 6 | Análise de teste e revisão | **Analista de Teste + Revisor** | diff + evidência | revisão item a item, reprovação justificada | veredito por item |
| 7 | Homologação | **Anderson** | entrega + evidências | aprovação registrada | aprovação humana registrada |
| 8 | Produção | **DevOps** | release aprovada | deploy | verificação pós-deploy |
| 9 | Verificação pós-deploy | **Tester + DevOps** | produção | confirmação com dado real | evidência de produção |

**Regra dura:** máquina **nunca** concede aprovação humana — o estágio 7 é do Anderson, sempre (matriz
Dev × Sales, `docs/architecture/hermes-dev-x-sales.md`).

## Perfis (reaproveitados do harness Hefesto — construção da Transformativa)

Nada foi inventado: são os perfis já usados no projeto de construção da Transformativa, via harness Hefesto
(`skill hefesto-harness`), adaptados ao contexto do TRE.

| Perfil | Papel | Escreve | Nunca faz |
|---|---|---|---|
| **Analista de Requisitos** | transforma card em requisito + critério de aceitação | critérios de aceitação, spec | não decide arquitetura, não implementa |
| **Designer (Design System)** | identidade visual e tokens | tokens/design lock | não decide regra de negócio |
| **Arquiteto de Software** | contratos, tipos, integração entre sistemas | ADR, contratos | não implementa produção |
| **Desenvolvedor** | implementa a mudança | código, migration, testes | não aprova o próprio trabalho |
| **Analista de Teste** (novo no TRE) | desenha o teste a partir do critério | plano de teste por critério | não implementa o que testa |
| **Tester (QA)** | executa gates, suíte, smoke | resultado bruto com exit code | não interpreta "sem output" como sucesso |
| **Revisor de Código** | revisão adversária do diff | parecer item a item | não aprova homologação |
| **DevOps** | build, deploy, rollback | release + evidência de deploy | não decide negócio |
| **Anderson** | homologação | aprovação | — |

Os campos ACCEPTANCE / TEST / ROLLBACK / RISK dos cards são escritos no **estágio 1** (o card nasce com a
lista do que falta) e o critério de aceitação só vale depois de homologado.

## Como os estágios conversam com o board e com o JEV

- **JEV** (`jev-policy-v1.0`) decide a **lane** (small/medium/high/critical) e o **esforço** — quanta
  capacidade a tarefa merece. O fluxo decide **quem** faz e **em que ordem**.
- **Lane ↔ estágio:** `small` não precisa dos estágios 3 e 6 completos (mas nunca dispensa o 5); `critical`
  exige todos, com revisão dedicada e aprovação humana registrada.
- **Board:** os estágios vivem dentro das colunas (BACKLOG → READY → DISCOVERY → IMPLEMENTATION → TEST →
  HUMAN APPROVAL → READY FOR PROD → PRODUCTION → VERIFIED). O board é a visão de alto nível; os estágios são
  o detalhe de execução.
- **Defeito:** defeito achado em qualquer estágio segue o `processo-de-defeitos.md` (card de defeito,
  pré-requisito do card origem, correção com evidência, liberação).

## O que falta implementar (em ordem)

1. **Criar os perfis** como perfis do Hermes + assignees do kanban (hoje só existe `default`).
2. **Escrever os critérios de aceitação** dos cards que não têm — a começar pela fila imediata (bloco JEV,
   W1 e W2). Proposta minha, homologação do Anderson.
3. **Gantt** (pedido seguinte, depois destes dois): visão de Gantt além do quadro kanban, para acompanhar a
   evolução dos 75 cards do roadmap.
