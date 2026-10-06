#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analise de desempenho de mensagens — `desempenho-mensagens-v1` (card TRE-W8-E04-T01).

O que mede: dado o fato ja gravado pelos cards irmaos (W6-E04 envio outbound -> `interactions`
OUTBOUND com `content_reference` `envio:<approval_id>:<texto_hash>`; W6-E05 ingestao/classificacao
de resposta -> `interactions` INBOUND com `response_category`), calcula por VARIANTE de texto e por
CANAL: enviadas, respondidas, positivas, negativas, opt-outs, taxas, tempo de resposta e qual
variante performa melhor.

O que NAO faz (declarado no contrato `hermes/analytics/desempenho-mensagens-v1.json`):
  - NAO envia, NAO aprova, NAO classifica resposta e NAO chama LLM;
  - NAO escreve: a unica instrucao enviada a porta de banco e um SELECT (guardrail conferido);
  - NAO inventa categoria: o vocabulario e o do irmao de ingestao, e divergencia RECUSA (fail-closed);
  - prod RECUSA exit 4 (ADR-005).

Uso:
  python3 hermes/analytics/desempenho_mensagens.py --ambiente dev \
      --prefixo "docker exec -i pg-x psql -U sales_ai -d sales_intelligence" \
      --desde 2026-09-01T00:00:00Z --ate 2026-09-30T23:59:59Z
  python3 hermes/analytics/desempenho_mensagens.py --regras
Exit: 0 = ANALISADO/SEM_MENSAGENS · 2 = uso/porta ausente · 3 = fail-closed · 4 = prod recusado.
"""
from __future__ import annotations

import argparse
import csv
import json
import shlex
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSAO = "desempenho-mensagens-v1"
AMBIENTES = ("dev", "homolog", "prod")
CLASSES_COMERCIAIS = ("RESPOSTA_POSITIVA", "RESPOSTA_NEGATIVA", "RESPOSTA_INDEFINIDA")
PREFIXO_ENVIO = "envio:"

# Verbos de escrita proibidos no SQL deste componente (fail-closed em `afirmar_somente_leitura`).
VERBOS_DE_ESCRITA = ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE",
                     "GRANT", "REVOKE", "COPY", "CALL", "DO", "MERGE", "VACUUM", "REFRESH")

SQL_INTERACOES = """SELECT i.id, i.organization_id, COALESCE(i.contact_id::text, ''), i.channel, i.direction,
       COALESCE(i.interaction_type, ''),
       to_char(i.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS') || 'Z',
       COALESCE(i.content_reference, ''), COALESCE(i.response_category, '')
FROM sales_intelligence.interactions i
WHERE i.occurred_at >= TIMESTAMPTZ '{desde}'
  AND i.occurred_at <= TIMESTAMPTZ '{fim}'
  AND ( (upper(i.direction) = 'OUTBOUND' AND i.content_reference LIKE 'envio:%')
        OR upper(i.direction) = 'INBOUND' )
ORDER BY i.occurred_at ASC, i.id ASC;"""


class Recusa(Exception):
    """Falha declarada: carrega motivo (contrato) e detalhe; quem trata e o main."""

    def __init__(self, motivo: str, detalhe: str = "", exit_code: int = 3):
        super().__init__(motivo)
        self.motivo = motivo
        self.detalhe = detalhe
        self.exit_code = exit_code


# ------------------------------------------------------------------ utilidades de dominio

def normalizar_canal(canal: str) -> str:
    """Canal NORMALIZADO: o irmao de envio grava `EMAIL` e o de ingestao grava `email`.

    Sem normalizar, envio e resposta de e-mail nunca casariam. A divergencia de FORMA e do dado
    gravado (registrada como lacuna), nao do contrato — o canal e o mesmo.
    """
    return (canal or "").strip().upper()


def parse_instant(valor: str, campo: str) -> datetime:
    texto = (valor or "").strip()
    if not texto:
        raise Recusa("RECORTE_INVALIDO", f"{campo} vazio", 2)
    normalizado = texto.replace("Z", "+00:00") if texto.endswith("Z") else texto
    try:
        dt = datetime.fromisoformat(normalizado)
    except ValueError as exc:
        raise Recusa("RECORTE_INVALIDO", f"{campo}={texto!r} nao e ISO-8601", 2) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def formatar_instant(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_referencia_envio(referencia: str):
    """`envio:<approval_id>:<texto_hash>` -> (approval_id, texto_hash) ou None se malformada."""
    partes = (referencia or "").split(":")
    if len(partes) != 3 or partes[0] != "envio" or not all(partes):
        return None
    return partes[1], partes[2]


def afirmar_somente_leitura(sql: str) -> None:
    """Guardrail: o componente so manda SELECT/WITH e nenhum verbo de escrita pode aparecer."""
    texto = (sql or "").strip()
    primeiro = re.split(r"\s+", texto, maxsplit=1)[0].upper() if texto else ""
    if primeiro not in ("SELECT", "WITH"):
        raise Recusa("ESCRITA_RECUSADA", f"statement nao e leitura: {primeiro or '<vazio>'}")
    for verbo in VERBOS_DE_ESCRITA:
        if re.search(r"\b%s\b" % verbo, texto.upper()):
            raise Recusa("ESCRITA_RECUSADA", f"verbo de escrita no SQL: {verbo}")


# ------------------------------------------------------------------ contrato

def carregar_json(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - qualquer falha de leitura e incoerencia declarada
        raise Recusa("CONTRATO_INCOERENTE", f"{caminho}: {exc}") from exc


def carregar_contrato(raiz: Path, contrato_path: Path, contrato_irmao: Path) -> dict:
    """Le o contrato deste card e confere a PARTICAO das classes contra o vocabulario do irmao."""
    contrato = carregar_json(contrato_path)
    if contrato.get("nome") != VERSAO:
        raise Recusa("CONTRATO_INCOERENTE", f"nome do contrato != {VERSAO}")
    classes = contrato.get("classes_de_resposta") or {}
    if not classes:
        raise Recusa("CONTRATO_INCOERENTE", "classes_de_resposta ausente")

    ref = contrato.get("vocabulario_irmao") or {}
    caminho = ref.get("caminho") or "vocabulario.response_category"
    nao_persistidas = set(ref.get("nao_persistidas") or [])
    irmao = carregar_json(contrato_irmao)
    atual = irmao
    for parte in caminho.split("."):
        if not isinstance(atual, dict) or parte not in atual:
            raise Recusa("CONTRATO_INCOERENTE", f"vocabulario do irmao ausente em {caminho!r}")
        atual = atual[parte]
    if not isinstance(atual, list) or not atual:
        raise Recusa("CONTRATO_INCOERENTE", f"vocabulario do irmao em {caminho!r} nao e lista")
    vocabulario = set(atual) - nao_persistidas

    mapeado: list[str] = []
    for classe in classes.values():
        mapeado.extend(classe)
    faltando = sorted(vocabulario - set(mapeado))
    sobrando = sorted(set(mapeado) - vocabulario)
    repetidos = sorted({c for c in mapeado if mapeado.count(c) > 1})
    if faltando or sobrando or repetidos:
        raise Recusa(
            "CONTRATO_INCOERENTE",
            f"classes nao particionam o vocabulario do irmao: faltando={faltando} sobrando={sobrando} repetidos={repetidos}",
        )

    classe_de: dict[str, str] = {}
    for classe, categorias in classes.items():
        for categoria in categorias:
            classe_de[categoria] = classe
    contrato["_classe_de"] = classe_de
    contrato["_vocabulario"] = sorted(vocabulario)
    return contrato


# ------------------------------------------------------------------ porta de banco (somente leitura)

class PortaBanco:
    """Porta de banco declarada pelo operador (`--prefixo`), no mesmo contrato dos cards irmaos."""

    def __init__(self, prefixo: str):
        try:
            self.comando = shlex.split(prefixo)
        except ValueError as exc:
            raise Recusa("PORTA_DE_BANCO_AUSENTE", f"prefixo invalido: {exc}", 2) from exc
        if not self.comando:
            raise Recusa("PORTA_DE_BANCO_AUSENTE", "prefixo vazio", 2)

    def consultar(self, sql: str) -> list[str]:
        afirmar_somente_leitura(sql)
        try:
            proc = subprocess.run(self.comando + ["-tA", "-F", "|", "-c", sql],
                                  capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Recusa("PORTA_DE_BANCO_AUSENTE", str(exc), 2) from exc
        if proc.returncode != 0:
            raise Recusa("PORTA_DE_BANCO_AUSENTE",
                         f"a porta respondeu rc={proc.returncode}: {(proc.stderr or '').strip()[:200]}", 2)
        return [linha for linha in proc.stdout.splitlines() if linha.strip()]


def ler_interacoes(porta: PortaBanco, desde: datetime, ate: datetime, janela: timedelta) -> list[dict]:
    """Uma UNICA leitura: envios do recorte + respostas candidatas (recorte + janela)."""
    sql = SQL_INTERACOES.format(desde=formatar_instant(desde), fim=formatar_instant(ate + janela))
    linhas = porta.consultar(sql)
    interacoes: list[dict] = []
    for linha in linhas:
        campos = linha.split("|")
        if len(campos) != 9:
            raise Recusa("PORTA_RESPOSTA_INVALIDA", f"esperado 9 colunas, veio {len(campos)}: {linha[:120]!r}")
        interacoes.append({
            "id": campos[0], "organization_id": campos[1], "contact_id": campos[2],
            "canal": normalizar_canal(campos[3]), "direction": campos[4].strip().upper(),
            "interaction_type": campos[5], "occurred_at": campos[6],
            "content_reference": campos[7], "response_category": campos[8],
        })
    return interacoes


# ------------------------------------------------------------------ atribuicao

def separar(interacoes: list[dict], desde: datetime, ate: datetime, janela: timedelta,
            classe_de: dict[str, str], vocabulario: set[str]) -> dict:
    """Separa envios do recorte de respostas candidatas, contando o que fica fora (sem silencio)."""
    excluidas = {
        "outbound_sem_referencia_de_envio": 0, "referencia_malformada": 0,
        "inbound_sem_categoria": 0, "inbound_categoria_fora_do_vocabulario": 0,
        "fora_do_recorte_de_envio": 0, "resposta_fora_do_recorte": 0,
    }
    envios: list[dict] = []
    respostas: list[dict] = []
    for i in interacoes:
        momento = parse_instant(i["occurred_at"], "occurred_at")
        if i["direction"] == "OUTBOUND":
            if not i["content_reference"].startswith(PREFIXO_ENVIO):
                excluidas["outbound_sem_referencia_de_envio"] += 1
                continue
            partes = parse_referencia_envio(i["content_reference"])
            if partes is None:
                excluidas["referencia_malformada"] += 1
                continue
            if not (desde <= momento <= ate):
                excluidas["fora_do_recorte_de_envio"] += 1
                continue
            envios.append({**i, "momento": momento, "texto_hash": partes[1]})
        elif i["direction"] == "INBOUND":
            categoria = (i["response_category"] or "").strip()
            if not categoria:
                excluidas["inbound_sem_categoria"] += 1
                continue
            if categoria not in vocabulario:
                excluidas["inbound_categoria_fora_do_vocabulario"] += 1
                continue
            if not (desde <= momento <= ate + janela):
                excluidas["resposta_fora_do_recorte"] += 1
                continue
            respostas.append({**i, "momento": momento, "classe": classe_de[categoria]})
    envios.sort(key=lambda e: (e["momento"], e["id"]))
    respostas.sort(key=lambda r: (r["momento"], r["id"]))
    return {"envios": envios, "respostas": respostas, "excluidas": excluidas}


def _escolher(candidatos: list[dict], resposta: dict, janela: timedelta):
    """Envio mais PROXIMO ANTERIOR da lista, com janela inclusiva; None se nenhum serve."""
    for envio in reversed(candidatos):  # do mais recente para o mais antigo
        if envio["momento"] >= resposta["momento"]:
            continue
        if resposta["momento"] - envio["momento"] > janela:
            break  # mais antigos so pioram: nenhum candidato adiante cabe na janela
        return envio
    return None


def creditar(envios: list[dict], respostas: list[dict], janela: timedelta) -> dict[str, list[dict]]:
    """Crédito ao envio mais PROXIMO ANTERIOR (nearest preceding), em DUAS faixas de especificidade.

    Cada resposta e creditada a no MAXIMO um envio. Faixas, da mais especifica para a menos:
      (1) envio com o MESMO contact_id da resposta (a mensagem foi para aquela pessoa);
      (2) se nenhum da faixa 1 servir, envio SEM contato declarado (a mensagem foi para a organizacao).
    Envio com contato DIFERENTE do da resposta nunca concorre. O canal tem de ser o mesmo (normalizado)
    e a resposta tem de cair em (momento do envio, momento do envio + janela] — limite INCLUSIVO.
    """
    por_chave: dict[tuple[str, str], list[dict]] = {}
    for envio in envios:
        por_chave.setdefault((envio["organization_id"], envio["canal"]), []).append(envio)
    for lista in por_chave.values():
        lista.sort(key=lambda e: (e["momento"], e["id"]))

    creditos: dict[str, list[dict]] = {}
    for resposta in respostas:
        candidatos = por_chave.get((resposta["organization_id"], resposta["canal"]), [])
        escolhido = None
        if resposta["contact_id"]:
            especificos = [e for e in candidatos if e["contact_id"] == resposta["contact_id"]]
            escolhido = _escolher(especificos, resposta, janela)
        if escolhido is None:
            escopo_da_organizacao = [e for e in candidatos if not e["contact_id"]]
            escolhido = _escolher(escopo_da_organizacao, resposta, janela)
        if escolhido is not None:
            creditos.setdefault(escolhido["id"], []).append(resposta)
    return creditos


# ------------------------------------------------------------------ metricas

def _taxa(numerador: int, denominador: int):
    return round(numerador / denominador, 4) if denominador else None


def _mediana_horas(segundos: list[float]):
    if not segundos:
        return None
    ordenado = sorted(segundos)
    meio = len(ordenado) // 2
    valor = ordenado[meio] if len(ordenado) % 2 else (ordenado[meio - 1] + ordenado[meio]) / 2.0
    return round(valor / 3600.0, 2)


def medir_grupo(envios: list[dict], creditos: dict[str, list[dict]], limite_amostra: int) -> dict:
    respondidas = positivas = negativas = opt_outs = indefinidas = 0
    comerciais = descartadas = 0
    tempos: list[float] = []
    for envio in envios:
        creditadas = creditos.get(envio["id"], [])
        classes = {r["classe"] for r in creditadas}
        comerciais += sum(1 for r in creditadas if r["classe"] in CLASSES_COMERCIAIS)
        descartadas += sum(1 for r in creditadas if r["classe"] == "NAO_E_RESPOSTA_DE_LEAD")
        if classes & set(CLASSES_COMERCIAIS):
            respondidas += 1
            primeiras = [r["momento"] for r in creditadas if r["classe"] in CLASSES_COMERCIAIS]
            tempo = min(primeiras) - envio["momento"]
            tempos.append(tempo.total_seconds())
        if "RESPOSTA_POSITIVA" in classes:
            positivas += 1
        if "RESPOSTA_NEGATIVA" in classes:
            negativas += 1
        if any(r["response_category"] == "OPT_OUT" for r in creditadas):
            opt_outs += 1
        if "RESPOSTA_INDEFINIDA" in classes:
            indefinidas += 1
    enviadas = len(envios)
    return {
        "enviadas": enviadas,
        "organizacoes": len({e["organization_id"] for e in envios}),
        "respondidas": respondidas,
        "positivas": positivas,
        "negativas": negativas,
        "opt_outs": opt_outs,
        "indefinidas": indefinidas,
        "respostas_comerciais": comerciais,
        "respostas_descartadas": descartadas,
        "taxa_de_resposta": _taxa(respondidas, enviadas),
        "taxa_de_interesse": _taxa(positivas, enviadas),
        "taxa_de_opt_out": _taxa(opt_outs, enviadas),
        "tempo_medio_de_resposta_horas": round(sum(tempos) / len(tempos) / 3600.0, 2) if tempos else None,
        "tempo_mediano_de_resposta_horas": _mediana_horas(tempos),
        "amostra_suficiente": enviadas >= limite_amostra,
    }


def analisar(envios: list[dict], creditos: dict[str, list[dict]], limite_amostra: int) -> dict:
    por_variante: dict[tuple[str, str], list[dict]] = {}
    por_canal: dict[str, list[dict]] = {}
    for envio in envios:
        por_variante.setdefault((envio["texto_hash"], envio["canal"]), []).append(envio)
        por_canal.setdefault(envio["canal"], []).append(envio)

    variantes = []
    for (texto_hash, canal), lista in sorted(por_variante.items()):
        grupo = {"texto_hash": texto_hash, "canal": canal}
        grupo.update(medir_grupo(lista, creditos, limite_amostra))
        variantes.append(grupo)
    variantes.sort(key=lambda v: (-(v["taxa_de_interesse"] or 0.0), -(v["taxa_de_resposta"] or 0.0),
                                  -v["enviadas"], v["texto_hash"]))
    canais = {canal: medir_grupo(lista, creditos, limite_amostra) for canal, lista in sorted(por_canal.items())}
    return {"por_variante": variantes, "por_canal": canais}


def melhor_variante(variantes: list[dict]) -> tuple[dict | None, str | None]:
    elegiveis = [v for v in variantes if v["amostra_suficiente"]]
    if not variantes:
        return None, "SEM_VARIANTES"
    if not elegiveis:
        return None, "AMOSTRA_INSUFICIENTE"
    return elegiveis[0], None


# ------------------------------------------------------------------ saida

def regras(contrato: dict) -> dict:
    return {
        "versao": VERSAO,
        "identidade_da_mensagem": contrato["identidade_da_mensagem"],
        "classes_de_resposta": contrato["classes_de_resposta"],
        "vocabulario_irmao": contrato["vocabulario_irmao"],
        "criterio_de_atribuicao": contrato["criterio_de_atribuicao"],
        "metricas": contrato["metricas"],
        "ranking": contrato["ranking"],
        "guardrails": contrato["guardrails"],
        "lacunas": contrato["lacunas"],
    }


def montar_relatorio(contrato: dict, ambiente: str, desde: datetime, ate: datetime,
                     janela: timedelta, limite_amostra: int, dados: dict,
                     carimbo: bool) -> dict:
    envios, respostas = dados["envios"], dados["respostas"]
    creditos = creditar(envios, respostas, janela)
    metricas = analisar(envios, creditos, limite_amostra)
    melhor, motivo = melhor_variante(metricas["por_variante"])
    total = medir_grupo(envios, creditos, limite_amostra)
    relatorio = {
        "versao": VERSAO,
        "card": contrato.get("card"),
        "ambiente": ambiente,
        "veredito": "ANALISADO" if envios else "SEM_MENSAGENS",
        "recorte": {
            "desde": formatar_instant(desde), "ate": formatar_instant(ate),
            "janela_dias": round(janela.total_seconds() / 86400.0, 4),
            "limite_amostra": limite_amostra,
            "criterio_de_atribuicao": contrato["criterio_de_atribuicao"]["regra"],
        },
        "totais": total,
        "por_canal": metricas["por_canal"],
        "por_variante": metricas["por_variante"],
        "melhor_variante": melhor,
        "melhor_variante_motivo": motivo,
        "respostas_lidas": len(respostas),
        "excluidas_do_recorte": dados["excluidas"],
        "lacunas": contrato["lacunas"],
    }
    if carimbo:
        relatorio["gerado_em"] = formatar_instant(datetime.now(timezone.utc))
    return relatorio


def escrever_csv(caminho: Path, variantes: list[dict]) -> None:
    campos = ["texto_hash", "canal", "enviadas", "organizacoes", "respondidas", "positivas", "negativas",
              "opt_outs", "indefinidas", "respostas_comerciais", "respostas_descartadas",
              "taxa_de_resposta", "taxa_de_interesse", "taxa_de_opt_out",
              "tempo_medio_de_resposta_horas", "tempo_mediano_de_resposta_horas", "amostra_suficiente"]
    with caminho.open("w", encoding="utf-8", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=campos, delimiter=";")
        escritor.writeheader()
        for variante in variantes:
            escritor.writerow({campo: variante.get(campo) for campo in campos})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analise de desempenho de mensagens (TRE-W8-E04-T01).")
    parser.add_argument("--ambiente", choices=AMBIENTES, default=None)
    parser.add_argument("--prefixo", default=None, help="comando da porta de banco (psql)")
    parser.add_argument("--desde", default=None, help="inicio do recorte (ISO-8601); default: ate - 30 dias")
    parser.add_argument("--ate", default=None, help="fim do recorte (ISO-8601); default: agora UTC")
    parser.add_argument("--janela-dias", type=float, default=14.0, help="janela de atribuicao (default 14)")
    parser.add_argument("--limite-amostra", type=int, default=5, help="minimo de envios por variante (default 5)")
    parser.add_argument("--json", dest="saida_json", default=None, help="grava o relatorio neste arquivo")
    parser.add_argument("--csv", dest="saida_csv", default=None, help="grava as variantes em CSV (;-separado)")
    parser.add_argument("--com-carimbo", action="store_true", help="inclui 'gerado_em' (quebra o determinismo)")
    parser.add_argument("--regras", action="store_true", help="imprime criterio, classes e lacunas e sai")
    parser.add_argument("--contrato", default=None)
    parser.add_argument("--contrato-irmao", default=None)
    parser.add_argument("--raiz", default=None)
    args = parser.parse_args(argv)

    raiz = Path(args.raiz).resolve() if args.raiz else Path(__file__).resolve().parents[2]
    contrato_path = Path(args.contrato).resolve() if args.contrato else raiz / "hermes/analytics/desempenho-mensagens-v1.json"
    contrato_irmao = (Path(args.contrato_irmao).resolve() if args.contrato_irmao
                      else raiz / "hermes/agentes/respostas/ingestao-respostas-v1.json")

    try:
        if args.ambiente is None:
            raise Recusa("AMBIENTE_NAO_DECLARADO", "--ambiente e obrigatorio (dev|homolog)", 2)
        if args.ambiente == "prod":
            raise Recusa("PROD_RECUSADO", "ADR-005: a analise nao roda em producao", 4)
        contrato = carregar_contrato(raiz, contrato_path, contrato_irmao)
        if args.regras:
            print(json.dumps(regras(contrato), ensure_ascii=False, indent=2, sort_keys=False))
            return 0
        if not args.prefixo:
            raise Recusa("PORTA_DE_BANCO_AUSENTE", "--prefixo (comando da porta de banco) e obrigatorio", 2)
        if args.janela_dias <= 0:
            raise Recusa("RECORTE_INVALIDO", "--janela-dias tem de ser > 0", 2)
        if args.limite_amostra < 1:
            raise Recusa("RECORTE_INVALIDO", "--limite-amostra tem de ser >= 1", 2)

        ate = parse_instant(args.ate, "--ate") if args.ate else datetime.now(timezone.utc)
        desde = parse_instant(args.desde, "--desde") if args.desde else ate - timedelta(days=30)
        if desde > ate:
            raise Recusa("RECORTE_INVALIDO", "--desde posterior a --ate", 2)
        janela = timedelta(days=args.janela_dias)

        porta = PortaBanco(args.prefixo)
        interacoes = ler_interacoes(porta, desde, ate, janela)
        dados = separar(interacoes, desde, ate, janela, contrato["_classe_de"], set(contrato["_vocabulario"]))
        relatorio = montar_relatorio(contrato, args.ambiente, desde, ate, janela, args.limite_amostra,
                                     dados, args.com_carimbo)
    except Recusa as recusa:
        print(json.dumps({"versao": VERSAO, "veredito": "RECUSADA", "motivo": recusa.motivo,
                          "detalhe": recusa.detalhe}, ensure_ascii=False))
        return recusa.exit_code

    texto = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=False)
    if args.saida_json:
        Path(args.saida_json).write_text(texto + "\n", encoding="utf-8")
    if args.saida_csv:
        escrever_csv(Path(args.saida_csv), relatorio["por_variante"])
    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
