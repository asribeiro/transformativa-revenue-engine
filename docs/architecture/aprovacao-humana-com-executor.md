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
- Decisão 4 — primeira aprovação e sequência: **pendente**.
