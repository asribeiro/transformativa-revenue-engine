#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite OFFLINE do Titan IMAP v1 (card TRE-W6-E01-T02).

Mede o componente `hermes/integracoes/titan/imap_titan.py` SEM abrir conexao nenhuma: a suite roda o
modulo por subprocesso com o ambiente que ela mesma monta e confere (a) a configuracao — completude,
inferencias, coerencia porta x TLS, guardas de ambiente; (b) o INVARIANTE DE LEITURA — a auditoria
propria do modulo nao acha escrita no codigo intacto e PEGA a violacao injetada numa copia; (c) o
segredo — a senha sentinela nao pode aparecer em stdout, relatorio, trilha nem em argumento de linha
de comando; (d) a trilha — dry-run sem conexao, chave obrigatoria, desfazer so de ingesta registrada;
(e) o contrato e a documentacao versionados.

A prova de conexao de verdade (TLS, LOGIN, EXAMINE, leitura e ingesta) vive no aceite
`scripts/integracoes/teste_imap_titan_aceite.sh`, que sobe um sink IMAP descartavel em 127.0.0.1.
Aqui, a regra e: se o modulo RECUSA, ele nao conecta — e a recusa e o que se mede; para provar que
nao houve conexao, os casos usam host nao-roteavel (192.0.2.1, TEST-NET-1) e porta sem ninguem
escutando: se o modulo tentasse conectar, o exit seria 1 (FALHOU) e nao a recusa esperada.

Uso: python3 scripts/integracoes/verificar_imap_titan.py [--raiz <dir>] [--modulo <caminho>]
Exit: 0 = IMAP_TITAN_SUITE_OK · 1 = IMAP_TITAN_SUITE_FALHOU (ou sem itens medidos).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

SENHA_SENTINELA = "senha-sentinela-dev-9f3a"
SENHA_REAL = "senha-sentinela-corporativa-7c1b"

ITENS = []
FALHAS = 0


def item(nome: str, condicao: bool, detalhe: str = "") -> None:
    global FALHAS
    ITENS.append(nome)
    if condicao:
        print(f"OK    {nome}")
    else:
        FALHAS += 1
        print(f"FALHOU {nome}" + (f"  -> {detalhe}" if detalhe else ""))


def rodar(modulo: str, argumentos: list, extra_env: dict, timeout: int = 30) -> tuple:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRE_TITAN_")}
    env.update({k: str(v) for k, v in extra_env.items()})
    proc = subprocess.run([sys.executable, modulo] + argumentos, capture_output=True, text=True,
                          env=env, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def relatorio(caminho: str) -> dict:
    with open(caminho, "r", encoding="utf-8") as fh:
        return json.load(fh)


def ler(caminho: str) -> str:
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def primeiro_evento(caminho: str) -> dict:
    eventos = relatorio(caminho).get("eventos", [])
    return eventos[0] if eventos else {}


def base_completa(host="192.0.2.1", porta="993", seguranca="implicit_tls",
                  usuario="anderson.ribeiro@transformativa.com.br"):
    """Configuracao completa contra o provedor (host TEST-NET: nao roteavel, nunca conectavel)."""
    return {
        "TRE_TITAN_IMAP_HOST": host,
        "TRE_TITAN_IMAP_PORT": porta,
        "TRE_TITAN_IMAP_SEGURANCA": seguranca,
        "TRE_TITAN_USER": usuario,
        "TRE_TITAN_PASSWORD": SENHA_REAL,
        "TRE_TITAN_APROVACAO_HUMANA": "APROV-2026-10-01-001",
        "TRE_TITAN_CAIXAS_PERMITIDAS": "INBOX",
    }


def sink_completa(porta="2993", seguranca="implicit_tls", caixa="INBOX"):
    """Configuracao completa contra o sink local de dev."""
    return {
        "TRE_TITAN_IMAP_HOST": "127.0.0.1",
        "TRE_TITAN_IMAP_PORT": porta,
        "TRE_TITAN_IMAP_SEGURANCA": seguranca,
        "TRE_TITAN_IMAP_CAIXA": caixa,
        "TRE_TITAN_USER": "sink-dev@dev.local",
        "TRE_TITAN_PASSWORD": SENHA_SENTINELA,
        "TRE_TITAN_CA": "/tmp/nao-existe-ainda.pem",
        "TRE_TITAN_DOMINIO_DEV": "dev.local",
        "TRE_TITAN_TIMEOUT": "5",
    }


def ident_outra_senha(rodar_fn, modulo: str, rel: str) -> str:
    """Mesma configuracao, outra senha: a identidade tem de ser a mesma (a senha nao entra no hash)."""
    env = sink_completa()
    env.pop("TRE_TITAN_CA")
    env["TRE_TITAN_PASSWORD"] = "outra-senha-qualquer-1234"
    rodar_fn(modulo, ["--conferir", "--relatorio", rel], env)
    return primeiro_evento(rel).get("identidade_config")


def main() -> int:
    p = argparse.ArgumentParser(description="Suite offline do Titan IMAP v1 (TRE-W6-E01-T02)")
    p.add_argument("--raiz", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    p.add_argument("--modulo", default=None)
    args = p.parse_args()
    raiz = args.raiz
    modulo = args.modulo or os.path.join(raiz, "hermes/integracoes/titan/imap_titan.py")
    contrato = os.path.join(raiz, "hermes/integracoes/titan/titan-imap-v1.json")
    env_exemplo = os.path.join(raiz, ".env.example")
    runbook = os.path.join(raiz, "docs/runbooks/titan-imap.md")
    doc_contrato = os.path.join(raiz, "docs/integrations/titan-imap-v1.md")
    sink = os.path.join(raiz, "scripts/integracoes/sink-imap-dev.py")

    trabalho = tempfile.mkdtemp(prefix="imap-suite-")
    print(f"# modulo:   {modulo}")
    print(f"# trabalho: {trabalho}")
    print("--- 1. arquivos, contrato e documentacao ---")

    item("1.1 modulo presente", os.path.isfile(modulo))
    item("1.2 contrato presente", os.path.isfile(contrato))
    try:
        contrato_json = json.loads(ler(contrato) or "{}")
    except json.JSONDecodeError:
        contrato_json = {}
    item("1.3 contrato e JSON com versao titan-imap-v1",
         contrato_json.get("versao") == "titan-imap-v1", str(contrato_json.get("versao")))
    matriz_contrato = json.dumps(contrato_json.get("matriz_porta_tls", {}), ensure_ascii=False)
    item("1.4 contrato traz a matriz do provedor (993 implicit_tls, 143 starttls)",
         "993" in matriz_contrato and "143" in matriz_contrato
         and "implicit_tls" in matriz_contrato and "starttls" in matriz_contrato, matriz_contrato[:120])
    invariante = json.dumps(contrato_json.get("invariante_de_leitura", {}), ensure_ascii=False)
    item("1.5 contrato declara o invariante de leitura (EXAMINE + BODY.PEEK)",
         "EXAMINE" in invariante and "PEEK" in invariante, invariante[:120])
    item("1.6 documento do contrato presente", os.path.isfile(doc_contrato))
    item("1.7 runbook presente", os.path.isfile(runbook))

    codigo = ler(modulo)
    exemplo = ler(env_exemplo)
    nomes = re.findall(r"^(TRE_TITAN_[A-Z_]+)=.*$", exemplo, re.M)
    obrigatorios = ["TRE_TITAN_IMAP_HOST", "TRE_TITAN_IMAP_PORT", "TRE_TITAN_IMAP_SEGURANCA",
                    "TRE_TITAN_IMAP_CAIXA", "TRE_TITAN_USER", "TRE_TITAN_PASSWORD", "TRE_TITAN_CA",
                    "TRE_TITAN_CAIXAS_PERMITIDAS"]
    faltando = [n for n in obrigatorios if n not in nomes]
    item("1.8 .env.example lista os nomes do IMAP (sem valor)", not faltando, str(faltando))
    item("1.9 nenhum valor de segredo versionado no .env.example",
         not re.search(r"^(TRE_TITAN_[A-Z_]+)=\S+", exemplo, re.M), "ha nome com valor atribuido")
    item("1.10 modulo nao recarrega credencial de arquivo versionado",
         ".env.example" not in codigo and "titan_calendar_sync" not in codigo)

    fonte_sink = ler(sink)
    item("1.11 sink de dev nao escuta fora do loopback por padrao",
         'default="127.0.0.1"' in fonte_sink)
    item("1.12 sink nao registra a senha no comando LOGIN (o defeito medido na rodada 1)",
         "<senha-oculta>" in fonte_sink and "LOGIN {usuario} <senha-oculta>" in fonte_sink)
    item("1.13 aceite e mutador presentes",
         os.path.isfile(os.path.join(raiz, "scripts/integracoes/teste_imap_titan_aceite.sh"))
         and os.path.isfile(os.path.join(raiz, "scripts/integracoes/mutar_imap_titan.py")))

    print("--- 2. configuracao: completude e inferencias ---")
    rel = os.path.join(trabalho, "r.json")
    rc, saida, _ = rodar(modulo, ["--planejar", "--relatorio", rel], {})
    dados = primeiro_evento(rel)
    item("2.1 --planejar sem ambiente nenhum: exit 0 e nao conecta", rc == 0, f"exit={rc}")
    item("2.2 --planejar declara config incompleta com os NOMES que faltam",
         dados.get("config_completa") is False and set(dados.get("faltantes", [])) ==
         {"TRE_TITAN_IMAP_HOST", "TRE_TITAN_IMAP_PORT", "TRE_TITAN_USER", "TRE_TITAN_PASSWORD"},
         str(dados.get("faltantes")))

    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], {})
    item("2.3 --conferir sem configuracao RECUSA (CONFIG_INCOMPLETA, exit 3)",
         rc == 3 and "CONFIG_INCOMPLETA" in saida, f"exit={rc}")

    env = base_completa(porta="143")
    env.pop("TRE_TITAN_IMAP_SEGURANCA")
    env.pop("TRE_TITAN_IMAP_CAIXA", None)
    rc, saida, _ = rodar(modulo, ["--planejar", "--relatorio", rel], env)
    dados = primeiro_evento(rel)
    item("2.4 porta 143 sem seguranca explicita infere starttls (e registra a inferencia)",
         rc == 0 and dados.get("seguranca") == "starttls"
         and any("inferida" in i for i in dados.get("inferencias", [])),
         f"exit={rc} seg={dados.get('seguranca')}")
    item("2.5 caixa ausente assume INBOX (e registra a inferencia)",
         dados.get("caixa") == "INBOX" and any("caixa padrao" in i for i in dados.get("inferencias", [])),
         str(dados.get("caixa")))
    item("2.6 limite padrao 50 e timeout padrao 15 registrados",
         dados.get("limite") == 50 and dados.get("timeout") == 15,
         f"limite={dados.get('limite')} timeout={dados.get('timeout')}")

    rc, saida, _ = rodar(modulo, ["--planejar", "--provar"], {})
    item("2.7 duas acoes no mesmo comando: exit 2 (uso)", rc == 2, f"exit={rc}")

    env = sink_completa()
    env["TRE_TITAN_CA"] = "/caminho/que/nao/existe.pem"
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("2.8 CA declarada e ausente RECUSA (CA_AUSENTE, exit 3)",
         rc == 3 and "CA_AUSENTE" in saida, f"exit={rc}")

    env = sink_completa()
    env.pop("TRE_TITAN_CA")
    env["TRE_TITAN_IMAP_LIMITE"] = "0"
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("2.9 limite fora de 1..500 RECUSA (LIMITE_INVALIDO, exit 3)",
         rc == 3 and "LIMITE_INVALIDO" in saida, f"exit={rc}")

    env = sink_completa(caixa="INBOX com espaco")
    env.pop("TRE_TITAN_CA")
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("2.10 nome de caixa invalido RECUSA (CAIXA_INVALIDA, exit 3)",
         rc == 3 and "CAIXA_INVALIDA" in saida, f"exit={rc}")

    print("--- 3. matriz porta x TLS ---")
    casos = [
        ("3.1 porta 110 (POP3) recusada como outro protocolo", "110", "implicit_tls",
         "PORTA_DE_OUTRO_PROTOCOLO", 3),
        ("3.2 porta 587 (SMTP) recusada como outro protocolo", "587", "starttls",
         "PORTA_DE_OUTRO_PROTOCOLO", 3),
        ("3.3 porta 993 com starttls recusada (CONFIG_INCOERENTE)", "993", "starttls",
         "CONFIG_INCOERENTE", 3),
        ("3.4 porta 143 com implicit_tls recusada (CONFIG_INCOERENTE)", "143", "implicit_tls",
         "CONFIG_INCOERENTE", 3),
        ("3.5 porta fora da matriz com host real recusada (PORTA_NAO_PREVISTA)", "2143", "starttls",
         "PORTA_NAO_PREVISTA", 3),
    ]
    for nome, porta, seg, motivo, esperado in casos:
        env = base_completa()
        env.update({"TRE_TITAN_IMAP_PORT": porta, "TRE_TITAN_IMAP_SEGURANCA": seg})
        rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
        item(nome, rc == esperado and motivo in saida, f"exit={rc} (esperado {esperado})")

    env = sink_completa()
    env.pop("TRE_TITAN_IMAP_SEGURANCA")
    env.pop("TRE_TITAN_CA")
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("3.6 porta de sink local exige seguranca explicita (SEGURANCA_AUSENTE)",
         rc == 3 and "SEGURANCA_AUSENTE" in saida, f"exit={rc}")
    env["TRE_TITAN_IMAP_SEGURANCA"] = "implicit_tls"
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("3.7 porta de sink local com seguranca explicita e aceita em dev (exit 0)",
         rc == 0, f"exit={rc} saida={saida.strip()[:120]}")

    env = base_completa(seguranca="nenhuma")
    rc, saida, _ = rodar(modulo, ["--planejar", "--relatorio", rel], env)
    item("3.8 --planejar registra a recusa em vez de esconder (texto claro)",
         rc == 0 and "TLS_OBRIGATORIO" in ler(rel), f"exit={rc}")
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("3.9 texto claro com host real RECUSA (TLS_OBRIGATORIO, exit 3)",
         rc == 3 and "TLS_OBRIGATORIO" in saida, f"exit={rc}")

    print("--- 4. guardas de ambiente (ADR-005) ---")
    env = base_completa()
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("4.1 dev com host real RECUSA antes de conectar (HOST_NAO_E_DEV, exit 3)",
         rc == 3 and "HOST_NAO_E_DEV" in saida, f"exit={rc}")
    rc, saida, _ = rodar(modulo, ["--provar", "--relatorio", rel], env)
    item("4.2 --provar em dev com host real tambem RECUSA (nada de conexao crua)",
         rc == 3 and "HOST_NAO_E_DEV" in saida, f"exit={rc}")
    rc, saida, _ = rodar(modulo, ["--listar", "--relatorio", rel], env)
    item("4.3 --listar em dev com host real tambem RECUSA",
         rc == 3 and "HOST_NAO_E_DEV" in saida, f"exit={rc}")
    rc, saida, _ = rodar(modulo, ["--ingerir", "--saida", trabalho, "--chave-idempotencia", "k",
                                 "--confirmo", "--relatorio", rel], env)
    item("4.4 --ingerir em dev com host real tambem RECUSA (nem le nem grava)",
         rc == 3 and "HOST_NAO_E_DEV" in saida, f"exit={rc}")

    env = sink_completa()
    env.pop("TRE_TITAN_CA")
    env["TRE_TITAN_USER"] = "anderson.ribeiro@transformativa.com.br"
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("4.5 dev com login de dominio corporativo RECUSA (USUARIO_NAO_DEV)",
         rc == 3 and "USUARIO_NAO_DEV" in saida, f"exit={rc}")

    env = base_completa()
    rc, saida, _ = rodar(modulo, ["--conferir", "--ambiente", "prod", "--relatorio", rel], env)
    item("4.6 prod RECUSA por desenho, exit 4 (nada nasce em producao)",
         rc == 4 and "PRODUCAO_NAO_E_DESTE_CARD" in saida, f"exit={rc}")

    env = base_completa()
    env.pop("TRE_TITAN_APROVACAO_HUMANA")
    rc, saida, _ = rodar(modulo, ["--conferir", "--ambiente", "homolog", "--relatorio", rel], env)
    item("4.7 homolog contra o provedor exige aprovacao registrada (HOMOLOG_SEM_APROVACAO)",
         rc == 3 and "HOMOLOG_SEM_APROVACAO" in saida, f"exit={rc}")

    env = base_completa()
    env.pop("TRE_TITAN_CAIXAS_PERMITIDAS")
    rc, saida, _ = rodar(modulo, ["--conferir", "--ambiente", "homolog", "--relatorio", rel], env)
    item("4.8 homolog sem lista de caixas nao le caixa nenhuma (CAIXA_NAO_PERMITIDA)",
         rc == 3 and "CAIXA_NAO_PERMITIDA" in saida, f"exit={rc}")

    env = base_completa()
    env["TRE_TITAN_CAIXAS_PERMITIDAS"] = "INBOX"
    env["TRE_TITAN_IMAP_CAIXA"] = "Sent"
    rc, saida, _ = rodar(modulo, ["--conferir", "--ambiente", "homolog", "--relatorio", rel], env)
    item("4.9 homolog: caixa fora da lista RECUSA (CAIXA_NAO_PERMITIDA)",
         rc == 3 and "CAIXA_NAO_PERMITIDA" in saida, f"exit={rc}")

    rc, saida, _ = rodar(modulo, ["--conferir", "--ambiente", "homolog", "--relatorio", rel],
                         base_completa())
    item("4.10 homolog com aprovacao + caixa na lista aprova (exit 0)",
         rc == 0 and relatorio(rel).get("veredito") == "CONFIGURACAO_OK",
         f"exit={rc} veredito={relatorio(rel).get('veredito')}")

    print("--- 5. invariante de leitura (o traco do card) ---")
    env = sink_completa()
    env["TRE_TITAN_CA"] = "/caminho/que/nao/existe.pem"
    rc, saida, _ = rodar(modulo, ["--planejar", "--relatorio", rel], env)
    dados = primeiro_evento(rel)
    item("5.1 a auditoria de leitura nao acha escrita no modulo intacto",
         rc == 0 and dados.get("auditoria_de_leitura") == "sem comando de escrita",
         str(dados.get("auditoria_de_leitura")))

    # A auditoria precisa PEGAR a violacao: copia do modulo com uma chamada de escrita dentro.
    copia = os.path.join(trabalho, "modulo-violado.py")
    with open(copia, "w", encoding="utf-8") as fh:
        fh.write(codigo.replace("    return sessao, medidas",
                                "    sessao.store('1', '+FLAGS', '(\\\\Seen)')\n    return sessao, medidas"))
    mudou = ler(copia) != codigo
    rc, saida, _ = rodar(copia, ["--conferir", "--relatorio", rel], sink_completa())
    item("5.2 a auditoria PEGA a violacao injetada e RECUSA antes de conectar (ESCRITA_NO_CODIGO)",
         mudou and rc == 3 and "ESCRITA_NO_CODIGO" in saida, f"exit={rc} mudou={mudou}")

    item("5.3 a caixa e aberta em modo leitura (readonly=True, nenhum readonly=False)",
         "readonly=True" in codigo and not re.search(r"readonly\s*=\s*Fal" + "se", codigo))
    item("5.4 o componente so busca conteudo por BODY.PEEK",
         "BODY.PEEK" in codigo and not re.search("BODY" + r"\[", codigo))
    item("5.5 o componente nao chama metodo de escrita na sessao",
         not re.search(r"\b" + "ses" + r"sao\.(store|expunge|delete|copy|move|append|create)\s*\(",
                       codigo))
    item("5.6 o sink de dev registra selecao, comandos de escrita e buscas sem PEEK",
         "selecoes" in fonte_sink and "total_comandos_de_escrita" in fonte_sink
         and "total_buscas_sem_peek" in fonte_sink)
    item("5.7 o sink aplica \\Seen na busca sem PEEK (prova a INTENCAO do cliente)",
         "buscas_sem_peek" in fonte_sink and fonte_sink.count("\\\\Seen") >= 1)

    print("--- 6. segredo ---")
    rel = os.path.join(trabalho, "segredo-rel.json")
    trilha = os.path.join(trabalho, "segredo-trilha.jsonl")
    env = base_completa()
    rc, saida, _ = rodar(modulo, ["--planejar", "--relatorio", rel, "--registro", trilha], env)
    juntos = saida + ler(rel) + ler(trilha)
    item("6.1 a senha nunca aparece na saida, no relatorio nem na trilha",
         SENHA_REAL not in juntos, "SENHA_VAZADA na suite")
    item("6.2 o relatorio mostra a senha como <oculta>", '"senha": "<oculta>"' in ler(rel))
    item("6.3 nao existe caminho de senha por argumento de linha de comando",
         "--senha" not in codigo and "--password" not in codigo)
    item("6.4 o modulo tem a checagem fail-closed de vazamento (SENHA_VAZADA, exit 5)",
         "SENHA_VAZADA" in codigo and "CODIGO_SEGREDO = 5" in codigo)

    papeis = ler(os.path.join(raiz, "hermes/policies/dev-harness.yaml"))
    item("6.5 politica: dev-harness tem TRE_TITAN_* em credenciais_proibidas",
         re.search(r"credenciais_proibidas:\s*\n\s*-\s*TRE_TITAN_\*", papeis) is not None)
    item("6.6 o codigo implementa a guarda que a politica implica (HOST_NAO_E_DEV)",
         "HOST_NAO_E_DEV" in codigo)

    print("--- 7. trilha, idempotencia e desfazer (sem conexao) ---")
    trilha = os.path.join(trabalho, "trilha.jsonl")
    env = sink_completa(porta="2994")
    env.pop("TRE_TITAN_CA")
    rc, saida, _ = rodar(modulo, ["--ingerir", "--saida", os.path.join(trabalho, "saida"),
                                 "--chave-idempotencia", "t:1", "--relatorio", rel,
                                 "--registro", trilha], env)
    item("7.1 --ingerir sem --confirmo e DRY_RUN exit 0 e nao conecta (porta 2994 sem ninguem)",
         rc == 0 and "DRY_RUN" in saida, f"exit={rc}")
    item("7.2 a trilha registra o DRY_RUN (auditoria do que NAO foi lido) e nenhum INGERIDO",
         os.path.isfile(trilha) and '"resultado": "DRY_RUN"' in ler(trilha)
         and '"resultado": "INGERIDO"' not in ler(trilha), ler(trilha)[:160])

    rc, saida, _ = rodar(modulo, ["--ingerir", "--saida", os.path.join(trabalho, "saida"),
                                 "--confirmo", "--relatorio", rel, "--registro", trilha], env)
    item("7.3 --confirmo sem chave-idempotencia RECUSA (exit 2: retry nao duplica)",
         rc == 2 and "chave-idempotencia" in saida, f"exit={rc}")

    rc, saida, _ = rodar(modulo, ["--ingerir", "--chave-idempotencia", "t:1", "--confirmo",
                                 "--relatorio", rel, "--registro", trilha], env)
    item("7.4 --ingerir sem --saida RECUSA (exit 2: ingesta sem destino e leitura perdida)",
         rc == 2 and "saida" in saida, f"exit={rc}")

    trilha_desfazer = os.path.join(trabalho, "trilha-desfazer.jsonl")
    with open(trilha_desfazer, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"evento": "INGERIR", "resultado": "INGERIDO",
                             "identidade_mensagem": "999:7", "quando": "2026-10-01T00:00:00Z"}) + "\n")
    rc, saida, _ = rodar(modulo, ["--desfazer", "999:7", "--relatorio", rel,
                                 "--registro", trilha_desfazer], env)
    item("7.5 --desfazer sem --confirmo e dry-run (exit 0, nada marcado)",
         rc == 0 and "DRY_RUN" in saida and '"DESFEITO"' not in ler(trilha_desfazer), f"exit={rc}")
    rc, saida, _ = rodar(modulo, ["--desfazer", "999:7", "--confirmo", "--relatorio", rel,
                                 "--registro", trilha_desfazer], env)
    item("7.6 --desfazer com --confirmo marca DESFEITO e preserva o INGERIDO original",
         rc == 0 and '"DESFEITO"' in ler(trilha_desfazer)
         and '"resultado": "INGERIDO"' in ler(trilha_desfazer), f"exit={rc}")
    rc, saida, _ = rodar(modulo, ["--desfazer", "999:7", "--confirmo", "--relatorio", rel,
                                 "--registro", trilha_desfazer], env)
    item("7.7 --desfazer repetido responde JA_DESFEITO sem gravar de novo",
         rc == 0 and "JA_DESFEITO" in saida, f"exit={rc}")
    rc, saida, _ = rodar(modulo, ["--desfazer", "999:8", "--confirmo", "--relatorio", rel,
                                 "--registro", trilha_desfazer], env)
    item("7.8 --desfazer de mensagem nunca ingerida nao inventa registro (exit 1)",
         rc == 1 and "NAO_ENCONTRADO" in saida, f"exit={rc}")

    # Regressao do defeito medido na rodada 1: o DRY_RUN do proprio --desfazer entra na trilha; se ele
    # contasse como alvo, um desfazer de mensagem nunca ingerida responderia DESFEITO.
    trilha_dry = os.path.join(trabalho, "trilha-dry.jsonl")
    with open(trilha_dry, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"evento": "DESFAZER", "resultado": "DRY_RUN",
                             "identidade_mensagem": "999:9", "quando": "2026-10-01T00:00:00Z"}) + "\n")
    rc, saida, _ = rodar(modulo, ["--desfazer", "999:9", "--confirmo", "--relatorio", rel,
                                 "--registro", trilha_dry], env)
    item("7.9 o DRY_RUN registrado nao serve de alvo para o --desfazer (NAO_ENCONTRADO, exit 1)",
         rc == 1 and "NAO_ENCONTRADO" in saida
         and '"resultado": "DESFEITO"' not in ler(trilha_dry), f"exit={rc}")

    print("--- 8. relatorios ---")
    rel = os.path.join(trabalho, "final-rel.json")
    env = sink_completa()
    env.pop("TRE_TITAN_CA")
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    dados = primeiro_evento(rel)
    item("8.1 relatorio final e JSON valido com veredito e identidade da configuracao",
         rc == 0 and relatorio(rel).get("veredito") == "CONFIGURACAO_OK"
         and bool(dados.get("identidade_config")), str(relatorio(rel).get("veredito")))
    item("8.2 identidade da configuracao nao inclui a senha (troca de senha nao muda a identidade)",
         dados.get("senha") == "<oculta>"
         and ident_outra_senha(rodar, modulo, rel) == dados.get("identidade_config"))
    item("8.3 o relatorio declara o modo de leitura usado (EXAMINE + BODY.PEEK)",
         "EXAMINE" in str(dados.get("modo_de_leitura")) and "PEEK" in str(dados.get("modo_de_leitura")),
         str(dados.get("modo_de_leitura")))

    print("---")
    total = len(ITENS)
    if FALHAS == 0 and total > 0:
        print(f"RESULTADO: IMAP_TITAN_SUITE_OK ({total} itens, 0 falhas)")
        return 0
    print(f"RESULTADO: IMAP_TITAN_SUITE_FALHOU ({total} itens, {FALHAS} falha(s))")
    return 1


if __name__ == "__main__":
    sys.exit(main())
