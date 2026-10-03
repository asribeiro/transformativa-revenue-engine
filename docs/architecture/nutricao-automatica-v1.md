# Nurture automatizado v1 (`nutricao-automatica-v1`) — card TRE-W9-E05-T01

Documento do card: o que o componente faz, o que ele **nao** faz, os criterios de aceitacao, o plano de
teste, o rollback e o risco. O verificador e' `scripts/agentes/verificar_nutricao_automatica.py` (suite
offline, sem banco, com autoteste por mutacao) e o aceite E2E e'
`scripts/agentes/teste_nutricao_automatica_aceite.sh` (PostgreSQL descartavel na VPS, cadeia inteira).

## 1. O que entra e o que sai

O nurture **nao mede nada por conta propria**: ele consome as duas camadas ja' medidas pelos pais e
transforma a medicao em PLANO.

| Papel | Objeto |
|---|---|
| Entrada (leitura) | relatorio `previsao-canal-v1` (card W9-E03-T01): `previsao_emitida`, `previsoes[]` (`organization_id`, `canal_previsto`, `amostra_do_canal`, `canais_bloqueados`), `endpoint_principal` |
| Entrada (leitura) | relatorio `melhor-horario-v1` (card W9-E04-T01): `melhor_janela` (`dia`, `faixa`), `melhor_janela_motivo`, `fuso.offset_utc`, `grade` (dias e faixas com hora de inicio) |
| Decisao | a **politica declarada** em `hermes/agentes/analytics/nutricao-automatica-v1.json` (cadencia de 4 passos, condicoes de parada, ordem da fila) |
| Saida | relatorio JSON + HTML auto-contido com a **fila de toques** (`due_at_utc` calculado) e o resumo por organizacao |
| Proibido | banco (nao abre porta), escrita, envio, `recommendations`, `human_approvals`, `outbox_events`, Odoo, cron, agendamento |

**Nao existe canal, janela nem horario escritos no codigo**: canal vem do relatorio do pai de canal,
janela e fuso vem do relatorio do pai de horario, cadencia vem do contrato. O componente nao tem uma
segunda regra de desempate, uma segunda leitura de `interactions` nem um segundo fuso.

## 2. A politica (4 passos, ordem = decisao)

| Passo | Rotulo | Intervalo |
|---|---|---|
| 1 | `toque_imediato` | 0 dias |
| 2 | `reforco_curto` | 4 dias |
| 3 | `reforco_medio` | 11 dias |
| 4 | `reabertura` | 25 dias |

- **Ancora**: primeira ocorrencia do `dia` da melhor janela a partir da referencia (no mesmo dia **so'**
  se a faixa ainda nao comecou), na hora de **inicio da faixa**, no fuso **declarado pelo pai do horario**.
- **`due_at`**: `ancora + intervalo_dias`, deslocado para a proxima ocorrencia do dia-alvo —
  `due_at_local` na hora de inicio da faixa e `due_at_utc = due_at_local - offset`. O toque **nunca cai
  no passado** e a fila e' **monotona** dentro de cada organizacao.
- **Ordem da fila**: `due_at_utc`, depois `canal`, depois `organization_id` — determinismo sem heuristica.
- **Condicoes de parada** (carregadas em CADA toque, checadas por quem executa): opt-out/`do_not_contact`;
  resposta positiva (handoff humano, W6-E05); resposta negativa (espera); avanco no funil ate' o endpoint
  principal do pai; `max_toques` atingido.
- **`exige_aprovacao_humana: true` em todo toque.** O plano e' um **PEDIDO**, nunca um ato.

## 3. Fail-closed: quando o plano ABSTEM

`veredito = PLANO_ABSTIDO`, `plano_emitido = false`, `fila = []` e a lista **`faltando`** quando:

- `previsao-canal-v1.pre_condicao_dados_multicanal.atendida` e' falso (base multicanal insuficiente), ou
- o relatorio do canal nao traz `previsoes` (nenhuma organizacao elegivel), ou
- `melhor-horario-v1.melhor_janela` e' `null` — com o motivo do pai viajando na lista
  (`AMOSTRA_INSUFICIENTE` / `SEM_ENVIOS`).

Relatorio do pai ausente/ilegivel, versao diferente da exigida, fuso ilegivel, dia/faixa fora da grade
do pai, contrato incoerente (aprovacao humana desligada, `max_toques` != numero de passos, intervalo que
diminui, passo fora de ordem, versao de entrada divergente, sem lacunas declaradas) ou statement de
escrita no proprio codigo **RECUSAM** com exit 3 — nao existe plano "mais ou menos".

## 4. Criterios de aceitacao

| # | Criterio | Como e' medido |
|---|---|---|
| AC1 | o plano e' emitido **somente** quando as duas camadas respondem (`previsao_emitida` e `melhor_janela`) | suite (3 itens de abstencao) + aceite P1/P2 |
| AC2 | o canal de cada toque e' o `canal_previsto` do pai (nao se remede) | suite + aceite P3 |
| AC3 | a janela de cada toque e' a `melhor_janela` do pai, no fuso do pai (nao se remede) | suite + aceite P4/P7 |
| AC4 | `due_at` = ancora + intervalo deslocado ao dia-alvo, nunca no passado, monotono | suite (conferido a mao: 08/10, 15/10, 22/10, 05/11) + aceite P6/P7 |
| AC5 | 4 toques por organizacao prevista; `max_toques` == numero de passos | suite + aceite P2 |
| AC6 | **nada envia**: todo toque com `exige_aprovacao_humana: true` e as condicoes de parada do contrato | suite + aceite P8 |
| AC7 | canal bloqueado por opt-out nunca recebe toque e a lacuna e' contada | suite + aceite P5 |
| AC8 | determinismo: mesma entrada + referencia => mesmo `hash_do_plano`; `gerado_em` fora do hash | suite + aceite H3 |
| AC9 | `prod` RECUSA exit 4 **antes** de ler contrato/relatorio; `homolog` exige `--confirmo` | suite + aceite A1..A3, J1 |
| AC10 | o componente **nao abre banco**: sem `--porta-banco` na CLI, e a auditoria de codigo reprova statement de escrita | suite (2 itens) + aceite A7 |
| AC11 | contrato/dependencia incoerentes RECUSAM (fail-closed) | suite (10 itens) |
| AC12 | saida sem PII e dashboard HTML auto-contido | suite + aceite P10/P11 |
| AC13 | leitura pura na cadeia inteira: contagem das 12 tabelas identica antes/depois | aceite I1 |

## 5. Plano de teste

1. **Suite offline** (sem banco): `python3 scripts/agentes/verificar_nutricao_automatica.py --autoteste`
   → `VERIFICADOR_NUTRICAO_AUTOMATICA_PASS (42 itens, 0 falhas)` + `AUTOTESTE OK (5/5 mutacoes)`.
   As fixtures sao montadas NA FORMA declarada pelos contratos dos pais (dias, faixas e fuso **lidos** do
   contrato do horario — nunca digitados na suite).
2. **Dentes (autoteste por mutacao)**: `sem-pre-condicao`, `sem-abstencao-de-janela`,
   `sem-deslocamento-para-o-dia-alvo`, `sem-aprovacao-humana`, `ordem-da-fila-trocada` — cada uma tem de
   reprovar **o item que nomeia**.
3. **Aceite E2E** (PostgreSQL descartavel na VPS):
   `bash scripts/agentes/teste_nutricao_automatica_aceite.sh` → `ACEITE_NUTRICAO_AUTOMATICA_001_OK`.
   A cadeia medida e' a real: `previsao_canal.py` e `melhor_horario.py` medem a MESMA base semeada e o
   nurture deriva o plano a partir dos DOIS relatorios que eles produzem (7 organizacoes previstas →
   28 toques), com regressao das duas suites offline dos pais, abstencao medida, reproducibilidade e
   contagem das 12 tabelas identica antes/depois.
4. **Portao de estrutura**: `bash scripts/verificar_estrutura.sh` → `PASS`.

## 6. Rollback

- **Desfazer o card**: reverter o commit — arquivos novos (`nutricao_automatica.py`,
  `nutricao-automatica-v1.json`, `verificar_nutricao_automatica.py`,
  `teste_nutricao_automatica_aceite.sh`, `docs/architecture/nutricao-automatica-v1.md`,
  `docs/runbooks/nutricao-automatica.md`), uma secao em `criterios-de-aceitacao.md`,
  `registro-de-execucoes.md`, `CHANGELOG.md` e o bloco do portao de estrutura.
- **Nada a desfazer no banco**: ZERO migration, ZERO escrita, ZERO tabela, ZERO cron, ZERO credencial.
  O container de aceite e' descartavel e sai no `trap`.
- O plano nao se executa sozinho: nao ha toque pendente para cancelar — quem executa e' o caminho de
  outbound com aprovacao humana, que nunca recebeu nada deste card automaticamente.

## 7. Risco

**Baixo-medio.** O componente nao abre banco e nao escreve nada; o aceite mede que ele nao tem sequer
porta de banco. O risco real e' de **politica** (a cadencia 0-4-11-25 dias e as condicoes de parada sao
DECLARADAS, nao medidas — nao ha teste A/B nesta v1) e de **interpretacao** (o plano e' pedido, nao ato:
por isso `exige_aprovacao_humana` e' obrigatorio no contrato e o `prod` e' recusado). Ampliar escopo —
materializar toques, agendar, executar, avaliar estagio por toque — exige contrato novo + aprovacao
humana registrada (doc 12 §10).

## 8. Fora do card (declarado)

Execucao e aprovacao dos toques (W6 outbound + Human Approval) · materializacao em tabela/cron/outbox ·
avaliacao do estagio do funil por toque na derivacao · deduplicacao entre rodadas (historico de toques
pedidos) · segmentacao por setor/porte/safra · fuso do destinatario e feriados · nova rodada de calibracao
da cadencia com dado observado.
