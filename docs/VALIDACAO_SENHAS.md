# Validação da seção Senhas — 10/11/2026

## Implementação e escopo

A análise partiu de `eff22a2`, que já substituía a seção web Tarefas pelo cofre existente em `/vault`. Esta atualização completa a revisão com armazenamento corporativo autorizado, confirmação de exclusão no servidor e testes de integração em PostgreSQL real.

- O menu Senhas, com cadeado, abre o catálogo existente. Serviço, usuário/e-mail, senha, site, categoria e observações continuam em `access_catalog`; não há outro armazenamento de credenciais.
- A web permite senhas pessoais e corporativas aos usuários com permissão explícita para o cofre. O padrão de concessão de permissões não muda. O desktop independente mantém sua política anterior.
- Cadastro, edição, pesquisa por usuário, abertura do site, cópia de usuário, revelação/ocultação e cópia de senha foram exercitados. Definir, substituir, remover, revelar ou copiar uma senha exige reautenticação. Uma edição com senha vazia mantém o ciphertext original.
- Excluir abre uma página de confirmação. GET não exclui; POST sem confirmação ou CSRF válido é recusado. Cancelar preserva o cadastro. O fluxo funciona sem JavaScript.
- A criptografia permanece AES-256-GCM, com nonce aleatório e vínculo ao ID do acesso. A atualização não gera, troca ou rotaciona chaves. Listagens, edição e confirmação não recebem senhas descriptografadas ou ciphertexts.
- A interface mantém o menu azul-marinho, cartões brancos e estilos existentes; os fluxos foram verificados também em viewport de 390 px.

## Preservação e migração

A revisão `0009_corporate_passwords` remove somente `ck_access_catalog_password_scope` no PostgreSQL, autorizando senha em registros corporativos. Não recria tabelas, remove colunas, reclassifica acessos, converte tarefas ou modifica linhas funcionais. A validação de classificação `0`/`1` permanece. A revisão recusa downgrade automático.

Os testes comparam todas as linhas existentes antes e depois das migrações, verificam o ciphertext original e confirmam sua leitura com a chave original. Compromissos, usuários e tarefas são preservados. A tabela `tasks` continua original e administradores podem consultá-la em `/tasks/archive`; os POSTs antigos de tarefas permanecem desativados.

## Verificações executadas

Ambiente: Windows, Python 3.14.10, dependências fixadas do projeto, PostgreSQL 18.6 local descartável e Microsoft Edge headless via Playwright. Chaves e contas foram geradas exclusivamente para os testes.

| Verificação | Resultado |
| --- | --- |
| `python -m unittest discover -s tests -v`, com `TEST_POSTGRES_DATABASE_URL` de teste | 180 testes aprovados, sem omissões |
| `python -m tests.smoke_vault_web` | Fluxos corporativo e pessoal aprovados, desktop e celular |
| Confirmação de exclusão com JavaScript desabilitado | Cancelamento e exclusão confirmada aprovados |
| Abertura do site cadastrado | URL correta em nova aba, sem acesso a `window.opener`; destino fictício interceptado no teste |
| Área de transferência real | Cópia de usuário/senha, reautenticação e limpeza condicional aprovadas |
| PostgreSQL real | Migração, CRUD criptografado, reinicialização Flask, preservação, permissões e CSRF HTTPS aprovados |
| `python -m compileall -q agenda webapp tests scripts app.py` | Aprovado |
| `git diff --check` | Aprovado |

A integração PostgreSQL cria um esquema exclusivo no banco descartável e o remove ao terminar. O servidor temporário foi encerrado após os testes. Nenhum banco do Render ou SQLite pessoal foi acessado. Os testes de criptografia e rotação usam somente chaves temporárias, sem alterar as configurações de produção.

## Render

Não há variável adicional de produção. Preserve `VAULT_KEYS` e `VAULT_ACTIVE_KEY_ID` existentes e mantenha `VAULT_ENABLED=true` para armazenar senhas. Caso o cofre ainda não esteja configurado, siga [COFRE.md](COFRE.md); sem configuração válida, apenas os metadados ficam disponíveis. `TEST_POSTGRES_DATABASE_URL` é exclusiva dos testes e não deve ser configurada no serviço de produção.

Antes do tráfego do novo código, aplique `flask --app app db upgrade`, usando o pre-deploy já documentado, e confira `0009_corporate_passwords` em `flask --app app db current`. Mantenha o backup e as chaves existentes. Não execute downgrade.

A página pública de login do Render respondeu HTTP 200; as rotas do cofre e do arquivo de tarefas exigem login. Isso não certifica as operações autenticadas, as variáveis de ambiente, os backups ou a revisão do banco em produção. Essa conferência deve ser feita no serviço após o deploy, com uma conta autorizada, sem compartilhar credenciais.
