---
id: priv-what-stays-on-device
title: O que fica no seu celular
category: Privacy
keywords: [privacidade, no dispositivo, local, áudio, gravações, transcrição, voice id, calendário, chaves de api, contatos, fotos, offline]
screen: settings/privacy
---

Quase tudo o que o Shoulder Surf faz acontece no celular, e muita coisa nunca vai para nenhum outro lugar.

**Transcrição.** A fala vira texto pelo mecanismo de fala da Apple que roda no próprio aparelho. Nenhum áudio é enviado para lugar nenhum para ser transcrito, e funciona sem sinal.

**Separação de falantes e Voice ID.** Distinguir as vozes e reconhecer as vozes cadastradas roda no celular, com modelos no dispositivo. O áudio do seu cadastro e as impressões de voz ficam no aparelho.

**Gravações de áudio.** Ficam salvas no celular junto com a reunião. O app nunca envia áudio para os servidores do Shoulder Surf por conta própria. As duas formas de o áudio sair do celular são escolhas suas: o seu próprio iCloud (Sincronização com o iCloud, ativada por padrão, e Backup no iCloud Drive, desativado por padrão), que a Apple criptografa e o Shoulder Surf não consegue ler; e um compartilhamento que você cria com "Incluir o áudio" ativado.

**Fotos.** Ficam guardadas com a reunião no celular. Uma foto só vai para uma IA quando você a anexa a uma pergunta, e só para o modelo que você escolheu.

**Calendário.** É lido no aparelho para a preparação das reuniões. Os detalhes dos eventos nunca são enviados.

**Contatos.** Só se você permitir ("Adicionar fotos dos Contatos", em Pessoas). O app lê os Contatos no celular para encontrar as pessoas das suas reuniões e guarda só a foto, o telefone, o e-mail e a empresa da pessoa encontrada. Isso fica nos seus aparelhos e no seu iCloud privado quando a sincronização está ativada; nada disso vai para os servidores do Shoulder Surf. Sem acesso, vincular uma pessoa usa o seletor da Apple e só o cartão que você escolher é guardado.

**Chaves de API.** Ficam no Keychain do iPhone, nunca são sincronizadas e nunca são enviadas ao Shoulder Surf.

**IA no dispositivo.** Com No dispositivo (Apple) selecionado, perguntas, resumos e o Meeting Chat rodam no celular; nada da reunião sai do aparelho para isso.

**Estatísticas de uso e relatórios de travamento** são a exceção ao "fica no celular" e estão descritos em "O que vai para o servidor, e quando". Eles nunca incluem transcrições, gravações ou nomes.

Ninguém no Shoulder Surf consegue ver suas gravações. Não existe nenhum caminho no app que mande áudio para nós, a não ser um link de compartilhamento que você mesmo cria.
