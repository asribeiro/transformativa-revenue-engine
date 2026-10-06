# Memória comercial em Qdrant — `memoria-comercial-v1` (card TRE-W9-E06-T01)

Onda W9 (Inteligência Avançada) · épico E06 · prioridade P3. Pré-condição do card: **corpus comercial
estável** (condição do mundo real, não card — doc 11).

## 1. O que é, e o que não é

O componente `hermes/memoria/memoria_comercial.py` (contrato `hermes/memoria/memoria-comercial-v1.json`)
**mede** a estabilidade do corpus comercial na base canônica `sales_intelligence` em **leitura pura** e,
somente quando a pré-condição é atendida, **deriva** a memória semântica — documentos do corpus com vetor —
para uma coleção do Qdrant, e responde à **busca por semelhança** com filtro declarado.

O Qdrant, aqui, é **memória derivada**: a fonte de verdade continua sendo o PostgreSQL. Perder a coleção
custa uma reindexação, nunca um dado. Nada é escrito na fonte (sessão `READ ONLY` + auditoria que reprova
verbo de escrita antes de conectar).

O que o componente **não** faz: não envia nada, não recomenda ação, não decide canal nem momento, não lê
`contacts`, não guarda PII e não publica em produção (ADR-005).

## 2. Pré-condição como mecanismo (fail-closed)

`pre_condicao.nome = "corpus comercial estavel"`. Piso declarado no contrato (`minimos`): 10 documentos
indexáveis, 3 tipos com base e mínimo por tipo (MENSAGEM 3, OBJECAO 2, DOR 3, CONTEXTO 2).

Abaixo do piso o relatório **sai** com `pre_condicao.atendida=false`, a lista `faltando` nomeada e
`indexacao.memoria_publicada=false` — e **nada é escrito no Qdrant** (nem a coleção é criada). Medido no
aceite com uma base magra (1 documento): `faltando` com 6 itens, 0 coleções no Qdrant.

## 3. Corpus: receitas declaradas, nunca literal no código

Cada tipo de documento é uma **receita** do contrato (tabela, coluna de id, coluna de organização, coluna
de canal, coluna de data, colunas de texto e filtro). O código monta o SQL a partir dela e o submete à
auditoria antes de qualquer conexão; nome de tabela no componente é defeito (há item de suíte para isso —
o prefixo do schema aparece uma única vez, como guarda de validação do contrato).

| tipo | origem | texto |
|---|---|---|
| `MENSAGEM` | `interactions` (OUTBOUND) | `subject` · `content_summary` |
| `OBJECAO` | `interactions` (INBOUND com `response_category`) | `response_category` · `content_summary` · `subject` |
| `DOR` | `pain_hypotheses` | `pain_category` · `pain_statement` · `evidence_summary` |
| `CONTEXTO` | `recommendations` | `action` · `description` · `rationale` |

Documento sem texto, sem id de origem, ou cujo texto casa com padrão proibido do contrato, vira **lacuna
nomeada** (`DOCUMENTO_SEM_TEXTO`, `ORIGEM_SEM_ID`, `PII_SUSPEITA`) e **não** entra na coleção.

## 4. PII é lacuna, não memória

`privacidade.padroes_proibidos` declara regex de e-mail, telefone (com `+55` ou parênteses), CNPJ e CPF.
O texto que casa vai para lacuna **com a origem** e fica fora da coleção. O `payload` é **fechado** — nove
campos declarados em `payload_fechado`, nenhum deles de contato — e o componente lança
`PAYLOAD_FORA_DO_CONTRATO` se algum campo não declarado aparecer. A unidade da memória é a organização
(UUID canônico), não o contato.

## 5. Idempotência e atualização no lugar

O id do ponto é **UUIDv5** sobre `(coleção, tabela de origem, id de origem)`. Reindexar não duplica:
contagem e `hash_do_relatorio` estáveis entre rodadas (medido: 12 → 12). Conteúdo alterado na origem
**atualiza o mesmo ponto** (o `conteudo_sha256` do payload muda, a contagem não) — medido com `UPDATE` na
base descartável do aceite.

## 6. Busca

Ordenada por `score desc` com desempate por `id asc`, filtro declarado (`tipo`, `organization_id`), limite
padrão 5 e máximo 20, e **piso de score** (`busca.score_minimo`). Consulta sem correspondência devolve
lista **vazia**, não “o menos pior”.

O provedor de embedding da v1 é **local e declarado** (`local-deterministico-v1`): hashing de tokens
(blake2b com semente fixa) em vetor de 256 posições com sinal, normalizado em L2 — sem rede, sem
dependência e determinístico. É **lexical**: não há alegação de qualidade semântica (sinonímia/paráfrase)
e há **colisão de hash** possível. Medição do aceite: com 64 posições o ruído de colisão chegava a 0,19
numa consulta sem correspondência; a 256 posições o ruído foi 0,00 e a sobreposição real deu 0,42 e 0,49.
O piso 0,20 troca recall por precisão e isso é declarado (lacuna L2).

Nome de modelo externo e preço **não** moram no contrato (mesma regra do catálogo de modelos do JEV):
trocar de provedor ou de dimensão exige **versão nova** do contrato e reindexação completa.

## 7. Guardas de ambiente (ADR-005)

| ambiente | comportamento |
|---|---|
| `dev` | exige Qdrant em host local (`127.0.0.1`, `localhost`, `[::1]`); host remoto → `QDRANT_NAO_E_DEV` (exit 3) |
| `homolog` | exige `--confirmo` |
| `prod` | **RECUSA por desenho** (exit 4) |

Além disso: `--recriar` exige `--confirmo`; coleção existente com dimensão diferente da declarada →
`DIMENSAO_DIVERGENTE` (exit 3) **sem escrever**; uso errado = exit 2; recusa = exit 3; produção = exit 4;
segredo vigiado na evidência = exit 5.

## 8. Camadas e limites declarados (lacunas viajam no relatório)

L1 caso/playbook vive em `recommendations`/`pain_hypotheses` (não há tabela dedicada no Data Contract V1) ·
L2 busca lexical, com colisão de hash e piso que troca recall por precisão · L3 nenhum agente consome a
memória ainda (ligar ao motor é card posterior) · L4 a janela temporal do funil não é aplicada · L5 o
volume do Qdrant não entra no backup do produto (é reconstruível) · L6 `organization_id` nulo aparece como
`null`, sem descartar o documento.

## 9. Evidência

- Suíte offline: `python3 scripts/agentes/verificar_memoria_comercial.py --autoteste` → 26 itens, 0
  falhas + 11/11 mutações detectadas (sem banco e sem Qdrant).
- Aceite de ponta na VPS de dev: `bash scripts/agentes/teste_memoria_comercial_aceite.sh` → 66 itens, 0
  falhas, com Qdrant descartável (`qdrant/qdrant:v1.12.4`) e PostgreSQL descartável (migration 0001 +
  base semeada), ambos removidos no fim.
