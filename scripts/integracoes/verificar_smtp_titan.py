#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite OFFLINE do Titan SMTP v1 (card TRE-W6-E01-T01).

Mede o componente `hermes/integracoes/titan/smtp_titan.py` SEM abrir conexao nenhuma: a suite roda o
modulo por subprocesso com o ambiente que ela mesma monta e confere (a) a configuracao — completude,
inferencias, coerencia porta x TLS, guardas de ambiente; (b) o segredo — a senha sentinela nao pode
aparecer em stdout, relatorio, trilha nem em argumento de linha de comando; (c) a trilha — dry-run sem
conexao, idempotencia, desfazer; (d) o contrato e a documentacao versionados.

A prova de conexao de verdade (EHLO, TLS, AUTH, entrega) vive no aceite
`scripts/integracoes/teste_smtp_titan_aceite.sh`, que sobe um sink SMTP descartavel em 127.0.0.1.
Aqui, a regra e: se o modulo RECUSA, ele nao conecta — e a recusa e o que se mede; para provar que
nao houve conexao, os casos usam host nao-roteavel (192.0.2.1, TEST-NET-1) e porta sem ninguem
escutando: se o modulo tentasse conectar, o exit seria 1 (FALHOU) e nao a recusa esperada.

Uso: python3 scripts/integracoes/verificar_smtp_titan.py [--raiz <dir>] [--modulo <caminho>]
Exit: 0 = SMTP_TITAN_SUITE_OK · 1 = SMTP_TITAN_SUITE_FALHOU (ou sem itens medidos).
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


def base_completa(host="192.0.2.1", porta="465", seguranca="implicit_tls", remetente="outreach@transformativarevenue.com.br"):
    return {
        "TRE_TITAN_SMTP_HOST": host,
        "TRE_TITAN_SMTP_PORT": porta,
        "TRE_TITAN_SMTP_SEGURANCA": seguranca,
        "TRE_TITAN_USER": "anderson.ribeiro@transformativa.com.br",
        "TRE_TITAN_PASSWORD": SENHA_REAL,
        "TRE_TITAN_FROM": remetente,
        "TRE_TITAN_APROVACAO_HUMANA": "APROV-2026-10-01-001",
    }


def ler(caminho: str) -> str:
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def primeiro_evento(caminho: str) -> dict:
    eventos = relatorio(caminho).get("eventos", [])
    return eventos[0] if eventos else {}


def ident_outra_senha(rodar, modulo: str, rel: str) -> str:
    """Mesma configuracao, outra senha: a identidade tem de ser a mesma (a senha nao entra no hash)."""
    env = base_completa(host="127.0.0.1", porta="2465")
    env["TRE_TITAN_FROM"] = "no-reply@dev.local"
    env["TRE_TITAN_PASSWORD"] = "outra-senha-qualquer-1234"
    rodar(modulo, ["--conferir", "--relatorio", rel], env)
    return primeiro_evento(rel).get("identidade_config")


def main() -> int:
    p = argparse.ArgumentParser(description="Suite offline do Titan SMTP v1 (TRE-W6-E01-T01)")
    p.add_argument("--raiz", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    p.add_argument("--modulo", default=None)
    args = p.parse_args()
    raiz = args.raiz
    modulo = args.modulo or os.path.join(raiz, "hermes/integracoes/titan/smtp_titan.py")
    contrato = os.path.join(raiz, "hermes/integracoes/titan/titan-smtp-v1.json")
    env_exemplo = os.path.join(raiz, ".env.example")
    runbook = os.path.join(raiz, "docs/runbooks/titan-smtp.md")

    trabalho = tempfile.mkdtemp(prefix="smtp-suite-")
    print(f"# modulo:   {modulo}")
    print(f"# trabalho: {trabalho}")
    print("--- 1. arquivos, contrato e documentacao ---")

    item("1.1 modulo presente", os.path.isfile(modulo))
    item("1.2 contrato presente", os.path.isfile(contrato))
    corpo_contrato = ler(contrato)
    contrato_json = {}
    try:
        contrato_json = json.loads(corpo_contrato or "{}")
    except json.JSONDecodeError:
        pass
    item("1.3 contrato e JSON com versao titan-smtp-v1",
         contrato_json.get("versao") == "titan-smtp-v1", str(contrato_json.get("versao")))

    codigo = ler(modulo)
    matriz = re.findall(r"MATRIZ_PORTA_TLS = \{(.*?)\}", codigo, re.S)
    item("1.4 matriz porta x TLS do codigo bate com o contrato (465 implicit, 587 starttls)",
         bool(matriz) and "465" in matriz[0] and "587" in matriz[0]
         and "implicit_tls" in matriz[0] and "starttls" in matriz[0],
         matriz[0][:80] if matriz else "nao achei MATRIZ_PORTA_TLS")

    exemplo = ler(env_exemplo)
    nomes = [n for n in re.findall(r"^(TRE_TITAN_[A-Z_]+)=.*$", exemplo, re.M)]
    obrigatorios = ["TRE_TITAN_SMTP_HOST", "TRE_TITAN_SMTP_PORT", "TRE_TITAN_SMTP_SEGURANCA",
                    "TRE_TITAN_USER", "TRE_TITAN_PASSWORD", "TRE_TITAN_FROM", "TRE_TITAN_CA"]
    faltando = [n for n in obrigatorios if n not in nomes]
    item("1.5 .env.example lista os nomes do SMTP (sem valor)", not faltando, str(faltando))
    item("1.6 nenhum valor de segredo versionado no .env.example",
         not re.search(r"^(TRE_TITAN_[A-Z_]+)=\S+", exemplo, re.M),
         "ha nome com valor atribuido")
    item("1.7 runbook presente", os.path.isfile(runbook))
    item("1.10 documento do contrato presente",
         os.path.isfile(os.path.join(raiz, "docs/integrations/titan-smtp-v1.md")))
    item("1.8 sink de dev nao escuta fora do loopback por padrao",
         'default="127.0.0.1"' in ler(os.path.join(raiz, "scripts/integracoes/sink-smtp-dev.py")))
    item("1.9 modulo nao recarrega credencial de arquivo versionado",
         ".env.example" not in codigo and "titan_calendar_sync" not in codigo)

    print("--- 2. configuracao: completude e inferencias ---")
    rel = os.path.join(trabalho, "r.json")
    rc, saida, _ = rodar(modulo, ["--planejar", "--relatorio", rel], {})
    dados = primeiro_evento(rel)
    item("2.1 --planejar sem ambiente nenhum: exit 0 e nao conecta", rc == 0, f"exit={rc}")
    item("2.2 --planejar declara config incompleta com os NOMES que faltam",
         dados.get("config_completa") is False and set(dados.get("faltantes", [])) ==
         {"TRE_TITAN_SMTP_HOST", "TRE_TITAN_SMTP_PORT", "TRE_TITAN_USER", "TRE_TITAN_PASSWORD"},
         str(dados.get("faltantes")))

    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], {})
    item("2.3 --conferir sem configuracao RECUSA (CONFIG_INCOMPLETA, exit 3)",
         rc == 3 and "CONFIG_INCOMPLETA" in saida, f"exit={rc}")

    env = base_completa()
    env.pop("TRE_TITAN_FROM")
    env["TRE_TITAN_SMTP_PORT"] = "587"
    env.pop("TRE_TITAN_SMTP_SEGURANCA")
    rc, saida, _ = rodar(modulo, ["--planejar", "--relatorio", rel], env)
    dados = primeiro_evento(rel)
    item("2.4 porta 587 sem seguranca explicita infere starttls (e registra a inferencia)",
         rc == 0 and dados.get("seguranca") == "starttls"
         and any("inferida" in i for i in dados.get("inferencias", [])),
         f"exit={rc} seg={dados.get('seguranca')} inf={dados.get('inferencias')}")
    item("2.5 remetente assume o usuario quando TRE_TITAN_FROM esta ausente",
         dados.get("remetente") == env["TRE_TITAN_USER"], str(dados.get("remetente")))
    item("2.6 timeout padrao 15s registrado", dados.get("timeout") == 15, str(dados.get("timeout")))

    rc, saida, _ = rodar(modulo, ["--planejar", "--provar"], {})
    item("2.7 duas acoes no mesmo comando: exit 2 (uso)", rc == 2, f"exit={rc}")

    env = base_completa()
    env["TRE_TITAN_CA"] = "/caminho/que/nao/existe.pem"
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("2.8 CA declarada e ausente RECUSA (CA_AUSENTE, exit 3)",
         rc == 3 and "CA_AUSENTE" in saida, f"exit={rc}")

    print("--- 3. matriz porta x TLS ---")
    casos = [
        ("3.1 porta 25 recusada (PORTA_NAO_AUTORIZADA)", "25", "starttls", "PORTA_NAO_AUTORIZADA", 3),
        ("3.2 porta 465 com starttls recusada (CONFIG_INCOERENTE)", "465", "starttls", "CONFIG_INCOERENTE", 3),
        ("3.3 porta 587 com implicit_tls recusada (CONFIG_INCOERENTE)", "587", "implicit_tls", "CONFIG_INCOERENTE", 3),
        ("3.4 porta fora da matriz com host real recusada (PORTA_NAO_PREVISTA)", "1234", "starttls", "PORTA_NAO_PREVISTA", 3),
    ]
    for nome, porta, seg, motivo, esperado in casos:
        env = base_completa()
        env.update({"TRE_TITAN_SMTP_PORT": porta, "TRE_TITAN_SMTP_SEGURANCA": seg})
        rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
        item(nome, rc == esperado and motivo in saida, f"exit={rc} (esperado {esperado})")

    env = base_completa(host="127.0.0.1", porta="2465")
    env.pop("TRE_TITAN_SMTP_SEGURANCA")
    env["TRE_TITAN_FROM"] = "no-reply@dev.local"
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("3.5 porta de sink local exige seguranca explicita (SEGURANCA_AUSENTE)",
         rc == 3 and "SEGURANCA_AUSENTE" in saida, f"exit={rc}")
    env["TRE_TITAN_SMTP_SEGURANCA"] = "implicit_tls"
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("3.6 porta de sink local com seguranca explicita e aceita em dev (exit 0)",
         rc == 0, f"exit={rc} saida={saida.strip()[:120]}")

    print("--- 4. guardas de ambiente (ADR-005) ---")
    env = base_completa()
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("4.1 dev com host real RECUSA antes de conectar (HOST_NAO_E_DEV, exit 3)",
         rc == 3 and "HOST_NAO_E_DEV" in saida, f"exit={rc}")
    rc, saida, _ = rodar(modulo, ["--provar", "--relatorio", rel], env)
    item("4.2 --provar em dev com host real tambem RECUSA (nada de conexao crua)",
         rc == 3 and "HOST_NAO_E_DEV" in saida, f"exit={rc}")

    env = base_completa(host="127.0.0.1", porta="2465")
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("4.3 dev com remetente de dominio corporativo RECUSA (REMETENTE_NAO_DEV)",
         rc == 3 and "REMETENTE_NAO_DEV" in saida, f"exit={rc}")

    env = base_completa()
    rc, saida, _ = rodar(modulo, ["--conferir", "--ambiente", "prod", "--relatorio", rel], env)
    item("4.4 prod RECUSA por desenho, exit 4 (nada nasce em producao)",
         rc == 4 and "PRODUCAO_NAO_E_DESTE_CARD" in saida, f"exit={rc}")

    env = base_completa()
    env.pop("TRE_TITAN_APROVACAO_HUMANA")
    rc, saida, _ = rodar(modulo, ["--conferir", "--ambiente", "homolog", "--relatorio", rel], env)
    item("4.5 homolog contra o provedor exige aprovacao registrada (HOMOLOG_SEM_APROVACAO)",
         rc == 3 and "HOMOLOG_SEM_APROVACAO" in saida, f"exit={rc}")

    env = base_completa()
    env["TRE_TITAN_DESTINOS_PERMITIDOS"] = "teste@transformativa.com.br"
    rc, saida, _ = rodar(modulo, ["--enviar", "--ambiente", "homolog", "--para", "outro@exemplo.com",
                                 "--chave-idempotencia", "k1", "--relatorio", rel], env)
    item("4.6 homolog: destino fora da lista RECUSA (DESTINO_NAO_PERMITIDO)",
         rc == 3 and "DESTINO_NAO_PERMITIDO" in saida, f"exit={rc}")

    env = base_completa()
    rc, saida, _ = rodar(modulo, ["--enviar", "--ambiente", "homolog", "--para", "outro@exemplo.com",
                                 "--chave-idempotencia", "k1", "--relatorio", rel], env)
    item("4.7 homolog sem lista de destinos nao envia para ninguem (DESTINO_NAO_PERMITIDO)",
         rc == 3 and "DESTINO_NAO_PERMITIDO" in saida, f"exit={rc}")

    env = base_completa(host="127.0.0.1", porta="2465")
    env["TRE_TITAN_FROM"] = "no-reply@dev.local"
    rc, saida, _ = rodar(modulo, ["--enviar", "--para", "alguem@transformativarevenue.com.br",
                                 "--chave-idempotencia", "k2", "--confirmo", "--relatorio", rel], env)
    item("4.8 dev: destino fora do dominio de dev RECUSA (DESTINO_NAO_PERMITIDO)",
         rc == 3 and "DESTINO_NAO_PERMITIDO" in saida, f"exit={rc}")

    env = base_completa(seguranca="nenhuma")
    env["TRE_TITAN_SMTP_PORT"] = "587"
    rc, saida, _ = rodar(modulo, ["--planejar", "--relatorio", rel], env)
    item("4.9 planejar registra as recusas em vez de esconder (texto claro/TLS)",
         rc == 0 and "TLS_OBRIGATORIO" in ler(rel), f"exit={rc}")
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    item("4.10 texto claro com host real RECUSA (TLS_OBRIGATORIO)",
         rc == 3 and "TLS_OBRIGATORIO" in saida, f"exit={rc}")

    print("--- 5. segredo ---")
    rel = os.path.join(trabalho, "segredo-rel.json")
    trilha = os.path.join(trabalho, "segredo-trilha.jsonl")
    env = base_completa()
    rc, saida, _ = rodar(modulo, ["--planejar", "--relatorio", rel, "--registro", trilha], env)
    juntos = saida + ler(rel) + ler(trilha)
    item("5.1 a senha nunca aparece na saida, no relatorio nem na trilha",
         SENHA_REAL not in juntos, "SENHA_VAZADA na suite")
    item("5.2 o relatorio mostra a senha como <oculta>", '"senha": "<oculta>"' in ler(rel))
    item("5.3 nao existe caminho de senha por argumento de linha de comando",
         "--senha" not in codigo and "--password" not in codigo)
    item("5.4 o modulo tem a checagem fail-closed de vazamento (SENHA_VAZADA, exit 5)",
         "SENHA_VAZADA" in codigo and "CODIGO_SEGREDO = 5" in codigo)

    papeis = ler(os.path.join(raiz, "hermes/policies/dev-harness.yaml"))
    item("5.5 politica: dev-harness tem TRE_TITAN_* em credenciais_proibidas",
         re.search(r"credenciais_proibidas:\s*\n\s*-\s*TRE_TITAN_\*", papeis) is not None)
    item("5.6 o codigo implementa a guarda que a politica implica (HOST_NAO_E_DEV)",
         "HOST_NAO_E_DEV" in codigo)

    print("--- 6. trilha, idempotencia e desfazer (sem conexao) ---")
    rel = os.path.join(trabalho, "trilha-rel.json")
    trilha = os.path.join(trabalho, "trilha.jsonl")
    env = base_completa(host="127.0.0.1", porta="2466")
    env["TRE_TITAN_FROM"] = "no-reply@dev.local"
    rc, saida, _ = rodar(modulo, ["--enviar", "--para", "caixa@dev.local", "--assunto", "a",
                                 "--corpo", "b", "--chave-idempotencia", "t:1",
                                 "--relatorio", rel, "--registro", trilha], env)
    item("6.1 --enviar sem --confirmo e DRY_RUN exit 0 e nao conecta (porta 2466 sem ninguem)",
         rc == 0 and "DRY_RUN" in saida, f"exit={rc}")
    item("6.2 a trilha registra o DRY_RUN (auditoria do que NAO saiu) e nenhum ENVIADO",
         os.path.isfile(trilha) and '"resultado": "DRY_RUN"' in ler(trilha)
         and '"resultado": "ENVIADO"' not in ler(trilha), ler(trilha)[:160])

    rc, saida, _ = rodar(modulo, ["--enviar", "--para", "caixa@dev.local", "--confirmo",
                                 "--relatorio", rel, "--registro", trilha], env)
    item("6.3 --confirmo sem chave-idempotencia RECUSA (exit 2: retry nao duplica)",
         rc == 2 and "chave-idempotencia" in saida, f"exit={rc}")

    with open(trilha, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"evento": "ENVIAR", "resultado": "ENVIADO", "chave_idempotencia": "t:9",
                             "quando": "2026-10-01T00:00:00Z"}) + "\n")
    rc, saida, _ = rodar(modulo, ["--enviar", "--para", "caixa@dev.local", "--confirmo",
                                 "--chave-idempotencia", "t:9", "--relatorio", rel,
                                 "--registro", trilha], env)
    item("6.4 chave ja enviada -> JA_ENVIADO sem novo envio e sem conexao",
         rc == 0 and "JA_ENVIADO" in saida, f"exit={rc}")

    rc, saida, _ = rodar(modulo, ["--desfazer", "t:9", "--relatorio", rel, "--registro", trilha], env)
    item("6.5 --desfazer sem --confirmo e dry-run (nada gravado)",
         rc == 0 and "DESFAZER" in saida and "DRY_RUN" in saida, f"exit={rc}")
    rc, saida, _ = rodar(modulo, ["--desfazer", "t:9", "--confirmo", "--relatorio", rel,
                                 "--registro", trilha], env)
    item("6.6 --desfazer com --confirmo marca a trilha e preserva o registro original",
         rc == 0 and "DESFEITO" in saida and "ENVIADO" in ler(trilha), f"exit={rc}")
    rc, saida, _ = rodar(modulo, ["--desfazer", "nao-existe", "--confirmo", "--relatorio", rel,
                                 "--registro", trilha], env)
    item("6.7 --desfazer de chave inexistente nao inventa registro (exit 1)",
         rc == 1 and "NAO_ENCONTRADO" in saida, f"exit={rc}")

    print("--- 7. relatorios ---")
    rel = os.path.join(trabalho, "final-rel.json")
    env = base_completa(host="127.0.0.1", porta="2465")
    env["TRE_TITAN_FROM"] = "no-reply@dev.local"
    rc, saida, _ = rodar(modulo, ["--conferir", "--relatorio", rel], env)
    dados = primeiro_evento(rel)
    item("7.1 relatorio final e JSON valido com veredito e identidade da configuracao",
         rc == 0 and relatorio(rel).get("veredito") == "CONFIGURACAO_OK" and dados.get("identidade_config"),
         str(relatorio(rel).get("veredito")))
    item("7.2 identidade da configuracao nao inclui a senha (troca de senha nao muda a identidade)",
         dados.get("senha") == "<oculta>" and ident_outra_senha(rodar, modulo, rel) == dados.get("identidade_config"))

    print("---")
    total = len(ITENS)
    if FALHAS == 0 and total > 0:
        print(f"RESULTADO: SMTP_TITAN_SUITE_OK ({total} itens, 0 falhas)")
        return 0
    print(f"RESULTADO: SMTP_TITAN_SUITE_FALHOU ({total} itens, {FALHAS} falha(s))")
    return 1


if __name__ == "__main__":
    sys.exit(main())
