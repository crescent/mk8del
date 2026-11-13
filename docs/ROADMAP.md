# Pendências e roteiro

O MK8-online é **experimental**: ele chega ao menu, completa corridas e já rodou duas instâncias multijogador numa máquina, mas tem defeitos conhecidos. Nada aqui está qualificado (`gameplay_verified=false`). Os detalhes técnicos e as evidências de cada item ficam em [STATUS.md](STATUS.md).

Legenda: **[bloqueia]** impede o uso normal · **[visível]** atrapalha, mas dá para testar · **[validação]** funciona em teste, falta provar no uso real.

Preparação 0.1.7: Python 3.12 x64 privado baixado automaticamente, com reparo de ambientes incompatíveis/incompletos e backup. Resta acompanhar instalações em outras máquinas; detalhes em STATUS.md.

Versão 0.1.11 (testes, fork crescent/mk8del): compilação em dois níveis (funções frias sem otimização), compilação nativa ~2× mais rápida.

Versão 0.1.10 (testes, fork crescent/mk8del): correção do + na seleção de personagens (applet de controles).

Versão 0.1.9 (testes, fork crescent/mk8del): aba Perfil, teclas/botões e vídeo no launcher, importação de save e correção do save que não persistia. Gameplay continua não qualificado.

Preparação 0.1.8 (testes): recuperação de caches, isolamento Python/pip, coordenação de processos e tratamento de arquivos bloqueados/unidades indisponíveis. Carregador Vulkan incluído. Resta validar em máquinas dos relatos e com antivírus/dispositivos reais; não altera a qualificação de gameplay.

## 1. Multijogador

- **[planejado]** Investigar integração com Nextendo Network para serviços online alternativos: mapear requisitos de conta, autenticação, amigos e matchmaking no runtime nativo antes de implementar. Ainda não integrado, sem prazo ou compatibilidade comprovada. Referência em [THIRD_PARTY.md](THIRD_PARTY.md).

- **[validação]** Um erro de comunicação apareceu uma vez com duas instâncias na mesma máquina (não reproduzido na repetição). Se aparecer entre duas máquinas, testar com `MK8_ASYNC_SUBMIT=0` e enviar os relatórios dos dois PCs.

- **[validação]** Corrida entre **duas máquinas** pela LAN direta (UDP 47820 no firewall do anfitrião). O transporte e o roteiro já existem; falta repetir com dois PCs reais.
- **[validação]** Sala pela internet com **relay público** HTTPS/UDP (`mk8-relay`). Até agora só foi testado em loopback; NAT, latência e MTU não foram medidos.
- Verificado em 08/11/2026 com a build atual: relay em loopback e LAN direta com dois jogos na mesma corrida, 60 FPS, sem erro de comunicação. Corrida VS e Batalha funcionam. Vídeos dos menus corrigidos.
- **[visível]** No lobby LAN, o nome do segundo jogador aparece espelhado.
- **[visível]** Tela dividida: depois do applet de controles, a seleção de personagens para 2 jogadores ignora os botões dos dois jogadores, embora o jogo leia e receba os dois controles (diagnósticos em `toolchain/split_screen.py`).
- **[validação]** Mais de quatro jogos reais (até 12), com perda/atraso de rede e reconexão.

## 2. Imagem

- **[visível]** O halo de bloom do letreiro do estádio some em cerca de 1 a cada 4 quadros (parâmetros da cadeia secundária de bloom).
- **[visível]** Pontos coloridos, sombras e fidelidade geral da imagem.
- Corrigido: alternância de brilho da cena a 30 Hz (cubemap de ambiente lido num ID de textura reaproveitado).
- Corrigido: exposição estourada (reflexo especular BC3 gerado por shader no início da corrida escrito numa view inválida; `toolchain/bc_render.py`). 7 de 7 GPs limpos.

## 3. Desempenho (prioridade atual: VRAM)

- **[visível]** A memória de vídeo cresce ao trocar de pista. Foram corrigidas listas de comandos que sobreviviam à finalização de seus buffers/pools; algumas imagens antigas grandes continuam pertencendo a texturas não finalizadas pelo jogo. O candidato com blocos VMA de 128 MiB percorreu quatro pistas e voltou à seleção de copas, com ~60 FPS nos trechos medidos e pico reservado de 1.648 MiB no heap 0. Falta isolar o efeito dos blocos menores e verificar estabilização repetindo copas/pistas; a memória ativa caiu no menu, mas a reserva permaneceu.
- **[validação]** A liberação direta de imagens de pools permanece opt-in (`MK8_RELEASE_POOL_TEXTURES=1`). A possível regressão de FPS no GP de Thwomp Ruins não se repetiu no Contra o Relógio parcial, que não teve despachos de compute no trecho medido; a dependência específica ainda não foi identificada.

- **[visível]** Primeiro uso de efeitos: com a espera limitada (padrão, 4 ms por quadro), o efeito pode faltar por alguns quadros em vez de travar a tela; o cache em disco resolve nas corridas seguintes.
- **[visível]** Picos menores de 25–45 ms ao entrar em áreas novas (prefetch de shaders) e na tradução de comandos.
- Melhorado: anel de 32 envios, tradução de comandos numa thread própria e espera limitada de shaders. No GP completo: 59,99 FPS, p95 17,3 ms, p99 17,6 ms, pior quadro 38 ms (no início do dia: 57,9 FPS, p95 21,2 ms, picos de 160–370 ms). O jogo roda a 99,7% da velocidade real.
- Corrigido: travamento (`c0000005`) no início da corrida ao comparar samplers copiados pelo jogo.
- **[validação]** 60 FPS estáveis e velocidade correta do jogo: o MK8 avança um passo por quadro, então abaixo de 60 FPS o jogo fica em câmera lenta.

## 4. Qualificação da corrida

- **[validação]** Áudio, controle físico, reinício/save, relógio e imagem verificados juntos numa corrida inteira.
- **[validação]** Modelos Mii dentro do jogo (cabeça, corpo, retratos). Hoje o jogo roda sem modelos Mii.

## 5. Launcher e distribuição

- Incluído no setup 0.1.6: inglês e PT-BR no launcher e instalador, com seleção e preferência persistente. Os READMEs acompanham os dois idiomas.
- **[validação]** Antes de tornar o repositório público, revisar nome/e-mail de autoria e commit no histórico Git. A revisão de 08/11 encontrou esses metadados pessoais nos 21 commits alcançáveis; a varredura de conteúdo não encontrou credenciais pelos padrões utilizados. O histórico não foi reescrito.

- **[validação]** Atualização pelo botão **Atualizar agora** contra um Release publicado de verdade (os testes unitários cobrem versão, asset e SHA-256).
- Instância única: duas janelas do Launcher competiriam pela mesma pasta de dados.
- Fechar a janela não interrompe uma preparação ou o jogo em andamento.
- Importação XCI/XCZ, DLC e outras versões do jogo estão fora do escopo até o percurso atual ser qualificado.

## 6. Plano: build, desempenho e launcher (12/11/2026)

Vem da comparação com o Racing 8 DX Recomp (ideias apenas; [THIRD_PARTY.md](THIRD_PARTY.md)) e da compilação em dois níveis já feita ([STATUS.md](STATUS.md)). Cada item é um commit próprio com seu teste. Itens de desempenho só entram com A/B medido: dois GPs por variante (`scripts/test-race.py` com `LARECOMP_VK_PROF=1 MK8_FRAME_TIMES=1`, `scripts/frame-stats.py`). Itens de interface exigem teste no navegador, `cargo fmt` e Clippy.

Já conferido no código, fora do plano: o despacho de funções (`runtime/dispatch.c` do upstream) usa cache por thread sem atômicos nem leitura de MXCSR, e `FN_ENTER` custa um desvio previsto quando o perfil está desligado. Só voltar a isso se o amostrador mostrar `dispatch_slow` entre as funções mais caras.

### Fase A — fechar a compilação em dois níveis

1. Feito: `test-race.py` confirma a pista pelo arquivo `Course/` lido e tem `--character-picker` para saves cujo personagem abre o seletor de cores.
2. Feito: `--fnprof` une os perfis à lista. Cobertas uma pista por copa, GP e Batalha com derrapagem e itens. Funções chamadas uma só vez ficam frias; compilação com cache vazio em 137 s.
3. Feito em parte: chamadas a funções frias em pistas fora da lista ficaram em 0,05–0,07% e o GP continua a 60 FPS. Faltam tela dividida (depende da correção da seleção para 2 jogadores) e LAN (portar `test-multiplayer.py` para a pasta de dados atual).

### Fase B — build mais rápida e mais segura

4. Feito: a identidade do cache de objetos cobre só os headers que o código compilado alcança por `#include` (6 dos 24: `cpu.h`, `simd.h`, `simd_fp.h`, `game.h`, `gamelib.h`, `module_config.h`). Teste em `tests/test_native_cache.py`.
5. Feito: antes de gravar `candidate.json` (build completa e `--runtime-only`), o jogo abre numa janela oculta e numa pasta de dados temporária até capturar a tela-título (quadro 360, ~15 s). Falha, travamento ou saída antecipada não publicam o candidato e deixam `boot-check.log` na pasta de dados; `MK8_SKIP_BOOT_CHECK=1` pula. Testes em `tests/test_pipeline.py`.
6. Feito: `games/mk8d/profiles/<perfil>-patches.json` (opcional; nenhum ativo) lista `module`, `offset`, `original`, `patched` e `reason`, em hexadecimal. O gerador confere a palavra original e traduz uma cópia descomprimida do módulo; palavra diferente interrompe a build. Teste com módulo sintético comprimido em `tests/test_pipeline.py`.

### Fase C — desempenho

7. **Tela dividida a 60 FPS.** Depende da correção dos controles na seleção para 2 jogadores (seção 1). Conferir e desmontar a instrução em `main+0xafece8` no nosso `main`, aplicar pelo item 6 como opção desligada por padrão e medir 2–4 jogadores. A velocidade do jogo tem de continuar correta.
8. **Bloqueado — predefinição Notebook.** A verificação prévia falhou: qualquer `resolution` abaixo de `match` (75%, 50%) derruba o MK8 no início da corrida por falta de VRAM (`VK_ERROR_OUT_OF_DEVICE_MEMORY`), e desligar o SMAA não mudou o tempo de GPU. Antes da predefinição: corrigir a alocação das render targets em escala reduzida e entender por que `antialias`/`upscaler` do `mk8d.ini` não aparecem no pós-processamento. Detalhes em STATUS.md.
9. VRAM entre pistas e picos de shader continuam na seção 3; não duplicar aqui.

### Fase D — launcher e UX

10. Feito: antes da importação longa, `toolchain/import_mk8.py` (`check_packages`) identifica cada arquivo pelos cabeçalhos e dá mensagem própria para XCI/XCZ, arquivo que não é NSP/NSZ, outro jogo, DLC, atualização de outra versão, base diferente, base ou atualização repetida, seleção ausente e prod.keys sem `header_key` ou sem as chaves da geração usada. Pacotes nos campos trocados são aceitos. O launcher mostra o motivo da falha em vez do texto genérico, em PT-BR e inglês. Testes em `tests/test_import_mk8.py` e `crates/build-manager`.
11. Feito: `Launcher.exe --play` inicia o jogo pelo mesmo caminho do botão Jogar (solo), sem abrir a janela. Sem build pronta, com o jogo já aberto ou se o jogo fechar com erro, o launcher abre com a mensagem (e o relatório da falha em Suporte). Fechar o jogo normalmente encerra tudo sem janela. O atalho na área de trabalho com `--play` ficou para depois.
12. **Mods (uma pasta).** O runtime já aceita `RECOMP_MOD_DIR`, e o upstream tem uma página de mods no menu do jogo (`runtime/ui/mods_page.mm`). Primeiro verificar se ela funciona no Windows. Depois, aba no launcher que lista `data/mods/*/romfs`, ativa uma e passa a variável ao abrir. Combinar vários mods não está planejado.
13. **Menu dentro do jogo.** O menu ImGui do upstream (`runtime/ui/menu.mm`) já é compilado no Windows (`toolchain/host.py`). Levantar o que ele mostra, esconder opções de outros jogos (vida, rupias e afins), garantir que launcher e menu leiam e gravem o mesmo arquivo de configurações e documentar como abrir. Pronto quando uma mudança no menu persiste e aparece no launcher.
14. **Controles por dispositivo.** Começar pela tela de controles do jogo (`runtime/ui/controls_ui.mm`): perfil por GUID SDL e associação apertando o botão. Atribuição automática de 2–4 jogadores só depois da correção da tela dividida.

### Fora do plano

FSR 3/4, DLSS e HDR (o renderizador é outro; custo alto), DLC, editor de Mii e mais de 60 FPS reais (`frame_gen = 120` já é interpolação). A compilação `-O2` das funções mais quentes só entra se um A/B mostrar a thread do jogo limitada pela CPU.

Ordem sugerida: A → 4, 5 → 10, 11 → 8 → 6, 7 → 12, 13, 14. Nenhum item altera `gameplay_verified`.

## Como ajudar

1. Instale pelo `setup.exe` do Release mais recente e prepare com os arquivos do **seu** console.
2. Ao encontrar um problema: **Suporte → Gerar relatório**. Abra uma issue com o `.zip`, o que você fez e o que esperava.
3. Para testar multijogador em duas máquinas, siga **Multijogador → Como testar em duas máquinas** e envie os relatórios dos dois PCs.
