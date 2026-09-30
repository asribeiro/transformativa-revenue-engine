# Aprovação humana COM executor — desenho para homologação

Status: **PROPOSTA — aguardando homologação de Anderson Ribeiro** (30/09/2026).
Card de origem: beco da aprovação humana, descoberto na revisão da projeção de entrega e
provado no despacho da W1.

## 1. O problema, medido (não deduzido)

O roteador exige humano para ação sensível e o gate respeita isso — mas **nada executa o
card depois da aprovação**. Medições de 30/09/2026:

- `hermes/jev/routing/router.py:1511` — código comum (ajuste_de_texto, consulta_interna,
  operacao_comercial, migracao_de_esquema, execucao_de_card) **não cobre domínio sensível
  nenhum, por desenho**: com domínio declarado/inferido, a decisão é abster e escalar;
- gate do card `t_969affa7` (TRE-W1-E01-T01, criar database/schema) → **ESCALATE**, motivo
  "confiança ausente: ausência de resposta é abstinência, não permissão";
- os 6 cards seguintes da W1 → **BLOCK por dependência pendente** (a corrente está certa:
  E02/E04-T01/E06 esperam o E01; E03/E04-T02/E05 esperam o E02/E03);
- consequência: os 65 cards do roadmap estão atrás desse ponto — cada um ou declara
  domínio sensível, ou depende de quem declara.

`hermes/policies/human-approval.yaml` **declara** a exigência, e nada a consome para
liberar execução. Ou seja: a regra manda e nenhum código obedece — o inverso do achado
D08 ("regra declarada ≠ regra executada"), que continua valendo para o resto.

## 2. O desenho proposto (mínimo, fail-closed)

Três peças, todas versionadas no repositório:

1. **Registro de aprovações de card** — `hermes/jev/aprovacoes-humanas.yaml`, com uma
   entrada por card:

   ```yaml
   aprovacoes:
     - card_id: t_969affa7
       aprovador: Anderson Ribeiro
       aprovado_em: 2026-09-30
       escopo: [producao_ou_release]        # domínios que ESTA aprovação cobre
       evidencia: "linha do registro-de-aprovacoes.md de 30/09/2026 + mensagem no Telegram"
       validade: 2026-10-07                 # data explícita; sem validade, não vale
   ```

2. **O gate consulta o registro** — depois de decidir, o gate faz a única pergunta nova:
   *o card tem aprovação registrada, válida e que cobre exatamente os domínios da decisão?*

   - **sim** → decisão passa a `PASS` com `origem: aprovacao_humana_registrada` gravada no
     recibo (rastro completo: quem aprovou, quando, qual escopo, qual data);
   - **não** → comportamento de hoje, sem exceção (falha fecha).

3. **Dupla entrada obrigatória** — a aprovação só vale se existir **também** a linha
   correspondente em `docs/operations/registro-de-aprovacoes.md` (a mesma disciplina do
   registro: identidade, data e evidência). Divergência entre os dois arquivos = aprovação
   inválida, e a suíte fica vermelha.

## 3. Limites que NÃO se movem

- **Quem aprova**: só Anderson Ribeiro (nome conferido no arquivo; nenhum outro
  aprovador é aceito).
- **Credencial**: a aprovação abre o **gate**, não credencial. A matriz Dev × Sales
  continua: perfil de Sales AI nunca recebe credencial de deploy.
- **Domínio não coberto**: aprovação de `producao_ou_release` não libera card que também
  declara `credencial` ou `dado_de_cliente` — a cobertura é por domínio, item a item.
- **Validade**: aprovação sem `validade` explícita não vale; vencida, volta a escalar.
- **O que a aprovação não faz**: não escreve em ambiente vivo por si, não promove release,
  e não dispensa o passo de operador (infra, credenciais, DDL em ambiente).
- **Fail-closed**: ausência, expiração, escopo parcial ou divergência entre arquivos →
  escala como hoje. Nunca "aprovação presumida".

## 4. Prova e verificação exigidas antes de valer

- suíte do gate: itens novos com **mutações obrigatórias** — aprovação ausente → escala;
  aprovação de outro card → escala; aprovação vencida → escala; escopo parcial → escala;
  aprovação válida e completa → PASS com `origem` no recibo;
- suíte de alinhamento dos dois arquivos (aprovacoes-humanas × registro-de-aprovacoes);
- **verificação independente pelo perfil `tester`** antes de o caminho valer para despacho;
- linha datada no registro para cada aprovação concedida (nada de aprovação sem rastro).

## 5. O que preciso de você para homologar

1. Aprova o desenho acima como está? (a única decisão de política é "só Anderson aprova,
   e por dupla entrada");
2. Aceita `validade` de 7 dias corridos como padrão, ou prefere outro prazo?
3. Confirma que a primeira aprovação de teste será o card TRE-W1-E01-T01 (criar
   database/schema) — lembrando que ele ainda depende de um PostgreSQL dev existindo na
   Contabo (passo de operador).

## 6. Decisões de homologação (registro incremental)

- **Decisão 1 — quem registra a aprovação: HÍBRIDA (opção C)**, escolhida por Anderson
  Ribeiro em 30/09/2026. Regra única, por `canal` da entrada:
  - `canal: telegram` — vale para escopo SÓ de desenvolvimento; as duas entradas
    (aprovacoes-humanas.yaml e registro-de-aprovacoes.md) são escritas pelo agente a partir
    da palavra dele, e **toda aprovação passa por revisão independente do `tester`**;
  - `canal: commit-do-aprovador` — obrigatório quando o escopo tocar **ambiente vivo,
    credencial ou dado de cliente**: só vale com commit do próprio Anderson (GitHub web).
  - a suíte tem de **reprovar** entrada com `canal: telegram` cujo escopo toque ambiente
    vivo, credencial ou dado de cliente.

- **Decisão 2 — vínculo: HASH DO TEXTO DO CARD (opção A)**, escolhida por Anderson
  Ribeiro em 30/09/2026. A entrada guarda o hash de titulo+corpo do card aprovado; vale
  para todas as tentativas ate vencer e **cai sozinha** se o card for editado depois.
  Efeito declarado: editar o texto de um card aprovado (inclusive correcao do agente)
  invalida a aprovacao e exige nova palavra dele — fecha o atalho usado no T11.
- **Decisão 3 — validade: 7 DIAS CORRIDOS DA DATA DA APROVACAO (opção A)**, escolhida por
  Anderson Ribeiro em 30/09/2026. Vencida, o card volta a escalar e o agente pede nova
  palavra. Registrado o racional: com o vinculo por hash (decisao 2), o prazo e higiene,
  nao seguranca — nao ha troca possivel de texto dentro do prazo.
- **Decisão 4 — escopo julgado pelo AMBIENTE e pelo DOMINIO (opção C)**, escolhida por
  Anderson Ribeiro em 30/09/2026: exige `canal: commit-do-aprovador` quando houver
  (i) execucao em ambiente que nao seja de desenvolvimento ou (ii) dominio de `credencial`
  ou `dado_de_cliente`. Mencao a producao/release apenas no TEXTO, com execucao em
  desenvolvimento, vale por `canal: telegram`. Primeira aprovacao: `TRE-W1-E01-T01`.

## 7. Decisão de acesso operacional (acesso do agente às máquinas)

- **Decisão 5 — acesso às duas máquinas (opção B)**, escolhida por Anderson Ribeiro em
  30/09/2026: chave dedicada do agente (`~/.ssh/id_ed25519_ops`, sem passphrase, 600)
  autorizada na VPS do TRE (Contabo `169.58.24.102`) e no host do Hermes
  (Hostinger `187.127.56.17`), usuário root nas duas.
- Limites que o agente mantém por conta própria (declarados, não pedidos):
  1. **o agente não aprova nada** — aprovação humana é ato do dono (decisões 1 a 4);
  2. **nada destrutivo** (remover container/volume/imagem, apagar dado, derrubar serviço)
     sem comando explícito do dono;
  3. **ambiente vivo e credencial** seguem exigindo `canal: commit-do-aprovador`
     (decisão 1/4), mesmo com acesso;
  4. **toda execução nas máquinas entra no registro** com comando e saída, para a auditoria
     não depender da narrativa do agente.
- Risco declarado e aceito: docker equivale a root; a chave dá a máquina inteira; não existe
  escopo pequeno. Mitigação é o registro e os limites acima, não a cerca.

## 8. Nota de contrato do recibo (correcao de desvio)

O recibo do gate tem contrato **fechado de 13 campos** (`hermes/jev/policy_v1_2.yaml`) e a
suite do gate reprova campo a mais. O desenho na secao 2 pedia "origem gravada no recibo" e
a primeira implementacao criou campos novos (`origem`, `aprovador`, `canal`, `validade`,
`hash`, `aprovacao_motivo`) — **desvio corrigido**: o rastro da aprovacao passa a morar em
`override.aprovacao_humana`, dentro do campo `override`, que existe exatamente para registrar
excecao com razao. Nenhum campo novo no recibo; a trilha continua auditavel no arquivo. O
motivo de uma aprovacao NAO aplicavel viaja na resposta do gate (que o board grava), nunca no
recibo.

## 9. Decisão 6 — escala das aprovações (onda x item a item)

- **Decisão 6 — HIBRIDA: onda para desenvolvimento, item a item para o resto (opção C)**,
  escolhida por Anderson Ribeiro em 30/09/2026. Motivo declarado: 65 cards em `todo` (W1→W9)
  e o pedido individual por card (todas as travas que hoje existem) viraria gargalo do dono —
  e gargalo de aprovacao termina em aprovacao no automatico, que e pior que nao ter controle.
  - a ONDA cobre somente escopo estritamente de **desenvolvimento** e grava, no ato da
    aprovacao, a **lista de cards + o hash do texto de cada um** (card editado depois sai da
    onda: o hash deixa de bater);
  - qualquer card cuja declaracao toque **ambiente vivo, credencial ou dado de cliente** fica
    FORA da onda e exige aprovacao individual, com `canal: commit-do-aprovador` (decisao 1/4);
  - a onda e entrada do mesmo registro declarado (`aprovacoes-humanas.yaml`), com dupla
    entrada no `registro-de-aprovacoes.md`, validade de 7 dias (decisao 3) e revisao
    independente do `tester` antes de valer;
  - nada aqui afrouxa o fail-closed: sem aprovacao (individual ou de onda) valida, o gate
    escala exatamente como hoje.
- Pendente de implementacao: a onda como entrada do registro (lista + hash por card) e a
  suite que reprova card de onda cujo escopo toque ambiente vivo, credencial ou dado.
