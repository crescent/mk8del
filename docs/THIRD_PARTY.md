# Dependências e atribuições

Referência de diagnóstico de hardware (08/11/2026): [Intel — driver gráfico para processadores de 7ª a 10ª geração](https://www.intel.com/content/www/us/en/download/776137/intel-7th-10th-gen-processor-graphics-windows.html), pacote que inclui o i3-10110U e Windows 10. Consultado para o bloqueio Vulkan 1.2.131 descrito em `STATUS.md`; o driver não é distribuído pelo projeto, e sua compatibilidade com o runtime nessa máquina ainda precisa ser testada.

O código próprio usa GPL-3.0-or-later (`../LICENSE`). Os snapshots e bibliotecas mantêm suas licenças originais. Nomes em caminhos, símbolos e identificadores serializados são contratos de integração, não alegações de equivalência entre projetos.

Licenciamento conferido em 08/11/2026: o `LICENSE` do snapshot SMM2Recomp contém GPLv3; o de LASRecomp contém MIT. A origem MIT de partes do runtime não relicencia as alterações GPL incorporadas posteriormente. Uma eventual oferta MIT de código próprio independente exige separar sua proveniência; mudar apenas o `LICENSE` principal não transforma o conjunto em MIT. A licença atual foi preservada. Referência: [GNU — titularidade e licenciamento alternativo](https://www.gnu.org/licenses/gpl-faq.en.html#HeardOtherLicense).

| Origem efetivamente utilizada | Revisão / uso | Licença preservada |
| --- | --- | --- |
| [SMM2Recomp](https://github.com/smm2recomp/smm2recomp) | `9dd1858fe8f4688ae5daa390c5e18d027300ada6`; snapshot do tradutor/runtime, verificado por `upstream/manifests/smm2recomp.json` | `upstream/smm2recomp/LICENSE`, `upstream/smm2recomp/recomp/UPSTREAM.md` |
| [LASRecomp](https://github.com/peiikai/LASRecomp) | `a6b4f03844ecfb4012f18deff96da8b410aeafcd`; origem da infraestrutura derivada | `upstream/LASRecomp/LICENSE` e avisos dos snapshots |
| [Power Moon Recomp](https://gitlab.com/zzzarathustra-dev/power-moon-recomp) | `cdbe05c9df0e2ff4bfbecece89ad3f0fd9806193`; adaptação de heap sempre protegido em `toolchain/experimental.py` | [MIT / créditos](licenses/power-moon-recomp.txt) |
| [SkyRecomp](https://github.com/Apolinario-coder/SkyRecomp) | `ad649b4fd319b7f7c309857b2d14353d2e5dcd79`; TLD4S F32 adaptado em `runtime/mk8/graphics/maxwell_gather.inc` | [MIT, Apolinario-coder, 2026](licenses/SkyRecomp-MIT.txt), [aviso LAS](licenses/SkyRecomp-LASRecomp-MIT.txt) |
| [LibHac](https://www.nuget.org/packages/LibHac/0.18.0) | 0.18.0, pacote conferido por SHA-256; leitura e aplicação da atualização NCA | [MIT](licenses/LibHac-MIT.txt) |

O setup leva o executável do runtime já compilado (`prebuilt/host`, gerado por `scripts/prebuild-host.py` a partir das mesmas fontes que vão no pacote, sem nenhum arquivo do jogo). Nele entram estaticamente mbedTLS, Dear ImGui e SMAA (licenças nos snapshots do upstream), além da libc++/libunwind do LLVM (Apache-2.0 com LLVM Exception) e da CRT do mingw-w64 fornecidas pelo Zig 0.17.0. O código-fonte correspondente acompanha o pacote.

O setup leva também um FFmpeg 9.0.2 mínimo (`prebuilt/ffmpeg`: avcodec, avformat, avutil e swscale, só com mov/matroska, h264/vp8/aac e o protocolo de arquivo), compilado por `scripts/build-ffmpeg.sh` a partir do pacote oficial ([ffmpeg.org](https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz), SHA-256 conferido) com o assembly do nasm 2.16.03. Licença LGPL 2.1 ou posterior (nenhum componente GPL ou não livre habilitado); `LICENSE.md` e `COPYING.LGPLv2.1` vão junto das DLLs, e cada release anexa o código-fonte e o script de build. Os headers e bibliotecas de importação continuam os do pacote MSYS2 fixado da mesma versão.

Os avisos MIT completos nesta pasta acompanham os trechos adaptados. Não retirar licenças dos snapshots ao organizar o código. Dependências Rust estão fixadas por `Cargo.lock`; compilador e dependências nativas pelos scripts/manifestos locais.

As demais avaliações históricas não são dependências de execução nem evidência de FPS ou compatibilidade deste projeto. Não copiar DLLs, endereços ou resultados de jogos diferentes. ROM, chaves, código gerado e modelos do dump permanecem privados.

Referência operacional do multiplayer: [Nintendo — How to Use the LAN Play Feature of Mario Kart 8 Deluxe](https://en-americas-support.nintendo.com/app/answers/detail/a_id/25961/p/897), consultada em 07/11/2026. Documenta o atalho L + R + clique no analógico esquerdo e o limite de 12 jogadores. Esse limite do jogo não constitui qualificação da nossa implementação.

Direção futura solicitada pelo mantenedor: [Nextendo Network](https://nextendo.network/), consultada em 08/11/2026. O site apresenta serviços independentes de contas, amigos e matchmaking para suas integrações com Ryujinx, Citron e Switch com CFW. O cliente Nextendo usado aqui é o do runtime SMM2Recomp (`runtime/net`); esta build só passou a guardar a escolha do backend para o MK8 (`runtime/mk8/services/net_config.c`). Login, salas e corridas pela Nextendo não foram validados com este runtime, e não há afiliação entre os projetos. A avaliação está no roteiro; os resultados divulgados pelo serviço não qualificam este projeto.

Ideia da compilação em dois níveis (12/11/2026): [Racing 8 DX Recomp](https://github.com/galarianpikachu2021-hue/Racing-8-DX-Recomp), `c2b9f30`, GPL-3.0, cujo `docs/PIPELINE.md` mede blocos quentes em `-O2` e o resto em `-O0`. Nenhum código, lista de endereços ou medição desse projeto foi copiado: a lista `games/mk8d/profiles/4.0.1-hot-functions.txt` vem do `LARECOMP_FNPROF` do nosso runtime e os tempos estão em `STATUS.md`.

Referência de metodologia solicitada pelo usuário: [Co-op Anything](https://github.com/cranebeaker/coop-anything), consultada em 07/11/2026, especialmente [falhas de rede](https://github.com/cranebeaker/coop-anything/blob/main/playbook/network-chaos-testing.md), [diagnóstico de dessincronização](https://github.com/cranebeaker/coop-anything/blob/main/playbook/desync-hunting.md) e [instâncias isoladas](https://github.com/cranebeaker/coop-anything/blob/main/playbook/multi-instance-testing.md). Útil para ampliar os testes da ponte LAN existente. Nenhum código, MCP ou relay Steam desse projeto foi incorporado; seus resultados em outros jogos não são evidência para MK8.

## Vulkan Loader distribuído com o runtime

O pacote MSYS2 `vulkan-loader-1~1.4.363.0-1` já é fixado no manifesto de dependências upstream com SHA-256 `065076fe13d39ab97eea4740596426d5ae741b5044e48e1c9495d04835c92dbe`. A DLL original passa a acompanhar a pasta do jogo; [licença original](licenses/Vulkan-Loader.txt) preservada (Apache 2.0 e avisos do projeto). Referências: [SDL_Vulkan_LoadLibrary](https://wiki.libsdl.org/SDL3/SDL_Vulkan_LoadLibrary) e [LunarG — Loader Application Interface](https://vulkan.lunarg.com/doc/view/1.4.309.0/windows/LoaderApplicationInterface.html). O loader é separado do driver Vulkan da GPU; nenhuma implementação de driver foi adicionada.

## Supervisão de processos Windows

Diagnóstico de acesso a arquivos (09/11/2026): [Microsoft — System Error Codes 0–499](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--0-499-) e [1000–1299](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--1000-1299-). Referências para diferenciar violações de compartilhamento/bloqueio, acesso negado e dispositivo indisponível. O código retornado não identifica qual produto ou processo causou o bloqueio.

Supervisão de processos Windows (09/11/2026): [Microsoft — Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) e [AssignProcessToJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject). Fundamentam a herança do job pelos descendentes e o encerramento com `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Chamadas por `windows-sys 0.61.2`, já presente no lockfile; não se aplica terminação automática ao jogo quando apenas a janela do launcher fecha.

## Python privado para ferramentas

Diagnóstico do issue #4 (09/11/2026): [metadados oficiais de lz4 4.4.5](https://pypi.org/pypi/lz4/4.4.5/json) distinguem o SHA-256 do arquivo-fonte e do wheel CPython 3.12 Windows x64. A instalação segue a documentação do pip sobre [configuração e PIP_CONFIG_FILE](https://pip.pypa.io/en/stable/topics/configuration/) e [only-binary](https://pip.pypa.io/en/stable/cli/pip_install/#cmdoption-only-binary). Os hashes e avisos das dependências permanecem preservados.

[CPython 3.12.10 x64 no NuGet](https://www.nuget.org/packages/python/3.12.10), distribuído pelos mantenedores CPython e documentado em [Using Python on Windows](https://docs.python.org/3.12/using/windows.html#the-nuget-org-packages). O bootstrap baixa o pacote original de `api.nuget.org`, SHA-256 `0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8`. A extração integral preserva `tools/LICENSE.txt` e os avisos incluídos (Python Software Foundation License e licenças de componentes). Sem redistribuição do interpretador no repositório.
