---
id: ref-global-instructions
title: Instruções globais e edição de prompts
category: Prompt Library and References
keywords: [instruções globais de sistema, instruções, regras, sempre responder em tópicos, gerenciado, sua própria chave, edição de prompts, prompts de resumo, prompt de análise, contexto, avançado]
screen: settings/prompts
---

As **Instruções globais de sistema** são as suas regras fixas para a IA: "responda em tópicos", "nunca especule", "trabalho na indústria farmacêutica, considere esse contexto". Ajustes > Prompts e referências > Prompts rápidos > Avançado. Elas são adicionadas a todo prompt da sessão ao vivo, e também ao Meeting Chat e ao Project Chat.

**Elas só valem com sua própria chave.** No Shoulder Surf AI, no Apple Cloud e no modelo No dispositivo (Apple), as instruções são gerenciadas pelo Shoulder Surf, e o rodapé avisa isso: o que você salvar ali fica guardado, mas não é usado até você trocar para um modelo com sua própria chave de API. O motivo: nesses modelos, a receita do prompt é mantida no servidor e ajustada para cada modelo, e as suas regras iam brigar com ela.

**O texto dos Prompts rápidos é seu em qualquer modelo.** Você pode editar, adicionar, reordenar e apagar Prompts rápidos também no Shoulder Surf AI. Veja "Prompts rápidos: um toque, uma resposta."

**Prompts de fundo** (Resumo completo, Resumo incremental, Consolidação final, Análise pós-sessão) ficam em Ajustes > Reuniões > Resumos > Editar prompts de resumo. São editáveis com sua própria chave e gerenciados no Shoulder Surf AI ("Otimizado para o Shoulder Surf AI"). Trocar para o Shoulder Surf AI volta os prompts personalizados para os padrões gerenciados.

**Contexto.** O que vai junto com uma pergunta: a transcrição, o resumo contínuo, as informações do projeto, o histórico da conversa, imagens, o Context Quilt. A seção Avançado de cada Prompt rápido pode mudar isso só para aquele prompt (Padrão / Ativado / Desativado). Os padrões globais são o que o prompt herda.

**Buscar prompts padrão mais recentes** puxa do servidor o texto gerenciado atual. Se você personalizou os prompts que vêm com o app, eles não se atualizam sozinhos; esse botão, ou "Redefinir padrões", é como você pega os novos.
