# Validação da Agenda Executiva

Ambiente: Windows, Python 3.14.10, CustomTkinter 5.2.2, Pillow 13.0.0 e PyInstaller 6.22.3.

## Calendário compacto e central de alertas — 05/11/2026

- **125 testes automatizados aprovados** com `.venv\Scripts\python.exe -m unittest discover -s tests -v`. Novos testes cobrem a janela de aviso de compromissos, fuso `America/Sao_Paulo`, tarefas concluídas/vencidas, contas pagas/vencidas/próximas, leitura/adiamento, cadastro e exclusão de lembrete manual, persistência após reinicialização e detalhe diário do calendário.
- A migração aditiva `0006_reminder_center` foi aplicada em bancos SQLite temporários, preservando as tabelas existentes. A cadeia foi compilada offline para PostgreSQL e gerou as tabelas/índices esperados; não foi conectada a uma instância Render/PostgreSQL real.
- Teste real em Microsoft Edge headless com dados fictícios: calendário com dez eventos exibiu três itens e `+ 7 mais`; célula desktop de 84 px (redução de 28% frente ao mínimo anterior de 117 px). Em 390 px, o calendário permite rolagem horizontal interna, sem overflow da página; `/reminders` também não teve overflow nem erros JavaScript.
- Timestamps de lembretes manuais são persistidos como UTC naive e convertidos para o horário local de São Paulo na exibição. Os estados de leitura/adiamento dos alertas derivados são individuais por usuário.
- Dependências não foram alteradas. Não houve acesso ou alteração de dados reais, deploy, teste de PostgreSQL Render ou reconstrução do executável desktop. A aparência foi verificada em Edge para desktop/mobile, mas não em todos os navegadores e dispositivos.

## Aprimoramento do cofre com senhas — 05/11/2026

- **114 testes automatizados aprovados** com `.venv\Scripts\python.exe -m unittest discover -s tests`. São 24 testes específicos em `test_vault.py`, 4 da ferramenta desktop em `test_vault_admin.py` e 86 testes de regressão. Todos usam bancos temporários e chaves próprias de teste.
- AES-256-GCM validado efetivamente: ida/volta autorizada, nonce aleatório, texto Unicode e espaços preservados, adulteração recusada, ciphertext vinculado ao registro e à versão da chave, chave ausente/incorreta e configuração inválida sem exposição. Nunca é usado `SECRET_KEY` como substituto.
- CRUD de senhas: cadastro opcional, atualização, edição com campo vazio preservando ciphertext, remoção explícita, exclusão do acesso e persistência após reiniciar. Listagem e HTML inicial sem senha ou ciphertext; registro corporativo bloqueado no serviço e pelo banco.
- Web: autenticação, sessão recente, CSRF, reautenticação por operação, cinco falhas com bloqueio persistente de 15 minutos, ausência de eco de segredos em erros e proteção contra remoção via representação alternativa `clear_password=true`.
- Migração `0004_vault_passwords` testada sobre catálogo web `0003` e SQLite local esquema 3, preservando IDs/campos antigos e gerando backup no desktop antes de passar ao esquema 4. SQL PostgreSQL compilado offline; `db check` sem diferenças de esquema.
- Rotação web e desktop testadas: validação sem gravar, aplicação com chave nova, preservação de metadados, backup desktop e rollback integral quando um registro falha. Perda de chave nunca apaga automaticamente o ciphertext.
- `tests.smoke_vault` e `tests.smoke_features` aprovados com widgets CustomTkinter reais: campo mascarado, autenticação local incorreta/correta, substituição/preservação da senha, olho/cópia, conteúdo alheio preservado no clipboard e catálogo sem hash/chaves.
- `tests.smoke_vault_web` aprovado em **Microsoft Edge headless**, com servidor Flask localhost e SQLite temporário: cadastro/edição, HTML inicial sem texto puro, olho e modal de reautenticação, cópia pela API real do clipboard, máscara e limpeza condicional aos 30 segundos (relógio simulado), preservação de conteúdo posterior no clipboard, modo indisponível e largura de 390 px sem transbordamento. Nenhum erro JavaScript.
- Dependência `cryptography==50.0.2` instalada; Playwright é apenas dependência opcional de testes. Não foram consultados segredos reais, bancos pessoais ou Render. Nenhuma chave foi gerada para produção, nenhum push/deploy foi feito e o executável desktop não foi reconstruído.
- Limites: PostgreSQL real e concorrência com múltiplos workers ainda precisam de homologação; não há auditoria externa nem suporte a senhas corporativas. O teste de clipboard não certifica políticas de todos os navegadores/sistemas. Configuração, custódia e recuperação das chaves estão em [COFRE.md](COFRE.md).

As seções abaixo são registros históricos anteriores ao aprimoramento do cofre, incluindo o modo catálogo então disponível. As restrições e contagens antigas não descrevem a implementação atual.

## Atualização anterior de catálogo e contas — 05/11/2026

- **86 testes automatizados aprovados** com `.venv\Scripts\python.exe -m unittest discover -s tests`, usando somente bancos temporários. Incluem CRUD, centavos/Decimal, filtros/totais, vencimentos 29/30/31 e bissextos, recorrência sem duplicação, pagamentos idempotentes, histórico após exclusão, persistência, calendário, lembretes 7/3/1 e regressões dos módulos anteriores.
- As revisões `0002_finance` e `0003_access_catalog` foram aplicadas a um banco temporário que já estava na revisão `0001_create_agenda_tables`, contendo compromisso fictício. Todas as colunas do registro permaneceram idênticas. `flask db check` não detectou diferenças entre o esquema migrado e os modelos.
- SQL das migrações compilado para o dialeto PostgreSQL em modo offline, sem conexão com servidor. A execução transacional e a concorrência em PostgreSQL real **não foram validadas**; ainda é necessária homologação no Render.
- Segurança do catálogo: login, CSRF, escape HTML, `no-store`, recusa de campos de credenciais e URLs com credenciais/parâmetros, ausência de rotas de revelação/cópia, rejeição sem eco de segredo submetido, persistência após nova instância Flask.
- **Criptografia/descriptografia e autenticação adicional não implementadas nem certificadas.** O cofre permanece em modo catálogo sem usuários/e-mails/senhas. Os requisitos para ativação, rotação e recuperação estão em [ACESSOS_E_CONTAS.md](ACESSOS_E_CONTAS.md).
- Comprovantes: PNG/JPEG validados e regravados, limites de tamanho, nomes opacos, rejeição de conteúdo inválido/extensão divergente/caminhos arbitrários, download autenticado, configuração fora da pasta web e limpeza em falha de banco. Injeção de uma falha de banco verificou que o detalhe não aparece em respostas ou logs.
- Validação de backups ampliada para os esquemas novos, vínculos e unicidade. Bancos incompatíveis ou incompletos são recusados antes da substituição dos dados atuais.
- `tests.smoke_features` aprovado em sessão gráfica Windows, com SQLite temporário: navegação, catálogo, formulários de contas, edição, recorrência, pagamento, histórico, recibo privado, calendário, alerta persistido sem repetição e ajuste horizontal em 1000×650.
- `tests.smoke_actions` aprovado para regressão gráfica dos compromissos, filtros, atalhos, persistência e escala 110%. Nenhuma captura anterior foi substituída nesta atualização.
- Compilação sintática (`compileall`) e dependências (`pip check`) aprovadas. Esta atualização não reconstruiu o executável; para distribuir a versão desktop nova, execute o build do README.
- Revisão da responsividade web baseada nos templates e CSS existentes: filtros adaptados e tabelas com rolagem horizontal. Não houve certificação visual em navegador nem auditoria externa de segurança.
- Nenhum banco pessoal, variável de produção ou serviço Render foi acessado/modificado. Nenhum push ou deploy foi realizado.

As seções abaixo são registros históricos de versões anteriores, inclusive os testes de esquema SQLite versão 2 e do executável então gerado. Não representam intervenções desta atualização no banco pessoal.

## Resultados registrados

- Geração de `assets/app.png` e `assets/app.ico`: concluída; PNG inspecionado visualmente.
- Compilação sintática de `scripts/create_icon.py` e `AgendaExecutiva.spec`: aprovada.
- Versões das dependências instaladas: confirmadas com `pip show`.
- Verificação de dependências com `pip check`: aprovada.
- Testes automatizados: **31 testes unitários aprovados**, incluindo persistência, validação, conflitos, edição/exclusão por ID com títulos repetidos, backups, restauração, integridade e notificações. Os testes usam bancos temporários.
- Teste gráfico `tests/smoke_ui.py`: aprovado em todas as cinco seções, vazias e preenchidas, nas dimensões 1240×820 e 1000×650; verificação recursiva dos widgets sem transbordamento horizontal. Inclui salvar/editar, validações, conflito com cancelamento/confirmação, exclusão com cancelamento/confirmação, conclusão/reabertura, filtros, tema claro/escuro, escala, backup/restauração, lembrete interno e persistência após fechar.
- Capturas finais de dados fictícios inspecionadas: [painel](screenshots/painel.png) e [calendário](screenshots/calendario.png).
- Corrigida e retestada a limpeza dos callbacks de escala dos menus no CustomTkinter 5.2.2; antes da correção, mudar a escala após navegar poderia tentar atualizar menus destruídos.
- Empacotamento final com `scripts/build.ps1`: reconstruído após as últimas correções, com código de saída 0 em Windows 11, usando PyInstaller 6.22.3 e Python 3.14.10. Resultado: `dist/AgendaExecutiva/AgendaExecutiva.exe`, em distribuição de pasta com dependências e recursos locais. Nenhum banco pessoal foi incluído no pacote.
- Executável final aberto com diretório de trabalho temporário, fora do projeto. Janela principal `Agenda Executiva`/`TkTopLevel` confirmada, encerramento normal com código 0, nenhum banco criado na pasta de execução e registro original preservado. Apenas o processo iniciado para o teste foi encerrado; a janela da versão antiga não foi fechada.

## Preservação dos dados existentes

A migração do banco real foi executada e conferida sem cadastrar dados de teste nele:

- Registros existentes antes e depois: **1**.
- As **9 colunas originais** foram comparadas antes e depois e permaneceram idênticas, incluindo o identificador do registro.
- SHA-256 da comparação dos dados originais: `5eec303632f056843b93f0cde27a011cb561bdc6a129c102a1b7c77021467912`.
- `PRAGMA integrity_check`: `ok`.
- Versão final do esquema: `2`.
- Backup anterior à migração confirmado como cópia exata: `%USERPROFILE%\AgendaExecutiva\backups\agenda_antes_migracao_20261104_100825_881885.db`.

O relatório omite o conteúdo pessoal do compromisso preservado.

## Atualização do gerenciamento de compromissos

A revisão reutilizou `AgendaService.save_event`, os métodos de conclusão/exclusão do repositório e a atualização de telas já existentes. Nenhuma alteração de esquema foi necessária.

- Menu único `⋯` nas linhas do painel, calendário e resultados filtrados, com Editar, Concluir/Reabrir, Reagendar, Duplicar e Excluir.
- `tests/smoke_actions.py` aprovado em SQLite temporário: cadastro, edição de todos os campos mantendo o ID original, leitura por duplo clique, conclusão/reabertura, duplicação com novo ID e estado pendente, reagendamento, confirmação Cancelar/Excluir com o título, títulos repetidos e atualização do painel, calendário, lista e pendências. Conferidas a gravação imediata por outra instância do repositório e a persistência após reabrir.
- Reproduzido e corrigido o defeito que os testes por `.invoke()` não capturavam: `<Button-1>` propagava do botão `⋯` até a janela principal; o binding global de fechar menu destruía o menu recém-aberto antes de qualquer ação. A rotina agora ignora cliques no botão âncora e em seus componentes internos, mantendo o fechamento para cliques externos.
- A integração de ações injeta sequência `<ButtonPress-1>`/`<ButtonRelease-1>` nos widgets CTk nativos, confirma callback e ID, e verifica visibilidade, janela transitória e grab/foco dos diálogos de edição e exclusão. A automação de cursor do Windows não conseguiu assumir a janela em primeiro plano nesta sessão; portanto, o teste valida o encaminhamento de eventos Tk, não um clique de hardware físico.
- Instrumentação INFO e `logging.exception(..., exc_info=...)` registra módulo/DB ativos, ação/ID, abertura e foco de diálogos, INSERT/UPDATE/DELETE e redesenho, além de tracebacks não tratados em callbacks.
- Filtros de pendências e próximos sete dias testados separadamente e combinados. Itens concluídos ou reagendados para fora do período deixam os resultados; calendário e indicadores refletem as operações sem reinicialização.
- Clique simples não altera dados. Abertura de detalhes, cancelamento da cópia e cancelamento do reagendamento também não gravam alterações. Duplicação preserva os dados de origem e não copia o histórico de notificações.
- Menu testado com Escape, perda de foco, setas e Enter, nas escalas de 100% e 110% e janelas de 1240×820 e 1000×650.
- Regressão `tests/smoke_actions.py` e os 31 testes unitários aprovados. Compilação sintática aprovada. Capturas do painel e calendário atualizadas; [menu de ações inspecionado](screenshots/acoes.png), com dados fictícios.
- Executável reconstruído com sucesso após a atualização, aberto em diretório temporário e encerrado normalmente com código 0. Os registros do banco pessoal foram comparados antes/depois e permaneceram idênticos; nenhum dado de teste foi cadastrado nele.

Arquivos de código alterados: `agenda/ui.py`, `agenda/dialogs.py`. Arquivos novos: `agenda/event_menu.py`, `tests/smoke_actions.py`. Atualizados também README, este relatório, capturas e distribuição `dist/AgendaExecutiva`.

## Limites da validação

A API WinRT aceitou o envio de uma notificação nativa (`True`). O banner visível do Windows não foi certificado; sua exibição depende das configurações do sistema. O aviso interno da aplicação é exibido independentemente do toast nativo. O uso offline é suportado pelos recursos e banco locais; esta sessão não desativou a rede do computador para testá-lo.

## Comandos reproduzíveis

```powershell
.\.venv\Scripts\python.exe -m compileall -q agenda agenda_executiva.py scripts
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.smoke_ui
.\.venv\Scripts\python.exe -m tests.smoke_actions
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

Os testes devem usar bancos temporários. Nunca use o banco pessoal para inserir ou excluir registros de teste.
