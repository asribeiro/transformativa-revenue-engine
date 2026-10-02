#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gerador do corpus de respostas da fixture de dev (TRE-W6-E05-T01).

Produz o JSONL que o sink IMAP (`scripts/integracoes/sink-imap-dev.py --fixtures`) serve e o mapa de
EXPECTATIVA (rótulo por UID) que o aceite usa para conferir a classificação no banco. Determinístico:
mesmos UIDs, mesmos Message-IDs, mesmos corpos em toda rodada.

Uso:
  python3 scripts/integracoes/gerar-fixtures-respostas.py --saida /tmp/fx/respostas.jsonl \\
      --expectativa /tmp/fx/expectativa.json

Cada linha do JSONL: {"uid", "flags", "raw_base64", "rotulo", "esperado", "remetente_vinculado"}
(`remetente_vinculado: false` = remetente de propósito FORA de `contacts`, para medir SEM_VINCULO).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from email.message import EmailMessage
from email.utils import formatdate

NOSSO = "anderson.ribeiro@transformativa.com.br"
RODAPE_OPT_OUT = ("Se nao quiser receber meus e-mails, responda com \"remover meu e-mail da lista\".\n"
                  "-- \nAnderson Ribeiro\nTransformativa\n")


def mensagem(de: str, assunto: str, corpo: str, *, html: str | None = None,
             cabecalhos: dict | None = None, citacao: str | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"] = de
    msg["To"] = NOSSO
    msg["Subject"] = assunto
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = ("<fixture-resp-"
                         + hashlib.sha1(f"{de}|{assunto}|{corpo}".encode("utf-8")).hexdigest()[:16]
                         + "@cliente-demo.test>")
    for nome, valor in (cabecalhos or {}).items():
        msg[nome] = valor
    texto = corpo if not citacao else f"{corpo}\n\nEm 01/10/2026, Anderson Ribeiro escreveu:\n{citacao}\n"
    if html is not None:
        # HTML PURO (sem text/plain): o caso que o primitivo entrega como `corpo_html` vazio de texto.
        # Alternativa com text/plain de enchimento nao serve — o classificador veria o enchimento.
        msg.set_content(html, subtype="html")
    else:
        msg.set_content(texto)
    return msg.as_bytes()


def relatorio_de_entrega(de: str = "MAILER-DAEMON@titan.email") -> bytes:
    """Relatorio de entrega (bounce) montado a mao: `EmailMessage.set_content` nao aceita
    `multipart/report` (TypeError: set_content not valid on multipart), e o bounce PRECISA desse
    Content-Type para o classificador ser exercitado pelo caminho de cabecalho."""
    return (
        f"From: {de}\r\n"
        f"To: {NOSSO}\r\n"
        "Subject: Undeliverable: Proposta Transformativa\r\n"
        "Date: Thu, 02 Oct 2026 12:00:00 +0000\r\n"
        "Message-ID: <fixture-resp-bounce@titan.email>\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: multipart/report; report-type=delivery-status; boundary=\"bounce-fixture\"\r\n"
        "\r\n"
        "--bounce-fixture\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "This is the mail system at host titan.email.\r\n"
        "Delivery to the following recipient failed permanently: lead@cliente-demo.test\r\n"
        "--bounce-fixture--\r\n"
    ).encode("utf-8")


# (rotulo, esperado, remetente, vinculado, assunto, construtor)
CORPUS = [
    ("interesse", "INTERESSE", "joao@cliente-demo.test", True, "Re: Eficiencia operacional com IA",
     lambda: mensagem("joao@cliente-demo.test", "Re: Eficiencia operacional com IA",
                      "Oi Anderson, podemos conversar na quinta? Tenho interesse em entender melhor o escopo.",
                      citacao=RODAPE_OPT_OUT)),
    ("sem_interesse", "SEM_INTERESSE", "carlos@cliente-demo.test", True, "Re: Proposta",
     lambda: mensagem("carlos@cliente-demo.test", "Re: Proposta",
                      "Obrigado pelo contato, mas no momento nao temos interesse.", citacao=RODAPE_OPT_OUT)),
    ("opt_out", "OPT_OUT", "ana@cliente-demo.test", True, "Re: Proposta",
     lambda: mensagem("ana@cliente-demo.test", "Re: Proposta",
                      "Por favor remova meu e-mail da sua lista. Nao quero mais receber contato.")),
    ("bounce", "BOUNCE", "MAILER-DAEMON@titan.email", False, "Undeliverable: Proposta Transformativa",
     relatorio_de_entrega),
    ("auto_resposta", "AUTO_RESPOSTA", "contato@cliente-demo.test", True,
     "Resposta automatica: estou de ferias",
     lambda: mensagem("contato@cliente-demo.test", "Resposta automatica: estou de ferias",
                      "Estarei fora do escritorio ate 10/10.", cabecalhos={"Auto-Submitted": "auto-replied"})),
    ("ruido", "RUIDO", "news@fornecedor-demo.test", False,
     "Newsletter de outubro: novidades para sua empresa",
     lambda: mensagem("news@fornecedor-demo.test", "Newsletter de outubro: novidades para sua empresa",
                      "Prezado cliente, confira nossa promocao do mes.",
                      cabecalhos={"List-Unsubscribe": "<mailto:news@fornecedor-demo.test>"})),
    ("indefinido", "INDEFINIDO", "alguem@cliente-demo.test", True, "Re: Conversa",
     lambda: mensagem("alguem@cliente-demo.test", "Re: Conversa",
                      "Bom dia, encaminhei seu contato ao time responsavel.")),
    # O dente da citação: o descadastro existe SÓ no histórico citado (nosso próprio rodapé). Se a
    # limpeza de citação falhar, esta resposta vira OPT_OUT — e o aceite reprova.
    ("citacao_so_no_historico", "INDEFINIDO", "lead2@cliente-demo.test", True, "Re: Proposta",
     lambda: mensagem("lead2@cliente-demo.test", "Re: Proposta", "Ok, obrigado pelo retorno.",
                      citacao=RODAPE_OPT_OUT)),
    ("html_sem_texto", "INTERESSE", "maria@cliente-demo.test", True, "Re: Proposta",
     lambda: mensagem("maria@cliente-demo.test", "Re: Proposta", "",
                      html="<html><body><p>Podemos <b>conversar</b> amanha? Tenho <b>interesse</b>.</p></body></html>")),
    ("nao_resposta", "NAO_RESPOSTA", NOSSO, False, "Re: Proposta",
     lambda: mensagem(NOSSO, "Re: Proposta", "Confirmo o envio.")),
]


def main() -> int:
    p = argparse.ArgumentParser(description="Gerador das fixtures de respostas (TRE-W6-E05-T01)")
    p.add_argument("--saida", required=True, help="JSONL das mensagens cruas para o sink")
    p.add_argument("--expectativa", required=True, help="JSON do rótulo esperado por UID")
    args = p.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.saida)), exist_ok=True)
    expectativa = {}
    with open(args.saida, "w", encoding="utf-8") as fh:
        for indice, (rotulo, esperado, de, vinculado, assunto, construtor) in enumerate(CORPUS, start=1):
            crua = construtor()
            fh.write(json.dumps({"uid": indice, "flags": [], "rotulo": rotulo, "esperado": esperado,
                                 "remetente_vinculado": vinculado, "assunto": assunto,
                                 "raw_base64": base64.b64encode(crua).decode("ascii")},
                                ensure_ascii=False, sort_keys=True) + "\n")
            expectativa[str(indice)] = {"rotulo": rotulo, "esperado": esperado, "de": de,
                                       "remetente_vinculado": vinculado}
    with open(args.expectativa, "w", encoding="utf-8") as fh:
        json.dump(expectativa, fh, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps({"evento": "FIXTURES_GERADAS", "saida": args.saida, "mensagens": len(CORPUS),
                      "expectativa": args.expectativa}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
