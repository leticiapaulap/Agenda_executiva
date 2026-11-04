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

O serviço falha ao iniciar se não houver uma `SECRET_KEY`, credenciais de gestor ou `DATABASE_URL` PostgreSQL; SQLite é rejeitado fora de testes. A rota de cadastro público não existe. Sessões são assinadas, protegidas por HTTPS, `HttpOnly`, `SameSite=Lax` e expiram após oito horas. Operações POST usam CSRF. Tentativas inválidas de login são limitadas por endereço de origem (cinco falhas bloqueiam novas tentativas por 15 minutos).

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
