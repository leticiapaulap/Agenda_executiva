# Validação da Agenda Executiva

Ambiente: Windows, Python 3.14.10, CustomTkinter 5.2.2, Pillow 13.0.0 e PyInstaller 6.22.3.

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
