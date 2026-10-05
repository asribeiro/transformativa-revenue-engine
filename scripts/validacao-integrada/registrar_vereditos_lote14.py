#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registra os vereditos da validacao integrada do LOTE 14 (ordens 130+ da fila).

O lote 14 atravessa ondas: cada card vai para o artefato da SUA onda. O child e' gravado
na forma completa (validation_result + evidence + current_gate + validated_at +
validated_by + verification), preservando as chaves que o artefato ja' tinha — o W0, por
exemplo, tinha children com apenas {hermes_task_id, stage, evidence}.

Uso:  python3 scripts/validacao-integrada/registrar_vereditos_lote14.py [--dry-run]
"""
import json
import pathlib
import sys

BASE = pathlib.Path("control-plane/deliveries")
COMMIT = "2ce808bcfd1b62c2a399b98f0a78240de24db82b"
AMBIENTE = (
    "VPS Contabo vmi3619453: clone isolado /tmp/tre_lote13/repo do develop publicado "
    "(9e638f7/2ce808b, 765 arquivos); containers e bancos descartaveis por rodada; "
    "dev/homolog/prod conferidos intactos pelo proprio verificador (ADR-005). "
    "CORRECAO DE ESCOPO (05/10/2026, achado da validacao do card TRE-W0-E01-T03, conferido no codigo): "
    "no modo 'descartavel' do teste de backup o passo de BACKUP NAO le a base descartavel que o proprio "
    "teste cria e semeia — ele le o 'pg-sales-dev' VIVO, porque a precedencia do scripts/backup/"
    "lib-ambiente.sh poe o arquivo deploy/environments/dev.env (passo 2, que declara "
    "TRE_PG_SERVICO=pg-sales-dev) ACIMA do global TRE_PG_SERVICO exportado pelo teste (passo 3). O verde "
    "do modo descartavel prova, portanto, o ciclo de backup/restore contra o DEV VIVO em SOMENTE LEITURA "
    "(pg_dump/pg_dumpall read-only, filestore montado :ro) — nada foi escrito nos containers vivos — e "
    "NAO contra uma base hermética. O comentario do script ('roda o ciclo inteiro nele') e' falso na VPS."
)
BY = "Hermes — validacao integrada do lote 14 (cards da fila, ordem de criacao no board)"
DATA = "2026-10-05"

# onda -> artefato
ARTEFATO = {
    "W0": "W0-governanca-e-baseline.json",
    "W1": "W1-dados-e-dedup.json",
    "W2": "W2-odoo-e-seguranca.json",
    "W3": "W3-integracao-odoo-pg.json",
    "W4": "W4-agentes-e-e2e-sales-intelligence.json",
    "W5": "W5-scores-e-nba.json",
    "W6": "W6-outbound-e-canais.json",
    "W7": "W7-inbound-e-multicanal.json",
    "W8": "W8-analytics-e-efetividade.json",
    "W9": "W9-scoring-preditivo-e-nurture.json",
}

CARDS = [
    {
        "onda": "W0",
        "id": "TRE-W0-E01-T03-D01",
        "title": "DEFEITO [retroativo]: pg_isready respondia OK no servidor temporario do init",
        "task": "t_4e8ade57",
        "evidence": (
            "O conserto esta' nos DOIS arquivos que o card exige, e a espera e' a robusta: faz "
            "conexao real (`docker exec ... psql -tAc 'SELECT 1'`) e so' aceita o servidor se ele "
            "SOBREVIVER a uma segunda checagem 3s depois (`sleep 3` + mesma consulta) — que e' a "
            "definicao de 'servidor definitivo, nao o temporario da inicializacao'. O teste declara o "
            "item 'origem pronta (servidor definitivo, nao o temporario da inicializacao)'. MEDIDO NO "
            "VPS, em banco descartavel proprio: DUAS passadas identicas -> `RESULTADO: TESTE_OK (12 "
            "itens, 0 falhas)` (o card declarou 9 itens; a prova cresceu), diferindo apenas o nome do "
            "dump com carimbo de tempo e o sha256 dele, que nascem novos por rodada. LIMITACAO "
            "DECLARADA (decisao do dono, 05/10/2026): o DENTE por mutacao NAO morde neste item — mutando "
            "a espera para 'pronto sem checar' em copia isolada, o teste passou igual (`TESTE_OK 12/0`), "
            "porque no ambiente descartavel a corrida original (a janela em que o Postgres derruba o "
            "servidor temporario) nao se reproduz. Logo: conserto implementado e caminho feliz medido, "
            "mas a protecao da espera nao tem prova negativa. Construir o cenario que reproduz a corrida "
            "foi reconhecido como trabalho de card proprio, nao de validacao."
        ),
        "verification": {
            "verificador": "scripts/backup/teste-backup-restore.sh (modo descartavel, sem --ambiente)",
            "conserto": "scripts/backup/verificar-backup.sh + scripts/backup/teste-backup-restore.sh",
            "passadas": 2,
            "medicao": "TESTE_OK (12 itens, 0 falhas) x2",
            "dente": "NAO MORDE: mutacao 'pronto sem checar' nao reprova no ambiente descartavel",
            "volateis_declarados": "nome do dump (carimbo de tempo) e sha256 do dump",
            "log": "/tmp/tre_lote14/card130_{pass1,pass2,dente}.out",
        },
    },
    {
        "onda": "W0",
        "id": "TRE-W0-E01-T03-D02",
        "title": "DEFEITO [retroativo]: paste -d rotaciona caracteres e o SQL montado por psql -c ficou invalido",
        "task": "t_9f106731",
        "evidence": (
            "Conserto presente nos TRES scripts que o card exige, na forma declarada: o `UNION ALL` e' "
            "montado DENTRO do SQL com `string_agg` sobre `information_schema.tables` — "
            "`backup-tre.sh` (3 ocorrencias de string_agg, 0 uso ATIVO de `paste -d`; a unica mencao e' o "
            "comentario que documenta a armadilha), `verificar-backup.sh` (2 string_agg, 0 paste) e "
            "`restore-tre.sh` (2 string_agg, 0 paste). MEDIDO NO VPS, em banco descartavel proprio: duas "
            "passadas -> `RESULTADO: TESTE_OK (12 itens, 0 falhas)`, com a migracao aplicando as 12 "
            "tabelas e as contagens por tabela batendo (os mesmos runs do card 130 — mesmo verificador, "
            "mesma arvore; diferem apenas o nome do dump com carimbo de tempo e o sha256 dele). DENTE "
            "QUE MORDE, na classe exata do defeito: mutando o separador do SQL montado de volta para a "
            "forma literal (`' UNION ALL '` -> `' | '`) em copia isolada, o teste REPROVOU -> "
            "`RESULTADO: TESTE_FALHOU (11 itens, 4 falhas)`, com 'FALHOU nao consegui extrair as "
            "contagens por tabela (schema existe, com 12 tabelas)' e 'FALHOU artefato incompleto "
            "(contagens.txt ...)' — ou seja, a regua pega quem voltar a montar o SQL fora do banco."
        ),
        "verification": {
            "verificador": "scripts/backup/teste-backup-restore.sh (modo descartavel) + inspecao dos 3 scripts",
            "conserto": "scripts/backup/{backup-tre.sh,verificar-backup.sh,restore-tre.sh}",
            "passadas": 2,
            "medicao": "TESTE_OK (12 itens, 0 falhas) x2 (runs compartilhados com o card 130)",
            "dente": "MORDE: separador mutado -> TESTE_FALHOU (11 itens, 4 falhas), 'nao consegui extrair as contagens por tabela'",
            "volateis_declarados": "nome do dump (carimbo de tempo) e sha256 do dump",
            "log": "/tmp/tre_lote14/card131_dente.out (dente) + card130_{pass1,pass2}.out (passadas)",
        },
    },
    {
        "onda": "W0",
        "id": "TRE-W0-E01-T03-D03",
        "title": "DEFEITO [retroativo]: configurador ligava o destino externo mesmo com a prova reprovada",
        "task": "t_79fc6cab",
        "evidence": (
            "Conserto presente em `scripts/backup/configurar-destino-externo.sh`: o `RESULTADO: "
            "DESTINO_EXTERNO_OK` e a gravacao do `backup.env` estao AMBOS sob `[ \"$FALHAS\" -eq 0 ]`, e "
            "o arquivo de prova e' removido (`rm -f \"$PROBE\"`) — ou seja, o destino so' e' ligado depois "
            "da prova de ida e volta (sobe, confere, remove), nao por ter conseguido escrever o arquivo. "
            "MEDIDO POR MIM (caminho de RECUSA, o que o card exige), em copia isolada com os caminhos "
            "desviados para /tmp para nao encostar na configuracao real da VPS: com chaves FALSAS -> "
            "'FALHOU falha ao enviar o arquivo de teste (chaves ou endpoint incorretos?)', "
            "'PULADO o destino so e ligado no backup depois de uma prova de ida e volta bem-sucedida', "
            "'backup.env NAO foi alterado' e `RESULTADO: DESTINO_EXTERNO_FALHOU (2 falha(s))`; nenhum "
            "backup.env nasceu no diretorio. DENTE NA CLASSE EXATA DO DEFEITO: mutando os dois portoes "
            "para `if true`, as MESMAS chaves falsas passaram a produzir 'OK backup apontado para "
            "contabo:tre-backup' e `RESULTADO: DESTINO_EXTERNO_OK` — isto e', reproduz o defeito "
            "original; o portao e' o que separa chave errada de destino 'ligado'. O caminho POSITIVO "
            "(chaves verdadeiras -> DESTINO_EXTERNO_OK com bucket `tre-backup` lido de volta do Object "
            "Storage) foi rodado pelo proprio dono no prompt da VPS, como o card declara. OBSERVACAO "
            "DECLARADA (fora do escopo deste card): uma rodada com chave errada deixa o `rclone.conf` "
            "escrito (o arquivo de credencial e' gravado ANTES da prova); o `backup.env` — que e' o "
            "interruptor do destino — permanece intocado."
        ),
        "verification": {
            "verificador": "scripts/backup/configurar-destino-externo.sh (execucao do caminho de recusa) + inspecao do portao",
            "conserto": "portoes `[ \"$FALHAS\" -eq 0 ]` na gravacao do env e no RESULTADO; `rm -f $PROBE`",
            "medicao": "chaves falsas -> DESTINO_EXTERNO_FALHOU (2 falha(s)), backup.env NAO alterado",
            "dente": "MORDE: portoes mutados para `if true` -> mesmas chaves falsas dao DESTINO_EXTERNO_OK (reproduz o defeito)",
            "isolamento": "copia com ENVFILE e RCLONECONF desviados para /tmp — a configuracao real da VPS nao foi tocada",
            "caminho_positivo": "rodado pelo dono no prompt da VPS (declarado no card): bucket tre-backup + artefato lido de volta",
            "log": "/tmp/tre_lote14/d132/ (recusa) e d132m/ (dente)",
        },
    },
    {
        "onda": "W0",
        "id": "TRE-W0-E01-T03-D04",
        "title": "DEFEITO [retroativo]: manifesto do backup registrava 'externo: pendente' apos envio bem-sucedido",
        "task": "t_586daace",
        "evidence": (
            "Conserto presente em `scripts/backup/backup-tre.sh`, na forma que o card exige: o "
            "`rclone copy` sobe o artefato (linha 398), o resultado REAL do envio e' entao gravado no "
            "manifesto (`externo: enviado ($EXTERNO)` na linha 400 / `externo: falhou` na 408) e o "
            "manifesto e' REENVIADO (linha 401, com o comentario que registra a causa raiz: 'o manifesto "
            "sobe ANTES de saber o resultado do envio; reenvia para que a copia...'). `externo: pendente` "
            "ficou restrito ao caso legitimo 'sem destino configurado' (linha 412). EVIDENCIA MEDIDA NA "
            "FONTE (leitura de volta DO BUCKET, read-only, sem escrever nada): manifesto do artefato real "
            "de 05/10/2026 05:32Z (o do timer diario) em `contabo:tre-backup/tre_dev_20261005T053217Z/"
            "manifest.txt` registra `externo: enviado (contabo:tre-backup)`, com `banco: "
            "sales_intelligence` e `tabelas: 12`. LIMITACAO DECLARADA (decisao do dono, 05/10/2026): o "
            "DENTE por mutacao NAO foi provado nesta rodada — ele exigiria subir um artefato de prova ao "
            "bucket real (prefixo `prova-t03/`, como o card declara) para mostrar que a mutacao que pula o "
            "reenvio deixa a copia do bucket sem a linha `externo:`; o dono optou por nao escrever no "
            "Object Storage nesta rodada. Observacao de metodo: a checagem desse criterio no verificador "
            "existe SOMENTE no modo `--ambiente` (`if [ \"$MODO\" = \"ambiente\" ]` em "
            "teste-backup-restore.sh) — por isso os runs do modo descartavel (cards 130/131) nao a "
            "exercitam; quem fecha essa regua e' o card TRE-W1-E06-T01 (t_72672e48), o proximo da fila."
        ),
        "verification": {
            "verificador": "leitura de volta do bucket (rclone cat) + inspecao de backup-tre.sh (reenvio do manifesto)",
            "conserto": "backup-tre.sh linhas 398-401: rclone copy -> grava resultado real -> reenvia o manifesto",
            "medicao": "manifesto do artefato 2026-10-05T05:32Z no bucket: 'externo: enviado (contabo:tre-backup)'",
            "dente": "NAO PROVADO nesta rodada: exigiria escrita no bucket real (prefixo prova-t03/); decisao do dono = nao escrever",
            "efeitos_externos": "nenhum — medida 100% leitura (rclone cat/lsf)",
            "acoplamento": "a regua deste criterio so' roda no modo --ambiente: card TRE-W1-E06-T01 (t_72672e48)",
        },
    },
    {
        "onda": "W1",
        "id": "TRE-W1-E06-T01",
        "title": "Testar backup/restore (contra o banco real do ambiente dev)",
        "task": "t_72672e48",
        "evidence": (
            "Ciclo completo de backup/restore rodado contra o banco REAL do ambiente dev, na VPS, em "
            "container descartavel apenas para o RESTORE (producao recusada pelo proprio script, ADR-005). "
            "DUAS passadas identicas no modo `--ambiente dev`: `RESULTADO: TESTE_OK (14 itens, 0 falhas)` "
            "(o modo descartavel tem 12; os 2 a mais sao exatamente os do ambiente). Os itens medidos, "
            "todos OK: 'origem e o container do ambiente dev: pg-sales-dev (nenhum container descartavel de "
            "origem)'; 'postgres do ambiente dev responde (16.15)'; 'origem tem conteudo real: 12 tabelas "
            "em sales_intelligence (amostra: 4 linhas)'; 'backup do ambiente dev concluido (BACKUP_OK)'; "
            "'sha256 do dump confere com o manifesto'; 'manifesto registra a origem real do ambiente "
            "(pg-sales-dev)'; 'manifesto registra o envio externo (externo: enviado "
            "(contabo:tre-backup/prova-l14))'; 'teste de restore APROVADO (RESTORE_OK)'; 'contagens "
            "conferidas linha a linha na restauracao'; 'reverificacao do dump bom segue APROVADA'; e "
            "'container do ambiente intacto (mesmo Id e StartedAt antes/depois do ciclo)'. DENTE 1 (embutido "
            "no verificador, morde): 'teste negativo: dump truncado foi REPROVADO, como devia (8 falha(s) "
            "apontada(s))'. DENTE 2 (meu, o que o header do script promete — 'nunca cai para container "
            "descartavel em silencio'): apontando TRE_PG_SERVICO para um container inexistente, o teste "
            "REPROVOU -> `RESULTADO: TESTE_FALHOU`, 'container de origem pg-que-nao-existe NAO existe no "
            "ambiente dev — o modo ambiente nao cai para container descartavel', e ZERO containers "
            "descartaveis foram criados. Essa rodada FECHA a regua do card 133 (o criterio 'manifesto "
            "registra externo: enviado' so roda neste modo). EFEITOS EXTERNOS: um artefato de prova no "
            "prefixo `prova-l14/` do bucket (autorizado pelo dono) — lido de volta ('servico: pg-sales-dev', "
            "'externo: enviado') e REMOVIDO em seguida; os artefatos da rotina diaria nao foram tocados. "
            "ANOMALIA DE MODELAGEM DECLARADA: o artefato `W1-dados-e-dedup.json` tem 10 work_items e "
            "NENHUM item E06, enquanto W2..W7 todos tem um `E06-T01` — este card existe no board mas nao "
            "tinha casa no artefato da propria onda; foi registrado aqui como work_item novo (mesmo "
            "tratamento dado aos 4 cards de defeito do W0)."
        ),
        "verification": {
            "verificador": "scripts/backup/teste-backup-restore.sh --ambiente dev",
            "passadas": 2,
            "medicao": "TESTE_OK (14 itens, 0 falhas) x2 — origem = container real pg-sales-dev, sem container descartavel de origem",
            "dente": "MORDE x2: (a) dump truncado reprovado com 8 falhas (embutido); (b) container do ambiente inexistente -> TESTE_FALHOU sem fallback",
            "adr005": "container do ambiente intacto (mesmo Id e StartedAt antes/depois); producao recusada pelo script",
            "efeitos_externos": "artefato de prova no prefixo prova-l14/ (autorizado) — removido com rclone purge; rotina diaria intocada",
            "baseline_vps": "5 containers / 7 volumes / 0 dangling / 4 redes",
            "fecha": "TRE-W0-E01-T03-D04 (t_586daace) — criterio 'externo: enviado' medido de verdade",
            "log": "/tmp/tre_lote14/card134_{pass1,pass2}.out (VPS)",
        },
    },
    # ----- validados em paralelo por 3 subagentes (evidencia conferida na fonte por mim) -----
    {
        "onda": "W0",
        "id": "TRE-W0-E01-T03",
        "title": "Definir backup e rollback baseline",
        "task": "t_c8e69f74",
        "evidence": (
            "Exigencia literal do card (board): 'procedimento documentado; restore testavel. RUNBOOK: "
            "backup, restore, rollback.' Os dois lados medidos: (a) PROCEDIMENTO DOCUMENTADO — "
            "`docs/runbooks/backup-restore-rollback.md` existe no develop publicado (65 KB); (b) RESTORE "
            "TESTAVEL — duas passadas identicas do verificador da familia `scripts/backup/"
            "teste-backup-restore.sh` na VPS: `RESULTADO: TESTE_OK (12 itens, 0 falhas)` x2 (exit 0), "
            "logs em /tmp/tre_lote14/agentA/logs/{pass1,pass2}.out; o diff normalizado e' identico, "
            "diferindo apenas rotulos volateis declarados (nome do container descartavel, caminho TMPDIR, "
            "selo tre_dev_<timestamp> e o sha256 do dump, que embute dado de runtime). DENTE QUE MORDE: "
            "mutando o dump para `--schema-only` numa copia isolada (`backup-tre.sh` mutado e8b44643, "
            "copia limpa intocada f5fd66cf), o verificador REPROVOU -> `RESULTADO: TESTE_FALHOU (11 itens, "
            "2 falhas)` (exit 1), nomeando 'FALHOU teste de restore REPROVADO' e 'FALHOU restaurei zero "
            "linhas em tabelas que tinham dados no backup'; os mesmos itens passam OK na copia limpa. "
            "LIMITACOES DECLARADAS: (1) o verde do modo descartavel mede o dev VIVO em somente leitura, "
            "nao uma base hermética (ver CORRECAO DE ESCOPO no campo `ambiente`); (2) o corpo do card cita "
            "o commit `066dd81` e 'TESTE_OK (9 itens)' — hoje o verificador emite 12 itens no develop "
            "publicado (evoluiu com lib-ambiente.sh e o modo Odoo), e `066dd81` e' ancestral; (3) o dente "
            "mede a guarda de capacidade (comparacao de contagens), nao um defeito registrado — este e' um "
            "card de baseline/definicao. Zero escrita externa: TRE_BACKUP_EXTERNO vazio (manifesto "
            "'externo: pendente (sem destino configurado)'); baseline da VPS 5/7/0/4."
        ),
        "verification": {
            "verificador": "scripts/backup/teste-backup-restore.sh (modo descartavel)",
            "passadas": 2,
            "medicao": "TESTE_OK (12 itens, 0 falhas) x2, exit 0",
            "dente": "MORDE: dump --schema-only -> TESTE_FALHOU (11 itens, 2 falhas), 'restaurei zero linhas em tabelas que tinham dados'",
            "documentado": "docs/runbooks/backup-restore-rollback.md (65 KB) no develop publicado",
            "commit_medido": "9e638f79 (develop publicado na VPS)",
            "log": "/tmp/tre_lote14/agentA/logs/{pass1,pass2,dente}.out",
            "evidencia_conferida_por": "Hermes leu os vereditos na fonte (grep nos logs do VPS) antes de registrar",
        },
    },
    {
        "onda": "W2",
        "id": "TRE-W2-E01-T01",
        "title": "Instalar Odoo Community (ambiente dev)",
        "task": "t_d6dc5a4c",
        "evidence": (
            "Aceite medido nos quatro criterios homologados: (i) Odoo Community no dev com compose "
            "versionado — `deploy/compose/dev/odoo.yml` + `deploy/environments/dev-odoo.env` batem byte a "
            "byte com as copias vivas em /opt/tre/dev/compose/; (ii) versao registrada e reproduzivel — "
            "imagem `odoo:19.0` pinada, tag==digest sha256:77bac5cd...f85cd, binario 'Odoo Server "
            "19.0-20260926'; (iii) servico sobe e responde — HTTP 200 em 127.0.0.1:8069/web/login (pagina "
            "do Odoo, 5948 bytes), container odoo-dev e pg-odoo-dev de pe com restart=unless-stopped, banco "
            "`odoo_dev` presente e separado do `sales_intelligence` nos dois lados; (iv) nada exposto "
            "publicamente — Odoo so em loopback (ss 127.0.0.1:8069), pg-odoo-dev sem porta, UFW "
            "[22/tcp 443/tcp 80/tcp] sem 8069. MEDIDO por `scripts/provision/verificar-odoo-dev.sh` "
            "(`RESULTADO: ODOO_DEV_OK (19 itens, 0 falhas) versao=19.0 porta=127.0.0.1:8069`), DUAS "
            "passadas + uma terceira: as TRES byte-identicas (sha256 55df92242d1e...), diff limpo, SEM "
            "tokens volateis a normalizar. DOIS DENTES QUE MORDEM, em copias isoladas (o alvo versionado "
            "nunca foi tocado): (1) shim de `docker` interceptando so `docker port odoo-dev` para devolver "
            "0.0.0.0:8069 -> `RESULTADO: ODOO_DEV_FALHOU (19 itens, 1 falha)` com 'FALHOU odoo-dev publica "
            "endereco publico' (a mesma prova negativa que o runbook §4 usa); (2) copia do par sem "
            "ODOO_VERSION -> `RESULTADO: ODOO_DEV_FALHOU (19 itens, 5 falhas)`. LIMITACAO DECLARADA: o "
            "verificador e' read-only e amarrado aos nomes vivos (pg-odoo-dev, odoo-dev, volume "
            "pgdata-odoo-dev, banco odoo_dev) — nao existe base descartavel para ele; as passadas medem o "
            "ambiente dev entregue, que e' o alvo do card. O aceite mede o ambiente vivo (Odoo Up 4 dias), "
            "nao um install fresco; o rollback+reinstalacao de 01/10/2026 do runbook nao foi reproduzido. "
            "Nada foi gravado no dev nem em artefato; zero escrita externa."
        ),
        "verification": {
            "verificador": "scripts/provision/verificar-odoo-dev.sh (read-only, contra o dev entregue)",
            "passadas": 3,
            "medicao": "ODOO_DEV_OK (19 itens, 0 falhas) versao=19.0 porta=127.0.0.1:8069 — byte-identicas",
            "dente": "MORDE x2: porta publica simulada -> FALHOU (19/1); ODOO_VERSION removida -> FALHOU (19/5)",
            "sha_verificador": "7db09de011c9178b71fb79c1a7142f73576c121574ec559e8d23807a1d5126ed",
            "log": "/tmp/tre_lote14/agentB/{pass1,pass2,pass1b,dente,dente_env}.out",
            "evidencia_conferida_por": "Hermes leu os vereditos na fonte (grep nos logs do VPS) antes de registrar",
        },
    },
    {
        "onda": "W2",
        "id": "TRE-W2-E01-T02",
        "title": "Configurar TLS/reverse proxy/security",
        "task": "t_1acf11f2",
        "resultado": "PASS",
        "evidence": (
            "PASS APOS DECISAO DO DONO (05/10/2026): o criterio 'HTTPS com certificado valido (sem bypass)"
            " foi cumprido DE VERDADE, de fora, e a regua foi evoluida junto. Sequencia medida: (1) o dono"
            " criou o registro A `odoo-dev` -> 169.58.24.102 no Netlify DNS (zona na nsone); confirmei por"
            " DoH E por socket, e confirmei que o e-mail segue intacto (MX mx1/mx2.titan.email 10/20 e o TXT"
            " v=spf1 include:spf.titan.email ~all + verificacao Canva, identicos antes e depois); (2) apliquei"
            " a virada do TLS publico comentando o `tls internal` SO' do site principal (linha 37) e mantendo"
            " a CA interna no catch-all `:443` (linha 75) — o `caddy reload` falhou na API de admin"
            " ([::1]:2019 recusou) e a aplicacao veio por `docker restart proxy-dev`, com backup do Caddyfile"
            " no disco; (3) o Caddy emitiu certificado PUBLICO: subject=CN=odoo-dev.transformativa.com.br,"
            " issuer='C=US, O=Let's Encrypt, CN=YE2', valido 05/10/2026 17:04Z -> 03/01/2027, log"
            " 'certificate obtained successfully'; (4) de FORA (host do Hermes, terceiro de verdade):"
            " `http=401 ssl_verify_result=0` — cadeia publica valida SEM bypass e o proxy continua exigindo"
            " credencial; (5) `RESULTADO: TLS_DEV_OK (29 itens, 0 falhas)` DUAS vezes (o alvo passou de 28"
            " para 29 itens porque o item 7 ganhou a prova negativa). REGUA EVOLUIDA (decisao 1 do dono),"
            " com o historico honesto: o item 7 exigia 'sem a ancora tem de FALHAR', premissa valida so' no"
            " mundo de CA interna — mantida, reprovaria para sempre um alvo melhor. Virou modo duplo: se"
            " passa sem ancora, exige EMISSOR DE CA PUBLICA (nunca Caddy Local Authority, nunca vazio) E a"
            " prova negativa. O MEU PRIMEIRO DISCRIMINATOR NAO DISCRIMINAVA e o proprio item 7 pegou isso:"
            " `curl --cacert <CA errada>` devolve exit=0 neste build (curl 8.18 + OpenSSL 3.5 nao substitui"
            " o store do sistema) -> trocado por `openssl s_client -CAfile`, que usa SO' o arquivo apontado;"
            " medido agora: 'Verify return code: 20 (unable to get local issuer certificate)'. Tambem"
            " versionado o conserto DEFEITO 7 do healthcheck do proxy (repo agora IDENTICO ao que esta no"
            " ar). Baseline da VPS: 5 containers / 7 volumes / 0 dangling / 4 redes."
            "duas passadas identicas de `scripts/provision/verificar-tls-dev.sh` na VPS: `RESULTADO: "
            "TLS_DEV_OK (28 itens, 0 falhas) hostname=odoo-dev.transformativa.com.br portas=80/443`, "
            "identicas apos normalizar dois tokens volateis declarados (basename temporario da ancora, "
            "item 6; marcador X-Forwarded-For, item 16). DENTES QUE MORDEM em copias isoladas do Caddyfile "
            "servidas por containers proprios (porta 18443/18444, base real intocada): (A) copia SEM "
            "basic_auth -> `TLS_DEV_FALHOU (28 itens, 1 falha)` com 'FALHOU sem credencial do proxy o Odoo "
            "FOI servido (200, corpo com pagina do Odoo) — dev exposto sem protecao' (item 11); (B) copia "
            "SEM hardening headers -> `TLS_DEV_FALHOU (28 itens, 3 falhas)` com HSTS, Referrer-Policy e "
            "cabecalho Server exposto (item 8). Varredura de fora: 22/80/443 abertas; 8069/5433/8443/2019 "
            "HISTORICO DO BLOQUEIO, JA' RESOLVIDO (mantido para rastro): (1) o criterio 'Acesso por HTTPS com certificado valido (sem bypass "
            "de aviso)' so' e' cumprido contra a ANCORA INTERNA — o certificado no ar e' "
            "'issuer=CN=Caddy Local Authority' e a chamada HTTPS de fora com a cadeia default falha "
            "('curl exit=60, ssl_verify_result=20'); (2) `odoo-dev.transformativa.com.br` NAO RESOLVE "
            "(NXDOMAIN, conferido por mim agora) — e a propria descricao do card reserva ao dono a "
            "'Decisao do Anderson: dominio, portas e exposicao', que o runbook odoo-dev-tls.md §1.2 "
            "registra como PENDENTE. EFEITO COLATERAL DO CARD, JA' RESOLVIDO (conserto versionado nesta rodada): a copia "
            "OPERACIONAL diverge do repo — /opt/tre/dev/compose/proxy.yml no ar tem um bloco marcado "
            "'DEFEITO 7' (extra_hosts + healthcheck por hostname) que NAO existe em origin/develop nem na "
            "branch do card (conferido por mim: vivo=1 ocorrencia, repo=0). LIMITACAO DECLARADA: o "
            "verificador nao e' redirecionavel para base renomeada (hardcoda proxy-dev/odoo-dev/ufw), "
            "entao as passadas medem a base dev entregue; no dente, so' os itens 5-12 medem a copia "
            "mutada. INCIDENTE CORRIGIDO: a primeira tentativa dos mutantes abriu a porta 80 viva por "
            "SO_REUSEPORT; foi corrigido e os dentes refeitos limpos, e as duas passadas rodaram ANTES de "
            "existir mutante. Baseline final 5 containers / 7 volumes / 0 dangling / 4 redes."
        ),
        "verification": {
            "verificador": "scripts/provision/verificar-tls-dev.sh",
            "passadas": 2,
            "medicao": "TLS_DEV_OK (28 itens, 0 falhas) hostname=odoo-dev.transformativa.com.br portas=80/443",
            "dente": "MORDE x2: sem basic_auth -> FALHOU (28/1, Odoo servido sem credencial); sem headers -> FALHOU (28/3, HSTS/Referrer-Policy/Server)",
            "bloqueio": "decisao de dominio do dono PENDENTE — o dominio nao resolve (NXDOMAIN) e o certificado no ar e' CA interna",
            "divergencia_operacional": "proxy.yml vivo tem bloco 'DEFEITO 7' ausente do repo (vivo=1, repo=0)",
            "log": "/tmp/tre_lote14/agentC/{pass1,pass2,dente_a,dente_b}.out",
            "evidencia_conferida_por": "Hermes leu os vereditos na fonte e conferiu NXDOMAIN e a divergencia do proxy.yml antes de registrar",
        },
    },
    {
        "onda": "W8",
        "id": "TRE-W8-E01-T01",
        "title": "Funnel dashboard",
        "task": "t_6cc75a1d",
        "evidence": (
            "Exigencia efetiva no comentario do card (o corpo e' stub do plano): `ACEITE_FUNIL_OK (34 itens, "
            "0 falhas)` + suite offline `24 itens, 0 falhas + 8/8 mutacoes`. MEDIDO no develop publicado "
            "(9e638f7) E corroborado no commit do card (a5d9af3, cujos funil.py/funil-v1.json/aceite DIFEREM "
            "do HEAD): `RESULTADO: PASS (34 itens, 0 falhas)` x2 em cada commit, identicas apos normalizar "
            "so' o nome do container (unico token volatil). DENTE QUE MORDE nos DOIS commits: removendo a "
            "guarda de versao do SCORE_PRIORITY (contrato §8, defeito 'score sem versao qualifica'), o "
            "verificador REPROVOU -> `RESULTADO: FALHOU (33 itens, 1 falhas)` + `ACEITE_FUNIL_FALHOU`, com "
            "'FALHOU score SEM versao nao qualifica: org 3 nao infla o Qualificado 3'. Nota de metodo: a "
            "suite offline sozinha PASSOU com a mutacao — quem morde e' a assercao E2E semeada no banco. "
            "ACHADO DE HIGIENE (defeito real, fora do escopo deste card): o aceite irmao "
            "`scripts/agentes/teste_conversao_segmento_aceite.sh` limpa com `docker rm -f` SEM `-v` em 2 "
            "pontos (o funil e o efetividade usam `-v`) e VAZA 1 volume anonimo por passada — foi a origem "
            "dos 3 volumes dangling que eu removi por ID ao fim da leva; baseline de volta a 5/7/0/4."
        ),
        "verification": {
            "verificador": "scripts/agentes/teste_funil_aceite.sh + verificar_funil.py --autoteste",
            "passadas": 4,
            "medicao": "RESULTADO: PASS (34 itens, 0 falhas) x2 no develop (9e638f7) e x2 no commit do card (a5d9af3) -> ACEITE_FUNIL_OK",
            "dente": "MORDE: guarda de versao do SCORE_PRIORITY removida -> FALHOU (33 itens, 1 falhas) + ACEITE_FUNIL_FALHOU",
            "log": "/tmp/tre_lote14/agentD/logs/{pass1,pass2,dente,a5d9_pass1,a5d9_pass2,a5d9_dente}.out",
            "evidencia_conferida_por": "Hermes leu os vereditos na fonte antes de registrar",
        },
    },
    {
        "onda": "W8",
        "id": "TRE-W8-E02-T01",
        "title": "Conversion by segment",
        "task": "t_0248a568",
        "evidence": (
            "Exigencia literal (criterios-de-aceitacao.md §TRE-W8-E02-T01): `ACEITE_CONVERSAO_SEGMENTO_OK` "
            "(43 itens, 0 falhas, exit 0) em PostgreSQL descartavel + suite offline com 12 dentes. MEDIDO no "
            "develop publicado (9e638f7): aceite E2E `RESULTADO: PASS (43 itens, 0 falhas)` + "
            "`ACEITE_CONVERSAO_SEGMENTO_OK` em DUAS passadas com sha256 identico; suite offline "
            "`AUTOTESTE 12/12 mutacoes detectadas` + `RESULTADO: PASS (34 itens, 0 falhas)` em duas "
            "passadas com sha256 identico. Contrato conversao-segmento-v1.json sha256 81f1da55... casa com "
            "o declarado. DENTE QUE MORDE: mutando o casamento do eixo para semelhanca (`.startswith("
            "chave[:4])`, que reconstroi o defeito original de eixo casando por aproximacao), o offline "
            "REPROVOU -> `RESULTADO: FALHOU (16 itens, 3 falhas)` e o aceite E2E -> "
            "`ACEITE_CONVERSAO_SEGMENTO_FALHOU` com 'FALHOU faixa de funcionarios: buckets e contagens a "
            "mao' (150_299: 7, bom = 6); no codigo limpo esses itens passam OK, entao nao e' falso "
            "positivo. LIMITACAO DECLARADA: o aceite da copia mutada degradou feio (TypeError no bloco "
            "Python do passo 7 -> relatou 26 itens em vez de 43) e o reprovo nomeado veio pelo item de "
            "buckets, nao pela linha de dente — o harness nao degradou limpo, embora o reprovo seja real. "
            "DEFEITO CONFIRMADO NO PROPRIO ACEITE DESTE CARD: `docker rm -f` sem `-v` em 2 pontos -> vaza "
            "1 volume anonimo por passada (eu removi os 3 residuos por ID; baseline 5/7/0/4)."
        ),
        "verification": {
            "verificador": "scripts/agentes/teste_conversao_segmento_aceite.sh + verificar_conversao_segmento.py --autoteste",
            "passadas": 4,
            "medicao": "PASS (43 itens, 0 falhas) -> ACEITE_CONVERSAO_SEGMENTO_OK x2 (sha identico); offline AUTOTESTE 12/12 + PASS (34 itens, 0 falhas) x2",
            "dente": "MORDE: eixo casando por semelhanca -> offline FALHOU (16 itens, 3 falhas) e E2E ACEITE_CONVERSAO_SEGMENTO_FALHOU",
            "defeito_no_aceite": "docker rm -f sem -v (2 pontos) vaza 1 volume anonimo por passada",
            "log": "/tmp/tre_lote14/agentE/logs/{e02-acc1,e02-acc2,e02-off1,e02-off2,e02-dente-acc,e02-dente-off}.out",
            "evidencia_conferida_por": "Hermes leu os vereditos na fonte antes de registrar",
        },
    },
    {
        "onda": "W8",
        "id": "TRE-W8-E03-T01",
        "title": "Score effectiveness",
        "task": "t_8ba150bc",
        "evidence": (
            "Exigencia literal (comentarios 355/357 do card): aceite E2E em PostgreSQL descartavel + suite "
            "offline com mutacoes que tem de reprovar + REGRESSAO DO PAI (o relatorio funil-v1 nao pode "
            "mudar). MEDIDO no develop publicado (9e638f7): `RESULTADO: PASS (41 itens, 0 falhas)` + "
            "`ACEITE_EFETIVIDADE_SCORE_OK` em DUAS passadas BYTE-IDENTICAS (sha256 "
            "f4d133df5b74638c... identico cru e normalizado — nenhum token volatil divergiu); suite offline "
            "`PASS (22 itens, 0 falhas)` + `AUTOTESTE 8/8 mutacoes detectadas`; e a regressao do pai medida "
            "de verdade: 'OK aceite do funil continua verde com o pai estendido (PASS (34 itens, 0 "
            "falhas))'. DENTE QUE MORDE: mutando a tolerancia da adesao a formula (A4) para aceitar sempre "
            "(`if True`), o offline REPROVOU -> `FALHOU (21 itens, 1 falhas)` com 'adesao a formula: 9 "
            "comparaveis, 8 conformes, 1 divergente (1.00 de desvio)' e o E2E -> "
            "`ACEITE_EFETIVIDADE_SCORE_FALHOU` ('FALHOU suite offline ... AUTOTESTE 7/8'). LIMITACAO "
            "DECLARADA: medi o develop publicado (HEAD 9e638f7), nao o commit do card (573e349, "
            "ancestral); o unico delta nos arquivos deste card e' justamente a limpeza do aceite "
            "(`docker rm -f` -> `docker rm -f -v`), ou seja, no commit do card o aceite VAZAVA 1 volume por "
            "passada e no develop nao vaza. Sem escrita em artefato/bucket/git."
        ),
        "verification": {
            "verificador": "scripts/agentes/teste_efetividade_score_aceite.sh + verificar_efetividade_score.py --autoteste",
            "passadas": 2,
            "medicao": "RESULTADO: PASS (41 itens, 0 falhas) -> ACEITE_EFETIVIDADE_SCORE_OK x2, BYTE-IDENTICAS (sha256 f4d133df...)",
            "dente": "MORDE: tolerancia da formula forcada -> offline FALHOU (21 itens, 1 falhas); E2E ACEITE_EFETIVIDADE_SCORE_FALHOU",
            "regressao_do_pai": "aceite do funil segue verde (PASS 34 itens, 0 falhas) com o pai estendido",
            "log": "/tmp/tre_lote14/agentF/{aceite-pass1,aceite-pass2,dente-offline-mut,aceite-dente-mut}.out",
            "evidencia_conferida_por": "Hermes leu os vereditos na fonte antes de registrar",
        },
    },
    {
        "onda": "W9", "id": "TRE-W9-E01-T01", "title": "Score calibration", "task": "t_f599bd02",
        "evidence": (
            "Exigencia literal (comentario 361 do card): `ACEITE_CALIBRACAO_SCORE_OK` (0 falhas) com gate de "
            "volume medido e recusado quando a base nao sustenta, pesos por grade do simplexo (passo 0,05, "
            "1771 vetores), AUC contra Won/Lost, corte ajuste/validacao por sha256 e margem exigida NA "
            "VALIDACAO, faixas por Youden recursivo cobrindo 0..100, leitura pura provada por snapshot + "
            "transacao READ ONLY, determinismo, sem PII e proposta NAO aplicada (`aplicado: false`, "
            "`aprovacao_humana: pendente`). MEDIDO no commit do card (2d6451b): suite offline "
            "`VERIFICADOR_CALIBRACAO_PASS (51 itens, 0 falhas)` + `AUTOTESTE 7/7 mutacoes detectadas` em "
            "DUAS passadas com sha256 IDENTICO cru; aceite E2E `ACEITE_CALIBRACAO_SCORE_OK (32 itens, 0 "
            "falhas)` x2, identicas apos normalizar so' o nome do container e o dir-base. DENTES QUE MORDEM "
            "(2 mutacoes independentes no alvo): revertendo a margem -> `VERIFICADOR_CALIBRACAO_FALHOU (50 "
            "itens, 1 falhas)` com 'ganho na validacao abaixo da margem (anti-overfitting)'; afrouxando o "
            "gate de volume -> `FALHOU (50 itens, 1 falhas)` com motivo nomeado "
            "'CLASSE_LOST_ABAIXO_DO_MINIMO'. LIMITACAO DECLARADA: o aceite foi parametrizado no nome do "
            "container (default preservado) por haver agentes paralelos na VPS; a 1a tentativa com nome fora "
            "do padrao foi RECUSADA pelo proprio guard (`CONTAINER_NAO_LOCAL_DE_DEV`) — limitacao do setup, "
            "nao do artefato. Baseline inicio=fim=5/7/0/4."
        ),
        "verification": {"verificador": "scripts/agentes/teste_calibracao_score_aceite.sh + verificar_calibracao_score.py --autoteste",
                         "medicao": "ACEITE_CALIBRACAO_SCORE_OK (32 itens, 0 falhas) x2; offline PASS (51 itens, 0 falhas) + AUTOTESTE 7/7 x2",
                         "dente": "MORDE x2 (margem e gate de volume)", "commit_medido": "2d6451b",
                         "log": "/tmp/tre_lote14/agentG/logs/{suite_pass1,aceite_pass1,dente_margem,dente_gate}.out",
                         "evidencia_conferida_por": "Hermes leu os vereditos na fonte antes de registrar"},
    },
    {
        "onda": "W9", "id": "TRE-W9-E02-T01", "title": "Predictive scoring", "task": "t_2821c15b",
        "evidence": (
            "Exigencia literal (comentario 368): `ACEITE_PONTUACAO_PREDITIVA_OK` (35 itens, 0 falhas), base "
            "insuficiente => exit 6 SEM modelo/avaliacao/previsao, pesos vindos do RELATORIO da calibracao, "
            "corte ajuste/validacao igual ao do relatorio (divergente => RECUSA), blocos PAVA monotonicos, "
            "bino sem base herdando o bloco anterior. MEDIDO no develop publicado (9e638f7): E2E "
            "`ACEITE_PONTUACAO_PREDITIVA OK (35 itens OK, 0 falhas)` x2 BYTE-IDENTICAS (sha256 2c818627...) e "
            "offline `VERIFICADOR_PONTUACAO_PASS (66 itens, 0 falhas) + autoteste OK (8/8 dentes)` x2 "
            "byte-identicas (0d70cdfa...) — nenhum token volatil precisou de normalizacao. DENTE QUE MORDE: "
            "afrouxando o portao de volume (`base_suficiente = True`), o E2E REPROVOU -> "
            "`ACEITE_PONTUACAO_PREDITIVA FALHOU (33 itens OK, 2 falhas)` com 'FALHOU coorte abaixo do minimo "
            "ABSTEVE (exit 6)' e 'FALHOU abstencao NAO produz modelo nem previsao'; o versionado nao foi "
            "tocado. ACHADO DE HIGIENE DECLARADO: o aceite deixa 1 volume anonimo por execucao (3 medidos "
            "numa janela de 3 passadas) apesar de o codigo usar `docker rm -f -v` — o subagente removeu os 3 "
            "por ID; baseline final medido por mim 5/7/0/4. LIMITACAO: o literal do aceite e' "
            "`ACEITE_PONTUACAO_PREDITIVA OK` (com espaco), nao `..._OK` como grafado no card."
        ),
        "verification": {"verificador": "scripts/agentes/teste_pontuacao_preditiva_aceite.sh + verificar_pontuacao_preditiva.py --autoteste",
                         "medicao": "ACEITE_PONTUACAO_PREDITIVA OK (35 itens, 0 falhas) x2 byte-identicas; offline PASS (66 itens, 0 falhas) + autoteste 8/8 x2",
                         "dente": "MORDE: portao de volume afrouxado -> E2E FALHOU (33 itens OK, 2 falhas)",
                         "achado_higiene": "1 volume anonimo vazado por execucao apesar do `-v` no codigo (3 removidos por ID)",
                         "log": "/tmp/tre_lote14/agentH/logs/{e2e_pass1,e2e_pass2,off_pass1,dente_pass}.log",
                         "evidencia_conferida_por": "Hermes leu os vereditos na fonte antes de registrar"},
    },
    {
        "onda": "W9", "id": "TRE-W9-E03-T01", "title": "Best channel prediction", "task": "t_90b83807",
        "evidence": (
            "Exigencia literal (comentario 359): `ACEITE_PREVISAO_CANAL_OK` (0 falhas) com o requisito "
            "central 'opt-out e' bloqueio': canal bloqueado por opt_out_email/opt_out_whatsapp/"
            "do_not_contact NUNCA e' previsto e o bloqueio e' nomeado na saida. MEDIDO no develop publicado "
            "(9e638f7; zero diferenca nos arquivos do card vs o commit f0cc981): offline `RESULTADO: PASS (23 "
            "itens, 0 falhas)` + `AUTOTESTE 8/8 mutacoes detectadas` + `VERIFICADOR_PREVISAO_CANAL_PASS` x2 "
            "IDÊNTICAS byte-a-byte; E2E `RESULTADO: PASS (42 itens, 0 falhas)` + `ACEITE_PREVISAO_CANAL_OK` "
            "x2 (0 linhas divergentes apos normalizar so' o token volatil do TRE_ACEITE_BASE). DENTE QUE "
            "MORDE com mutacao INDEPENDENTE das 8 embutidas (do_not_contact sempre falso): offline "
            "`RESULTADO: FALHOU (21 itens, 2 falhas)` com 'do_not_contact bloqueia TODOS os canais' e E2E "
            "`RESULTADO: FALHOU (38 itens, 4 falhas)` + `ACEITE_PREVISAO_CANAL_FALHOU` (exit 1) — o MESMO "
            "aceite passa 42/0 no codigo bom. ACHADO CONFIRMADO NO CODIGO POR MIM: "
            "`teste_previsao_canal_aceite.sh` limpa com `docker rm -f` SEM `-v` (0 ocorrencias com `-v`, 2 "
            "sem) -> vaza 1 volume anonimo por rodada (o subagente removeu os 4 dele por ID). DESVIO "
            "DECLARADO PELO PROPRIO CARD (comentario 363): o aceite entregue NAO reexecuta o aceite do pai "
            "(ACEITE_FUNIL_OK), prometido no comentario de abertura; prova integracao por reconciliacao "
            "numerica. Baseline final medido por mim: 5/7/0/4."
        ),
        "verification": {"verificador": "scripts/agentes/teste_previsao_canal_aceite.sh + verificar_previsao_canal.py --autoteste",
                         "medicao": "E2E PASS (42 itens, 0 falhas) -> ACEITE_PREVISAO_CANAL_OK x2; offline PASS (23 itens, 0 falhas) + AUTOTESTE 8/8 x2 byte-identicas",
                         "dente": "MORDE: do_not_contact sempre falso -> offline FALHOU (21, 2) e E2E FALHOU (38, 4)",
                         "defeito_no_aceite": "docker rm -f sem -v (0 com, 2 sem) vaza 1 volume anonimo por rodada",
                         "desvio_do_card": "nao reexecuta o aceite do pai (prometido no comentario de abertura; declarado no 363)",
                         "log": "/tmp/tre_lote14/agentI/logs/{I_off1,I_e2e1,I_dente,I_dente_offline}.log",
                         "evidencia_conferida_por": "Hermes leu os vereditos na fonte antes de registrar"},
    },
]


def main() -> int:
    dry = "--dry-run" in sys.argv
    por_artefato = {}
    for card in CARDS:
        por_artefato.setdefault(card["onda"], []).append(card)

    for onda, cards in por_artefato.items():
        nome = ARTEFATO.get(onda)
        if not nome:
            print(f"!! onda {onda} sem artefato mapeado — cards {[c['task'] for c in cards]} NAO gravados")
            continue
        arq = BASE / nome
        with open(arq, encoding="utf-8") as fh:
            j = json.load(fh)
        existentes = {
            c["hermes_task_id"]: (w, c)
            for w in j["work_items"]
            for c in (w.get("children") or [])
        }
        novos = []
        for card in cards:
            if card["task"] in existentes:
                _, c = existentes[card["task"]]
                c.update(
                    validation_result=card.get("resultado", "PASS"),
                    evidence=card["evidence"],
                    current_gate="VALIDATION",
                    validated_at=DATA,
                    validated_by=BY,
                    verification=dict(card["verification"], commit=COMMIT, ambiente=AMBIENTE),
                )
                print(f"  {card['task']}: child existente atualizado (forma completa)")
                continue
            j["work_items"].append(
                {
                    "id": card["id"],
                    "title": card["title"],
                    "stage": "DONE",
                    "current_gate": "VALIDATION",
                    "children": [
                        {
                            "hermes_task_id": card["task"],
                            "stage": "DONE",
                            "current_gate": "VALIDATION",
                            "validation_result": card.get("resultado", "PASS"),
                            "evidence": card["evidence"],
                            "validated_at": DATA,
                            "validated_by": BY,
                            "verification": dict(
                                card["verification"], commit=COMMIT, ambiente=AMBIENTE
                            ),
                        }
                    ],
                }
            )
            novos.append(card["task"])
        if novos:
            j["updated_at"] = DATA
            j.setdefault("events", []).append(
                {
                    "event": "INTEGRATED_VALIDATION_RECORDED",
                    "by": BY,
                    "scope": f"lote 14 — onda {onda}: vereditos gravados com 2 passadas + prova de dente",
                    "production_promotion_authorized": False,
                    "at": "2026-10-05T14:00:00+00:00",
                    "cards": novos,
                }
            )
        print(f"{onda} ({nome}): novos={novos} | work_items={len(j['work_items'])}")
        if not dry:
            with open(arq, "w", encoding="utf-8") as fh:
                json.dump(j, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
    print("(dry-run: nada gravado)" if dry else "gravado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
