# -*- coding: utf-8 -*-
"""Motor da API controlada do Odoo — card TRE-W3-E01-T01 (`t_e0489efc`).

O QUE ESTE ARQUIVO E', E POR QUE ELE E' PURO:

Aqui vive a DECISAO da API controlada: o que existe (a politica versionada), o que cada operacao
pode tocar (modelo, campos, limites, chave de idempotencia), sob que ambiente ela atende
(guarda do ADR-005) e como cada recusa e' nomeada (taxonomia de erro). Este arquivo NAO importa
`odoo`: quem fala com o ORM e' o controlador (`controllers/api_controlada.py`), que executa o
PLANO montado aqui. A separacao tem duas razoes medidas:

  1. a decisao (allowlist/limites/ambiente) pode ser exercitada sem subir Odoo nenhum
     (`scripts/odoo/testar_motor_api.py`), inclusive nos dentes do verificador; e
  2. o caminho de dados fica num lugar so' — o controlador nao decide nada, so' executa o plano.
     Nao existe rota de "execute qualquer metodo/modelo/campo": fora da declaracao, a resposta e'
     recusa (fail-closed).

FONTE DA REGRA (nada aqui e' inventado):
  * doc 02 §3 — "Nao escrever diretamente nas tabelas internas do Odoo" / "Integracao Odoo via
    modulo/API controlada" -> todo acesso por ORM, e a porta e' declarada;
  * doc 06 §7 e doc 13 §9 — `idempotency_key`, correlacao, retry limitado, `sync_events`,
    dead-letter -> a chave de idempotencia e' exigida no contrato da escrita;
  * doc 12 §2/§3 — o evento PG -> Odoo carrega `event_type`/`event_version`/`timestamp`/payload;
  * ADR-005 — nada nasce em producao: a API so' atende no ambiente declarado E permitido pela
    politica, e homologacao/producao exigem aprovacao humana registrada.

O QUE ESTE ARQUIVO NAO FAZ (lacuna declarada):
  * nao implementa o motor de deduplicacao por `idempotency_key` (card `TRE-W3-E02-T02`, filho);
    aqui a chave e' exigida, validada e devolvida no plano, para ser registrada;
  * nao declara as operacoes de negocio: `empresa_upsert` entra pela politica no card
    `TRE-W3-E01-T02`, `contato_upsert` no `TRE-W3-E01-T03`, e opportunity upsert e activity
    create sao dos cards `TRE-W3-E01-T04..T05`;
  * nao persiste auditoria: emite a decisao; a linha de auditoria e' do controlador e a
    persistencia/observabilidade e' do `TRE-W3-E05-T01`.

O QUE O CARD E01-T02 ACRESCENTOU AQUI (e por que a decisao continua sendo DECLARADA, nao literal):
  * a identidade da escrita passa a poder ser uma LISTA ORDENADA (`campos_de_identidade`): a ordem
    da lista e' a prioridade com que o controlador casa o registro (`tf_company_id` canonico
    primeiro, depois os fortes do contrato §5: CNPJ -> dominio -> LinkedIn). `campo_de_identidade`
    continua valendo (forma de um campo so', declarada pela politica do E01-T01);
  * `valores_fixos`: campo cujo valor o chamador NAO decide (ex.: `is_company` do upsert de
    empresa). Divergencia do payload vira recusa nomeada (`campo_fixo_divergente`), nunca "ignora
    o que o chamador disse";
  * sem NENHUM identificador com valor, a escrita abstém-se com `identificador_ausente` — a API
    nao inventa identidade nem escolhe registro por conta propria.

O QUE O CARD E01-T03 ACRESCENTOU AQUI:
  * `identificador` (o parametro escalar da forma de UM campo) passa a ser recusado sempre que a
    DECLARACAO usa a lista ordenada — inclusive quando a lista tem um elemento so', caso da
    operacao de contato (`campos_de_identidade: ["email"]`). Antes o portao olhava o TAMANHO da
    lista (`len(ordem) != 1`) e, numa lista de um, aceitava e DESCARTAVA o parametro em silencio:
    o chamador informava a identidade e o plano usava outra coisa (ou abstinha) sem nomear nada.
    Nenhum mecanismo novo: e' o mesmo portao, olhando a forma declarada em vez do resultado dela.
  * nenhuma operacao de negocio entrou pela politica por causa deste card — `contato_upsert` e'
    declarada em `politica_api.json` (identidade por `email`, `is_company` como valor fixo), e o
    motor continua sem conhecer nome de operacao: so' executa o que a politica declara.
"""

import datetime
import json
import os
import re

# ---------------------------------------------------------------------------
# Taxonomia de erro — a UNICA fonte dos codigos e do status HTTP. O controlador, o teste puro e o
# verificador leem daqui: codigo novo sem entrada aqui e' erro de programacao (o teste puro cobre).
# ---------------------------------------------------------------------------
CODIGOS_DE_ERRO = {
    "payload_invalido": 400,
    "metodo_nao_permitido": 405,
    "operacao_nao_declarada": 404,
    "modelo_nao_declarado": 422,
    "campo_nao_declarado": 422,
    "campo_obrigatorio_ausente": 422,
    "valor_invalido": 422,
    "limite_excedido": 422,
    "idempotency_key_ausente": 422,
    "idempotency_key_invalida": 422,
    "dry_run_nao_suportado": 422,
    # Identidade da escrita (card TRE-W3-E01-T02): o valor de identidade ausente nao e' "campo
    # obrigatorio que faltou" — e' a RECUSA de operar sem saber sobre qual registro a operacao
    # fala. Codigo proprio para o consumidor poder distinguir as duas coisas (n8n: identidade
    # ausente = evento malformado; campo obrigatorio ausente = payload incompleto).
    "identificador_ausente": 422,
    # Campo declarado como FIXO na politica (ex.: `is_company` do `empresa_upsert`) e o chamador
    # mandou outro valor: recusa nomeada, nunca "ignora o que o chamador disse".
    "campo_fixo_divergente": 422,
    "valor_ambiguo": 409,
    "ambiente_nao_declarado": 503,
    "ambiente_nao_permitido": 503,
    "aprovacao_ausente": 503,
    "acesso_negado": 403,
    "politica_invalida": 500,
    "erro_interno": 500,
}

VERSAO_DO_ESQUEMA_SUPORTADA = "1"
TIPOS_DE_OPERACAO = ("leitura", "escrita")
ACOES_DE_ESCRITA = ("criar", "atualizar", "upsert")
FONTES_CONHECIDAS = ("capacidades",)
AMBIENTES_CONHECIDOS = ("dev", "homologacao", "producao")
# Ambientes que exigem aprovacao humana registrada, alem de constarem da politica (ADR-005).
AMBIENTES_COM_APROVACAO = ("homologacao", "producao")

FORMATO_DE_NOME_DE_OPERACAO = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
CHAVES_DE_TOPO = ("parametros", "idempotency_key", "correlation_id", "dry_run")
CHAVES_DE_PARAMETROS = {
    "leitura": ("modelo", "filtro", "campos", "ordem", "limite"),
    "escrita": ("modelo", "valores", "identificador"),
}
# Operacao de FONTE (nao toca modelo de negocio nenhum): aceita 'parametros' vazio e nada mais.
CHAVES_DE_PARAMETROS_DE_FONTE = ()

CAMINHO_PADRAO_DA_POLITICA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "politica_api.json"
)


class ErroApi(Exception):
    """Recusa nomeada da API controlada: `codigo` + mensagem + status HTTP."""

    def __init__(self, codigo, mensagem, http=None):
        self.codigo = codigo
        self.mensagem = mensagem
        self.http = http if http is not None else CODIGOS_DE_ERRO.get(codigo, 400)
        super().__init__("%s: %s" % (codigo, mensagem))


# ---------------------------------------------------------------------------
# Politica: carga e validacao (fail-closed — politica invalida nao serve nada)
# ---------------------------------------------------------------------------
def carregar_politica(caminho=None):
    """Le a politica do disco. Politica ilegivel/invalida levanta `ErroApi('politica_invalida')`."""
    caminho = caminho or CAMINHO_PADRAO_DA_POLITICA
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            politica = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ErroApi("politica_invalida", "politica ilegivel em %s (%s)" % (caminho, exc))
    problemas = validar_politica(politica)
    if problemas:
        raise ErroApi(
            "politica_invalida",
            "politica invalida em %s: %s" % (caminho, "; ".join(problemas[:5])),
        )
    politica["_caminho"] = caminho
    return politica


def validar_politica(politica):
    """Devolve a lista de problemas da politica (vazia = valida). Nao levanta excecao."""
    problemas = []
    if not isinstance(politica, dict):
        return ["politica nao e' um objeto JSON"]
    versao = politica.get("versao")
    if not isinstance(versao, str) or not versao.strip():
        problemas.append("campo 'versao' ausente ou vazio")
    esquema = politica.get("esquema")
    if esquema != VERSAO_DO_ESQUEMA_SUPORTADA:
        problemas.append(
            "campo 'esquema' diferente de %r (recebido %r)"
            % (VERSAO_DO_ESQUEMA_SUPORTADA, esquema)
        )
    ambientes = politica.get("ambientes_permitidos")
    if not isinstance(ambientes, list) or not ambientes:
        problemas.append("'ambientes_permitidos' tem de ser lista nao vazia")
    else:
        for amb in ambientes:
            if amb not in AMBIENTES_CONHECIDOS:
                problemas.append("ambiente desconhecido em 'ambientes_permitidos': %r" % (amb,))
    operacoes = politica.get("operacoes")
    if not isinstance(operacoes, list):
        problemas.append("'operacoes' tem de ser lista")
        return problemas
    vistas = set()
    for indice, op in enumerate(operacoes):
        if not isinstance(op, dict):
            problemas.append("operacao #%d nao e' objeto" % indice)
            continue
        nome = op.get("nome")
        if not isinstance(nome, str) or not FORMATO_DE_NOME_DE_OPERACAO.match(nome or ""):
            problemas.append("operacao #%d com nome fora do formato" % indice)
        elif nome in vistas:
            problemas.append("operacao duplicada: %s" % nome)
        else:
            vistas.add(nome)
        tipo = op.get("tipo")
        if tipo not in TIPOS_DE_OPERACAO:
            problemas.append("operacao %s com tipo invalido: %r" % (nome, tipo))
            continue
        fonte = op.get("fonte")
        if fonte is not None:
            if fonte not in FONTES_CONHECIDAS:
                problemas.append("operacao %s com fonte desconhecida: %r" % (nome, fonte))
            if tipo != "leitura":
                problemas.append("operacao %s: operacao de fonte so' pode ser de leitura" % nome)
            if "modelos" in op:
                problemas.append("operacao %s: operacao de fonte nao declara 'modelos'" % nome)
            continue
        modelos = op.get("modelos")
        if not isinstance(modelos, dict) or not modelos:
            problemas.append("operacao %s sem 'modelos' declarados" % nome)
            continue
        for modelo, declaracao in modelos.items():
            if not isinstance(declaracao, dict):
                problemas.append("operacao %s: modelo %s sem declaracao" % (nome, modelo))
                continue
            campos = declaracao.get("campos")
            if not isinstance(campos, list) or not campos:
                problemas.append("operacao %s: modelo %s sem 'campos'" % (nome, modelo))
                continue
            for chave in ("campos_de_filtro", "campos_de_ordem"):
                lista = declaracao.get(chave, [])
                if not isinstance(lista, list):
                    problemas.append("operacao %s: %s.%s nao e' lista" % (nome, modelo, chave))
                    continue
                for campo in lista:
                    if campo not in campos:
                        problemas.append(
                            "operacao %s: %s declara %s em %s fora de 'campos'"
                            % (nome, modelo, campo, chave)
                        )
            if tipo == "escrita":
                acao = declaracao.get("acao")
                if acao not in ACOES_DE_ESCRITA:
                    problemas.append(
                        "operacao %s: escrita em %s com acao invalida: %r" % (nome, modelo, acao)
                    )
                identidade = declaracao.get("campo_de_identidade")
                identidades = declaracao.get("campos_de_identidade")
                if identidades is not None:
                    if not isinstance(identidades, list) or not identidades:
                        problemas.append(
                            "operacao %s: %s declara 'campos_de_identidade' que nao e' lista nao "
                            "vazia (a ORDEM da lista e' a prioridade da identidade)" % (nome, modelo)
                        )
                        identidades = None
                    else:
                        vistas_identidade = []
                        for campo in identidades:
                            if campo not in campos:
                                problemas.append(
                                    "operacao %s: %s declara 'campos_de_identidade' com %s fora "
                                    "de 'campos'" % (nome, modelo, campo)
                                )
                            elif campo in vistas_identidade:
                                problemas.append(
                                    "operacao %s: %s repete o identificador %s em "
                                    "'campos_de_identidade'" % (nome, modelo, campo)
                                )
                            else:
                                vistas_identidade.append(campo)
                if acao in ("atualizar", "upsert"):
                    if not identidade and not identidades:
                        problemas.append(
                            "operacao %s: %s exige 'campo_de_identidade' (um campo) ou "
                            "'campos_de_identidade' (lista ordenada por prioridade) dentro de "
                            "'campos'" % (nome, modelo)
                        )
                    if identidade and identidade not in campos:
                        problemas.append(
                            "operacao %s: %s declara 'campo_de_identidade' %s fora de 'campos'"
                            % (nome, modelo, identidade)
                        )
                for campo in declaracao.get("campos_obrigatorios", []):
                    if campo not in campos:
                        problemas.append(
                            "operacao %s: %s declara obrigatorio %s fora de 'campos'"
                            % (nome, modelo, campo)
                        )
                fixos = declaracao.get("valores_fixos")
                if fixos is not None:
                    if not isinstance(fixos, dict) or not fixos:
                        problemas.append(
                            "operacao %s: %s declara 'valores_fixos' que nao e' objeto nao vazio"
                            % (nome, modelo)
                        )
                    else:
                        for campo in fixos:
                            if campo not in campos:
                                problemas.append(
                                    "operacao %s: %s declara valor fixo em %s fora de 'campos'"
                                    % (nome, modelo, campo)
                                )
                            if campo in declaracao.get("campos_obrigatorios", []):
                                problemas.append(
                                    "operacao %s: %s declara %s ao mesmo tempo obrigatorio e fixo "
                                    "(o chamador nao teria o que enviar)" % (nome, modelo, campo)
                                )
        if tipo == "escrita" and not op.get("requer_idempotency_key"):
            problemas.append(
                "operacao %s: escrita sem 'requer_idempotency_key' (doc 06 §7 / doc 13 §9)" % nome
            )
        if tipo == "leitura":
            limite = op.get("limite_de_registros")
            if not isinstance(limite, int) or limite <= 0:
                problemas.append("operacao %s: leitura sem 'limite_de_registros' positivo" % nome)
            if not isinstance(op.get("operadores_de_dominio"), list) or not op.get(
                "operadores_de_dominio"
            ):
                problemas.append("operacao %s: leitura sem 'operadores_de_dominio'" % nome)
    return problemas


def operacoes_declaradas(politica):
    """Nomes das operacoes declaradas, em ordem — o que a API serve, e so' isso."""
    return [
        str(op.get("nome"))
        for op in politica.get("operacoes", [])
        if isinstance(op, dict) and op.get("nome")
    ]


def capacidades(politica, ambiente):
    """Resposta da operacao de fonte `capacidades`: o que ESTA versao da politica declara.

    Nao le modelo nenhum: e' a sonda de saude do consumidor e o objeto que o verificador usa para
    provar que o que a API serve e' exatamente o que a politica declara.
    """
    return {
        "politica_versao": politica.get("versao"),
        "ambiente": ambiente,
        "ambientes_permitidos": list(politica.get("ambientes_permitidos", [])),
        "operacoes": [
            {
                "nome": op.get("nome"),
                "tipo": op.get("tipo"),
                "modelos": sorted(op.get("modelos", {})),
                "aceita_dry_run": bool(op.get("aceita_dry_run")),
                "requer_idempotency_key": bool(op.get("requer_idempotency_key")),
            }
            for op in politica.get("operacoes", [])
            if isinstance(op, dict)
        ],
    }


def operacao(politica, nome):
    for op in politica.get("operacoes", []):
        if isinstance(op, dict) and op.get("nome") == nome:
            return op
    return None


# ---------------------------------------------------------------------------
# Guarda de ambiente (ADR-005)
# ---------------------------------------------------------------------------
def aprovacao_valida(aprovacao, hoje=None):
    """Aprovacao registrada no formato do projeto: `card=...,aprovador=...,validade=AAAA-MM-DD`.

    Sem os tres campos, ou com validade vencida (ou de hoje para tras), NAO vale. Formato igual ao
    usado no caminho de aprovacao humana do projeto (card + quem + validade), para nao inventar
    contrato paralelo.
    """
    if not aprovacao or not isinstance(aprovacao, str):
        return False
    campos = {}
    for parte in aprovacao.split(","):
        if "=" in parte:
            chave, valor = parte.split("=", 1)
            campos[chave.strip().lower()] = valor.strip()
    if not all(campos.get(chave) for chave in ("card", "aprovador", "validade")):
        return False
    try:
        validade = datetime.date.fromisoformat(campos["validade"])
    except ValueError:
        return False
    hoje = hoje or datetime.date.today()
    return validade >= hoje


def validar_ambiente(politica, ambiente, aprovacao="", hoje=None):
    """Fail-closed: ambiente ausente/fora da politica nao atende; homologacao/producao exigem aprovacao."""
    if not ambiente or not isinstance(ambiente, str):
        raise ErroApi(
            "ambiente_nao_declarado",
            "ambiente nao declarado (parametro tf.api.ambiente) — a API nao atende sem ambiente declarado",
        )
    ambiente = ambiente.strip().lower()
    if ambiente not in AMBIENTES_CONHECIDOS:
        raise ErroApi("ambiente_nao_declarado", "ambiente desconhecido: %r" % ambiente)
    permitidos = [a.lower() for a in politica.get("ambientes_permitidos", [])]
    if ambiente not in permitidos:
        raise ErroApi(
            "ambiente_nao_permitido",
            "ambiente %s nao consta de ambientes_permitidos da politica (%s)"
            % (ambiente, ", ".join(permitidos) or "nenhum"),
        )
    if ambiente in AMBIENTES_COM_APROVACAO and not aprovacao_valida(aprovacao, hoje=hoje):
        raise ErroApi(
            "aprovacao_ausente",
            "ambiente %s exige aprovacao humana registrada (parametro tf.api.aprovacao "
            "com card/aprovador/validade) — ADR-005" % ambiente,
        )
    return ambiente


# ---------------------------------------------------------------------------
# Montagem do plano (a chamada e' validada ANTES de qualquer acesso a dado)
# ---------------------------------------------------------------------------
def _exigir(condicao, codigo, mensagem):
    if not condicao:
        raise ErroApi(codigo, mensagem)


def montar_plano(politica, nome_operacao, corpo, ambiente=None, aprovacao="", hoje=None):
    """Valida a chamada contra a politica e devolve o PLANO que o controlador executa.

    Nenhum acesso a dado acontece aqui: qualquer coisa fora do declarado vira `ErroApi`.
    """
    ambiente = validar_ambiente(politica, ambiente, aprovacao, hoje=hoje)

    _exigir(
        isinstance(nome_operacao, str) and nome_operacao,
        "operacao_nao_declarada",
        "operacao ausente na rota",
    )
    op = operacao(politica, nome_operacao)
    if op is None:
        raise ErroApi(
            "operacao_nao_declarada",
            "operacao %r nao esta declarada na politica %s (declaradas: %s)"
            % (nome_operacao, politica.get("versao"), ", ".join(operacoes_declaradas(politica))),
        )

    _exigir(isinstance(corpo, dict), "payload_invalido", "corpo da requisicao tem de ser objeto JSON")
    for chave in corpo:
        if chave not in CHAVES_DE_TOPO:
            raise ErroApi(
                "payload_invalido",
                "chave nao aceita no corpo: %r (aceitas: %s)" % (chave, ", ".join(CHAVES_DE_TOPO)),
            )

    parametros = corpo.get("parametros") or {}
    _exigir(isinstance(parametros, dict), "payload_invalido", "'parametros' tem de ser objeto")
    if op.get("fonte"):
        aceitas = CHAVES_DE_PARAMETROS_DE_FONTE
    else:
        aceitas = CHAVES_DE_PARAMETROS.get(op["tipo"], ())
    for chave in parametros:
        if chave not in aceitas:
            raise ErroApi(
                "payload_invalido",
                "parametro nao aceito na operacao %s: %r (aceitos: %s)"
                % (nome_operacao, chave, ", ".join(aceitas) or "nenhum"),
            )

    if op.get("fonte"):
        # Operacao de FONTE (capacidades): nao ha' modelo de negocio para resolver — e por isso
        # `modelo` nao e' aceito em `parametros` (a lista de chaves aceitas ja' recusou).
        modelo, declaracao = None, None
    else:
        modelos = op["modelos"]
        modelo = parametros.get("modelo")
        if modelo is None and len(modelos) == 1:
            modelo = next(iter(modelos))
        _exigir(
            isinstance(modelo, str) and modelo,
            "modelo_nao_declarado",
            "parametro 'modelo' obrigatorio",
        )
        if modelo not in modelos:
            raise ErroApi(
                "modelo_nao_declarado",
                "modelo %r nao esta declarado na operacao %s (declarados: %s)"
                % (modelo, nome_operacao, ", ".join(sorted(modelos))),
            )
        declaracao = modelos[modelo]

    idempotency_key = corpo.get("idempotency_key")
    if idempotency_key is not None:
        _exigir(
            isinstance(idempotency_key, str),
            "idempotency_key_invalida",
            "'idempotency_key' tem de ser texto",
        )
    if op["tipo"] == "escrita":
        if not idempotency_key or not str(idempotency_key).strip():
            raise ErroApi(
                "idempotency_key_ausente",
                "operacao de escrita %s exige 'idempotency_key' (doc 06 §7)" % nome_operacao,
            )
    if idempotency_key:
        padrao = politica.get("padroes", {}).get("idempotency_key")
        if padrao and not re.match(padrao, str(idempotency_key)):
            raise ErroApi(
                "idempotency_key_invalida",
                "'idempotency_key' fora do formato declarado (%s)" % padrao,
            )

    correlation_id = corpo.get("correlation_id")
    if correlation_id is not None:
        _exigir(
            isinstance(correlation_id, str) and len(correlation_id) <= 128,
            "payload_invalido",
            "'correlation_id' tem de ser texto de ate 128 caracteres",
        )

    dry_run = corpo.get("dry_run", False)
    _exigir(isinstance(dry_run, bool), "payload_invalido", "'dry_run' tem de ser booleano")
    if dry_run and not op.get("aceita_dry_run", False):
        raise ErroApi("dry_run_nao_suportado", "operacao %s nao aceita dry_run" % nome_operacao)

    plano = {
        "operacao": nome_operacao,
        "tipo": op["tipo"],
        "ambiente": ambiente,
        "politica_versao": politica.get("versao"),
        "modelo": modelo,
        "idempotency_key": str(idempotency_key) if idempotency_key else None,
        "correlation_id": correlation_id,
        "dry_run": bool(dry_run),
    }

    if op.get("fonte"):
        plano["acao"] = "capacidades"
        plano["fonte"] = op["fonte"]
    elif op["tipo"] == "leitura":
        plano["acao"] = "ler"
        plano.update(_plano_de_leitura(politica, op, declaracao, parametros))
    else:
        plano.update(_plano_de_escrita(op, declaracao, parametros))
    return plano


def _plano_de_leitura(politica, op, declaracao, parametros):
    campos = declaracao["campos"]
    pedidos = parametros.get("campos")
    if pedidos is None:
        campos_do_plano = list(campos)
    else:
        _exigir(isinstance(pedidos, list) and pedidos, "payload_invalido", "'campos' tem de ser lista nao vazia")
        for campo in pedidos:
            if campo not in campos:
                raise ErroApi(
                    "campo_nao_declarado",
                    "campo %r nao esta declarado para este modelo na operacao %s (declarados: %s)"
                    % (campo, op["nome"], ", ".join(campos)),
                )
        campos_do_plano = list(dict.fromkeys(pedidos))

    operadores = op.get("operadores_de_dominio", [])
    filtro = parametros.get("filtro") or []
    _exigir(isinstance(filtro, list), "payload_invalido", "'filtro' tem de ser lista")
    filtro_do_plano = []
    for condicao in filtro:
        _exigir(
            isinstance(condicao, list) and len(condicao) == 3,
            "payload_invalido",
            "cada condicao de 'filtro' tem de ser [campo, operador, valor]",
        )
        campo, operador, _valor = condicao
        if campo not in declaracao.get("campos_de_filtro", []):
            raise ErroApi(
                "campo_nao_declarado",
                "campo %r nao e' filtravel nesta operacao (filtraveis: %s)"
                % (campo, ", ".join(declaracao.get("campos_de_filtro", []))),
            )
        if operador not in operadores:
            raise ErroApi(
                "valor_invalido",
                "operador %r nao permitido nesta operacao (permitidos: %s)"
                % (operador, ", ".join(operadores)),
            )
        filtro_do_plano.append([campo, operador, condicao[2]])

    ordem = parametros.get("ordem")
    if ordem is not None:
        _exigir(isinstance(ordem, str) and ordem.strip(), "valor_invalido", "'ordem' tem de ser texto")
        partes = ordem.split()
        _exigir(len(partes) == 2, "valor_invalido", "'ordem' no formato '<campo> asc|desc'")
        campo, sentido = partes
        if campo not in declaracao.get("campos_de_ordem", []):
            raise ErroApi(
                "campo_nao_declarado",
                "campo %r nao e' ordenavel nesta operacao (ordenaveis: %s)"
                % (campo, ", ".join(declaracao.get("campos_de_ordem", []))),
            )
        if sentido.lower() not in ("asc", "desc"):
            raise ErroApi("valor_invalido", "sentido de ordenacao invalido: %r" % sentido)
        ordem = "%s %s" % (campo, sentido.lower())

    limite = parametros.get("limite")
    if limite is None:
        limite = min(op.get("limite_de_registros", 0), politica.get("padroes", {}).get(
            "limite_maximo_de_registros", op.get("limite_de_registros", 0)
        ))
    _exigir(
        isinstance(limite, int) and not isinstance(limite, bool) and limite > 0,
        "valor_invalido",
        "'limite' tem de ser inteiro positivo",
    )
    teto = min(
        op.get("limite_de_registros", 0),
        politica.get("padroes", {}).get("limite_maximo_de_registros", op.get("limite_de_registros", 0)),
    )
    if limite > teto:
        raise ErroApi("limite_excedido", "'limite' %d acima do teto declarado (%d)" % (limite, teto))

    return {
        "campos": campos_do_plano,
        "filtro": filtro_do_plano,
        "ordem": ordem,
        "limite": limite,
    }


def _plano_de_escrita(op, declaracao, parametros):
    valores = parametros.get("valores")
    _exigir(isinstance(valores, dict) and valores, "payload_invalido", "'valores' tem de ser objeto nao vazio")
    campos = declaracao["campos"]
    for campo in valores:
        if campo not in campos:
            raise ErroApi(
                "campo_nao_declarado",
                "campo %r nao esta declarado para escrita nesta operacao (declarados: %s)"
                % (campo, ", ".join(campos)),
            )
    for campo in declaracao.get("campos_obrigatorios", []):
        if campo not in valores:
            raise ErroApi(
                "campo_obrigatorio_ausente",
                "campo obrigatorio ausente na operacao %s: %s" % (op["nome"], campo),
            )
    fixos = declaracao.get("valores_fixos") or {}
    for campo, fixo in fixos.items():
        if campo in valores and valores[campo] != fixo:
            raise ErroApi(
                "campo_fixo_divergente",
                "campo %s e' fixo nesta operacao (%r, declarado na politica): o chamador nao "
                "decide esse valor (recebido %r)" % (campo, fixo, valores[campo]),
            )
    acao = declaracao["acao"]
    identidade = declaracao.get("campo_de_identidade")
    # A declaracao pode identificar por UM campo (`campo_de_identidade`, forma do E01-T01) ou por
    # uma LISTA ORDENADA (`campos_de_identidade`, forma do E01-T02): a ordem da lista e' a
    # prioridade da identidade — e' ela que o controlador percorre para casar o registro.
    if declaracao.get("campos_de_identidade"):
        ordem = list(declaracao["campos_de_identidade"])
        if identidade and identidade not in ordem:
            ordem.insert(0, identidade)
    else:
        ordem = [identidade] if identidade else []
    identificador = parametros.get("identificador")
    # Quem decide se 'identificador' existe e' a FORMA DA DECLARACAO, nao o tamanho da lista
    # resultante. A politica pode identificar por UM campo (`campo_de_identidade`, forma do
    # E01-T01 — e' para ela que `identificador` foi criado) ou por uma LISTA ORDENADA
    # (`campos_de_identidade`, forma do E01-T02): declarada por lista, a identidade vem dos VALORES.
    # Aceitar `identificador` numa lista de um elemento so' (caso da operacao de contato,
    # `contato_upsert`, TRE-W3-E01-T03) o descartaria EM SILENCIO — o oposto da regra da casa
    # ("nunca ignorar o que o chamador disse", a mesma razao de `campo_fixo_divergente`).
    por_lista = declaracao.get("campos_de_identidade") is not None
    if identificador is not None and (por_lista or len(ordem) != 1):
        raise ErroApi(
            "payload_invalido",
            "'identificador' so' vale para operacao que identifica por um campo; esta identifica "
            "por lista ordenada (%s) — informe os identificadores dentro de 'valores'"
            % ", ".join(ordem),
        )
    valores_de_identidade = {}
    if acao in ("atualizar", "upsert"):
        for campo in ordem:
            valor = valores.get(campo)
            if valor in (None, "") and campo == identidade and identificador not in (None, ""):
                valor = identificador
            if valor not in (None, ""):
                valores_de_identidade[campo] = valor
        _exigir(
            bool(valores_de_identidade),
            "identificador_ausente",
            "acao %s exige ao menos um identificador com valor, na ordem de prioridade da "
            "politica: %s" % (acao, ", ".join(ordem) or "nenhum declarado"),
        )
    valor_identidade = valores_de_identidade.get(identidade) if identidade else None
    valores_finais = dict(valores)
    # Valor fixo declarado sempre vale (a divergencia do chamador ja' foi recusada acima).
    valores_finais.update(fixos)
    return {
        "acao": acao,
        "valores": valores_finais,
        "valores_fixos": fixos,
        "campo_de_identidade": identidade,
        "valor_de_identidade": valor_identidade,
        "campos_de_identidade": ordem,
        "valores_de_identidade": valores_de_identidade,
    }
