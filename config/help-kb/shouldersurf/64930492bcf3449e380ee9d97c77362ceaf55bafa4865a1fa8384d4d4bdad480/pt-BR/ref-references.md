---
id: ref-references
title: Referências: documentos que a IA lê com sua pergunta
category: Prompt Library and References
keywords: [referências, documentos de referência, anexar, pdf, docx, pptx, xlsx, txt, markdown, colar texto, manter formatação original, adicionar como texto, modelo de relatório de status, escopo de trabalho, faq, sincronização]
screen: settings/prompts
---

Referências são documentos que você guarda no app para a IA ler junto com uma pergunta: um modelo de relatório de status para seguir, uma declaração de escopo para conferir, um FAQ, um briefing.

**Onde.** Ajustes > Prompts e referências > Referências. Três formas de adicionar uma: **Escrever ou colar texto**, **Adicionar como texto** (texto e Markdown vão como estão; PDF, Word e RTF viram texto editável) ou **Manter formatação original** (o próprio arquivo fica guardado, para formatos que o servidor consegue ler direto).

**Usando uma.** No Project Chat e no Meeting Chat, toque em + > Referências e prompts, marque as que você quer, e elas acompanham aquela conversa até você tirá-las. Na janela de Consulta da sessão ao vivo também tem uma seção Referências; uma referência anexada ali vai junto com todas as perguntas daquela reunião. Um Prompt rápido também pode levar uma referência toda vez que roda (edite o prompt > Referências). Uma referência vinculada a projetos ("Só estes projetos") se anexa sozinha a todas as perguntas nesses projetos; "Todos os projetos" quer dizer que ela fica disponível em todo lugar, mas nunca é anexada automaticamente.

**Quem lê o arquivo original.** No Shoulder Surf AI com plano Plus ou Pro, PDFs originais são lidos página por página (até algumas centenas de páginas), e originais de Word, PowerPoint e Excel são lidos no servidor. Em outros modelos, abaixo desse plano ou em formatos de texto simples, o que vai com a pergunta é o texto extraído. Com sua própria chave e o pacote Extras, PDFs originais vão direto para os modelos Claude, Gemini, OpenAI ou OpenRouter. Uma referência que não pode ir com o modelo atual avisa isso no seletor ("Só Shoulder Surf AI") em vez de piorar sem você saber.

**Arquivos que a IA criou** (no Project Chat ou no Meeting Chat) vão parar em Referências automaticamente, para você poder anexá-los de novo depois.

**Entre aparelhos.** As referências sincronizam pelo seu iCloud privado quando a Sincronização com o iCloud está ativada.

Arquivos do Keynote, Pages e Numbers não podem ser lidos; exporte para PDF antes. Um PDF escaneado sem camada de texto funciona no Shoulder Surf AI (ele é lido visualmente) e fica vazio nos outros modelos.
