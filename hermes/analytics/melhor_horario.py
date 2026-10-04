#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Melhor horario de contato — `melhor-horario-v1` (card TRE-W9-E04-T01, W9 / Inteligencia Avancada).

O que FAZ (e so' isto): sobre o MESMO fato que o irmao de desempenho ja' le (interactions OUTBOUND
com `content_reference` `envio:<approval_id>:<texto_hash>` e INBOUND com `response_category`),
agrupa os ENVIOS pela JANELA em que sairam — (dia da semana x faixa horaria) no fuso declarado no
contrato — e mede o desfecho de cada janela com a MESMA regra de credito do irmao. Responde, com
amostra declarada: em que janela o contato responde mais, e qual janela nao tem base para coroar.

O que NAO faz (declarado em `hermes/analytics/melhor-horario-v1.json`):
  - NAO envia, NAO agenda, NAO aprova e NAO classifica resposta;
  - NAO escreve: a unica instrucao enviada a porta de banco e' um SELECT (guarda do irmao);
  - NAO cria uma segunda regra de atribuicao: IMPORTA `desempenho_mensagens` e usa `separar`,
    `creditar` e `medir_grupo` — atribuicao e' UMA SO' no projeto;
  - NAO negocia fuso: o offset e' declarado no contrato (nao vem do sistema nem do banco);
  - NAO preve: mede o observado. Base insuficiente -> ABSTEM (AMOSTRA_INSUFICIENTE), nunca chute;
  - prod RECUSA exit 4 (ADR-005).

Decisao de arquitetura (o ponto do card depender do W8-E04-T01): a ATRIBUICAO resposta->envio e' UMA
SO'. Duas copias da regra divergiriam em silencio; este componente importa o dono da regra.

Uso:
  python3 hermes/analytics/melhor_horario.py --ambiente dev \\
      --prefixo "docker exec -i pg-x psql -U sales_ai -d sales_intelligence" \\
      --desde 2026-09-01T00:00:00Z --ate 2026-09-30T23:59:59Z
  python3 hermes/analytics/melhor_horario.py --regras
Exit: 0 = ANALISADO/SEM_ENVIOS · 2 = uso/porta ausente · 3 = fail-closed · 4 = prod recusado.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSAO = "melhor-horario-v1"
AMBIENTES = ("dev", "homolog", "prod")
AQUI = os.path.dirname(os.path.abspath(__file__))

# Reuso deliberado do irmao W8-E04-T01: atribuicao, leitura, metricas e guardas nao se duplicam.
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)
import desempenho_mensagens as irmao  # noqa: E402

CONTRATO_IRMAO_PADRAO = "hermes/analytics/desempenho-mensagens-v1.json"
CONTRATO_DO_IRMAO_IRMAO = "hermes/agentes/respostas/ingestao-respostas-v1.json"
RE_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")


class Recusa(irmao.Recusa):
    """Mesma recusa do irmao (motivo, detalhe, exit code) — o tratamento de erro nao se duplica."""


# ------------------------------------------------------------------ contrato e grade

def carregar_contrato(contrato_path: Path, dono: dict) -> dict:
    """Le/valida o contrato DESTE card; a particao das classes de resposta e' a do dono (W8-E04-T01)."""
    try:
        contrato = json.loads(contrato_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - qualquer falha de leitura e incoerencia declarada
        raise Recusa("CONTRATO_INCOERENTE", f"{contrato_path}: {exc}") from exc
    if contrato.get("nome") != VERSAO:
        raise Recusa("CONTRATO_INCOERENTE", f"nome do contrato != {VERSAO}")

    fuso = contrato.get("fuso") or {}
    offset = fuso.get("offset_utc") or ""
    casamento = RE_OFFSET.match(offset)
    if not casamento or int(casamento.group(2)) > 14 or int(casamento.group(3)) > 59:
        raise Recusa("FUSO_INVALIDO", f"offset_utc={offset!r} nao e' +HH:MM/-HH:MM plausivel")
    sinal = -1 if casamento.group(1) == "-" else 1
    deslocamento = sinal * timedelta(hours=int(casamento.group(2)), minutes=int(casamento.group(3)))

    dias = contrato.get("dias") or []
    if len(dias) != 7 or len(set(dias)) != 7:
        raise Recusa("CONTRATO_INCOERENTE", f"dias tem de ser 7 nomes unicos, veio {dias!r}")

    faixas = contrato.get("faixas") or []
    if not faixas:
        raise Recusa("CONTRATO_INCOERENTE", "faixas ausente")
    nomes = [f.get("nome") for f in faixas]
    if any(not n for n in nomes) or len(set(nomes)) != len(nomes):
        raise Recusa("CONTRATO_INCOERENTE", f"faixa sem nome ou repetida: {nomes!r}")
    cursor = 0
    for faixa in faixas:
        de, ate = faixa.get("de"), faixa.get("ate")
        if not isinstance(de, int) or not isinstance(ate, int) or de < 0 or ate > 24 or de >= ate:
            raise Recusa("CONTRATO_INCOERENTE", f"faixa {faixa.get('nome')!r} invalida: {de}..{ate}")
        if de != cursor:
            raise Recusa("GRADE_COM_BURACO_OU_SOBREPOSICAO",
                         f"{faixa.get('nome')!r} comeca em {de} e o cursor esta' em {cursor}")
        cursor = ate
    if cursor != 24:
        raise Recusa("GRADE_COM_BURACO", f"a ultima faixa termina em {cursor}, nao em 24")

    # A particao de classes de resposta e' do DONO da regra (W8-E04-T01): nada e' redeclarado aqui.
    if not dono.get("_classe_de"):
        raise Recusa("CONTRATO_INCOERENTE", "contrato do dono sem particao de classes de resposta")
    contrato["_classe_de"] = dono["_classe_de"]
    contrato["_offset"] = deslocamento
    return contrato


def carregar_contrato_irmao(raiz: Path, contrato_irmao_path: Path, contrato_irmao_irmao: Path) -> dict:
    """Contrato do irmao (dono da atribuicao e da particao de classes)."""
    return irmao.carregar_contrato(raiz, contrato_irmao_path, contrato_irmao_irmao)


def janela_do_instante(momento: datetime, contrato: dict) -> tuple[str, str]:
    """Instante UTC do ENVIO -> (dia da semana, faixa horaria) no fuso declarado."""
    local = momento.astimezone(timezone.utc) + contrato["_offset"]
    dia = contrato["dias"][local.weekday()]
    hora = local.hour
    for faixa in contrato["faixas"]:
        if faixa["de"] <= hora < faixa["ate"]:
            return dia, faixa["nome"]
    raise Recusa("HORA_FORA_DA_GRADE", f"hora local {hora} nao caiu em nenhuma faixa")  # nao deve ocorrer


# ------------------------------------------------------------------ metricas

def melhor_de(grupos: list[dict], campos_de_desempate: list[str]) -> tuple[dict | None, str | None]:
    """Mesma regra do irmao: so' concorre celula com amostra; ordem declarada e deterministica."""
    if not grupos:
        return None, "SEM_ENVIOS"
    elegiveis = [g for g in grupos if g["amostra_suficiente"]]
    if not elegiveis:
        return None, "AMOSTRA_INSUFICIENTE"
    ordenado = sorted(
        elegiveis,
        key=lambda g: tuple([-(g["taxa_de_interesse"] or 0.0), -(g["taxa_de_resposta"] or 0.0), -g["enviadas"]]
                            + [g[campo] for campo in campos_de_desempate]),
    )
    return ordenado[0], None


def montar_relatorio(contrato: dict, ambiente: str, desde: datetime, ate: datetime,
                     limite_amostra: int, dados: dict, carimbo: bool) -> dict:
    envios, respostas = dados["envios"], dados["respostas"]
    for envio in envios:
        envio["dia"], envio["faixa"] = janela_do_instante(envio["momento"], contrato)
    creditos = irmao.creditar(envios, respostas, dados["janela"])

    celulas: dict[tuple[str, str], list[dict]] = {}
    for dia in contrato["dias"]:
        for faixa in contrato["faixas"]:
            celulas[(dia, faixa["nome"])] = []
    for envio in envios:
        celulas[(envio["dia"], envio["faixa"])].append(envio)

    por_janela = []
    for dia in contrato["dias"]:
        for faixa in contrato["faixas"]:
            chave = (dia, faixa["nome"])
            linha = {"dia": dia, "faixa": faixa["nome"]}
            linha.update(irmao.medir_grupo(celulas[chave], creditos, limite_amostra))
            por_janela.append(linha)

    por_dia = []
    for dia in contrato["dias"]:
        lista = [e for e in envios if e["dia"] == dia]
        linha = {"dia": dia}
        linha.update(irmao.medir_grupo(lista, creditos, limite_amostra))
        por_dia.append(linha)

    por_faixa = []
    for faixa in contrato["faixas"]:
        lista = [e for e in envios if e["faixa"] == faixa["nome"]]
        linha = {"faixa": faixa["nome"]}
        linha.update(irmao.medir_grupo(lista, creditos, limite_amostra))
        por_faixa.append(linha)

    melhor, motivo = melhor_de(por_janela, ["dia", "faixa"])
    melhor_dia, motivo_dia = melhor_de(por_dia, ["dia"])
    melhor_faixa, motivo_faixa = melhor_de(por_faixa, ["faixa"])
    total = irmao.medir_grupo(envios, creditos, limite_amostra)
    if not envios:
        # Base vazia e' base vazia: a grade continua inteira (49 celulas), mas o ranking ABSTEM por
        # ausencia de envios — nunca por "amostra pequena", que diria outra coisa.
        melhor = melhor_dia = melhor_faixa = None
        motivo = motivo_dia = motivo_faixa = "SEM_ENVIOS"

    relatorio = {
        "versao": VERSAO,
        "card": contrato.get("card"),
        "ambiente": ambiente,
        "veredito": "ANALISADO" if envios else "SEM_ENVIOS",
        "recorte": {
            "desde": irmao.formatar_instant(desde), "ate": irmao.formatar_instant(ate),
            "janela_dias": round(dados["janela"].total_seconds() / 86400.0, 4),
            "limite_amostra": limite_amostra,
        },
        "fuso": {"nome": contrato["fuso"]["nome"], "offset_utc": contrato["fuso"]["offset_utc"]},
        "grade": {"dias": contrato["dias"],
                  "faixas": [{"nome": f["nome"], "de": f["de"], "ate": f["ate"]} for f in contrato["faixas"]]},
        "totais": total,
        "por_janela": por_janela,
        "por_dia": por_dia,
        "por_faixa": por_faixa,
        "melhor_janela": melhor,
        "melhor_janela_motivo": motivo,
        "melhor_dia": melhor_dia,
        "melhor_dia_motivo": motivo_dia,
        "melhor_faixa": melhor_faixa,
        "melhor_faixa_motivo": motivo_faixa,
        "respostas_lidas": len(respostas),
        "excluidas_do_recorte": dados["excluidas"],
        "lacunas": contrato.get("lacunas") or [],
    }
    if carimbo:
        relatorio["gerado_em"] = irmao.formatar_instant(datetime.now(timezone.utc))
    return relatorio


def escrever_csv(caminho: Path, janelas: list[dict]) -> None:
    campos = ["dia", "faixa", "enviadas", "organizacoes", "respondidas", "positivas", "negativas", "opt_outs",
              "indefinidas", "respostas_comerciais", "respostas_descartadas", "taxa_de_resposta",
              "taxa_de_interesse", "taxa_de_opt_out", "tempo_medio_de_resposta_horas",
              "tempo_mediano_de_resposta_horas", "amostra_suficiente"]
    with caminho.open("w", encoding="utf-8", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=campos, delimiter=";")
        escritor.writeheader()
        for janela in janelas:
            escritor.writerow({campo: janela.get(campo) for campo in campos})


def regras(contrato: dict) -> dict:
    return {
        "versao": VERSAO,
        "unidade_da_medida": contrato["unidade_da_medida"],
        "fuso": contrato["fuso"],
        "dias": contrato["dias"],
        "faixas": contrato["faixas"],
        "cobertura_da_grade": contrato["cobertura_da_grade"],
        "metricas": contrato["metricas"],
        "ranking": contrato["ranking"],
        "reuso_do_irmao": contrato["reuso_do_irmao"],
        "guardrails": contrato["guardrails"],
        "lacunas": contrato["lacunas"],
    }


# ------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Melhor horario de contato (TRE-W9-E04-T01).")
    parser.add_argument("--ambiente", choices=AMBIENTES, default=None)
    parser.add_argument("--prefixo", default=None, help="comando da porta de banco (psql)")
    parser.add_argument("--desde", default=None, help="inicio do recorte (ISO-8601); default: ate - 30 dias")
    parser.add_argument("--ate", default=None, help="fim do recorte (ISO-8601); default: agora UTC")
    parser.add_argument("--janela-dias", type=float, default=14.0, help="janela de atribuicao (default 14)")
    parser.add_argument("--limite-amostra", type=int, default=5, help="minimo de envios por celula (default 5)")
    parser.add_argument("--json", dest="saida_json", default=None, help="grava o relatorio neste arquivo")
    parser.add_argument("--csv", dest="saida_csv", default=None, help="grava a grade em CSV (;-separado)")
    parser.add_argument("--com-carimbo", action="store_true", help="inclui 'gerado_em' (quebra o determinismo)")
    parser.add_argument("--regras", action="store_true", help="imprime fuso, grade e regras e sai")
    parser.add_argument("--contrato", default=None)
    parser.add_argument("--contrato-irmao", default=None)
    parser.add_argument("--contrato-do-irmao-irmao", dest="contrato_irmao_irmao", default=None)
    parser.add_argument("--raiz", default=None)
    args = parser.parse_args(argv)

    raiz = Path(args.raiz).resolve() if args.raiz else Path(__file__).resolve().parents[2]
    contrato_path = Path(args.contrato).resolve() if args.contrato else raiz / "hermes/analytics/melhor-horario-v1.json"
    contrato_irmao = (Path(args.contrato_irmao).resolve() if args.contrato_irmao
                      else raiz / CONTRATO_IRMAO_PADRAO)
    contrato_irm_irm = (Path(args.contrato_irmao_irmao).resolve() if args.contrato_irmao_irmao
                        else raiz / CONTRATO_DO_IRMAO_IRMAO)

    try:
        if args.ambiente is None:
            raise Recusa("AMBIENTE_NAO_DECLARADO", "--ambiente e obrigatorio (dev|homolog)", 2)
        if args.ambiente == "prod":
            raise Recusa("PROD_RECUSADO", "ADR-005: a analise nao roda em producao", 4)
        dono = carregar_contrato_irmao(raiz, contrato_irmao, contrato_irm_irm)
        contrato = carregar_contrato(contrato_path, dono)
        if args.regras:
            print(json.dumps(regras(contrato), ensure_ascii=False, indent=2, sort_keys=False))
            return 0
        if not args.prefixo:
            raise Recusa("PORTA_DE_BANCO_AUSENTE", "--prefixo (comando da porta de banco) e obrigatorio", 2)
        if args.janela_dias <= 0:
            raise Recusa("RECORTE_INVALIDO", "--janela-dias tem de ser > 0", 2)
        if args.limite_amostra < 1:
            raise Recusa("RECORTE_INVALIDO", "--limite-amostra tem de ser >= 1", 2)

        ate = irmao.parse_instant(args.ate, "--ate") if args.ate else datetime.now(timezone.utc)
        desde = irmao.parse_instant(args.desde, "--desde") if args.desde else ate - timedelta(days=30)
        if desde > ate:
            raise Recusa("RECORTE_INVALIDO", "--desde posterior a --ate", 2)
        janela = timedelta(days=args.janela_dias)

        porta = irmao.PortaBanco(args.prefixo)
        interacoes = irmao.ler_interacoes(porta, desde, ate, janela)
        dados = irmao.separar(interacoes, desde, ate, janela, contrato["_classe_de"], set(dono["_vocabulario"]))
        dados["janela"] = janela
        relatorio = montar_relatorio(contrato, args.ambiente, desde, ate, args.limite_amostra,
                                     dados, args.com_carimbo)
    except irmao.Recusa as recusa:
        print(json.dumps({"versao": VERSAO, "veredito": "RECUSADA", "motivo": recusa.motivo,
                          "detalhe": recusa.detalhe}, ensure_ascii=False))
        return recusa.exit_code

    texto = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=False)
    if args.saida_json:
        Path(args.saida_json).write_text(texto + "\n", encoding="utf-8")
    if args.saida_csv:
        escrever_csv(Path(args.saida_csv), relatorio["por_janela"])
    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
