# Runbook — memória comercial (`memoria-comercial-v1`, card TRE-W9-E06-T01)

## O que este runbook cobre

Indexar o corpus comercial no Qdrant de **dev** (memória derivada), medir a pré-condição, consultar por
semelhança, reindexar e desfazer. **Nada aqui se aplica a produção**: o componente recusa `prod` por
desenho (ADR-005) e a memória é reconstruível a partir do PostgreSQL.

## Pré-requisitos

- PostgreSQL canônico (`sales_intelligence`) acessível por **porta de banco local** — o mesmo padrão dos
  irmãos: `docker exec -i pg-<nome> psql -U sales_ai -d sales_intelligence`.
- Qdrant em host local (dev: container descartável `qdrant/qdrant:v1.12.4` em `127.0.0.1:6333`).
- `python3` (só biblioteca padrão: o componente fala REST por `urllib`).

## 1. Ver o plano sem tocar em banco nem Qdrant

```bash
python3 hermes/memoria/memoria_comercial.py --planejar --contrato hermes/memoria/memoria-comercial-v1.json
```

Declara coleção, provedor/dimensão do embedding, tipos de documento, payload fechado, mínimo e guardas.
Exit 0. É o comando para descobrir o que o contrato manda antes de qualquer escrita.

## 2. Medir a pré-condição e indexar (dev)

```bash
python3 hermes/memoria/memoria_comercial.py --ambiente dev \
  --qdrant-url http://127.0.0.1:6333 \
  --porta-banco "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --saida /tmp/memoria-comercial
```

- Saída: `/tmp/memoria-comercial/memoria-comercial.json` + `.html` (auto-contido), e o JSON também no stdout.
- **Pré-condição não atendida** (corpus abaixo do piso): `pre_condicao.atendida=false`, `faltando` nomeado
  e `indexacao.memoria_publicada=false` — o comando termina **exit 0** e **nada** é escrito no Qdrant.
- Com a pré-condição atendida: coleção criada com a dimensão do contrato e pontos enviados; `pontos_antes`
  / `pontos_depois` / `enviados` mostram o efeito real.

## 3. Consultar a memória

```bash
python3 hermes/memoria/memoria_comercial.py --ambiente dev --qdrant-url http://127.0.0.1:6333 \
  --buscar "objecao preco orcamento do projeto" --limite 5
python3 hermes/memoria/memoria_comercial.py --ambiente dev --qdrant-url http://127.0.0.1:6333 \
  --buscar "conciliacao manual do time financeiro" --filtro-tipo DOR
```

- Resultado vazio = **não há correspondência acima do piso** — não insista baixando o piso por conta
  própria: o piso é declarado no contrato e protege contra ruído de colisão do provedor local.
- Coleção ausente → `COLECAO_AUSENTE` (exit 3): indexe antes.

## 4. Reindexar do zero (rollback da memória)

```bash
python3 hermes/memoria/memoria_comercial.py --ambiente dev --qdrant-url http://127.0.0.1:6333 \
  --porta-banco "docker exec -i pg-sales-dev psql -U sales_ai -d sales_intelligence" \
  --recriar --confirmo
```

`--recriar` sem `--confirmo` recusa (exit 3). Como a memória é derivada, este é o **rollback normal**:
reindexa a partir da fonte canônica e volta ao mesmo estado (medido no aceite: 12 pontos de volta).

## 5. Rollback total / desmontar o ambiente

- Remover a coleção: `curl -X DELETE http://127.0.0.1:6333/collections/memoria_comercial_v1`
- Remover o Qdrant de dev do aceite: `docker rm -f qd-memoria-comercial`
- Reverter o commit do card (arquivos novos, sem DDL e sem migration): nada em homolog/produção depende
  da coleção e o PostgreSQL **não** foi tocado (leitura pura).

## 6. Diagnóstico rápido

| sintoma | causa provável | ação |
|---|---|---|
| `ESCRITA_NO_CODIGO` (exit 3) | SQL da receita com verbo de escrita | corrija o contrato; o componente recusa antes de conectar |
| `BANCO_NAO_E_DEV` (exit 3) | porta de banco remota em dev | use `docker exec -i pg-<nome> psql …` local |
| `QDRANT_NAO_E_DEV` (exit 3) | `--qdrant-url` remoto em dev | aponte para `127.0.0.1` |
| `DIMENSAO_DIVERGENTE` (exit 3) | coleção de outra dimensão | `--recriar --confirmo` (nova dimensão exige versão nova do contrato) |
| `PII_SUSPEITA` no relatório | documento com e-mail/telefone/CNPJ/CPF | é o comportamento correto: a origem fica de fora e o dado precisa ser tratado na fonte |
| `FONTE_JSON_ILEGIVEL` (exit 3) | resposta do psql fora do formato | confira a porta de banco; a leitura do array multi-linha do `psql` é tratada pelo componente |

## 7. Aceite e suíte (reproduzir a prova)

```bash
python3 scripts/agentes/verificar_memoria_comercial.py --autoteste
bash scripts/agentes/teste_memoria_comercial_aceite.sh          # na VPS de dev, na raiz do repo
```

O aceite cria **containers descartáveis** (`pg-memoria-comercial`, `qd-memoria-comercial`), mede 66 itens
e os remove no fim (`--manter` preserva para inspeção). Nenhum container do ambiente é tocado.
