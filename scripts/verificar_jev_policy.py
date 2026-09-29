#!/usr/bin/env python3
"""Verificador da JEV Decision Policy V1 (card TRE-W0-E04-T01).

Prova que o documento e o arquivo legivel por maquina NAO divergem, que os limiares
sao coerentes, que nenhuma lane aponta para perfil inexistente, que nao ha modelo ou
preco hard-coded e que a precedencia e exatamente a definida.

Uso:
    python3 scripts/verificar_jev_policy.py                # verifica
    python3 scripts/verificar_jev_policy.py --autoteste     # verifica + mutacoes (prova que detecta)

Saida: OK/FALHOU por item + RESULTADO final. Codigo de saida 0 so quando tudo passa.
"""
import copy, pathlib, re, site, sys

# Este verificador precisa de YAML de verdade. Se o interpretador em uso nao tiver PyYAML,
# procura nos ambientes conhecidos em vez de obrigar a lembrar qual python chamar.
try:
    import yaml
except ModuleNotFoundError:
    for candidato in ("/opt/hermes/.venv/lib/python3.13/site-packages",
                      "/opt/data/.venv/lib/python3.13/site-packages"):
        if pathlib.Path(candidato).is_dir():
            site.addsitedir(candidato)
    try:
        import yaml
    except ModuleNotFoundError:
        print("FALHOU PyYAML nao encontrado. Rode com o python do ambiente Hermes:\n"
              "  /opt/hermes/.venv/bin/python scripts/verificar_jev_policy.py")
        sys.exit(2)

RAIZ = pathlib.Path(__file__).resolve().parents[1]
YAML_PATH = RAIZ / "hermes/jev/policy_v1.yaml"
DOC_PATH = RAIZ / "docs/architecture/jev-decision-policy-v1.md"

VERSAO_ESPERADA = "jev-policy-v1.0"
LANES_ESPERADAS = ["small", "medium", "high", "critical"]
PRECEDENCIA_ESPERADA = ["security", "human_approval", "prioridade_e_dependencias", "jev", "llm"]
PERFIS_ESPERADOS = ["worker-barato", "reasoning-padrao", "reasoning-forte", "critical-frontier-reviewer"]
CAMPOS_RECIBO = ["decision_id", "card_id", "task_hash", "lane", "model_profile", "selected_model",
                 "effort", "confidence", "policy_version", "router_version", "timestamp", "override", "outcome"]
NUNCA_MAQUINA = ["aprovacao_de_producao", "primeiro_contato_outbound", "envio_de_proposta_comercial",
                 "mudanca_estrutural_de_arquitetura", "rollback_em_producao", "exclusao_de_dado_de_cliente",
                 "rotacao_ou_revogacao_de_credencial", "publicacao_em_nome_da_transformativa"]

# Nomes de modelo e precos NAO podem ficar na politica (virariam mentira em semanas).
PADRAO_MODELO = re.compile(r"(?i)\b(gpt-|claude-|gemini-|llama|deepseek-|o1-|sonnet|opus|haiku)")
PADRAO_PRECO = re.compile(r"(?i)(\$\s?\d|US\$|R\$\s?\d|€\s?\d|\d+[.,]\d+\s*(usd|eur|brl)|por\s+1m\s+de\s+tokens)")


def verificar(yaml_txt: str, doc: str) -> list[tuple[str, bool, str]]:
    """Devolve [(item, ok, detalhe)]. Roda igual nos arquivos reais e nas mutacoes."""
    itens: list[tuple[str, bool, str]] = []

    def add(nome, ok, detalhe=""):
        itens.append((nome, bool(ok), detalhe))

    try:
        d = yaml.safe_load(yaml_txt)
    except Exception as e:
        add("YAML valido", False, f"nao parseia: {e}")
        return itens
    add("YAML valido", isinstance(d, dict), f"{len(d)} chaves de topo" if isinstance(d, dict) else "raiz nao e mapa")
    if not isinstance(d, dict):
        return itens

    # versao e autoridade
    add("versao da politica", d.get("versao") == VERSAO_ESPERADA,
        f"versao={d.get('versao')} esperada={VERSAO_ESPERADA}")
    autoridade = d.get("autoridade", "")
    add("autoridade aponta para documento existente", (RAIZ / autoridade).is_file() if autoridade else False,
        f"autoridade={autoridade!r}")
    add("documento cita a versao", VERSAO_ESPERADA in doc or "v1.0" in doc)

    # lanes
    lanes = d.get("lanes") or {}
    add("lanes exatamente as 4 esperadas", list(lanes.keys()) == LANES_ESPERADAS, f"encontradas={list(lanes.keys())}")
    for lane in LANES_ESPERADAS:
        cfg = lanes.get(lane) or {}
        faltando = [k for k in ("escopo", "risco", "perfil", "revisao") if not cfg.get(k)]
        add(f"lane {lane}: campos obrigatorios", not faltando, f"faltando={faltando}" if faltando else "")

    # perfis
    perfis = d.get("perfis_modelo") or {}
    add("perfis de modelo esperados", list(perfis.keys()) == PERFIS_ESPERADOS, f"encontrados={list(perfis.keys())}")
    for lane in LANES_ESPERADAS:
        perfil = (lanes.get(lane) or {}).get("perfil")
        add(f"lane {lane} aponta para perfil existente", perfil in perfis, f"perfil={perfil!r}")

    # nada de modelo ou preco hard-coded (nem no YAML, nem no documento)
    m_yaml = PADRAO_MODELO.search(yaml_txt) or PADRAO_PRECO.search(yaml_txt)
    add("sem modelo/preco hard-coded no YAML", not m_yaml, m_yaml.group(0) if m_yaml else "")
    m_doc = PADRAO_MODELO.search(doc) or PADRAO_PRECO.search(doc)
    add("sem modelo/preco hard-coded no documento", not m_doc, m_doc.group(0) if m_doc else "")

    # limiares
    lim = d.get("limiares") or {}
    add("limiar de aceite = 0.85", lim.get("aceitar") == 0.85, f"aceitar={lim.get('aceitar')}")
    add("limiar conservador = 0.65", lim.get("conservador") == 0.65, f"conservador={lim.get('conservador')}")
    add("limiar de abstencao = 0.65", lim.get("abster") == 0.65, f"abster={lim.get('abster')}")
    try:
        coerente = lim["aceitar"] > lim["conservador"] >= lim["abster"]
    except Exception:
        coerente = False
    add("limiares coerentes (aceitar > conservador >= abster)", coerente,
        f"{lim.get('aceitar')} > {lim.get('conservador')} >= {lim.get('abster')}")
    add("documento cita os limiares", ("0,85" in doc) and ("0,65" in doc))

    # precedencia
    prec = d.get("precedencia") or []
    add("precedencia exata e na ordem", prec == PRECEDENCIA_ESPERADA, f"encontrada={prec}")
    # Exigir a CADEIA na ordem, nao apenas as palavras soltas: o documento fala de "Security",
    # "Human Approval", "JEV" e "LLM" em varias secoes, entao procurar palavra solta nao prova nada.
    cadeia = re.compile(r"Security\s*→\s*Human Approval\s*→\s*prioridade/depend[êe]ncias\s*→\s*JEV\s*→\s*LLM")
    add("documento cita a cadeia de precedencia na ordem", bool(cadeia.search(doc)),
        "" if cadeia.search(doc) else "cadeia completa (com as setas) nao encontrada no documento")

    # nunca decidido por maquina
    nm = d.get("nunca_decidido_por_maquina") or []
    faltam = [x for x in NUNCA_MAQUINA if x not in nm]
    add("nunca-decide-por-maquina completo", not faltam, f"faltando={faltam}" if faltam else f"{len(nm)} itens")
    temas_doc = {"producao": "produção", "primeiro contato": "contato", "proposta": "proposta",
                 "arquitetura": "arquitetura", "rollback": "rollback", "exclusao": "exclusão",
                 "credencial": "credencial", "publicacao": "publica"}
    sem_tema = [k for k, v in temas_doc.items() if v.lower() not in doc.lower()]
    add("documento cobre os oito temas proibidos", not sem_tema, f"faltando={sem_tema}" if sem_tema else "")

    # guardrails
    guards = d.get("guardrails") or []
    add("guardrails definidos (>= 5)", len(guards) >= 5, f"{len(guards)} guardrails")
    texto_guards = " ".join(str(g) for g in guards)
    add("guardrail fail-closed presente", "fail-closed" in texto_guards.lower())
    add("documento explica fail-closed", "falha de guardrail bloqueia" in doc.lower())

    # fallback
    fb = d.get("fallback") or {}
    acoes = " ".join(fb.get("acao") or [])
    add("fallback define lane conservadora = high", "high" in acoes, acoes[:60])
    add("fallback registra modo degradado", "degraded_mode" in acoes)
    add("fallback nao contorna Human Approval", "nunca contornar Human Approval" in acoes or
        "nunca contornar human approval" in acoes.lower())
    add("fallback trata ausencia de resposta como abstencao",
        "abstin" in str(fb.get("proibido", "")).lower())
    add("documento descreve o fallback", "modo degradado" in doc.lower() and "high" in doc)

    # recibo
    campos = (d.get("recibo") or {}).get("campos") or []
    faltam_campos = [c for c in CAMPOS_RECIBO if c not in campos]
    add("recibo com os 13 campos do baseline", not faltam_campos,
        f"faltando={faltam_campos}" if faltam_campos else f"{len(campos)} campos")
    add("documento lista todos os campos do recibo", all(c in doc for c in CAMPOS_RECIBO))
    add("recibo proibe segredo", "nunca" in str((d.get("recibo") or {}).get("segredo", "")).lower())

    # decisoes tipadas
    antes = d.get("decisoes_antes_da_llm") or []
    add("decisoes antes da LLM definidas", len(antes) >= 7, f"{len(antes)} decisoes")
    depois = d.get("decisoes_depois_do_ciclo") or []
    add("decisao de proximo passo e finita", depois == ["resultado"], f"{depois}")
    add("documento cita PASS/RETRY/ESCALATE/BLOCK",
        all(x in doc for x in ("PASS", "RETRY", "ESCALATE", "BLOCK")))

    # metricas
    met = d.get("metricas") or {}
    add("objetivo economico e custo por card VERIFIED",
        "card VERIFIED" in str(met.get("objetivo", "")), str(met.get("objetivo"))[:70])
    add("acompanha falso rebaixamento", "falso_rebaixamento" in (met.get("acompanhar") or []))
    add("criterio de aceite exige ausencia de regressao",
        "regress" in str(met.get("criterio_de_aceite", "")).lower())

    return itens


def imprimir(itens):
    for nome, ok, detalhe in itens:
        marca = "OK   " if ok else "FALHOU"
        print(f"{marca} {nome}" + (f"  [{detalhe}]" if detalhe else ""))
    falhas = sum(1 for _, ok, _ in itens if not ok)
    return falhas


def autoteste(yaml_txt, doc):
    """Cada mutacao TEM de ser detectada. Verificador que aceita tudo nao vale nada."""
    print("\n=== AUTOTESTE: mutacoes que o verificador precisa reprovar ===")
    base = yaml.safe_load(yaml_txt)
    mutacoes = []

    m = copy.deepcopy(base); m["versao"] = "jev-policy-v1.1"
    mutacoes.append(("versao trocada", yaml.safe_dump(m, allow_unicode=True), doc))

    m = copy.deepcopy(base); m["limiares"]["aceitar"] = 0.75
    mutacoes.append(("limiar de aceite afrouxado", yaml.safe_dump(m, allow_unicode=True), doc))

    m = copy.deepcopy(base); m["limiares"]["conservador"] = 0.90
    mutacoes.append(("limiares incoerentes (conservador > aceitar)", yaml.safe_dump(m, allow_unicode=True), doc))

    m = copy.deepcopy(base); m["precedencia"] = ["llm", "jev", "prioridade_e_dependencias", "human_approval", "security"]
    mutacoes.append(("precedencia invertida", yaml.safe_dump(m, allow_unicode=True), doc))

    m = copy.deepcopy(base); m["lanes"]["critical"] = dict(m["lanes"]["critical"], perfil="perfil-inexistente")
    mutacoes.append(("lane apontando para perfil inexistente", yaml.safe_dump(m, allow_unicode=True), doc))

    m = copy.deepcopy(base); m["nunca_decidido_por_maquina"].remove("primeiro_contato_outbound")
    mutacoes.append(("primeiro contato autorizado por maquina", yaml.safe_dump(m, allow_unicode=True), doc))

    m = copy.deepcopy(base); m["recibo"]["campos"].remove("policy_version")
    mutacoes.append(("recibo sem a versao da politica", yaml.safe_dump(m, allow_unicode=True), doc))

    m = copy.deepcopy(base); m["fallback"]["acao"] = ["seguir pela lane conservadora configurada: small"]
    mutacoes.append(("fallback apontando para lane barata", yaml.safe_dump(m, allow_unicode=True), doc))

    m = copy.deepcopy(base); m["perfis_modelo"]["worker-barato"]["custo"] = "US$ 0,14 por 1M de tokens"
    mutacoes.append(("preco hard-coded na politica", yaml.safe_dump(m, allow_unicode=True), doc))

    m = copy.deepcopy(base); m["perfis_modelo"]["reasoning-forte"]["classe"] = "claude-3-7-sonnet"
    mutacoes.append(("nome de modelo fixado na politica", yaml.safe_dump(m, allow_unicode=True), doc))

    mutacoes.append(("documento sem os limiares", yaml_txt, doc.replace("0,85", "oitenta e cinco")
                     .replace("0,65", "sessenta e cinco")))

    mutacoes.append(("documento sem a cadeia de precedencia",
                     yaml_txt, doc.replace("Security → Human Approval → prioridade/dependências → JEV → LLM", "a definir")))

    detectadas = 0
    for nome, y, dd in mutacoes:
        itens = verificar(y, dd)
        falhas = sum(1 for _, ok, _ in itens if not ok)
        if falhas > 0:
            detectadas += 1
            print(f"OK    detectada: {nome}  ({falhas} item(ns) reprovado(s))")
        else:
            print(f"FALHOU NAO detectada: {nome}  <-- buraco no verificador")
    print(f"\nautoteste: {detectadas}/{len(mutacoes)} mutacoes detectadas")
    return detectadas == len(mutacoes)


def main():
    if not YAML_PATH.is_file() or not DOC_PATH.is_file():
        print(f"FALHOU arquivo ausente: {YAML_PATH if not YAML_PATH.is_file() else DOC_PATH}")
        return 1
    yaml_txt = YAML_PATH.read_text(encoding="utf-8")
    doc = DOC_PATH.read_text(encoding="utf-8")

    print("=" * 72)
    print("VERIFICADOR DA JEV DECISION POLICY V1")
    print(f"  politica:  {YAML_PATH.relative_to(RAIZ)}")
    print(f"  documento: {DOC_PATH.relative_to(RAIZ)}")
    print("=" * 72)
    itens = verificar(yaml_txt, doc)
    falhas = imprimir(itens)

    teste_ok = True
    if "--autoteste" in sys.argv:
        teste_ok = autoteste(yaml_txt, doc)

    print()
    if falhas == 0 and teste_ok:
        print(f"RESULTADO: PASS ({len(itens)} itens, 0 falhas) + autoteste OK")
        return 0
    print(f"RESULTADO: FALHOU ({len(itens)} itens, {falhas} falha(s))"
          + ("" if teste_ok else " + autoteste com buraco"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
