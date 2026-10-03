# Atribuicao de lead do Google — como se roda (TRE-W7-E03-T01)

Componente: `hermes/inbound/google/atribuicao_google.py` (`google-lead-attribution-v1`).
Contrato: `hermes/inbound/google/atribuicao-google-v1.json`. Exemplos: `hermes/inbound/google/exemplos/`.

## 1. Uso (dev)

```bash
# configuracao efetiva (token mascarado; nao conecta)
python3 hermes/inbound/google/atribuicao_google.py --planejar

# contrato + guardas + auditoria da propria fonte
python3 hermes/inbound/google/atribuicao_google.py --conferir

# decide a atribuicao SEM gravar (DRY_RUN)
python3 hermes/inbound/google/atribuicao_google.py --atribuir \
    --entrada hermes/inbound/google/exemplos/lead-google-ads.json

# decide e grava (exige --confirmo; dev exige porta de banco LOCAL)
python3 hermes/inbound/google/atribuicao_google.py --ingerir --confirmo \
    --porta-banco "docker exec -i pg-google-acc psql -U sales_ai -d sales_intelligence" \
    --porta-ads "http://127.0.0.1:8899" \
    --entrada hermes/inbound/google/exemplos/lead-site-gclid.json

# desfaz (append de trilha DESFEITO; nao apaga nada)
python3 hermes/inbound/google/atribuicao_google.py --desfazer \
    "google-lead:site_utm:site-4411" --confirmo \
    --porta-banco "docker exec -i pg-google-acc psql -U sales_ai -d sales_intelligence"
```

Exit: `0` OK/DRY_RUN/replay · `1` falha de execucao · `2` uso · `3` recusa de guarda/contrato/payload ·
`4` recusa de producao · `5` segredo vazado.

## 2. Porta do `gclid` (Ads API) — o que e' e o que nao e'

`TRE_GOOGLE_ADS_PORTA` (ou `--porta-ads`) aponta para a Ads API declarada: `GET {porta}/gclid/<gclid>`
devolve `{"status": "RESOLVIDO|NAO_ENCONTRADO", "campanha_id", ...}`. Em **dev** a porta tem de ser
loopback e o provedor e' o stub `scripts/inbound/stub-google-ads-dev.py`. A Ads API **real** exige
developer token/OAuth — fica para homolog, com aprovacao registrada (lacuna declarada no contrato).

Fail-closed: porta ausente, fora do loopback em dev, timeout, HTTP != 200 ou JSON invalido → a resolucao
**nao** e' usada (o veredito cai nas regras seguintes; sem alternativa, `NAO_ATRIBUIDO`).

## 3. Testes

```bash
# suíte offline (28 itens) — nao precisa de banco nem de rede
python3 scripts/inbound/verificar_atribuicao_google.py

# autoteste por mutacao (8/8 dentes: cada mutacao reprova o item ESPERADO)
python3 scripts/inbound/verificar_atribuicao_google.py --autoteste

# aceite E2E na VPS do ambiente (ADR-0008: e' la' que vive o Docker do TRE)
bash scripts/inbound/aceite-atribuicao-google.sh                 # 35 itens
bash scripts/inbound/aceite-atribuicao-google.sh --prova-de-dente # 38 itens (3 dentes)
bash scripts/inbound/aceite-atribuicao-google.sh --manter         # deixa trio de pe para investigar
```

O aceite **ABORTA** se o container `pg-google-acc` ou a porta `8899` ja' existirem (nao toca em
`pg-sales-dev`, `pg-odoo-dev`, `odoo-dev`, `proxy-dev`) e limpa o proprio trio ao sair.

## 4. Evidencia da execucao (03/10/2026, VPS Contabo vmi3619453)

| artefato | resultado | sha256 |
|---|---|---|
| `aceite-atribuicao-google-35ok.out` | `ACEITE_GOOGLE_LEADS_001_OK` — 35 itens / 0 falhas, exit 0 | `af61a07b2824ed93c298dc0cee38e1bbef01f7bcbd7e86f2eb572d261a322009` |
| `aceite-atribuicao-google-dentes.out` | `ACEITE_GOOGLE_LEADS_001_OK` — 38 itens / 0 falhas, **3/3 dentes** | `547b268b763ad9f646a89d0b631630e18c2aff491a55eafe4d3e9790db73f2d0` |

Dentes medidos: confianca do formulario degradada, guarda de producao desligada e INSERT fora do limite
— cada um reprovando o item que nomeia (nao o aceite inteiro).

## 5. Achados das rodadas (medidos, nao presumidos)

1. **Exemplo e aceite discordavam no `gclid`** (rodada 1): o aceite media um `gclid` que o exemplo nao
   carregava e o cenario 2 caiu em `GCLID_NAO_RESOLVIDO` — defeito do instrumento, corrigido alinhando
   `GCLID_BOM` ao exemplo.
2. **Snapshot das 12 tabelas lido como dicionario** (rodada 1): `/snapshot` devolve uma LISTA de
   `{table_name, linhas}`; lido errado, o item 11.1 ficou **cego** (nao via violacao). Corrigido para
   comparar por tabela — e o controle do item passou a medir de verdade (`organizations`/`contacts`
   intactos, `interactions`/`sync_events` como unicas tabelas que mudam).
3. **Ancora de mutacao no arquivo errado**: dois dentes mutavam o componente um campo que so' existe no
   contrato; a mutacao "nao aplicada" foi tratada como FALHOU (nao como OK silencioso).
4. **Buraco na auditoria de fonte herdada do W6-E05**: `"DE" + "LETE FROM"` avalia para `DELETEFROM`
   (sem espaco) e **nunca** casaria `DELETE FROM`. Aqui o padrao foi montado com o espaco separado
   (`"DE" + "LETE" + " FROM"`) e a suite offline prova os dois lados (limpo passa, mutado reprova).
   O mesmo padrao existe no componente do W6-E05 — registrado como achado para quem fechar aquele card.
5. A auditoria de docstring do W6-E05 (ligar/desligar por linha que comeca com aspas) desalinha com
   duas docstrings de uma linha seguidas; aqui a faixa de docstring vem do `ast` (parser de verdade).
