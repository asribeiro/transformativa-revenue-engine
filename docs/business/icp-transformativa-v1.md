# ICP da Transformativa — definição do dono (v1, 07/10/2026)

Registro literal da definição dada por Anderson Ribeiro no Telegram em 07/10/2026, e a tradução dela em
critério medível para o motor. O ICP deixa de ser proposta: passa a ser **contrato**.

## Definição (palavras do dono)

> empresas com no mínimo 50 usuários associados no linkedin ou funcionários declarados, inicialmente do
> estado de São Paulo, que estejam buscando por serviços automatizáveis por meio de IA, ou estejam
> contratando para posições que poderiam ser desempenhadas por IA, e empresas que estejam buscando aprender
> e aplicar IA para gerar melhores resultados.

## Critérios, do jeito que a máquina consegue medir

| # | critério | como fica medível | ausência de dado |
|---|----------|-------------------|------------------|
| 1 | porte: ≥ 50 usuários no LinkedIn **ou** funcionários declarados | campo de porte da organização (faixa/contagem) ≥ 50 | **não pontua** e registra o motivo — ausência não vira fit (regra já declarada no modelo `icp-v1.0.0`) |
| 2 | geografia: estado de São Paulo | `state = SP` (expansão para outros estados é decisão futura, em versão nova) | idem |
| 3 | intenção A: buscando serviços automatizáveis por IA | sinal declarado com fonte e data | idem |
| 4 | intenção B: contratando para posições que poderiam ser desempenhadas por IA | vaga observada, com fonte e data | idem |
| 5 | intenção C: buscando aprender e aplicar IA para gerar melhores resultados | sinal declarado com fonte e data | idem |

Critério 1 é **corte** (abaixo de 50 não entra na campanha), não peso: os três primeiros critérios definem
quem é alvo, e as três intenções definem quem é prioridade.

## Restrição de fonte (importante, não é detalhe)

O motor **não raspa LinkedIn** e não tem API do LinkedIn — isso é decisão de projeto e não muda (ver
`hermes/agents/linkedin/linkedin-assistido-v1.json`, `USAR_API_DO_LINKEDIN: "nenhuma credencial/API do
LinkedIn no sistema"`). Portanto:

- o **porte** e a **lista** de empresas vêm do dono ou de fonte licenciada que ele declarar;
- as **intenções** vêm de fonte declarada (indicação, evento, formulário do site, material que o dono
  forneça) — cada sinal precisa guardar **fonte e data**, porque intenção sem fonte é palpite;
- a máquina pode **inferir** intenção a partir do que já está na base (ex.: sinal lido de uma vaga trazida
  pelo dono), mas inferência entra sempre **marcada** como inferência, nunca como fato (regra já vigente).

## Regra de campanha (do ADR-0009)

Toda campanha nasce **apenas** com aprovação expressa do dono sobre (a) o **texto** e (b) a **lista de
disparo**. Sem as duas aprovações o motor produz no máximo recomendação interna — nunca material de disparo.
O primeiro contato outbound (e-mail, LinkedIn ou WhatsApp) segue exigindo aprovação expressa, item a item.

## Como isto entra no sistema

1. **Modelo do score:** o `icp-v1.0.0` estava `PROPOSTA_A_HOMOLOGAR` e passa a **homologado** — com nova
   versão do modelo (1.1) que acrescenta o corte de porte ≥ 50 e o critério de geografia (SP) e reserva
   espaço para os três sinais de intenção com fonte e data.
2. **Pesos:** os pesos atuais (segmento 0.45, porte 0.35, modelo_b2b 0.20) passam a conviver com o **corte**
   de porte — corte não é peso: quem não passa no corte não entra, independentemente do score.
3. **Rastreabilidade:** cada pontuação continua explicando o porquê (`explicacao`), agora nomeando qual dos
   cinco critérios casou e qual ficou sem dado.
