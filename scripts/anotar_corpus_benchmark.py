#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Preenche lane_esperada do corpus de anotacao (card TRE-W0-E04-T03).

FERRAMENTA DE UM USO SO: rodou uma vez, em 29/09/2026, para produzir o corpus
v1.3 a partir da v1.2. Fica versionada porque a anotacao e o insumo do benchmark:
sem ela nao ha como auditar DEPOIS que nenhum campo protegido foi mexido.

Rodar de novo e um no-op que FALHA alto ("esperava 30 trocas, fiz 0"): os 30
casos ja tem rotulo, o script nao sobrescreve rotulo existente.

Executa o CONTRATO DA ANOTACAO homologado em 29/09/2026:
  * lane_esperada := lane_proposta_hermes nos 30 casos vazios;
  * os 2 casos ja rotulados (real-t_969affa7, real-t_d9cb5755) NAO sao tocados;
  * proveniencia no campo nota (texto prescrito no card);
  * lane_proposta_hermes, sinais, acao e justificativa NAO mudam (provado por
    comparacao profunda com o YAML de antes);
  * versao do arquivo sobe para v1.3 e nota_da_versao e reescrita.

A prova da anotacao (32/32 homologados, 0 campo protegido alterado) esta gravada na
secao 2 de `docs/validation/jev-benchmark-routing.md`. Uso:
`/opt/hermes/.venv/bin/python scripts/anotar_corpus_benchmark.py`
"""
import hashlib
import json
import pathlib
import sys

import yaml

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CORPUS = RAIZ / "hermes/jev/benchmarks/corpus-anotacao.yaml"

PROVENIENCIA = ("lane homologada por Anderson em 29/09/2026 a partir da proposta "
                "do Hermes (corpus v1.2)")
INTOCAVEIS = ("real-t_969affa7", "real-t_d9cb5755")

NOTA_DA_VERSAO = (
    "v1.3 (29/09/2026): lane_esperada preenchida nos 30 casos que estavam vazios, por homologacao "
    "EM BLOCO do Anderson em 29/09/2026 (Telegram, via card TRE-W0-E04-T03) sobre as propostas do "
    "corpus v1.2; a proveniencia de cada caso esta no campo nota. Os 2 casos ja rotulados "
    "individualmente em 842d62e (cards 3 e 4 = high) seguem como estavam. Limite registrado com "
    "todas as letras: como os 30 rotulos vieram da propria proposta do Hermes, nesses casos "
    "lane_esperada == lane_proposta_hermes; a acuracia medida contra eles NAO e prova de qualidade "
    "do classificador, e sim linha de base de regressao (a leitura individual por caso segue "
    "pendente de revisao do Anderson). Nenhum caso ficou valido sem homologacao: os 32 tem "
    "lane_esperada homologada (2 individual, 30 em bloco) e nenhum segue pendente."
)


def _escalar(chave: str, texto: str) -> str:
    """Linha YAML de um escalar garantidamente valida (quoting pelo proprio PyYAML)."""
    return yaml.safe_dump({chave: texto}, allow_unicode=True, default_flow_style=False,
                          width=10 ** 6).strip()


def main() -> int:
    antes_texto = CORPUS.read_text(encoding="utf-8")
    antes = yaml.safe_load(antes_texto)
    hash_antes = hashlib.sha256(antes_texto.encode("utf-8")).hexdigest()

    # Guarda de re-execucao: sem caso vazio NAO ha o que anotar — e o script para
    # ANTES de escrever qualquer coisa (nunca reescreve rotulo ja homologado).
    vazios_de_entrada = [c["id"] for c in antes["casos"]
                         if not str(c.get("lane_esperada") or "").strip()]
    if not vazios_de_entrada:
        print("NADA A FAZER: nenhum caso com lane_esperada vazio — o corpus ja esta anotado "
              f"(versao={antes.get('versao')}). Arquivo NAO foi tocado.")
        print(f"sha256={hash_antes}")
        return 1

    linhas = antes_texto.splitlines()
    if not linhas or not linhas[-1].strip():
        pass
    # idx da linha onde comeca nota_da_versao (o bloco final e reescrito)
    corte = next(i for i, l in enumerate(linhas) if l.startswith("nota_da_versao:"))
    corpo = linhas[:corte]

    atual_id, atual_proposta = None, None
    trocados = []
    saida = []
    for linha in corpo:
        if linha.startswith("- id:"):
            atual_id = linha.split(":", 1)[1].strip()
        if linha.startswith("  lane_proposta_hermes:"):
            atual_proposta = linha.split(":", 1)[1].strip()
        if linha.startswith("  lane_esperada:") and linha.split(":", 1)[1].strip() in ("''", '""', ""):
            if atual_id in INTOCAVEIS:
                saida.append(linha)
                continue
            if not atual_proposta:
                raise SystemExit(f"FALHOU: caso {atual_id} sem lane_proposta_hermes")
            saida.append(f"  lane_esperada: {atual_proposta}")
            trocados.append((atual_id, atual_proposta))
            continue
        if linha.startswith("  nota:") and linha.split(":", 1)[1].strip() in ("''", '""', ""):
            if atual_id in INTOCAVEIS:
                saida.append(linha)
                continue
            saida.append("  " + _escalar("nota", PROVENIENCIA))
            continue
        if linha.startswith("versao: corpus-anotacao-v1.2"):
            saida.append("versao: corpus-anotacao-v1.3")
            continue
        saida.append(linha)

    novo_texto = "\n".join(saida + [_escalar("nota_da_versao", NOTA_DA_VERSAO)]) + "\n"
    CORPUS.write_text(novo_texto, encoding="utf-8")

    # ---------------------------------------------------------------- prova
    depois = yaml.safe_load(CORPUS.read_text(encoding="utf-8"))
    casos_a = antes["casos"]
    casos_d = depois["casos"]
    falhas = []
    if len(casos_a) != len(casos_d):
        falhas.append("numero de casos mudou")
    if len(casos_d) != 32:
        falhas.append(f"esperava 32 casos, achei {len(casos_d)}")
    if len(trocados) != 30:
        falhas.append(f"esperava 30 trocas, fiz {len(trocados)}")
    vazios = [c["id"] for c in casos_d if not str(c.get("lane_esperada") or "").strip()]
    if vazios:
        falhas.append(f"caso sem lane_esperada (nao homologado): {vazios}")
    for a, d in zip(casos_a, casos_d):
        if a["id"] != d["id"]:
            falhas.append(f"ordem dos casos mudou: {a['id']} -> {d['id']}")
            continue
        esperada_a = str(a.get("lane_esperada") or "").strip()
        esperada_d = str(d.get("lane_esperada") or "").strip()
        if a["id"] in INTOCAVEIS:
            if esperada_a != esperada_d:
                falhas.append(f"{a['id']} (intocavel) mudou de lane_esperada")
            if a.get("nota") != d.get("nota"):
                falhas.append(f"{a['id']} (intocavel) mudou de nota")
        else:
            if esperada_a:
                falhas.append(f"{a['id']}: ja tinha lane_esperada e foi trocado")
            if esperada_d != str(d["lane_proposta_hermes"]).strip():
                falhas.append(f"{a['id']}: lane_esperada != lane_proposta_hermes")
            if PROVENIENCIA not in str(d.get("nota") or ""):
                falhas.append(f"{a['id']}: nota sem a proveniencia prescrita")
        # campos protegidos: identicos antes e depois
        for campo in ("lane_proposta_hermes", "sinais", "acao", "justificativa", "titulo", "origem"):
            if a.get(campo) != d.get(campo):
                falhas.append(f"{a['id']}: campo protegido '{campo}' mudou")

    relatorio = {
        "corpus": str(CORPUS.relative_to(RAIZ)),
        "sha256_antes": hash_antes,
        "sha256_depois": hashlib.sha256(CORPUS.read_text(encoding='utf-8').encode()).hexdigest(),
        "casos": len(casos_d),
        "rotulados_agora": len(trocados),
        "intocados": list(INTOCAVEIS),
        "por_lane": {},
        "falhas": falhas,
    }
    for c in casos_d:
        lane = str(c["lane_esperada"])
        relatorio["por_lane"][lane] = relatorio["por_lane"].get(lane, 0) + 1
    print(json.dumps(relatorio, ensure_ascii=False, indent=2))
    if falhas:
        print("RESULTADO: FALHOU")
        return 1
    print("RESULTADO: OK — 32/32 casos com lane_esperada homologada, 0 campo protegido alterado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
