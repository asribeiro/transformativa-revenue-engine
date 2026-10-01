#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite PURA do motor da API controlada — card TRE-W3-E01-T01 (`t_e0489efc`).

Por que uma suite pura: o motor (`odoo/addons/transformativa_sales_ai/api/motor.py`) NAO importa
`odoo` de proposito — e' ele quem decide o que existe, o que cada operacao pode tocar e sob que
ambiente a API atende. Aqui essa decisao e' exercitada SEM subir Odoo nenhum, o que da' tres coisas
que a suite dentro do Odoo nao da': roda em qualquer lugar (inclusive na fase de dentes do
verificador), roda em segundos, e cobre as recusas que so' aparecem no payload (nao no HTTP).

Uso:
    python3 scripts/odoo/testar_motor_api.py [--modulo-dir <dir>] [--politica <arquivo>]

Saida: um item por linha (`OK`/`FALHOU`), resumo em uma linha e exit code:
    0 = todos os itens OK     1 = houve falha
"""

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from datetime import date, timedelta

ITENS = 0
FALHAS = 0
FALHOU_ALGUM = False


def ok(texto):
    global ITENS
    ITENS += 1
    print("OK    %s" % texto)


def falhou(texto):
    global ITENS, FALHAS
    ITENS += 1
    FALHAS += 1
    print("FALHOU %s" % texto)


def verificar(condicao, texto):
    if condicao:
        ok(texto)
    else:
        falhou(texto)


def carregar_motor(caminho):
    spec = importlib.util.spec_from_file_location("tre_motor_api", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def recusa(motor, funcao, codigo_esperado, rotulo):
    """Roda `funcao` esperando `ErroApi` com o codigo esperado (fail-closed tem de ter nome)."""
    try:
        funcao()
    except motor.ErroApi as erro:
        if erro.codigo == codigo_esperado:
            ok("%s -> recusa %s (HTTP %d)" % (rotulo, erro.codigo, erro.http))
        else:
            falhou("%s -> recusou com %s (esperado %s)" % (rotulo, erro.codigo, codigo_esperado))
        return
    falhou("%s -> NAO recusou (esperado %s)" % (rotulo, codigo_esperado))


def politica_de_teste(caminho_de_teste, base):
    """Copia a politica real trocando o que o teste precisa, sem tocar no arquivo versionado."""
    with open(base, "r", encoding="utf-8") as fh:
        dados = json.load(fh)
    with open(caminho_de_teste, "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False, indent=2)
    return dados


def main():
    parser = argparse.ArgumentParser(description="Suite pura do motor da API controlada (TRE).")
    parser.add_argument("--modulo-dir", default=os.environ.get(
        "TRE_MODULO_DIR",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "odoo/addons/transformativa_sales_ai",
        ),
    ))
    parser.add_argument("--politica", default="")
    args = parser.parse_args()

    modulo_dir = os.path.abspath(args.modulo_dir)
    caminho_motor = os.path.join(modulo_dir, "api", "motor.py")
    caminho_politica = args.politica or os.path.join(modulo_dir, "api", "politica_api.json")
    caminho_politica_teste = os.path.join(modulo_dir, "tests", "politicas", "politica_de_teste.json")

    print("== carga ==")
    if not os.path.isfile(caminho_motor):
        falhou("motor ausente em %s" % caminho_motor)
        return resumo()
    motor = carregar_motor(caminho_motor)
    ok("motor carregado sem Odoo instalado (%s)" % caminho_motor)
    texto = open(caminho_motor, "r", encoding="utf-8").read()
    verificar("import odoo" not in texto and "from odoo" not in texto,
              "motor e' puro (nenhum import de odoo)")

    print("== politica real ==")
    politica = motor.carregar_politica(caminho_politica)
    ok("politica real carrega e valida: versao %s" % politica["versao"])
    verificar(politica["ambientes_permitidos"] == ["dev"],
              "politica real permite so' o dev (%s)" % politica["ambientes_permitidos"])
    nomes = motor.operacoes_declaradas(politica)
    verificar(nomes == ["sistema_capacidades", "crm_registros_ler"],
              "operacoes declaradas na politica real: %s" % ", ".join(nomes))
    verificar(not any(op.get("tipo") == "escrita" for op in politica["operacoes"]),
              "politica real nao declara escrita (operacoes de negocio sao dos cards E01-T02..T05)")

    print("== validacao da politica (politica ruim nao serve nada) ==")
    casos = [
        ({}, "politica vazia"),
        ({"esquema": "1", "versao": "x", "ambientes_permitidos": [], "operacoes": []},
         "sem ambiente permitido"),
        ({"esquema": "1", "versao": "x", "ambientes_permitidos": ["staging"], "operacoes": []},
         "ambiente desconhecido"),
        ({"esquema": "9", "versao": "x", "ambientes_permitidos": ["dev"], "operacoes": []},
         "esquema nao suportado"),
        ({"esquema": "1", "versao": "x", "ambientes_permitidos": ["dev"], "operacoes": [
            {"nome": "op", "tipo": "leitura", "modelos": {"res.partner": {"campos": ["id"]}},
             "limite_de_registros": 10, "operadores_de_dominio": ["="]}]},
         "nome de operacao fora do formato"),
        ({"esquema": "1", "versao": "x", "ambientes_permitidos": ["dev"], "operacoes": [
            {"nome": "op_valida", "tipo": "escrita",
             "modelos": {"res.partner": {"campos": ["name"], "acao": "upsert",
                                         "campo_de_identidade": "tf_cnpj"}}}]},
         "escrita com identidade fora de campos e sem idempotency_key"),
        ({"esquema": "1", "versao": "x", "ambientes_permitidos": ["dev"], "operacoes": [
            {"nome": "op_valida", "tipo": "leitura",
             "modelos": {"res.partner": {"campos": ["id"], "campos_de_filtro": ["email"]}},
             "limite_de_registros": 10, "operadores_de_dominio": ["="]}]},
         "filtro declara campo fora de 'campos'"),
        ({"esquema": "1", "versao": "x", "ambientes_permitidos": ["dev"], "operacoes": [
            {"nome": "op_valida", "tipo": "leitura",
             "modelos": {"res.partner": {"campos": ["id"]}},
             "limite_de_registros": 0, "operadores_de_dominio": ["="]}]},
         "leitura sem teto de registros"),
        ({"esquema": "1", "versao": "x", "ambientes_permitidos": ["dev"], "operacoes": [
            {"nome": "op_valida", "tipo": "leitura", "fonte": "magica"}]},
         "fonte desconhecida"),
        ({"esquema": "1", "versao": "x", "ambientes_permitidos": ["dev"], "operacoes": [
            {"nome": "op_valida", "tipo": "leitura", "fonte": "capacidades"},
            {"nome": "op_valida", "tipo": "leitura", "fonte": "capacidades"}]},
         "operacao duplicada"),
    ]
    for politica_ruim, rotulo in casos:
        problemas = motor.validar_politica(politica_ruim)
        verificar(bool(problemas), "%s e' reprovada (%s)" % (rotulo, problemas[0] if problemas else "sem motivo"))

    print("== politica ilegivel / invalida ==")
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "politica.json")
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write("{ isso nao e' json")
        recusa(motor, lambda: motor.carregar_politica(caminho), "politica_invalida", "JSON invalido")
        recusa(motor, lambda: motor.carregar_politica(os.path.join(tmp, "nao-existe.json")),
               "politica_invalida", "arquivo ausente")

    print("== guarda de ambiente (ADR-005) ==")
    recusa(motor, lambda: motor.validar_ambiente(politica, ""), "ambiente_nao_declarado",
           "ambiente nao declarado")
    recusa(motor, lambda: motor.validar_ambiente(politica, "staging"), "ambiente_nao_declarado",
           "ambiente desconhecido")
    recusa(motor, lambda: motor.validar_ambiente(politica, "producao"), "ambiente_nao_permitido",
           "producao fora da politica real")
    verificar(motor.validar_ambiente(politica, "dev") == "dev", "dev atende na politica real")
    politica_teste = json.load(open(caminho_politica_teste, encoding="utf-8"))
    recusa(motor, lambda: motor.validar_ambiente(politica_teste, "producao"),
           "aprovacao_ausente", "producao permitida mas sem aprovacao")
    aprovacao = "card=TRE-W3-E01-T01,aprovador=Anderson Ribeiro,validade=%s" % (
        date.today() + timedelta(days=7))
    verificar(motor.validar_ambiente(politica_teste, "producao", aprovacao) == "producao",
              "producao permitida COM aprovacao valida (a guarda e' portao, nao parede)")
    verificar(motor.aprovacao_valida(aprovacao), "aprovacao valida reconhecida")
    verificar(not motor.aprovacao_valida("card=x,aprovador=y,validade=2000-01-01"),
              "aprovacao vencida NAO vale")
    verificar(not motor.aprovacao_valida("aprovador=y,validade=2099-01-01"),
              "aprovacao sem card NAO vale")
    verificar(not motor.aprovacao_valida("card=x,aprovador=y,validade=31/12/2099"),
              "aprovacao com data fora do ISO NAO vale")

    print("== plano de leitura ==")
    plano = motor.montar_plano(politica, "crm_registros_ler",
                               {"parametros": {"modelo": "res.partner", "limite": 5,
                                               "campos": ["id", "name"]}}, ambiente="dev")
    verificar(plano["acao"] == "ler" and plano["modelo"] == "res.partner" and plano["limite"] == 5,
              "plano de leitura declarado e' montado (%s)" % plano["acao"])
    verificar(plano["campos"] == ["id", "name"], "campos do plano sao os pedidos e declarados")
    recusa(motor, lambda: motor.montar_plano(
        politica, "crm_registros_ler",
        {"parametros": {"modelo": "res.partner", "campos": ["email"]}}, ambiente="dev"),
        "campo_nao_declarado", "campo fora da declaracao na leitura")
    recusa(motor, lambda: motor.montar_plano(
        politica, "crm_registros_ler",
        {"parametros": {"modelo": "res.partner", "filtro": [["email", "=", "x"]]}}, ambiente="dev"),
        "campo_nao_declarado", "campo de filtro fora da declaracao")
    recusa(motor, lambda: motor.montar_plano(
        politica, "crm_registros_ler",
        {"parametros": {"modelo": "res.partner", "filtro": [["tf_cnpj", "like", "x"]]}},
        ambiente="dev"),
        "valor_invalido", "operador fora da declaracao")
    recusa(motor, lambda: motor.montar_plano(
        politica, "crm_registros_ler", {"parametros": {"modelo": "res.partner", "limite": 9999}},
        ambiente="dev"),
        "limite_excedido", "limite acima do teto")
    recusa(motor, lambda: motor.montar_plano(
        politica, "crm_registros_ler", {"parametros": {"modelo": "res.users"}}, ambiente="dev"),
        "modelo_nao_declarado", "modelo fora da declaracao")
    recusa(motor, lambda: motor.montar_plano(politica, "nao_existe", {}, ambiente="dev"),
           "operacao_nao_declarada", "operacao fora da declaracao")
    recusa(motor, lambda: motor.montar_plano(politica, "sistema_capacidades", {"forcar": 1},
                                             ambiente="dev"),
           "payload_invalido", "chave desconhecida no corpo")
    recusa(motor, lambda: motor.montar_plano(
        politica, "crm_registros_ler",
        {"parametros": {"modelo": "res.partner", "limite": "cinco"}}, ambiente="dev"),
        "valor_invalido", "limite nao inteiro")

    print("== plano de escrita (politica de teste) ==")
    verificar(not motor.validar_politica(politica_teste),
              "politica de teste (fixture) passa na propria validacao")
    recusa(motor, lambda: motor.montar_plano(
        politica_teste, "teste_criar_parceiro",
        {"parametros": {"valores": {"name": "x"}}}, ambiente="dev"),
        "idempotency_key_ausente", "escrita sem idempotency_key")
    recusa(motor, lambda: motor.montar_plano(
        politica_teste, "teste_criar_parceiro",
        {"idempotency_key": "curta!!", "parametros": {"valores": {"name": "x"}}}, ambiente="dev"),
        "idempotency_key_invalida", "idempotency_key fora do formato")
    recusa(motor, lambda: motor.montar_plano(
        politica_teste, "teste_criar_parceiro",
        {"idempotency_key": "tre-teste-0001", "parametros": {"valores": {"name": "x",
                                                                        "email": "a@b.c"}}},
        ambiente="dev"),
        "campo_nao_declarado", "campo fora da declaracao na escrita")
    recusa(motor, lambda: motor.montar_plano(
        politica_teste, "teste_upsert_parceiro",
        {"idempotency_key": "tre-teste-0002", "parametros": {"valores": {"name": "x"}}},
        ambiente="dev"),
        "campo_obrigatorio_ausente", "upsert sem o valor de identidade")
    plano = motor.montar_plano(
        politica_teste, "teste_upsert_parceiro",
        {"idempotency_key": "tre-teste-0003", "dry_run": True,
         "parametros": {"valores": {"name": "x", "tf_cnpj": "1"}}}, ambiente="dev")
    verificar(plano["acao"] == "upsert" and plano["dry_run"] is True
              and plano["valor_de_identidade"] == "1", "plano de upsert com dry-run montado")
    recusa(motor, lambda: motor.montar_plano(
        politica_teste, "sistema_capacidades", {"dry_run": "sim"}, ambiente="dev"),
        "payload_invalido", "dry_run nao booleano")
    recusa(motor, lambda: motor.montar_plano(
        politica_teste, "teste_ler_sem_dry_run",
        {"dry_run": True, "parametros": {"modelo": "res.partner"}}, ambiente="dev"),
        "dry_run_nao_suportado", "dry_run onde a politica declara que NAO aceita")
    plano = motor.montar_plano(
        politica_teste, "teste_ler_sem_dry_run",
        {"parametros": {"modelo": "res.partner"}}, ambiente="dev")
    verificar(plano["acao"] == "ler" and plano["dry_run"] is False,
              "sem dry_run a mesma operacao de leitura e' planejada normalmente")

    print("== operacao de fonte ==")
    plano = motor.montar_plano(politica, "sistema_capacidades", {}, ambiente="dev")
    verificar(plano["acao"] == "capacidades", "operacao de fonte nao resolve modelo de negocio")
    capacidades = motor.capacidades(politica, "dev")
    verificar(sorted(op["nome"] for op in capacidades["operacoes"])
              == ["crm_registros_ler", "sistema_capacidades"],
              "capacidades listam exatamente as operacoes declaradas")
    recusa(motor, lambda: motor.montar_plano(
        politica, "sistema_capacidades",
        {"parametros": {"modelo": "res.partner"}}, ambiente="dev"),
        "payload_invalido", "operacao de fonte nao aceita parametro")

    print("== taxonomia de erro ==")
    verificar(all(400 <= status < 600 for status in motor.CODIGOS_DE_ERRO.values()),
              "todo codigo de erro tem status 4xx/5xx (nenhum codigo responde OK)")
    verificar(len(set(motor.CODIGOS_DE_ERRO.values())) >= 5 and
              motor.CODIGOS_DE_ERRO["acesso_negado"] == 403 and
              motor.CODIGOS_DE_ERRO["ambiente_nao_permitido"] == 503,
              "codigos centrais com status declarado (403/503)")
    verificar(not any(chave in texto for chave in ("senha", "token", "api_key", "password")),
              "motor nao menciona material de credencial")
    return resumo()


def resumo():
    if FALHAS == 0:
        print("RESULTADO: MOTOR_API_OK (%d itens, 0 falhas)" % ITENS)
        return 0
    print("RESULTADO: MOTOR_API_FALHOU (%d itens, %d falha(s))" % (ITENS, FALHAS))
    return 1


if __name__ == "__main__":
    sys.exit(main())
