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
| 1 | porte: **de 50 a 1.000 funcionários** (abaixo de 50 e acima de 1.000 ficam fora) | contagem de funcionários entre 50 e 1.000 | **não pontua** e registra o motivo — ausência não vira fit (regra já declarada no modelo `icp-v1.0.0`) |
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

## Recorte de porte e Tiers (refinamento do dono, 07/10/2026)

> "é importante deixar claro que o meu recorte de empresa vai de empresas de 50 a 1000 funcionários, e que
> dentro desse recorte quero ter Tiers por segmento (cnaes) e por tamanho/porte"

O ICP deixa de ser ">= 50" e passa a ser **faixa fechada de 50 a 1.000 funcionários**. Acima de 1.000 sai do
recorte (é enterprise, outro ciclo de compra); abaixo de 50 sai também. Dentro da faixa, o dono pediu **dois
eixos de priorização**, e eles são independentes:

### Eixo 1 — Tiers por tamanho/porte

| tier | faixa | por que importa para a abordagem |
|------|-------|----------------------------------|
| **P1** | 50 a 99 funcionários | volume: muitas empresas, decisão operacional, ciclo curto. Melhor lugar para provar retorno rápido |
| **P2** | 100 a 299 funcionários | ponto doce: já existe alguma área de operações/processos e verba, e ainda há um decisor só |
| **P3** | 300 a 999 funcionários | conta grande: verba e estrutura reais, ciclo mais longo, pede governança e gestão de portfólio |
| — | acima de 1.000 | **fora do recorte** (enterprise) |

### Eixo 2 — Tiers por segmento (CNAE)

As três famílias de segmento, com os CNAEs verificados na base oficial do IBGE, estão em
`cnaes-icp-transformativa.csv`. Resumindo o critério:

| tier | famílias | por que |
|------|----------|---------|
| **S-A** | saúde e diagnóstico; educação privada; serviços de volume (contábil, BPO, cobrança) | repetição documental intensa, pressão de custo alta, retorno do agente é evidente |
| **S-B** | tecnologia e dados; logística e transporte; financeiro, pagamentos e seguros | bom encaixe, mas ciclo mais longo (fazem internamente ou têm compliance pesado) |
| **S-C** | indústria de transformação pura; energia; óleo e gás; bancos | **fora do primeiro lote**: superfície automatizável física ou porte enterprise |

### Como os dois eixos se combinam (prioridade de abordagem)

Combinar os eixos é o que define a **ordem**, não a exclusão — todo cruzamento dentro de 50 a 1.000 é alvo
válido, o que muda é por onde começar:

1. **S-A x P2** — o ponto de melhor retorno: verba, dor de volume e um decisor só.
2. **S-A x P1** — volume e ciclo rápido; serve para encher o funil e calibrar a mensagem.
3. **S-A x P3** e **S-B x P2** — contas maiores, proposta mais consultiva (governança, portfólio).
4. **S-B x P3** — depois, com prova social do primeiro lote.

### Requisito que isso impõe à fonte de dados

O campo `porte` da Receita Federal é **faixa de receita** (MEI, ME, EPP, Demais) e **não tem contagem de
funcionários**. Como o recorte do dono é por **número de funcionários em três faixas**, os dados abertos do
governo **não conseguem expressar os tiers P1/P2/P3** — eles só dizem "Demais". Portanto: todo provedor
avaliado passa a ser medido por uma pergunta direta — **entrega contagem de funcionários, e em que
granularidade?** Sem isso, não há como aplicar o eixo de porte.
