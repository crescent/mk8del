<div align="center">

<img src="docs/tag-ratone-tend.png" alt="RatOne — criador e mantenedor do MK8-online" width="560">

# MK8-online

### Prepare uma vez. Corra nativamente. Conecte-se com amigos.

**Criado e mantido por [RatOne](https://github.com/Vnnc1)**

[English](README.md) · **Português (Brasil)**

![Plataforma](https://img.shields.io/badge/Windows-x64-0078D6)
![Gráficos](https://img.shields.io/badge/Gráficos-Vulkan-AC162C)
![Linguagens](https://img.shields.io/badge/Rust-C%2FC%2B%2B-555555)
![Release base](https://img.shields.io/badge/Release_base-0.1.9-ef795d)
![Estado](https://img.shields.io/badge/Estado-Experimental-e6a23c)
[![Licença](https://img.shields.io/badge/Licença-GPL--3.0--or--later-blue)](LICENSE)

Projeto experimental de recompilação estática de **Mario Kart 8 Deluxe 4.0.1** para Windows,
com launcher nativo, runtime Vulkan e salas multijogador em desenvolvimento.

[Download / Releases](https://github.com/Vnnc1/MK8-online/releases) · [Relatar um problema](https://github.com/Vnnc1/MK8-online/issues) · [Roteiro](docs/ROADMAP.md)

</div>

> [!IMPORTANT]
> Use seus próprios arquivos do jogo e as chaves do seu console. O repositório e as releases não incluem ROMs, recursos do jogo, chaves, saves ou código recompilado do jogo. A compilação acontece localmente, a partir dos seus arquivos.

> [!NOTE]
> **Disponível para testes, ainda experimental.** Corridas foram concluídas em testes locais, mas persistem defeitos visuais, engasgos no primeiro uso de shaders e crescimento de VRAM. Multiplayer entre PCs diferentes ainda não foi qualificado. O instalador de testes 0.1.9 traz a aba Perfil, teclas/botões no launcher, geração de quadros, importação de save e a correção dos saves que não persistiam.

## No grid de largada

| Área | Progresso atual | Próxima etapa |
| --- | --- | --- |
| Launcher | Janela nativa, preparação guiada, instalador, atualizações e relatórios; interface em inglês e PT-BR | Atualização ponta a ponta contra uma release publicada |
| Corrida solo | Quatro pistas da Mushroom Cup concluídas, com retorno à seleção de copas | Qualificação conjunta de imagem, áudio, controle e reinício/save |
| Desempenho | Cerca de 60 FPS nos trechos medidos no PC de teste | Repetir copas, estabilizar VRAM e testar mais hardware |
| LAN / códigos de sala | Dois jogos correram no mesmo PC; transporte testado com 12 clientes | Dois PCs reais, relay na internet e mais de quatro jogadores reais |
| Tela dividida | Em investigação | Corrigir entrada na seleção de personagens para dois jogadores |
| Nextendo | Investigação prevista | Avaliar integração com este runtime nativo |

São marcos observados, sem estimativa artificial de porcentagem concluída. `gameplay_verified` permanece `false` até passar pela qualificação completa. Evidências e limitações: [estado técnico](docs/STATUS.md) e [roteiro](docs/ROADMAP.md).

## O que este projeto faz

O pipeline traduz o executável AArch64 da versão suportada para C e o compila para Windows x64. Um runtime C/C++ fornece gráficos, áudio, entrada e serviços de sistema; o launcher Rust/Tauri gerencia preparação, builds, atualizações e salas.

RatOne desenvolve e mantém a adaptação MK8, o launcher e a integração. A base do tradutor/runtime vem de **SMM2Recomp e LASRecomp**, com contribuições de outros projetos devidamente creditadas. Veja [origens e licenças de terceiros](docs/THIRD_PARTY.md).

## Entre na pista

### Requisitos

- **Windows x64** e WebView2. A etapa Ferramentas instala um **Python 3.12 x64** privado durante a etapa Ferramentas.
- GPU e driver gráfico que exponham **Vulkan 1.3 ou superior**. Vulkan 1.2 é rejeitado na inicialização; a versão da API sozinha não qualifica todos os recursos necessários nem o desempenho. Ainda não há especificação mínima geral de CPU/GPU qualificada.
- Seu jogo base, **atualização 4.0.1** e `prod.keys` do seu console, ou uma pasta extraída compatível.
- Cerca de **28 GiB livres para extração/preparação**, incluindo 4 GiB de reserva. Separe espaço adicional para os pacotes de origem, ferramentas e caches de compilação. SSD recomendado para a preparação.
- Internet para baixar as ferramentas inicialmente (cerca de 0,4 GiB instalados, antes dos caches) e atualizar. As ferramentas são compartilhadas entre versões do launcher.

Ferramentas baixa CPython fixado, verifica SHA-256 e cria o ambiente local sem alterar o Python do sistema ou o PATH. Ambientes incompatíveis ou incompletos são preservados em `local/python-backup-*` e recriados automaticamente. Disponível na 0.1.7; usuários da 0.1.6 podem atualizar pelo Suporte antes de repetir Ferramentas.

Incluído na versão de testes 0.1.9: aba **Perfil** (nome de exibição e nome/cor do Mii; o modelo Mii continua desligado nas corridas); aba **Configurações** com teclas/botões do controle, geração de quadros (120 fps em telas de 100 Hz ou mais, o jogo continua a 60), tela cheia e contador de FPS; teclado padrão do MK8 (setas: direção, X: acelerar, Z: frear, Espaço: item, Shift esquerdo: derrapar); e **Saves → Importar** para uma pasta do JKSV/Checkpoint ou um único `userdata.dat`, guardando o save atual como backup. O runtime agora monta separadamente os saves de dispositivo e de conta, e o progresso persiste entre sessões. O launcher ganhou visual no estilo do Switch e confirmações próprias. As mudanças de runtime exigem **Build** de novo após atualizar.

Incluído na versão de testes 0.1.8: a verificação de prontidão usa o Python privado; a instalação ignora configurações externas do pip e exige wheels binários com hashes verificados. Arquivos de download danificados são baixados novamente, e metadados inválidos do cache de compilação acionam recuperação em vez de interromper a preparação. Essas correções exigem atualizar da 0.1.7 para a 0.1.8. Veja o [estado técnico](docs/STATUS.md).

As verificações de retomada também cobrem recibos de geração interrompidos e hashes ausentes/inválidos do runtime preparado: arquivos derivados são regenerados das fontes verificadas. A preparação verifica a reserva de 4 GiB antes da importação/substituição do runtime e informa quando a unidade de destino está indisponível. Processos de ferramentas iniciados pelo launcher ignoram variáveis externas de inicialização do Python, preservando os imports locais dos scripts.

As correções da 0.1.8 também distinguem arquivos inacessíveis de caches corrompidos: erro de compartilhamento ou permissão interrompe a preparação sem considerar a importação/runtime afetado descartável. A substituição de cada arquivo preserva o destino anterior até conseguir publicar o novo. Origem ausente ou ilegível interrompe a limpeza do espelho; falha ao publicar um download preserva o arquivo baixado e informa o problema local. Resolva o bloqueio/acesso ou reconecte a mesma unidade antes de tentar novamente. Nenhuma permissão ou configuração de antivírus é alterada automaticamente.

A preparação nas fontes agora inclui o carregador `vulkan-1.dll` fixado ao lado do jogo e o preserva na limpeza de dependências. O SDL carrega essa DLL dinamicamente; verificar apenas imports estáticos a deixava de fora na 0.1.7. Continua sendo necessário um driver gráfico compatível com Vulkan 1.3: incluir o carregador não instala o driver da GPU.

A versão 0.1.8 também bloqueia operações concorrentes entre launchers que compartilham dados/ferramentas e entre execuções do pipeline na mesma pasta de dados. Falhas no cancelamento mantêm o processo sob controle para permitir nova tentativa. A limpeza rejeita diretórios pais com links e para diante de erros de permissão, sem forçar exclusões. Essas proteções continuam em validação; os limites restantes estão no estado técnico.

O launcher 0.1.8 supervisiona os processos separadamente: fechar ou perder o launcher interrompe suas ferramentas/builds, enquanto o jogo ativo mantém os bloqueios e pode concluir o salvamento normalmente. Outras instâncias não podem abrir outro jogo com os mesmos saves, recompilar, alterar caminhos ou iniciar atualização desses recursos. Jogos com pastas de dados diferentes podem compartilhar ferramentas somente para leitura. Fechar o launcher ainda desconecta seu gateway multijogador; isso não implementa reconexão de sessão.

### Instalar e jogar

1. Baixe `MK8-online-<versão>-setup.exe` em [Releases](https://github.com/Vnnc1/MK8-online/releases). Escolha as pastas do programa e dos dados. A instalação é por usuário, sem administrador.
2. Abra **Instalar** no launcher. Conclua **Ferramentas** e selecione jogo base e atualização em **Arquivos do jogo**. As chaves podem ser encontradas ao lado do jogo; selecione o arquivo do seu console se necessário. O launcher guarda o caminho.
3. Execute **Build do jogo**, espere terminar e escolha **Jogar solo**. A preparação mostra progresso, tempo decorrido e acesso ao log.

**Idioma:** launcher e instalador oferecem **English** e **Português (Brasil)** no seletor **Idioma / Language**. A primeira abertura segue o idioma do sistema/navegador, com inglês como alternativa. A escolha fica salva na pasta de dados e continua após reiniciar. Isso traduz a interface do launcher; o idioma do jogo é independente. Logs de ferramentas e detalhes externos de diagnóstico mantêm o idioma original.

Uma pasta extraída precisa conter `exefs`, `romfs` e `control.nacp` (ou `control/control.nacp`); selecione **Já tenho o jogo extraído numa pasta**. O perfil ativo é 4.0.1 AArch64. Outras versões, DLC e importação XCI/XCZ ainda precisam de validação.

O setup inclui o runtime host pré-compilado e um FFmpeg mínimo para vídeos; o código do jogo é compilado no seu PC. Extrações/preparações incompletas são reparadas na build seguinte. Os dados ficam por padrão em `%LOCALAPPDATA%\MK8-online` e são preservados nas atualizações e na desinstalação. Para escolher outro disco ao iniciar o launcher portátil:

```powershell
& .\Launcher.exe --data-dir E:\MK8-Local
```

Use a mesma pasta nas próximas aberturas. Pastas antigas de dados por versão não são migradas nem apagadas automaticamente.

**Atualizações:** **Suporte → Verificar atualizações** consulta as Releases do GitHub, incluindo pré-releases. **Atualizar agora** baixa o setup, verifica o SHA-256 publicado e reabre o launcher após instalar.

## Corra com amigos

### Salas LAN

Cada PC precisa preparar sua própria cópia da mesma versão do jogo. Em **Multijogador**, salve seu nome, crie ou busque uma sala e gere **Novo convite** para cada amigo. Os convidados colam seus convites individuais no launcher. O convite contém o endereço do anfitrião: compartilhe em privado.

A descoberta e o tráfego LAN usam UDP **47820** e dependem da rede/firewall. O launcher escolhe o endereço local automaticamente; **Endereço alternativo** aceita uma VPN ou um endereço com redirecionamento configurado. **Esc → Friend rooms** abre a aba de salas em builds compatíveis do runtime.

Após conectar no launcher:

1. Todos escolhem **Jogar na sala**.
2. No menu principal do jogo, segure **L + R** e pressione o **analógico esquerdo** para mostrar **LAN Play**.
3. O anfitrião escolhe **Create Room**; os convidados selecionam seu nome em **Lobby**.
4. Cada jogador escolhe personagem e kart; o anfitrião define as regras.

O jogo admite até **12 jogadores em LAN**. Os testes de transporte cobrem 12 clientes, mas corridas reais só foram testadas com duas instâncias em uma máquina. Tela dividida, dois PCs e salas cheias precisam de validação separada. Consulte **Multijogador → Como testar em duas máquinas**.

### Códigos de sala pela internet

Um launcher configurado com relay público pode criar um novo código `MK8-…` para cada sala pela internet. Os amigos colam o código para receber suas credenciais individuais. O anfitrião controla a sala do jogo; o relay encaminha os pacotes. Jogadores fazem conexões de saída, enquanto o operador do relay disponibiliza as portas do servidor.

**É necessário hospedar e configurar um relay público.** Os testes atuais usaram relay local; não comprovam conectividade pela internet nem baixa latência. A localização do relay e a conexão de cada jogador influenciam o resultado. **Online Play** oficial e comunicação local sem fio (`ldn`) não fazem parte desta integração LAN.

<details>
<summary><strong>Para operadores: configurar um relay</strong></summary>

Compile e execute no servidor, substituindo `IP_PUBLICO_DO_RELAY`:

```text
cargo build --release --locked -p mk8-session --bin mk8-relay
mk8-relay 127.0.0.1:47819 0.0.0.0 IP_PUBLICO_DO_RELAY 47820
```

Libere UDP **47820–47835**. Publique o controle `127.0.0.1:47819` atrás de proxy HTTPS com certificado válido, limite de tamanho e taxa de requisições. Mantenha o HTTP interno em loopback. Um túnel apenas HTTP não encaminha o UDP do jogo.

Defina `MK8_RELAY_URL` com esse endereço HTTPS antes de executar `python scripts/package-launcher.py` e distribua o launcher resultante aos jogadores. A variável também funciona ao iniciar uma build de desenvolvimento. HTTP em loopback é aceito para testes locais.

O daemon admite até 16 salas, cada uma com anfitrião e até 11 convidados. Elas expiram após quatro horas e também são removidas por inatividade do anfitrião. Códigos e credenciais individuais são privados.

</details>

### Nextendo: uma direção futura

Investigar a **[Nextendo Network](https://nextendo.network/)** está no roteiro para oferecer uma alternativa de serviço online. **Esta build ainda não integra Nextendo.** Contas, amigos e matchmaking exigiriam trabalho de compatibilidade e validação neste runtime nativo. É uma intenção de desenvolvimento, sem data anunciada ou vínculo presumido; não substitui o caminho LAN/relay atual.

## Desempenho, com contexto

O candidato local mais recente percorreu as quatro pistas da Mushroom Cup e voltou à seleção de copas em **18 min 19 s**. No **Xeon E5-2666 v3 / 16 GB RAM / RX 580 2048SP**, os trechos de corrida selecionados mediram:

| Pista | FPS médio | Tempo por quadro p95 |
| --- | ---: | ---: |
| Mario Kart Stadium | 59,99 | 17,46 ms |
| Water Park | 59,98 | 17,45 ms |
| Sweet Sweet Canyon | 59,97 | 17,46 ms |
| Thwomp Ruins | 59,99 | 17,33 ms |

As medições excluem janelas de captura e não garantem 60 FPS em todas as cenas ou em outros PCs. O primeiro uso de shaders ainda pode causar engasgos ou ausência temporária de efeitos durante a compilação.

## Ajude a melhorar a próxima volta

Em **Suporte → Gerar relatório**, crie um ZIP de diagnóstico e anexe a uma [issue](https://github.com/Vnnc1/MK8-online/issues), informando CPU/GPU, versão do launcher, pista/modo, passos para reproduzir e resultado esperado. Para multiplayer, inclua relatórios dos dois PCs e descreva a rede.

Os relatórios contêm logs do jogo, build e ferramentas e o manifesto da build. O launcher oculta caminhos pessoais e nomes de conta e exclui saves e chaves; revise os anexos antes de compartilhar. Os arquivos ficam em `<pasta de dados>\data\reports`; um relatório também é criado automaticamente após falha do jogo. Nunca publique ROMs, chaves do console ou convites privados.

As prioridades são estabilizar VRAM ao repetir copas, melhorar shaders/tempo por quadro e fidelidade visual, corrigir a entrada em tela dividida e testar multiplayer entre máquinas. Modelos Mii não estão integrados ao jogo; o launcher atual não oferece editor de perfis. A qualificação completa deve verificar imagem, áudio, controle físico, velocidade do relógio e reinício/save juntos.

## Compilar e contribuir

O desenvolvimento exige Rust, Python 3.12 x64, PowerShell 7 e .NET 10 x64 para o importador. Na raiz do repositório:

```powershell
.\scripts\start-launcher.ps1
```

O script baixa as fontes fixadas do upstream, verifica os hashes, compila e abre o launcher. Prepare seu jogo pelo mesmo fluxo da interface. Para empacotar o launcher portátil:

```powershell
python scripts/package-launcher.py
```

Saída: `target/portable/Launcher.exe`. Requisitos completos de empacotamento e checagens de release estão em [RELEASING.md](docs/RELEASING.md). Mantenha adaptações em `runtime/mk8` e `toolchain`; a preparação aplica as mudanças a uma cópia de build verificada. Não edite snapshots do upstream ou código gerado do jogo diretamente. Siga [AGENTS.md](AGENTS.md) e execute os testes da área alterada.

| Local | Responsabilidade |
| --- | --- |
| `apps/launcher` | Launcher nativo, interface web, instalador, atualizações e relatórios |
| `crates/installer`, `crates/manifest`, `crates/build-manager` | Instalação, validação e supervisão de builds |
| `crates/session`, `crates/transport` | Salas, convites, relay e transporte autenticado |
| `toolchain`, `runtime/mk8` | Pipeline de recompilação e adaptações MK8 do runtime |
| `games/mk8d` | Perfis de versão, símbolos e configuração do jogo |
| `scripts`, `tests` | Preparação, empacotamento e regressões |
| `upstream/manifests` | Manifestos fixados; snapshots baixados ficam fora do Git |
| `local`, `target`, `prebuilt` | Dados privados e artefatos gerados, ignorados pelo Git |

## Créditos e licença

**MK8-online é criado e mantido por RatOne.** Os autores das dependências mantêm os créditos e a titularidade de suas contribuições. Atribuições e avisos estão em [THIRD_PARTY.md](docs/THIRD_PARTY.md) e [docs/licenses](docs/licenses).

O código do projeto usa **[GPL-3.0-or-later](LICENSE)**. Dependências preservam suas próprias licenças: o snapshot SMM2Recomp inclui GPLv3, enquanto LASRecomp inclui avisos MIT. O conjunto não pode simplesmente ser reclassificado como MIT. A licença não concede direitos sobre código, recursos ou marcas da Nintendo.

Projeto comunitário independente, sem afiliação ou endosso da Nintendo. Mario Kart e nomes relacionados pertencem aos respectivos titulares.
