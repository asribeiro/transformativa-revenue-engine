# -*- coding: utf-8 -*-
"""Massa de parceiros do aceite da RECONCILIACAO (card TRE-W3-E04-T01).

Roda por `odoo shell -d <banco> --no-http < este_arquivo`, no trio DESCARTÁVEL do aceite
(nunca no dev/prod): o modulo precisa estar instalado e o banco e' o do descartavel.

O que este arquivo faz, por ESTADO (variavel `TRE_MASSA_ESTADO`):

  A  espelho SAUDAVEL     — parceiro com o `tf_company_id` e o `tf_cnpj` da organizacao
  B  espelho AUSENTE      — nao cria nada (o PostgreSQL aponta um parceiro que nao existe)
  C  ID CRUZADO divergente— o parceiro se declara o espelho de OUTRA organizacao do lote
  D  identidade FORTE     — `tf_cnpj` divergente do que a origem declara
  E  espelho ARQUIVADO    — parceiro com `active = False`
  F  pendencia + espelho saudavel (a fila e' semeada no PostgreSQL)
  G  porta unica FORA DO AR (o aceite derruba o servidor antes da rodada)

Todo parceiro e' criado com o sufixo do teste no nome e com `tf_company_id` de uma
organizacao do lote, para que a limpeza (unlink por `tf_company_id`) nao alcance parceiro
alheio. A limpeza roda ANTES da criacao, entao rodar duas vezes nao acumula.

Saida (uma linha por papel, lida pelo aceite):
    PARCEIRO <papel> <id>
"""
import os

ESTADO = os.environ.get("TRE_MASSA_ESTADO", "A").strip().upper()
ORGS = [o for o in os.environ.get("TRE_MASSA_ORGS", "").split(",") if o.strip()]
if not ORGS:
    raise SystemExit("TRE_MASSA_ORGS vazio: a massa precisa saber quais organizacoes sao do teste")

Parceiro = env["res.partner"].with_context(active_test=False)

# ------------------------------------------------------------------ limpeza do teste
alvos = Parceiro.search([("tf_company_id", "in", ORGS)])
quantos = len(alvos)
if alvos:
    alvos.unlink()
print("LIMPEZA %d parceiro(s) do teste removido(s)" % quantos)


def criar(papel, valores):
    valores = dict(valores)
    valores.setdefault("is_company", True)
    parceiro = env["res.partner"].create(valores)
    print("PARCEIRO %s %d" % (papel, parceiro.id))
    return parceiro


# ------------------------------------------------------------------ o parceiro de cada estado
if ESTADO == "A" or ESTADO in ("F", "G"):
    criar("a", {"name": "Alfa Espelho Ltda (aceite E04)", "tf_company_id": ORGS[0],
                "tf_cnpj": "11.222.333/0001-81", "tf_domain": "alfa.example"})
elif ESTADO == "B":
    print("PARCEIRO nenhum 0")
elif ESTADO == "C":
    if len(ORGS) < 2:
        raise SystemExit("estado C precisa de DUAS organizacoes do teste em TRE_MASSA_ORGS")
    criar("a", {"name": "Alfa (aponta outro espelho)", "tf_company_id": ORGS[1]})
elif ESTADO == "D":
    criar("a", {"name": "Alfa (cnpj divergente)", "tf_company_id": ORGS[0],
                "tf_cnpj": "99.999.999/0001-99"})
elif ESTADO == "E":
    # Arquivar DEPOIS de criar: `active=False` no `create` e' fragil entre versoes do Odoo, e a
    # massa precisa medir "espelho ARQUIVADO", nao "parceiro que nao nasceu".
    arquivado = criar("a", {"name": "Alfa (arquivado)", "tf_company_id": ORGS[0]})
    arquivado.active = False
else:
    raise SystemExit("estado desconhecido: %r (esperado A..G)" % ESTADO)

env.cr.commit()
print("MASSA_OK estado=%s" % ESTADO)
