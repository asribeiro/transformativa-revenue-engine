#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador offline da ingestao/classificacao de respostas (TRE-W6-E05-T01).

Roda SEM rede, SEM banco e SEM credencial: importa o componente
`hermes/agentes/respostas/ingestao_respostas.py`, exercita o classificador contra um corpus rotulado
(nenhum caso inventado pelo proprio codigo) e confere contrato, guardas de ambiente, invariantes de
fonte, trilha com checagem de segredo e derivacao da chave de idempotencia.

Uso:
  python3 scripts/agentes/verificar_ingestao_respostas.py            # suite
  python3 scripts/agentes/verificar_ingestao_respostas.py --prova-de-dente
        # muta uma COPIA temporaria do modulo/contrato e exige que o item esperado REPROVE
        # (verificador que nunca reprova e verde decorativo)

Exit: 0 = PASS · 1 = FALHOU · 2 = uso.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(RAIZ, "hermes", "agentes", "respostas"))
sys.path.insert(0, os.path.join(RAIZ, "hermes", "integracoes", "titan"))

MODULO = os.path.join(RAIZ, "hermes", "agentes", "respostas", "ingestao_respostas.py")
CONTRATO = os.path.join(RAIZ, "hermes", "agentes", "respostas", "ingestao-respostas-v1.json")

OK = []
FALHOU = []


def item(nome: str, condicao: bool, detalhe: str = "") -> None:
    if condicao:
        OK.append(nome)
        print(f"OK    {nome}")
    else:
        FALHOU.append(nome)
        print(f"FALHOU {nome} {('— ' + detalhe) if detalhe else ''}")


# ---------------------------------------------------------------------------------------------
# Corpus rotulado: (caso, remetente, assunto, texto, cabecalhos, categoria esperada)
# ---------------------------------------------------------------------------------------------
CORPUS = [
    ("interesse", "joao@cliente-demo.com.br", "Re: Eficiencia operacional com IA",
     "Oi Anderson, podemos conversar na quinta? Tenho interesse em entender melhor o escopo.",
     {}, "INTERESSE"),
    ("interesse_proposta", "maria@industria-demo.com.br", "Re: Proposta",
     "Gostaria de saber mais detalhes. Pode me mandar a proposta?", {}, "INTERESSE"),
    ("sem_interesse", "carlos@demo.com.br", "Re: Proposta",
     "Obrigado pelo contato, mas no momento nao temos interesse.", {}, "SEM_INTERESSE"),
    ("sem_interesse_en", "peter@demo-intl.com", "Re: Proposal",
     "Thanks, but we are not interested at this time.", {}, "SEM_INTERESSE"),
    ("opt_out", "ana@demo.com.br", "Re: Proposta",
     "Por favor remova meu e-mail da sua lista. Nao quero mais receber contato.", {}, "OPT_OUT"),
    ("opt_out_ingles", "bob@demo-intl.com", "Re: Proposal",
     "Please remove me from your list. Unsubscribe.", {}, "OPT_OUT"),
    ("bounce", "MAILER-DAEMON@titan.email", "Undeliverable: Proposta Transformativa",
     "This is the mail system at host titan.email.",
     {"Content-Type": "multipart/report; report-type=delivery-status"}, "BOUNCE"),
    ("auto_resposta", "contato@demo.com.br", "Resposta automatica: estou de ferias",
     "Estarei fora do escritorio ate 10/10.", {"Auto-Submitted": "auto-replied"}, "AUTO_RESPOSTA"),
    ("newsletter", "news@fornecedor-demo.com", "Newsletter de outubro: novidades para sua empresa",
     "Prezado cliente, confira nossa promocao do mes.", {"List-Unsubscribe": "<mailto:x@y>"}, "RUIDO"),
    ("indefinido", "alguem@demo.com.br", "Re: Conversa",
     "Bom dia, encaminhei seu contato ao time responsavel.", {}, "INDEFINIDO"),
    # O dente da citacao: o descadastro existe SO no historico citado (nosso proprio texto). A resposta
    # NAO pode virar OPT_OUT — se virar, a limpeza de citacao perdeu efeito.
    ("citacao_so_no_historico", "lead@demo.com.br", "Re: Proposta",
     "Ok, obrigado pelo retorno.\n\nEm 01/10/2026, Anderson Ribeiro escreveu:\n"
     "> Se nao quiser receber meus e-mails, responda com \"remover meu e-mail da lista\".",
     {}, "INDEFINIDO"),
    ("html_sem_texto", "lead2@demo.com.br", "Re: Proposta",
     "<html><body><p>Podemos <b>conversar</b> amanha? Tenho <b>interesse</b>.</p></body></html>",
     {"Content-Type": "text/html"}, "INTERESSE"),
]

ESPERADO_NAO_RESPOSTA = [
    ("remetente_proprio", "anderson.ribeiro@transformativa.com.br", "Re: Proposta",
     "Confirmo o envio.", {}),
    ("remetente_vazio", "", "Re: Proposta", "resposta sem remetente", {}),
]


def classificar_caso(mod, contrato, remetente, assunto, texto, cabecalhos):
    html = texto if "text/html" in " ".join(cabecalhos.values()).lower() else ""
    return mod.classificar({"de": remetente, "assunto": assunto, "texto": "" if html else texto,
                            "html": html, "cabecalhos": cabecalhos}, contrato,
                           "transformativa.com.br")


def suite(mod, contrato) -> None:
    # 1. contrato
    falhas = mod.validar_contrato(contrato)
    item("contrato valido (vocabulario fechado, regras com ordem/categoria/confianca)", not falhas,
         "; ".join(falhas))
    categorias = contrato["vocabulario"]["response_category"]
    item("vocabulario fechado cobre os vereditos esperados",
         {"INTERESSE", "SEM_INTERESSE", "OPT_OUT", "BOUNCE", "AUTO_RESPOSTA", "RUIDO",
          "INDEFINIDO", "NAO_RESPOSTA"}.issubset(set(categorias)))
    item("contrato declara as lacunas (o que o componente nao faz)",
         len(contrato.get("lacunas") or []) >= 5)

    # 2. corpus rotulado
    for nome, de, assunto, texto, cabecalhos, esperado in CORPUS:
        veredito = classificar_caso(mod, contrato, de, assunto, texto, cabecalhos)
        item(f"corpus/{nome}: categoria = {esperado}", veredito["categoria"] == esperado,
             f"obtido {veredito['categoria']} ({veredito['campos_que_casaram'][:2]})")

    # 3. prioridade: opt-out vence interesse no MESMO texto
    veredito = classificar_caso(mod, contrato, "lead@demo.com.br", "Re: Proposta",
                                "Tenho interesse, mas por favor remova meu e-mail da lista.", {})
    item("OPT_OUT vence INTERESSE quando os dois aparecem (fail-closed de compliance)",
         veredito["categoria"] == "OPT_OUT", f"obtido {veredito['categoria']}")

    # 4. excluidos nao viram resposta
    for nome, de, assunto, texto, cabecalhos in ESPERADO_NAO_RESPOSTA:
        veredito = classificar_caso(mod, contrato, de, assunto, texto, cabecalhos)
        item(f"exclusao/{nome}: NAO_RESPOSTA sem categoria comercial",
             veredito["categoria"] == "NAO_RESPOSTA" and veredito["excluida"] is True,
             f"obtido {veredito['categoria']}")

    # 5. sem regra => INDEFINIDO confianca 0 (nunca palpite)
    vazio = classificar_caso(mod, contrato, "x@demo.com.br", "Re: nada", "abc", {})
    item("sem regra casando: INDEFINIDO com confianca 0",
         vazio["categoria"] == "INDEFINIDO" and vazio["confianca"] == 0.0)

    # 6. categoria vem do contrato, nao do codigo: contrato com categoria faltante RECUSA
    quebrado = json.loads(json.dumps(contrato))
    quebrado["vocabulario"]["response_category"].remove("OPT_OUT")
    falhas = mod.validar_contrato(quebrado)
    item("categoria fora do vocabulario RECUSA o contrato",
         any("fora do vocabulario" in f for f in falhas), "; ".join(falhas))
    quebrado2 = json.loads(json.dumps(contrato))
    quebrado2["regras"] = []
    item("contrato sem regras RECUSA", bool(mod.validar_contrato(quebrado2)))

    # 7. guardas de ambiente
    def cfg(**env):
        base = {"TRE_AMBIENTE": "dev", "TRE_TITAN_IMAP_HOST": "127.0.0.1",
                "TRE_TITAN_IMAP_PORT": "2993", "TRE_TITAN_IMAP_SEGURANCA": "implicit_tls",
                "TRE_TITAN_USER": "sink-dev@dev.local", "TRE_TITAN_PASSWORD": "senha-de-teste",
                "TRE_RESPOSTAS_PORTA_BANCO":
                    "docker exec -i pg-resp-acc psql -U sales_ai -d sales_intelligence"}
        base.update(env)
        return mod.Configuracao(base)

    item("dev com sink loopback, login de dev e container local: sem pendencia",
         mod.validar(cfg()) == [], str(mod.validar(cfg())))
    item("dev recusa host real (HOST_NAO_E_DEV)",
         any(m == "HOST_NAO_E_DEV" for m, _ in mod.validar(cfg(TRE_TITAN_IMAP_HOST="imap.titan.email"))))
    item("dev recusa login corporativo (USUARIO_NAO_DEV)",
         any(m == "USUARIO_NAO_DEV" for m, _ in mod.validar(cfg(TRE_TITAN_USER="ai@transformativa.com.br"))))
    item("dev recusa prefixo de banco remoto (BANCO_NAO_E_DEV)",
         any(m == "BANCO_NAO_E_DEV" for m, _ in mod.validar(cfg(
             TRE_RESPOSTAS_PORTA_BANCO="ssh root@10.0.0.9 psql -U sales_ai -d sales_intelligence"))))
    item("dev sem porta de banco RECUSA (BANCO_NAO_DECLARADO)",
         any(m == "BANCO_NAO_DECLARADO" for m, _ in mod.validar(cfg(TRE_RESPOSTAS_PORTA_BANCO=""))))
    item("homolog sem aprovacao registrada RECUSA (HOMOLOG_SEM_APROVACAO)",
         any(m == "HOMOLOG_SEM_APROVACAO" for m, _ in mod.validar(cfg(TRE_AMBIENTE="homolog"))))
    item("homolog com aprovacao mas sem lista de caixas RECUSA (CAIXA_NAO_PERMITIDA)",
         any(m == "CAIXA_NAO_PERMITIDA" for m, _ in mod.validar(cfg(
             TRE_AMBIENTE="homolog", TRE_TITAN_APROVACAO_HUMANA="APROV-2026-10-02"))))
    prod = mod.validar(cfg(TRE_AMBIENTE="prod"))
    item("prod RECUSA por desenho (PRODUCAO_RECUSADA)", any(m == "PRODUCAO_RECUSADA" for m, _ in prod))
    item("config incompleta nomeia o que falta (CONFIG_INCOMPLETA)",
         any(m == "CONFIG_INCOMPLETA" for m, _ in mod.validar(cfg(TRE_TITAN_PASSWORD=""))))
    item("divergencia de porta nao numerica RECUSA (CONFIG_INCOERENTE)",
         any(m == "CONFIG_INCOERENTE" for m, _ in mod.validar(cfg(TRE_TITAN_IMAP_PORT="imap"))))

    # 8. invariantes da propria fonte (sem escrita IMAP, sem DDL/UPDATE/DELETE)
    violacoes = mod.auditar_fonte()
    item("auditoria da fonte: nenhuma escrita IMAP/DDL no modulo", violacoes == [], str(violacoes))
    with open(MODULO, "r", encoding="utf-8") as fh:
        fonte = fh.read()
    item("modulo nao marca a mensagem como lida nem altera flag",
         "\\Seen" not in fonte and "add_flag" not in fonte)
    # Guard que existe mas nao e CHAMADO e verde decorativo: o item afirma que a auditoria esta LIGADA
    # no caminho de ingesta, antes de qualquer conexao.
    corpo_main = fonte.split("def main(", 1)[-1]
    ligado = ('violacoes = auditar_fonte()\n    if violacoes:\n'
              '        saida.evento(evento="ESCRITA_NO_CODIGO"') in corpo_main
    item("caminho de ingesta EXECUTA a auditoria da fonte antes de conectar", ligado,
         "auditoria nao esta ligada no caminho de ingesta do main")

    # 9. chave de idempotencia derivada da identidade da mensagem
    chave = mod.chave_de({"identidade_mensagem": "999:7"})
    item("chave de idempotencia = resposta:<UIDVALIDITY:UID>", chave == "resposta:999:7", chave)
    item("chave e estavel entre rodadas (mesma identidade, mesma chave)",
         chave == mod.chave_de({"identidade_mensagem": "999:7"}))

    # 10. limpeza de citacao/assinatura e HTML
    citado = "Resposta nova.\n\nEm 01/10/2026, Anderson escreveu:\n> texto antigo com unsubscribe"
    item("citacao removida do que o classificador ve",
         "texto antigo" not in mod.normalizar(mod.remover_citacao(citado)))
    item("assinatura removida", "Assinatura" not in mod.limpar_assinatura("corpo\n-- \nAssinatura"))
    item("HTML vira texto", "podemos conversar" in mod.normalizar(
        mod.html_para_texto("<p>Podemos <b>conversar</b></p>")))
    # Caso medido no aceite: em mensagem só HTML o primitivo entrega as TAGS no campo de texto; sem
    # esta rota o classificador casa padrao contra markup e devolve INDEFINIDO.
    crua_html = {"identidade_mensagem": "999:9", "message_id": "<x>", "de": "maria@demo.test",
                 "assunto": "Re: Proposta", "data": "",
                 "corpo_texto": "<html><body><p>Podemos <b>conversar</b> amanha?</p></body></html>",
                 "corpo_html": "", "cabecalhos_completos": {"Content-Type": "text/html; charset=utf-8"}}
    analise_html = mod.mensagem_para_analise(crua_html)
    item("mensagem só HTML: markup nao vira texto (tags removidas antes da regra)",
         "<b>" not in analise_html["corpo_limpo"] and "podemos conversar" in mod.normalizar(analise_html["corpo_limpo"]),
         repr(analise_html["corpo_limpo"])[:120])
    # Regra que casa TUDO e defeito: padrao vazio tem de reprovar o contrato.
    quebrado3 = json.loads(json.dumps(contrato))
    quebrado3["regras"][4]["deteccao"]["padroes"] = [""]
    item("padrao vazio no contrato RECUSA (regra que casaria qualquer mensagem)",
         any("vazio" in f for f in mod.validar_contrato(quebrado3)))

    # 11. trilha local: senha nunca aparece (fail-closed exit 5)
    with tempfile.TemporaryDirectory() as tmp:
        segredo = "senha-do-sink-123456"
        saida = mod.Saida(None, os.path.join(tmp, "trilha.jsonl"), segredo)
        saida.evento(evento="TESTE", detalhe="linha inocente")
        with open(os.path.join(tmp, "trilha.jsonl"), "r", encoding="utf-8") as fh:
            trilha = fh.read()
        item("trilha nao contem a senha do IMAP", segredo not in trilha)
        try:
            saida.evento(evento="TESTE", detalhe=f"vazando {segredo}")
            item("gravacao com senha dentro RECUSA (SENHA_VAZADA, exit 5)", False, "gravou assim mesmo")
        except SystemExit as e:
            item("gravacao com senha dentro RECUSA (SENHA_VAZADA, exit 5)", e.code == 5, str(e.code))
        item("mascaramento da senha nao revela o valor",
             mod.mascarar(segredo) != segredo and segredo[:3] != mod.mascarar(segredo)[:3])

    # 12. --planejar/--conferir/--classificar nao abrem conexao (CLI real, sem rede)
    env = dict(os.environ)
    env.update({"TRE_AMBIENTE": "dev", "TRE_TITAN_IMAP_HOST": "127.0.0.1", "TRE_TITAN_IMAP_PORT": "2993",
                "TRE_TITAN_IMAP_SEGURANCA": "implicit_tls", "TRE_TITAN_USER": "sink-dev@dev.local",
                "TRE_TITAN_PASSWORD": "senha-de-teste",
                "TRE_RESPOSTAS_PORTA_BANCO":
                    "docker exec -i pg-resp-acc psql -U sales_ai -d sales_intelligence"})
    for acao in (["--planejar"], ["--conferir"]):
        proc = subprocess.run([sys.executable, MODULO] + acao, capture_output=True, text=True, env=env)
        item(f"CLI {acao[0]} sem rede/banco: exit 0", proc.returncode == 0,
             proc.stdout[-200:] + proc.stderr[-200:])
    proc = subprocess.run([sys.executable, MODULO, "--classificar", "Podemos conversar?"],
                          capture_output=True, text=True, env=env)
    item("CLI --classificar devolve o veredito",
         proc.returncode == 0 and "INTERESSE" in proc.stdout, proc.stdout[-200:])
    proc = subprocess.run([sys.executable, MODULO, "--ingerir"], capture_output=True, text=True, env=env)
    item("--ingerir sem --chave-idempotencia: exit 2 (uso)",
         proc.returncode == 2 and "chave-idempotencia" in proc.stdout, proc.stdout[-200:])
    proc = subprocess.run([sys.executable, MODULO, "--ingerir", "--chave-idempotencia", "x"],
                          capture_output=True, text=True, env=env)
    item("--ingerir sem --confirmo e DRY_RUN (exit 0, nada lido/gravado)",
         proc.returncode == 0 and "DRY_RUN" in proc.stdout and "CAIXA_LIDA" not in proc.stdout,
         proc.stdout[-200:])
    proc = subprocess.run([sys.executable, MODULO, "--ingerir", "--chave-idempotencia", "x",
                           "--confirmo", "--ambiente", "prod"], capture_output=True, text=True, env=env)
    item("prod RECUSA a ingesta com exit 4", proc.returncode == 4, str(proc.returncode))


# ---------------------------------------------------------------------------------------------
# Prova de dente: cada mutacao numa COPIA temporaria tem de reprovar o item que ela nomeia
# ---------------------------------------------------------------------------------------------
MUTACOES = [
    ("sem-exclusao-de-remetente-proprio", "exclusao/remetente_proprio",
     '"remetente_proprio" in campos and remetente_proprio and \\\n                dominio(de).endswith(remetente_proprio.lower())',
     "False"),
    ("limpeza-de-citacao-desligada", "corpus/citacao_so_no_historico",
     'corpo_limpo = limpar_assinatura(remover_citacao(texto_bruto))',
     'corpo_limpo = texto_bruto'),
    ("opt-out-sem-prioridade", "OPT_OUT vence INTERESSE",
     'for regra in sorted(contrato["regras"], key=lambda r: r["ordem"]):',
     'for regra in sorted(contrato["regras"], key=lambda r: -r["ordem"]):'),
    ("indefinido-vira-interesse", "sem regra casando",
     'return {"categoria": "INDEFINIDO", "intent": None, "sentiment": None, "confianca": 0.0,',
     'return {"categoria": "INTERESSE", "intent": None, "sentiment": None, "confianca": 0.9,'),
    ("guardas-de-dev-desligadas", "dev recusa host real",
     'if config.imap_host and not loopback(config.imap_host):',
     'if False and config.imap_host and not loopback(config.imap_host):'),
    ("sem-auditoria-da-fonte", "caminho de ingesta EXECUTA a auditoria da fonte antes de conectar",
     '\n    violacoes = auditar_fonte()\n    if violacoes:\n        saida.evento(evento="ESCRITA_NO_CODIGO"',
     '\n    violacoes = []\n    if violacoes:\n        saida.evento(evento="ESCRITA_NO_CODIGO"'),
]


def prova_de_dente(mod, contrato) -> None:
    for nome, item_alvo, alvo, troca in MUTACOES:
        with tempfile.TemporaryDirectory() as tmp:
            copia = os.path.join(tmp, nome.replace("-", "_") + ".py")
            with open(MODULO, "r", encoding="utf-8") as fh:
                fonte = fh.read()
            if alvo not in fonte:
                item(f"dente/{nome}: mutacao aplicavel", False, "texto-alvo nao encontrado no modulo")
                continue
            with open(copia, "w", encoding="utf-8") as fh:
                fh.write(fonte.replace(alvo, troca, 1))
            proc = subprocess.run([sys.executable, __file__, "--somente-modulo", copia],
                                  capture_output=True, text=True)
            reprovou = f"FALHOU {item_alvo}" in proc.stdout
            item(f"dente/{nome}: reprova '{item_alvo}'", reprovou,
                 "mutacao nao fez o item falhar (dente sem dente)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verificador offline da ingestao de respostas (TRE-W6-E05-T01)")
    parser.add_argument("--prova-de-dente", action="store_true")
    parser.add_argument("--somente-modulo", default=None, help="usa uma COPIA do modulo (uso interno do dente)")
    args = parser.parse_args()

    global MODULO
    if args.somente_modulo:
        MODULO = args.somente_modulo
    import importlib
    if args.somente_modulo:
        # carrega a copia mutada como modulo proprio (nome isolado, sem tocar o versionado)
        import importlib.util
        spec = importlib.util.spec_from_file_location("modulo_mutado", args.somente_modulo)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    else:
        import ingestao_respostas as mod  # noqa: PLC0415
    with open(CONTRATO, "r", encoding="utf-8") as fh:
        contrato = json.load(fh)

    suite(mod, contrato)
    if args.prova_de_dente and not args.somente_modulo:
        prova_de_dente(mod, contrato)

    print("---")
    if FALHOU:
        print(f"FALHOU ({len(OK) + len(FALHOU)} itens, {len(FALHOU)} falhas): {FALHOU}")
        return 1
    print(f"PASS ({len(OK)} itens, 0 falhas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
