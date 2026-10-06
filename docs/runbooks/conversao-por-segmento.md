# Runbook — Conversão por segmento (`conversao-segmento-v1`)

Card `TRE-W8-E02-T01` (W8 / Analytics). Depende do funil (`funil-v1`, card W8-E01-T01) — o mesmo código.

## Quando usar

- "Qual faixa de funcionários converte mais que a base?" (eixo `faixa_funcionarios`)
- "Os tiers altos convertem de verdade?" (eixo `tier_prioridade`)
- Auditoria de cobertura: quanto da base **não** tem segmento atribuído.

## ACCEPTANCE

O card só está entregue quando, na mesma base:

1. `python3 scripts/agentes/verificar_conversao_segmento.py --autoteste` termina em
   `VERIFICADOR_CONVERSAO_PASS` com `34 itens, 0 falhas` e `AUTOTESTE 12/12 mutacoes detectadas`;
2. `bash scripts/agentes/teste_conversao_segmento_aceite.sh` termina em
   `ACEITE_CONVERSAO_SEGMENTO_OK` (**43 itens, 0 falhas**) em PostgreSQL descartável;
3. os números do recorte batem com a conta à mão (buckets por eixo, won por segmento, taxa, índice,
   cobertura) e **o recorte da base inteira é idêntico ao relatório do `funil.py`** na mesma base
   (estágios, alcance, evidência própria, conversões, won/lost/nurture e lacunas);
4. `--conferir` valida os três contratos (recorte × funil × contrato de dados) e `bash -n` do aceite passa.

## TEST

```bash
# suíte offline (sem banco) — inclui os 12 dentes por mutação
python3 scripts/agentes/verificar_conversao_segmento.py --autoteste

# aceite de ponta (cria e remove o container descartável pg-analytics-seg-acc)
bash scripts/agentes/teste_conversao_segmento_aceite.sh          # na VPS do ambiente
bash scripts/agentes/teste_conversao_segmento_aceite.sh --manter # deixa o container para inspeção
```

Bordas que o aceite mede e que **precisam** continuar medidas: faixa quase idêntica ao vocabulário não
vira segmento; `SEM_DADO` ≠ `FORA_DO_VOCABULARIO`; tier vem da pontuação **vigente**; score sem versão
não qualifica; bucket que não cobre a base é recusa; leitura pura (snapshot das 12 tabelas + transação
READ ONLY recusando escrita); determinismo; saída sem PII.

## ROLLBACK

O componente **não escreve nada** — não há migração, coluna, tabela nem evento a desfazer.

- Reverter a entrega = reverter os arquivos (`hermes/agentes/analytics/conversao_segmento.py`,
  `conversao-segmento-v1.json`, o verificador, o aceite e os dois documentos) no commit do card; a base
  permanece intacta.
- Se o relatório for considerado errado em produção, o efeito é um **artefato gerado** (JSON/HTML): basta
  parar de consumi-lo e reexecutar com o contrato anterior (o `sha256` do contrato viaja no relatório,
  então a procedência é auditável).
- O aceite cria e remove seu próprio container (`pg-analytics-seg-acc`); interrompê-lo no meio deixa lixo
  descartável apenas com `--manter` — remover com `docker rm -f pg-analytics-seg-acc`.

## RISK

| Risco | Mitigação medida |
|---|---|
| Segundo funil (divergência entre `funil.py` e o recorte) | o recorte **importa** o funil; o aceite exige igualdade item a item na mesma base |
| Segmento inventado por semelhança (a falha D04/D06/D07 do roteador JEV) | vocabulário fechado vindo do contrato de dados + igualdade exata; dente `150_299X` no aceite e mutação `d2` na suíte |
| Leitura "vencedora" sobre amostra minúscula | `amostra_pequena` marcado abaixo de `amostra_minima` e cobertura declarada por eixo |
| Escrita acidental na base | leitura pura em dois `-c` (mecanismo do PostgreSQL) + auditoria da fonte + snapshot das 12 tabelas no aceite |
| Vazamento de dado pessoal | o recorte lê faixa de funcionários e pontuação; o aceite reprova se aparecer e-mail, telefone, nome ou UUID da base |
| Foto de hoje vendida como história | lacuna L6 declarada no relatório e no documento (o eixo não é histórico) |
| Base sem segmento atribuído passar como conclusão | soma dos buckets tem de fechar com a base (`RECORTE_NAO_FECHA_COM_A_BASE`) e a cobertura é publicada |
