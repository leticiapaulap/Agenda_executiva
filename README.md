# Agenda Executiva

Aplicativo desktop em português brasileiro para organizar compromissos e tarefas no Windows. A interface usa CustomTkinter e o armazenamento usa SQLite. O uso diário funciona sem internet e não exige conta ou serviços externos.

## Instalação e execução

Use Python **3.14** para Windows com Tcl/Tk e pip instalados. No PowerShell, abra a pasta do projeto e execute:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe agenda_executiva.py
```

Se `python` abrir a Microsoft Store, use o caminho completo do interpretador instalado para criar o ambiente. Neste computador:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m venv .venv
```

No PyCharm, selecione `.venv\Scripts\python.exe` como interpretador e execute `agenda_executiva.py`. Não é necessário ativar o ambiente virtual para usar os comandos acima. A instalação inicial das dependências precisa de internet; após a instalação, o aplicativo funciona localmente.

## Uso diário

- **Painel Inicial:** saudação, data, próxima reunião e resumo de compromissos e tarefas.
- **Calendário:** navegue pelos meses, selecione um dia e veja sua agenda.
- **Compromissos:** cadastre, edite, pesquise, filtre, conclua e exclua com confirmação. Datas usam `DD/MM/AAAA`; horários usam `HH:MM`, em formato de 24 horas. A indicação de conflito permite revisar o horário ou manter o cadastro.
- **Tarefas:** organize pendências por prazo e prioridade; conclua ou reabra atividades.
- **Configurações:** nome do gestor, lembretes, aparência e backup/restauração.
- **Cofre de Senhas:** catálogo e senhas pessoais opcionais criptografadas, com máscara, olho/cópia e reautenticação. Exige chave exclusiva configurada fora do banco; acessos corporativos continuam somente no catálogo. Veja [configuração, migração e recuperação](docs/COFRE.md).
- **Contas e Vencimentos:** contas únicas ou mensais, filtros, totais, pagamentos, histórico, vencimentos no calendário e comprovantes privados PNG/JPEG.

Consulte [escopo e segurança dos novos módulos](docs/ACESSOS_E_CONTAS.md), incluindo recorrência nos dias 29–31, limitações dos lembretes e backup separado dos comprovantes. O cofre só permite senhas pessoais quando as chaves e a autenticação adicional estão configuradas.

Cada compromisso exibe um único botão **⋯**, tanto no painel quanto no calendário e na lista filtrada. O menu permite **Editar**, **Concluir/Reabrir**, **Reagendar**, **Duplicar** e **Excluir**. Um duplo clique no compromisso abre os detalhes completos em modo de leitura; um clique simples não altera nem exclui registros. O clique que abre o menu não o fecha: cliques externos fecham, mas os cliques no próprio botão **⋯** e no menu são preservados.

**Editar** carrega todos os dados e atualiza o mesmo compromisso pelo ID; após salvar, painel, calendário e listagem são atualizados. **Reagendar** abre um formulário só com a data e os horários, preservando os demais dados. **Duplicar** abre uma cópia preenchida e cria um novo compromisso pendente somente ao clicar em **Salvar cópia**; cancelar mantém o original intacto. Os conflitos de horário continuam sendo avisados. A exclusão abre uma confirmação com o título e os botões **Cancelar** e **Excluir**.

Cada operação confirmada grava imediatamente no SQLite e atualiza a tela. Os filtros ativos são mantidos: concluir um item em **Pendentes** ou reagendá-lo para fora de **Próximos 7 dias** faz com que ele saia daquela lista. Os indicadores e o calendário consultam os dados atualizados, sem reiniciar o aplicativo. O menu fecha ao clicar fora ou pressionar **Esc**; use as setas e **Enter** para navegar pelo teclado.

Ao excluir um compromisso, a confirmação pergunta **“Deseja realmente excluir o compromisso [nome]?”**; cancelar mantém os dados intactos.

Os lembretes dependem do aplicativo **aberto**, inclusive quando minimizado. As antecedências incluem 5, 10, 15 e 30 minutos e 1 hora; opções legadas continuam aceitas. O histórico de alertas é persistido para evitar repetição após reiniciar. O aplicativo sempre exibe um aviso interno e também tenta apresentar uma notificação nativa do Windows. A exibição nativa depende das permissões de notificações e do modo Não perturbe do sistema. Não há serviço que continue executando após fechar a janela.

Atalhos: **Ctrl+N** abre um cadastro, **Ctrl+F** abre a pesquisa e **Ctrl+Enter** salva o formulário. **Esc** fecha o formulário sem salvar. Compromissos novos ou editados exigem término posterior ao início, no mesmo dia; os registros antigos sem término continuam visíveis e preservados.

## Banco existente e backups

O banco permanece no mesmo local da versão anterior:

```text
%USERPROFILE%\AgendaExecutiva\agenda.db
```

Esse caminho independe do diretório de execução e da pasta do executável. O banco não é incluído no pacote distribuído. Atualizar o aplicativo não remove os registros. A inicialização aplica migrações aditivas e cria um backup antes de modificar um banco existente. As cópias automáticas ficam em `%USERPROFILE%\AgendaExecutiva\backups`. Os compromissos já cadastrados, incluindo os classificados como Tarefa, continuam acessíveis na seção Compromissos; novas tarefas têm armazenamento próprio.

Erros e eventos técnicos são registrados em `%USERPROFILE%\AgendaExecutiva\agenda.log`, com rotação de até dois arquivos anteriores de aproximadamente 1 MB cada.

Em **Configurações**, use **Backup** para salvar uma cópia consistente do SQLite em uma pasta de sua escolha. Guarde cópias também em outro disco quando necessário. Para recuperar dados, use **Restaurar**, selecione um backup e confirme: a restauração substitui o conteúdo atual pelo conteúdo do arquivo selecionado, após validá-lo e criar uma cópia de segurança do banco atual. Ela não mescla agendas. Não substitua o arquivo manualmente enquanto o aplicativo estiver aberto. Feche outras instâncias antes de restaurar.

## Gerar executável Windows

O pacote é gerado no próprio Windows, com Python 3.14:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

O resultado fica em `dist\AgendaExecutiva\AgendaExecutiva.exe`, com ícone próprio e sem terminal. Distribua **toda a pasta `dist\AgendaExecutiva`**, incluindo `_internal`. O formato de distribuição é uma pasta com um `.exe` principal e suas dependências. O usuário final não precisa instalar Python nem estar conectado à internet.

O script pode receber outro interpretador com `-Python "C:\caminho\python.exe"`. Para executar o PyInstaller diretamente:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm AgendaExecutiva.spec
```

O arquivo `.spec` inclui os recursos do CustomTkinter e os ícones. As notificações locais usam Windows PowerShell/WinRT e não exigem pacote adicional. Consulte as referências de [empacotamento do CustomTkinter](https://github.com/TomSchimansky/CustomTkinter/wiki/Packaging) e [uso do PyInstaller](https://pyinstaller.org/en/stable/usage.html). O suporte ao Python 3.14 foi adicionado no [PyInstaller 6.16](https://pyinstaller.org/en/v6.16.0/CHANGES.html).

Dependências verificadas neste ambiente: Python 3.14.10, CustomTkinter 5.2.2, Pillow 13.0.0 e PyInstaller 6.22.3. As versões estão fixadas nos arquivos de requisitos.

## Verificações

```powershell
.\.venv\Scripts\python.exe -m compileall -q agenda agenda_executiva.py scripts
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.smoke_ui
.\.venv\Scripts\python.exe -m tests.smoke_actions
.\.venv\Scripts\python.exe -m tests.smoke_features
.\.venv\Scripts\python.exe -m tests.smoke_vault
```

Para o teste opcional do cofre em navegador real, com Microsoft Edge instalado:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m tests.smoke_vault_web
```

Os testes automatizados usam bancos temporários. O teste `smoke_ui.py` precisa de uma sessão gráfica do Windows e gera capturas com dados fictícios em `docs/screenshots`. Os testes unitários e os fluxos de ações verificam edição, exclusão, cancelamento, títulos repetidos e persistência após reabrir o banco. Consulte [o relatório de validação](docs/VALIDACAO.md).

Se aparecer `ModuleNotFoundError: No module named 'customtkinter'`, o arquivo foi aberto com outro Python. Use o comando com `.venv\Scripts\python.exe`, configure esse interpretador no PyCharm ou abra `dist\AgendaExecutiva\AgendaExecutiva.exe`. O executável já inclui as dependências. Encerre a janela da versão anterior antes de iniciar o uso diário da nova versão.

## Arquitetura

- `agenda_executiva.py`: ponto de entrada do desktop.
- `agenda/`: interface, regras de negócio e persistência em módulos separados.
- `agenda/storage.py`: repositório SQLite, migrações, configurações e backups.
- `agenda/services.py`: validação, conflitos e regras da agenda.
- `agenda/ui.py`, `agenda/dialogs.py`, `agenda/theme.py`: navegação, formulários e identidade visual.
- `agenda/notifications.py`: notificações locais opcionais do Windows, sem dependência adicional.
- `assets/`: ícones locais.
- `scripts/`: geração de ícone e empacotamento.
- `tests/`: verificações automatizadas isoladas dos dados pessoais.
- `app.py` e `webapp/`: versão web Flask com autenticação, interface responsiva e PostgreSQL.
- `migrations/`: revisões versionadas do esquema hospedado; não reverta a migração inicial.

As versões desktop e web são independentes: o desktop continua usando o SQLite local, enquanto a web exige PostgreSQL em `DATABASE_URL` e login individual. A publicação não envia nem importa automaticamente o banco local. A versão web importa as credenciais de bootstrap do administrador uma única vez para `users.password_hash`; mudanças posteriores em `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` não sobrescrevem contas persistidas. O menu **Usuários** permite administrar perfis, acessos e sessões, e a alteração da própria senha exige a senha atual. O guia do Render documenta diagnóstico e recuperação de administrador inclusive no plano Free sem Shell, com backup e autorização temporária de uso único. A seção web **Senhas** reutiliza o cofre AES-GCM e a permissão existente; a migração de usuário é aditiva, mantém as tarefas antigas na tabela original e oferece consulta administrativa somente leitura. Nenhuma variável nova é necessária se o cofre já estiver configurado. O calendário compacto reúne compromissos, tarefas e vencimentos, e a Central de Alertas exibe apenas compromissos dentro da antecedência configurada, tarefas e contas próximas/vencidas e lembretes manuais. As datas e horas desses alertas seguem `America/Sao_Paulo`. Consulte [o guia do Render](docs/RENDER.md) para migrações e validação.

Na web, **Calendário** e **Compromissos** também permitem exportar PDF, Excel e ICS com filtros de período e prioridade; os arquivos são privados e preparados em memória. A Central de Alertas abre o cadastro e a edição de lembretes manuais em modal, sem formulário permanente. Consulte a seção de [exportação e lembretes no guia do Render](docs/RENDER.md#9-exportação-e-edição-de-lembretes) para permissões e testes.

Na versão web, a antiga URL `/tasks` abre **Senhas** em `/vault`. Os POSTs antigos de tarefas respondem `410 Gone`, preservando os registros. Administradores consultam `/tasks/archive` somente para leitura; pesquisa, calendário e lembretes mantêm a visibilidade das tarefas antigas conforme as permissões. O gerenciador permite cadastrar, editar, pesquisar, excluir com confirmação, copiar usuário e abrir o site. Revelar ou copiar senha exige reautenticação por operação. Se o cofre ainda não estiver habilitado, configure `VAULT_ENABLED`, `VAULT_KEYS` e `VAULT_ACTIVE_KEY_ID` conforme o [guia do cofre](docs/COFRE.md); preserve todas as chaves existentes.

Testes Flask isolados do banco pessoal:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_webapp -v
```
