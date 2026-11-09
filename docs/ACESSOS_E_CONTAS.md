# Acessos e contas: escopo, segurança e operação

## Arquitetura e Cofre de Senhas

O desktop mantém SQLite e CustomTkinter; a web mantém SQLAlchemy, PostgreSQL, contas individuais, permissões e CSRF. A seção web **Senhas** reutiliza `agenda/vault.py`, serviço AES-GCM compartilhado, chaveiro externo versionado e autenticação adicional já existentes. `SECRET_KEY` permanece exclusivamente para a sessão. Nenhuma variável de produção precisa ser alterada se o cofre já estiver configurado.

Na web, a senha fica como hash independente em `users.password_hash`. `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` são usados apenas para importar o primeiro administrador quando a migração ainda não encontrou contas e o marcador `security_state.bootstrap_complete` ainda está falso. Após o bootstrap, alterações nas variáveis não atualizam contas existentes. `DATABASE_URL` seleciona o PostgreSQL; `SECRET_KEY` assina sessões e não corrige credenciais. Para diagnóstico e recuperação operacional, use o procedimento Render em [RENDER.md](RENDER.md), que também funciona sem Shell no plano Free.

Na web, **Senhas** reutiliza o catálogo `access_catalog` e o cofre AES-GCM existente; não cria um segundo armazenamento de credenciais. Os acessos têm sistema, usuário/e-mail, senha, URL, categoria e observações; busca cobre serviço, usuário, categoria e notas. Senhas são reveladas ou copiadas apenas após reautenticação; a listagem contém metadados e um indicador de senha cadastrada, sem plaintext ou ciphertext. A permissão explícita do cofre continua obrigatória. A URL antiga `/tasks` redireciona para `/vault`; os POSTs antigos de tarefas respondem `410 Gone`, sem gravação. A consulta administrativa `/tasks/archive` é somente leitura; a tabela `tasks` e os registros existentes não são convertidos em acessos. Pesquisa, calendário e lembretes mantêm a consulta aos registros antigos conforme as permissões.

O campo Senha é opcional para acessos pessoais autorizados. Olho e cópia exigem reautenticação por operação; editar com senha vazia preserva a existente. Listagem e HTML inicial não contêm senha ou ciphertext. Somente uma operação POST específica, autorizada e protegida por CSRF, pode devolver a senha para revelação/cópia.

Sem configuração válida, funciona somente o catálogo. Registros antigos e corporativos permanecem no catálogo, com proibição de senha também no banco. Não reclassifique credenciais corporativas como pessoais para contornar políticas. Suporte corporativo permanece pendente de autorização e avaliação específica da organização.

Consulte [COFRE.md](COFRE.md) para as variáveis exatas do Render e desktop, geração privada de chaves, rotação transacional, recuperação, limitações de clipboard e permissões. A migração `0008_access_username` adiciona o campo de usuário de acesso, sem alterar compromissos ou tarefas; o upgrade do SQLite desktop é aditivo e cria backup.

## Contas e Vencimentos

Os valores são armazenados em centavos inteiros e validados com `Decimal`. Cada ocorrência tem ID próprio. O status é calculado: uma conta sem pagamento cujo vencimento é anterior ao dia consultado está vencida; no dia do vencimento ainda está pendente. Totais respeitam os filtros selecionados; o total a pagar inclui as contas vencidas ainda sem pagamento.

Recorrência mensal mantém o dia original como âncora, ajustando meses curtos ao último dia válido (29/30/31). Há unicidade por série e mês para evitar duplicação. A consulta de um mês materializa suas ocorrências; não é preciso cadastrar cada mês manualmente. O pagamento preserva um histórico independente dos dados atuais. A exclusão não apaga o histórico de pagamentos. Revise as regras de edição/exclusão na interface: ocorrências e série são operações distintas.

Editar uma ocorrência atualiza as próximas ainda não pagas; pagamentos anteriores são imutáveis. Excluir uma ocorrência mensal encerra a série e cancela as próximas ainda não pagas, preservando ocorrências anteriores e pagamentos. Editar um registro antigo de uma série encerrada não a reativa; cadastre uma nova série quando necessário. Datas financeiras são limitadas ao intervalo 1900–2199.

Os vencimentos aparecem no calendário em uma camada própria, sem criar, editar ou excluir compromissos. Lembretes de contas são apresentados com 7, 3 e 1 dia de antecedência. O desktop depende do aplicativo aberto; a web apresenta a lista ao acessar a página de lembretes. Não há e-mail, push em segundo plano, agendador externo ou garantia de alerta enquanto os aplicativos estiverem fechados.

## Comprovantes privados

São aceitas apenas imagens **PNG/JPEG até 5 MB e 20 megapixels**. PDF e outros formatos são recusados nesta versão. O sistema verifica o conteúdo com Pillow, exige compatibilidade entre formato e extensão, regrava a imagem removendo metadados, usa nomes aleatórios e impede caminhos arbitrários. Na web, o download passa pela autenticação e pela conta correspondente, como anexo, fora de `static`. Arquivos não são URLs públicas.

No desktop, os arquivos ficam ao lado do banco em `receipts`, protegidos pelas permissões da conta Windows. Restrinja essa pasta à conta autorizada usando as permissões do sistema. O armazenamento de imagens não oferece criptografia em repouso própria: use proteção do disco e políticas de backup adequadas.

Na web, configure **opcionalmente** `RECEIPT_STORAGE_DIR` como um caminho absoluto fora de `webapp`, em disco persistente privado do Render (exemplo `/var/data/agenda-receipts`, se o disco estiver montado em `/var/data`). Sem essa configuração, contas funcionam normalmente e novos uploads são desabilitados. Não use o filesystem efêmero do serviço para documentos reais. Configure acesso e backups do disco de acordo com as políticas da organização.

O backup SQLite inclui metadados, mas **não inclui os arquivos de comprovantes**. Copie a pasta `receipts` separadamente e mantenha banco e arquivos no mesmo ponto de recuperação. O mesmo vale para PostgreSQL e disco no Render. Arquivos antigos podem permanecer para preservar referências e histórico; não remova comprovantes sem verificar os registros que dependem deles.

## Publicação e validação

As migrações web incluem `0002_finance`, `0003_access_catalog`, `0004_vault_passwords`, `0005_users_permissions`, `0006_reminder_center`, `0007_admin_recovery` e `0008_access_username`, posteriores a `0001_create_agenda_tables`; são aditivas e não recriam os dados existentes. O desktop passa para `PRAGMA user_version=5`, com backup antes da atualização de bancos antigos. Não use downgrade para apagar tabelas.

Execute os testes com `.venv\Scripts\python.exe -m unittest discover -s tests -v`. Todos usam bancos temporários. A suíte do cofre verifica criptografia, autorização, CSRF, política corporativa, chave perdida, migrações e rotação atômica. Consulte o relatório de validação para resultados e limites, incluindo validação PostgreSQL real e revisão visual.

## Arquivos desta atualização

Novos arquivos:

- Serviços/repositórios locais: `agenda/catalog.py`, `agenda/finance.py`, `agenda/finance_storage.py`, `agenda/receipts.py`.
- Interface desktop: `agenda/feature_views.py`.
- Web: `webapp/catalog.py`, `webapp/finance_repository.py`, `webapp/finance_routes.py`.
- Templates: `webapp/templates/catalog.html`, `webapp/templates/bills.html`, `webapp/templates/bill_form.html`, `webapp/templates/bill_history.html`.
- Migrações: `migrations/versions/0002_finance.py`, `migrations/versions/0003_access_catalog.py`.
- Testes: `tests/test_catalog_receipts.py`, `tests/test_finance.py`, `tests/test_web_finance.py`, `tests/smoke_features.py`.
- Documentação: `docs/ACESSOS_E_CONTAS.md`.

Arquivos alterados:

- Desktop/persistência: `agenda/storage.py`, `agenda/ui.py`.
- Web/configuração: `webapp/__init__.py`, `webapp/models.py`, `webapp/routes.py`.
- Navegação/calendário/lembretes: `webapp/templates/base.html`, `webapp/templates/calendar.html`, `webapp/templates/reminders.html`, `webapp/static/style.css`.
- Migrações e testes existentes: `migrations/env.py`, `tests/test_storage_integrity.py`.
- Dependências/operação: `requirements.txt`, `.env.example`, `.gitignore`, `README.md`, `docs/RENDER.md`, `docs/VALIDACAO.md`.

`migrations/env.py` preserva os loggers existentes ao carregar Alembic. O aprimoramento posterior acrescenta `cryptography==50.0.2`; veja [os arquivos e instruções do cofre](COFRE.md).
