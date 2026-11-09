# Publicar a Agenda Executiva no Render

A versão web é uma aplicação Flask separada da interface CustomTkinter. Ela usa PostgreSQL e não abre, converte ou modifica `%USERPROFILE%\AgendaExecutiva\agenda.db`. As duas versões mantêm bancos independentes; esta entrega não importa automaticamente os dados pessoais do SQLite para o serviço hospedado.

## 1. Criar os segredos

Não envie arquivos `.env`, o SQLite, logs, senhas ou registros reais ao GitHub. O `.gitignore` exclui esses arquivos; `.env.example` contém apenas valores fictícios.

Gere uma chave de sessão localmente:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

Gere o hash da senha do gestor sem exibir a senha no terminal:

```powershell
.\.venv\Scripts\python.exe -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass('Senha do gestor: ')))"
```

Configure os quatro valores abaixo no painel do Render (ou num `.env` local ignorado pelo Git):

| Variável | Valor |
|---|---|
| `SECRET_KEY` | Chave aleatória forte gerada acima |
| `DATABASE_URL` | URL interna do banco PostgreSQL do Render |
| `ADMIN_USERNAME` | Login da conta administrativa inicial |
| `ADMIN_PASSWORD_HASH` | Hash Werkzeug da senha inicial; nunca a senha em texto |
| `AGENDA_MANAGER_NAME` | (Opcional) nome exibido no cabeçalho dos PDFs exportados |

A aplicação exige `SECRET_KEY` e `DATABASE_URL` PostgreSQL; SQLite é rejeitado fora de testes. `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` são credenciais de **bootstrap**: depois da migração, o primeiro login importa o administrador inicial para `users` somente se ainda não houver usuários e `security_state.bootstrap_complete` não estiver definido. A senha passa a ser o hash independente em `users.password_hash`; deploys, mudanças nessas duas variáveis e reinicializações não sobrescrevem contas persistidas. Remova as variáveis de bootstrap somente depois de confirmar a importação e o login. `DATABASE_URL` seleciona o PostgreSQL que contém as contas e os demais dados; `SECRET_KEY` assina sessões e não é senha de usuário — não a altere para corrigir falha de login. A rota de cadastro público não existe. Sessões são assinadas, protegidas por HTTPS, `HttpOnly`, `SameSite=Lax` e expiram após oito horas. Operações POST usam CSRF. Tentativas inválidas de login são limitadas por endereço de origem (cinco falhas bloqueiam novas tentativas por 15 minutos).

## 2. Configurar o PostgreSQL e o serviço web

1. No Render, crie um banco PostgreSQL com armazenamento persistente e backups apropriados ao uso. Para o serviço web, use a **Internal Database URL** desse banco em `DATABASE_URL`; não coloque a URL no repositório.
2. Crie um **Web Service** apontando para o repositório e branch desejados. Defina:

   - **Build Command:** `pip install -r requirements.txt`
   - **Pre-Deploy Command:** `flask --app app db upgrade`
   - **Start Command:** `gunicorn app:app`

3. Configure `SECRET_KEY`, `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` em Environment no Render. Configure `DATABASE_URL` pelo vínculo seguro com o PostgreSQL.
4. Faça o deploy e abra a URL HTTPS do serviço. Antes de cadastrar dados reais, valide o login autorizado, os fluxos de criar/editar/concluir/reagendar/excluir e a recuperação de backup do PostgreSQL.

O pre-deploy aplica migrações Alembic antes da nova versão receber tráfego. Em planos sem comando pre-deploy ou sem Shell, aplique migrações de uma máquina autorizada usando `.\.venv\Scripts\flask.exe --app scripts.admin_operator:create_operator_app db upgrade`; a URL externa do PostgreSQL é solicitada sem eco e não deve ser passada em argumentos. Não use `db downgrade`: as migrações recusam reversão automática para impedir remoção de dados.

O Flask-WTF mantém `WTF_CSRF_ENABLED=True` e a validação estrita de `Referer` para POSTs HTTPS (`WTF_CSRF_SSL_STRICT=True`). A aplicação confia em um valor `X-Forwarded-Proto` do proxy imediato por meio de `ProxyFix(x_for=1, x_proto=1)`, para que HTTPS terminado no Render seja reconhecido; mantenha o serviço atrás do proxy gerenciado, sem expor o Gunicorn diretamente. As páginas administrativas e o cofre publicam `Referrer-Policy: strict-origin-when-cross-origin`. O formulário de cadastro/edição de usuário declara a mesma política explicitamente, preservando o `Referer` de mesma origem que o Flask-WTF exige; não use `no-referrer` nesses formulários. Tokens CSRF continuam obrigatórios e origens externas são recusadas. Se o Render ainda responder “The referrer header is missing”, confira se o serviço concluiu o deploy do commit que contém essa correção e verifique em DevTools que o POST usa HTTPS, envia `Referer` da mesma origem e que `X-Forwarded-Proto` foi interpretado como HTTPS; não desabilite a validação.

## 3. Executar localmente

Instale e inicie um PostgreSQL local; a versão web não usa SQLite como base persistente. No PowerShell, configure os segredos no processo local (ou use um `.env` e uma ferramenta local que o carregue):

```powershell
$env:SECRET_KEY = "<chave-aleatoria>"
$env:DATABASE_URL = "postgresql://agenda_user:senha-local@localhost:5432/agenda_db"
$env:ADMIN_USERNAME = "gestor"
$env:ADMIN_PASSWORD_HASH = "<hash-Werkzeug>"
$env:SESSION_COOKIE_SECURE = "false"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\flask.exe --app app db upgrade
.\.venv\Scripts\flask.exe --app app run
```

`SESSION_COOKIE_SECURE=false` é somente para desenvolvimento por HTTP local; remova essa variável em produção (o padrão é `true`). O servidor Flask local é apenas para desenvolvimento. No Render, use Gunicorn pelo comando de início acima. O banco SQLite temporário aparece somente nos testes automatizados e não é aceito pela configuração de produção.

## 4. Migrações e dados

As revisões ficam em `migrations/versions/`. A primeira revisão cria as tabelas `events` e `tasks`; revisões futuras devem ser aditivas sempre que possível, preservando registros. Faça backup do PostgreSQL antes de mudanças de esquema. O desktop continua no SQLite local; publicar o código não envia nem apaga esse banco.

## 5. Funcionalidades web

Painel inicial, calendário mensal, pesquisa, compromissos (criar, editar, concluir, reagendar, duplicar e excluir), tarefas (criar, editar, concluir e excluir), prioridades e lista de lembretes usam a mesma conta protegida. A interface adapta-se a telas pequenas. As regras de validação de datas, horários, prioridades e lembretes reutilizam `AgendaService`.

Os testes web usam SQLite em diretórios temporários, exceto o teste PostgreSQL opcional, que exige `TEST_POSTGRES_DATABASE_URL` apontando para um banco descartável. Eles cobrem autenticação, CSRF, migrações, recuperação, CRUD e persistência ao abrir uma nova instância Flask sobre o mesmo banco temporário. Não substituem a validação de implantação e backup com uma instância PostgreSQL real.

## 6. Publicar a atualização de contas e catálogo

1. Faça backup do PostgreSQL atual. Valide a nova versão em um serviço de homologação com PostgreSQL próprio; não use dados ou segredos de produção nos testes automatizados.
2. Execute localmente `.venv\Scripts\python.exe -m unittest discover -s tests -v` e os smoke tests desktop indicados no README. Revise `git diff` e `git status`: não inclua bancos, logs, comprovantes ou arquivos de segredos.
3. Envie os arquivos de código, testes, documentação e migrações ao branch conectado ao Render. Use `git add` somente nos arquivos revisados, `git commit -m "Substitui tarefas por gerenciador de senhas"` e `git push origin <branch>`.
4. Mantenha as variáveis de produção existentes. Não altere `SECRET_KEY`, `DATABASE_URL` nem credenciais do gestor para instalar esta atualização. **Não é necessária uma variável nova** se o cofre já está habilitado: a seção Senhas reutiliza `VAULT_ENABLED`, `VAULT_KEYS` e `VAULT_ACTIVE_KEY_ID` existentes, sem mudar a chave ou recriptografar dados. Se essas variáveis ainda não estiverem configuradas e a organização autorizar armazenamento de credenciais pessoais, configure-as conforme [COFRE.md](COFRE.md); sem elas a página funciona apenas como catálogo, sem guardar senha. Conceda também a permissão Cofre ao usuário em **Usuários**. Acessos corporativos sempre permanecem somente no catálogo.
5. Para habilitar comprovantes, configure um disco privado persistente no serviço web e `RECEIPT_STORAGE_DIR` apontando para um diretório absoluto nesse disco, fora de `webapp`, por exemplo `/var/data/agenda-receipts`. Sem disco persistente, mantenha a variável ausente; contas funcionam e uploads ficam desativados. Faça backup do disco separadamente do PostgreSQL.
6. Publique com os comandos já configurados: build `pip install -r requirements.txt`, pre-deploy `flask --app app db upgrade`, start `gunicorn app:app`. As revisões incluem `0002_finance`, `0003_access_catalog`, `0004_vault_passwords`, `0005_users_permissions`, `0006_reminder_center`, `0007_admin_recovery` e `0008_access_username`; somente as pendentes serão executadas. A revisão `0008` adiciona uma coluna de usuário com valor vazio aos acessos antigos; não altera a tabela `tasks` nem converte tarefas. Não execute downgrade. Em planos sem pre-deploy, aplique a migração antes de liberar tráfego.
7. Após o deploy, confira `flask --app app db current`, login, compromissos existentes, contas únicas/mensais, pagamento/histórico, calendário, lembretes e recusa de uploads não permitidos. Confira que downloads pedem login e que senhas pessoais só podem ser reveladas/copiadas após reautenticação e CSRF. Teste restauração de banco, chaves e comprovantes em ambiente isolado.

Os testes locais com SQLite não validam concorrência, permissões ou persistência reais do PostgreSQL/disco Render. Após o push, confira no painel do Render se o deploy do commit esperado e a migração terminaram; um push só inicia publicação automática quando o serviço está configurado para isso. A homologação do serviço publicado deve confirmar os fluxos reais. Consulte [o guia do cofre](COFRE.md) para geração, rotação, recuperação e limitações. `VAULT_DESKTOP_PASSWORD_HASH` é exclusivo do desktop e não precisa ser configurado no Render.

A seção **Senhas** abre `/vault`, com cadastro/edição, pesquisa, revelação e cópia após reautenticação. A URL antiga `/tasks` redireciona para o cofre; POSTs de criação, conclusão e exclusão de tarefas antigas são encerrados com `410 Gone`, sem alterar o banco. Administradores consultam as tarefas preservadas em `/tasks/archive`, somente leitura. A tabela `tasks` continua intacta e seus registros permanecem visíveis na pesquisa, no calendário e nos lembretes autorizados.

## 7. Migrar e administrar usuários

1. Faça backup do PostgreSQL e mantenha `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` atuais no Render até confirmar o primeiro acesso após o bootstrap. Publique as revisões pendentes usando o pre-deploy `flask --app app db upgrade` antes de iniciar o Gunicorn. A migração de usuários cria tabelas e índices novos e acrescenta `owner_id` opcional a compromissos e tarefas; as revisões são aditivas e preservam os registros existentes.
2. Entre uma vez com o login e a senha iniciais. No primeiro envio do formulário de login após a migração, a aplicação importa a conta como Administrador, mantendo o hash configurado. Se o PostgreSQL já contém usuários, eles prevalecem e o bootstrap é marcado como concluído. Esse marcador persistido impede novas importações em reinicializações e impede que redefinições sejam sobrescritas por `ADMIN_PASSWORD_HASH`. Confirme o acesso à agenda e à administração antes de remover as variáveis antigas.
3. Abra **Usuários** no menu lateral para cadastrar contas, editar nome/login e perfil, conceder permissões, redefinir senhas ou ativar/desativar pessoas. Senhas novas devem ter ao menos 12 caracteres. Operações administrativas exigem a senha atual do administrador e confirmação explícita; sessões do usuário afetado são invalidadas quando credenciais, permissões ou status mudam. Desativação preserva os registros e a auditoria; usuários não são apagados fisicamente.
4. Administradores têm acesso à agenda e às contas. A agenda existente permanece organizacional e compartilhada apenas com quem recebeu acesso; compromissos antigos sem proprietário continuam visíveis, mas somente administradores podem alterá-los. Gestores podem editar seus próprios compromissos e consultar/atualizar contas somente quando a permissão correspondente foi concedida. Tarefas antigas permanecem somente para consulta. Leitores podem consultar dados autorizados, sem operações de escrita. A remoção de acesso ao cofre é independente e nenhum perfil o recebe automaticamente, inclusive Administradores; cada revelação exige sessão recente e reautenticação com a senha do próprio usuário.
5. A aplicação impede desativação ou rebaixamento do último Administrador ativo. As ações administrativas são consultáveis em **Usuários → Consultar auditoria**; registros guardam apenas ação e IDs, nunca senhas ou hashes. O diagnóstico `flask --app app auth-status --email usuario@example.com` informa se a conta existe no banco conectado, perfil/status, último acesso e se o bootstrap terminou, sem revelar o hash. Sem Shell, rode `.\.venv\Scripts\flask.exe --app scripts.admin_operator:create_operator_app auth-status --email usuario@example.com` localmente para consultar o PostgreSQL externo autorizado.

6. **Recuperação no Render Free sem Shell:** use um computador confiável com o projeto, clientes PostgreSQL compatíveis (`pg_dump` e `pg_restore`) e acesso autorizado à **External Database URL** do banco correto no painel Render. Não use a URL interna. Crie uma pasta local privada, fora do repositório, para o backup e a autorização. Rode cada comando abaixo localmente; cada execução solicita a URL externa com entrada oculta, exige TLS com validação do certificado/host e não inclui a URL em argumentos ou logs:

   ```powershell
   .\.venv\Scripts\flask.exe --app scripts.admin_operator:create_operator_app admin-recovery-backup --backup-file C:\Caminho\Privado\agenda-recovery.dump
   .\.venv\Scripts\flask.exe --app scripts.admin_operator:create_operator_app admin-recovery-authorize --email usuario@example.com --backup-file C:\Caminho\Privado\agenda-recovery.dump --token-file C:\Caminho\Privado\agenda-recovery.authorization
   .\.venv\Scripts\flask.exe --app scripts.admin_operator:create_operator_app reset-admin-password-once --email usuario@example.com --backup-file C:\Caminho\Privado\agenda-recovery.dump --authorization-file C:\Caminho\Privado\agenda-recovery.authorization
   ```

   Antes, instale os requisitos do projeto, `pg_dump`/`pg_restore` compatíveis e crie a pasta indicada com permissões privadas; mantenha ambos os arquivos fora do repositório. O primeiro comando cria um backup `pg_dump` completo em formato custom e verifica que corresponde ao banco conectado e contém todas as tabelas da aplicação. Os comandos seguintes mostram a conta ativa selecionada e exigem confirmação digitando o ID completo; a nova senha é digitada somente em prompt oculto. A autorização é gerada separadamente, vinculada ao administrador, à versão atual da conta e ao hash daquele backup; o banco guarda somente o hash do token. Ela expira em 15 minutos, funciona uma vez e é invalidada após a recuperação. O terceiro comando incrementa a versão da sessão, grava auditoria e remove o arquivo local da autorização; se a remoção local falhar, apague-o manualmente. A recuperação não cria conta, não autentica automaticamente e não altera outros dados. Nunca passe senhas em argumentos nem publique/compartilhe esses arquivos.

   Se houver Shell disponível em outro plano, `flask --app app reset-admin-password --email usuario@example.com --backup-file /caminho/privado/backup.dump` também exige backup recente, confirmação do ID completo e nova senha oculta. Recuperação nunca faz parte do deploy. A migração aditiva `0007_admin_recovery` cria apenas a tabela de autorizações temporárias; não recria nem apaga tabelas ou dados.

O comando combinado `flask --app app db upgrade && gunicorn app:app` continua compatível. Os testes de migração, autenticação, recuperação e permissões usam bancos SQLite temporários; o teste PostgreSQL opcional deve apontar somente para um banco descartável. Valide também em homologação PostgreSQL antes de liberar o deploy para produção.

## 8. Central de alertas e calendário compacto

A revisão `0006_reminder_center` cria somente as tabelas `manual_reminders` e `reminder_states`; compromissos, tarefas, contas, usuários e sessões permanecem intactos. Aplique-a com o comando de migração configurado antes do tráfego da nova versão. Lembretes manuais pertencem ao usuário que os criou; estado de leitura e adiamento dos alertas derivados é individual por usuário.

O calendário mostra até três itens combinados por dia e oferece um detalhe diário com todos os compromissos, tarefas e vencimentos autorizados. A Central de Alertas não replica a lista de compromissos: reuniões aparecem somente na janela definida em cada compromisso; tarefas concluídas e contas pagas não geram pendências; contas próximas (até sete dias) ou vencidas e lembretes manuais são exibidos separadamente. Datas e horários de alertas são interpretados em `America/Sao_Paulo`; campos de horário de lembrete manual são armazenados como UTC naive no PostgreSQL e convertidos para o fuso local ao exibir.

Valide em homologação: aplique `flask --app app db upgrade`, confirme `flask --app app db current` como `0008_access_username`, veja o calendário e a Central com perfis de acesso distintos e teste criar/adiar/concluir/excluir um lembrete manual, marcar alertas como lidos, pagar uma conta vencida e conferir que ela deixa de aparecer. Os testes automatizados usam bancos SQLite temporários; não certificam a concorrência ou o funcionamento de um PostgreSQL Render real.

## 9. Exportação e edição de lembretes

Calendário e Compromissos oferecem exportação em PDF, XLSX e ICS, com período, situação, categoria e prioridade selecionáveis. A rota exige acesso de leitura à agenda e exporta somente campos autorizados dos compromissos que `PostgresRepository` disponibiliza para esse perfil; notas e descrições não são incluídas. Os arquivos são compostos em memória, enviados como anexos privados com `Cache-Control: private, no-store` e não criam links permanentes. A planilha grava texto como texto e neutraliza valores iniciados por caracteres de fórmula. Os calendários ICS incluem `America/Sao_Paulo`, identificadores estáveis por compromisso e horários locais representados com fuso explícito. Para incluir o nome do gestor no PDF, configure opcionalmente `AGENDA_MANAGER_NAME`.

A Central de Alertas mantém as janelas existentes: compromissos aparecem apenas entre o horário de aviso configurado e o início (e não aparecem quando concluídos ou sem lembrete); tarefas pendentes aparecem quando vencidas ou com prazo em até sete dias; lembretes manuais próprios aparecem quando concluídos ou com vencimento em até sete dias, exceto enquanto adiados; as regras de contas permanecem separadas. Assim, uma categoria vazia pode representar corretamente ausência de itens elegíveis, e não uma falha de consulta. O banco PostgreSQL e o esquema não mudam com esta atualização. A criação e edição de lembretes manuais acontecem em modal; cancelar não grava dados e falhas de validação reabrem o formulário com os valores enviados.

As dependências de exportação estão fixadas em `requirements.txt` (`reportlab`, `openpyxl`, `icalendar` e `pypdf` para validar PDFs). Os testes `tests.test_exports` abrem os arquivos gerados, conferem páginas PDF, filtros, datas, fuso e UID ICS, autorização e injeção de fórmulas; `tests.test_reminders` cobre modal e edição junto aos fluxos já existentes. Execute `.\.venv\Scripts\python.exe -m unittest tests.test_exports tests.test_reminders -v`. Esses testes usam exclusivamente bancos SQLite temporários; valide em homologação PostgreSQL antes de publicar, sem alterar ou migrar o banco de produção.
