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
| `ADMIN_USERNAME` | Nome do único usuário autorizado |
| `ADMIN_PASSWORD_HASH` | Hash Werkzeug gerado localmente; nunca a senha em texto |

A aplicação exige `SECRET_KEY` e `DATABASE_URL` PostgreSQL; SQLite é rejeitado fora de testes. `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` são usados para importar a conta antiga uma única vez após a migração, e podem ser removidos somente depois de validar o primeiro acesso. A rota de cadastro público não existe. Sessões são assinadas, protegidas por HTTPS, `HttpOnly`, `SameSite=Lax` e expiram após oito horas. Operações POST usam CSRF. Tentativas inválidas de login são limitadas por endereço de origem (cinco falhas bloqueiam novas tentativas por 15 minutos).

## 2. Configurar o PostgreSQL e o serviço web

1. No Render, crie um banco PostgreSQL com armazenamento persistente e backups apropriados ao uso. Para o serviço web, use a **Internal Database URL** desse banco em `DATABASE_URL`; não coloque a URL no repositório.
2. Crie um **Web Service** apontando para o repositório e branch desejados. Defina:

   - **Build Command:** `pip install -r requirements.txt`
   - **Pre-Deploy Command:** `flask --app app db upgrade`
   - **Start Command:** `gunicorn app:app`

3. Configure `SECRET_KEY`, `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` em Environment no Render. Configure `DATABASE_URL` pelo vínculo seguro com o PostgreSQL.
4. Faça o deploy e abra a URL HTTPS do serviço. Antes de cadastrar dados reais, valide o login autorizado, os fluxos de criar/editar/concluir/reagendar/excluir e a recuperação de backup do PostgreSQL.

O pre-deploy aplica migrações Alembic antes da nova versão receber tráfego. Em planos sem comando pre-deploy, aplique `flask --app app db upgrade` no Shell do serviço antes do primeiro deploy e antes de cada deploy que introduza uma migração. Não use `db downgrade`: a migração inicial recusa reversão automática para impedir que tabelas com dados sejam apagadas.

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

Os testes web usam exclusivamente SQLite em diretórios temporários. Eles cobrem autenticação, CSRF, migrações, CRUD, conclusão, reagendamento, pesquisa, títulos repetidos e persistência ao abrir uma nova instância Flask sobre o mesmo banco temporário. Eles não substituem a validação de implantação e backup com uma instância PostgreSQL real.

## 6. Publicar a atualização de contas e catálogo

1. Faça backup do PostgreSQL atual. Valide a nova versão em um serviço de homologação com PostgreSQL próprio; não use dados ou segredos de produção nos testes automatizados.
2. Execute localmente `.venv\Scripts\python.exe -m unittest discover -s tests -v` e os smoke tests desktop indicados no README. Revise `git diff` e `git status`: não inclua bancos, logs, comprovantes ou arquivos de segredos.
3. Envie os arquivos de código, testes, documentação e migrações ao branch conectado ao Render. Use `git add` somente nos arquivos revisados, `git commit -m "Adiciona contas e catálogo de acessos"` e `git push origin <branch>`.
4. Mantenha as variáveis de produção existentes. Não altere `SECRET_KEY`, `DATABASE_URL` nem credenciais do gestor para instalar esta atualização. Para habilitar senhas pessoais, configure `VAULT_ENABLED=true`, `VAULT_KEYS` (JSON de chaves aleatórias de 32 bytes) e `VAULT_ACTIVE_KEY_ID` conforme [COFRE.md](COFRE.md). Sem essas variáveis, o cofre funciona somente como catálogo; acessos corporativos sempre permanecem no catálogo.
5. Para habilitar comprovantes, configure um disco privado persistente no serviço web e `RECEIPT_STORAGE_DIR` apontando para um diretório absoluto nesse disco, fora de `webapp`, por exemplo `/var/data/agenda-receipts`. Sem disco persistente, mantenha a variável ausente; contas funcionam e uploads ficam desativados. Faça backup do disco separadamente do PostgreSQL.
6. Publique com os comandos já configurados: build `pip install -r requirements.txt`, pre-deploy `flask --app app db upgrade`, start `gunicorn app:app`. As revisões são `0002_finance`, `0003_access_catalog` e `0004_vault_passwords`; somente as pendentes serão executadas. Não execute downgrade. Em planos sem pre-deploy, aplique a migração pelo Shell antes de liberar tráfego.
7. Após o deploy, confira `flask --app app db current`, login, compromissos existentes, contas únicas/mensais, pagamento/histórico, calendário, lembretes e recusa de uploads não permitidos. Confira que downloads pedem login e que senhas pessoais só podem ser reveladas/copiadas após reautenticação e CSRF. Teste restauração de banco, chaves e comprovantes em ambiente isolado.

Os testes locais com SQLite não validam concorrência, permissões ou persistência reais do PostgreSQL/disco Render. A publicação e essa homologação permanecem sob responsabilidade do operador; nenhum deploy ou push é realizado automaticamente por esta alteração. Consulte [o guia do cofre](COFRE.md) para geração, rotação, recuperação e limitações. `VAULT_DESKTOP_PASSWORD_HASH` é exclusivo do desktop e não precisa ser configurado no Render.

## 7. Migrar e administrar usuários

1. Faça backup do PostgreSQL e mantenha `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` atuais no Render. Publique a revisão `0005_users_permissions` usando o comando de migração já configurado (`flask --app app db upgrade`) antes de iniciar o Gunicorn. A migração cria tabelas e índices novos e acrescenta `owner_id` opcional a compromissos e tarefas; os registros existentes continuam intactos e compartilhados com usuários autorizados.
2. Entre uma vez com o login e a senha antigos. No primeiro envio do formulário de login, a aplicação importa a conta como Administrador, mantendo o hash atual. Um marcador persistido impede novas importações em reinicializações e impede que uma senha redefinida pela interface seja sobrescrita. Confirme o acesso à agenda e à administração antes de remover as variáveis antigas.
3. Abra **Usuários** no menu lateral para cadastrar contas, editar nome/login e perfil, conceder permissões, redefinir senhas ou ativar/desativar pessoas. Senhas novas devem ter ao menos 12 caracteres. Operações administrativas exigem a senha atual do administrador e confirmação explícita; sessões do usuário afetado são invalidadas quando credenciais, permissões ou status mudam. Desativação preserva os registros e a auditoria; usuários não são apagados fisicamente.
4. Administradores têm acesso à agenda e às contas. A agenda existente permanece organizacional e compartilhada apenas com quem recebeu acesso; registros antigos sem proprietário continuam visíveis, mas somente administradores podem alterá-los. Gestores podem editar seus próprios compromissos, criar e editar tarefas e consultar/atualizar contas somente quando a permissão correspondente foi concedida. Leitores podem consultar dados autorizados, sem operações de escrita. A remoção de acesso ao cofre é independente e nenhum perfil o recebe automaticamente, inclusive Administradores; cada revelação exige sessão recente e reautenticação com a senha do próprio usuário.
5. A aplicação impede desativação ou rebaixamento do último Administrador ativo. As ações administrativas são consultáveis em **Usuários → Consultar auditoria**; registros guardam apenas ação e IDs, nunca senhas ou credenciais. Para recuperar a senha de uma conta Administradora existente, um operador autorizado do Render pode executar `flask --app app users-reset-admin-password --email usuario@example.com` no Shell do serviço e informar a nova senha no prompt oculto. Esse comando invalida sessões e grava auditoria, sem criar contas ou senhas padrão.

O comando combinado `flask --app app db upgrade && gunicorn app:app` continua compatível. Os testes de migração, autenticação e permissões usam bancos SQLite temporários; valide também em homologação PostgreSQL antes de liberar o deploy para produção.
