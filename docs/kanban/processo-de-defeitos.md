# Processo de defeitos

> **Regra (Anderson, 29/09/2026):** *"sempre que pegar um defeito crie um card do tipo defeito filho do
> card que o originou, então bloqueie o card pai, resolva o defeito, e só então desbloqueie o card pai e
> volte a trabalhar nele, assim podemos depois reavaliar o nosso processo, e gerar aprendizados a partir
> dos defeitos encontrados e corrigidos."*

O motivo é o registro: defeito corrigido e não registrado é aprendizado perdido. O card de defeito existe
para que, ao fechar a onda, dê para olhar os defeitos e responder *onde o processo está fraco* — não para
burocratizar a correção.

## Semântica do board (medida em 29/09/2026, com prova)

Antes de escrever o processo, medimos o que o board realmente faz — porque a intuição estava errada:

| Regra medida | Prova |
|---|---|
| **Filho só é concluído depois de o pai estar `done`.** | `complete <filho>` → `cannot complete: unsatisfied parent dependencies: <pai> (todo)` |
| **`block` só aceita card em `ready`/`running`/`review`.** Card em `todo`/`triage` → `cannot block`. | `block t_6ffe49f6 --kind dependency` → `cannot block t_6ffe49f6` |
| **`complete` só aceita `running`/`ready`/`blocked`/`review`.** De `todo` → `unknown id or terminal state`. | tentativa de fechar card em `todo` |
| **`create --json` devolve o card na raiz; `show --json` devolve sob `task`.** | formato da saída dos dois comandos |
| **Descartar card é `archive`** (não existe delete). | `archive` usado nos cards de teste |

**Consequência direta:** defeito criado como *filho* do card de origem fica **congelado** enquanto a origem
estiver aberta — o gate só libera o filho depois que o pai conclui. Ou seja, na direção literal "defeito
filho" seria impossível corrigir o defeito e fechar o card durante a correção. Por isso o vínculo é feito
na direção que o board respeita:

> **O defeito é PRÉ-REQUISITO do card que o originou** (`link <defeito> <origem>`). A origem não é
> reivindicada nem concluída enquanto o defeito existir — é o próprio board segurando o card, não um
> combinado verbal. A rastreabilidade fica no corpo do defeito (`ORIGEM: card X`) e no vínculo visível nos
> dois cards.

Se a origem já estiver `done` (registro retroativo, abaixo), o vínculo é o mesmo e não muda nada: card
concluído continua concluído.

## O ciclo, em cinco passos

| # | Passo | Comando | O que se espera |
|---|---|---|---|
| 1 | Abrir o card de defeito | `scripts/kanban/abrir-defeito.sh <origem> "<titulo>" <corpo.md>` | defeito em `todo`, **ligado como pré-requisito da origem** |
| 2 | Registrar sintoma, causa raiz e correção prevista | (o script cria com o corpo) | — |
| 3 | Bloquear a origem explicitamente, quando o estado permite | idem (passo 3 do script) | origem `blocked` se estava `ready`/`running`/`review`; se estava `todo`, o gate já segura |
| 4 | Resolver e **provar** o defeito | teste/verificador que reprovava antes e passa depois | defeito fechável de `ready`/`running` |
| 5 | Fechar o defeito **com a evidência** e liberar a origem | `scripts/kanban/fechar-defeito.sh <defeito> <evidencia.txt>` | defeito `done`; origem de volta a `ready` |

Ordem invertida é violação: fechar a origem antes de o defeito estar resolvido derruba o propósito. E o
próprio board recusa essa inversão — o gate de dependência devolve *"unsatisfied parent dependencies"*.

## Corpo padrão do card de defeito

```markdown
**Card <ID do defeito> — DEFEITO** (originado pelo card <ID da origem>)

ORIGEM: card <origem> — o que eu estava fazendo quando o defeito apareceu
SINTOMA: o que se observa de fora (mensagem de erro, número errado, aceite falso)
CAUSA RAIZ: por que aconteceu — não o sintoma repetido
CORREÇÃO: o que mudou (arquivo, commit)
EVIDÊNCIA: como ficou provado (teste/verificador, reprovava antes e passa depois)
SEVERIDADE: alta | média | baixa
DETECTADO POR: teste | verificador | autoteste | revisão | uso real
APRENDIZADO: a lição generalizável que deve virar regra, teste ou skill
```

## Severidade

- **alta** — produziu ou poderia produzir aceite falso, dado errado, perda de dado ou conclusão inválida.
  Ex.: contrato marcado como "congelado e versionado" sem estar versionado; script que liga o destino
  externo mesmo com a prova reprovada.
- **média** — quebra o fluxo, obriga retrabalho ou faz um artefato registrar informação errada.
  Ex.: manifesto de backup dizendo "pendente" depois de envio bem-sucedido.
- **baixa** — ruído, texto errado, erro de digitação; não altera conclusão.

Severidade entra no corpo e a **detecção** entra junto: defeito achado por autoteste vale mais que defeito
achado por sorte — e é isso que orienta onde investir em verificação.

## Registro retroativo (defeito já corrigido)

Quando o defeito é encontrado e corrigido **no mesmo card**, antes de existir o card de defeito, o registro
vem depois: abre-se o card filho do mesmo jeito, fecha-se com a evidência da correção, e o bloqueio
explícito não se aplica — não há trabalho em voo a interromper. O que se preserva é o essencial: defeito,
causa raiz e aprendizado no board, ligados ao card que os originou.

## Revisão no fechamento da onda

Ao fechar cada onda: `scripts/kanban/listar-defeitos.sh <onda>` e responder três perguntas —

1. **Onde o processo falhou** (não a pessoa): faltou teste, faltou verificação, faltou critério de aceite?
2. **Que verificação previne a repetição** — e ela foi criada?
3. **Que regra sai daqui** para o processo, o `HERMES_BOOTSTRP_PROMPT.md` ou uma skill?

A resposta vira uma seção `Aprendizados` no relatório de fechamento da onda — e, quando for regra, entra no
processo e não fica só no texto.

## Ferramentas

- `scripts/kanban/abrir-defeito.sh <origem> "<titulo>" <arquivo-do-corpo> [--retroativo]` — cria o defeito,
  liga-o como pré-requisito da origem e bloqueia a origem quando o estado permite. Imprime o ID do defeito.
- `scripts/kanban/fechar-defeito.sh <defeito> [arquivo-do-resultado]` — fecha o defeito com a evidência e
  libera as origens ligadas. Recusa fechar sem evidência (a menos de `--sem-evidencia`, consciente).
- `scripts/kanban/listar-defeitos.sh [onda|tudo]` — lista os defeitos com severidade e detecção, para a
  revisão de fim de onda.

Os scripts usam o CLI do Hermes (`hermes kanban`), então valem as mesmas regras de transição do board.

## Aprendizados desta primeira aplicação

A própria regra, ao ser posta em prática, encontrou dois defeitos **na sua implementação** — e os dois
foram corrigidos antes de o processo entrar em vigor:

1. **Defeito criado como filho ficava congelado** (o gate impede fechar o filho com o pai aberto): corrigido
   invertendo o vínculo para pré-requisito, direção em que o gate trabalha a favor da regra.
2. **Bloqueio explícito em card `todo` é recusado pelo CLI**: corrigido — quando o card não é bloqueável, o
   script registra que o gate de dependência é quem segura, em vez de inventar estado.

Lição: processo novo se prova executando, não descrevendo. Os dois defeitos apareceram nos primeiros
minutos de uso real, e nenhum apareceria na leitura da regra.
