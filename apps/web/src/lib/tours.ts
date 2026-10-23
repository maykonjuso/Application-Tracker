/**
 * Tours de cada tela: o que é a tela, quando usar e o que cada parte faz. `target` aponta para um elemento
 * com data-tour="..."; sem target (ou se o elemento não existir), o passo aparece centralizado.
 */
export type TourStep = { target?: string; title: string; body: string };

export const TOURS: Record<string, TourStep[]> = {
  hoje: [
    {
      title: "Bem-vindo ao seu painel",
      body: "Esta é a tela de todo dia: mostra o que precisa da sua atenção agora. O app trabalha sozinho em segundo plano (busca vagas, lê e-mails, dá notas); aqui você só decide e age.",
    },
    {
      target: "journey",
      title: "O caminho de uma candidatura",
      body: "São 4 passos: (1) ver as vagas recomendadas, (2) candidatar-se no site da empresa, (3) acompanhar as respostas, (4) preparar as entrevistas. Cada passo mostra quantos itens tem e leva direto para a tela certa.",
    },
    {
      target: "pending",
      title: "Pendências",
      body: "Coisas que só você pode resolver: perguntas que o app não soube responder, e-mails que ele não conseguiu ligar a uma candidatura, eventos para confirmar. Número maior que zero = clique para resolver.",
    },
    {
      target: "top-jobs",
      title: "Melhores vagas novas",
      body: "As vagas com maior nota que chegaram recentemente. Clique para ver os detalhes e se candidatar.",
    },
    {
      target: "help",
      title: "Ajuda sempre aqui",
      body: "Cada tela tem um tour como este. Clique em “Como usar esta tela” quando quiser rever.",
    },
  ],
  vagas: [
    {
      title: "Vagas recomendadas",
      body: "Aqui ficam as vagas que o app encontrou, já filtradas pelos seus cargos e ordenadas pela nota de match. Seu trabalho: olhar, decidir e se candidatar às boas.",
    },
    {
      target: "job-card",
      title: "Cada cartão é uma vaga",
      body: "Logo e nome da empresa, local, modelo, senioridade, salário (quando informado) e as tecnologias pedidas (passe o mouse no ícone para ver o nome). O número colorido é o match: quanto maior, melhor.",
    },
    {
      target: "job-card",
      title: "Clique no cartão para agir",
      body: "Abre um painel com o passo a passo: (1) “Abrir a vaga no site” para se candidatar lá (a extensão preenche o formulário sozinha), (2) “Já me candidatei” para acompanhar. O X no cartão descarta a vaga.",
    },
    {
      target: "filters",
      title: "Filtros",
      body: "Filtre por tecnologia, empresa, salário mínimo, modelo de trabalho, senioridade, data e mais. Os filtros ativos aparecem como etiquetas acima da lista; clique no × para remover.",
    },
    {
      target: "search",
      title: "Busca e ordenação",
      body: "Busque por palavra, escolha ver as novas, as já decididas ou todas, e ordene por match, data, salário ou empresa.",
    },
    {
      target: "tabs",
      title: "Buscar vagas e acompanhar empresas",
      body: "Em “Buscar vagas”, digite palavras-chave, país e modalidade para ver vagas correspondentes e pesquisar novas fontes. “Empresas (ATS)” configura empresas acompanhadas; “Buscas salvas” mostra e organiza suas pesquisas automáticas.",
    },
    {
      target: "import",
      title: "Viu uma vaga em outro lugar?",
      body: "Cole o link ou o texto do anúncio em “Importar vaga” e o app avalia para você.",
    },
  ],
  candidaturas: [
    {
      title: "Suas candidaturas",
      body: "Cada cartão é uma vaga à qual você se candidatou (ou que está na fila). As colunas são as etapas do processo.",
    },
    {
      target: "kanban",
      title: "O status muda sozinho",
      body: "Quando a empresa responde no Gmail (convite, teste, recusa), o app move o cartão. Se precisar corrigir, arraste o cartão para outra coluna. Clique no cartão para ver o histórico.",
    },
    {
      target: "manual",
      title: "Candidatou-se fora do app?",
      body: "Use “Registrar enviei” para adicionar uma candidatura feita em outro site, para que ela também seja acompanhada.",
    },
  ],
  inbox: [
    {
      title: "E-mails das empresas",
      body: "Os e-mails de processos seletivos, já classificados (confirmação, entrevista, recusa, oferta). Se o app não souber de qual candidatura é um e-mail, ele pede para você escolher. Rascunhos de resposta ficam na aba Rascunhos: nada é enviado sem você.",
    },
  ],
  agenda: [
    {
      title: "Agenda",
      body: "Entrevistas e prazos de testes técnicos da sua Agenda Google. Convites detectados nos e-mails aparecem como rascunho para você confirmar.",
    },
  ],
  entrevistas: [
    {
      title: "Entrevistas",
      body: "Para cada entrevista, o app monta um dossiê: história e valores da empresa, notícias recentes, seus pontos fortes para a vaga e perguntas prováveis. Dá também para treinar com uma simulação.",
    },
  ],
  perfil: [
    {
      title: "Seu perfil",
      body: "O currículo mestre é a fonte de verdade: todo currículo e toda resposta saem dele, e nada fora dele é usado. Aqui você corrige experiências, competências e as respostas padrão de formulários.",
    },
  ],
  curriculos: [
    {
      title: "Currículos",
      body: "Coringas são currículos prontos por perfil (ex.: Data Engineer em inglês). Específicos são ajustados para uma vaga. Escolha o perfil e o idioma e clique em criar; depois gere o PDF e aprove.",
    },
  ],
  pessoas: [
    {
      title: "Pessoas",
      body: "Recrutadores e contatos das empresas, com o histórico de conversas e candidaturas ligadas a cada um.",
    },
  ],
  ofertas: [
    {
      title: "Ofertas",
      body: "Registre propostas e compare pelo valor líquido anual, CLT x PJ, sempre contra o seu emprego atual (preencha o cartão do topo).",
    },
  ],
  insights: [
    {
      title: "Insights",
      body: "Funil das suas candidaturas, taxas de resposta por currículo e por tipo de vaga, e as tecnologias que mais aparecem nas vagas e faltam no seu perfil.",
    },
  ],
  config: [
    {
      title: "Configurações",
      body: "Em Integrações estão os guias passo a passo de IA, Gmail, Telegram e extensão. As outras abas ajustam automação, limites, pesos das notas e notificações.",
    },
  ],
};
