#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nurture automatizado (`nutricao-automatica-v1`) — card TRE-W9-E05-T01 (W9 / Inteligencia Avancada).

O que este componente FAZ (e so' isto): PLANEJA o nurture. Consome as duas camadas ja' medidas pelos
pais — a previsao de CANAL por organizacao (`previsao-canal-v1`, card W9-E03-T01) e a JANELA de
contato (`melhor-horario-v1`, card W9-E04-T01) — e deriva, de forma deterministica, a FILA de
proximos toques: quem, por qual canal, em que dia x faixa e QUANDO (`due_at_utc`), com cadencia
declarada, condicoes de parada e aprovacao humana obrigatoria.

  NAO_ENVIA nada. Cada toque da fila e' um PEDIDO (`exige_aprovacao_humana: true`), nunca um ato:
  quem executa e' o caminho de outbound com Human Approval (W6).

Invariantes (cada um com item de suite/aceite):

  1. SEM BANCO: este componente nao abre porta de banco, nao executa SQL e nao tem `--porta-banco`.
     A unica entrada e' o JSON dos pais + o proprio contrato. A auditoria de codigo (tokenize,
     ignorando comentario e string) reprova verbo de escrita (`ESCRITA_NO_CODIGO`, exit 3).
  2. CANAL NAO SE REMEDE: o canal de cada organizacao e' o `canal_previsto` do relatorio do pai
     (previsao-canal-v1). Reimplementar a leitura de `interactions` ou o desempate do canal criaria
     duas verdades para o mesmo numero.
  3. JANELA NAO SE REMEDE: a janela e o fuso vem do relatorio do pai do horario (melhor-horario-v1),
     que declara o offset. Nada de zoneinfo/tzdata do sistema.
  4. OPT-OUT E' BLOQUEIO (Data Contract §9): canal bloqueado nunca e' previsto pelo pai; a fila
     repete as condicoes de parada e nao contorna bloqueio nenhum.
  5. FAIL-CLOSED: sem `previsao_emitida` do pai OU sem `melhor_janela`, o plano ABSTEM por inteiro
     (`veredito=PLANO_ABSTIDO`, `plano_emitido=false`, `fila=[]`, lista `faltando`) — nao se escolhe
     canal/janela/horario com base de brinquedo.
  6. DETERMINISMO: mesma entrada + mesma referencia temporal => MESMO `hash_do_plano`; `gerado_em` e'
     a unica diferenca e NAO entra no hash.
  7. GUARDAS DE AMBIENTE (ADR-005 — nada nasce em producao): `prod` RECUSA por desenho (exit 4) antes
     de ler contrato ou relatorio; `homolog` exige `--confirmo`.

Uso:
  python3 hermes/agentes/analytics/nutricao_automatica.py --ambiente dev \\
      --relatorio-canal saida/previsao-canal.json --relatorio-horario saida/melhor-horario.json \\
      --agora 2026-10-03T00:00:00Z --saida saida/nurture
  python3 hermes/agentes/analytics/nutricao_automatica.py --ambiente dev --conferir   # sem relatorio
Exit: 0 = plano/abstencao/conferencia · 2 = uso errado · 3 = recusa (contrato/dependencia/guarda) ·
      4 = producao recusada · 5 = segredo vazado.
"""
from __future__ import annotations

import argparse
import hashlib
import html as _html
import json
import os
import re
import sys
import tokenize
from datetime import datetime, timedelta, timezone

VERSAO = "nutricao-automatica-v1"
CARD = "TRE-W9-E05-T01"
AMBIENTES = ("dev", "homolog", "prod")
AQUI = os.path.dirname(os.path.abspath(__file__))
CONTRATO_PADRAO = os.path.join(AQUI, "nutricao-automatica-v1.json")
CONTRATO_CANAL_PADRAO = os.path.join(AQUI, "previsao-canal-v1.json")
CONTRATO_HORARIO_PADRAO = os.path.join(AQUI, "..", "..", "analytics", "melhor-horario-v1.json")

VERSAO_CANAL_EXIGIDA = "previsao-canal-v1"
VERSAO_HORARIO_EXIGIDA = "melhor-horario-v1"
RE_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")
CODIGO_SEGREDO = 5
RE_SQL_DE_ESCRITA = re.compile(
    r"\b(insert\s+into|update\s+[\w.\"]+\s+set|delete\s+from|create\s+(table|index|view|schema|database)|"
    r"drop\s+(table|index|view|schema|database)|alter\s+table|truncate\s+table|grant\s+[\w\s,]+\bon\b|"
    r"revoke\s+[\w\s,]+\bon\b|copy\s+[\w.]+\s+from|merge\s+into)\b", re.IGNORECASE)
PORTAS_DE_BANCO = ("psql", "PortaBanco", "psycopg", "connect", "cursor", "execute")
TOKENS_DE_SEGREDO = ("TRE_NURTURE_TOKEN", "TRE_CANAL_TOKEN", "TRE_PREVISAO_CANAL_TOKEN", "TRE_TIMING_TOKEN")


def auditar_proprio_codigo(caminho: str | None = None) -> None:
    """Prova que o componente NAO pode escrever: sem porta de banco e sem statement de escrita.

    Dois mecanismos, medidos (nao prosa): (a) nenhum statement de escrita SQL existe no texto do
    arquivo (padrao de STATEMENT, nao de palavra solta — `digest.update()` nao e' um UPDATE); (b)
    nenhum identificador de porta de banco aparece nos NAMES do codigo tokenizado (comentario e
    string fora) — o componente nao tem como falar com banco.
    """
    caminho = caminho or os.path.abspath(__file__)
    with open(caminho, encoding="utf-8") as fh:
        texto = fh.read()
    achados = sorted({casamento.group(0).strip().lower() for casamento in RE_SQL_DE_ESCRITA.finditer(texto)})
    if achados:
        raise Recusa("ESCRITA_NO_CODIGO",
                     "o componente nao escreve nada, mas carrega statement de escrita: %s" % achados)
    with open(caminho, "rb") as fh:
        nomes = {token.string for token in tokenize.tokenize(fh.readline) if token.type == tokenize.NAME}
    portas = sorted(nome for nome in PORTAS_DE_BANCO if nome in nomes)
    if portas:
        raise Recusa("PORTA_DE_BANCO_NO_CODIGO",
                     "o componente nao abre banco, mas o codigo referencia: %s" % portas)


class Recusa(Exception):
    """Recusa declarada: motivo, detalhe e codigo de saida (mesma semantica dos cards irmaos)."""

    def __init__(self, motivo: str, detalhe: str = "", codigo: int = 3):
        super().__init__(motivo)
        self.motivo = motivo
        self.detalhe = detalhe
        self.codigo = codigo


def recusar_producao():
    raise Recusa("PROD_RECUSADO", "ADR-005: o nurture planejado nao roda em producao", 4)


# ------------------------------------------------------------------ contrato e dependencias

def carregar_json(caminho: str, motivo_ausente: str, motivo_ilegivel: str) -> dict:
    if not os.path.isfile(caminho):
        raise Recusa(motivo_ausente, "%s nao existe" % caminho)
    try:
        with open(caminho, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001 - qualquer falha de leitura e' recusa declarada
        raise Recusa(motivo_ilegivel, "%s: %s" % (caminho, exc)) from exc


def sha256_de_arquivo(caminho: str) -> str:
    digest = hashlib.sha256()
    with open(caminho, "rb") as fh:
        for bloco in iter(lambda: fh.read(65536), b""):
            digest.update(bloco)
    return digest.hexdigest()


def carregar_contrato(caminho: str | None = None) -> dict:
    caminho = caminho or CONTRATO_PADRAO
    contrato = carregar_json(caminho, "CONTRATO_AUSENTE", "CONTRATO_ILEGIVEL")
    return validar_contrato(contrato)


def validar_contrato(contrato: dict) -> dict:
    """Coerencia do proprio contrato — fail-closed, nada de aviso."""
    if contrato.get("nome") != VERSAO or contrato.get("versao") != VERSAO:
        raise Recusa("CONTRATO_INCOERENTE", "nome/versao do contrato != %s" % VERSAO)
    politica = contrato.get("politica") or {}
    if politica.get("exige_aprovacao_humana") is not True:
        raise Recusa("CONTRATO_INCOERENTE",
                     "politica.exige_aprovacao_humana tem de ser true (nurture nao se executa sozinho)")
    passos = politica.get("passos") or []
    if not passos:
        raise Recusa("CONTRATO_INCOERENTE", "politica.passos vazia")
    anterior = None
    for indice, passo in enumerate(passos, start=1):
        numero = passo.get("passo")
        intervalo = passo.get("intervalo_dias")
        rotulo = passo.get("rotulo")
        if numero != indice or not isinstance(intervalo, int) or intervalo < 0 or not rotulo:
            raise Recusa("CONTRATO_INCOERENTE",
                         "passo invalido: %s (passo, intervalo_dias>=0 e rotulo sao obrigatorios)" % (passo,))
        if anterior is not None and intervalo < anterior:
            raise Recusa("CONTRATO_INCOERENTE", "intervalo do passo %d diminui (%d < %d)" % (numero, intervalo, anterior))
        anterior = intervalo
    if passos[0]["intervalo_dias"] != 0:
        raise Recusa("CONTRATO_INCOERENTE", "primeiro passo tem de ter intervalo_dias = 0")
    if politica.get("max_toques") != len(passos):
        raise Recusa("CONTRATO_INCOERENTE",
                     "max_toques (%s) != numero de passos (%d)" % (politica.get("max_toques"), len(passos)))
    if not politica.get("condicoes_de_parada"):
        raise Recusa("CONTRATO_INCOERENTE", "politica.condicoes_de_parada vazia")
    if not politica.get("ordem_da_fila"):
        raise Recusa("CONTRATO_INCOERENTE", "politica.ordem_da_fila vazia")
    entradas = contrato.get("entradas") or {}
    if ((entradas.get("canal") or {}).get("versao_exigida") != VERSAO_CANAL_EXIGIDA
            or (entradas.get("horario") or {}).get("versao_exigida") != VERSAO_HORARIO_EXIGIDA):
        raise Recusa("CONTRATO_INCOERENTE", "entradas.canal/horario.versao_exigida divergem do declarado no codigo")
    if not contrato.get("lacunas_declaradas"):
        raise Recusa("CONTRATO_INCOERENTE", "lacunas_declaradas vazia")
    return contrato


def carregar_contrato_do_pai(caminho: str, campo: str, valor: str, papel: str) -> dict:
    contrato = carregar_json(caminho, "CONTRATO_DO_PAI_AUSENTE", "CONTRATO_DO_PAI_ILEGIVEL")
    if contrato.get(campo) != valor:
        raise Recusa("DEPENDENCIA_INCOERENTE",
                     "contrato do pai (%s): %s=%r != %r" % (papel, campo, contrato.get(campo), valor))
    return contrato


def carregar_relatorio(caminho: str, versao_exigida: str, papel: str) -> dict:
    relatorio = carregar_json(caminho, "RELATORIO_DO_PAI_AUSENTE", "RELATORIO_DO_PAI_ILEGIVEL")
    if relatorio.get("versao") != versao_exigida:
        raise Recusa("DEPENDENCIA_INCOERENTE",
                     "relatorio %s: versao=%r != %r" % (papel, relatorio.get("versao"), versao_exigida))
    return relatorio


# ------------------------------------------------------------------ entrada dos pais (leitura pura)

def extrair_faltando(rel_canal: dict, rel_horario: dict) -> list:
    """Pre-condicao das DUAS camadas: sem canal medido ou sem janela medida, o plano ABSTEM."""
    faltando = []
    pre = rel_canal.get("pre_condicao_dados_multicanal") or {}
    if not pre.get("atendida"):
        faltando.append("previsao-canal-v1:pre_condicao_dados_multicanal")
    if not (rel_canal.get("previsoes") or []):
        faltando.append("previsao-canal-v1:previsoes")
    if not rel_horario.get("melhor_janela"):
        faltando.append("melhor-horario-v1:melhor_janela=%s" % (rel_horario.get("melhor_janela_motivo") or "AUSENTE"))
    return faltando


def offset_do_pai(rel_horario: dict) -> tuple[timedelta, dict]:
    fuso = rel_horario.get("fuso") or {}
    casamento = RE_OFFSET.match(fuso.get("offset_utc") or "")
    if not casamento or int(casamento.group(2)) > 14 or int(casamento.group(3)) > 59:
        raise Recusa("FUSO_INVALIDO", "fuso.offset_utc=%r nao e' +HH:MM/-HH:MM plausivel" % (fuso.get("offset_utc"),))
    sinal = -1 if casamento.group(1) == "-" else 1
    deslocamento = sinal * timedelta(hours=int(casamento.group(2)), minutes=int(casamento.group(3)))
    return deslocamento, fuso


def grade_da_janela(rel_horario: dict, dia: str, faixa: str) -> tuple[list, dict]:
    grade = rel_horario.get("grade") or {}
    dias = grade.get("dias") or []
    if len(dias) != 7 or dia not in dias:
        raise Recusa("JANELA_FORA_DA_GRADE", "dia %r nao esta' numa grade de 7 dias: %r" % (dia, dias))
    faixa_def = next((f for f in (grade.get("faixas") or []) if f.get("nome") == faixa), None)
    if faixa_def is None or not isinstance(faixa_def.get("de"), int):
        raise Recusa("JANELA_FORA_DA_GRADE", "faixa %r sem definicao com hora de inicio na grade do pai" % (faixa,))
    return dias, faixa_def


# ------------------------------------------------------------------ cadencia

def ancora_da_cadencia(referencia: datetime, dias: list, dia_alvo: str, hora_inicio: int,
                       offset: timedelta) -> datetime:
    """Primeira ocorrencia do dia-alvo a partir da referencia (hoje SO' se a faixa ainda nao comecou)."""
    local = (referencia.astimezone(timezone.utc) + offset).replace(tzinfo=None)
    avanco = (dias.index(dia_alvo) - local.weekday()) % 7
    if avanco == 0 and local.hour >= hora_inicio:
        avanco = 7
    return (local + timedelta(days=avanco)).replace(hour=hora_inicio, minute=0, second=0, microsecond=0)


def calcular_due(ancora_local: datetime, intervalo_dias: int, dias: list, dia_alvo: str,
                 hora_inicio: int, offset: timedelta) -> tuple[datetime, datetime]:
    """(ancora + intervalo) deslocado para a proxima ocorrencia do dia-alvo — o toque NUNCA cai no passado.

    A ancora e' calculada UMA vez (`ancora_da_cadencia`); cada passo soma o seu intervalo e volta ao
    dia-alvo, o que mantem a fila monotona (o passo N+1 nunca cai antes do passo N).
    """
    alvo = ancora_local + timedelta(days=intervalo_dias)
    avanco = (dias.index(dia_alvo) - alvo.weekday()) % 7
    due_local = (alvo + timedelta(days=avanco)).replace(hour=hora_inicio, minute=0, second=0, microsecond=0)
    due_utc = (due_local - offset).replace(tzinfo=timezone.utc)
    return due_local, due_utc


def formatar(instante: datetime) -> str:
    return instante.strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ derivacao (sem banco, pura)

def planejar(contrato: dict, rel_canal: dict, rel_horario: dict, referencia: datetime, ambiente: str,
             gerado_em: str | None = None, sha_contrato: str | None = None,
             sha_canal: str | None = None, sha_horario: str | None = None) -> dict:
    politica = contrato["politica"]
    passos = politica["passos"]
    faltando = extrair_faltando(rel_canal, rel_horario)
    resumo_canal = rel_canal.get("resumo") or {}

    relatorio = {
        "versao": VERSAO,
        "card": CARD,
        "ambiente": ambiente,
        "referencia_temporal": formatar(referencia),
        "contrato": {"versao": contrato["versao"], "sha256": sha_contrato or sha256_de_arquivo(CONTRATO_PADRAO)},
        "dependencias": {
            "canal": {"relatorio": VERSAO_CANAL_EXIGIDA, "sha256": sha_canal or "",
                      "previsoes_lidas": len(rel_canal.get("previsoes") or []),
                      "hash_do_relatorio": rel_canal.get("hash_do_relatorio") or ""},
            "horario": {"relatorio": VERSAO_HORARIO_EXIGIDA, "sha256": sha_horario or "",
                        "melhor_janela_motivo": rel_horario.get("melhor_janela_motivo")},
        },
        "pre_condicoes": {"atendida": not faltando, "faltando": faltando},
        "politica": {
            "unidade": politica["unidade"],
            "passos": passos,
            "max_toques": politica["max_toques"],
            "regra_do_due": politica["regra_do_due"],
            "ordem_da_fila": politica["ordem_da_fila"],
            "condicoes_de_parada": politica["condicoes_de_parada"],
            "exige_aprovacao_humana": True,
            "nao_envia": True,
        },
        "janela": None,
        "fila": [],
        "por_organizacao": [],
        "lacunas": {"organizacoes_com_canal_bloqueado": 0, "toques_por_canal": {}},
        "lacunas_declaradas": contrato.get("lacunas_declaradas") or [],
    }

    if faltando:
        relatorio["veredito"] = "PLANO_ABSTIDO"
        relatorio["plano_emitido"] = False
        relatorio["resumo"] = {"organizacoes_na_fila": 0, "toques": 0, "por_canal": {},
                               "primeiro_toque_utc": None, "ultimo_toque_utc": None,
                               "endpoint_de_parada": resumo_canal.get("endpoint_principal")}
        relatorio["gerado_em"] = gerado_em or formatar(datetime.now(timezone.utc))
        relatorio["hash_do_plano"] = hash_do_plano(relatorio)
        return relatorio

    melhor = rel_horario["melhor_janela"]
    dia, faixa = melhor["dia"], melhor["faixa"]
    offset, fuso = offset_do_pai(rel_horario)
    dias, faixa_def = grade_da_janela(rel_horario, dia, faixa)
    hora_inicio = int(faixa_def["de"])
    ancora = ancora_da_cadencia(referencia, dias, dia, hora_inicio, offset)

    previsoes = sorted(rel_canal["previsoes"], key=lambda p: p["organization_id"])
    fila = []
    por_organizacao = []
    for previsao in previsoes:
        organizacao = previsao["organization_id"]
        canal = previsao["canal_previsto"]
        toques = []
        for passo in passos:
            due_local, due_utc = calcular_due(ancora, int(passo["intervalo_dias"]), dias, dia, hora_inicio, offset)
            toques.append({
                "organization_id": organizacao,
                "passo": passo["passo"],
                "rotulo": passo["rotulo"],
                "canal": canal,
                "dia": dia,
                "faixa": faixa,
                "intervalo_dias": passo["intervalo_dias"],
                "due_at_utc": formatar(due_utc),
                "due_at_local": due_local.strftime("%Y-%m-%dT%H:%M:%S") + fuso["offset_utc"],
                "motivo": "canal=%s (previsto pelo pai) | janela=%s x %s (melhor do pai)" % (canal, dia, faixa),
                "exige_aprovacao_humana": True,
                "condicoes_de_parada": list(politica["condicoes_de_parada"]),
            })
        if previsao.get("canais_bloqueados"):
            relatorio["lacunas"]["organizacoes_com_canal_bloqueado"] += 1
        fila.extend(toques)
        por_organizacao.append({
            "organization_id": organizacao,
            "canal": canal,
            "amostra_do_canal": previsao.get("amostra_do_canal"),
            "canais_bloqueados": previsao.get("canais_bloqueados") or [],
            "toques": len(toques),
            "primeiro_toque_utc": toques[0]["due_at_utc"],
            "ultimo_toque_utc": toques[-1]["due_at_utc"],
        })

    fila.sort(key=lambda t: (t["due_at_utc"], t["canal"], t["organization_id"]))
    contagem = {}
    for toque in fila:
        contagem[toque["canal"]] = contagem.get(toque["canal"], 0) + 1

    relatorio["veredito"] = "PLANO_EMITIDO"
    relatorio["plano_emitido"] = True
    relatorio["janela"] = {"dia": dia, "faixa": faixa, "faixa_de": hora_inicio,
                           "fuso": fuso.get("nome"), "offset_utc": fuso.get("offset_utc"),
                           "fonte": "melhor-horario-v1:melhor_janela"}
    relatorio["fila"] = fila
    relatorio["por_organizacao"] = por_organizacao
    relatorio["lacunas"]["toques_por_canal"] = {canal: contagem[canal] for canal in sorted(contagem)}
    relatorio["resumo"] = {
        "organizacoes_na_fila": len(por_organizacao),
        "toques": len(fila),
        "por_canal": {canal: contagem[canal] for canal in sorted(contagem)},
        "primeiro_toque_utc": fila[0]["due_at_utc"] if fila else None,
        "ultimo_toque_utc": fila[-1]["due_at_utc"] if fila else None,
        "endpoint_de_parada": resumo_canal.get("endpoint_principal"),
    }
    relatorio["gerado_em"] = gerado_em or formatar(datetime.now(timezone.utc))
    relatorio["hash_do_plano"] = hash_do_plano(relatorio)
    return relatorio


def hash_do_plano(relatorio: dict) -> str:
    limpo = {chave: valor for chave, valor in relatorio.items() if chave not in ("gerado_em", "hash_do_plano")}
    canonico = json.dumps(limpo, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ dashboard (HTML auto-contido)

def emitir_html(relatorio: dict) -> str:
    def linhas(registros, colunas):
        corpo = []
        for registro in registros:
            celulas = "".join("<td>%s</td>" % _html.escape(str(registro.get(coluna, ""))) for coluna in colunas)
            corpo.append("<tr>%s</tr>" % celulas)
        return "".join(corpo)

    fila = relatorio["fila"][:200]
    resumo = relatorio["resumo"]
    janela = relatorio.get("janela") or {}
    partes = [
        "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='utf-8'>",
        "<title>Nurture automatizado — %s</title>" % _html.escape(relatorio["versao"]),
        "<style>body{font-family:system-ui,Arial,sans-serif;margin:24px;color:#12263a}",
        "h1{font-size:20px}h2{font-size:16px;margin-top:24px}",
        "table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #ccd6e0;padding:4px 6px;text-align:left}",
        "th{background:#f1f5f9}.aviso{background:#fff7e6;border-left:4px solid #d98e04;padding:8px}</style></head><body>",
        "<h1>Nurture automatizado — %s</h1>" % _html.escape(CARD),
        "<div class='aviso'>Plano e PEDIDO de toque, nunca envio: cada item exige aprovacao humana. ",
        "O componente nao abre banco e nao escreve nada.</div>",
        "<h2>Pre-condicoes</h2><p>atendida=%s · faltando=%s</p>"
        % (_html.escape(str(relatorio["pre_condicoes"]["atendida"])),
           _html.escape(json.dumps(relatorio["pre_condicoes"]["faltando"], ensure_ascii=False))),
        "<h2>Janela escolhida (dono: melhor-horario-v1)</h2><p>%s</p>"
        % _html.escape(json.dumps(janela, ensure_ascii=False)),
        "<h2>Resumo</h2><p>organizacoes=%s · toques=%s · por canal=%s · primeiro=%s · ultimo=%s</p>"
        % (_html.escape(str(resumo.get("organizacoes_na_fila"))), _html.escape(str(resumo.get("toques"))),
           _html.escape(json.dumps(resumo.get("por_canal"), ensure_ascii=False)),
           _html.escape(str(resumo.get("primeiro_toque_utc"))), _html.escape(str(resumo.get("ultimo_toque_utc")))),
        "<h2>Fila de toques (aprovacao humana obrigatoria)</h2>",
    ]
    if fila:
        partes.append("<table><tr><th>organizacao</th><th>passo</th><th>canal</th><th>dia</th><th>faixa</th>"
                      "<th>due_at_utc</th><th>aprovacao</th></tr>")
        partes.append(linhas(fila, ["organization_id", "passo", "canal", "dia", "faixa", "due_at_utc",
                                    "exige_aprovacao_humana"]))
        partes.append("</table>")
    else:
        partes.append("<p>Fila vazia (plano abstido ou sem toques).</p>")
    partes.append("<h2>Lacunas declaradas</h2><ul>")
    for lacuna in relatorio["lacunas_declaradas"]:
        partes.append("<li>%s</li>" % _html.escape(str(lacuna)))
    partes.append("</ul></body></html>")
    return "".join(partes)


# ------------------------------------------------------------------ main

def regras(contrato: dict) -> dict:
    return {"versao": contrato["versao"], "card": contrato["card"], "pre_condicao": contrato["pre_condicao"],
            "entradas": contrato["entradas"], "politica": contrato["politica"], "guardas": contrato["guardas"],
            "lacunas_declaradas": contrato["lacunas_declaradas"]}


def conferir(contrato: dict, contrato_canal_path: str, contrato_horario_path: str) -> str:
    canal = carregar_contrato_do_pai(contrato_canal_path, "versao", VERSAO_CANAL_EXIGIDA, "canal")
    horario = carregar_contrato_do_pai(contrato_horario_path, "nome", VERSAO_HORARIO_EXIGIDA, "horario")
    auditar_proprio_codigo()
    return ("NUTRICAO_AUTOMATICA_CONFERIR_OK contrato=%s passos=%d max_toques=%s aprovacao_humana=%s "
            "canal=%s horario=%s" % (contrato["versao"], len(contrato["politica"]["passos"]),
                                     contrato["politica"]["max_toques"],
                                     contrato["politica"]["exige_aprovacao_humana"],
                                     canal.get("versao"), horario.get("nome")))


def resumo_texto(relatorio: dict) -> str:
    pre = relatorio["pre_condicoes"]
    resumo = relatorio["resumo"]
    if not pre["atendida"]:
        return "PLANO_ABSTIDO %s faltando=%s" % (relatorio["veredito"], json.dumps(pre["faltando"], ensure_ascii=False))
    return " ".join([
        "NUTRICAO_AUTOMATICA_OK",
        "veredito=%s" % relatorio["veredito"],
        "organizacoes=%d" % resumo["organizacoes_na_fila"],
        "toques=%d" % resumo["toques"],
        "por_canal=%s" % json.dumps(resumo["por_canal"], ensure_ascii=False),
        "primeiro=%s" % resumo["primeiro_toque_utc"],
        "hash=%s" % relatorio["hash_do_plano"][:16],
    ])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Nurture automatizado v1 — %s" % CARD)
    parser.add_argument("--ambiente", required=True, choices=list(AMBIENTES))
    parser.add_argument("--relatorio-canal", default=None, help="JSON do relatorio previsao-canal-v1 (card W9-E03-T01)")
    parser.add_argument("--relatorio-horario", default=None, help="JSON do relatorio melhor-horario-v1 (card W9-E04-T01)")
    parser.add_argument("--agora", default=None, help="referencia temporal (ISO); padrao: relogio da rodada")
    parser.add_argument("--saida", default=None, help="diretorio de saida do relatorio")
    parser.add_argument("--formato", default="json,html", choices=["json", "html", "json,html"])
    parser.add_argument("--confirmo", action="store_true", help="obrigatorio em homolog")
    parser.add_argument("--conferir", action="store_true", help="valida contratos e a guarda de escrita (sem relatorio)")
    parser.add_argument("--regras", action="store_true", help="imprime a politica declarada e sai")
    parser.add_argument("--contrato", default=CONTRATO_PADRAO)
    parser.add_argument("--contrato-canal", default=CONTRATO_CANAL_PADRAO)
    parser.add_argument("--contrato-horario", default=CONTRATO_HORARIO_PADRAO)
    args = parser.parse_args(argv)

    try:
        if args.ambiente == "prod":
            recusar_producao()  # antes de qualquer leitura, independentemente de contrato/relatorio
        if args.ambiente == "homolog" and not args.confirmo:
            raise Recusa("HOMOLOG_SEM_CONFIRMACAO", "homolog exige --confirmo", 2)
        contrato = carregar_contrato(args.contrato)

        if args.conferir:
            print(conferir(contrato, args.contrato_canal, args.contrato_horario))
            return 0
        if args.regras:
            print(json.dumps(regras(contrato), ensure_ascii=False, indent=2, sort_keys=False))
            return 0

        if not args.relatorio_canal or not args.relatorio_horario:
            raise Recusa("USO", "--relatorio-canal e --relatorio-horario sao obrigatorios (ou use --conferir/--regras)", 2)
        rel_canal = carregar_relatorio(args.relatorio_canal, VERSAO_CANAL_EXIGIDA, "canal")
        rel_horario = carregar_relatorio(args.relatorio_horario, VERSAO_HORARIO_EXIGIDA, "horario")
        # Contratos dos pais conferidos TAMBEM na rodada normal: o dono do numero nao muda em silencio.
        carregar_contrato_do_pai(args.contrato_canal, "versao", VERSAO_CANAL_EXIGIDA, "canal")
        carregar_contrato_do_pai(args.contrato_horario, "nome", VERSAO_HORARIO_EXIGIDA, "horario")
        auditar_proprio_codigo()

        referencia = datetime.now(timezone.utc)
        if args.agora:
            try:
                referencia = datetime.fromisoformat(args.agora.replace("Z", "+00:00"))
            except ValueError as exc:
                raise Recusa("REFERENCIA_INVALIDA", "--agora=%r: %s" % (args.agora, exc), 2) from exc
            if referencia.tzinfo is None:
                raise Recusa("REFERENCIA_INVALIDA", "--agora sem fuso explicito", 2)

        relatorio = planejar(contrato, rel_canal, rel_horario, referencia, args.ambiente,
                             sha_contrato=sha256_de_arquivo(args.contrato),
                             sha_canal=sha256_de_arquivo(args.relatorio_canal),
                             sha_horario=sha256_de_arquivo(args.relatorio_horario))
        saida_json = json.dumps(relatorio, ensure_ascii=False, indent=2, sort_keys=True)
        saida_html = emitir_html(relatorio)
        for token_nome in TOKENS_DE_SEGREDO:
            token = os.environ.get(token_nome)
            if token and (token in saida_json or token in saida_html):
                raise Recusa("SENHA_VAZADA", "valor de %s presente na evidencia" % token_nome, CODIGO_SEGREDO)

        if args.saida:
            os.makedirs(args.saida, exist_ok=True)
            if "json" in args.formato:
                with open(os.path.join(args.saida, "nutricao-automatica.json"), "w", encoding="utf-8") as fh:
                    fh.write(saida_json)
            if "html" in args.formato:
                with open(os.path.join(args.saida, "nutricao-automatica.html"), "w", encoding="utf-8") as fh:
                    fh.write(saida_html)
        print(resumo_texto(relatorio))
        return 0
    except Recusa as exc:
        print(json.dumps({"versao": VERSAO, "veredito": "RECUSADA", "motivo": exc.motivo,
                          "detalhe": exc.detalhe}, ensure_ascii=False))
        return exc.codigo
    except Exception as exc:  # noqa: BLE001 - nada pode virar traceback (exit 1) nem sucesso silencioso
        raise


if __name__ == "__main__":
    sys.exit(main())
